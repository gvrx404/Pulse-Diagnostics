import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hardware_sensors import select_cpu_temp, TempStabiliser, set_temp_mode, get_temp_mode


def s(name, value, category='cpu', kind='Temperature', device='CPU', sid=None):
    return {'id': sid or '/%s/%s' % (category, name), 'device': device, 'category': category,
            'name': name, 'type': kind, 'value': value}


RYZEN = [s('Core (Tctl/Tdie)', 61.5), s('CCD1 (Tdie)', 58.0), s('CPU Package', 88.0, kind='Power'),
         s('Core Max', 60.0), s('Package Distance to TjMax', 40.0)]
INTEL = [s('Core #1', 55), s('Core #2', 62), s('Core Max', 62), s('Core Average', 58.5),
         s('CPU Package', 64), s('Core #1 Distance to TjMax', 45)]


class SelectCpuTempTests(unittest.TestCase):
    def test_ryzen_prefers_tctl_tdie(self):
        r = select_cpu_temp(RYZEN, 'auto')
        self.assertEqual(r['value'], 61.5)
        self.assertEqual(r['kind'], 'package')

    def test_intel_prefers_cpu_package(self):
        r = select_cpu_temp(INTEL, 'auto')
        self.assertEqual(r['value'], 64)
        self.assertIn('CPU Package', r['src'])

    def test_hottest_mode(self):
        self.assertEqual(select_cpu_temp(INTEL, 'hottest')['value'], 64)
        self.assertEqual(select_cpu_temp(RYZEN, 'hottest')['value'], 61.5)

    def test_average_mode_uses_individual_cores(self):
        r = select_cpu_temp(INTEL, 'average')
        self.assertEqual(r['kind'], 'average')
        self.assertEqual(r['value'], 58.5)

    def test_distance_and_power_never_used(self):
        r = select_cpu_temp(INTEL + [s('Core Max Distance to TjMax', 99)], 'hottest')
        self.assertLessEqual(r['value'], 64)

    def test_implausible_values_rejected(self):
        self.assertIsNone(select_cpu_temp([s('CPU Package', 0), s('Core #1', 200)], 'auto'))

    def test_igpu_and_disk_never_substituted(self):
        sensors = [s('GPU Core', 70, category='gpu', device='AMD Radeon(TM) Graphics'),
                   s('Temperature', 40, category='storage', device='NVMe')]
        self.assertIsNone(select_cpu_temp(sensors, 'auto'))

    def test_motherboard_socket_is_last_resort_and_labelled(self):
        r = select_cpu_temp([s('CPU Socket', 47, category='motherboard', device='Board')], 'auto')
        self.assertEqual(r['kind'], 'socket')
        self.assertIn('not CPU die', r['label'])

    def test_core_fallback_when_no_package_sensor(self):
        r = select_cpu_temp([s('Core #1', 50), s('Core #2', 57)], 'auto')
        self.assertEqual(r['kind'], 'core')
        self.assertIn(r['value'], (50, 57))


class StabiliserTests(unittest.TestCase):
    def test_single_spike_is_rejected(self):
        st = TempStabiliser()
        out = [st.push(v, 'a') for v in (50, 51, 92, 52, 52)]
        self.assertEqual(out[2], 51)
        self.assertEqual(out[-1], 52)

    def test_follows_real_sustained_change(self):
        st = TempStabiliser()
        out = [st.push(v, 'a') for v in (50, 50, 80, 81, 82)]
        self.assertEqual(out[-1], 81)

    def test_sensor_change_resets_history(self):
        st = TempStabiliser()
        st.push(90, 'a'); st.push(90, 'a')
        self.assertEqual(st.push(40, 'b'), 40)

    def test_missing_value_clears_history(self):
        st = TempStabiliser()
        st.push(70, 'a')
        self.assertIsNone(st.push(None))
        self.assertEqual(st.push(40, 'a'), 40)


class ModeTests(unittest.TestCase):
    def test_modes_validate(self):
        old = get_temp_mode()
        try:
            self.assertEqual(set_temp_mode('hottest'), 'hottest')
            with self.assertRaises(ValueError):
                set_temp_mode('bogus')
        finally:
            set_temp_mode(old)


if __name__ == '__main__':
    unittest.main()
