#!/usr/bin/env python3
"""Pulse Diagnostics — offline bundled Chromium launcher.

Uses PySide6 QtWebEngine; never probes WebView2, never chooses MSHTML, and
never starts an elevated external browser. Logs startup errors for frozen EXEs.
"""
from __future__ import annotations

import datetime
import logging
import multiprocessing
import os
from pathlib import Path
import platform
import sys
import tempfile
import traceback

APP_NAME = 'PulseDiagnostics'


def log_file():
    candidates = [os.getenv('LOCALAPPDATA'), os.getenv('APPDATA'), tempfile.gettempdir()]
    for root in candidates:
        if not root:
            continue
        try:
            folder = Path(root) / APP_NAME
            folder.mkdir(parents=True, exist_ok=True)
            return folder / 'startup.log'
        except OSError:
            continue
    return None


def log(message):
    target = log_file()
    if target:
        try:
            with target.open('a', encoding='utf-8') as fp:
                fp.write(f'[{datetime.datetime.now().isoformat(timespec="seconds")}] {message}\n')
        except OSError:
            pass


def system_report():
    return '\n'.join([
        f'OS: {platform.platform()}',
        f'Executable: {sys.executable}',
        f'Frozen: {bool(getattr(sys, "frozen", False))}',
        f'Python: {sys.version.split()[0]}',
        'Renderer: bundled PySide6 Qt WebEngine (Chromium)',
        'Microsoft Edge WebView2 Runtime: not required',
    ])


def show_problem(detail):
    if sys.platform != 'win32':
        print(detail, file=sys.stderr)
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, detail, 'Pulse Diagnostics - Startup error', 0x10)
    except Exception:
        print(detail, file=sys.stderr)


def main():
    multiprocessing.freeze_support()
    target = log_file()
    if target:
        logging.basicConfig(filename=str(target), level=logging.INFO,
                            format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
                            force=True)
    log('--- Chromium Launch ---\n' + system_report())
    if '--diagnose' in sys.argv:
        report = system_report()
        show_problem(report + '\n\nStartup log: ' + str(log_file()))
        return
    try:
        log('Importing bundled QtWebEngine/Chromium shell')
        from chromium_shell import run
        log('Launching bundled Chromium window (no WebView2, no browser fallback)')
        exit_code = run(debug='--debug' in sys.argv)
        log('Chromium window exited, return code: ' + str(exit_code))
        if exit_code:
            raise RuntimeError(f'Chromium exited with status {exit_code}.')
    except BaseException as exc:
        log('STARTUP FAILURE:\n' + traceback.format_exc())
        show_problem(
            f'{type(exc).__name__}: {exc}\n\nFull diagnostic log:\n{log_file()}\n\n'
            'This build contains Chromium itself. If it cannot initialize, '
            'please check antivirus restrictions and the QtWebEngineProcess helper '
            'instead of installing Microsoft Edge WebView2.')
        if not getattr(sys, 'frozen', False):
            raise
        sys.exit(1)


if __name__ == '__main__':
    main()
