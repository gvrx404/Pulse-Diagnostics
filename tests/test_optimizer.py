import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import optimizer as opt
from optimizer import Optimizer


class FakeRegistry:
    def __init__(self):
        self.data = {('HKLM', opt.REG_POWER, 'HiberbootEnabled'): 1,
                     ('HKLM', opt.REG_POWER_CTRL, 'HibernateEnabled'): 1}
        self.writes = []

    def get(self, hive, path, name):
        return self.data.get((hive, path, name))

    def set_dword(self, hive, path, name, value):
        self.data[(hive, path, name)] = value
        self.writes.append((hive, path, name, value))

    def set_binary(self, hive, path, name, value):
        self.data[(hive, path, name)] = value
        self.writes.append((hive, path, name, value))

    def values(self, hive, path):
        return [(n, v) for (h, p, n), v in self.data.items() if h == hive and p == path]

    def get_typed(self, hive, path, name):
        v = self.data.get((hive, path, name))
        return (v, 1) if v is not None else (None, None)

    def set_string(self, hive, path, name, value, reg_type=1):
        self.data[(hive, path, name)] = value
        self.writes.append((hive, path, name, value))

    def delete_value(self, hive, path, name):
        return self.data.pop((hive, path, name), None) is not None


class FakeRunner:
    """Pretends to be powercfg: stores per-setting AC/DC values."""

    def __init__(self):
        self.calls = []
        self.warp_on = False
        self.power = {opt.HYBRID_SLEEP: [1, 1], opt.WAKE_TIMERS: [1, 1], opt.USB_SELECTIVE: [1, 1]}
        self.plans = ('Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *\n'
                      'Power Scheme GUID: a1841308-3541-4fab-bc81-f71556f20b4a  (Power saver)\n')

    def run(self, cmd, timeout=60):
        self.calls.append(cmd)
        if cmd[0].endswith('warp-cli'):
            if cmd[1] == 'connect':
                self.warp_on = True
            elif cmd[1] == 'disconnect':
                self.warp_on = False
            return 0, ('Status update: ' + ('Connected' if self.warp_on else 'Disconnected') if cmd[1] == 'status' else '')
        if cmd[0] == 'powershell' and 'ADAPTER|' in cmd[-1]:
            return 0, getattr(self, 'adapters_out', '')
        if cmd[0] == 'powershell' and 'Set-DnsClientServerAddress' in cmd[-1]:
            return 0, 'BEFORE|Wi-Fi|192.168.1.1\nSET|Wi-Fi\n'
        if cmd[0] != 'powercfg':
            return 0, ''
        a = cmd[1:]
        if a[0] == '/query':
            ac, dc = self.power[a[3]]
            return 0, ('  Power Setting GUID: x\n    Current AC Power Setting Index: 0x%08x\n'
                       '    Current DC Power Setting Index: 0x%08x\n' % (ac, dc))
        if a[0] in ('/setacvalueindex', '/setdcvalueindex'):
            self.power[a[3]][0 if a[0] == '/setacvalueindex' else 1] = int(a[4])
            return 0, ''
        if a[0] == '/list':
            return 0, self.plans
        if a[0] == '-duplicatescheme':
            return 0, 'Power Scheme GUID: 11111111-2222-3333-4444-555555555555  (Ultimate Performance)'
        return 0, ''


def make(admin=True):
    tmp = tempfile.mkdtemp()
    r, g = FakeRunner(), FakeRegistry()
    return Optimizer(runner=r, registry=g, is_win=True, is_admin=lambda: admin, log_dir=tmp), r, g


class ParserTests(unittest.TestCase):
    def test_power_indexes_language_independent(self):
        text = 'Aktuelle Wechselstromeinstellung: 0x00000001\nAktuelle Gleichstromeinstellung: 0x00000000'
        self.assertEqual(opt.parse_power_indexes(text), (1, 0))
        self.assertEqual(opt.parse_power_indexes('nothing'), (None, None))

    def test_plan_parser(self):
        plans = opt.parse_power_plans(FakeRunner().plans)
        self.assertEqual(len(plans), 2)
        self.assertTrue(plans[0]['active'])
        self.assertFalse(plans[1]['active'])

    def test_startup_blob(self):
        self.assertTrue(opt.blob_is_enabled(opt.approved_blob(True)))
        self.assertFalse(opt.blob_is_enabled(opt.approved_blob(False)))
        self.assertEqual(len(opt.approved_blob(False)), 12)
        self.assertTrue(opt.blob_is_enabled(None))
        self.assertTrue(opt.blob_is_enabled(bytes([6]) + bytes(11)))
        self.assertFalse(opt.blob_is_enabled(bytes([7]) + bytes(11)))

    def test_percent(self):
        self.assertEqual(opt.percent_in('Verification 45% complete.'), 45.0)
        self.assertIsNone(opt.percent_in('no number'))

    def test_human(self):
        self.assertEqual(opt.human(1536), '1.5 KB')


class CleanTests(unittest.TestCase):
    def test_refuses_non_temp_dirs(self):
        self.assertFalse(opt.safe_clean_dir('/'))
        self.assertFalse(opt.safe_clean_dir('/home/user/Documents'))
        with self.assertRaises(ValueError):
            opt.clean_directory('/home')

    def test_cleans_temp_dir(self):
        base = Path(tempfile.mkdtemp()) / 'Temp'
        (base / 'sub').mkdir(parents=True)
        (base / 'a.tmp').write_bytes(b'x' * 100)
        (base / 'sub' / 'b.tmp').write_bytes(b'y' * 50)
        freed, removed, skipped = opt.clean_directory(str(base))
        self.assertEqual((freed, removed, skipped), (150, 2, 0))
        self.assertEqual(list(base.iterdir()), [])


class ToggleTests(unittest.TestCase):
    def test_status_reads_everything(self):
        o, _r, _g = make()
        st = {t['id']: t for t in o.status()['toggles']}
        self.assertTrue(st['fast_startup']['state'])
        self.assertFalse(st['fast_startup']['optimal'])
        self.assertTrue(st['hybrid_sleep']['state'])
        self.assertIsNone(st['hibernation']['optimal'])  # no recommendation: user choice

    def test_fast_startup_off_and_logged(self):
        o, _r, g = make()
        res = o.set_toggle('fast_startup', False)
        self.assertTrue(res['ok'])
        self.assertEqual(g.data[('HKLM', opt.REG_POWER, 'HiberbootEnabled')], 0)
        self.assertEqual(o.read_log()[0]['id'], 'fast_startup')
        self.assertTrue(o.set_toggle('fast_startup', True)['ok'])
        self.assertEqual(g.data[('HKLM', opt.REG_POWER, 'HiberbootEnabled')], 1)

    def test_power_settings_set_ac_and_dc(self):
        o, r, _g = make()
        self.assertTrue(o.set_toggle('hybrid_sleep', False)['ok'])
        self.assertEqual(r.power[opt.HYBRID_SLEEP], [0, 0])
        self.assertFalse(o.toggle_state('hybrid_sleep'))

    def test_hibernation_commands(self):
        o, r, _g = make()
        o.set_toggle('hibernation', False)
        self.assertIn(['powercfg', '/hibernate', 'off'], r.calls)

    def test_requires_admin(self):
        o, r, g = make(admin=False)
        res = o.set_toggle('fast_startup', False)
        self.assertFalse(res['ok'])
        self.assertEqual(g.writes, [])
        self.assertEqual(r.calls, [])

    def test_unknown_ids_rejected(self):
        o, _r, _g = make()
        self.assertFalse(o.set_toggle('format_c', True)['ok'])
        self.assertFalse(o.run_action('format_c')['ok'])

    def test_apply_recommended_only_touches_recommended(self):
        o, _r, g = make()
        res = o.apply_recommended()
        self.assertTrue(res['ok'])
        self.assertEqual(g.data[('HKLM', opt.REG_POWER, 'HiberbootEnabled')], 0)
        self.assertEqual(g.data[('HKLM', opt.REG_POWER_CTRL, 'HibernateEnabled')], 1)  # untouched
        self.assertNotIn(('HKLM', opt.REG_THROTTLE, 'PowerThrottlingOff', 1), g.writes)
        self.assertIn('already set', o.apply_recommended()['message'])

    def test_non_windows_is_inert(self):
        o = Optimizer(is_win=False, is_admin=lambda: True, log_dir=tempfile.mkdtemp())
        self.assertFalse(o.set_toggle('fast_startup', False)['ok'])
        self.assertFalse(o.status()['supported'])


class PlanTests(unittest.TestCase):
    def test_switch_existing_plan(self):
        o, r, _g = make()
        self.assertTrue(o.set_power_plan('saver')['ok'])
        self.assertIn(['powercfg', '/setactive', 'a1841308-3541-4fab-bc81-f71556f20b4a'], r.calls)

    def test_ultimate_is_duplicated_when_missing(self):
        o, r, _g = make()
        self.assertTrue(o.set_power_plan('ultimate')['ok'])
        self.assertIn(['powercfg', '/setactive', '11111111-2222-3333-4444-555555555555'], r.calls)


class StartupTests(unittest.TestCase):
    def test_list_and_disable(self):
        o, _r, g = make()
        g.data[('HKCU', opt.RUN_PATH, 'Discord')] = r'C:\Discord\Update.exe'
        items = o.list_startup()['items']
        discord = next(i for i in items if i['name'] == 'Discord')
        self.assertTrue(discord['enabled'])
        self.assertTrue(o.set_startup(discord['id'], False)['ok'])
        again = next(i for i in o.list_startup()['items'] if i['name'] == 'Discord')
        self.assertFalse(again['enabled'])
        self.assertEqual(g.data[('HKCU', opt.APPROVED + r'\Run', 'Discord')][0], 3)

    def test_hklm_entries_need_admin(self):
        o, _r, g = make(admin=False)
        g.data[('HKLM', opt.RUN_PATH, 'Vendor')] = 'x.exe'
        self.assertFalse(o.set_startup('HKLM-Run|Vendor', False)['ok'])


    def test_remove_and_restore_registry_entry(self):
        o, _r, g = make()
        g.data[('HKCU', opt.RUN_PATH, 'Spotify')] = r'C:\Users\me\Spotify.exe --autostart'
        g.data[('HKCU', opt.APPROVED + r'\Run', 'Spotify')] = opt.approved_blob(True)
        res = o.remove_startup('HKCU-Run|Spotify')
        self.assertTrue(res['ok'], res)
        self.assertNotIn(('HKCU', opt.RUN_PATH, 'Spotify'), g.data)
        self.assertFalse(any(i['name'] == 'Spotify' for i in o.list_startup()['items']))
        removed = o.list_removed_startup()['items']
        self.assertEqual(len(removed), 1)
        self.assertEqual(o.read_log()[0]['type'], 'startup_remove')
        self.assertTrue(o.restore_startup(removed[0]['bid'])['ok'])
        self.assertEqual(g.data[('HKCU', opt.RUN_PATH, 'Spotify')], r'C:\Users\me\Spotify.exe --autostart')
        self.assertEqual(o.list_removed_startup()['items'], [])

    def test_windows_components_cannot_be_removed(self):
        o, _r, g = make()
        g.data[('HKLM', opt.RUN_PATH, 'SecurityHealth')] = r'C:\Windows\system32\SecurityHealthSystray.exe'
        res = o.remove_startup('HKLM-Run|SecurityHealth')
        self.assertFalse(res['ok'])
        self.assertIn(('HKLM', opt.RUN_PATH, 'SecurityHealth'), g.data)
        self.assertTrue(o.list_startup()['items'][0]['protected'])

    def test_remove_needs_admin_for_all_users_entries(self):
        o, _r, g = make(admin=False)
        g.data[('HKLM', opt.RUN_PATH, 'Vendor')] = r'C:\Vendor\v.exe'
        self.assertFalse(o.remove_startup('HKLM-Run|Vendor')['ok'])
        self.assertIn(('HKLM', opt.RUN_PATH, 'Vendor'), g.data)

    def test_remove_and_restore_startup_folder_shortcut(self):
        o, _r, _g = make()
        folder = tempfile.mkdtemp()
        old = os.environ.get('APPDATA')
        os.environ['APPDATA'] = folder
        try:
            startup = os.path.join(folder, r'Microsoft\Windows\Start Menu\Programs\Startup')  # same join as the app
            os.makedirs(startup)
            lnk = os.path.join(startup, 'Tool.lnk')
            Path(lnk).write_text('x')
            self.assertTrue(o.remove_startup('USER-Folder|Tool.lnk')['ok'])
            self.assertFalse(os.path.exists(lnk))
            bid = o.list_removed_startup()['items'][0]['bid']
            self.assertTrue(o.restore_startup(bid)['ok'])
            self.assertTrue(os.path.exists(lnk))
        finally:
            if old is None:
                os.environ.pop('APPDATA', None)
            else:
                os.environ['APPDATA'] = old

    def test_remove_unknown_entry(self):
        o, _r, _g = make()
        self.assertFalse(o.remove_startup('HKCU-Run|Nope')['ok'])
        self.assertFalse(o.remove_startup('bogus')['ok'])


class ActionTests(unittest.TestCase):
    def test_flush_dns_and_log(self):
        o, r, _g = make()
        res = o.run_action('flush_dns')
        self.assertTrue(res['ok'])
        self.assertIn(['ipconfig', '/flushdns'], r.calls)

    def _ps_calls(self, r):
        return [c[-1] for c in r.calls if c[0] == 'powershell']

    def test_dns_google_and_cloudflare(self):
        for aid, ip in (('dns_google', '8.8.8.8'), ('dns_cloudflare', '1.1.1.1')):
            o, r, _g = make()
            res = o.run_action(aid)
            self.assertTrue(res['ok'], res)
            script = self._ps_calls(r)[-1]
            self.assertIn(ip, script)
            self.assertIn('-Physical', script)
            self.assertIn('Clear-DnsClientCache', script)

    def test_dns_auto_resets_and_needs_admin(self):
        o, r, _g = make()
        self.assertIn('-ResetServerAddresses', (o.run_action('dns_auto') and self._ps_calls(r)[-1]))
        o2, r2, _g2 = make(admin=False)
        res = o2.run_action('dns_google')
        self.assertFalse(res['ok'])
        self.assertEqual(self._ps_calls(r2), [])

    def test_warp_missing_and_present(self):
        from unittest import mock
        o, r, _g = make()
        with mock.patch.object(opt.shutil, 'which', return_value=None), \
                mock.patch.object(opt.os.path, 'exists', return_value=False):
            for aid in ('warp_connect', 'warp_disconnect', 'warp_status'):
                res = o.run_action(aid)
                self.assertFalse(res['ok'])
                self.assertIn('1.1.1.1', res['error'])
        with mock.patch.object(opt.shutil, 'which', return_value='warp-cli'):
            self.assertTrue(o.run_action('warp_connect')['ok'])
            self.assertIn(['warp-cli', 'connect'], r.calls)
            self.assertTrue(o.run_action('warp_disconnect')['ok'])
            self.assertIn(['warp-cli', 'disconnect'], r.calls)
            st = o.run_action('warp_status')
            self.assertTrue(st['ok'])
            self.assertIn('Disconnected', st['message'])

    def test_classify_dns(self):
        A = lambda st, eff=None: {'name': 'Wi-Fi', 'static': st, 'effective': eff or st}
        self.assertEqual(opt.classify_dns([A(['8.8.8.8', '8.8.4.4'])])['provider'], 'google')
        self.assertEqual(opt.classify_dns([A(['1.1.1.1'])])['provider'], 'cloudflare')
        auto = opt.classify_dns([A([], ['192.168.1.1'])])
        self.assertEqual((auto['provider'], auto['detail']), ('auto', '192.168.1.1'))
        self.assertEqual(opt.classify_dns([A(['9.9.9.9'])])['provider'], 'custom')
        self.assertEqual(opt.classify_dns([A(['1.1.1.1']), A([], ['10.0.0.1'])])['provider'], 'mixed')
        self.assertIsNone(opt.classify_dns([])['provider'])

    def test_net_state_and_smart_guards(self):
        from unittest import mock
        o, r, _g = make()
        r.adapters_out = 'ADAPTER|Wi-Fi|1.1.1.1,1.0.0.1|1.1.1.1,1.0.0.1\n'
        with mock.patch.object(opt.shutil, 'which', return_value='warp-cli'):
            s = o.net_state()
            self.assertEqual(s['dns']['provider'], 'cloudflare')
            self.assertTrue(s['warp']['installed'])
            self.assertFalse(s['warp']['connected'])
            # already Cloudflare -> no change made
            before = len(self._ps_calls(r))
            res = o.run_action('dns_cloudflare')
            self.assertTrue(res['ok'])
            self.assertIn('Already', res['message'])
            self.assertFalse(any('Set-DnsClientServerAddress' in c for c in self._ps_calls(r)[before:]))
            # WARP on -> DNS change refused
            o.run_action('warp_connect')
            self.assertTrue(o.net_state()['warp']['connected'])
            res = o.run_action('dns_google')
            self.assertFalse(res['ok'])
            self.assertIn('Disconnect WARP', res['error'])
            # connecting again is a harmless no-op; disconnecting twice too
            self.assertIn('already connected', o.run_action('warp_connect')['message'])
            self.assertTrue(o.run_action('warp_disconnect')['ok'])
            self.assertIn('already disconnected', o.run_action('warp_disconnect')['message'])

    def test_wake_report_collects_three_sections(self):
        o, r, _g = make()
        res = o.run_action('wake_report')
        self.assertTrue(res['ok'])
        self.assertEqual(res['text'].count('=='), 6)

    def test_repair_hibernation_sequence(self):
        o, r, _g = make()
        self.assertTrue(o.run_action('repair_hibernation')['ok'])
        pc = [c[1:] for c in r.calls if c[0] == 'powercfg']
        self.assertEqual(pc[0], ['/hibernate', 'off'])
        self.assertEqual(pc[1], ['/hibernate', 'on'])

    def test_catalog_is_consistent(self):
        for a in opt.ACTIONS:
            self.assertTrue(hasattr(Optimizer, '_do_' + a['id']), a['id'])
        ids = [a['id'] for a in opt.ACTIONS] + [t['id'] for t in opt.TOGGLES]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == '__main__':
    unittest.main()
