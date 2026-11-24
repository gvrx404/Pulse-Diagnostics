"""Offline Chromium build invariants; no system Qt install required to run."""
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class BundleContractTests(unittest.TestCase):
    def test_prebuilt_ui_contains_bridge_before_react(self):
        html=(ROOT/'ui/index.html').read_text(encoding='utf-8')
        qt='qrc:///qtwebchannel/qwebchannel.js'
        assert qt in html
        assert 'window.pywebview = { api: api };' in html
        self.assertLess(html.index(qt),html.index('window.pywebview = { api: api };'))
        self.assertLess(html.index('window.pywebview = { api: api };'),html.rfind('<script>'))

    def test_build_pipeline_keeps_qt_bridge(self):
        src=(ROOT/'frontend/build.py').read_text(encoding='utf-8')
        self.assertIn('qt_bridge.js',src)
        self.assertIn('qrc:///qtwebchannel/qwebchannel.js',src)

    def test_only_qt_renderer_is_built(self):
        requirements=(ROOT/'requirements.txt').read_text()
        builder=(ROOT/'build_windows.py').read_text()
        launcher=(ROOT/'main.py').read_text()
        self.assertIn('PySide6==6.11.3',requirements)
        self.assertNotIn('pywebview',requirements.lower())
        self.assertIn("'PySide6.QtWebEngineWidgets'", builder)
        self.assertIn('from chromium_shell import run',launcher)
        self.assertNotIn('run_browser_ui(',launcher)
        self.assertIn('--skip-ui --no-admin',(ROOT/'build_release.bat').read_text())

if __name__=='__main__': unittest.main()
