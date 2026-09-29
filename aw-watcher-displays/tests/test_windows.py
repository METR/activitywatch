"""The Windows backend's filtering and ordering, run on any OS against a fake Win32 wrapper."""
import ctypes

import pytest

from aw_watcher_displays import windows as w
from aw_watcher_displays.geometry import assign_windows


class FakeWin32:
    """Stands in for windows._Win32. Windows are given top of the z-order first, like EnumWindows."""

    def __init__(self, monitors, wins, foreground=None, cursor=None):
        self._monitors = monitors
        self.wins = {h: dict(info, hwnd=h) for h, info in wins}
        self.order = [h for h, _ in wins]
        self.foreground = foreground
        self.cursor = cursor
        self.exe_lookups = 0
        self.dpi_calls = 0

    def set_dpi_aware(self):
        self.dpi_calls += 1

    def monitors(self):
        return self._monitors

    def top_level_windows(self):
        return list(self.order)

    def is_visible(self, h):
        return self.wins[h].get("visible", True)

    def is_minimized(self, h):
        return self.wins[h].get("minimized", False)

    def is_cloaked(self, h):
        return self.wins[h].get("cloaked", False)

    def ex_style(self, h):
        return self.wins[h].get("ex", 0)

    def class_name(self, h):
        return self.wins[h].get("cls", "Chrome_WidgetWin_1")

    def bounds(self, h):
        return self.wins[h].get("bounds")

    def pid(self, h):
        return self.wins[h].get("pid")

    def exe_name(self, pid):
        self.exe_lookups += 1
        return {1: "Code.exe", 2: "WindowsTerminal.exe", 3: "chrome.exe", 4: "explorer.exe", 5: "LockApp.exe"}.get(pid)

    def foreground_window(self):
        return self.foreground

    def cursor_pos(self):
        return self.cursor


MONITORS = [
    (0x20, (2560, 0, 4480, 1080), False),   # right, 1080p
    (0x10, (0, 0, 2560, 1440), True),       # primary, 1440p
    (0x30, (-1920, 0, 0, 1080), False),     # left of primary
]


@pytest.fixture
def fake():
    api = FakeWin32(
        MONITORS,
        [
            (101, {"pid": 1, "bounds": (0, 0, 2560, 1400)}),                       # Code on primary
            (102, {"pid": 2, "bounds": (2560, 0, 4480, 1040)}),                    # terminal on the right
            (103, {"pid": 3, "bounds": (-1900, 0, -100, 1000)}),                   # chrome on the left
            (104, {"pid": 3, "bounds": (10, 10, 900, 700), "visible": False}),
            (105, {"pid": 3, "bounds": (10, 10, 900, 700), "minimized": True}),
            (106, {"pid": 3, "bounds": (10, 10, 900, 700), "cloaked": True}),      # other virtual desktop
            (107, {"pid": 3, "bounds": (10, 10, 900, 700), "ex": w.WS_EX_TOOLWINDOW}),
            (108, {"pid": 4, "bounds": (0, 0, 2560, 1440), "cls": "Progman"}),     # the desktop
            (109, {"pid": 4, "bounds": (0, 1400, 2560, 1440), "cls": "Shell_TrayWnd"}),
            (110, {"pid": 3, "bounds": (0, 0, 0, 0)}),                             # zero size
            (111, {"pid": 3, "bounds": None}),
            (112, {"pid": 3, "bounds": (0, 0, 4480, 1440), "ex": w.WS_EX_TRANSPARENT}),  # click-through overlay
            (113, {"pid": 3, "bounds": (0, 0, 800, 600), "ex": w.WS_EX_NOACTIVATE}),     # toast
        ],
        foreground=102,
        cursor=(-500, 500),
    )
    w._set_api(api)
    yield api
    w._set_api(None)


def test_module_imports_off_windows_and_refuses_to_run_without_a_fake():
    w._set_api(None)
    if not hasattr(ctypes, "WinDLL"):
        with pytest.raises(OSError):
            w.display_list()


def test_init_asks_for_dpi_awareness(fake):
    w.init()
    assert fake.dpi_calls == 1


def test_display_list_orders_left_to_right_and_marks_the_primary(fake):
    ds = w.display_list()
    assert [d["idx"] for d in ds] == [0, 1, 2]
    assert [(d["x"], d["w"], d["main"]) for d in ds] == [(-1920.0, 1920.0, False), (0.0, 2560.0, True),
                                                         (2560.0, 1920.0, False)]
    assert all(d["asleep"] is False and d["builtin"] is False for d in ds)


def test_window_list_keeps_only_real_app_windows_in_z_order(fake):
    ws = w.window_list()
    assert [x["number"] for x in ws] == [101, 102, 103]
    assert [x["owner"] for x in ws] == ["Code.exe", "WindowsTerminal.exe", "chrome.exe"]
    assert ws[0] == {"owner": "Code.exe", "pid": 1, "number": 101, "x": 0.0, "y": 0.0, "w": 2560.0, "h": 1400.0}


def test_exe_name_is_looked_up_once_per_process(fake):
    w.window_list()
    assert fake.exe_lookups == 3


def test_tool_windows_that_ask_for_the_taskbar_are_kept(fake):
    fake.wins[107]["ex"] = w.WS_EX_TOOLWINDOW | w.WS_EX_APPWINDOW
    assert 107 in [x["number"] for x in w.window_list()]


def test_window_whose_process_cannot_be_opened_is_unknown(fake):
    fake.wins[101]["pid"] = 99
    assert w.window_list()[0]["owner"] == "unknown"


def test_frontmost_and_cursor(fake):
    assert w.frontmost_pid([]) == 2
    fake.foreground = None
    assert w.frontmost_pid([]) is None
    assert w.cursor_position() == (-500.0, 500.0)
    assert w.session_locked() is None


def test_desktop_or_taskbar_in_front_defers_to_the_cursor(fake):
    fake.foreground = 108  # the desktop, owned by explorer.exe like any File Explorer window
    assert w.frontmost_pid([]) is None
    fake.foreground = 109  # the taskbar
    assert w.frontmost_pid([]) is None


def test_lock_screen_in_front_means_locked(fake):
    fake.wins[120] = {"pid": 5, "hwnd": 120}
    fake.foreground = 120
    assert w.session_locked() is True


def test_end_to_end_assignment_across_three_monitors(fake):
    ds, ws = w.display_list(), w.window_list()
    tops = [s["topmost"]["owner"] if s["topmost"] else None for s in assign_windows(ws, ds, min_cover=0.1)]
    assert tops == ["chrome.exe", "Code.exe", "WindowsTerminal.exe"]


def test_struct_layouts_match_win32():
    assert ctypes.sizeof(w.RECT) == 16
    assert ctypes.sizeof(w.POINT) == 8
    assert ctypes.sizeof(w.MONITORINFO) == 40
    assert ctypes.sizeof(w.HWND) == ctypes.sizeof(ctypes.c_void_p)
