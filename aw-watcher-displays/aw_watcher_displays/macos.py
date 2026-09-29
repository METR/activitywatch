"""macOS backend: Quartz window-server reads. Needs no permission: app names and window bounds are available
without Screen Recording, and no window titles are read.

display_list(), window_list() and session_locked() are adapted from Khalid Alim's display sampler.
"""
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

import Quartz

from .geometry import usable

logger = logging.getLogger(__name__)
_logged_errors: Set[int] = set()


def init() -> None:
    pass


def display_list() -> List[Dict[str, Any]]:
    """Online displays as plain dicts, builtin first, then by position. idx is the position in this order."""
    err, ids, _n = Quartz.CGGetOnlineDisplayList(16, None, None)
    if err != 0 and err not in _logged_errors:
        _logged_errors.add(err)
        logger.warning(f"CGGetOnlineDisplayList error {err}: display list empty this tick")
    out = []
    for did in ids or ():
        b = Quartz.CGDisplayBounds(did)
        out.append({"idx": 0, "id": int(did), "x": float(b.origin.x), "y": float(b.origin.y),
                    "w": float(b.size.width), "h": float(b.size.height),
                    "asleep": bool(Quartz.CGDisplayIsAsleep(did)), "builtin": bool(Quartz.CGDisplayIsBuiltin(did)),
                    "main": bool(Quartz.CGDisplayIsMain(did))})
    out.sort(key=lambda d: (not d["builtin"], d["x"], d["y"]))
    for i, d in enumerate(out):
        d["idx"] = i
    return out


def window_list() -> List[Dict[str, Any]]:
    """Normal-layer, visible on-screen windows front-to-back as plain dicts."""
    wl = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID) or []
    out = []
    for w in wl:
        if w.get("kCGWindowLayer") != 0 or w.get("kCGWindowAlpha", 1.0) == 0:
            continue
        b = w.get("kCGWindowBounds")
        if b is None:
            continue
        out.append({"owner": w.get("kCGWindowOwnerName"), "pid": w.get("kCGWindowOwnerPID"),
                    "number": w.get("kCGWindowNumber"), "x": float(b["X"]), "y": float(b["Y"]),
                    "w": float(b["Width"]), "h": float(b["Height"])})
    return out


def frontmost_pid(windows: List[Dict[str, Any]]) -> Optional[int]:
    """Owner of the first usable window in front-to-back order: the app in front, without needing AppKit."""
    first = next((w for w in windows if usable(w)), None)
    return first["pid"] if first else None


def cursor_position() -> Optional[Tuple[float, float]]:
    ev = Quartz.CGEventCreate(None)
    if ev is None:
        return None
    p = Quartz.CGEventGetLocation(ev)
    return float(p.x), float(p.y)


def session_locked() -> Optional[bool]:
    """Screen locked, or another user has switched in (fast user switching)."""
    sess = Quartz.CGSessionCopyCurrentDictionary() or {}
    return bool(sess.get("CGSSessionScreenIsLocked", False)) or not sess.get("kCGSessionOnConsoleKey", True)
