"""Pure geometry: which window is on top of each display. No platform imports, so it is unit-testable anywhere.

Adapted from Khalid Alim's display sampler. The functions below are his, unchanged except that assign_windows()
gained the min_share/min_cover keyword arguments.

Windows are dicts {"owner", "pid", "number", "x", "y", "w", "h"} in front-to-back order. Displays are dicts with at
least {"x", "y", "w", "h"}. Both use one global desktop coordinate space with the origin at the top-left corner of
the main display and y growing downwards (Quartz global coordinates on macOS, virtual-screen pixels on Windows).
"""
from typing import Any, Dict, List, Optional

MIN_WINDOW_PX = 50  # ignore tiny windows (tooltips, status items, indicator strips)
MIN_SHARE = 0.25  # a window "is on" a display when at least this share of its area lies there
SKIP_OWNERS = frozenset(
    {
        "Window Server",
        "Dock",
        "loginwindow",
        # system dialogs that float over everything and would hide the real topmost window
        "universalAccessAuthWarn",
        "SecurityAgent",
        "UserNotificationCenter",
        "CoreServicesUIAgent",
    }
)


def intersect_area(ax, ay, aw, ah, bx, by, bw, bh):
    """Area of the overlap of two axis-aligned rectangles given as (x, y, w, h)."""
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def window_share(win, disp):
    """Fraction of the window's own area that lies on the display (0.0 to 1.0)."""
    area = win["w"] * win["h"]
    if area <= 0:
        return 0.0
    return intersect_area(win["x"], win["y"], win["w"], win["h"], disp["x"], disp["y"], disp["w"], disp["h"]) / area


def display_cover(win, disp):
    """Fraction of the display's area that the window covers (0.0 to 1.0)."""
    area = disp["w"] * disp["h"]
    if area <= 0:
        return 0.0
    return intersect_area(win["x"], win["y"], win["w"], win["h"], disp["x"], disp["y"], disp["w"], disp["h"]) / area


def usable(win):
    return win.get("owner") not in SKIP_OWNERS and win["w"] >= MIN_WINDOW_PX and win["h"] >= MIN_WINDOW_PX


def assign_windows(
    windows: List[Dict[str, Any]],
    displays: List[Dict[str, Any]],
    min_share: float = MIN_SHARE,
    min_cover: float = 0.0,
) -> List[Dict[str, Any]]:
    """For each display: the topmost usable window with share >= min_share on it, and the owners visible there.

    Returns a list aligned with `displays`: [{"topmost": {...} or None, "visible_owners": [...]}, ...].
    `topmost` carries owner, pid, number, x, y, w, h, share (of the window's own area, 2 decimals) and
    cover (of the display's area, 2 decimals). A window that straddles two displays with at least a quarter
    of its area on each can be topmost on both; mirrored displays with identical bounds see the same window.
    A window only becomes topmost when it also covers at least `min_cover` of the display, so a small floating
    window (a video-call thumbnail) does not hide the real window behind it; when no window on a display is that
    large, the frontmost one is used anyway. min_cover=0.0 keeps the original behaviour.
    """
    out: List[Dict[str, Any]] = [{"topmost": None, "visible_owners": []} for _ in displays]
    fallback: List[Optional[Dict[str, Any]]] = [None for _ in displays]
    for win in windows:
        if not usable(win):
            continue
        for i, disp in enumerate(displays):
            share = window_share(win, disp)
            if share < min_share:
                continue
            slot = out[i]
            if win["owner"] not in slot["visible_owners"]:
                slot["visible_owners"].append(win["owner"])
            if slot["topmost"] is None:
                cover = display_cover(win, disp)
                top = {"owner": win["owner"], "pid": win["pid"], "number": win.get("number"),
                       "x": win["x"], "y": win["y"], "w": win["w"], "h": win["h"],
                       "share": round(share, 2), "cover": round(cover, 2)}
                if cover >= min_cover:
                    slot["topmost"] = top
                elif fallback[i] is None:
                    fallback[i] = top
    for slot, small in zip(out, fallback):
        if slot["topmost"] is None:
            slot["topmost"] = small
    return out


def focused_display(windows, displays, frontmost_pid) -> Optional[int]:
    """Index of the display holding most of the frontmost app's front window, or None.

    The frontmost app's front window is its first usable window in front-to-back order. When the
    frontmost app has no usable on-screen window (empty Finder, lock screen) the result is None.
    """
    if frontmost_pid is None:
        return None
    for win in windows:
        if win["pid"] != frontmost_pid or not usable(win):
            continue
        best, best_area = None, 0.0
        for i, disp in enumerate(displays):
            a = intersect_area(win["x"], win["y"], win["w"], win["h"], disp["x"], disp["y"], disp["w"], disp["h"])
            if a > best_area:
                best, best_area = i, a
        return best
    return None


def display_of_point(displays, x, y) -> Optional[int]:
    """Index of the display containing the point (x, y) in global coordinates, or None."""
    for i, d in enumerate(displays):
        if d["x"] <= x < d["x"] + d["w"] and d["y"] <= y < d["y"] + d["h"]:
            return i
    return None
