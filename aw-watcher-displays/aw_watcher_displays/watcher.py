import argparse
import json
import logging
import os
import platform
import sys
from datetime import datetime, timezone
from time import sleep
from typing import Any, Dict, List, Optional

from aw_client import ActivityWatchClient
from aw_core.config import load_config_toml
from aw_core.log import setup_logging
from aw_core.models import Event

from . import geometry

logger = logging.getLogger(__name__)

CLIENT_NAME = "aw-watcher-displays"
EVENT_TYPE = "display.topmost"

default_config = """
[aw-watcher-displays]
poll_time = 1.0
min_share = 0.25
min_cover = 0.1

[aw-watcher-displays-testing]
poll_time = 1.0
min_share = 0.25
min_cover = 0.1
""".strip()


def load_config(testing: bool) -> Dict[str, Any]:
    section = CLIENT_NAME + ("-testing" if testing else "")
    return load_config_toml(CLIENT_NAME, default_config)[section]


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    argv = sys.argv[1:] if argv is None else argv
    # get testing in a dirty way, because we need it for the config lookup (same as aw-watcher-afk)
    config = load_config("--testing" in argv)

    parser = argparse.ArgumentParser(description="A watcher for which app is on top of each display.")
    parser.add_argument("--host", dest="host")
    parser.add_argument("--port", dest="port")
    parser.add_argument("--testing", dest="testing", action="store_true", help="run in testing mode")
    parser.add_argument("--verbose", dest="verbose", action="store_true", help="run with verbose logging")
    parser.add_argument("--poll-time", dest="poll_time", type=float, default=float(config["poll_time"]))
    parser.add_argument("--min-cover", dest="min_cover", type=float, default=float(config["min_cover"]),
                        help="share of a display a window must cover to count as its top app")
    parser.add_argument("--once", dest="once", action="store_true",
                        help="print one sample as JSON and exit, without contacting the server")
    # Launchers may pass flags we don't use; ignore them rather than exit
    args, unknown = parser.parse_known_args(argv)
    args.min_share = float(config["min_share"])
    args.unknown = unknown
    return args


def load_backend() -> Any:
    system = platform.system()
    if system == "Darwin":
        from . import macos as backend
    elif system == "Windows":
        from . import windows as backend  # type: ignore[no-redef]
    elif system == "Linux":
        from . import linux as backend  # type: ignore[no-redef]
    else:
        raise RuntimeError(f"aw-watcher-displays does not support {system} (macOS, Windows and Linux only)")
    backend.init()
    return backend


def sample(backend: Any, min_share: float = geometry.MIN_SHARE, min_cover: float = 0.0) -> Dict[str, Any]:
    """{"n_displays", "focused", "d0", "d1", ...}: the app on top of each display, and which display is in use.

    Only app names are recorded, never titles. While the screen is locked nothing is visible, so every display
    reads None.
    """
    displays = backend.display_list()
    data: Dict[str, Any] = {"n_displays": len(displays), "focused": None}
    if backend.session_locked():
        data.update({f"d{d['idx']}": None for d in displays})
        return data

    windows = backend.window_list()
    per = geometry.assign_windows(windows, displays, min_share=min_share, min_cover=min_cover)
    frontmost_window = getattr(backend, "frontmost_window", None)
    focused = geometry.focused_display(windows, displays, backend.frontmost_pid(windows),
                                       frontmost_window() if frontmost_window else None)
    if focused is None:  # the app in front could not be placed on a display: use the display under the cursor
        pos = backend.cursor_position()
        if pos is not None:
            focused = geometry.display_of_point(displays, pos[0], pos[1])
    if focused is not None and displays[focused]["asleep"]:
        focused = None
    data["focused"] = focused
    for d, slot in zip(displays, per):
        top = slot["topmost"]
        data[f"d{d['idx']}"] = None if d["asleep"] or top is None else top["owner"]
    return data


class DisplaysWatcher:
    def __init__(self, args: argparse.Namespace, backend: Any, client: Optional[ActivityWatchClient] = None):
        self.args = args
        self.backend = backend
        self.client = client or ActivityWatchClient(CLIENT_NAME, host=args.host, port=args.port, testing=args.testing)
        self.bucketname = f"{self.client.client_name}_{self.client.client_hostname}"
        self._initial_ppid = os.getppid()

    def run(self, max_ticks: Optional[int] = None) -> None:
        logger.info("aw-watcher-displays started")
        self.client.wait_for_start()
        # Same order as aw-watcher-afk: registering the bucket before the queue starts makes the queue create it
        # before it sends anything (aw-client warns about this order, but the other order races on first run)
        self.client.create_bucket(self.bucketname, EVENT_TYPE, queued=True)
        with self.client:
            self.heartbeat_loop(max_ticks)

    def heartbeat_loop(self, max_ticks: Optional[int] = None) -> None:
        pulsetime = self.args.poll_time + 1.0
        fatal = getattr(self.backend, "FATAL_ERRORS", ())  # e.g. the X server has gone away
        ticks = 0
        while max_ticks is None or ticks < max_ticks:
            ticks += 1
            if platform.system() in ["Darwin", "Linux"] and os.getppid() != self._initial_ppid:
                logger.info("aw-watcher-displays stopped because parent process died")
                break
            try:
                data = sample(self.backend, self.args.min_share, self.args.min_cover)
                e = Event(timestamp=datetime.now(timezone.utc), data=data)
                self.client.heartbeat(self.bucketname, e, pulsetime=pulsetime, queued=True)
            except fatal as e:
                logger.error(f"aw-watcher-displays stopped: {e}")
                raise SystemExit(1)
            except Exception:
                logger.exception("Failed to sample the displays; skipping this tick")
            sleep(self.args.poll_time)


def main() -> None:
    args = parse_args()
    setup_logging(CLIENT_NAME, testing=args.testing, verbose=args.verbose, log_stderr=True, log_file=True)
    if args.unknown:
        logger.warning(f"Ignoring unknown arguments: {args.unknown}")
    try:
        backend = load_backend()
    except RuntimeError as e:
        logger.error(str(e))
        sys.exit(1)

    if args.once:
        print(json.dumps(sample(backend, args.min_share, args.min_cover)))
        return
    try:
        DisplaysWatcher(args, backend).run()
    except KeyboardInterrupt:
        logger.info("aw-watcher-displays stopped by keyboard interrupt")
