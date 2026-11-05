"""Linux backend: X11 (window managers that set the standard EWMH hints), Sway and Hyprland.

Other Wayland desktops (GNOME, KDE) don't let apps see other apps' windows, so the watcher stops with an error there.
App names match ActivityWatch's Linux window watchers: the WM_CLASS class on X11 ("firefox"), the Wayland app id or
the X11 class on Sway and Hyprland.
"""
import json
import os
import socket
import struct
import time
from typing import Any, Dict, List, Optional, Tuple

BUILTIN_PREFIXES = ("eDP", "LVDS", "DSI")  # connector names of laptop panels
SNAPSHOT_TTL_S = 0.2  # one read of the desktop serves all calls of a sample
STICKY = 0xFFFFFFFF  # _NET_WM_DESKTOP of a window shown on every desktop


class DesktopGone(RuntimeError):
    """The connection to the X server or compositor is gone for good: the watcher exits and its launcher restarts
    it, as aw-watcher-window does."""


FATAL_ERRORS = (DesktopGone,)


def _builtin(name: Optional[str]) -> bool:
    return bool(name) and str(name).startswith(BUILTIN_PREFIXES)


def _ordered(displays: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    displays.sort(key=lambda d: (not d["builtin"], d["x"], d["y"]))
    for i, d in enumerate(displays):
        d["idx"] = i
    return displays


def _display(ident: Any, name: Optional[str], x: float, y: float, w: float, h: float,
             main: bool = False, asleep: bool = False) -> Dict[str, Any]:
    return {"idx": 0, "id": ident, "x": float(x), "y": float(y), "w": float(w), "h": float(h), "asleep": asleep,
            "builtin": _builtin(name), "main": main}


def _window(owner: Optional[str], pid: Optional[int], number: int, x: float, y: float, w: float,
            h: float) -> Dict[str, Any]:
    return {"owner": owner or "unknown", "pid": pid or None, "number": number, "x": float(x), "y": float(y),
            "w": float(w), "h": float(h)}


def _snap(displays, windows, focused_pid=None, focused_window=None, pointer=None) -> Dict[str, Any]:
    """One read of the desktop. pointer is the cursor, or on Sway the focused output's centre (Sway doesn't report
    the cursor): the fallback for which display is in use when no window has focus."""
    return {"displays": displays, "windows": windows, "focused_pid": focused_pid or None,
            "focused_window": focused_window or None, "pointer": pointer}


def _four(values: Any) -> List[int]:
    return (list(values or []) + [0, 0, 0, 0])[:4]


# ----------------------------------------------------------------------------- X11

X11_SKIP_TYPES = frozenset({
    "_NET_WM_WINDOW_TYPE_DESKTOP", "_NET_WM_WINDOW_TYPE_DOCK", "_NET_WM_WINDOW_TYPE_TOOLBAR",
    "_NET_WM_WINDOW_TYPE_MENU", "_NET_WM_WINDOW_TYPE_SPLASH", "_NET_WM_WINDOW_TYPE_DROPDOWN_MENU",
    "_NET_WM_WINDOW_TYPE_POPUP_MENU", "_NET_WM_WINDOW_TYPE_TOOLTIP", "_NET_WM_WINDOW_TYPE_NOTIFICATION",
    "_NET_WM_WINDOW_TYPE_COMBO", "_NET_WM_WINDOW_TYPE_DND",
})
X11_SKIP_STATES = frozenset({"_NET_WM_STATE_HIDDEN", "_NET_WM_STATE_SHADED"})  # minimised, rolled up


def x11_windows(entries: List[Dict[str, Any]], showing_desktop: bool = False,
                current_desktop: Optional[int] = None) -> List[Dict[str, Any]]:
    """Contract windows from raw X11 client info, front-to-back. Each entry: {id, viewable, types, states,
    x, y, w, h (client area, root coordinates), frame [left, right, top, bottom] (_NET_FRAME_EXTENTS),
    gtk_frame (_GTK_FRAME_EXTENTS: the invisible shadow of client-side decorated windows), desktop, wm_class, pid}.
    current_desktop, when given, also drops windows on other desktops that the window manager keeps mapped."""
    if showing_desktop:  # "show desktop": every app window is out of the way
        return []
    out = []
    for e in entries:
        if not e["viewable"] or set(e["types"]) & X11_SKIP_TYPES or set(e["states"]) & X11_SKIP_STATES:
            continue
        desk = e.get("desktop")
        if current_desktop is not None and desk is not None and desk not in (current_desktop, STICKY):
            continue
        left, right, top, bottom = _four(e.get("frame"))
        gl, gr, gt, gb = _four(e.get("gtk_frame"))
        out.append(_window(e.get("wm_class"), e.get("pid"), e["id"], e["x"] + gl - left, e["y"] + gt - top,
                           max(0, e["w"] - gl - gr + left + right), max(0, e["h"] - gt - gb + top + bottom)))
    return out


def x11_displays(crtcs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Displays from active RandR CRTCs: {id, outputs [names], x, y, w, h, primary}."""
    ds = []
    for c in crtcs:
        name = next((n for n in c["outputs"] if _builtin(n)), c["outputs"][0] if c["outputs"] else None)
        ds.append(_display(c["id"], name, c["x"], c["y"], c["w"], c["h"], main=bool(c.get("primary"))))
    return _ordered(ds)


class X11:
    """Reads the window manager's EWMH hints and RandR's outputs through python-xlib."""

    def __init__(self) -> None:
        import Xlib.display
        import Xlib.error

        self.XError = Xlib.error.XError
        self.Closed = Xlib.error.ConnectionClosedError
        try:
            self.d = Xlib.display.Display()
        except Xlib.error.DisplayError as e:
            raise RuntimeError(f"can't connect to the X server at DISPLAY={os.environ.get('DISPLAY')!r}: {e}") from e
        self.root = self.d.screen().root
        self._atoms: Dict[str, int] = {}
        self._names: Dict[int, str] = {}
        # KWin keeps windows of other virtual desktops mapped (for its thumbnails), so only there the desktop
        # number decides; other window managers unmap them, and i3 shows a different desktop on each monitor
        self.filter_desktops = self._wm_name() == "KWin"

    def _atom(self, name: str) -> int:
        if name not in self._atoms:
            self._atoms[name] = self.d.intern_atom(name)
        return self._atoms[name]

    def _atom_name(self, atom: int) -> str:
        if atom not in self._names:
            self._names[atom] = self.d.get_atom_name(atom)
        return self._names[atom]

    def _prop(self, win: Any, name: str) -> List[int]:
        p = win.get_full_property(self._atom(name), 0)  # 0 = AnyPropertyType
        return list(p.value) if p is not None and p.format == 32 else []

    def _wm_name(self) -> Optional[str]:
        try:
            check = self._prop(self.root, "_NET_SUPPORTING_WM_CHECK")
            if not check:
                return None
            p = self.d.create_resource_object("window", check[0]).get_full_property(self._atom("_NET_WM_NAME"), 0)
            value = p.value if p is not None else b""
            return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
        except self.XError:
            return None

    def crtcs(self) -> List[Dict[str, Any]]:
        """Active RandR CRTCs (one per picture shown; mirrored outputs share one). The "current" request reads the
        server's state without making it probe the monitors again."""
        res = self.root.xrandr_get_screen_resources_current()
        primary = self.root.xrandr_get_output_primary().output
        out = []
        for crtc in res.crtcs:
            info = self.d.xrandr_get_crtc_info(crtc, res.config_timestamp)
            if info.mode and info.outputs:
                names = [str(self.d.xrandr_get_output_info(o, res.config_timestamp).name) for o in info.outputs]
                out.append({"id": crtc, "outputs": names, "x": info.x, "y": info.y, "w": info.width,
                            "h": info.height, "primary": primary in info.outputs})
        return out

    def displays(self) -> List[Dict[str, Any]]:
        try:
            ds = x11_displays(self.crtcs())
        except (AttributeError, self.XError):  # no RandR
            ds = []
        if not ds:
            g = self.root.get_geometry()
            ds = [_display(0, None, 0, 0, g.width, g.height, main=True)]
        return _ordered(ds)

    def stacking(self) -> List[int]:
        """Client windows bottom-to-top. Window managers that publish only _NET_CLIENT_LIST (dwm, ...) are ordered by
        the position of each client's top-level window among the root's children, which X keeps bottom-to-top."""
        stack = self._prop(self.root, "_NET_CLIENT_LIST_STACKING")
        clients = self._prop(self.root, "_NET_CLIENT_LIST") if not stack else []
        if not clients:
            return stack
        order = {w.id: i for i, w in enumerate(self.root.query_tree().children)}
        ranked = []
        for wid in clients:
            win = self.d.create_resource_object("window", wid)
            try:
                while win.id not in order:  # walk up to the top-level (frame) window
                    parent = win.query_tree().parent
                    if not parent or parent.id == self.root.id or parent.id == win.id:
                        break
                    win = parent
            except self.XError:
                continue
            ranked.append((order.get(win.id, -1), wid))
        return [wid for _, wid in sorted(ranked)]

    def entries(self) -> List[Dict[str, Any]]:
        from Xlib import X

        out = []
        for wid in reversed(self.stacking()):
            win = self.d.create_resource_object("window", wid)
            try:
                if win.get_attributes().map_state != X.IsViewable:  # most window managers unmap other workspaces
                    continue
                geo = win.get_geometry()
                pos = self.root.translate_coords(win, 0, 0)
                cls = win.get_wm_class()
                pids = self._prop(win, "_NET_WM_PID")
                desk = self._prop(win, "_NET_WM_DESKTOP")
                out.append({"id": wid, "viewable": True,
                            "types": [self._atom_name(a) for a in self._prop(win, "_NET_WM_WINDOW_TYPE")],
                            "states": [self._atom_name(a) for a in self._prop(win, "_NET_WM_STATE")],
                            "x": pos.x, "y": pos.y, "w": geo.width, "h": geo.height,
                            "frame": self._prop(win, "_NET_FRAME_EXTENTS"),
                            "gtk_frame": self._prop(win, "_GTK_FRAME_EXTENTS"),
                            "desktop": desk[0] if desk else None,
                            "wm_class": cls[1] if cls else None, "pid": pids[0] if pids else None})
            except self.XError:  # the window closed while it was being read
                continue
        return out

    def focused(self) -> Tuple[Optional[int], Optional[int]]:
        """(pid, window id) of _NET_ACTIVE_WINDOW."""
        active = self._prop(self.root, "_NET_ACTIVE_WINDOW")
        if not active or not active[0]:
            return None, None
        try:
            pids = self._prop(self.d.create_resource_object("window", active[0]), "_NET_WM_PID")
        except self.XError:
            return None, None
        return (pids[0] if pids else None), active[0]

    def snapshot(self) -> Dict[str, Any]:
        try:
            while self.d.pending_events():  # nothing here reads events, but some reach every client anyway
                self.d.next_event()
            showing = self._prop(self.root, "_NET_SHOWING_DESKTOP")
            current = self._prop(self.root, "_NET_CURRENT_DESKTOP") if self.filter_desktops else []
            windows = x11_windows(self.entries(), showing_desktop=bool(showing and showing[0]),
                                  current_desktop=current[0] if current else None)
            pid, wid = self.focused()
            p = self.root.query_pointer()
            return _snap(self.displays(), windows, pid, wid, (float(p.root_x), float(p.root_y)))
        except self.Closed as e:
            raise DesktopGone(f"the X server closed the connection: {e}") from e


# ----------------------------------------------------------------------------- Sway

SWAY_GET_OUTPUTS, SWAY_GET_TREE = 3, 4


def _sway_views(node: Dict[str, Any], floating: bool = False) -> List[Tuple[bool, Dict[str, Any]]]:
    if node.get("pid") and node.get("type") in ("con", "floating_con"):
        return [(floating, node)]
    views: List[Tuple[bool, Dict[str, Any]]] = []
    for child in node.get("nodes") or []:
        views += _sway_views(child, floating)
    for child in reversed(node.get("floating_nodes") or []):  # listed bottom-to-top
        views += _sway_views(child, True)
    return views


def sway_parse(outputs: List[Dict[str, Any]], tree: Dict[str, Any]) -> Dict[str, Any]:
    """A snapshot from Sway's GET_OUTPUTS and GET_TREE replies."""
    active = [o for o in outputs if o.get("active")]
    displays, current, centre = [], {}, None
    for i, o in enumerate(active):
        r = o["rect"]
        asleep = o.get("power") is False or o.get("dpms") is False
        displays.append(_display(i, o["name"], r["x"], r["y"], r["width"], r["height"], asleep=asleep))
        current[o["name"]] = o.get("current_workspace")
        if o.get("focused"):
            centre = (r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)

    ranked: List[Tuple[int, int, Dict[str, Any]]] = []
    focused_pid = focused_window = None
    for out in tree.get("nodes") or []:
        for ws in out.get("nodes") or []:
            if ws.get("type") != "workspace" or ws.get("name") != current.get(out.get("name")):
                continue
            for floating, v in _sway_views(ws):
                if v.get("visible") is False:  # a background tab of a tabbed or stacked container
                    continue
                if v.get("focused"):
                    focused_pid, focused_window = v.get("pid"), v["id"]
                rank = 0 if v.get("fullscreen_mode") else (1 if floating else 2)
                props = v.get("window_properties") or {}
                r = v["rect"]
                ranked.append((rank, len(ranked), _window(v.get("app_id") or props.get("class"), v.get("pid"),
                                                          v["id"], r["x"], r["y"], r["width"], r["height"])))
    windows = [w for _, _, w in sorted(ranked, key=lambda t: (t[0], t[1]))]
    return _snap(_ordered(displays), windows, focused_pid, focused_window, centre)


def _connect(path: str) -> socket.socket:
    s = socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM)  # Unix socket: Linux only
    s.settimeout(2)
    try:
        s.connect(path)
    except (FileNotFoundError, ConnectionRefusedError) as e:  # the compositor has exited
        s.close()
        raise DesktopGone(f"the compositor's socket {path} is gone: {e}") from e
    return s


def _recv(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("the compositor closed the connection")
        buf += chunk
    return buf


def _json(raw: bytes) -> Any:
    # Window titles need not be valid UTF-8; only app ids are used, so replace what doesn't decode
    return json.loads(raw.decode("utf-8", "replace") if raw else "{}")


class Sway:
    """Talks to Sway over its IPC socket (the i3 protocol)."""

    def __init__(self, path: str) -> None:
        self.path = path

    def _ipc(self, msg_type: int) -> Any:
        with _connect(self.path) as s:
            s.sendall(b"i3-ipc" + struct.pack("=II", 0, msg_type))
            length, _ = struct.unpack("=II", _recv(s, 14)[6:])
            return _json(_recv(s, length))

    def snapshot(self) -> Dict[str, Any]:
        return sway_parse(self._ipc(SWAY_GET_OUTPUTS), self._ipc(SWAY_GET_TREE))


# ----------------------------------------------------------------------------- Hyprland


def _hypr_logical(px_w: int, px_h: int, scale: float) -> Tuple[int, int]:
    """Logical size of a monitor. Hyprland snaps its scale to k/120 so that pixels/scale is whole, but hyprctl up to
    0.55 prints the scale with 2 decimals (1.566667 as 1.57), so find that k again."""
    k0 = scale * 120
    for k in sorted({round(k0) - 1, round(k0), round(k0) + 1}, key=lambda k: abs(k - k0)):
        if k > 0 and (px_w * 120) % k == 0 and (px_h * 120) % k == 0:
            return px_w * 120 // k, px_h * 120 // k
    return round(px_w / scale), round(px_h / scale)


def _hist(c: Dict[str, Any]) -> int:
    h = c.get("focusHistoryID", -1)
    return h if isinstance(h, int) and h >= 0 else 1 << 30  # -1: never focused


def hyprland_parse(monitors: List[Dict[str, Any]], clients: List[Dict[str, Any]], active: Dict[str, Any],
                   cursor: Dict[str, Any]) -> Dict[str, Any]:
    """A snapshot from hyprctl's monitors, clients, activewindow and cursorpos replies."""
    displays, visible, special = [], set(), set()
    for m in monitors:
        if m.get("disabled"):
            continue
        w, h = _hypr_logical(m["width"], m["height"], float(m.get("scale") or 1.0))  # width/height: mode in pixels
        if int(m.get("transform") or 0) % 2:  # rotated by 90 or 270 degrees
            w, h = h, w
        displays.append(_display(m.get("id"), m.get("name"), m["x"], m["y"], w, h,
                                 asleep=m.get("dpmsStatus") is False))
        visible.add((m.get("activeWorkspace") or {}).get("id"))
        sp = (m.get("specialWorkspace") or {}).get("id")
        if sp:
            special.add(sp)

    shown = [(i, c) for i, c in enumerate(clients)
             if c.get("mapped", True) and not c.get("hidden")
             and ((c.get("workspace") or {}).get("id") in visible | special or c.get("pinned"))]
    fullscreen = {(c.get("workspace") or {}).get("id"): _hist(c) for _, c in shown if c.get("fullscreen")}

    ranked = []
    for i, c in shown:  # the clients reply is in z-order, back to front
        ws = (c.get("workspace") or {}).get("id")
        over = False
        if ws in fullscreen and not c.get("fullscreen") and c.get("floating"):
            flag = c.get("allowedOverFullscreen", c.get("overFullscreen"))  # exposed by newer Hyprland
            over = bool(flag) if flag is not None else _hist(c) < fullscreen[ws]
            if not (over or c.get("pinned")):
                continue  # faded out behind the full-screen window
        # Hyprland draws a shown special workspace on top, then pinned windows and floats kept over a full-screen
        # window, then full-screen, other floating and tiled windows
        rank = (0 if ws in special else 1 if c.get("pinned") or over else 2 if c.get("fullscreen")
                else 3 if c.get("floating") else 4)
        (x, y), (w, h) = c["at"], c["size"]
        ranked.append((rank, -i, _window(c.get("class") or c.get("initialClass"), c.get("pid"),
                                         int(str(c.get("address", "0")), 16), x, y, w, h)))
    windows = [w for _, _, w in sorted(ranked, key=lambda t: (t[0], t[1]))]
    pointer = (float(cursor["x"]), float(cursor["y"])) if "x" in cursor and "y" in cursor else None
    address = active.get("address")
    return _snap(_ordered(displays), windows, active.get("pid"), int(str(address), 16) if address else None, pointer)


class Hyprland:
    """Talks to Hyprland over its request socket (the same one hyprctl uses)."""

    def __init__(self, signature: str, runtime_dir: Optional[str]) -> None:
        candidates = [os.path.join(d, "hypr", signature, ".socket.sock") for d in (runtime_dir, "/tmp") if d]
        self.path = next((p for p in candidates if os.path.exists(p)), candidates[0])

    def _request(self, command: str) -> Any:
        with _connect(self.path) as s:
            s.sendall(f"j/{command}".encode())
            buf = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return _json(buf)

    def snapshot(self) -> Dict[str, Any]:
        return hyprland_parse(self._request("monitors"), self._request("clients"), self._request("activewindow"),
                              self._request("cursorpos"))


# ----------------------------------------------------------------------------- backend contract


def detect(env: Optional[Dict[str, str]] = None) -> str:
    env = dict(os.environ) if env is None else env
    if env.get("SWAYSOCK"):
        return "sway"
    if env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if env.get("WAYLAND_DISPLAY") or env.get("XDG_SESSION_TYPE") == "wayland":
        desktop = env.get("XDG_CURRENT_DESKTOP") or "this desktop"
        raise RuntimeError(f"aw-watcher-displays can't see other apps' windows on {desktop} under Wayland. "
                           "On Linux it supports X11 sessions, Sway and Hyprland.")
    if env.get("DISPLAY"):
        return "x11"
    raise RuntimeError("no graphical session found (DISPLAY, SWAYSOCK or HYPRLAND_INSTANCE_SIGNATURE is not set)")


_desktop: Any = None
_cache: Tuple[float, Optional[Dict[str, Any]]] = (0.0, None)


def init() -> None:
    kind = detect()
    if kind == "sway":
        _set_desktop(Sway(os.environ["SWAYSOCK"]))
    elif kind == "hyprland":
        _set_desktop(Hyprland(os.environ["HYPRLAND_INSTANCE_SIGNATURE"], os.environ.get("XDG_RUNTIME_DIR")))
    else:
        _set_desktop(X11())


def _set_desktop(desktop: Any) -> None:
    """Also used by tests to put in a fake desktop."""
    global _desktop, _cache
    _desktop, _cache = desktop, (0.0, None)


def _snapshot() -> Dict[str, Any]:
    global _cache
    if _cache[1] is None or time.monotonic() - _cache[0] > SNAPSHOT_TTL_S:
        snap = _desktop.snapshot()
        _cache = (time.monotonic(), snap)  # stamped after the read, so a slow read is not repeated in a sample
    return _cache[1]  # type: ignore[return-value]


def display_list() -> List[Dict[str, Any]]:
    global _cache
    _cache = (0.0, None)  # each sample starts here: always read the desktop afresh
    return [dict(d) for d in _snapshot()["displays"]]


def window_list() -> List[Dict[str, Any]]:
    return list(_snapshot()["windows"])


def frontmost_pid(windows: List[Dict[str, Any]]) -> Optional[int]:
    return _snapshot()["focused_pid"]


def frontmost_window() -> Optional[int]:
    """The focused window's number, which places it exactly even when its app has windows on other displays."""
    return _snapshot()["focused_window"]


def cursor_position() -> Optional[Tuple[float, float]]:
    return _snapshot()["pointer"]


def session_locked() -> Optional[bool]:
    """Not detected on Linux; aw-watcher-afk still marks the time as away after its timeout."""
    return None
