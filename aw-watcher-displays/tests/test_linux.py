"""The Linux backends' parsing, run on any OS against recorded-style replies."""
import os
import socket
import struct
import tempfile
import threading
from types import SimpleNamespace

import pytest

from aw_watcher_displays import linux, watcher


def test_detect_picks_the_desktop():
    assert linux.detect({"SWAYSOCK": "/run/user/1000/sway-ipc.sock", "WAYLAND_DISPLAY": "wayland-1"}) == "sway"
    assert linux.detect({"HYPRLAND_INSTANCE_SIGNATURE": "abc", "WAYLAND_DISPLAY": "wayland-1"}) == "hyprland"
    assert linux.detect({"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}) == "x11"


def test_other_wayland_desktops_are_refused_even_with_xwayland():
    with pytest.raises(RuntimeError, match="GNOME"):
        linux.detect({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "GNOME"})
    with pytest.raises(RuntimeError):
        linux.detect({"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"})
    with pytest.raises(RuntimeError, match="no graphical session"):
        linux.detect({})


def test_x11_without_a_server_is_a_clean_error(monkeypatch):
    pytest.importorskip("Xlib")
    monkeypatch.setenv("DISPLAY", ":97")  # nothing listens there
    with pytest.raises(RuntimeError, match="can't connect to the X server"):
        linux.X11()


# ----------------------------------------------------------------------------- X11


def x11(id, **kw):
    e = {"id": id, "viewable": True, "types": ["_NET_WM_WINDOW_TYPE_NORMAL"], "states": [], "x": 100, "y": 50,
         "w": 800, "h": 600, "frame": [2, 2, 30, 2], "wm_class": "firefox", "pid": 10}
    e.update(kw)
    return e


def test_x11_windows_keep_normal_windows_and_add_the_frame():
    ws = linux.x11_windows([
        x11(1),
        x11(2, viewable=False),                                  # on another workspace
        x11(3, states=["_NET_WM_STATE_HIDDEN"]),                 # minimised
        x11(4, types=["_NET_WM_WINDOW_TYPE_DOCK"]),              # a panel
        x11(5, types=["_NET_WM_WINDOW_TYPE_NOTIFICATION"]),
        x11(6, types=[], wm_class=None, pid=None, frame=[]),     # no hints at all
        x11(7, types=["_NET_WM_WINDOW_TYPE_DIALOG"], wm_class="Gimp"),
        x11(8, states=["_NET_WM_STATE_SHADED"]),                 # rolled up to its title bar
    ])
    assert [w["number"] for w in ws] == [1, 6, 7]
    assert ws[0] == {"owner": "firefox", "pid": 10, "number": 1, "x": 98.0, "y": 20.0, "w": 804.0, "h": 632.0}
    assert ws[1]["owner"] == "unknown" and ws[1]["pid"] is None and ws[1]["x"] == 100.0


def test_x11_client_side_decoration_shadow_is_taken_off():
    ws = linux.x11_windows([x11(1, x=974, y=517, w=852, h=652, frame=[], gtk_frame=[26, 26, 23, 29])])
    assert (ws[0]["x"], ws[0]["y"], ws[0]["w"], ws[0]["h"]) == (1000.0, 540.0, 800.0, 600.0)


def test_x11_show_desktop_and_windows_kept_mapped_on_other_desktops():
    assert linux.x11_windows([x11(1)], showing_desktop=True) == []
    ws = linux.x11_windows([x11(1, desktop=0), x11(2, desktop=1), x11(3, desktop=linux.STICKY), x11(4)],
                           current_desktop=0)
    assert [w["number"] for w in ws] == [1, 3, 4]
    assert len(linux.x11_windows([x11(2, desktop=1)])) == 1  # only filtered when asked (KWin)


def test_x11_displays_from_randr_crtcs():
    ds = linux.x11_displays([
        {"id": 63, "outputs": ["HDMI-1", "DP-2"], "x": 1920, "y": 0, "w": 2560, "h": 1440, "primary": True},  # mirrored
        {"id": 62, "outputs": ["eDP-1"], "x": 0, "y": 360, "w": 1920, "h": 1080, "primary": False},
    ])
    assert [(d["idx"], d["id"], d["x"], d["y"], d["builtin"], d["main"]) for d in ds] == [
        (0, 62, 0.0, 360.0, True, False), (1, 63, 1920.0, 0.0, False, True)]


# ----------------------------------------------------------------------------- Sway

SWAY_OUTPUTS = [
    {"name": "eDP-1", "active": True, "power": True, "focused": False, "current_workspace": "1",
     "rect": {"x": 0, "y": 0, "width": 1440, "height": 900}},
    {"name": "DP-1", "active": True, "power": True, "focused": True, "current_workspace": "2",
     "rect": {"x": 1440, "y": 0, "width": 2560, "height": 1440}},
    {"name": "HDMI-A-1", "active": False, "current_workspace": None, "rect": {"x": 0, "y": 0, "width": 0, "height": 0}},
]


def view(id, app_id=None, cls=None, rect=(0, 0, 100, 100), **kw):
    x, y, w, h = rect
    v = {"id": id, "type": "con", "pid": 1000 + id, "app_id": app_id, "rect": {"x": x, "y": y, "width": w, "height": h},
         "visible": True, "focused": False, "fullscreen_mode": 0, "nodes": [], "floating_nodes": []}
    if cls:
        v["window_properties"] = {"class": cls}
    v.update(kw)
    return v


SWAY_TREE = {"type": "root", "nodes": [
    {"type": "output", "name": "__i3", "nodes": [
        {"type": "workspace", "name": "__i3_scratch", "nodes": [], "floating_nodes": [view(90, "scratch")]}]},
    {"type": "output", "name": "eDP-1", "nodes": [
        {"type": "workspace", "name": "1", "nodes": [
            {"type": "con", "layout": "tabbed", "nodes": [
                view(11, "firefox", rect=(0, 0, 1440, 900)),
                view(12, "thunderbird", rect=(0, 0, 1440, 900), visible=False)]}],
         "floating_nodes": []},
        {"type": "workspace", "name": "3", "nodes": [view(30, "hidden-ws")], "floating_nodes": []}]},
    {"type": "output", "name": "DP-1", "nodes": [
        {"type": "workspace", "name": "2",
         "nodes": [view(21, None, cls="code", rect=(1440, 0, 2560, 1440), focused=True)],
         "floating_nodes": [
             {**view(22, "pavucontrol", rect=(2000, 200, 600, 400)), "type": "floating_con"},
             {**view(23, "mpv", rect=(2800, 300, 640, 360)), "type": "floating_con"}]}]},
]}


def test_sway_visible_windows_per_output_floating_on_top():
    snap = linux.sway_parse(SWAY_OUTPUTS, SWAY_TREE)
    assert [(d["idx"], d["x"], d["w"], d["builtin"]) for d in snap["displays"]] == [(0, 0.0, 1440.0, True),
                                                                                   (1, 1440.0, 2560.0, False)]
    # background tab, other workspace, scratchpad and inactive output are gone; last floating = topmost
    assert [w["owner"] for w in snap["windows"]] == ["mpv", "pavucontrol", "firefox", "code"]
    assert (snap["focused_pid"], snap["focused_window"]) == (1021, 21)
    assert snap["pointer"] == (1440 + 1280, 720)


def test_sway_fullscreen_first_and_dpms_off_is_asleep():
    outputs = [dict(SWAY_OUTPUTS[1], power=False, focused=False)]
    tree = {"nodes": [{"type": "output", "name": "DP-1", "nodes": [
        {"type": "workspace", "name": "2", "nodes": [view(21, "code"), view(24, "vlc", fullscreen_mode=1)],
         "floating_nodes": [{**view(22, "calc"), "type": "floating_con"}]}]}]}
    snap = linux.sway_parse(outputs, tree)
    assert snap["displays"][0]["asleep"] is True
    assert [w["owner"] for w in snap["windows"]] == ["vlc", "calc", "code"]
    assert snap["focused_pid"] is None and snap["pointer"] is None


def test_focused_display_follows_the_focused_window_not_the_app():
    # One terminal server with a window on each output (same pid); the one on the second output has focus
    tree = {"nodes": [
        {"type": "output", "name": "eDP-1", "nodes": [
            {"type": "workspace", "name": "1", "nodes": [view(11, "foot", rect=(0, 0, 1440, 900), pid=7)]}]},
        {"type": "output", "name": "DP-1", "nodes": [
            {"type": "workspace", "name": "2",
             "nodes": [view(21, "foot", rect=(1440, 0, 2560, 1440), pid=7, focused=True)]}]}]}
    _run_sample(linux.sway_parse(SWAY_OUTPUTS, tree), expect_focused=1)


# ----------------------------------------------------------------------------- Hyprland

HYPR_MONITORS = [
    {"id": 0, "name": "eDP-1", "width": 2880, "height": 1800, "scale": 2.0, "transform": 0, "x": 0, "y": 0,
     "activeWorkspace": {"id": 1}, "specialWorkspace": {"id": 0}, "focused": True, "dpmsStatus": True},
    {"id": 1, "name": "DP-2", "width": 1920, "height": 1080, "scale": 1.0, "transform": 1, "x": 1440, "y": 0,
     "activeWorkspace": {"id": 2}, "specialWorkspace": {"id": -98}, "focused": False, "dpmsStatus": True},
    {"id": 2, "name": "HDMI-A-1", "width": 1920, "height": 1080, "scale": 1.0, "x": 0, "y": 0, "disabled": True},
]


def client(addr, cls, ws, at, size, **kw):
    c = {"address": addr, "mapped": True, "hidden": False, "at": list(at), "size": list(size),
         "workspace": {"id": ws}, "floating": False, "class": cls, "pid": int(addr, 16), "pinned": False,
         "fullscreen": 0, "focusHistoryID": 5}
    c.update(kw)
    return c


# In z-order, back to front, as hyprctl lists them
HYPR_CLIENTS = [
    client("0x10", "firefox", 1, (0, 0), (1440, 900), focusHistoryID=0),
    client("0x11", "kitty", 1, (100, 100), (600, 400), floating=True, focusHistoryID=2),
    client("0x20", "code", 2, (1440, 0), (1080, 1920), focusHistoryID=1),
    client("0x21", "spotify", -98, (1500, 100), (900, 900), focusHistoryID=4),    # shown special workspace
    client("0x30", "slack", 3, (0, 0), (1440, 900)),                               # workspace not shown
    client("0x31", "obs", 3, (1200, 700), (200, 150), pinned=True, floating=True),  # pinned: shown everywhere
    client("0x40", "discord", 1, (0, 0), (1440, 900), hidden=True),                # inactive group tab
    client("0x41", "zoom", 1, (0, 0), (1440, 900), mapped=False),
]


def owners(snap):
    return [w["owner"] for w in snap["windows"]]


def test_hyprland_logical_monitor_size_and_stacking():
    snap = linux.hyprland_parse(HYPR_MONITORS, HYPR_CLIENTS, {"pid": 0x10, "address": "0x10"}, {"x": 700, "y": 400})
    assert [(d["x"], d["w"], d["h"], d["builtin"]) for d in snap["displays"]] == [(0.0, 1440.0, 900.0, True),
                                                                                 (1440.0, 1080.0, 1920.0, False)]
    assert owners(snap) == ["spotify", "obs", "kitty", "code", "firefox"]
    assert snap["windows"][0]["number"] == 0x21
    assert (snap["focused_pid"], snap["focused_window"], snap["pointer"]) == (0x10, 0x10, (700.0, 400.0))


def test_hyprland_nothing_focused():
    snap = linux.hyprland_parse(HYPR_MONITORS, [], {}, {})
    assert snap["focused_pid"] is None and snap["focused_window"] is None and snap["pointer"] is None


def test_hyprland_floating_order_is_the_z_order_not_focus_history():
    clients = [client("0x1", "B", 1, (0, 0), (800, 600), floating=True, focusHistoryID=0),
               client("0x2", "A", 1, (100, 100), (800, 600), floating=True, focusHistoryID=1)]
    assert owners(linux.hyprland_parse(HYPR_MONITORS, clients, {}, {})) == ["A", "B"]


def test_hyprland_floats_over_a_fullscreen_window():
    full = client("0x1", "mpv", 1, (0, 0), (1440, 900), fullscreen=2, focusHistoryID=1)

    def top(float_kw):
        c = client("0x2", "pavucontrol", 1, (100, 100), (600, 400), floating=True, **float_kw)
        return owners(linux.hyprland_parse(HYPR_MONITORS, [full, c], {}, {}))

    assert top({"allowedOverFullscreen": True}) == ["pavucontrol", "mpv"]
    assert top({"allowedOverFullscreen": False}) == ["mpv"]          # faded out behind it
    assert top({"focusHistoryID": 0}) == ["pavucontrol", "mpv"]      # older Hyprland: more recent than the fullscreen
    assert top({"focusHistoryID": 3}) == ["mpv"]
    assert top({"pinned": True, "focusHistoryID": 3}) == ["pavucontrol", "mpv"]


def test_hyprland_fractional_scale_printed_with_two_decimals():
    assert linux._hypr_logical(2256, 1504, 1.57) == (1440, 960)
    assert linux._hypr_logical(3840, 2160, 1.67) == (2304, 1296)
    assert linux._hypr_logical(2560, 1440, 1.33) == (1920, 1080)
    assert linux._hypr_logical(2880, 1800, 2.0) == (1440, 900)


# ----------------------------------------------------------------------------- sockets


def _serve_once(reply_for):
    """A throwaway Unix socket server answering one connection per request with reply_for(request)."""
    path = os.path.join(tempfile.mkdtemp(), "s.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(4)

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                conn.sendall(reply_for(conn.recv(4096)))

    threading.Thread(target=loop, daemon=True).start()
    return path, srv


def test_sway_ipc_survives_titles_that_are_not_utf8():
    body = b'{"nodes": [], "name": "caf\xe9"}'
    path, srv = _serve_once(lambda req: b"i3-ipc" + struct.pack("=II", len(body), 4) + body)
    try:
        assert linux.Sway(path)._ipc(linux.SWAY_GET_TREE)["name"] == "caf�"
    finally:
        srv.close()


def test_hyprland_socket_survives_titles_that_are_not_utf8():
    path, srv = _serve_once(lambda req: b'[{"title": "caf\xe9"}]')
    try:
        hypr = linux.Hyprland("sig", None)
        hypr.path = path
        assert hypr._request("clients")[0]["title"] == "caf�"
    finally:
        srv.close()


def test_a_vanished_compositor_socket_is_fatal():
    with pytest.raises(linux.DesktopGone):
        linux.Sway("/nonexistent/sway-ipc.sock")._ipc(linux.SWAY_GET_TREE)


# ----------------------------------------------------------------------------- contract and watcher


class FakeDesktop:
    def __init__(self, snap, error=None):
        self.snap, self.calls, self.error = snap, 0, error

    def snapshot(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.snap


def _run_sample(snap, expect_focused):
    linux._set_desktop(FakeDesktop(snap))
    try:
        assert watcher.sample(linux, min_cover=0.1)["focused"] == expect_focused
    finally:
        linux._set_desktop(None)


def test_one_desktop_read_per_sample_and_the_watcher_output():
    desk = FakeDesktop(linux.hyprland_parse(HYPR_MONITORS, HYPR_CLIENTS, {"pid": 0x20}, {"x": 700, "y": 400}))
    linux._set_desktop(desk)
    try:
        data = watcher.sample(linux, min_cover=0.1)
        assert desk.calls == 1
        assert data == {"n_displays": 2, "focused": 1, "d0": "kitty", "d1": "spotify"}
        watcher.sample(linux, min_cover=0.1)
        assert desk.calls == 2  # every sample reads the desktop afresh
    finally:
        linux._set_desktop(None)


def test_the_watcher_exits_when_the_desktop_is_gone():
    class Client:
        client_name, client_hostname = "aw-watcher-displays", "host"
        heartbeats = 0

        def heartbeat(self, *a, **k):
            Client.heartbeats += 1

    linux._set_desktop(FakeDesktop(None, error=linux.DesktopGone("the X server closed the connection")))
    try:
        args = SimpleNamespace(host=None, port=None, testing=True, poll_time=0.0, min_share=0.25, min_cover=0.1)
        w = watcher.DisplaysWatcher(args, linux, client=Client())
        with pytest.raises(SystemExit) as exc:
            w.heartbeat_loop(max_ticks=5)
        assert exc.value.code == 1 and Client.heartbeats == 0
    finally:
        linux._set_desktop(None)
