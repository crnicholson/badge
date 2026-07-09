# Settings App — Design Spec

Date: 2026-07-08
Status: Approved for planning

## Purpose

Add a `settings` app to the Universe 2025 Tufty badge that lets a user:
1. See live battery percentage (and charging status).
2. Toggle a "High Contrast" accessibility mode.

Large-text mode is explicitly out of scope for this iteration (deferred).

## Context / constraints discovered during brainstorming

- Apps live in `badge/apps/<name>/` and are auto-discovered by `badge/apps/menu/__init__.py` (any directory with `__init__.py`, except `menu`/`startup`). No manifest registration needed.
- `icon.png` is optional — the menu falls back to `apps/menu/default_icon.png` if an app doesn't ship one. This app will ship without a custom icon initially.
- There is no existing system-wide theming/settings mechanism. Each app draws independently to the shared `screen` object.
- The badge's actual, in-use API (confirmed by every existing app, `badge/AGENTS.md`, `badgerware/*.md`, and `simulator/badge_simulator.py`) is: `screen.brush`, `brushes.color()`, `io.ticks`, `get_battery_level()`, `is_charging()`. There is no documented pixel-filter or pixel-readback primitive in this API generation.
- A newer docs repo (`pimoroni/badgeware-docs`, pushed the day before this spec) documents a different API generation (`screen.pen`, `color.xxx`, `badge.ticks`, and filters `.onebit()`/`.monochrome()`/`.blur()`/`.dither()`, plus `.get()`/`.raw` pixel access). None of these names appear anywhere in this repo's apps or in the simulator, so this newer API is very likely **not** what's flashed on this specific badge. Using it is a calculated, guarded bet, not a confirmed capability.

## Design

### 1. New app: `badge/apps/settings/__init__.py`

Follows the standalone-app pattern used by `apps/wifi` (dark background, phosphor-green accent color, `ark`/`absolute` pixel fonts, `sys.path.insert`/`os.chdir` boilerplate).

State:
- `high_contrast: bool` — loaded from `/settings.json` in `init()`, defaulting to `False` if the file is missing or unparseable.

Rendering (`update()`):
- Header: "Settings"
- Row 1 — Battery: `f"{get_battery_level()}%"`, plus a battery-bar icon matching `apps/menu/ui.py:98-114`'s style, plus a "Charging" label when `is_charging()` is true.
- Row 2 — High Contrast: `ON` / `OFF` label with an inline hint ("A to toggle").

Input handling:
- `BUTTON_A` pressed → flip `high_contrast`, write `{"high_contrast": high_contrast}` to `/settings.json` immediately (not deferred to `on_exit()`, since the HOME button path in `main.py` calls `on_exit()` and then does a hard `machine.reset()` — writing on every toggle is safer than relying on a clean exit).

Persistence file: `/settings.json` (filesystem root, sibling to `/secrets.py`), written with the standard `json.dump` / loaded with `json.load` inside `try/except`, matching the "Persistent State" pattern in `AGENTS.md`.

No `icon.png` shipped — falls back to the menu's default icon.

### 2. `badge/main.py` changes

Add:
```python
import json
from badgeware import run, io, screen
```
(`screen` and `json` are new imports; `run`/`io` already present.)

Add a helper:
```python
def apply_high_contrast():
    try:
        with open("/settings.json") as f:
            settings = json.load(f)
    except (OSError, ValueError):
        return
    if not settings.get("high_contrast"):
        return
    try:
        screen.onebit()
    except AttributeError:
        pass  # not supported on this firmware build — no-op, no fallback effect
```

Wrap the two persistent update loops so the effect is applied consistently while the user is looking at the menu or any app (not the one-time boot cinematic):

```python
def wrap(fn):
    def wrapped():
        fn()
        apply_high_contrast()
    return wrapped
```

- `run(menu.update)` → `run(wrap(menu.update))`
- `run(running_app.update)` → `run(wrap(running_app.update))`

The settings file is re-read every frame (it's a few bytes; existing apps already do heavier per-frame I/O, e.g. `apps/wifi`'s network polling) so a toggle made inside the Settings app itself is reflected immediately without needing a restart.

### Error handling

- Missing/corrupt `/settings.json` → treated as `high_contrast: False` everywhere it's read (both the Settings app and `main.py`), never raises.
- `screen.onebit()` unavailable → silently no-ops (confirmed intentional per explicit instruction: no border-overlay fallback).

### Explicitly out of scope

- Large-text mode.
- Any change to individual apps' own rendering/colors.
- A border-overlay or other visual fallback if `onebit()` is unsupported.

## Testing plan

- `badge/apps/settings/__init__.py` tested standalone via:
  `python3 simulator/badge_simulator.py -C badge badge/apps/settings/__init__.py`
  Verifies: battery % renders (simulator mocks `get_battery_level()` → 75), toggle flips on `A`, `/settings.json` is written (simulator maps root-level file paths to a temp directory — verified by inspecting that file after a run).
- `badge/main.py` changes verified via `python3 -m py_compile` and code review only. The simulator drives a single app module directly and does not execute `main.py`'s menu/launcher flow, so the `onebit()` wiring cannot be integration-tested here — **it needs a real on-device check before being trusted**, and `onebit()` itself may not exist on this firmware at all (see Context above).
