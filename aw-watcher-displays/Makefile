.PHONY: build test typecheck package clean

build:
	poetry install

test:
	poetry run aw-watcher-displays --help  # Ensures that it at least starts
	poetry run pytest tests -v
	make typecheck

typecheck:
	poetry run mypy aw_watcher_displays --ignore-missing-imports
	poetry run mypy aw_watcher_displays --ignore-missing-imports --platform win32
	poetry run mypy aw_watcher_displays --ignore-missing-imports --platform linux

package:
	pyinstaller aw-watcher-displays.spec --clean --noconfirm

clean:
	rm -rf build dist
	rm -rf aw_watcher_displays/__pycache__
