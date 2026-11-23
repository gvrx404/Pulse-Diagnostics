"""Embedded LibreHardwareMonitor sensor bridge for Python/Windows.

Loads only the official .NET hardware DLL from vendor/lhm. Driver/firmware
support and administrator rights determine which real sensors are accessible.
Raw readings are preserved in the explorer/export even if unreliable.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

BASE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
LIB_DIR = BASE / 'vendor' / 'lhm'
LIB_PATH = LIB_DIR / 'LibreHardwareMonitorLib.dll'


def _number(value):
    if value is None:
        return None
    try:
        f = float(value)
        return round(f, 3) if -1e12 < f < 1e12 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _unit(kind):
    return {
        'Temperature': '°C', 'Load': '%', 'Clock': 'MHz',
        'Fan': 'RPM', 'Power': 'W', 'Voltage': 'V',
        'Current': 'A', 'Energy': 'mWh', 'Data': 'GB',
        'SmallData': 'MB', 'Throughput': 'B/s', 'Control': '%',
        'Level': '%', 'Factor': '', 'Frequency': 'Hz',
        'TimeSpan': 's', 'Conductivity': 'S/m', 'Humidity': '%',
        'Flow': 'L/h', 'Noise': 'dB', 'Raw': '',
    }.get(kind, '')


def _invalid_cpu_reading(item):
    """Flag suspicious zero/negative CPU readings without deleting raw data."""
    if item['category'] != 'cpu' or item['value'] is None:
        return ''
    if item['type'] == 'Temperature' and not (5 <= item['value'] <= 125):
        return 'Not a plausible running CPU temperature'
    if item['type'] == 'Power' and item['value'] <= 0:
        return 'Zero/negative CPU power reading; not trusted as package power'
    return ''


def _choose(sensors, category, kind, names, *, min_value=None, max_value=None):
    """Pick a valid *measured* sensor, not a zero placeholder."""
    available = []
    for s in sensors:
        if s['category'] != category or s['type'] != kind or s['value'] is None:
            continue
        v = s['value']
        if min_value is not None and v < min_value:
            continue
        if max_value is not None and v > max_value:
            continue
        name = s['name'].lower()
        if any(x in name for x in ('distance', 'tjmax', 'limit', 'threshold')):
            continue
        available.append(s)
    # Priority order by explicit hardware sensor labels, rather than the first sensor.
    for label in names:
        for sensor in available:
            if label in sensor['name'].lower():
                return sensor
    return available[0] if available else None



def _is_ryzen_igpu(device):
    """Recognize Ryzen integrated Radeon GPUs; reject discrete AMD RX / NVIDIA GPUs."""
    import re
    name = re.sub(r'[^a-z0-9]+', ' ', str(device).lower()).strip()
    words = set(name.split())
    return 'radeon' in words and 'graphics' in words and 'rx' not in words


def _pick_ryzen_igpu_temp(sensors):
    """Use only the real integrated Radeon temperature, never CPU or discrete GPU."""
    candidates = [s for s in sensors
        if s.get('category') == 'gpu'
        and s.get('type') == 'Temperature'
        and _is_ryzen_igpu(s.get('device', ''))
        and s.get('value') is not None
        and 5 <= s['value'] <= 125
        and not any(bad in s.get('name', '').lower() for bad in
                    ('distance', 'limit', 'threshold', 'tjmax'))]
    for name in ('gpu core', 'gpu temperature', 'core', 'temperature', 'hot spot'):
        for candidate in candidates:
            if name in candidate['name'].lower():
                return candidate
    return candidates[0] if candidates else None

# --------------------------------------------------------------------------- #
# CPU temperature resolver (single source of truth for the whole app)
# --------------------------------------------------------------------------- #
TEMP_MODES = ('auto', 'hottest', 'average')
_TEMP_MODE = {'value': 'auto'}
_PACKAGE_LABELS = ('cpu package', 'package', 'tctl/tdie', 'tdie', 'tctl')
_CORE_LABELS = ('core max', 'core average', 'cpu core', 'core #', 'ccd')
_BAD_TEMP_WORDS = ('distance', 'tjmax', 'limit', 'threshold', 'critical', 'warning')


def set_temp_mode(mode):
    """auto = CPU package / Tctl-Tdie; hottest = hottest core/CCD; average = mean of cores."""
    if mode not in TEMP_MODES:
        raise ValueError('Unknown temperature mode: %r' % (mode,))
    _TEMP_MODE['value'] = mode
    return mode


def get_temp_mode():
    return _TEMP_MODE['value']


def _valid_cpu_temps(sensors):
    out = []
    for s in sensors:
        if s.get('category') != 'cpu' or s.get('type') != 'Temperature' or s.get('value') is None:
            continue
        if not (5 <= s['value'] <= 125):
            continue
        if any(w in s.get('name', '').lower() for w in _BAD_TEMP_WORDS):
            continue
        out.append(s)
    return out


def select_cpu_temp(sensors, mode='auto'):
    """Return dict(value, sensor, kind, label, hottest, average) or None.

    Never mixes iGPU / motherboard values into a die temperature.  Motherboard
    CPU/socket sensors are only a clearly-labelled last resort.
    """
    cpu = _valid_cpu_temps(sensors)
    core_like = [s for s in cpu if not any(l in s['name'].lower() for l in _PACKAGE_LABELS)] or cpu
    if cpu:
        hottest = max(cpu, key=lambda s: s['value'])
        individual = [s for s in cpu if any(k in s['name'].lower() for k in ('core #', 'ccd'))
                      and 'max' not in s['name'].lower() and 'average' not in s['name'].lower()]
        pool = individual or core_like
        average = round(sum(s['value'] for s in pool) / len(pool), 1) if pool else None
        chosen, kind, label = None, 'package', 'CPU package / die'
        if mode == 'hottest':
            chosen, kind, label = hottest, 'hottest', 'Hottest CPU sensor'
        elif mode == 'average' and average is not None:
            chosen, kind, label = None, 'average', 'Average of CPU cores'
        else:
            for wanted in _PACKAGE_LABELS:
                chosen = next((s for s in cpu if wanted in s['name'].lower()), None)
                if chosen:
                    break
            if chosen is None:
                for wanted in _CORE_LABELS:
                    chosen = next((s for s in cpu if wanted in s['name'].lower()), None)
                    if chosen:
                        kind, label = 'core', 'CPU core sensor'
                        break
            if chosen is None:
                chosen, kind, label = hottest, 'hottest', 'Hottest CPU sensor'
        value = average if kind == 'average' else chosen['value']
        return {'value': value, 'sensor': chosen, 'kind': kind, 'label': label,
                'hottest': hottest['value'], 'average': average,
                'src': (chosen['device'] + ' / ' + chosen['name']) if chosen else
                       (hottest['device'] + ' / mean of %d core sensors' % len(pool))}
    board = [s for s in sensors
             if s.get('category') == 'motherboard' and s.get('type') == 'Temperature'
             and s.get('value') is not None and 5 <= s['value'] <= 125
             and any(x in s.get('name', '').lower() for x in ('cpu', 'processor', 'socket'))
             and not any(w in s.get('name', '').lower() for w in _BAD_TEMP_WORDS)]
    if board:
        s = board[0]
        return {'value': s['value'], 'sensor': s, 'kind': 'socket',
                'label': 'Motherboard socket sensor (not CPU die)',
                'hottest': s['value'], 'average': s['value'],
                'src': s['device'] + ' / ' + s['name'] + ' (socket/board)'}
    return None


class TempStabiliser:
    """Median-of-3 filter that rejects one-sample glitches but follows real change.

    Ryzen 7000 and many laptops can jump 15-25 C between two reads as cores boost.
    Showing the median of the newest three samples keeps the dashboard, the CPU
    test and the history graph consistent with each other.
    """

    def __init__(self, size=3):
        self.size, self.samples, self.sensor_id = size, [], None

    def push(self, value, sensor_id=None):
        if value is None:
            self.samples.clear()
            return None
        if sensor_id != self.sensor_id:
            self.samples.clear()
            self.sensor_id = sensor_id
        self.samples = (self.samples + [value])[-self.size:]
        ordered = sorted(self.samples)
        return round(ordered[len(ordered) // 2], 1)


def _missing_reason(sensors, category, kind, *, invalid=None):
    readings = [s for s in sensors if s['category'] == category and s['type'] == kind]
    if readings and invalid and any(s['value'] is not None and invalid(s['value']) for s in readings):
        return 'The sensor returned an implausible/zero value; raw reading is in Sensor explorer.'
    if readings:
        return 'No valid reading was returned by the detected hardware sensors.'
    return 'This hardware/driver does not expose the reading through LibreHardwareMonitor.'


def pawnio_status():
    """Read the official LHM driver availability flag, not an admin-only guess.

    This check does not install, start or alter any driver.
    """
    status = {'installed': None, 'version': '', 'required': '2.2.0',
              'help': 'PawnIO is needed for many AMD Ryzen CPU die-temperature and power sensors.'}
    if sys.platform != 'win32':
        return status
    try:
        from LibreHardwareMonitor.PawnIo import PawnIo
        status['installed'] = bool(PawnIo.IsInstalled)
        version = PawnIo.Version
        status['version'] = str(version) if version is not None else ''
        return status
    except Exception:
        # Prior to the .NET runtime being initialized, also check installed
        # application metadata without trusting it as proof of working access.
        pass
    try:
        import winreg
        uninstall_root = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, uninstall_root, 0,
                                    winreg.KEY_READ | view) as root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        with winreg.OpenKey(root, winreg.EnumKey(root, index)) as entry:
                            try:
                                label, _ = winreg.QueryValueEx(entry, 'DisplayName')
                            except OSError:
                                continue
                            if 'pawnio' in str(label).lower():
                                status['installed'] = True
                                try:
                                    status['version'] = str(winreg.QueryValueEx(entry, 'DisplayVersion')[0])
                                except OSError:
                                    pass
                                return status
            except OSError:
                pass
        status['installed'] = False
    except Exception:
        pass
    return status


def _empty(error=''):
    return {'ok': False, 'source': 'unavailable', 'error': error,
            'sensors': [], 'hardware': [], 'warnings': [],
            'pawnio': pawnio_status(),
            'cpu_temp': None, 'cpu_temp_src': '', 'cpu_temp_raw': None, 'cpu_temp_kind': '',
            'cpu_temp_label': '', 'cpu_temp_sensor_id': '', 'cpu_temp_hottest': None,
            'cpu_temp_average': None, 'cpu_temp_mode': 'auto',
            'gpu_temp': None, 'gpu_temp_src': '',
            'igpu_temp': None, 'igpu_temp_src': '',
            'cpu_power': None, 'cpu_power_src': '',
            'gpu_load': None, 'gpu_load_src': '', 'fans': []}


class SensorBridge:
    def __init__(self):
        self._lock = threading.RLock()
        self._computer = None
        self._ready = False
        self._error = ''
        self._last_attempt = 0.0
        self._cached = None
        self._cached_at = 0.0
        self._dll_handle = None
        self._stab = TempStabiliser()
        self._mode_seen = None

    def _open(self):
        if self._ready:
            return True
        if sys.platform != 'win32':
            self._error = 'Embedded LibreHardwareMonitor supports Windows only.'
            return False
        if time.monotonic() - self._last_attempt < 15:
            return False
        self._last_attempt = time.monotonic()
        if not LIB_PATH.is_file():
            self._error = 'LibreHardwareMonitorLib.dll is missing; build the EXE first.'
            return False
        try:
            import pythonnet
            pythonnet.load('netfx')
            if hasattr(os, 'add_dll_directory'):
                self._dll_handle = os.add_dll_directory(str(LIB_DIR))
            import clr
            import System
            import System.Reflection
            from System import AppDomain, ResolveEventHandler

            def resolver(sender, args):
                name = str(args.Name).split(',')[0]
                candidate = LIB_DIR / (name + '.dll')
                if candidate.is_file():
                    return System.Reflection.Assembly.LoadFrom(str(candidate))
                return None

            self._resolver = ResolveEventHandler(resolver)
            AppDomain.CurrentDomain.AssemblyResolve += self._resolver
            clr.AddReference(str(LIB_PATH))
            from LibreHardwareMonitor.Hardware import Computer
            c = Computer()
            for switch in ('IsCpuEnabled', 'IsGpuEnabled', 'IsMemoryEnabled',
                           'IsMotherboardEnabled', 'IsStorageEnabled', 'IsNetworkEnabled',
                           'IsControllerEnabled', 'IsBatteryEnabled', 'IsPsuEnabled'):
                if hasattr(c, switch):
                    setattr(c, switch, True)
            c.Open()
            self._computer = c
            self._ready = True
            self._error = ''
            return True
        except Exception as exc:
            self._error = f'{type(exc).__name__}: {exc}'
            return False

    @staticmethod
    def _category(hardware_type):
        t = str(hardware_type).lower()
        if 'cpu' in t: return 'cpu'
        if 'gpu' in t: return 'gpu'
        if 'storage' in t or 'hdd' in t: return 'storage'
        if 'memory' in t: return 'memory'
        if 'motherboard' in t or 'superio' in t: return 'motherboard'
        if 'battery' in t: return 'battery'
        if 'network' in t: return 'network'
        return 'other'

    def snapshot(self, max_age=0.85):
        with self._lock:
            now = time.monotonic()
            if self._cached is not None and now - self._cached_at < max_age:
                return self._cached
            if not self._open():
                return _empty(self._error)
            sensors, hardware = [], []
            try:
                def walk(device, parent_category=None, depth=0):
                    if depth > 6:
                        return
                    device.Update()
                    category = parent_category or self._category(device.HardwareType)
                    device_name = str(device.Name)
                    hardware.append({'name': device_name, 'type': str(device.HardwareType),
                                     'category': category})
                    for sensor in device.Sensors:
                        val = _number(sensor.Value)
                        # Preserve raw zero values so the user can diagnose them.
                        item = {'id': str(sensor.Identifier), 'device': device_name,
                                'category': category, 'name': str(sensor.Name),
                                'type': str(sensor.SensorType), 'value': val,
                                'unit': _unit(str(sensor.SensorType))}
                        item['issue'] = _invalid_cpu_reading(item)
                        sensors.append(item)
                    for sub in device.SubHardware:
                        walk(sub, category, depth + 1)

                for hw in self._computer.Hardware:
                    walk(hw)
                mode = get_temp_mode()
                picked = select_cpu_temp(sensors, mode)
                temp = picked['sensor'] if picked and picked['sensor'] else None
                temp_is_socket = bool(picked and picked['kind'] == 'socket')
                if mode != self._mode_seen:
                    self._stab = TempStabiliser()
                    self._mode_seen = mode
                shown_temp = self._stab.push(picked['value'] if picked else None,
                                             (temp or {}).get('id', picked['kind'] if picked else None))
                power = _choose(sensors, 'cpu', 'Power',
                                ['cpu package', 'package', 'cpu cores', 'cpu core'], min_value=0.01)
                gpu_temp = _choose(sensors, 'gpu', 'Temperature',
                                   ['gpu core', 'gpu temperature', 'core', 'hot spot'],
                                   min_value=5, max_value=125)
                igpu_temp = _pick_ryzen_igpu_temp(sensors)
                gpu_load = _choose(sensors, 'gpu', 'Load',
                                   ['gpu core', 'gpu 3d', 'core', 'gpu'], min_value=0, max_value=100)
                fans = [s for s in sensors if s['type'] == 'Fan' and
                        s['value'] is not None and s['value'] >= 0]
                warnings = []
                driver = pawnio_status()
                if driver['installed'] is False and (picked is None or power is None):
                    warnings.append('PawnIO hardware-access driver is missing. Use Install hardware access, then restart Pulse.')
                if picked is None:
                    warnings.append('CPU temperature unavailable: ' +
                                    _missing_reason(sensors, 'cpu', 'Temperature',
                                                    invalid=lambda v: v <= 0 or v > 125))
                elif temp_is_socket:
                    warnings.append('CPU temperature uses a motherboard socket sensor (not CPU die).')
                elif picked['kind'] in ('hottest', 'core') and mode == 'auto':
                    warnings.append('No CPU package/Tctl-Tdie sensor found; showing ' + picked['label'].lower() + '.')
                if power is None:
                    warnings.append('CPU package power unavailable: ' +
                                    _missing_reason(sensors, 'cpu', 'Power',
                                                    invalid=lambda v: v <= 0))
                def src(s):
                    return (s['device'] + ' / ' + s['name']) if s else ''
                result = {'ok': True, 'source': 'LibreHardwareMonitor (embedded)',
                          'error': '', 'sensors': sensors, 'hardware': hardware, 'pawnio': driver,
                          'warnings': warnings, 'cpu_temp': shown_temp,
                          'cpu_temp_raw': picked['value'] if picked else None,
                          'cpu_temp_src': picked['src'] if picked else '',
                          'cpu_temp_kind': picked['kind'] if picked else '',
                          'cpu_temp_label': picked['label'] if picked else '',
                          'cpu_temp_sensor_id': temp['id'] if temp else '',
                          'cpu_temp_hottest': picked['hottest'] if picked else None,
                          'cpu_temp_average': picked['average'] if picked else None,
                          'cpu_temp_mode': mode,
                          'gpu_temp': gpu_temp['value'] if gpu_temp else None,
                          'gpu_temp_src': src(gpu_temp),
                          'igpu_temp': igpu_temp['value'] if igpu_temp else None,
                          'igpu_temp_src': src(igpu_temp),
                          'cpu_power': power['value'] if power else None,
                          'cpu_power_src': src(power),
                          'gpu_load': gpu_load['value'] if gpu_load else None,
                          'gpu_load_src': src(gpu_load), 'fans': fans}
                self._cached, self._cached_at = result, now
                return result
            except Exception as exc:
                self._error = f'Sensor read error: {type(exc).__name__}: {exc}'
                return _empty(self._error)

    def close(self):
        with self._lock:
            if self._computer is not None:
                try:
                    self._computer.Close()
                except Exception:
                    pass
            self._computer = None
            self._ready = False
            self._cached = None


_bridge = SensorBridge()


def get_snapshot(max_age=0.85):
    return _bridge.snapshot(max_age=max_age)


def apply_temp_mode(mode):
    set_temp_mode(mode)
    with _bridge._lock:
        _bridge._cached = None
    return mode


def close():
    _bridge.close()
