"""New renderer regression tests: Chromium is bundled, never WebView2/browser."""
from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

import main
import chromium_shell


class RendererRoutingTests(unittest.TestCase):
    def test_defaults_to_embedded_chromium(self):
        fake = types.ModuleType('chromium_shell')
        fake.run = mock.Mock(return_value=0)
        with mock.patch.dict(sys.modules, {'chromium_shell': fake}), \
                mock.patch.object(main.sys, 'argv', ['PulseDiagnostics.exe']), \
                mock.patch.object(main, 'log'), \
                mock.patch.object(main, 'log_file', return_value=None):
            main.main()
        fake.run.assert_called_once_with(debug=False)

    def test_debug_flag_passes_through(self):
        fake = types.ModuleType('chromium_shell')
        fake.run = mock.Mock(return_value=0)
        with mock.patch.dict(sys.modules, {'chromium_shell': fake}), \
                mock.patch.object(main.sys, 'argv', ['PulseDiagnostics.exe','--debug']), \
                mock.patch.object(main, 'log'), \
                mock.patch.object(main, 'log_file', return_value=None):
            main.main()
        fake.run.assert_called_once_with(debug=True)

    def test_error_is_logged_for_either_user_level(self):
        fake = types.ModuleType('chromium_shell')
        fake.run = mock.Mock(side_effect=RuntimeError('QtWebEngineProcess.exe unavailable'))
        with mock.patch.dict(sys.modules, {'chromium_shell': fake}), \
                mock.patch.object(main.sys, 'argv', ['PulseDiagnostics.exe']), \
                mock.patch.object(main, 'log'), \
                mock.patch.object(main, 'log_file', return_value=None), \
                mock.patch.object(main, 'show_problem') as show:
            with self.assertRaisesRegex(RuntimeError,'QtWebEngineProcess'):
                main.main()
            self.assertIn('QtWebEngineProcess', show.call_args.args[0])

    def test_exposes_only_known_methods(self):
        self.assertIn('live', chromium_shell.RPC_METHODS)
        self.assertIn('opt_run', chromium_shell.RPC_METHODS)
        self.assertNotIn('__class__', chromium_shell.RPC_METHODS)
        self.assertNotIn('main', chromium_shell.RPC_METHODS)

    def test_arguments_validation(self):
        self.assertEqual([4, {'k':'v'}], chromium_shell.decode_arguments('[4,{"k":"v"}]'))
        with self.assertRaises(ValueError):
            chromium_shell.decode_arguments('{"a":1}')
        with self.assertRaises(ValueError):
            chromium_shell.decode_arguments('[1,2,3,4,5,6,7,8,9]')

    def test_serialization(self):
        self.assertIn('"value": 55', chromium_shell.encode_response(55))
        self.assertIn('"ok": false', chromium_shell.encode_response(error='offline'))


if __name__ == '__main__':
    unittest.main()
