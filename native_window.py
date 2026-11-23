"""Native Windows 10/11 rounded outer window clipping for the frameless Pulse UI.

CSS border-radius only rounds WebView content.  The HWND still remains a
rectangle on Windows 10 unless a real region is installed.  No pywin32 or
third-party binary is required here: this uses the Windows user32/gdi32 APIs.
"""
from __future__ import annotations

import os
import sys
import time

CORNER_RADIUS_DIP = 24
APP_USER_MODEL_ID = 'PulseDiagnostics.App'


def set_app_identity():
    """Give the process its own taskbar identity so it shows Pulse's icon, not python.exe's.

    Call once at startup, before the window is created. No-op outside Windows.
    """
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def _is_windows11():
    """Use the real OS build, not compatibility-mode version strings."""
    if sys.platform != 'win32':
        return False
    try:
        return sys.getwindowsversion().build >= 22000
    except (AttributeError, OSError):
        return False


def _try_win11_dwm_corners(hwnd):
    """Request DWM corners where supported, without imposing a GDI clip region."""
    try:
        import ctypes
        value = ctypes.c_int(2)  # DWMWCP_ROUND
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(value), ctypes.sizeof(value))
        return result == 0
    except (OSError, AttributeError, ValueError):
        return False


def _apply_window_icon(user32, wintypes_mod, hwnd, icon_path):
    """Set the title-bar/taskbar/Alt-Tab icon of *hwnd* from an .ico file."""
    import ctypes
    wintypes = wintypes_mod
    if not icon_path or not os.path.isfile(icon_path):
        return
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int,
                                  ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
    for kind, metric in ((1, 11), (0, 49)):  # ICON_BIG/SM_CXICON, ICON_SMALL/SM_CXSMICON
        size = user32.GetSystemMetrics(metric)
        handle = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if handle:
            user32.SendMessageW(hwnd, WM_SETICON, kind, handle)


def keep_window_rounded(window, radius=CORNER_RADIUS_DIP, icon_path=None):
    """Apply a rounded HWND region, keeping it updated as the window resizes.

    Call through webview.start(func=keep_window_rounded, args=(WINDOW,)).
    webview executes that function once the native window is available.
    This is a no-op outside Windows.
    """
    if sys.platform != 'win32':
        return

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
    user32.SetWindowRgn.restype = ctypes.c_int
    gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
    gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    get_dpi = getattr(user32, 'GetDpiForWindow', None)
    if get_dpi:
        get_dpi.argtypes = [wintypes.HWND]
        get_dpi.restype = wintypes.UINT

    own_pid = os.getpid()

    def own_window():
        # Find only this process' main Pulse window, not another running app.
        found = []
        cb_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @cb_type
        def each(hwnd, unused):
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != own_pid:
                return True
            count = user32.GetWindowTextLengthW(hwnd)
            if count <= 0:
                return True
            buf = ctypes.create_unicode_buffer(count + 1)
            user32.GetWindowTextW(hwnd, buf, count + 1)
            if buf.value == 'Pulse Diagnostics':
                found.append(hwnd)
                return False
            return True

        user32.EnumWindows(each, 0)
        return found[0] if found else None

    hwnd = None
    # The real HWND can be created shortly after webview.start invokes func.
    for _ in range(100):
        hwnd = own_window()
        if hwnd:
            break
        time.sleep(0.1)
    if not hwnd:
        return

    try:
        _apply_window_icon(user32, wintypes, hwnd, icon_path)
    except Exception:
        pass

    # Windows 11 already has a DWM rounding API. Continuously changing a
    # SetWindowRgn clip on a frameless WebView can cause black/blank surfaces
    # when the renderer updates. Preserve the existing Win10 behavior below.
    if _is_windows11():
        _try_win11_dwm_corners(hwnd)  # best-effort, may fail for borderless HWNDs
        return

    last_shape = None
    while user32.IsWindow(hwnd):
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            break
        width, height = rect.right - rect.left, rect.bottom - rect.top
        dpi = int(get_dpi(hwnd)) if get_dpi else 96
        shape = (width, height, dpi)
        if width > 0 and height > 0 and shape != last_shape:
            diameter = max(4, round(radius * 2 * max(96, dpi) / 96))
            region = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, diameter, diameter)
            if region:
                # Windows takes ownership of a region only when this succeeds.
                if user32.SetWindowRgn(hwnd, region, True):
                    last_shape = shape
                else:
                    gdi32.DeleteObject(region)
        time.sleep(0.35)
