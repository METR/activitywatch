aw-watcher-displays
===================

Records which app is on top of each of your screens, and which screen you are using, with [ActivityWatch](https://activitywatch.net).
Works on macOS and Windows.

It records app names only: no window titles, no URLs, no screenshots, and it needs no extra permission on either OS.
Everything goes to your local ActivityWatch server.

## What it records

One event per state in the bucket `aw-watcher-displays_<hostname>` (type `display.topmost`), for example:

```json
{"n_displays": 2, "focused": 0, "d0": "Code", "d1": "Ghostty"}
```

- `d0`, `d1`, ...: the app whose window is on top of that display, or `null` when there is no window on it or the
  display is asleep. Displays are numbered built-in screen first on macOS, then left to right (on Windows: left to
  right). The numbering follows the current set of displays, so it shifts when a display is added or removed or the
  laptop lid is closed; `n_displays` tells you when that happened.
- `focused`: the display holding the front window of the app you are using (the display under the mouse when that
  cannot be placed, e.g. the Windows desktop or taskbar is in front), or `null`. Never an asleep display.
- While the screen is locked, every display reads `null` and `focused` is `null`. On Windows this is detected when
  the lock screen is in front; sleeping monitors are not detected on Windows.

A window counts as a display's top app when at least a quarter of the window is on that display and it covers at least
`min_cover` (default 10%) of the display, so a small floating window such as a video-call thumbnail does not hide the
window behind it. When every window on a display is smaller than that, the frontmost one is used.

App names match aw-watcher-window's on each OS ("Google Chrome" on macOS, "chrome.exe" on Windows), so the two buckets
can be joined.

## Usage

```sh
aw-watcher-displays --once      # print one sample and exit, without contacting the server
aw-watcher-displays --testing   # send to a testing server (port 5666)
aw-watcher-displays             # send to the normal server
```

Options: `--host`, `--port`, `--testing`, `--verbose`, `--poll-time` (seconds, default 1), `--min-cover` (0 to 1).
The same settings live in the config file `aw-watcher-displays.toml` in ActivityWatch's config directory.

To have ActivityWatch start it, add it to the launcher's autostart list, e.g. in `aw-qt.toml`:

```toml
[aw-qt]
autostart_modules = ["aw-server", "aw-watcher-afk", "aw-watcher-window", "aw-watcher-displays"]
```

The ActivityWatch builds for macOS and Windows include it.

## Build and test

```sh
poetry install
make test
make package   # PyInstaller build into dist/aw-watcher-displays
```

## Credits

The display geometry and the macOS window-server reads are adapted from Khalid Alim's display sampler.
