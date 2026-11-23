#!/usr/bin/env python3
"""
system_diagnostics.py - Cross-platform laptop health & performance diagnostic tool
===========================================================================

Tests
  * System overview (OS, CPU, uptime, GPU, partitions)
  * Battery health   (design vs. full-charge capacity, cycles, condition)
  * RAM              (installed modules, rated vs. running speed, channel mode,
                      bandwidth benchmark, pattern integrity test)
  * Storage / SSD    (sequential R/W, random 4K R/W, SMART health + wear)
  * CPU              (light-load stress with live temp / clock / throttle detect)
  * Network          (link state, latency, DNS)
  * Processes        (top CPU / memory consumers)
  * Live monitor     (--monitor, optional CSV logging)

Output: colour console summary + JSON + self-contained HTML report.

Install:   pip install psutil            (required)
           pip install numpy             (optional, better RAM benchmark)
           smartmontools                 (optional, SSD SMART data; needs admin/root)

Usage examples:
    python system_diagnostics.py                       # everything, default settings
    python system_diagnostics.py --quick               # ~1 minute run
    python system_diagnostics.py --tests cpu ram       # selected tests only
    python system_diagnostics.py --duration 120 --load 30
    python system_diagnostics.py --disk-path D:\\ --disk-size 2048
    python system_diagnostics.py --monitor --log live.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import html
import json
import math
import multiprocessing as mp
import os
import platform
import random
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

try:
    import psutil
except ImportError:
    sys.exit("Missing dependency. Install it with:  pip install psutil")

__version__ = "1.0.0"
IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# --------------------------------------------------------------------------- #
# Console helpers
# --------------------------------------------------------------------------- #
# PyInstaller --windowed (and pythonw.exe) have no attached console, so
# sys.stdout/sys.stderr can be None. Never assume a TTY exists at import time.
USE_COLOR = bool(getattr(sys.stdout, "isatty", lambda: False)())
if IS_WIN and USE_COLOR:
    os.system("")  # enable ANSI on interactive Windows terminals only
COLORS = {"PASS": "\033[92m", "WARN": "\033[93m", "FAIL": "\033[91m",
          "INFO": "\033[96m", "SKIP": "\033[90m", "BOLD": "\033[1m"}


def col(text: str, key: str) -> str:
    return f"{COLORS[key]}{text}\033[0m" if USE_COLOR else text


def hb(n: float) -> str:
    """Human readable bytes."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} PB"


def run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as exc:  # missing binary, timeout, ...
        return -1, "", str(exc)


def ps(command: str, timeout=40):
    """Run a PowerShell command (Windows)."""
    return run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], timeout)


def as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def is_admin() -> bool:
    try:
        if IS_WIN:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Report collector
# --------------------------------------------------------------------------- #
class Report:
    def __init__(self):
        self.sections = []
        self.cur = None

    def section(self, title: str):
        self.cur = {"title": title, "items": []}
        self.sections.append(self.cur)
        print(f"\n{col('=' * 4 + ' ' + title + ' ' + '=' * max(4, 62 - len(title)), 'BOLD')}")

    def add(self, name: str, value, status: str = "INFO", note: str = ""):
        value = str(value)
        self.cur["items"].append({"name": name, "value": value, "status": status, "note": note})
        tag = col(f"[{status:^4}]", status)
        line = f" {tag} {name:<34} {value}"
        if note:
            line += col(f"  - {note}", "SKIP")
        print(line)

    def counts(self):
        c = {"PASS": 0, "WARN": 0, "FAIL": 0}
        for s in self.sections:
            for i in s["items"]:
                if i["status"] in c:
                    c[i["status"]] += 1
        return c

    def verdict(self):
        c = self.counts()
        if c["FAIL"]:
            return "PROBLEMS FOUND", "FAIL"
        if c["WARN"]:
            return "NEEDS ATTENTION", "WARN"
        return "HEALTHY", "PASS"


# --------------------------------------------------------------------------- #
# Sensors
# --------------------------------------------------------------------------- #
_TEMP_STATE = {"backend": None, "last_fail": 0.0}


def _pick_cpu_temp(pairs):
    """pairs: [(sensor name, value C)] -> best CPU temperature."""
    pairs = [(n, v) for n, v in pairs
             if v is not None and 0 < v < 125 and not re.search(r"distance|tjmax|limit", n, re.I)]
    if not pairs:
        return None
    for n, v in pairs:
        if re.search(r"package|tctl|tdie|ccd", n, re.I):
            return v
    return max(v for _, v in pairs)


def _temp_psutil():
    if not hasattr(psutil, "sensors_temperatures"):
        return None, ""
    try:
        data = psutil.sensors_temperatures()
    except Exception:
        return None, ""
    for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal", "thinkpad", "acpitz"):
        vals = [s.current for s in data.get(key, []) if s.current is not None and 0 < s.current < 125]
        if vals:
            return max(vals), key + (" (ACPI zone, approximate)" if key == "acpitz" else "")
    return None, ""


_ACPI_CACHE = {"t": 0.0, "val": None}


def _temp_acpi_windows():
    """Last-resort Windows ACPI thermal zone (approximate, NOT die temperature).

    Cached for 5 s so we do not spawn PowerShell every telemetry tick.
    """
    now = time.time()
    if now - _ACPI_CACHE["t"] < 5:
        return _ACPI_CACHE["val"]
    _ACPI_CACHE["t"] = now
    _ACPI_CACHE["val"] = None
    rc, out, _ = ps("(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
                    "-ErrorAction SilentlyContinue).CurrentTemperature", 8)
    if rc == 0:
        vals = [int(x) / 10.0 - 273.15 for x in out.split() if x.strip().isdigit()]
        vals = [v for v in vals if 5 < v < 125]
        if vals:
            _ACPI_CACHE["val"] = (round(max(vals), 1), "ACPI thermal zone (approximate, not CPU die)")
    return _ACPI_CACHE["val"]


def get_cpu_temp_detail():
    """Single source of truth for CPU temperature (dashboard, CPU test, reports).

    Returns dict(value, src, kind, label, hottest, average, mode).  The value is
    the SAME stabilised reading that the Hardware Sensors page highlights.
    """
    if IS_WIN:
        try:
            from hardware_sensors import get_snapshot
            snap = get_snapshot()
            if snap['cpu_temp'] is not None:
                return {"value": snap['cpu_temp'], "src": snap.get('cpu_temp_src') or snap['source'],
                        "kind": snap.get('cpu_temp_kind', ''), "label": snap.get('cpu_temp_label', ''),
                        "hottest": snap.get('cpu_temp_hottest'), "average": snap.get('cpu_temp_average'),
                        "mode": snap.get('cpu_temp_mode', 'auto'),
                        "sensor_id": snap.get('cpu_temp_sensor_id', '')}
        except Exception:
            pass
        acpi = _temp_acpi_windows()
        if acpi:
            return {"value": acpi[0], "src": acpi[1], "kind": "acpi", "label": "ACPI thermal zone (approximate)",
                    "hottest": acpi[0], "average": acpi[0], "mode": "auto", "sensor_id": ""}
    temp, source = _temp_psutil()
    if temp is not None:
        return {"value": temp, "src": source, "kind": "psutil", "label": source,
                "hottest": temp, "average": temp, "mode": "auto", "sensor_id": ""}
    return {"value": None, "src": "unavailable", "kind": "", "label": "", "hottest": None,
            "average": None, "mode": "auto", "sensor_id": ""}


def get_cpu_temp():
    """Real CPU temperature -> (value, source). Backwards compatible wrapper."""
    d = get_cpu_temp_detail()
    return d["value"], d["src"]


def cur_freq():
    try:
        f = psutil.cpu_freq()
        return f.current if f else None
    except Exception:
        return None


def get_fans():
    if IS_WIN:
        try:
            from hardware_sensors import get_snapshot
            snap = get_snapshot()
            if snap['ok']:
                return [(s['device'] + ' / ' + s['name'], s['value']) for s in snap['fans']]
        except Exception:
            pass
    try:
        fans = psutil.sensors_fans() if hasattr(psutil, 'sensors_fans') else {}
        return [(f'{k} / {x.label or i}', x.current) for k, vs in fans.items()
                for i, x in enumerate(vs)]
    except Exception:
        return {}


def throttle_count() -> int | None:
    """Linux kernel thermal-throttle event counter."""
    files = glob.glob("/sys/devices/system/cpu/cpu*/thermal_throttle/core_throttle_count")
    if not files:
        return None
    total = 0
    for f in files:
        try:
            total += int(open(f).read().strip())
        except Exception:
            pass
    return total


TEMP_HINT = {
    "linux": "install lm-sensors and run `sudo sensors-detect`",
    "win": "Run this app as administrator for embedded LibreHardwareMonitor sensors; unsupported sensors remain unavailable",
    "mac": "macOS hides sensors from user space (needs `sudo powermetrics`)",
}


def temp_hint():
    return TEMP_HINT["win" if IS_WIN else "mac" if IS_MAC else "linux"]


# --------------------------------------------------------------------------- #
# 1. System overview
# --------------------------------------------------------------------------- #
def cpu_model() -> str:
    try:
        if IS_WIN:
            rc, out, _ = ps("(Get-CimInstance Win32_Processor).Name")
            if out.strip():
                return out.strip().splitlines()[0]
        elif IS_MAC:
            rc, out, _ = run(["sysctl", "-n", "machdep.cpu.brand_string"])
            if out.strip():
                return out.strip()
        else:
            for line in open("/proc/cpuinfo"):
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "unknown"


def test_info(rep: Report, args):
    rep.section("System overview")
    rep.add("OS", f"{platform.system()} {platform.release()} ({platform.machine()})")
    rep.add("Hostname", platform.node())
    rep.add("CPU", cpu_model())
    rep.add("Cores (physical / logical)", f"{psutil.cpu_count(logical=False)} / {psutil.cpu_count()}")
    f = psutil.cpu_freq()
    if f:
        rep.add("CPU clock (cur / max)", f"{f.current:.0f} / {f.max:.0f} MHz" if f.max else f"{f.current:.0f} MHz")
    rep.add("Installed RAM", hb(psutil.virtual_memory().total))
    boot = dt.datetime.fromtimestamp(psutil.boot_time())
    up = dt.datetime.now() - boot
    rep.add("Uptime", f"{up.days}d {up.seconds // 3600}h", "WARN" if up.days > 30 else "INFO",
            "consider a reboot (pending updates / memory leaks)" if up.days > 30 else "")
    rep.add("Privileges", "admin/root" if is_admin() else "standard user", "INFO",
            "" if is_admin() else "run as admin/root for RAM spec, SMART & temps on some systems")

    for part in psutil.disk_partitions(all=False):
        if IS_LINUX and part.fstype in ("squashfs", "tmpfs", "devtmpfs", "overlay"):
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        if u.total < 1 << 28:
            continue
        st = "FAIL" if u.percent > 95 else "WARN" if u.percent > 85 else "PASS"
        rep.add(f"Volume {part.mountpoint}", f"{hb(u.used)} / {hb(u.total)} ({u.percent:.0f}% used)", st,
                "low free space hurts SSD speed & wear-levelling" if st != "PASS" else "")


# --------------------------------------------------------------------------- #
# 2. Battery
# --------------------------------------------------------------------------- #
def grade_battery(rep, health, cycles=None, condition=None):
    if health is not None:
        st = "PASS" if health >= 80 else "WARN" if health >= 60 else "FAIL"
        note = {"PASS": "healthy", "WARN": "noticeably worn", "FAIL": "replace soon"}[st]
        rep.add("Battery health (full / design)", f"{health:.1f}%", st, note)
    if cycles is not None:
        st = "PASS" if cycles < 500 else "WARN" if cycles < 1000 else "FAIL"
        rep.add("Charge cycles", cycles, st, "most Li-ion packs are rated 500-1000")
    if condition:
        rep.add("Reported condition", condition, "PASS" if condition.lower() in ("normal", "good") else "WARN")


def test_battery(rep: Report, args):
    rep.section("Battery")
    b = psutil.sensors_battery()
    if b is None:
        rep.add("Battery", "not detected (desktop, or no driver access)", "SKIP")
        return
    rep.add("Charge", f"{b.percent:.0f}%  ({'plugged in' if b.power_plugged else 'on battery'})")
    if b.secsleft and b.secsleft > 0 and not b.power_plugged:
        rep.add("Estimated runtime", f"{b.secsleft // 3600}h {(b.secsleft % 3600) // 60}m")

    health = cycles = condition = None
    try:
        if IS_LINUX:
            for bat in glob.glob("/sys/class/power_supply/BAT*"):
                def rd(name):
                    try:
                        return open(os.path.join(bat, name)).read().strip()
                    except Exception:
                        return None
                full = rd("energy_full") or rd("charge_full")
                design = rd("energy_full_design") or rd("charge_full_design")
                if full and design:
                    health = float(full) / float(design) * 100
                    rep.add(f"{os.path.basename(bat)} capacity",
                            f"{float(full)/1e6:.2f} / {float(design)/1e6:.2f} (full/design, uWh or uAh /1e6)")
                cc = rd("cycle_count")
                cycles = int(cc) if cc and int(cc) > 0 else None
                for label, key in (("Model", "model_name"), ("Chemistry", "technology"), ("Status", "status")):
                    v = rd(key)
                    if v:
                        rep.add(label, v)
                break
        elif IS_WIN:
            tmp = os.path.join(tempfile.gettempdir(), "diag_batt.xml")
            run(["powercfg", "/batteryreport", "/output", tmp, "/xml"])
            root = ET.parse(tmp).getroot()
            for el in root.iter():
                if el.tag.split("}")[-1] == "Battery":
                    kids = {k.tag.split("}")[-1]: (k.text or "") for k in el}
                    if "DesignCapacity" in kids and "FullChargeCapacity" in kids:
                        d, f = float(kids["DesignCapacity"]), float(kids["FullChargeCapacity"])
                        health = f / d * 100
                        rep.add("Capacity (full / design)", f"{f:.0f} / {d:.0f} mWh")
                        cc = kids.get("CycleCount", "0")
                        cycles = int(cc) if cc.isdigit() and int(cc) > 0 else None
                        for label, key in (("Manufacturer", "Manufacturer"), ("Chemistry", "Chemistry")):
                            if kids.get(key):
                                rep.add(label, kids[key])
                        break
            try:
                os.remove(tmp)
            except OSError:
                pass
        elif IS_MAC:
            rc, out, _ = run(["system_profiler", "SPPowerDataType"])
            m = re.search(r"Maximum Capacity:\s*(\d+)%", out)
            health = float(m.group(1)) if m else None
            m = re.search(r"Cycle Count:\s*(\d+)", out)
            cycles = int(m.group(1)) if m else None
            m = re.search(r"Condition:\s*(.+)", out)
            condition = m.group(1).strip() if m else None
    except Exception as exc:
        rep.add("Detailed health", f"could not read ({exc})", "SKIP")

    if health is None and cycles is None and condition is None:
        rep.add("Detailed health", "unavailable on this system", "SKIP")
    grade_battery(rep, health, cycles, condition)


# --------------------------------------------------------------------------- #
# 3. RAM
# --------------------------------------------------------------------------- #
SMBIOS_TYPES = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 27: "LPDDR", 28: "LPDDR2",
                29: "LPDDR3", 30: "LPDDR4", 34: "DDR5", 35: "LPDDR5"}


def ram_modules():
    """Return (list of module dicts, error message)."""
    mods = []
    try:
        if IS_WIN:
            rc, out, err = ps("Get-CimInstance Win32_PhysicalMemory | Select-Object Capacity,Speed,"
                              "ConfiguredClockSpeed,Manufacturer,PartNumber,SMBIOSMemoryType | ConvertTo-Json -Compress")
            for m in as_list(json.loads(out)) if out.strip() else []:
                mods.append({"size": int(m.get("Capacity") or 0), "rated": m.get("Speed"),
                             "running": m.get("ConfiguredClockSpeed"),
                             "type": SMBIOS_TYPES.get(m.get("SMBIOSMemoryType"), "RAM"),
                             "vendor": (m.get("Manufacturer") or "").strip(),
                             "part": (m.get("PartNumber") or "").strip()})
        elif IS_LINUX:
            rc, out, err = run(["dmidecode", "-t", "memory"])
            if rc != 0:
                return None, "dmidecode needs root (run with sudo)"
            for block in out.split("\n\n"):
                if "Memory Device" not in block:
                    continue
                def g(key):
                    m = re.search(rf"^\s*{key}:\s*(.+)$", block, re.M)
                    return m.group(1).strip() if m else ""
                size = g("Size")
                if not size or "No Module" in size:
                    continue
                num = re.search(r"(\d+)\s*(MB|GB)", size)
                sz = int(num.group(1)) * (1 << 30 if num.group(2) == "GB" else 1 << 20) if num else 0
                spd = re.search(r"(\d+)", g("Speed"))
                cfg = re.search(r"(\d+)", g("Configured Memory Speed") or g("Configured Clock Speed"))
                mods.append({"size": sz, "rated": int(spd.group(1)) if spd else None,
                             "running": int(cfg.group(1)) if cfg else None, "type": g("Type"),
                             "vendor": g("Manufacturer"), "part": g("Part Number")})
        elif IS_MAC:
            rc, out, _ = run(["system_profiler", "SPMemoryDataType"])
            t = re.search(r"Type:\s*(\S+)", out)
            s = re.search(r"Speed:\s*(\d+)", out)
            sz = re.search(r"Memory:\s*(\d+)\s*GB", out)
            mods.append({"size": int(sz.group(1)) << 30 if sz else psutil.virtual_memory().total,
                         "rated": int(s.group(1)) if s else None, "running": int(s.group(1)) if s else None,
                         "type": t.group(1) if t else "", "vendor": "Apple/unified", "part": ""})
    except Exception as exc:
        return None, str(exc)
    return mods, ""


def ram_bandwidth(size_mb=128, reps=5):
    """Single-thread bandwidth in GB/s (read, write, copy)."""
    n = size_mb << 20
    res = {}

    def best(fn, traffic):
        times = []
        for _ in range(reps):
            t = time.perf_counter()
            fn()
            times.append(time.perf_counter() - t)
        return traffic / min(times) / 1e9

    try:
        import numpy as np
        a = np.ones(n // 8, dtype=np.float64)
        b = np.empty_like(a)
        res["read"] = best(lambda: a.sum(), n)
        res["write"] = best(lambda: a.fill(1.5), n)
        res["copy"] = best(lambda: np.copyto(b, a), 2 * n)
    except ImportError:
        src, dst = bytearray(n), bytearray(n)

        def cp():
            dst[:] = src
        res["copy"] = best(cp, 2 * n)
    return res


def ram_integrity(size_mb):
    n = size_mb << 20
    buf = bytearray(n)
    errors = 0
    for pat in (0x00, 0xFF, 0xAA, 0x55):
        buf[:] = bytes([pat]) * n
        errors += n - buf.count(pat)
    return errors


def test_ram(rep: Report, args):
    rep.section("RAM")
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    rep.add("Total / available", f"{hb(vm.total)} / {hb(vm.available)}",
            "WARN" if vm.percent > 90 else "PASS", f"{vm.percent:.0f}% in use")
    rep.add("Swap / page file", f"{hb(sw.used)} / {hb(sw.total)}", "WARN" if sw.percent > 60 else "INFO")

    mods, err = ram_modules()
    theoretical = None
    if mods:
        for i, m in enumerate(mods, 1):
            run_s, rated = m.get("running"), m.get("rated")
            st = "INFO"
            note = ""
            if run_s and rated and run_s < rated * 0.9:
                st, note = "WARN", "running below rated speed (enable XMP/EXPO if available)"
            rep.add(f"Module {i}", f"{hb(m['size'])} {m['type']} @ {run_s or '?'} MT/s "
                    f"(rated {rated or '?'})  {m['vendor']} {m['part']}".strip(), st, note)
        speed = max((m.get("running") or m.get("rated") or 0) for m in mods)
        if len(mods) == 1:
            rep.add("Channel mode", "single module", "WARN", "dual-channel would ~double bandwidth (iGPU/CPU perf)")
        else:
            rep.add("Channel mode", f"{len(mods)} modules (likely dual-channel)", "PASS")
        if speed:
            theoretical = speed * 8 * min(len(mods), 2) / 1000
            rep.add("Theoretical peak bandwidth", f"{theoretical:.1f} GB/s")
    else:
        rep.add("Module details", err or "unavailable", "SKIP")

    print("  ... benchmarking memory bandwidth")
    bw = ram_bandwidth(64 if args.quick else 128, 3 if args.quick else 5)
    for k, v in bw.items():
        st = "WARN" if (k == "copy" and v < 4) else "PASS"
        rep.add(f"Bandwidth {k} (1 thread)", f"{v:.1f} GB/s", st)
    if theoretical and "read" in bw:
        pct = bw["read"] / theoretical * 100
        rep.add("Efficiency vs theoretical", f"{pct:.0f}%", "INFO", "single thread typically reaches 30-70%")
    elif "read" not in bw:
        rep.add("Hint", "pip install numpy for read/write bandwidth too", "INFO")

    size = 32 if args.quick else 128
    print(f"  ... pattern integrity test on {size} MB")
    errs = ram_integrity(size)
    rep.add("Pattern integrity", f"{errs} errors", "PASS" if errs == 0 else "FAIL",
            "quick sanity check only - use MemTest86 for a full test" if errs == 0 else "FAULTY RAM / unstable overclock")


# --------------------------------------------------------------------------- #
# 4. Storage
# --------------------------------------------------------------------------- #
def open_nocache(fn, mode):
    f = open(fn, mode, buffering=0)
    try:
        fd = f.fileno()
        if hasattr(os, "posix_fadvise"):
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        elif IS_MAC:
            import fcntl
            fcntl.fcntl(fd, getattr(fcntl, "F_NOCACHE", 48), 1)
    except Exception:
        pass
    return f


def tier(mbps):
    if mbps >= 2000:
        return "PASS", "NVMe-class"
    if mbps >= 400:
        return "PASS", "SATA-SSD-class"
    if mbps >= 100:
        return "WARN", "HDD-class / slow"
    return "FAIL", "very slow"


def disk_bench(rep: Report, args):
    base = args.disk_path or os.path.expanduser("~")
    size_mb = max(64, args.disk_size - args.disk_size % 4)
    try:
        free = shutil.disk_usage(base).free
    except Exception as exc:
        rep.add("Benchmark", f"path unusable: {exc}", "SKIP")
        return
    if free < size_mb * (1 << 20) * 2:
        size_mb = max(64, int(free / (1 << 20) / 4) // 4 * 4)
        rep.add("Test size reduced", f"{size_mb} MB (low free space)", "WARN")
    fn = os.path.join(base, f".diag_{os.getpid()}.bin")
    chunk = 4 << 20
    block = os.urandom(chunk)
    total = size_mb << 20
    try:
        print(f"  ... sequential write ({size_mb} MB) on {base}")
        t = time.perf_counter()
        with open(fn, "wb", buffering=0) as f:
            for _ in range(total // chunk):
                f.write(block)
            os.fsync(f.fileno())
        w = total / (time.perf_counter() - t) / 1e6
        st, lbl = tier(w)
        rep.add("Sequential write", f"{w:.0f} MB/s", st, lbl)

        print("  ... sequential read")
        t = time.perf_counter()
        with open_nocache(fn, "rb") as f:
            while f.read(chunk):
                pass
        r = total / (time.perf_counter() - t) / 1e6
        st, lbl = tier(r)
        rep.add("Sequential read", f"{r:.0f} MB/s", st, lbl + (" (may be cache-inflated on Windows; raise --disk-size)" if IS_WIN else ""))

        secs = 2 if args.quick else 4
        pages = total // 4096
        print("  ... random 4K read / write (QD1)")
        ops, end = 0, time.perf_counter() + secs
        with open_nocache(fn, "rb") as f:
            while time.perf_counter() < end:
                f.seek(random.randrange(pages) * 4096)
                f.read(4096)
                ops += 1
        riops = ops / secs
        rep.add("Random 4K read", f"{riops:,.0f} IOPS ({riops * 4096 / 1e6:.1f} MB/s)",
                "PASS" if riops > 5000 else "WARN" if riops > 1000 else "FAIL",
                "Python-limited ceiling; use relative to similar laptops")

        ops, end = 0, time.perf_counter() + secs
        data = os.urandom(4096)
        with open(fn, "r+b", buffering=0) as f:
            while time.perf_counter() < end:
                f.seek(random.randrange(pages) * 4096)
                f.write(data)
                ops += 1
                if ops % 32 == 0:
                    os.fsync(f.fileno())
            os.fsync(f.fileno())
        wiops = ops / secs
        rep.add("Random 4K write", f"{wiops:,.0f} IOPS ({wiops * 4096 / 1e6:.1f} MB/s)",
                "PASS" if wiops > 2000 else "WARN" if wiops > 500 else "FAIL", "synced every 32 writes")
    finally:
        try:
            os.remove(fn)
        except OSError:
            pass


def smart_check(rep: Report):
    if not shutil.which("smartctl"):
        if IS_WIN:
            rc, out, _ = ps("Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,"
                            "OperationalStatus,Size | ConvertTo-Json -Compress")
            try:
                for d in as_list(json.loads(out)):
                    ok = d.get("HealthStatus") == "Healthy"
                    rep.add(f"Disk {d.get('FriendlyName')}", f"{d.get('MediaType')} {hb(d.get('Size') or 0)} - "
                            f"{d.get('HealthStatus')}", "PASS" if ok else "FAIL")
                rep.add("Detailed SMART", "install smartmontools for wear / error counters", "INFO")
                return
            except Exception:
                pass
        rep.add("SMART health", "smartctl not found - install smartmontools", "SKIP")
        return
    rc, out, _ = run(["smartctl", "--scan", "-j"])
    try:
        devices = json.loads(out).get("devices", [])
    except Exception:
        devices = []
    if not devices:
        rep.add("SMART health", "no devices found (try admin/root)", "SKIP")
        return
    for d in devices:
        cmd = ["smartctl", "-a", "-j", d["name"]] + (["-d", d["type"]] if d.get("type") else [])
        _, out, _ = run(cmd)
        try:
            j = json.loads(out)
        except Exception:
            continue
        name = j.get("model_name", d["name"])
        passed = j.get("smart_status", {}).get("passed")
        rep.add(f"{name}", "SMART PASSED" if passed else "SMART FAILED" if passed is False else "unknown",
                "PASS" if passed else "FAIL" if passed is False else "SKIP")
        if "temperature" in j:
            t = j["temperature"].get("current")
            rep.add("  Drive temperature", f"{t} °C", "PASS" if t < 60 else "WARN" if t < 70 else "FAIL")
        if "power_on_time" in j:
            rep.add("  Power-on hours", j["power_on_time"].get("hours"))
        nv = j.get("nvme_smart_health_information_log")
        if nv:
            used = nv.get("percentage_used", 0)
            rep.add("  Wear (% life used)", f"{used}%", "PASS" if used < 70 else "WARN" if used < 90 else "FAIL")
            sp = nv.get("available_spare")
            if sp is not None:
                rep.add("  Available spare", f"{sp}%", "PASS" if sp > 20 else "WARN")
            rep.add("  Media errors", nv.get("media_errors", 0), "PASS" if not nv.get("media_errors") else "FAIL")
            rep.add("  Unsafe shutdowns", nv.get("unsafe_shutdowns", 0))
            if "data_units_written" in nv:
                rep.add("  Data written", f"{nv['data_units_written'] * 512000 / 1e12:.1f} TB")
        for a in j.get("ata_smart_attributes", {}).get("table", []):
            raw = a.get("raw", {}).get("value", 0)
            if a["name"] in ("Reallocated_Sector_Ct", "Current_Pending_Sector", "Offline_Uncorrectable",
                             "Reported_Uncorrect"):
                rep.add(f"  {a['name']}", raw, "PASS" if raw == 0 else "FAIL")
            elif a["name"] in ("Wear_Leveling_Count", "Media_Wearout_Indicator", "Percent_Lifetime_Remain",
                               "SSD_Life_Left"):
                v = a.get("value", 100)
                rep.add(f"  {a['name']} (normalised)", f"{v}/100", "PASS" if v > 30 else "WARN" if v > 10 else "FAIL")


def test_disk(rep: Report, args):
    rep.section("Storage / SSD")
    smart_check(rep)
    disk_bench(rep, args)


# --------------------------------------------------------------------------- #
# 5. CPU load + thermals
# --------------------------------------------------------------------------- #
def _burn(idx, counters, stop):
    x = 0.5
    while not stop.is_set():
        for _ in range(20000):
            x = math.sqrt(x * x + 1.2345) * 1.000001 % 1000 + math.sin(x)
        counters[idx] += 1


def test_cpu(rep: Report, args):
    rep.section("CPU thermals under light load")
    logical = psutil.cpu_count() or 1
    workers = max(1, round(logical * args.load / 100))
    t_idle, src = get_cpu_temp()
    psutil.cpu_percent()
    time.sleep(2)
    rep.add("Idle CPU usage", f"{psutil.cpu_percent():.0f}%")
    if t_idle is None:
        rep.add("Temperature sensor", "not accessible", "WARN", temp_hint())
    else:
        rep.add("Idle temperature", f"{t_idle:.0f} °C ({src})", "PASS" if t_idle < 55 else "WARN" if t_idle < 70 else "FAIL")
    fans0 = get_fans()
    thr0 = throttle_count()
    max_freq = psutil.cpu_freq().max if psutil.cpu_freq() else None

    counters = mp.Array("Q", workers, lock=False)
    stop = mp.Event()
    procs = [mp.Process(target=_burn, args=(i, counters, stop), daemon=True) for i in range(workers)]
    print(f"  Load: {workers}/{logical} worker threads (~{workers / logical * 100:.0f}% CPU) for {args.duration}s")
    for p in procs:
        p.start()
    rows, prev = [], 0
    psutil.cpu_percent()
    t0 = time.time()
    try:
        for i in range(args.duration):
            time.sleep(max(0, t0 + i + 1 - time.time()))
            cur = sum(counters)
            rate, prev = cur - prev, cur
            temp, _ = get_cpu_temp()
            freq = cur_freq()
            cpu = psutil.cpu_percent()
            rows.append({"t": i + 1, "cpu": cpu, "freq": freq, "temp": temp, "rate": rate})
            print(f"\r  t={i + 1:>3}s  CPU {cpu:5.1f}%  clock {freq or 0:6.0f} MHz  "
                  f"temp {('%.0f' % temp) if temp is not None else ' - ':>3} °C   ", end="", flush=True)
    except KeyboardInterrupt:
        print("\n  interrupted - analysing what was collected")
    finally:
        stop.set()
        for p in procs:
            p.join(timeout=3)
            if p.is_alive():
                p.terminate()
    print()

    temps = [r["temp"] for r in rows if r["temp"] is not None]
    if temps:
        peak, avg = max(temps), statistics.mean(temps)
        q = max(1, len(temps) // 4)
        trend = statistics.mean(temps[-q:]) - statistics.mean(temps[:q])
        st = "PASS" if peak <= 75 else "WARN" if peak <= 85 else "FAIL"
        rep.add("Peak temperature", f"{peak:.0f} °C (avg {avg:.0f})", st,
                "at only light load - " + ("excellent" if st == "PASS" else "check fans/dust/thermal paste"))
        rep.add("Temperature rise during test", f"{trend:+.1f} °C", "PASS" if trend < 12 else "WARN",
                "still climbing - cooling struggles" if trend >= 12 else "")
        print("  ... cooling down 10s")
        time.sleep(10)
        t_after, _ = get_cpu_temp()
        if t_after is not None:
            drop = temps[-1] - t_after
            rep.add("Cool-down in 10s", f"{drop:+.0f} °C", "PASS" if drop >= 5 else "WARN",
                    "slow heat dissipation (dust / dried paste?)" if drop < 5 else "")

    body = rows[2:] if len(rows) > 8 else []
    if body:
        q = max(1, len(body) // 4)
        head = statistics.median(r["rate"] for r in body[:q])
        tail = statistics.median(r["rate"] for r in body[-q:])
        if head > 0:
            drop = (head - tail) / head * 100
            rep.add("Throughput start -> end", f"{-drop:+.1f}%", "PASS" if drop < 15 else "WARN" if drop < 30 else "FAIL",
                    "thermal/power throttling suspected" if drop >= 15 else "stable performance")
        freqs = [r["freq"] for r in body if r["freq"]]
        if freqs:
            note = f"max {max_freq:.0f} MHz" if max_freq else ""
            rep.add("Avg clock under load", f"{statistics.mean(freqs):.0f} MHz", "INFO", note)
    thr1 = throttle_count()
    if thr0 is not None and thr1 is not None:
        d = thr1 - thr0
        rep.add("Kernel throttle events", d, "PASS" if d == 0 else "FAIL", "CPU hit thermal limit" if d else "")
    fans1 = get_fans()
    if fans1:
        for (name, rpm) in fans1:
            rep.add(f"Fan {name}", f"{rpm:.0f} RPM", "PASS" if rpm > 0 else "WARN")
    elif not fans0:
        rep.add("Fan sensors", "not exposed by this system", "SKIP")


# --------------------------------------------------------------------------- #
# 6. GPU / network / processes
# --------------------------------------------------------------------------- #
def _num(x):
    """Parse '36.94', '36', ' 12 ', '[N/A]' -> float or None (never raises)."""
    try:
        return float(str(x).strip().replace("%", ""))
    except (TypeError, ValueError):
        return None


def _fmt(v, suffix=""):
    return "n/a" if v is None else (f"{v:.0f}{suffix}" if float(v).is_integer() else f"{v:.1f}{suffix}")


def _nvidia_sample():
    """(temp C, utilisation %) of the first NVIDIA GPU, or (None, None)."""
    if not shutil.which("nvidia-smi"):
        return None, None
    rc, out, _ = run(["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu",
                      "--format=csv,noheader,nounits"])
    if rc != 0 or not out.strip():
        return None, None
    parts = out.strip().splitlines()[0].split(",")
    return (_num(parts[0]) if len(parts) > 0 else None), (_num(parts[1]) if len(parts) > 1 else None)


def gpu_load_test(rep: Report, args):
    """Optional GPU load (0-100 %). The host app supplies args.gpu_burn(on, level);
    sensors come from args.gpu_sample() -> (temp, load) or the NVIDIA fallback."""
    level = int(_num(getattr(args, "gpu_load", 0)) or 0)
    burn = getattr(args, "gpu_burn", None)
    if level <= 0 or burn is None:
        return
    sample = getattr(args, "gpu_sample", None) or _nvidia_sample
    stop = getattr(args, "should_stop", lambda: False)
    secs = max(5, min(int(getattr(args, "gpu_duration", 20) or 20), 60))
    print(f"  Load: GPU target {level}% for {secs}s")
    temps, loads = [], []
    burn(True, level)
    try:
        for i in range(secs):
            if stop():
                break
            time.sleep(1)
            try:
                t, u = sample()
            except Exception:
                t, u = None, None
            t, u = _num(t), _num(u)
            if t is not None:
                temps.append(t)
            if u is not None:
                loads.append(u)
            print(f"\r  t={i + 1:>3}s  GPU {_fmt(u, '%'):>5}  temp {_fmt(t, ' °C'):>7}   ", end="", flush=True)
    finally:
        burn(False, 0)
        print()
    if loads:
        peak = max(loads)
        ok = peak >= max(5, level * 0.5)
        rep.add("GPU load reached", f"peak {peak:.0f}% (target {level}%)", "PASS" if ok else "WARN",
                "" if ok else "load generator may be running on a different GPU than the one monitored")
    else:
        rep.add("GPU load reached", "no utilisation sensor", "SKIP")
    if temps:
        peak = max(temps)
        rep.add("GPU peak temperature", f"{peak:.0f} °C", "PASS" if peak < 80 else "WARN" if peak < 90 else "FAIL",
                "" if peak < 80 else "check fans / dust / thermal paste")
    else:
        rep.add("GPU peak temperature", "no temperature sensor", "SKIP")


def test_gpu(rep: Report, args):
    rep.section("Graphics")
    if shutil.which("nvidia-smi"):
        rc, out, _ = run(["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,"
                          "memory.total,driver_version", "--format=csv,noheader,nounits"])
        if rc == 0 and out.strip():
            for line in out.strip().splitlines():
                cols = [x.strip() for x in line.split(",")]
                cols += ["[N/A]"] * (6 - len(cols))
                n, t, u, mu, mt, drv = cols[:6]
                tv = _num(t)
                rep.add("NVIDIA GPU", f"{n} (driver {drv})")
                rep.add("  Temp / usage / VRAM", f"{_fmt(tv, ' °C')} / {_fmt(_num(u), '%')} / {mu}/{mt} MB",
                        "PASS" if tv is None or tv < 80 else "WARN")
            gpu_load_test(rep, args)
            return
    try:
        if IS_WIN:
            _, out, _ = ps("(Get-CimInstance Win32_VideoController | ForEach-Object {$_.Name + ' (driver ' + $_.DriverVersion + ')'}) -join ';'")
            names = [x for x in out.strip().split(";") if x]
        elif IS_MAC:
            _, out, _ = run(["system_profiler", "SPDisplaysDataType"])
            names = re.findall(r"Chipset Model:\s*(.+)", out)
        else:
            _, out, _ = run(["sh", "-c", "lspci | grep -Ei 'vga|3d|display'"])
            names = [l.split(": ", 1)[-1] for l in out.strip().splitlines()]
        for n in names:
            rep.add("GPU", n.strip())
        if not names:
            rep.add("GPU", "not detected", "SKIP")
    except Exception as exc:
        rep.add("GPU", f"query failed ({exc})", "SKIP")
    gpu_load_test(rep, args)


def test_network(rep: Report, args):
    rep.section("Network")
    stats = psutil.net_if_stats()
    for name, s in stats.items():
        if s.isup and not name.lower().startswith(("lo", "loopback")):
            rep.add(f"Interface {name}", f"up, {s.speed} Mbps link" if s.speed else "up")
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        times = []
        for _ in range(4):
            try:
                t = time.perf_counter()
                socket.create_connection((host, port), timeout=2).close()
                times.append((time.perf_counter() - t) * 1000)
            except OSError:
                pass
        if times:
            avg = statistics.mean(times)
            rep.add(f"Latency to {host}", f"{avg:.0f} ms ({len(times)}/4 ok)",
                    "PASS" if avg < 60 else "WARN" if avg < 150 else "FAIL")
        else:
            rep.add(f"Latency to {host}", "unreachable", "FAIL" if host == "1.1.1.1" else "WARN")
    try:
        t = time.perf_counter()
        socket.gethostbyname("example.com")
        ms = (time.perf_counter() - t) * 1000
        rep.add("DNS lookup", f"{ms:.0f} ms", "PASS" if ms < 150 else "WARN")
    except OSError:
        rep.add("DNS lookup", "failed", "FAIL")


def test_processes(rep: Report, args):
    rep.section("Top resource consumers")
    ncpu = psutil.cpu_count() or 1
    procs = list(psutil.process_iter(["name"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    time.sleep(1)
    groups = {}
    for p in procs:
        name = p.info.get("name") or "?"
        if p.pid == 0 or name.lower() in ("system idle process", "idle"):
            continue                              # pseudo-process, not real load
        try:
            cpu = p.cpu_percent(None) / ncpu      # % of the whole machine, not of one core
            rss = p.memory_info().rss
        except Exception:
            continue
        g = groups.setdefault(name, [0.0, 0, 0])
        g[0] += cpu
        g[1] += rss
        g[2] += 1
    label = lambda n, g: f"{n} x{g[2]}" if g[2] > 1 else n
    for n, g in sorted(groups.items(), key=lambda kv: kv[1][0], reverse=True)[:5]:
        rep.add(f"CPU  {label(n, g)}", f"{g[0]:.1f}%", "WARN" if g[0] > 50 else "INFO")
    for n, g in sorted(groups.items(), key=lambda kv: kv[1][1], reverse=True)[:5]:
        rep.add(f"RAM  {label(n, g)}", hb(g[1]))


# --------------------------------------------------------------------------- #
# Live monitor
# --------------------------------------------------------------------------- #
def monitor(args):
    print("Live monitor - press Ctrl+C to stop\n")
    writer = fh = None
    if args.log:
        fh = open(args.log, "w", newline="")
        writer = csv.writer(fh)
        writer.writerow(["time", "cpu_%", "clock_mhz", "cpu_temp_c", "ram_%", "battery_%", "plugged",
                         "disk_read_MBps", "disk_write_MBps", "net_down_KBps", "net_up_KBps"])
    psutil.cpu_percent()
    io0, net0, t_prev = psutil.disk_io_counters(), psutil.net_io_counters(), time.time()
    try:
        while True:
            time.sleep(1)
            now = time.time()
            dtm = now - t_prev
            io1, net1 = psutil.disk_io_counters(), psutil.net_io_counters()
            rd = (io1.read_bytes - io0.read_bytes) / dtm / 1e6
            wr = (io1.write_bytes - io0.write_bytes) / dtm / 1e6
            dn = (net1.bytes_recv - net0.bytes_recv) / dtm / 1e3
            up = (net1.bytes_sent - net0.bytes_sent) / dtm / 1e3
            io0, net0, t_prev = io1, net1, now
            cpu, freq = psutil.cpu_percent(), cur_freq()
            temp, _ = get_cpu_temp()
            ram = psutil.virtual_memory().percent
            b = psutil.sensors_battery()
            bs = f"{b.percent:.0f}%{'+' if b.power_plugged else '-'}" if b else "n/a"
            line = (f"CPU {cpu:5.1f}% {freq or 0:5.0f}MHz {('%.0f°C' % temp) if temp is not None else ' n/a':>5} | "
                    f"RAM {ram:4.1f}% | BAT {bs:>5} | disk R{rd:5.1f} W{wr:5.1f} MB/s | net v{dn:6.0f} ^{up:6.0f} KB/s")
            print("\r" + line, end="", flush=True)
            if writer:
                writer.writerow([dt.datetime.now().isoformat(timespec="seconds"), cpu, freq, temp, ram,
                                 b.percent if b else "", b.power_plugged if b else "", f"{rd:.2f}", f"{wr:.2f}",
                                 f"{dn:.1f}", f"{up:.1f}"])
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if fh:
            fh.close()


# --------------------------------------------------------------------------- #
# Report export
# --------------------------------------------------------------------------- #
def export_reports(rep: Report, outdir: str, html_on: bool):
    os.makedirs(outdir, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    verdict, vst = getattr(rep, 'ui_verdict', None) or rep.verdict()
    meta = {"tool": f"system_diagnostics {__version__}", "generated": dt.datetime.now().isoformat(timespec="seconds"),
            "host": platform.node(), "verdict": verdict, "counts": rep.counts()}
    assessment = getattr(rep, 'assessment', None)
    if assessment is not None:
        meta['assessment'] = assessment
    jpath = os.path.join(outdir, f"system_diagnostics_{stamp}.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "sections": rep.sections}, f, indent=2, ensure_ascii=False)
    paths = [jpath]
    if html_on:
        badge = {"PASS": "#2e9e5b", "WARN": "#d9a400", "FAIL": "#d64545", "INFO": "#3a86c8", "SKIP": "#8a8f98"}
        parts = [f"<!doctype html><meta charset='utf-8'><title>Laptop diagnostics</title><style>"
                 "body{font:14px system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#222}"
                 "h1{margin-bottom:0}h2{border-bottom:2px solid #ddd;padding-bottom:4px;margin-top:2rem}"
                 "table{width:100%;border-collapse:collapse}td{padding:5px 8px;border-bottom:1px solid #eee;vertical-align:top}"
                 ".b{color:#fff;border-radius:4px;padding:1px 8px;font-size:12px;font-weight:600}"
                 ".n{color:#777}</style>",
                 f"<h1>Laptop diagnostics</h1><p>{html.escape(meta['host'])} &middot; {meta['generated']} &middot; "
                 f"<span class='b' style='background:{badge[vst]}'>{verdict}</span> "
                 f"({meta['counts']['PASS']} pass, {meta['counts']['WARN']} warn, {meta['counts']['FAIL']} fail)</p>"]
        if assessment is not None:
            score_text = 'Not assessed' if assessment['score'] is None else f"{assessment['score']} / 100"
            parts.append(f"<h2>Assessment score: {score_text}</h2>"
                         f"<p>Evidence coverage: {assessment['coverage']}% | Completed tests: "
                         f"{assessment['completed']} / {assessment['total']} | "
                         f"{html.escape(assessment['score_label'])}</p>"
                         f"<p>{html.escape(assessment['method'])}</p>")
        for s in rep.sections:
            parts.append(f"<h2>{html.escape(s['title'])}</h2><table>")
            for i in s["items"]:
                parts.append(f"<tr><td><span class='b' style='background:{badge[i['status']]}'>{i['status']}</span></td>"
                             f"<td>{html.escape(i['name'])}</td><td>{html.escape(i['value'])}</td>"
                             f"<td class='n'>{html.escape(i['note'])}</td></tr>")
            parts.append("</table>")
        hpath = os.path.join(outdir, f"system_diagnostics_{stamp}.html")
        with open(hpath, "w", encoding="utf-8") as f:
            f.write("".join(parts))
        paths.append(hpath)
    return paths


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
TESTS = {"info": test_info, "battery": test_battery, "ram": test_ram, "disk": test_disk,
         "cpu": test_cpu, "gpu": test_gpu, "network": test_network, "processes": test_processes}


def parse_args():
    p = argparse.ArgumentParser(description="Laptop health & performance diagnostic tool",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--tests", nargs="+", choices=list(TESTS), default=list(TESTS), help="tests to run")
    p.add_argument("--quick", action="store_true", help="shorter run (20s CPU, 256MB disk, smaller RAM tests)")
    p.add_argument("--duration", type=int, default=60, help="CPU load test length in seconds")
    p.add_argument("--load", type=int, default=25, help="target CPU load percent (share of logical cores)")
    p.add_argument("--disk-size", type=int, default=512, help="disk benchmark file size in MB")
    p.add_argument("--disk-path", default=None, help="folder on the drive to benchmark (default: home dir)")
    p.add_argument("--report-dir", default="diag_reports", help="where to save reports")
    p.add_argument("--no-report", action="store_true", help="do not write JSON/HTML files")
    p.add_argument("--no-html", action="store_true", help="skip the HTML report")
    p.add_argument("--monitor", action="store_true", help="live monitor instead of tests")
    p.add_argument("--log", default=None, help="CSV file for --monitor")
    a = p.parse_args()
    if a.quick:
        a.duration, a.disk_size = min(a.duration, 20), min(a.disk_size, 256)
    a.load = min(max(a.load, 5), 100)
    return a


def main():
    args = parse_args()
    print(col(f"Laptop Diagnostic Tool v{__version__}", "BOLD"))
    if args.monitor:
        monitor(args)
        return
    rep = Report()
    start = time.time()
    for name in TESTS:
        if name not in args.tests:
            continue
        try:
            TESTS[name](rep, args)
        except KeyboardInterrupt:
            print("\nSkipped (Ctrl+C)")
        except Exception as exc:
            if rep.cur is None or rep.cur["title"] == "":
                rep.section(name)
            rep.add(f"{name} test", f"crashed: {exc}", "SKIP")

    verdict, vst = rep.verdict()
    c = rep.counts()
    print("\n" + "=" * 70)
    print(f" OVERALL: {col(verdict, vst)}   "
          f"({c['PASS']} pass / {c['WARN']} warn / {c['FAIL']} fail)  in {time.time() - start:.0f}s")
    problems = [(s["title"], i) for s in rep.sections for i in s["items"] if i["status"] in ("WARN", "FAIL")]
    for title, i in problems:
        print(f"   {col(i['status'], i['status'])}  {title}: {i['name']} = {i['value']}"
              + (f"  ({i['note']})" if i["note"] else ""))
    print("=" * 70)
    if not args.no_report:
        for path in export_reports(rep, args.report_dir, not args.no_html):
            print(f" Report saved: {os.path.abspath(path)}")


if __name__ == "__main__":
    mp.freeze_support()
    main()
