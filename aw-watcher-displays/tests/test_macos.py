"""Smoke test of the real Quartz reads on this Mac."""
import platform

import pytest

pytestmark = pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")


def test_real_reads_have_the_expected_shape():
    from aw_watcher_displays import macos, watcher

    ds = macos.display_list()
    assert ds and [d["idx"] for d in ds] == list(range(len(ds)))
    assert set(ds[0]) == {"idx", "id", "x", "y", "w", "h", "asleep", "builtin", "main"}
    for x in macos.window_list():
        assert set(x) == {"owner", "pid", "number", "x", "y", "w", "h"}
    assert isinstance(macos.session_locked(), bool)
    pos = macos.cursor_position()
    assert pos is None or len(pos) == 2

    data = watcher.sample(macos, min_cover=0.1)
    assert data["n_displays"] == len(ds)
    assert set(data) == {"n_displays", "focused"} | {f"d{i}" for i in range(len(ds))}
