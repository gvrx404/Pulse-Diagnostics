import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import main
import native_window


class StartupTests(unittest.TestCase):
    def test_log_is_writable_under_localappdata(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.dict(os.environ, {'LOCALAPPDATA': temp}):
                main.log('Hello from unit test')
                path = Path(temp) / 'PulseDiagnostics' / 'startup.log'
                self.assertTrue(path.is_file())
                self.assertIn('Hello from unit test', path.read_text(encoding='utf-8'))

    def test_diagnostic_report_has_os_information(self):
        report = main.system_report()
        self.assertIn('OS:', report)
        self.assertIn('bundled PySide6 Qt WebEngine', report)

    def test_windows10_returns_false(self):
        with mock.patch.object(native_window.sys, 'platform', 'win32'):
            with mock.patch.object(native_window.sys, 'getwindowsversion',
                                   return_value=type('V', (), {'build': 19045})(), create=True):
                self.assertFalse(native_window._is_windows11())

    def test_windows11_build_detected(self):
        with mock.patch.object(native_window.sys, 'platform', 'win32'):
            with mock.patch.object(native_window.sys, 'getwindowsversion',
                                   return_value=type('V', (), {'build': 22631})(), create=True):
                self.assertTrue(native_window._is_windows11())

    def test_build_packages_launcher_and_console_debug_option(self):
        builder = (Path(__file__).parents[1] / 'build_windows.py').read_text(encoding='utf-8')
        self.assertIn("args.append(ROOT / 'main.py')", builder)
        self.assertIn("p.add_argument('--onedir'", builder)
        self.assertIn("p.add_argument('--console'", builder)

    def test_gui_uses_bundled_chromium_shell(self):
        gui = (Path(__file__).parents[1] / 'desktop_backend.py').read_text(encoding='utf-8')
        self.assertIn('from chromium_shell import run', gui)
        builder = (Path(__file__).parents[1] / 'build_windows.py').read_text(encoding='utf-8')
        self.assertIn("'PySide6.QtWebEngineWidgets'", builder)
        self.assertNotIn("'--collect-all', 'webview'", builder)


if __name__ == '__main__':
    unittest.main()
