from aw_watcher_displays.geometry import assign_windows, display_of_point, focused_display

LAPTOP = {"x": 0.0, "y": 0.0, "w": 1728.0, "h": 1117.0}
RIGHT = {"x": 1728.0, "y": 0.0, "w": 2560.0, "h": 1440.0}
LEFT = {"x": -1920.0, "y": -200.0, "w": 1920.0, "h": 1080.0}


def win(owner, x, y, w, h, pid=None):
    return {"owner": owner, "pid": pid if pid is not None else hash(owner) % 1000, "number": 1,
            "x": x, "y": y, "w": w, "h": h}


def tops(windows, displays, **kw):
    return [s["topmost"]["owner"] if s["topmost"] else None for s in assign_windows(windows, displays, **kw)]


def test_single_display_frontmost_wins():
    ws = [win("Code", 0, 0, 1728, 1117), win("Slack", 100, 100, 800, 600)]
    assert tops(ws, [LAPTOP]) == ["Code"]


def test_two_displays_each_get_their_own_top():
    ws = [win("Code", 0, 0, 1728, 1117), win("Ghostty", 1800, 0, 2000, 1400)]
    assert tops(ws, [LAPTOP, RIGHT]) == ["Code", "Ghostty"]


def test_three_displays_with_negative_origin():
    ws = [win("Chrome", -1900, -150, 1800, 1000), win("Code", 0, 0, 1728, 1117), win("Ghostty", 1800, 0, 2000, 1400)]
    assert tops(ws, [LAPTOP, RIGHT, LEFT]) == ["Code", "Ghostty", "Chrome"]


def test_straddling_window_needs_a_quarter_of_its_area():
    # 80% on the laptop, 20% on the right display: top of the laptop only
    ws = [win("Code", 1728 - 800, 0, 1000, 800)]
    assert tops(ws, [LAPTOP, RIGHT]) == ["Code", None]
    # half and half: top of both
    ws = [win("Code", 1728 - 500, 0, 1000, 800)]
    assert tops(ws, [LAPTOP, RIGHT]) == ["Code", "Code"]


def test_mirrored_displays_see_the_same_window():
    ws = [win("Keynote", 0, 0, 1728, 1117)]
    assert tops(ws, [LAPTOP, dict(LAPTOP)]) == ["Keynote", "Keynote"]


def test_min_cover_skips_small_floating_windows():
    ws = [win("zoom.us", 10, 10, 320, 180), win("Code", 0, 0, 1728, 1117)]
    assert tops(ws, [LAPTOP]) == ["zoom.us"]  # original behaviour
    assert tops(ws, [LAPTOP], min_cover=0.1) == ["Code"]


def test_min_cover_falls_back_to_the_frontmost_window_when_all_are_small():
    ws = [win("Terminal", 100, 100, 560, 360), win("Calculator", 800, 100, 230, 400)]
    assert tops(ws, [LAPTOP], min_cover=0.3) == ["Terminal"]


def test_skipped_owners_and_tiny_windows_never_count():
    ws = [win("Dock", 0, 1000, 1728, 117), win("Tooltip", 5, 5, 40, 20), win("Finder", 0, 0, 1000, 800)]
    assert tops(ws, [LAPTOP]) == ["Finder"]


def test_nothing_on_screen():
    assert tops([], [LAPTOP, RIGHT]) == [None, None]


def test_focused_display_is_where_the_front_app_window_is():
    ws = [win("Ghostty", 1800, 0, 2000, 1400, pid=2), win("Code", 0, 0, 1728, 1117, pid=1)]
    assert focused_display(ws, [LAPTOP, RIGHT], 2) == 1
    assert focused_display(ws, [LAPTOP, RIGHT], 1) == 0
    assert focused_display(ws, [LAPTOP, RIGHT], 99) is None
    assert focused_display(ws, [LAPTOP, RIGHT], None) is None


def test_display_of_point():
    assert display_of_point([LAPTOP, RIGHT, LEFT], 10, 10) == 0
    assert display_of_point([LAPTOP, RIGHT, LEFT], 2000, 10) == 1
    assert display_of_point([LAPTOP, RIGHT, LEFT], -10, -100) == 2
    assert display_of_point([LAPTOP, RIGHT, LEFT], 10, 5000) is None
