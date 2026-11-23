#!/usr/bin/env python3
"""Pulse Diagnostics backend API and Qt WebEngine Chromium shell integration.

The live metrics, diagnostics, optimizer and sensor logic remain unchanged.
The UI renderer is bundled PySide6 QtWebEngine, never WebView2 or pywebview.
"""
from __future__ import annotations

import argparse
import atexit
import contextlib
import datetime
import hashlib
from pathlib import Path
import json
import logging
import multiprocessing as mp
import os
import queue
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import system_diagnostics as diag
except ImportError:
    sys.exit("system_diagnostics.py must be in the same folder as this file.")
import psutil
from assessment import calculate_assessment
from hardware_sensors import (get_snapshot, close as close_sensors, pawnio_status,
                         apply_temp_mode, get_temp_mode, TEMP_MODES)
from optimizer import Optimizer


atexit.register(close_sensors)

diag.USE_COLOR = False
REPORT_DIR = os.path.join(os.path.expanduser("~"), "LaptopDiag_Reports")
# Official PawnIO 2.2.0 installer: cryptographically pinned to the release asset.
PAWNIO_INSTALLER_SHA256 = '1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032'
APP_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
ICON_FILE = APP_ROOT / 'assets' / 'pulse.ico'  # app/taskbar icon only; not shown inside the UI
PAWNIO_INSTALLER = APP_ROOT / 'vendor' / 'pawnio' / 'PawnIO_setup.exe'
WINDOW = None


# --------------------------------------------------------------------------- #
# Plumbing: stream test output to the UI
# --------------------------------------------------------------------------- #
class GuiReport(diag.Report):
    """Report that pushes every section / item to the UI queue instead of printing."""

    def __init__(self, q):
        super().__init__()
        self._q = q

    def section(self, title):
        self.cur = {"title": title, "items": []}
        self.sections.append(self.cur)
        self._q.put({"t": "section", "title": title})

    def add(self, name, value, status="INFO", note=""):
        item = {"name": name, "value": str(value), "status": status, "note": note}
        self.cur["items"].append(item)
        self._q.put({"t": "item", **item})


class LogSink:
    """File-like object turning print() output into log events ('\\r' = overwrite line)."""

    def __init__(self, q):
        self._q, self._buf = q, ""

    def write(self, s):
        self._buf += s
        while True:
            m = re.search(r"[\r\n]", self._buf)
            if not m:
                break
            line, ch = self._buf[:m.start()], m.group()
            self._buf = self._buf[m.end():]
            if line.strip() or ch == "\n":
                self._q.put({"t": "log", "text": line.rstrip(), "cr": ch == "\r"})
        return len(s)

    def flush(self):
        pass


# --------------------------------------------------------------------------- #
# Python API exposed to JavaScript (state kept in _private attributes)
# --------------------------------------------------------------------------- #
class Api:
    def __init__(self):
        self._events = queue.Queue()
        self._running = False
        self._cancel = False
        self._report = None
        self._assessment = None
        self._tc = (0.0, (None, "unavailable"))
        self._live_lock = threading.RLock()
        self._io = psutil.disk_io_counters()
        self._net = psutil.net_io_counters()
        self._t = time.time()
        self._optimizer = Optimizer(is_admin=diag.is_admin)

    # ---- static info ------------------------------------------------------
    def info(self):
        boot = time.time() - psutil.boot_time()
        return {"cpu": diag.cpu_model(),
                "cores": f"{psutil.cpu_count(logical=False)}C / {psutil.cpu_count()}T",
                "ram": diag.hb(psutil.virtual_memory().total),
                "os": f"{diag.platform.system()} {diag.platform.release()}",
                "host": diag.platform.node(),
                "admin": diag.is_admin(),
                "uptime": f"{int(boot // 86400)}d {int(boot % 86400 // 3600)}h",
                "temp_hint": diag.temp_hint(),
                "sensor_engine": "LibreHardwareMonitorLib 0.9.6 / pythonnet",
                "version": "4.2.0"}

    # ---- locally embedded hardware sensors (no server / external process) ----
    def sensors(self):
        """Flat list of real sensors from the embedded LibreHardwareMonitor DLL."""
        return get_snapshot(max_age=0.85)

    def install_hardware_access(self):
        """Runs the OFFICIAL installer only after the user clicks Install.

        Windows might require a reboot; reopening Pulse is always recommended
        to reinitialize the .NET driver-dependent hardware objects.
        """
        if not diag.IS_WIN:
            return {'ok': False, 'error': 'Windows is required.'}
        if not diag.is_admin():
            return {'ok': False, 'error': 'Restart Pulse as Administrator to install the driver.'}
        if not PAWNIO_INSTALLER.is_file():
            return {'ok': False, 'error': 'Official PawnIO setup was not bundled. Rebuild with build_windows.py.'}
        digest = hashlib.sha256(PAWNIO_INSTALLER.read_bytes()).hexdigest()
        if digest != PAWNIO_INSTALLER_SHA256:
            return {'ok': False, 'error': 'PawnIO setup SHA-256 mismatch; refusing to run the installer.'}
        try:
            # No -silent: the official installer stays visible and in control.
            # User confirmation is requested from the React interface first.
            process = subprocess.run([str(PAWNIO_INSTALLER), '-install'], timeout=300, check=False)
            if process.returncode not in (0, 3010):
                return {'ok': False, 'error': f'PawnIO setup failed or was cancelled (exit {process.returncode}).'}
            return {'ok': True,
                    'message': 'PawnIO setup completed. Close and reopen Pulse to re-detect Ryzen sensors.' +
                               (' Windows may also require a reboot.' if process.returncode == 3010 else ''),
                    'restart_required': True,
                    'pawnio': pawnio_status()}
        except Exception as exc:
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}

    # ---- live stats (polled every second) ---------------------------------
    def live(self):
        # Qt bridge dispatches calls through worker threads.
        # psutil.cpu_percent(interval=None) yields a meaningless 0 on a new thread;
        # sampling over a real window avoids that per-thread initial-reading trap.
        with self._live_lock:
            cores = psutil.cpu_percent(interval=0.15, percpu=True)
            now = time.time()
            dtm = max(now - self._t, 0.2)
            io, net = psutil.disk_io_counters(), psutil.net_io_counters()
            rd = (io.read_bytes - self._io.read_bytes) / dtm / 1e6 if io and self._io else 0
            wr = (io.write_bytes - self._io.write_bytes) / dtm / 1e6 if io and self._io else 0
            dn = (net.bytes_recv - self._net.bytes_recv) / dtm / 1e3 if net and self._net else 0
            up = (net.bytes_sent - self._net.bytes_sent) / dtm / 1e3 if net and self._net else 0
            self._io, self._net, self._t = io, net, now
            vm = psutil.virtual_memory()
            try:
                b = psutil.sensors_battery()
            except (OSError, NotImplementedError):
                b = None
            snap = get_snapshot()
            # ONE temperature source for the dashboard, the CPU test, the graph and
            # the Hardware Sensors page (which highlights the sensor used here).
            td = diag.get_cpu_temp_detail()
            temp, src = td['value'], td['src']
            if temp is not None and not (5 <= temp <= 125):
                temp, src = None, ''
            return {"cpu": sum(cores) / len(cores) if cores else None,
                    "cores": cores, "freq": diag.cur_freq(),
                    "threads": len(cores), "temp": temp,
                    "temp_src": src or 'Not exposed by this hardware',
                    "temp_kind": td.get('kind', ''), "temp_label": td.get('label', ''),
                    "temp_hottest": td.get('hottest'), "temp_avg": td.get('average'),
                    "temp_mode": td.get('mode', 'auto'), "temp_sensor_id": td.get('sensor_id', ''),
                    "ram": vm.percent, "ram_used": diag.hb(vm.used),
                    "ram_total": diag.hb(vm.total),
                    "bat": {"pct": b.percent, "plugged": b.power_plugged} if b else None,
                    "disk_r": rd, "disk_w": wr, "net_d": dn, "net_u": up,
                    "sensor_ok": snap['ok'], "sensor_error": snap['error'],
                    "sensor_count": len(snap['sensors']),
                    "sensor_warnings": snap.get('warnings', []),
                    "pawnio": snap.get('pawnio', pawnio_status()),
                    "gpu_temp": snap['gpu_temp'], "gpu_load": snap['gpu_load'],
                    # Auxiliary integrated-GPU reading kept for raw sensor reports.
                    "igpu_temp": snap.get('igpu_temp'),
                    "igpu_temp_src": snap.get('igpu_temp_src', ''),
                    "cpu_power": snap['cpu_power'],
                    "cpu_power_src": snap.get('cpu_power_src', ''),
                    "fan_rpm": snap['fans'][0]['value'] if snap['fans'] else None,
                    "fan_count": len(snap['fans'])}


    # ---- CPU temperature source -------------------------------------------
    def temp_mode(self):
        return {"mode": get_temp_mode(), "modes": list(TEMP_MODES)}

    def set_temp_mode(self, mode):
        try:
            apply_temp_mode(mode)
            return {"ok": True, "mode": mode}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    # ---- Optimizer (all changes are explicit, logged and reversible) -------
    def opt_status(self):
        return self._optimizer.status()

    def opt_set_toggle(self, tid, enabled):
        return self._optimizer.set_toggle(tid, enabled)

    def opt_apply_recommended(self):
        return self._optimizer.apply_recommended()

    def opt_set_plan(self, key):
        return self._optimizer.set_power_plan(key)

    def opt_startup(self):
        return self._optimizer.list_startup()

    def opt_set_startup(self, item_id, enabled):
        return self._optimizer.set_startup(item_id, enabled)

    def opt_remove_startup(self, item_id):
        return self._optimizer.remove_startup(item_id)

    def opt_removed_startup(self):
        return self._optimizer.list_removed_startup()

    def opt_restore_startup(self, bid):
        return self._optimizer.restore_startup(bid)

    def opt_forget_startup(self, bid):
        return self._optimizer.forget_removed_startup(bid)

    def opt_cleanup_sizes(self):
        return self._optimizer.cleanup_sizes()

    def opt_net_state(self):
        return self._optimizer.net_state()

    def opt_run(self, aid):
        return self._optimizer.start_job(aid)

    def opt_poll(self):
        return self._optimizer.poll_job()

    def opt_open_log_folder(self):
        return self.open_folder()


    def window_minimize(self):
        try:
            if WINDOW is not None:
                WINDOW.minimize()
                return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": False, "error": "Window unavailable"}

    def window_close(self):
        try:
            if WINDOW is not None:
                WINDOW.destroy()
                return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": False, "error": "Window unavailable"}

    def export_sensor_debug(self):
        """Export the raw sensors plus diagnostic coverage; does not guess unavailable values."""
        try:
            snap = get_snapshot(max_age=0)
            os.makedirs(REPORT_DIR, exist_ok=True)
            filename = 'Pulse_Sensors_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '.json'
            path = os.path.join(REPORT_DIR, filename)
            payload = {
                'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'app': 'Pulse Diagnostics', 'librehardwaremonitor': '0.9.6',
                'is_admin': diag.is_admin(),
                'sensor_data': snap,
                'notes': ['Raw 0 values are preserved, but impossible CPU temperatures and '
                          'zero CPU package-power readings are not presented as valid.',
                          'Motherboard socket temperature may differ from CPU die temperature.',
                          'Missing sensors can depend on firmware, CPU model, hardware and driver access.']
            }
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            return {'ok': True, 'path': path}
        except Exception as exc:
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}

    def _gpu_sample(self):
        snap = get_snapshot(max_age=0.5) or {}
        t, u = snap.get('gpu_temp'), snap.get('gpu_load')
        if t is None and u is None:
            return diag._nvidia_sample()
        return t, u

    # ---- test runner --------------------------------------------------------
    def start(self, opts):
        if self._running:
            return {"ok": False, "error": "A run is already in progress"}
        tests = [t for t in diag.TESTS if t in opts.get("tests", [])]
        if not tests:
            return {"ok": False, "error": "No tests selected"}
        quick = bool(opts.get("quick"))
        args = argparse.Namespace(
            quick=quick,
            duration=min(int(opts.get("duration", 60)), 20) if quick else int(opts.get("duration", 60)),
            load=min(max(int(opts.get("load", 25)), 5), 100),
            disk_size=min(int(opts.get("disk_size", 512)), 256) if quick else int(opts.get("disk_size", 512)),
            disk_path=opts.get("disk_path") or None,
            gpu_load=min(max(int(opts.get("gpu_load", 0)), 0), 100),
            gpu_duration=min(int(opts.get("duration", 60)), 20) if quick else min(int(opts.get("duration", 60)), 60),
            gpu_burn=lambda on, level: self._events.put({"t": "gpu_burn", "on": bool(on), "level": int(level)}),
            gpu_sample=self._gpu_sample,
            should_stop=lambda: self._cancel)
        while not self._events.empty():
            self._events.get_nowait()
        self._cancel, self._running = False, True
        self._assessment = None
        threading.Thread(target=self._worker, args=(tests, args), daemon=True).start()
        return {"ok": True}

    def _worker(self, tests, args):
        rep = GuiReport(self._events)
        self._report = rep
        category_outcomes = []
        t0 = time.time()
        try:
            with contextlib.redirect_stdout(LogSink(self._events)):
                for i, name in enumerate(tests):
                    if self._cancel:
                        break
                    self._events.put({"t": "stage", "name": name, "i": i, "total": len(tests)})
                    old_n = len(rep.sections)
                    try:
                        diag.TESTS[name](rep, args)
                    except Exception as exc:
                        rep.section(f"{name} — could not run")
                        rep.add(f"{name} test", f"crashed: {type(exc).__name__}: {exc}", "SKIP")
                    new_sections = rep.sections[old_n:]
                    checks = [item for section in new_sections for item in section['items']]
                    category_outcomes.append({'id': name, 'items': checks})
                    self._events.put({'t': 'test_end', 'name': name, 'completed': i + 1,
                                      'total': len(tests)})
        finally:
            assessment = calculate_assessment(tests, category_outcomes, self._cancel)
            rep.assessment = assessment
            self._assessment = assessment
            verdict, vst = rep.verdict()
            if assessment['score'] is None:
                verdict, vst = 'INSUFFICIENT GRADED DATA', 'SKIP'
            elif assessment['coverage'] < 100 and vst == 'PASS':
                verdict = 'NO ISSUES IN SCORED CHECKS (PARTIAL)'
            rep.ui_verdict = (verdict, vst)
            problems = [{"section": s["title"], **i} for s in rep.sections for i in s["items"]
                        if i["status"] in ("WARN", "FAIL")]
            self._events.put({"t": "done", "verdict": verdict, "vst": vst, "counts": rep.counts(),
                              "problems": problems, "secs": round(time.time() - t0),
                              "cancelled": self._cancel, "assessment": assessment})
            self._running = False

    def poll(self):
        running = self._running  # read first, then drain: guarantees 'done' is seen
        events = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return {"running": running, "events": events}

    def cancel(self):
        self._cancel = True
        return True

    # ---- helpers ------------------------------------------------------------
    def pick_folder(self):
        """Qt bridge handles its native folder picker on the UI thread."""
        return None

    def save_report(self):
        if not self._report or not self._report.sections:
            return {"ok": False, "error": "Nothing to save yet"}
        paths = diag.export_reports(self._report, REPORT_DIR, True)
        return {"ok": True, "paths": paths, "dir": REPORT_DIR}

    def open_folder(self):
        os.makedirs(REPORT_DIR, exist_ok=True)
        try:
            if diag.IS_WIN:
                os.startfile(REPORT_DIR)  # type: ignore[attr-defined]
            elif diag.IS_MAC:
                subprocess.Popen(["open", REPORT_DIR])
            else:
                subprocess.Popen(["xdg-open", REPORT_DIR])
        except Exception:
            return False
        return True


# --------------------------------------------------------------------------- #
# React + bundled Chromium shell (Qt WebEngine, no pywebview/WebView2 dependency)
# --------------------------------------------------------------------------- #
UI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ui', 'index.html')


def main():
    from chromium_shell import run
    return run(debug='--debug' in sys.argv)


if __name__ == '__main__':
    mp.freeze_support()
    raise SystemExit(main())
