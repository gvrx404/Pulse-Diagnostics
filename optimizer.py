"""Pulse Optimizer - reversible Windows power, startup, cleanup and repair actions.

Design rules
------------
* Nothing runs automatically. Every change is started by an explicit click.
* Toggles are reversible and every change is appended to ``optimizer_log.json``
  with the before/after state so the user can always see what Pulse touched.
* Registry and command access go through small injectable back-ends, so the
  logic can be unit-tested on any OS (see tests/test_optimizer.py).
* No registry "cleaners", no service-disabling lists, no driver tweaks.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

IS_WIN = sys.platform.startswith('win')
CREATE_NO_WINDOW = 0x08000000

# --- Windows power-setting GUIDs (documented, stable since Windows 7) -------
SUB_SLEEP = '238c9fa8-0aad-41ed-83f4-97be242c8f20'
HYBRID_SLEEP = '94ac6d29-73ce-41a6-809f-6363ba21b47e'
WAKE_TIMERS = 'bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d'
SUB_USB = '2a737441-1930-4402-8d77-b2bebba308a3'
USB_SELECTIVE = '48e6b7a6-50f5-4782-a5d4-53bb8f07e226'
PLAN_GUIDS = {
    'balanced': '381b4222-f694-41f0-9685-ff5bb260df2e',
    'performance': '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c',
    'saver': 'a1841308-3541-4fab-bc81-f71556f20b4a',
    'ultimate': 'e9a42b02-d5df-448d-aa00-03f14749eb61',
}
GUID_RE = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')

REG_POWER = r'SYSTEM\CurrentControlSet\Control\Session Manager\Power'
REG_POWER_CTRL = r'SYSTEM\CurrentControlSet\Control\Power'
REG_THROTTLE = r'SYSTEM\CurrentControlSet\Control\Power\PowerThrottling'
REG_FLYOUT = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\FlyoutMenuSettings'
RUN_PATH = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_PATH_32 = r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run'
APPROVED = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved'

DNS_PROVIDERS = {
    'google': ('Google', ('8.8.8.8', '8.8.4.4', '2001:4860:4860::8888', '2001:4860:4860::8844')),
    'cloudflare': ('Cloudflare', ('1.1.1.1', '1.0.0.1', '2606:4700:4700::1111', '2606:4700:4700::1001')),
}

GOOGLE_V4 = {'8.8.8.8', '8.8.4.4'}
CLOUDFLARE_V4 = {'1.1.1.1', '1.0.0.1'}
WARP_PATHS = (r'C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe',
              r'C:\Program Files (x86)\Cloudflare\Cloudflare WARP\warp-cli.exe')
WARP_MISSING = ('Cloudflare WARP is not installed. Install it from https://1.1.1.1/ (free), '
                'open it once to accept the terms, then try again.')

# --------------------------------------------------------------------------- #
# Catalog shown in the UI
# --------------------------------------------------------------------------- #
TOGGLES = [
    dict(id='fast_startup', group='Power & startup', title='Fast Startup',
         desc='Hybrid shutdown that saves the kernel to disk. A common cause of "shutdown does not really shut down", '
              'stuck updates, driver glitches and dual-boot/disk problems. Restart always does a full boot.',
         recommended=False, needs_admin=True, risk='safe', reboot=False),
    dict(id='hibernation', group='Power & startup', title='Hibernation',
         desc='Keeps hiberfil.sys on the system drive (often 6-16 GB). Turning it off frees that space and also disables '
              'Fast Startup. Laptops that rely on hibernate should leave it on.',
         recommended=None, needs_admin=True, risk='caution', reboot=False),
    dict(id='hybrid_sleep', group='Power & startup', title='Hybrid sleep',
         desc='Writes memory to disk while sleeping. Can cause slow or failed sleep/wake and black screens on resume.',
         recommended=False, needs_admin=True, risk='safe', reboot=False),
    dict(id='wake_timers', group='Power & startup', title='Wake timers',
         desc='Lets scheduled tasks and updates wake the PC from sleep. Turn off if the laptop wakes in the bag or overnight.',
         recommended=False, needs_admin=True, risk='safe', reboot=False),
    dict(id='usb_suspend', group='Power & startup', title='USB selective suspend',
         desc='Powers down idle USB ports. Turn off if mouse, keyboard, dock or USB devices disconnect or fail to wake.',
         recommended=None, needs_admin=True, risk='safe', reboot=False),
    dict(id='power_throttling', group='Performance', title='Windows power throttling',
         desc='Limits background-app CPU speed to save energy. Disabling can help consistency on desktops; '
              'laptops will use somewhat more battery.',
         recommended=None, needs_admin=True, risk='caution', reboot=True),
]

ACTIONS = [
    dict(id='repair_hibernation', group='Power & startup', title='Repair hibernation',
         desc='Rebuilds hiberfil.sys (fixes a missing or corrupt hibernate file) and restores Hibernate in the Start power menu.',
         needs_admin=True, risk='safe', long=False, reboot=False),
    dict(id='shrink_hiberfil', group='Power & startup', title='Shrink hibernation file',
         desc='Switches hiberfil.sys to the reduced type (about 20% of RAM). Fast Startup keeps working; full hibernate is no longer available.',
         needs_admin=True, risk='caution', long=False, reboot=False),
    dict(id='wake_report', group='Power & startup', title='Why does my PC wake / not sleep?',
         desc='Shows the last wake source, apps blocking sleep and devices allowed to wake the PC. Read-only.',
         needs_admin=True, risk='safe', long=False, reboot=False),
    dict(id='battery_report', group='Power & startup', title='Battery report (laptops)',
         desc='Creates the official Windows battery health report as an HTML file and opens it.',
         needs_admin=False, risk='safe', long=False, reboot=False),
    dict(id='clean_user_temp', group='Cleanup', title='Clear user temp files',
         desc='Deletes unlocked files from your %TEMP% folder. Files in use are skipped automatically.',
         needs_admin=False, risk='safe', long=False, reboot=False),
    dict(id='clean_win_temp', group='Cleanup', title='Clear Windows temp files',
         desc='Deletes unlocked files from C:\\Windows\\Temp. Files in use are skipped automatically.',
         needs_admin=True, risk='safe', long=False, reboot=False),
    dict(id='empty_recycle', group='Cleanup', title='Empty Recycle Bin',
         desc='Permanently removes everything in the Recycle Bin on all drives.',
         needs_admin=False, risk='caution', long=False, reboot=False),
    dict(id='flush_dns', group='Cleanup', title='Flush DNS cache',
         desc='Clears stale name lookups. Quick fix when a site will not load but the internet works.',
         needs_admin=False, risk='safe', long=False, reboot=False),
    dict(id='dns_google', group='Network', title='Use Google DNS',
         desc='Sets DNS to Google Public DNS (8.8.8.8 / 8.8.4.4, IPv6 too) on every active Wi-Fi/Ethernet adapter, then flushes the cache. '
              'Often faster and more reliable than the ISP default. Undo with \"Restore automatic DNS\".',
         needs_admin=True, risk='safe', long=False, reboot=False),
    dict(id='dns_cloudflare', group='Network', title='Use Cloudflare DNS',
         desc='Sets DNS to Cloudflare (1.1.1.1 / 1.0.0.1, IPv6 too) on every active Wi-Fi/Ethernet adapter, then flushes the cache. '
              'Undo with \"Restore automatic DNS\".',
         needs_admin=True, risk='safe', long=False, reboot=False),
    dict(id='dns_auto', group='Network', title='Restore automatic DNS',
         desc='Puts DNS back to automatic (DHCP, usually your router or ISP) on every active adapter. Use this to undo Google or Cloudflare DNS.',
         needs_admin=True, risk='safe', long=False, reboot=False),
    dict(id='warp_connect', group='Network', title='WARP: connect',
         desc='Turns on Cloudflare WARP (needs the free WARP app from 1.1.1.1 installed). Encrypts your traffic and DNS through Cloudflare. '
              'While connected, WARP handles DNS itself, so the Google/Cloudflare DNS buttons have no effect.',
         needs_admin=False, risk='safe', long=False, reboot=False),
    dict(id='warp_disconnect', group='Network', title='WARP: disconnect',
         desc='Turns Cloudflare WARP off. Disconnect before changing DNS manually, then reconnect if you want it back.',
         needs_admin=False, risk='safe', long=False, reboot=False),
    dict(id='warp_status', group='Network', title='WARP: check status',
         desc='Shows whether Cloudflare WARP is installed and connected. Read-only.',
         needs_admin=False, risk='safe', long=False, reboot=False),
    dict(id='trim_ssd', group='Maintenance', title='Optimize SSD (TRIM)',
         desc='Sends a TRIM to the system SSD so it can reclaim free blocks. Safe to run any time.',
         needs_admin=True, risk='safe', long=False, reboot=False),
    dict(id='sfc', group='Repair', title='System File Checker',
         desc='Runs sfc /scannow and repairs corrupted Windows system files. Takes 5-15 minutes.',
         needs_admin=True, risk='safe', long=True, reboot=False),
    dict(id='dism', group='Repair', title='Repair Windows image (DISM)',
         desc='Runs DISM RestoreHealth to repair the component store. Needs internet, takes 10-30 minutes. Run before SFC if SFC fails.',
         needs_admin=True, risk='safe', long=True, reboot=False),
    dict(id='net_reset', group='Repair', title='Reset network stack',
         desc='Resets Winsock and TCP/IP and flushes DNS. Fixes broken connectivity after VPN/driver changes. Restart required.',
         needs_admin=True, risk='caution', long=False, reboot=True),
]
ACTION_IDS = {a['id'] for a in ACTIONS}
TOGGLE_IDS = {t['id'] for t in TOGGLES}


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------- #
def classify_dns(adapters):
    """adapters: [{'name', 'static': [ipv4...], 'effective': [ipv4...]}] -> what DNS the PC uses now.

    'static' is what the user (or Pulse) typed in; empty means automatic/DHCP.
    """
    if not adapters:
        return {'provider': None, 'label': 'No active adapter', 'detail': '', 'adapters': []}
    kinds, shown = [], []
    for a in adapters:
        st = [s for s in a.get('static', []) if s]
        if not st:
            kinds.append('auto')
            shown += a.get('effective', [])
        else:
            s = set(st)
            kinds.append('google' if s <= GOOGLE_V4 else 'cloudflare' if s <= CLOUDFLARE_V4 else 'custom')
            shown += st
    uniq = list(dict.fromkeys(shown))
    provider = kinds[0] if len(set(kinds)) == 1 else 'mixed'
    labels = {'google': 'Google DNS', 'cloudflare': 'Cloudflare DNS', 'auto': 'Automatic (DHCP)',
              'custom': 'Custom DNS', 'mixed': 'Mixed (adapters differ)'}
    return {'provider': provider, 'label': labels[provider], 'detail': ', '.join(uniq[:4]),
            'adapters': [a['name'] for a in adapters]}


def parse_power_indexes(text):
    """Return (ac, dc) from `powercfg /query` output, independent of Windows language."""
    tokens = re.findall(r'0x[0-9a-fA-F]{8}', text or '')
    if not tokens:
        return None, None
    if len(tokens) == 1:
        v = int(tokens[0], 16)
        return v, v
    return int(tokens[-2], 16), int(tokens[-1], 16)


def parse_power_plans(text):
    """Parse `powercfg /list` -> [{'guid','name','active'}]."""
    plans = []
    for line in (text or '').splitlines():
        m = GUID_RE.search(line)
        if not m:
            continue
        name = re.search(r'\(([^)]*)\)', line)
        plans.append({'guid': m.group(0).lower(), 'name': name.group(1) if name else m.group(0),
                      'active': line.rstrip().endswith('*')})
    return plans


def approved_blob(enabled, now=None):
    """12-byte StartupApproved value: 02.. = enabled, 03.. + FILETIME = disabled."""
    if enabled:
        return bytes([2, 0, 0, 0]) + bytes(8)
    now = now if now is not None else time.time()
    filetime = int((now + 11644473600) * 10_000_000)
    return bytes([3, 0, 0, 0]) + filetime.to_bytes(8, 'little')


def blob_is_enabled(blob):
    if not blob:
        return True
    return (blob[0] & 1) == 0  # 02/06 enabled, 03/07 disabled


# Entries that belong to Windows itself. Pulse can disable these with the switch
# but never offers to delete them.
PROTECTED_NAMES = ('securityhealth', 'windowsdefender', 'windows defender', 'windows security')


def protected_reason(name, command):
    """Return a short reason when a startup entry is part of Windows, else ''."""
    low = (name or '').lower()
    if any(low.startswith(n) for n in PROTECTED_NAMES):
        return 'Windows security component'
    cmd = (command or '').lower().replace('/', '\\')
    root = os.environ.get('SystemRoot', r'C:\Windows').lower().rstrip('\\')
    if cmd.strip('" ').startswith(root + '\\'):
        return 'Windows system file'
    return ''


def safe_clean_dir(path):
    """Refuse to clean anything that is not clearly a temp directory."""
    p = Path(path).resolve()
    if len(p.parts) < 3 or p.parent == p:
        return False
    name = p.name.lower()
    return name in ('temp', 'tmp') or 'temp' in name


def clean_directory(path):
    """Delete unlocked files/folders inside *path*. Returns (bytes_freed, files_removed, skipped)."""
    if not safe_clean_dir(path):
        raise ValueError('Refusing to clean a non-temp directory: %s' % path)
    freed = removed = skipped = 0
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            fp = os.path.join(root, name)
            try:
                size = os.path.getsize(fp)
                os.remove(fp)
                freed += size
                removed += 1
            except OSError:
                skipped += 1
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except OSError:
                pass
    return freed, removed, skipped


def dir_size(path, limit_files=200000):
    total = count = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
                count += 1
                if count >= limit_files:
                    return total
    except OSError:
        pass
    return total


def human(n):
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024


def percent_in(line):
    m = re.search(r'(\d{1,3}(?:[.,]\d)?)\s*%', line or '')
    if not m:
        return None
    v = float(m.group(1).replace(',', '.'))
    return v if 0 <= v <= 100 else None


class _Decoder:
    """Incremental decoder that handles sfc's UTF-16 output and OEM text."""

    def __init__(self):
        self.mode, self.rest = None, b''

    def feed(self, chunk):
        data = self.rest + chunk
        self.rest = b''
        if self.mode is None and data:
            self.mode = 'utf-16-le' if b'\x00' in data else 'oem'
        if self.mode == 'utf-16-le':
            if len(data) % 2:
                data, self.rest = data[:-1], data[-1:]
            return data.decode('utf-16-le', errors='ignore')
        try:
            return data.decode('oem' if IS_WIN else 'utf-8', errors='replace')
        except LookupError:
            return data.decode('utf-8', errors='replace')


# --------------------------------------------------------------------------- #
# Back-ends
# --------------------------------------------------------------------------- #
class CommandRunner:
    def run(self, cmd, timeout=60):
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                               creationflags=CREATE_NO_WINDOW if IS_WIN else 0)
            dec = _Decoder()
            out = dec.feed(p.stdout or b'') + dec.feed(p.stderr or b'')
            return p.returncode, out
        except Exception as exc:  # missing binary, timeout, ...
            return -1, '%s: %s' % (type(exc).__name__, exc)

    def stream(self, cmd, on_line, timeout=3600):
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    creationflags=CREATE_NO_WINDOW if IS_WIN else 0)
        except Exception as exc:
            on_line('%s: %s' % (type(exc).__name__, exc))
            return -1
        dec, buf, started = _Decoder(), '', time.time()
        while True:
            chunk = proc.stdout.read1(512) if hasattr(proc.stdout, 'read1') else proc.stdout.read(512)
            if not chunk:
                break
            buf += dec.feed(chunk)
            parts = re.split(r'[\r\n]+', buf)
            buf = parts.pop()
            for line in parts:
                if line.strip():
                    on_line(line.strip())
            if time.time() - started > timeout:
                proc.kill()
                on_line('Timed out.')
                break
        if buf.strip():
            on_line(buf.strip())
        return proc.wait()


class WinRegistry:
    def _hive(self, name):
        import winreg
        return winreg.HKEY_LOCAL_MACHINE if name == 'HKLM' else winreg.HKEY_CURRENT_USER

    def get(self, hive, path, name):
        import winreg
        try:
            with winreg.OpenKey(self._hive(hive), path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            return None

    def set_dword(self, hive, path, name, value):
        import winreg
        with winreg.CreateKeyEx(self._hive(hive), path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))

    def set_binary(self, hive, path, name, value):
        import winreg
        with winreg.CreateKeyEx(self._hive(hive), path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_BINARY, bytes(value))

    def get_typed(self, hive, path, name):
        import winreg
        try:
            with winreg.OpenKey(self._hive(hive), path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                data, typ = winreg.QueryValueEx(k, name)
                return data, typ
        except OSError:
            return None, None

    def set_string(self, hive, path, name, value, reg_type=1):
        import winreg
        with winreg.CreateKeyEx(self._hive(hive), path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_EXPAND_SZ if reg_type == 2 else winreg.REG_SZ, str(value))

    def delete_value(self, hive, path, name):
        import winreg
        try:
            with winreg.OpenKey(self._hive(hive), path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
                winreg.DeleteValue(k, name)
            return True
        except FileNotFoundError:
            return False

    def values(self, hive, path):
        import winreg
        out = []
        try:
            with winreg.OpenKey(self._hive(hive), path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                for i in range(winreg.QueryInfoKey(k)[1]):
                    name, data, _t = winreg.EnumValue(k, i)
                    out.append((name, data))
        except OSError:
            pass
        return out


# --------------------------------------------------------------------------- #
# Optimizer
# --------------------------------------------------------------------------- #
class Optimizer:
    def __init__(self, runner=None, registry=None, is_win=None, is_admin=None, log_dir=None):
        self.win = IS_WIN if is_win is None else is_win
        self.runner = runner or CommandRunner()
        self.reg = registry or (WinRegistry() if self.win else None)
        self._is_admin = is_admin or (lambda: False)
        self.log_dir = log_dir or os.path.join(os.path.expanduser('~'), 'LaptopDiag_Reports')
        self._lock = threading.RLock()
        self._job = {'id': 0, 'action': None, 'running': False, 'lines': [], 'progress': None, 'result': None}

    # -- basics ---------------------------------------------------------------
    def admin(self):
        try:
            return bool(self._is_admin())
        except Exception:
            return False

    def _need_admin(self):
        return {'ok': False, 'error': 'Administrator rights are required. Restart Pulse as Administrator and try again.'}

    def _powercfg(self, *args, timeout=30):
        return self.runner.run(['powercfg', *args], timeout)

    def _ps(self, command, timeout=60):
        return self.runner.run(['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                '-Command', command], timeout)

    def _log(self, entry):
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            path = os.path.join(self.log_dir, 'optimizer_log.json')
            try:
                with open(path, encoding='utf-8') as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                data = []
            entry['time'] = datetime.datetime.now().isoformat(timespec='seconds')
            data = (data + [entry])[-200:]
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, indent=2)
        except OSError:
            pass

    def read_log(self, limit=25):
        try:
            with open(os.path.join(self.log_dir, 'optimizer_log.json'), encoding='utf-8') as fh:
                return json.load(fh)[-limit:][::-1]
        except (OSError, ValueError):
            return []

    # -- toggle state -----------------------------------------------------------
    def _power_state(self, sub, setting):
        rc, out = self._powercfg('/query', 'SCHEME_CURRENT', sub, setting)
        if rc != 0:
            return None
        ac, dc = parse_power_indexes(out)
        if ac is None:
            return None
        return bool(ac or dc)

    def toggle_state(self, tid):
        if not self.win:
            return None
        if tid == 'fast_startup':
            v = self.reg.get('HKLM', REG_POWER, 'HiberbootEnabled')
            return None if v is None else bool(v)
        if tid == 'hibernation':
            v = self.reg.get('HKLM', REG_POWER_CTRL, 'HibernateEnabled')
            return None if v is None else bool(v)
        if tid == 'hybrid_sleep':
            return self._power_state(SUB_SLEEP, HYBRID_SLEEP)
        if tid == 'wake_timers':
            return self._power_state(SUB_SLEEP, WAKE_TIMERS)
        if tid == 'usb_suspend':
            return self._power_state(SUB_USB, USB_SELECTIVE)
        if tid == 'power_throttling':
            v = self.reg.get('HKLM', REG_THROTTLE, 'PowerThrottlingOff')
            return not bool(v)
        return None

    def hiberfil_bytes(self):
        if not self.win:
            return None
        try:
            return os.stat(os.path.join(os.environ.get('SystemDrive', 'C:') + os.sep, 'hiberfil.sys')).st_size
        except OSError:
            return None

    def power_plans(self):
        if not self.win:
            return []
        rc, out = self._powercfg('/list')
        return parse_power_plans(out) if rc == 0 else []

    def status(self):
        toggles = []
        for t in TOGGLES:
            item = dict(t)
            item['state'] = self.toggle_state(t['id'])
            item['optimal'] = None if t['recommended'] is None or item['state'] is None else item['state'] == t['recommended']
            toggles.append(item)
        hib = self.hiberfil_bytes()
        return {'supported': self.win, 'admin': self.admin(), 'toggles': toggles,
                'actions': [dict(a) for a in ACTIONS], 'plans': self.power_plans(),
                'hiberfil': human(hib) if hib else None, 'log': self.read_log()}

    # -- toggles ------------------------------------------------------------------
    def _set_power_value(self, sub, setting, value):
        for verb in ('/setacvalueindex', '/setdcvalueindex'):
            rc, out = self._powercfg(verb, 'SCHEME_CURRENT', sub, setting, str(int(value)))
            if rc != 0:
                return False, out.strip() or 'powercfg failed'
        rc, out = self._powercfg('/setactive', 'SCHEME_CURRENT')
        return rc == 0, out.strip()

    def set_toggle(self, tid, enabled):
        if tid not in TOGGLE_IDS:
            return {'ok': False, 'error': 'Unknown option.'}
        if not self.win:
            return {'ok': False, 'error': 'These optimizations are available on Windows only.'}
        if not self.admin():
            return self._need_admin()
        enabled = bool(enabled)
        before = self.toggle_state(tid)
        ok, err, reboot = True, '', False
        try:
            if tid == 'fast_startup':
                self.reg.set_dword('HKLM', REG_POWER, 'HiberbootEnabled', 1 if enabled else 0)
            elif tid == 'hibernation':
                rc, out = self._powercfg('/hibernate', 'on' if enabled else 'off')
                ok, err = rc == 0, out.strip()
                if ok and enabled:
                    self._powercfg('/h', '/type', 'full')
            elif tid == 'hybrid_sleep':
                ok, err = self._set_power_value(SUB_SLEEP, HYBRID_SLEEP, 1 if enabled else 0)
            elif tid == 'wake_timers':
                ok, err = self._set_power_value(SUB_SLEEP, WAKE_TIMERS, 1 if enabled else 0)
            elif tid == 'usb_suspend':
                ok, err = self._set_power_value(SUB_USB, USB_SELECTIVE, 1 if enabled else 0)
            elif tid == 'power_throttling':
                self.reg.set_dword('HKLM', REG_THROTTLE, 'PowerThrottlingOff', 0 if enabled else 1)
                reboot = True
        except Exception as exc:
            ok, err = False, '%s: %s' % (type(exc).__name__, exc)
        after = self.toggle_state(tid)
        title = next(t['title'] for t in TOGGLES if t['id'] == tid)
        self._log({'type': 'toggle', 'id': tid, 'title': title, 'before': before, 'after': after, 'ok': ok,
                   'error': err if not ok else ''})
        if not ok:
            return {'ok': False, 'error': err or 'The change could not be applied.', 'state': after}
        verb = 'enabled' if enabled else 'disabled'
        return {'ok': True, 'state': after, 'reboot': reboot,
                'message': '%s %s.%s' % (title, verb, ' Restart Windows to finish.' if reboot else '')}

    def apply_recommended(self):
        """Apply every toggle that has a recommendation and is not yet optimal."""
        if not self.win:
            return {'ok': False, 'error': 'Windows only.'}
        if not self.admin():
            return self._need_admin()
        changed, failed = [], []
        for t in TOGGLES:
            if t['recommended'] is None:
                continue
            state = self.toggle_state(t['id'])
            if state is None or state == t['recommended']:
                continue
            res = self.set_toggle(t['id'], t['recommended'])
            (changed if res['ok'] else failed).append(t['title'])
        msg = ('Applied: ' + ', '.join(changed) + '.') if changed else 'Everything recommended is already set.'
        if failed:
            msg += ' Could not change: ' + ', '.join(failed) + '.'
        return {'ok': not failed, 'message': msg, 'changed': changed, 'failed': failed}

    # -- power plans -----------------------------------------------------------------
    def set_power_plan(self, key):
        if key not in PLAN_GUIDS:
            return {'ok': False, 'error': 'Unknown power plan.'}
        if not self.win:
            return {'ok': False, 'error': 'Windows only.'}
        guid = PLAN_GUIDS[key]
        plans = self.power_plans()
        existing = next((p for p in plans if p['guid'] == guid), None)
        if existing is None and key == 'ultimate':
            existing = next((p for p in plans if 'ultimate' in p['name'].lower()), None)
            if existing:
                guid = existing['guid']
        if existing is None:
            rc, out = self._powercfg('-duplicatescheme', PLAN_GUIDS[key])
            m = GUID_RE.search(out or '')
            if rc != 0 or not m:
                return {'ok': False, 'error': 'This PC does not provide that power plan (common on Modern Standby laptops).'}
            guid = m.group(0).lower()
        rc, out = self._powercfg('/setactive', guid)
        self._log({'type': 'power_plan', 'id': key, 'ok': rc == 0})
        if rc != 0:
            return {'ok': False, 'error': out.strip() or 'Could not activate the plan.'}
        return {'ok': True, 'message': 'Power plan switched.', 'plans': self.power_plans()}

    # -- startup apps ---------------------------------------------------------------
    def _startup_sources(self):
        appdata = os.environ.get('APPDATA', '')
        progdata = os.environ.get('ProgramData', '')
        return [
            ('HKCU-Run', 'HKCU', RUN_PATH, APPROVED + r'\Run', 'Current user'),
            ('HKLM-Run', 'HKLM', RUN_PATH, APPROVED + r'\Run', 'All users'),
            ('HKLM32-Run', 'HKLM', RUN_PATH_32, APPROVED + r'\Run32', 'All users (32-bit)'),
            ('USER-Folder', 'HKCU', os.path.join(appdata, r'Microsoft\Windows\Start Menu\Programs\Startup'),
             APPROVED + r'\StartupFolder', 'Startup folder'),
            ('COMMON-Folder', 'HKLM', os.path.join(progdata, r'Microsoft\Windows\Start Menu\Programs\StartUp'),
             APPROVED + r'\StartupFolder', 'Startup folder (all users)'),
        ]

    def list_startup(self):
        if not self.win:
            return {'ok': False, 'error': 'Windows only.', 'items': []}
        items = []
        for sid, hive, path, approved, label in self._startup_sources():
            if sid.endswith('Folder'):
                try:
                    names = [n for n in os.listdir(path) if n.lower() != 'desktop.ini']
                except OSError:
                    names = []
                entries = [(n, os.path.join(path, n)) for n in names]
            else:
                entries = [(n, str(d)) for n, d in self.reg.values(hive, path)]
            for name, command in entries:
                blob = self.reg.get(hive, approved, name)
                items.append({'id': sid + '|' + name, 'name': os.path.splitext(name)[0] if sid.endswith('Folder') else name,
                              'command': command, 'source': label, 'needs_admin': hive == 'HKLM',
                              'enabled': blob_is_enabled(blob),
                              'protected': protected_reason(name, command)})
        items.sort(key=lambda i: (not i['enabled'], i['name'].lower()))
        return {'ok': True, 'items': items}

    def set_startup(self, item_id, enabled):
        if not self.win:
            return {'ok': False, 'error': 'Windows only.'}
        sid, _, name = item_id.partition('|')
        src = next((s for s in self._startup_sources() if s[0] == sid), None)
        if not src or not name:
            return {'ok': False, 'error': 'Unknown startup entry.'}
        _sid, hive, _path, approved, _label = src
        if hive == 'HKLM' and not self.admin():
            return self._need_admin()
        try:
            self.reg.set_binary(hive, approved, name, approved_blob(bool(enabled)))
        except Exception as exc:
            return {'ok': False, 'error': '%s: %s' % (type(exc).__name__, exc)}
        self._log({'type': 'startup', 'id': item_id, 'enabled': bool(enabled), 'ok': True})
        return {'ok': True, 'message': '%s will %s at sign-in.' % (name, 'start' if enabled else 'no longer start')}

    # -- remove / restore startup entries --------------------------------------------
    def _removed_path(self):
        return os.path.join(self.log_dir, 'startup_removed.json')

    def _read_removed(self):
        try:
            with open(self._removed_path(), encoding='utf-8') as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _write_removed(self, data):
        os.makedirs(self.log_dir, exist_ok=True)
        with open(self._removed_path(), 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2)

    def list_removed_startup(self):
        return {'ok': True, 'items': self._read_removed()[::-1]}

    def remove_startup(self, item_id):
        """Delete a startup entry (Run value or Startup-folder shortcut) after saving a backup.

        Nothing is uninstalled. The backup lets the entry be restored later.
        """
        if not self.win:
            return {'ok': False, 'error': 'Windows only.'}
        sid, _, name = item_id.partition('|')
        src = next((x for x in self._startup_sources() if x[0] == sid), None)
        if not src or not name:
            return {'ok': False, 'error': 'Unknown startup entry.'}
        _sid, hive, path, approved, label = src
        if hive == 'HKLM' and not self.admin():
            return self._need_admin()
        is_folder = sid.endswith('Folder')
        record = {'bid': '%d-%s' % (int(time.time() * 1000), re.sub(r'\W+', '', name)[:20]), 'sid': sid,
                  'name': name, 'source': label, 'hive': hive, 'path': path, 'approved': approved,
                  'kind': 'file' if is_folder else 'reg'}
        try:
            if is_folder:
                original = os.path.join(path, name)
                if not os.path.exists(original):
                    return {'ok': False, 'error': 'That entry no longer exists.'}
                reason = protected_reason(name, original)
                if reason:
                    return {'ok': False, 'error': '%s is a %s. Use the switch to disable it instead.' % (name, reason.lower())}
                backup_dir = os.path.join(self.log_dir, 'startup_backup')
                os.makedirs(backup_dir, exist_ok=True)
                backup = os.path.join(backup_dir, record['bid'] + '_' + name)
                shutil.move(original, backup)
                record.update(command=original, backup_file=backup)
                display = os.path.splitext(name)[0]
            else:
                data, reg_type = self.reg.get_typed(hive, path, name)
                if data is None:
                    return {'ok': False, 'error': 'That entry no longer exists.'}
                reason = protected_reason(name, str(data))
                if reason:
                    return {'ok': False, 'error': '%s is a %s. Use the switch to disable it instead.' % (name, reason.lower())}
                record.update(command=str(data), data=str(data), reg_type=int(reg_type or 1))
                self.reg.delete_value(hive, path, name)
                display = name
            try:  # drop the matching enabled/disabled flag as well, ignoring failures
                self.reg.delete_value(hive, approved, name)
            except Exception:
                pass
        except Exception as exc:
            return {'ok': False, 'error': '%s: %s' % (type(exc).__name__, exc)}
        removed = self._read_removed()
        removed.append(record)
        try:
            self._write_removed(removed[-100:])
        except OSError:
            pass
        self._log({'type': 'startup_remove', 'id': item_id, 'title': display, 'ok': True,
                   'message': 'Removed from startup (backup kept, can be restored).'})
        return {'ok': True, 'message': '%s removed from startup. You can restore it from "Removed startup apps".' % display}

    def restore_startup(self, bid):
        if not self.win:
            return {'ok': False, 'error': 'Windows only.'}
        removed = self._read_removed()
        rec = next((r for r in removed if r.get('bid') == bid), None)
        if not rec:
            return {'ok': False, 'error': 'Backup not found.'}
        if rec['hive'] == 'HKLM' and not self.admin():
            return self._need_admin()
        try:
            if rec['kind'] == 'file':
                target = rec['command']
                if os.path.exists(target):
                    return {'ok': False, 'error': 'A file with that name already exists in the Startup folder.'}
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(rec['backup_file'], target)
            else:
                self.reg.set_string(rec['hive'], rec['path'], rec['name'], rec['data'], rec.get('reg_type', 1))
            self.reg.set_binary(rec['hive'], rec['approved'], rec['name'], approved_blob(True))
        except Exception as exc:
            return {'ok': False, 'error': '%s: %s' % (type(exc).__name__, exc)}
        try:
            self._write_removed([r for r in removed if r.get('bid') != bid])
        except OSError:
            pass
        self._log({'type': 'startup_restore', 'id': rec['sid'] + '|' + rec['name'], 'title': rec['name'], 'ok': True})
        shown = os.path.splitext(rec['name'])[0] if rec['kind'] == 'file' else rec['name']
        return {'ok': True, 'message': '%s restored to startup.' % shown}

    def forget_removed_startup(self, bid):
        """Discard a backup for good (also deletes the saved shortcut file)."""
        removed = self._read_removed()
        rec = next((r for r in removed if r.get('bid') == bid), None)
        if not rec:
            return {'ok': False, 'error': 'Backup not found.'}
        try:
            if rec.get('backup_file') and os.path.exists(rec['backup_file']):
                os.remove(rec['backup_file'])
            self._write_removed([r for r in removed if r.get('bid') != bid])
        except OSError as exc:
            return {'ok': False, 'error': str(exc)}
        return {'ok': True, 'message': 'Backup deleted.'}

    # -- one-shot actions ------------------------------------------------------------
    def cleanup_sizes(self):
        import tempfile
        user = dir_size(tempfile.gettempdir()) if self.win else 0
        win = dir_size(os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'Temp')) if self.win else 0
        return {'user_temp': human(user), 'win_temp': human(win), 'total': human(user + win)}

    def run_action(self, aid, report=print):
        """Execute one catalog action. *report* receives progress lines."""
        meta = next((a for a in ACTIONS if a['id'] == aid), None)
        if meta is None:
            return {'ok': False, 'error': 'Unknown action.'}
        if not self.win:
            return {'ok': False, 'error': 'These tools are available on Windows only.'}
        if meta['needs_admin'] and not self.admin():
            return self._need_admin()
        try:
            result = getattr(self, '_do_' + aid)(report)
        except Exception as exc:
            result = {'ok': False, 'error': '%s: %s' % (type(exc).__name__, exc)}
        self._log({'type': 'action', 'id': aid, 'title': meta['title'], 'ok': bool(result.get('ok')),
                   'message': result.get('message', result.get('error', ''))[:200]})
        if meta['reboot'] and result.get('ok'):
            result['reboot'] = True
        return result

    def _do_repair_hibernation(self, report):
        report('Turning hibernation off to discard the old file...')
        self._powercfg('/hibernate', 'off')
        report('Recreating hiberfil.sys (full)...')
        rc, out = self._powercfg('/hibernate', 'on')
        if rc != 0:
            return {'ok': False, 'error': out.strip() or 'Hibernation could not be re-enabled (not supported by this PC/firmware?).'}
        self._powercfg('/h', '/type', 'full')
        try:
            self.reg.set_dword('HKLM', REG_FLYOUT, 'ShowHibernateOption', 1)
        except Exception:
            pass
        return {'ok': True, 'message': 'Hibernation was rebuilt and the Hibernate option restored in the power menu.'}

    def _do_shrink_hiberfil(self, report):
        rc, out = self._powercfg('/h', '/type', 'reduced')
        if rc != 0:
            return {'ok': False, 'error': out.strip() or 'Could not reduce the hibernation file.'}
        return {'ok': True, 'message': 'Hibernation file reduced. Fast Startup still works; full hibernate is unavailable until you run Repair hibernation.'}

    def _do_wake_report(self, report):
        parts = []
        for title, args in (('LAST WAKE SOURCE', ('/lastwake',)), ('APPS/DRIVERS BLOCKING SLEEP', ('/requests',)),
                            ('DEVICES ALLOWED TO WAKE THE PC', ('/devicequery', 'wake_armed'))):
            rc, out = self._powercfg(*args)
            parts.append('== %s ==\n%s' % (title, out.strip() or '(none)'))
        text = '\n\n'.join(parts)
        for line in text.splitlines():
            report(line)
        return {'ok': True, 'message': 'Wake and sleep diagnostics collected.', 'text': text}

    def _do_battery_report(self, report):
        os.makedirs(self.log_dir, exist_ok=True)
        path = os.path.join(self.log_dir, 'Pulse_BatteryReport_%s.html' % datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
        rc, out = self._powercfg('/batteryreport', '/output', path)
        if rc != 0 or not os.path.exists(path):
            return {'ok': False, 'error': out.strip() or 'No battery was found on this PC.'}
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            pass
        return {'ok': True, 'message': 'Battery report saved to ' + path, 'path': path}

    def _clean(self, path, label, report):
        if not os.path.isdir(path):
            return {'ok': False, 'error': label + ' folder was not found.'}
        report('Cleaning ' + path + ' ...')
        freed, removed, skipped = clean_directory(path)
        return {'ok': True, 'message': '%s: freed %s (%d items removed, %d in use and skipped).' % (label, human(freed), removed, skipped)}

    def _do_clean_user_temp(self, report):
        import tempfile
        return self._clean(tempfile.gettempdir(), 'User temp', report)

    def _do_clean_win_temp(self, report):
        return self._clean(os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'Temp'), 'Windows temp', report)

    def _do_empty_recycle(self, report):
        rc, out = self._ps('Clear-RecycleBin -Force -ErrorAction SilentlyContinue', 120)
        return {'ok': rc == 0, 'message': 'Recycle Bin emptied.', 'error': out.strip() or 'Could not empty the Recycle Bin.'}

    def _do_flush_dns(self, report):
        rc, out = self.runner.run(['ipconfig', '/flushdns'], 20)
        return {'ok': rc == 0, 'message': 'DNS resolver cache flushed.', 'error': out.strip()}

    def net_state(self):
        """Current DNS provider and WARP state, so the UI can enable/disable the right buttons."""
        state = {'supported': self.win,
                 'warp': {'installed': False, 'connected': None, 'text': ''},
                 'dns': {'provider': None, 'label': 'Unknown', 'detail': '', 'adapters': []}}
        if not self.win:
            return state
        exe = self._warp_cli()
        if exe:
            rc, out = self.runner.run([exe, 'status'], 15)
            state['warp'] = {'installed': True, 'connected': self._warp_connected(out) if rc == 0 else None,
                             'text': ' | '.join(l.strip() for l in out.splitlines() if l.strip())[:160]}
        script = (
            "$ErrorActionPreference='SilentlyContinue'; "
            "foreach($x in @(Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Up' })){ "
            "$p=Get-ItemProperty ('HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters\\Interfaces\\'+$x.InterfaceGuid); "
            "$e=(Get-DnsClientServerAddress -InterfaceIndex $x.ifIndex -AddressFamily IPv4).ServerAddresses -join ','; "
            "Write-Output ('ADAPTER|'+$x.Name+'|'+$p.NameServer+'|'+$e) }")
        rc, out = self._ps(script, 30)
        adapters = []
        for line in out.splitlines():
            if line.startswith('ADAPTER|'):
                _t, name, static, eff = (line.strip().split('|', 3) + ['', '', ''])[:4]
                adapters.append({'name': name,
                                 'static': [s for s in re.split(r'[,\s]+', static) if s],
                                 'effective': [s for s in re.split(r'[,\s]+', eff) if s]})
        state['dns'] = classify_dns(adapters)
        return state

    def _set_dns(self, report, provider):
        """Point every connected physical adapter at *provider* DNS, or back to automatic (None)."""
        if provider:
            label, servers = DNS_PROVIDERS[provider]
            apply_cmd = "Set-DnsClientServerAddress -InterfaceIndex $x.ifIndex -ServerAddresses (%s)" % \
                ','.join("'%s'" % s for s in servers)
        else:
            label = 'automatic (DHCP)'
            apply_cmd = 'Set-DnsClientServerAddress -InterfaceIndex $x.ifIndex -ResetServerAddresses'
        script = (
            "$ErrorActionPreference='Stop'; "
            "$a=@(Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Up' }); "
            "if($a.Count -eq 0){ Write-Output 'NOADAPTER'; exit 3 }; "
            "foreach($x in $a){ "
            "$old=(Get-DnsClientServerAddress -InterfaceIndex $x.ifIndex -AddressFamily IPv4).ServerAddresses -join ', '; "
            "Write-Output ('BEFORE|'+$x.Name+'|'+$old); "
            + apply_cmd + "; "
            "Write-Output ('SET|'+$x.Name) }; "
            "Clear-DnsClientCache")
        now = self.net_state()
        if now['warp'].get('connected'):
            return {'ok': False, 'error': 'WARP is connected and manages DNS itself. Disconnect WARP first, then change DNS.'}
        want = provider or 'auto'
        if now['dns']['provider'] == want:
            return {'ok': True, 'message': 'Already using %s. Nothing to change.' % (label if provider else 'automatic DNS')}
        report('Applying %s DNS to active adapters...' % label)
        rc, out = self._ps(script, 90)
        names = []
        for line in out.splitlines():
            line = line.strip()
            if line.startswith('BEFORE|'):
                _tag, name, old = (line.split('|', 2) + ['', ''])[:3]
                report('%s: was %s' % (name, old or 'automatic'))
            elif line.startswith('SET|'):
                names.append(line.split('|', 1)[1])
                report('%s: now %s' % (names[-1], label))
        if 'NOADAPTER' in out:
            return {'ok': False, 'error': 'No connected Wi-Fi or Ethernet adapter was found. Connect to a network and try again.'}
        if rc != 0 or not names:
            return {'ok': False, 'error': (out.strip() or 'Could not change DNS settings.')[:300]}
        return {'ok': True, 'message': 'DNS set to %s on: %s. Cache flushed.' % (label, ', '.join(names))}

    def _do_dns_google(self, report):
        return self._set_dns(report, 'google')

    def _do_dns_cloudflare(self, report):
        return self._set_dns(report, 'cloudflare')

    def _do_dns_auto(self, report):
        return self._set_dns(report, None)

    # -- Cloudflare WARP (controls the official client; nothing is bundled) ------------
    def _warp_cli(self):
        exe = shutil.which('warp-cli')
        if exe:
            return exe
        for p in WARP_PATHS:
            if os.path.exists(p):
                return p
        return None

    @staticmethod
    def _warp_connected(text):
        low = (text or '').lower()
        return 'connected' in low and 'disconnected' not in low

    def _do_warp_status(self, report):
        exe = self._warp_cli()
        if not exe:
            return {'ok': False, 'error': WARP_MISSING}
        rc, out = self.runner.run([exe, 'status'], 20)
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        for l in lines:
            report(l)
        return {'ok': rc == 0 and bool(lines), 'message': ' | '.join(lines)[:200] or 'WARP status read.',
                'error': ' | '.join(lines)[:200] or 'Could not read WARP status.'}

    def _do_warp_connect(self, report):
        exe = self._warp_cli()
        if not exe:
            return {'ok': False, 'error': WARP_MISSING}
        _rc, cur = self.runner.run([exe, 'status'], 15)
        if self._warp_connected(cur):
            return {'ok': True, 'message': 'WARP is already connected.'}
        report('Connecting WARP...')
        rc, out = self.runner.run([exe, 'connect'], 40)
        low = out.lower()
        if rc != 0 and 'registration' in low:
            report('Registering this device with Cloudflare WARP...')
            rc2, out2 = self.runner.run([exe, 'registration', 'new'], 90)
            if rc2 != 0:
                return {'ok': False, 'error': (out2.strip() or 'WARP registration failed.')[:300]}
            rc, out = self.runner.run([exe, 'connect'], 40)
            low = out.lower()
        if rc != 0:
            if 'terms' in low or 'tos' in low:
                return {'ok': False, 'error': 'Open the Cloudflare WARP app once and accept its terms, then try again.'}
            return {'ok': False, 'error': (out.strip() or 'WARP could not connect.')[:300]}
        status = ''
        for _ in range(6):
            _rc, status = self.runner.run([exe, 'status'], 20)
            if self._warp_connected(status):
                report('WARP is connected.')
                return {'ok': True, 'message': 'WARP connected. Traffic and DNS now go through Cloudflare.'}
            time.sleep(1)
        return {'ok': False, 'error': 'Connect was sent but WARP is not connected yet. Status: %s' % (status.strip()[:150] or 'unknown')}

    def _do_warp_disconnect(self, report):
        exe = self._warp_cli()
        if not exe:
            return {'ok': False, 'error': WARP_MISSING}
        _rc, cur = self.runner.run([exe, 'status'], 15)
        if cur.strip() and not self._warp_connected(cur):
            return {'ok': True, 'message': 'WARP is already disconnected.'}
        rc, out = self.runner.run([exe, 'disconnect'], 30)
        return {'ok': rc == 0, 'message': 'WARP disconnected.',
                'error': (out.strip() or 'Could not disconnect WARP.')[:300]}

    def _do_trim_ssd(self, report):
        drive = (os.environ.get('SystemDrive', 'C:') or 'C:')[0]
        report('Running TRIM on %s: ...' % drive)
        rc, out = self._ps('Optimize-Volume -DriveLetter %s -ReTrim -ErrorAction Stop' % drive, 300)
        return {'ok': rc == 0, 'message': 'TRIM completed on %s:.' % drive,
                'error': out.strip() or 'TRIM is not supported on this drive (hard disks are skipped).'}

    def _stream(self, cmd, report):
        def on_line(line):
            pct = percent_in(line)
            self._job['progress'] = pct if pct is not None else self._job.get('progress')
            report(line)
        return self.runner.stream(cmd, on_line)

    def _do_sfc(self, report):
        rc = self._stream(['sfc', '/scannow'], report)
        return {'ok': rc == 0, 'message': 'System File Checker finished (exit %s). See the log above for details.' % rc,
                'error': 'System File Checker reported a problem (exit %s). Run DISM repair, then SFC again.' % rc}

    def _do_dism(self, report):
        rc = self._stream(['DISM', '/Online', '/Cleanup-Image', '/RestoreHealth'], report)
        return {'ok': rc == 0, 'message': 'DISM image repair finished.',
                'error': 'DISM reported a problem (exit %s). Check your internet connection and try again.' % rc}

    def _do_net_reset(self, report):
        ok = True
        for cmd in (['netsh', 'winsock', 'reset'], ['netsh', 'int', 'ip', 'reset'], ['ipconfig', '/flushdns']):
            report('Running ' + ' '.join(cmd))
            rc, out = self.runner.run(cmd, 60)
            ok = ok and rc == 0
        return {'ok': ok, 'message': 'Network stack reset. Restart Windows to finish.',
                'error': 'One or more network reset steps failed.'}

    # -- background jobs (for long actions) ---------------------------------------------
    def start_job(self, aid):
        with self._lock:
            if self._job['running']:
                return {'ok': False, 'error': 'Another task is still running.'}
            meta = next((a for a in ACTIONS if a['id'] == aid), None)
            if meta is None:
                return {'ok': False, 'error': 'Unknown action.'}
            self._job = {'id': self._job['id'] + 1, 'action': aid, 'running': True, 'lines': [], 'progress': None, 'result': None}
            job = self._job

        def work():
            def report(line):
                job['lines'] = (job['lines'] + [str(line)])[-300:]
            res = self.run_action(aid, report)
            job['result'] = res
            job['running'] = False
        threading.Thread(target=work, daemon=True).start()
        return {'ok': True, 'job': job['id']}

    def poll_job(self):
        j = self._job
        return {'id': j['id'], 'action': j['action'], 'running': j['running'], 'lines': list(j['lines'][-60:]),
                'progress': j['progress'], 'result': j['result']}
