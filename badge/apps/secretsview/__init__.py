import sys
import os

sys.path.insert(0, "/system/apps/secretsview")
os.chdir("/system/apps/secretsview")

from badgeware import io, brushes, shapes, screen, PixelFont, run

try:
    from badgeware import State
except ImportError:
    State = None

OVERRIDE_STATE = "secrets_override"

small_font = PixelFont.load("/system/assets/fonts/ark.ppf")
large_font = PixelFont.load("/system/assets/fonts/absolute.ppf")

white = brushes.color(235, 245, 255)
phosphor = brushes.color(211, 250, 55)
background = brushes.color(13, 17, 23)
gray = brushes.color(100, 110, 120)
green = brushes.color(46, 160, 67)
red = brushes.color(248, 81, 73)

# Fields worth showing, in a sensible order. Secrets like the password and
# token are masked so the screen is safe to show to anyone.
SHOW_FIELDS = [
    ("GITHUB_USERNAME", False),
    ("WIFI_SSID", False),
    ("WIFI_PASSWORD", True),
    ("GITHUB_TOKEN", True),
    ("DEV_MODE", False),
    ("EDITOR_SERVER", False),
    ("TIMEZONE_OFFSET_HOURS", False),
]

# Every place secrets.py might live, checked the same way `import secrets`
# resolves it (writable root first, then the read-only /system copy).
CANDIDATE_PATHS = ["/secrets.py", "/system/secrets.py"]

rows = None       # wrapped display rows
page = 0
ROWS_PER_PAGE = 12
LINE_H = 8


def mask(value):
    s = str(value)
    if not s:
        return "(empty)"
    if len(s) <= 2:
        return "*" * len(s)
    return s[0] + "*" * (len(s) - 2) + s[-1]


def reimport_secrets():
    """Import secrets exactly the way the other apps do, fresh each time."""
    sys.path.insert(0, "/")
    try:
        if "secrets" in sys.modules:
            del sys.modules["secrets"]
        import secrets as s
        return s
    except Exception as e:
        return e
    finally:
        try:
            sys.path.pop(0)
        except Exception:
            pass


def build_lines():
    lines = []

    s = reimport_secrets()
    lines.append("import secrets ->")
    if isinstance(s, Exception):
        lines.append("  FAILED: " + str(s))
    else:
        path = getattr(s, "__file__", "(no __file__)")
        lines.append("  file: " + str(path))
        for name, secret in SHOW_FIELDS:
            if hasattr(s, name):
                val = getattr(s, name)
                shown = mask(val) if secret else str(val)
                lines.append("  " + name + ": " + shown)

    # The State override the editor persists — this is what actually wins,
    # since secrets.py reverts on reboot. This is the value apps really use.
    lines.append("")
    lines.append("State override ->")
    if State is None:
        lines.append("  (State unavailable)")
    else:
        ov = {}
        if State.load(OVERRIDE_STATE, ov) and ov:
            for name, secret in SHOW_FIELDS:
                if name in ov:
                    shown = mask(ov[name]) if secret else str(ov[name])
                    lines.append("  " + name + ": " + shown)
        else:
            lines.append("  (none saved yet)")

    # Raw bytes on disk for each candidate, so a stale/duplicate file is
    # obvious. We only pull out the GITHUB_USERNAME line to keep it short.
    for path in CANDIDATE_PATHS:
        lines.append("")
        try:
            with open(path) as f:
                content = f.read()
            found = None
            for line in content.split("\n"):
                if line.strip().startswith("GITHUB_USERNAME"):
                    found = line.strip()
                    break
            lines.append(path + " (%d bytes)" % len(content))
            lines.append("  " + (found or "(no GITHUB_USERNAME line)"))
        except OSError as e:
            lines.append(path + ": " + str(e))

    return lines


def wrap_to_width(text, max_w):
    text = str(text)
    out = []
    line = ""
    for ch in text:
        if screen.measure_text(line + ch)[0] > max_w and line:
            out.append(line)
            line = ch
        else:
            line += ch
    if line:
        out.append(line)
    return out or [""]


def refresh():
    global rows, page
    screen.font = small_font
    rows = []
    for line in build_lines():
        rows.extend(wrap_to_width(line, 152))
    page = 0


def update():
    global page, rows

    screen.brush = background
    screen.clear()
    screen.font = small_font

    if rows is None:
        refresh()

    if io.BUTTON_A in io.pressed:
        # Re-read from disk on demand, e.g. right after an OTA push.
        refresh()

    total = len(rows)
    pages = max(1, (total + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
    page = max(0, min(page, pages - 1))

    if io.BUTTON_DOWN in io.pressed or io.BUTTON_C in io.pressed:
        page = min(page + 1, pages - 1)
    if io.BUTTON_UP in io.pressed:
        page = max(page - 1, 0)

    screen.brush = phosphor
    screen.text("SECRETS  pg %d/%d" % (page + 1, pages), 3, 2)

    screen.brush = white
    y = 12
    for row in rows[page * ROWS_PER_PAGE:(page + 1) * ROWS_PER_PAGE]:
        screen.text(row, 3, y)
        y += LINE_H

    screen.brush = gray
    screen.text("UP/DN page   A refresh", 3, 113)


run(update)
