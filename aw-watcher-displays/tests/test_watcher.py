import json
from types import SimpleNamespace

import pytest

from aw_watcher_displays import watcher

LAPTOP = {"idx": 0, "id": 1, "x": 0.0, "y": 0.0, "w": 1728.0, "h": 1117.0, "asleep": False, "builtin": True,
          "main": True}
RIGHT = {"idx": 1, "id": 2, "x": 1728.0, "y": 0.0, "w": 2560.0, "h": 1440.0, "asleep": False, "builtin": False,
         "main": False}


class FakeBackend:
    def __init__(self, displays, windows, front=None, cursor=None, locked=False):
        self.displays, self.windows, self.front, self.cursor, self.locked = displays, windows, front, cursor, locked

    def display_list(self):
        return [dict(d) for d in self.displays]

    def window_list(self):
        return list(self.windows)

    def frontmost_pid(self, windows):
        return self.front

    def cursor_position(self):
        return self.cursor

    def session_locked(self):
        return self.locked


def win(owner, pid, x, y, w, h):
    return {"owner": owner, "pid": pid, "number": pid, "x": x, "y": y, "w": w, "h": h}


WINDOWS = [win("Ghostty", 2, 1800, 0, 2000, 1400), win("Code", 1, 0, 0, 1728, 1117)]


def test_sample_reports_one_app_per_display_and_the_focused_one():
    data = watcher.sample(FakeBackend([LAPTOP, RIGHT], WINDOWS, front=2))
    assert data == {"n_displays": 2, "focused": 1, "d0": "Code", "d1": "Ghostty"}


def test_sample_never_sends_titles_or_bounds():
    data = watcher.sample(FakeBackend([LAPTOP, RIGHT], WINDOWS, front=2))
    assert set(data) == {"n_displays", "focused", "d0", "d1"}


def test_asleep_display_reads_none():
    asleep = dict(RIGHT, asleep=True)
    assert watcher.sample(FakeBackend([LAPTOP, asleep], WINDOWS, front=1))["d1"] is None


def test_locked_session_hides_everything_without_listing_windows():
    class Locked(FakeBackend):
        def window_list(self):
            raise AssertionError("windows must not be listed while locked")

    data = watcher.sample(Locked([LAPTOP, RIGHT], WINDOWS, front=2, locked=True))
    assert data == {"n_displays": 2, "focused": None, "d0": None, "d1": None}


def test_an_asleep_display_is_never_the_focused_one():
    asleep = dict(RIGHT, asleep=True)
    assert watcher.sample(FakeBackend([LAPTOP, asleep], WINDOWS, front=2))["focused"] is None
    assert watcher.sample(FakeBackend([LAPTOP, asleep], WINDOWS, front=None, cursor=(2000.0, 50.0)))["focused"] is None


def test_focused_falls_back_to_the_cursor():
    data = watcher.sample(FakeBackend([LAPTOP, RIGHT], WINDOWS, front=None, cursor=(2000.0, 50.0)))
    assert data["focused"] == 1
    data = watcher.sample(FakeBackend([LAPTOP, RIGHT], WINDOWS, front=None, cursor=None))
    assert data["focused"] is None


def test_min_cover_is_applied():
    small = [win("zoom.us", 3, 10, 10, 320, 180)] + WINDOWS
    assert watcher.sample(FakeBackend([LAPTOP], small, front=1))["d0"] == "zoom.us"
    assert watcher.sample(FakeBackend([LAPTOP], small, front=1), min_cover=0.1)["d0"] == "Code"


@pytest.fixture
def no_config_file(monkeypatch):
    """Keep tests away from the real config directory."""
    monkeypatch.setattr(watcher, "load_config",
                        lambda testing: {"poll_time": 1.0, "min_share": 0.25, "min_cover": 0.1})


def test_unknown_launcher_arguments_are_tolerated(no_config_file):
    args = watcher.parse_args(["--port", "5667", "--testing", "--something-new", "x"])
    assert args.port == "5667" and args.testing is True
    assert args.unknown == ["--something-new", "x"]
    assert args.min_cover == 0.1 and args.min_share == 0.25 and args.poll_time == 1.0


class FakeClient:
    client_name = "aw-watcher-displays"
    client_hostname = "host"

    def __init__(self):
        self.buckets, self.heartbeats = [], []

    def wait_for_start(self):
        pass

    def create_bucket(self, bucket_id, event_type, queued=False):
        self.buckets.append((bucket_id, event_type, queued))

    def heartbeat(self, bucket_id, event, pulsetime, queued=False):
        self.heartbeats.append((bucket_id, event.data, pulsetime, queued))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def run_watcher(backend, ticks):
    args = SimpleNamespace(host=None, port=None, testing=True, poll_time=0.0, min_share=0.25, min_cover=0.1)
    client = FakeClient()
    watcher.DisplaysWatcher(args, backend, client=client).run(max_ticks=ticks)
    return client


def test_run_creates_the_bucket_and_heartbeats():
    client = run_watcher(FakeBackend([LAPTOP, RIGHT], WINDOWS, front=2), ticks=3)
    assert client.buckets == [("aw-watcher-displays_host", "display.topmost", True)]
    assert len(client.heartbeats) == 3
    bucket, data, pulsetime, queued = client.heartbeats[0]
    assert bucket == "aw-watcher-displays_host" and pulsetime == 1.0 and queued is True
    assert data == {"n_displays": 2, "focused": 1, "d0": "Code", "d1": "Ghostty"}


def test_a_failing_tick_is_skipped_not_fatal():
    class Flaky(FakeBackend):
        calls = 0

        def window_list(self):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise OSError("window server busy")
            return super().window_list()

    client = run_watcher(Flaky([LAPTOP, RIGHT], WINDOWS, front=2), ticks=3)
    assert len(client.heartbeats) == 2


def test_once_prints_json_without_a_server(no_config_file, monkeypatch, capsys):
    monkeypatch.setattr(watcher.sys, "argv", ["aw-watcher-displays", "--once"])
    monkeypatch.setattr(watcher, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(watcher, "load_backend", lambda: FakeBackend([LAPTOP, RIGHT], WINDOWS, front=2))
    monkeypatch.setattr(watcher, "ActivityWatchClient", None)  # would fail if anything tried to connect
    watcher.main()
    assert json.loads(capsys.readouterr().out) == {"n_displays": 2, "focused": 1, "d0": "Code", "d1": "Ghostty"}


def test_unsupported_platform_exits_with_an_error(no_config_file, monkeypatch):
    monkeypatch.setattr(watcher.sys, "argv", ["aw-watcher-displays", "--once"])
    monkeypatch.setattr(watcher, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(watcher.platform, "system", lambda: "Linux")
    with pytest.raises(SystemExit) as exc:
        watcher.main()
    assert exc.value.code == 1
