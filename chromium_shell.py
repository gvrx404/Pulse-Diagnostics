#!/usr/bin/env python3
"""Self-contained Chromium desktop shell for Pulse Diagnostics (Qt WebEngine).

No Microsoft Edge WebView2 runtime, browser installation, listening HTTP server,
or network access required. WebEngine ships with PySide6 and PyInstaller bundles
its subprocess and resource files alongside the application inside one-file
extraction, or in the _internal directory for a one-dir build.

The existing desktop_backend.Api remains the single source of truth for hardware
sensor readings, diagnostics, optimizer, reports and permission checks.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# Intentionally do NOT import PySide6 before the application entry point has
# called multiprocessing.freeze_support() on Windows.

# Only the UI's published API methods are exposed. Javascript cannot use
# the bridge to call arbitrary Python attributes or import code.
RPC_METHODS = frozenset({
    'info', 'sensors', 'live', 'temp_mode', 'set_temp_mode',
    'install_hardware_access', 'export_sensor_debug',
    'start', 'poll', 'cancel', 'save_report', 'open_folder',
    'opt_status', 'opt_set_toggle', 'opt_apply_recommended', 'opt_set_plan',
    'opt_startup', 'opt_set_startup', 'opt_remove_startup',
    'opt_removed_startup', 'opt_restore_startup', 'opt_forget_startup',
    'opt_cleanup_sizes', 'opt_net_state', 'opt_run', 'opt_poll',
    'opt_open_log_folder',
})


def encode_response(value=None, error=None):
    """Uniform error/result transport: every API call resolves or rejects."""
    payload = {'ok': error is None}
    if error is None:
        payload['value'] = value
    else:
        payload['error'] = str(error)
    return json.dumps(payload, ensure_ascii=False, default=str, allow_nan=False)


def decode_arguments(payload):
    args = json.loads(payload)
    if not isinstance(args, list) or len(args) > 8:
        raise ValueError('API arguments must be an array with at most 8 values')
    return args


def run(*, debug=False):
    """Launch real Chromium rendering in a frameless Qt window."""
    from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, QUrl, Signal, Slot
    from PySide6.QtGui import QColor, QIcon, QPainterPath, QRegion
    from PySide6.QtWidgets import QApplication, QFileDialog, QWidget, QVBoxLayout
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebChannel import QWebChannel

    from desktop_backend import Api, UI_FILE
    from native_window import set_app_identity
    from hardware_sensors import close as close_sensors

    logger = logging.getLogger('pulse.qt')
    if not Path(UI_FILE).is_file():
        raise RuntimeError('React UI missing: run python frontend/build.py before building.')

    # QtWebEngine uses its OWN embedded Chromium. No MSHTML, pywebview, external
    # browser, or WebView2 registry detection is involved.
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('Pulse Diagnostics')
    app.setOrganizationName('Pulse Diagnostics')
    app.setQuitOnLastWindowClosed(True)
    set_app_identity()
    icon = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets' / 'pulse.ico'
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))

    class Task(QRunnable):
        def __init__(self, bridge, request_id, method, args):
            super().__init__()
            self.bridge = bridge
            self.request_id = request_id
            self.method = method
            self.args = args

        def run(self):
            try:
                result = getattr(self.bridge.api, self.method)(*self.args)
                payload = encode_response(result)
            except BaseException as exc:
                logger.exception('API error in %s', self.method)
                payload = encode_response(error=f'{type(exc).__name__}: {exc}')
            # Signals may be emitted from the worker; _deliver is queued onto
            # the Qt GUI thread before the WebChannel forwards its notification.
            self.bridge.workerResult.emit(self.request_id, payload)

    class PulseBridge(QObject):
        resultReady = Signal(str, str)
        workerResult = Signal(str, str)

        def __init__(self, api, window):
            super().__init__()
            self.api = api
            self.window = window
            self.pool = QThreadPool(self)
            self.pool.setMaxThreadCount(4)
            self.workerResult.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

        @Slot(str, str)
        def _deliver(self, request_id, payload):
            self.resultReady.emit(request_id, payload)

        @Slot(str, str, str)
        def call(self, request_id, method, args_json):
            if not request_id or len(request_id) > 100:
                return
            if method == 'window_minimize':
                self.window.showMinimized()
                self.resultReady.emit(request_id, encode_response({'ok': True}))
                return
            if method == 'window_close':
                self.resultReady.emit(request_id, encode_response({'ok': True}))
                self.window.close()
                return
            if method == 'pick_folder':
                folder = QFileDialog.getExistingDirectory(self.window, 'Select test folder')
                self.resultReady.emit(request_id, encode_response(folder or None))
                return
            if method not in RPC_METHODS:
                self.resultReady.emit(request_id, encode_response(error='Unknown API method'))
                return
            try:
                args = decode_arguments(args_json)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self.resultReady.emit(request_id, encode_response(error=exc))
                return
            self.pool.start(Task(self, request_id, method, args))

        @Slot()
        def startDrag(self):
            window_handle = self.window.windowHandle()
            if window_handle is not None:
                try:
                    window_handle.startSystemMove()
                except Exception:
                    logger.exception('Native window dragging was unavailable')

    class LockedPage(QWebEnginePage):
        """Keep the UI offline and stop untrusted websites from calling RPC."""
        def javaScriptConsoleMessage(self, level, message, line_number, source_id):
            logger.info('Chromium JS %s:%s: %s', source_id, line_number, message)

        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
            if not is_main_frame:
                return False
            if url.isLocalFile():
                return Path(url.toLocalFile()).resolve() == Path(UI_FILE).resolve()
            return url.scheme() == 'about' and url.toString() == 'about:blank'

    class PulseWindow(QWidget):
        def __init__(self):
            super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setWindowTitle('Pulse Diagnostics')
            self.resize(1540, 880)
            self.setMinimumSize(1000, 680)
            self.view = QWebEngineView(self)
            self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self.view.setStyleSheet('background: transparent; border: 0;')
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            layout.addWidget(self.view)
            self.view.setPage(LockedPage(self.view))
            self.view.page().setBackgroundColor(QColor('#080808'))
            settings = self.view.settings()
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
            settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)
            self.channel = QWebChannel(self.view.page())
            self.api = Api()
            self.bridge = PulseBridge(self.api, self)
            self.channel.registerObject('pulse', self.bridge)
            self.view.page().setWebChannel(self.channel)
            self.view.loadFinished.connect(self.on_loaded)
            self.view.setUrl(QUrl.fromLocalFile(str(Path(UI_FILE).resolve())))

        def on_loaded(self, ok):
            if ok:
                logger.info('Bundled Qt Chromium loaded offline React UI')
            else:
                logger.error('Qt Chromium failed to load %s', UI_FILE)

        def resizeEvent(self, event):
            # Clip the REAL Qt top-level window, not just CSS corners. Covers
            # Windows 10 and 11 without requiring DWM/WebView2.
            super().resizeEvent(event)
            from PySide6.QtCore import QRectF
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 20, 20)
            self.setMask(QRegion(path.toFillPolygon().toPolygon()))

        def closeEvent(self, event):
            try:
                self.view.page().setWebChannel(None)
                self.bridge.pool.waitForDone(2000)
            finally:
                super().closeEvent(event)

    window = PulseWindow()
    window.show()
    logger.info('Chromium inside PySide6 QtWebEngine, no system WebView2 required')
    try:
        return app.exec()
    finally:
        close_sensors()


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(run(debug='--debug' in sys.argv))
