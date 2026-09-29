"""Windows backend: user32/dwmapi reads through ctypes (no pywin32), like aw-watcher-afk's windows.py.

App names are the executable's file name ("chrome.exe"), the same convention aw-watcher-window uses on Windows,
so the two buckets can be joined. No window titles are read.

All Win32 calls go through one small wrapper (_Win32), created on first use, so this module imports on any OS and
tests can swap in a fake with _set_api().
"""
import ctypes
import ntpath
import sys
from typing import Any, Dict, List, Optional, Tuple

BOOL = ctypes.c_int
DWORD = ctypes.c_uint32
LONG = ctypes.c_int32
HANDLE = ctypes.c_void_p
HWND = ctypes.c_void_p
HMONITOR = ctypes.c_void_p
HDC = ctypes.c_void_p
LPARAM = ctypes.c_ssize_t

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020  # click-through overlays (screen dimmers, game overlays)
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000  # toasts and on-screen displays that never take focus
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14  # hidden: on another virtual desktop, or a suspended store app
MONITORINFOF_PRIMARY = 0x1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
PROCESS_PER_MONITOR_DPI_AWARE = 2

# Desktop and taskbar windows: always on screen, never "the app on top"
SHELL_CLASSES = frozenset({"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"})


class RECT(ctypes.Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", DWORD)]


Bounds = Tuple[int, int, int, int]  # left, top, right, bottom

if sys.platform == "win32":

    class _Win32:
        def __init__(self) -> None:
            self.user32 = ctypes.WinDLL("user32", use_last_error=True)
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self.dwmapi = self._optional("dwmapi")
            self.shcore = self._optional("shcore")
            self.WNDENUMPROC = ctypes.WINFUNCTYPE(BOOL, HWND, LPARAM)
            self.MONITORENUMPROC = ctypes.WINFUNCTYPE(BOOL, HMONITOR, HDC, ctypes.POINTER(RECT), LPARAM)

            u, k = self.user32, self.kernel32
            u.EnumDisplayMonitors.argtypes = [HDC, ctypes.POINTER(RECT), self.MONITORENUMPROC, LPARAM]
            u.EnumDisplayMonitors.restype = BOOL
            u.GetMonitorInfoW.argtypes = [HMONITOR, ctypes.POINTER(MONITORINFO)]
            u.GetMonitorInfoW.restype = BOOL
            u.EnumWindows.argtypes = [self.WNDENUMPROC, LPARAM]
            u.EnumWindows.restype = BOOL
            u.IsWindowVisible.argtypes = [HWND]
            u.IsWindowVisible.restype = BOOL
            u.IsIconic.argtypes = [HWND]
            u.IsIconic.restype = BOOL
            u.GetWindowLongW.argtypes = [HWND, ctypes.c_int]
            u.GetWindowLongW.restype = LONG
            u.GetClassNameW.argtypes = [HWND, ctypes.c_wchar_p, ctypes.c_int]
            u.GetClassNameW.restype = ctypes.c_int
            u.GetWindowRect.argtypes = [HWND, ctypes.POINTER(RECT)]
            u.GetWindowRect.restype = BOOL
            u.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]
            u.GetWindowThreadProcessId.restype = DWORD
            u.GetForegroundWindow.argtypes = []
            u.GetForegroundWindow.restype = HWND
            u.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
            u.GetCursorPos.restype = BOOL
            k.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
            k.OpenProcess.restype = HANDLE
            k.QueryFullProcessImageNameW.argtypes = [HANDLE, DWORD, ctypes.c_wchar_p, ctypes.POINTER(DWORD)]
            k.QueryFullProcessImageNameW.restype = BOOL
            k.CloseHandle.argtypes = [HANDLE]
            k.CloseHandle.restype = BOOL
            if self.dwmapi is not None:
                self.dwmapi.DwmGetWindowAttribute.argtypes = [HWND, DWORD, ctypes.c_void_p, DWORD]
                self.dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long  # HRESULT

        @staticmethod
        def _optional(name: str) -> Any:
            try:
                return ctypes.WinDLL(name)
            except OSError:
                return None

        def set_dpi_aware(self) -> None:
            """Per-monitor DPI awareness, so monitor, window and cursor coordinates are all physical pixels."""
            try:
                f = self.user32.SetProcessDpiAwarenessContext  # Windows 10 1703+
                f.argtypes = [HANDLE]
                f.restype = BOOL
                if f(HANDLE(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
                    return
            except AttributeError:
                pass
            if self.shcore is not None:  # Windows 8.1+
                try:
                    if self.shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE) == 0:
                        return
                except AttributeError:
                    pass
            self.user32.SetProcessDPIAware()

        def monitors(self) -> List[Tuple[int, Bounds, bool]]:
            handles: List[int] = []

            def collect(hmon, _hdc, _rect, _lparam):
                if hmon:
                    handles.append(hmon)
                return True

            self.user32.EnumDisplayMonitors(None, None, self.MONITORENUMPROC(collect), 0)
            out = []
            for h in handles:
                mi = MONITORINFO()
                mi.cbSize = ctypes.sizeof(MONITORINFO)
                if self.user32.GetMonitorInfoW(h, ctypes.byref(mi)):
                    r = mi.rcMonitor
                    out.append((int(h), (r.left, r.top, r.right, r.bottom), bool(mi.dwFlags & MONITORINFOF_PRIMARY)))
            return out

        def top_level_windows(self) -> List[int]:
            hwnds: List[int] = []

            def collect(hwnd, _lparam):
                if hwnd:
                    hwnds.append(hwnd)
                return True

            self.user32.EnumWindows(self.WNDENUMPROC(collect), 0)  # top-level windows, top of the z-order first
            return hwnds

        def is_visible(self, hwnd: int) -> bool:
            return bool(self.user32.IsWindowVisible(hwnd))

        def is_minimized(self, hwnd: int) -> bool:
            return bool(self.user32.IsIconic(hwnd))

        def is_cloaked(self, hwnd: int) -> bool:
            if self.dwmapi is None:
                return False
            cloaked = DWORD()
            hr = self.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            return hr == 0 and cloaked.value != 0

        def ex_style(self, hwnd: int) -> int:
            return self.user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF

        def class_name(self, hwnd: int) -> str:
            buf = ctypes.create_unicode_buffer(256)
            self.user32.GetClassNameW(hwnd, buf, len(buf))
            return buf.value

        def bounds(self, hwnd: int) -> Optional[Bounds]:
            r = RECT()
            # The DWM frame excludes the invisible resize borders that GetWindowRect includes on Windows 10+
            if self.dwmapi is not None and self.dwmapi.DwmGetWindowAttribute(
                hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)
            ) == 0:
                return r.left, r.top, r.right, r.bottom
            if self.user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return r.left, r.top, r.right, r.bottom
            return None

        def pid(self, hwnd: int) -> Optional[int]:
            pid = DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return pid.value or None

        def exe_name(self, pid: int) -> Optional[str]:
            h = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return None  # e.g. a process of another user or of SYSTEM
            try:
                buf = ctypes.create_unicode_buffer(32768)  # long paths are enabled in PyInstaller builds
                size = DWORD(len(buf))
                if self.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    return ntpath.basename(buf.value) or None
                return None
            finally:
                self.kernel32.CloseHandle(h)

        def foreground_window(self) -> Optional[int]:
            return self.user32.GetForegroundWindow() or None

        def cursor_pos(self) -> Optional[Tuple[int, int]]:
            p = POINT()
            if self.user32.GetCursorPos(ctypes.byref(p)):
                return p.x, p.y
            return None


_api: Any = None


def _get_api() -> Any:
    global _api
    if _api is None:
        if sys.platform == "win32":
            _api = _Win32()
        else:
            raise OSError("the Windows backend only runs on Windows")
    return _api


def _set_api(api: Any) -> None:
    """Tests: replace the Win32 wrapper with a fake."""
    global _api
    _api = api


def init() -> None:
    _get_api().set_dpi_aware()


def display_list() -> List[Dict[str, Any]]:
    """Monitors ordered left to right, then top to bottom (Windows has no reliable "built-in" flag)."""
    out = []
    for hmon, (left, top, right, bottom), primary in _get_api().monitors():
        out.append({"idx": 0, "id": hmon, "x": float(left), "y": float(top), "w": float(right - left),
                    "h": float(bottom - top), "asleep": False, "builtin": False, "main": primary})
    out.sort(key=lambda d: (not d["builtin"], d["x"], d["y"]))
    for i, d in enumerate(out):
        d["idx"] = i
    return out


def window_list() -> List[Dict[str, Any]]:
    """Visible top-level application windows, front-to-back."""
    api = _get_api()
    names: Dict[int, Optional[str]] = {}
    out = []
    for hwnd in api.top_level_windows():
        if not api.is_visible(hwnd) or api.is_minimized(hwnd) or api.is_cloaked(hwnd):
            continue
        ex = api.ex_style(hwnd)
        if ex & WS_EX_TOOLWINDOW and not ex & WS_EX_APPWINDOW:
            continue
        if ex & (WS_EX_TRANSPARENT | WS_EX_NOACTIVATE):
            continue
        if api.class_name(hwnd) in SHELL_CLASSES:
            continue
        b = api.bounds(hwnd)
        if b is None or b[2] <= b[0] or b[3] <= b[1]:
            continue
        pid = api.pid(hwnd)
        if pid is not None and pid not in names:
            names[pid] = api.exe_name(pid)
        # "unknown" like aw-watcher-window, so the display is not mistaken for an empty one
        owner = (names.get(pid) if pid is not None else None) or "unknown"
        out.append({"owner": owner, "pid": pid, "number": int(hwnd),
                    "x": float(b[0]), "y": float(b[1]), "w": float(b[2] - b[0]), "h": float(b[3] - b[1])})
    return out


def frontmost_pid(windows: List[Dict[str, Any]]) -> Optional[int]:
    api = _get_api()
    hwnd = api.foreground_window()
    # The desktop and taskbar belong to explorer.exe, like File Explorer windows: let the cursor decide instead
    if not hwnd or api.class_name(hwnd) in SHELL_CLASSES:
        return None
    return api.pid(hwnd)


def cursor_position() -> Optional[Tuple[float, float]]:
    p = _get_api().cursor_pos()
    return (float(p[0]), float(p[1])) if p else None


def session_locked() -> Optional[bool]:
    """True while the lock screen (LockApp.exe) is in front, else None (unknown): the windows behind it are still
    listed, so they must not be reported. aw-watcher-afk still marks the time as away after its timeout."""
    api = _get_api()
    hwnd = api.foreground_window()
    pid = api.pid(hwnd) if hwnd else None
    if pid is not None and (api.exe_name(pid) or "").lower() == "lockapp.exe":
        return True
    return None
