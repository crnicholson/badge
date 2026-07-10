import sys
import os

sys.path.insert(0, "/system/apps/editor")
os.chdir("/system/apps/editor")

from badgeware import io, brushes, shapes, screen, PixelFont, run
import network
import json
from urllib.urequest import urlopen

try:
    from badgeware import State
except ImportError:
    State = None

# ---------------------------------------------------------------- chrome ---

small_font = PixelFont.load("/system/assets/fonts/ark.ppf")
large_font = PixelFont.load("/system/assets/fonts/absolute.ppf")

white = brushes.color(235, 245, 255)
phosphor = brushes.color(211, 250, 55)
background = brushes.color(13, 17, 23)
gray = brushes.color(100, 110, 120)
green = brushes.color(46, 160, 67)
red = brushes.color(248, 81, 73)
qr_dark = brushes.color(0, 0, 0)
qr_light = brushes.color(255, 255, 255)

# ------------------------------------------------------------- constants ---

# Where the badge-editor Next.js server runs. Override with EDITOR_SERVER in
# secrets.py — on real hardware set it to your computer's LAN IP, e.g.
# EDITOR_SERVER = "http://192.168.1.50:3000"
DEFAULT_SERVER = "http://127.0.0.1:3000"

# Where secrets.py is READ from, most-preferred first. Apps load it with
# `sys.path.insert(0, "/")` then `import secrets`, so this mirrors that
# resolution: the writable root first, then the /system fallback.
SECRETS_PATHS = ["/secrets.py", "/system/secrets.py"]

# Where the OTA update is WRITTEN. On hardware, /system is a read-only
# firmware partition, so we must write to the writable LittleFS root at
# "/secrets.py". Because every app does `sys.path.insert(0, "/")` before
# `import secrets`, a file at "/secrets.py" shadows a stale one in
# /system on the next boot — so the badge always picks up the edit. We
# also write /system/secrets.py as a best-effort (it succeeds in the
# simulator, where /system maps to the repo's writable badge/ dir, and
# harmlessly fails on read-only hardware).
SECRETS_WRITE_PATHS = ["/secrets.py", "/system/secrets.py"]
ID_FILE = "/badge_editor_id.txt"

WIFI_TIMEOUT = 15  # seconds
RETRY_DELAY = 5  # seconds between failed network attempts
POLL_MS = 3000

# ------------------------------------------------------------------ state ---

STATE_WIFI = "wifi"
STATE_REGISTER = "register"
STATE_QR = "qr"
STATE_WAIT = "wait"
STATE_ERROR = "error"
STATE_INFO = "info"

fs_info_lines = None   # raw lines from gather_fs_info()
fs_info_rows = None    # wrapped-to-width display rows
info_page = 0

state = STATE_WIFI
status_line = "Starting..."
error_detail = None

badge_id = None
server = DEFAULT_SERVER
wlan = None
connecting = False
connection_start = None
last_attempt = None

qr_rows = None
qr_size = 0
page_url = None
applied_version = 0
last_poll = 0
flashed = False  # an update was written; prompt for RESET
flashed_paths = None  # which files the last update was written to
effective_user = None  # GITHUB_USERNAME the badge reads after the write


# ------------------------------------------------------------ badge id -----


def get_badge_id():
    """Return a unique id that survives power cycles.

    Real hardware exposes the RP2350's factory-programmed chip id; the
    simulator has no `machine` module, so fall back to a random id
    persisted at the flash root.
    """
    try:
        import machine
        import binascii

        return binascii.hexlify(machine.unique_id()).decode()
    except ImportError:
        pass

    try:
        with open(ID_FILE) as f:
            saved = f.read().strip()
            if saved:
                return saved
    except OSError:
        pass

    import urandom

    new_id = "".join("{:02x}".format(urandom.getrandbits(8)) for _ in range(8))
    try:
        with open(ID_FILE, "w") as f:
            f.write(new_id)
    except OSError:
        pass
    return new_id


# ------------------------------------------------------------- secrets -----


def _statvfs(path):
    try:
        v = os.statvfs(path)
        # (bsize, frsize, blocks, bfree, bavail, ...) -> total/free KB
        total = v[0] * v[2] // 1024
        free = v[0] * v[3] // 1024
        return "%s %dK free/%dK" % (path, free, total)
    except Exception as e:
        return "%s statvfs err: %s" % (path, e)


def gather_fs_info():
    """Ground-truth of the badge filesystem, so we can see WHERE code can
    persist a file vs. what the USB drive exposes. Returned as short lines
    plus a dict for State.save()."""
    lines = []
    d = {}
    d["cwd"] = os.getcwd()
    lines.append("cwd: " + d["cwd"])
    for root in ("/", "/system", "/flash"):
        try:
            entries = os.listdir(root)
            d["ls " + root] = entries
            lines.append("ls %s: %s" % (root, ",".join(entries)[:60]))
        except Exception as e:
            lines.append("ls %s: %s" % (root, e))
    for root in ("/", "/system", "/flash"):
        lines.append(_statvfs(root))
        d["vfs " + root] = _statvfs(root)
    op = operative_secrets_path()
    d["secrets_path"] = op
    lines.append("secrets: " + str(op))
    # Prove where a code write actually lands: write a probe, list it back.
    probe = "/EDITOR_FS_PROBE.txt"
    try:
        with open(probe, "w") as f:
            f.write("probe")
        try:
            os.sync()
        except Exception:
            pass
        seen = probe.lstrip("/") in os.listdir("/")
        lines.append("probe %s written, in ls/: %s" % (probe, seen))
        d["probe_written"] = True
        d["probe_in_ls"] = seen
    except Exception as e:
        lines.append("probe write FAILED: " + str(e))
        d["probe_error"] = str(e)
    if State is not None:
        try:
            State.save("editor_fsinfo", d)
            lines.append("saved via State: editor_fsinfo")
        except Exception as e:
            lines.append("State.save err: " + str(e))
    print("[editor] FSINFO", d)
    return lines


def reimport_secrets():
    """Import secrets fresh (mirroring how every app loads it) and return
    the module, so we can read the values the badge ACTUALLY sees right
    now — the ground truth, not a guess about which file is live."""
    sys.path.insert(0, "/")
    try:
        if "secrets" in sys.modules:
            del sys.modules["secrets"]
        import secrets as s
        return s
    except Exception:
        return None
    finally:
        try:
            sys.path.pop(0)
        except Exception:
            pass


def operative_secrets_path():
    """The path `import secrets` actually resolves to on this device."""
    s = reimport_secrets()
    if s is not None:
        path = getattr(s, "__file__", None)
        if path:
            return path
    # Fall back to probing the candidate paths directly.
    for path in SECRETS_PATHS:
        try:
            with open(path):
                return path
        except OSError:
            continue
    return None


def read_secrets_file():
    """Return (content, path) of the live secrets.py, or (None, default)."""
    path = operative_secrets_path()
    if path:
        try:
            with open(path) as f:
                return f.read(), path
        except OSError:
            pass
    for candidate in SECRETS_PATHS:
        try:
            with open(candidate) as f:
                return f.read(), candidate
        except OSError:
            continue
    return None, SECRETS_PATHS[0]


def load_settings():
    global server
    ssid = password = None
    try:
        sys.path.insert(0, "/")
        import secrets

        ssid = getattr(secrets, "WIFI_SSID", None)
        password = getattr(secrets, "WIFI_PASSWORD", None)
        server = getattr(secrets, "EDITOR_SERVER", DEFAULT_SERVER)
        sys.path.pop(0)
    except ImportError:
        sys.path.pop(0)
    return ssid, password


# ---------------------------------------------------------------- http -----


def http_json(url, payload=None):
    """GET (or POST when payload is given) and decode a JSON response."""
    headers = {"User-Agent": "badge-editor-app"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    response = urlopen(url, data=data, headers=headers)
    try:
        chunks = []
        buf = bytearray(512)
        while True:
            n = response.readinto(buf)
            if not n:
                break
            chunks.append(bytes(buf[:n]))
    finally:
        response.close()
    return json.loads(b"".join(chunks))


# ---------------------------------------------------------------- wifi -----

WIFI_SSID, WIFI_PASSWORD = None, None


def tick_wifi():
    """Bring WiFi up; returns True once connected."""
    global wlan, connecting, connection_start, last_attempt, status_line, state, error_detail

    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)

    if wlan.isconnected():
        connecting = False
        return True

    if not WIFI_SSID or not WIFI_PASSWORD:
        state = STATE_ERROR
        error_detail = "No WiFi details in secrets.py"
        return False

    now = io.ticks
    if not connecting:
        if last_attempt and (now - last_attempt) / 1000 < RETRY_DELAY:
            status_line = "WiFi retry soon..."
            return False
        connecting = True
        connection_start = now
        status_line = "Connecting to WiFi..."
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        return False

    if (now - connection_start) / 1000 >= WIFI_TIMEOUT:
        connecting = False
        last_attempt = now
        status_line = "WiFi failed, retrying"
    return False


# ------------------------------------------------------------- protocol ----


def do_register():
    global state, status_line, applied_version, error_detail
    content, _path = read_secrets_file()
    try:
        result = http_json(
            server + "/api/register",
            {"id": badge_id, "secrets": content or ""},
        )
        applied_version = result.get("appliedVersion", result.get("version", 1))
        state = STATE_QR
        status_line = "Registered"
    except Exception as e:
        state = STATE_ERROR
        error_detail = "Can't reach server: " + str(e)


def do_fetch_qr():
    global state, status_line, qr_rows, qr_size, page_url, error_detail, last_poll
    try:
        result = http_json(server + "/api/badge/" + badge_id + "/qr")
        qr_rows = result["rows"]
        qr_size = result["size"]
        page_url = result["url"]
        state = STATE_WAIT
        status_line = "Scan to edit"
        last_poll = io.ticks
    except Exception as e:
        state = STATE_ERROR
        error_detail = "QR fetch failed: " + str(e)


def write_secrets(content):
    """Write the new secrets.py so that `import secrets` picks it up, and
    confirm by reading each target back. Targets, in order: the file the
    live import currently resolves to (so we overwrite the real one when
    it's writable), then the writable LittleFS root "/secrets.py" (which
    shadows a read-only /system copy on the next boot). Returns
    (written_paths, errors)."""
    targets = []
    op = operative_secrets_path()
    if op:
        targets.append(op)
    for path in SECRETS_WRITE_PATHS:
        if path not in targets:
            targets.append(path)

    written = []
    errors = []
    for path in targets:
        try:
            with open(path, "w") as f:
                f.write(content)
                # Push it out of the file object's buffer before close.
                try:
                    f.flush()
                except Exception:
                    pass
            # Force the filesystem to commit to flash. Without this, a
            # littlefs write can sit in RAM and be lost on reset — which
            # looks exactly like "the file never changed."
            try:
                os.sync()
            except Exception:
                pass
            # Read back from a fresh handle: a write that "succeeds" on a
            # read-only or full filesystem can still not persist, so never
            # trust it blind.
            with open(path) as f:
                if f.read() == content:
                    written.append(path)
                else:
                    errors.append(path + ": readback mismatch")
        except Exception as e:
            errors.append(path + ": " + str(e))
    return written, errors


# Settings persisted through the State API so they survive the reboot that
# wipes the RAM-backed /secrets.py. Apps read these on top of secrets.py.
OVERRIDE_FIELDS = [
    "WIFI_SSID", "WIFI_PASSWORD", "GITHUB_USERNAME", "GITHUB_TOKEN",
    "DEV_MODE", "TIMEZONE_OFFSET_HOURS", "EDITOR_SERVER", "WLED_IP",
]
# Shared name so every app loads the same override blob.
OVERRIDE_STATE = "secrets_override"


def persist_overrides(content):
    """Parse the new secrets.py text and save the scalar settings via State,
    which persists across resets (unlike the RAM /secrets.py). Returns True
    if the override was saved."""
    if State is None:
        return False
    ns = {}
    try:
        exec(content, ns)
    except Exception as e:
        print("[editor] override parse failed:", e)
        return False
    data = {}
    for name in OVERRIDE_FIELDS:
        if name in ns:
            value = ns[name]
            # Keep only JSON-serialisable scalars; skip anything exotic.
            if value is None or isinstance(value, (str, int, float, bool)):
                data[name] = value
    try:
        State.save(OVERRIDE_STATE, data)
        print("[editor] persisted override via State:", data.get("GITHUB_USERNAME"))
        return True
    except Exception as e:
        print("[editor] State.save failed:", e)
        return False


def write_status_file(version, written, errors):
    """Write a human-readable outcome next to secrets.py, visible in the
    badge's USB drive. Best-effort: dropped at both the writable root and
    /system so at least one lands wherever you browse the files."""
    body = "badge-editor last write\n"
    body += "version: %d\n" % version
    body += "written OK: %s\n" % (", ".join(written) if written else "(none!)")
    body += "errors: %s\n" % ("; ".join(errors) if errors else "(none)")
    body += "GITHUB_USERNAME now reads: %s\n" % effective_user
    for path in ("/EDITOR_LAST_WRITE.txt", "/system/EDITOR_LAST_WRITE.txt"):
        try:
            with open(path, "w") as f:
                f.write(body)
            try:
                os.sync()
            except Exception:
                pass
        except Exception:
            pass


def do_poll():
    """Ask the server whether a newer secrets.py was saved; apply it."""
    global applied_version, status_line, flashed, flashed_paths, state
    global error_detail, effective_user

    # Network step: a failure here (e.g. the dev server restarted) just
    # sends us back to re-register. Kept separate from the write step so a
    # write failure is never mistaken for a network blip.
    try:
        result = http_json(
            server + "/api/badge/" + badge_id + "/poll?have=" + str(applied_version)
        )
    except Exception as e:
        state = STATE_REGISTER
        status_line = "Reconnecting..."
        error_detail = str(e)
        return

    # Refresh the live value every poll so the wait screen always shows the
    # GITHUB_USERNAME the badge ACTUALLY imports right now — it visibly
    # flips the moment an OTA write lands, with no reset required.
    s = reimport_secrets()
    effective_user = getattr(s, "GITHUB_USERNAME", "?") if s else "(import failed)"

    if not result.get("update"):
        return

    version = result["version"]
    written, errors = write_secrets(result["content"])
    # Ground truth: re-import and read the value the badge now actually
    # sees. If this doesn't reflect the edit, the write hit the wrong file.
    s = reimport_secrets()
    effective_user = getattr(s, "GITHUB_USERNAME", "?") if s else "(import failed)"
    # Printed to the serial console — the fastest way to see, on real
    # hardware, exactly which path took the write and which refused it.
    print("[editor] v%d written=%s errors=%s reads=%s"
          % (version, written, errors, effective_user))
    # Also drop a plain-text result file that shows up in the badge's USB
    # drive, so the outcome is visible in a file browser without any serial
    # console. If this file itself never appears where you view secrets.py,
    # then the filesystem your code writes is not the one the drive shows.
    write_status_file(version, written, errors)

    # The real fix: persist the settings via State so they survive the reboot
    # that regenerates the RAM-backed /secrets.py from the read-only /system
    # copy. Apps read this override on top of secrets.py, so it sticks even
    # though the file write does not.
    override_ok = persist_overrides(result["content"])

    if not written and not override_ok:
        # Nothing landed anywhere persistent — surface the real reason.
        state = STATE_ERROR
        error_detail = "Write failed: " + ("; ".join(errors) if errors else "unknown")
        return

    applied_version = version
    flashed = True
    flashed_paths = written or ["State override"]
    status_line = "Update v%d saved!" % version
    try:
        http_json(
            server + "/api/badge/" + badge_id + "/applied",
            {"version": version},
        )
    except Exception:
        # The file is already written; a failed confirmation only means the
        # web page won't flip to "applied". Don't undo a good write for it.
        pass


# ------------------------------------------------------------- drawing -----


def center_text(text, y):
    w, _ = screen.measure_text(text)
    screen.text(text, 80 - (w // 2), y)


def draw_qr():
    """QR on the left, instructions on the right."""
    scale = max(1, (screen.height - 12) // qr_size)
    qr_px = qr_size * scale
    x0 = 8
    y0 = (screen.height - qr_px) // 2

    # Quiet zone
    screen.brush = qr_light
    screen.draw(shapes.rectangle(x0 - 4, y0 - 4, qr_px + 8, qr_px + 8))

    # Modules, drawn as horizontal runs to keep the draw-call count down
    screen.brush = qr_dark
    for r in range(qr_size):
        row = qr_rows[r]
        c = 0
        while c < qr_size:
            if row[c] == "1":
                start = c
                while c < qr_size and row[c] == "1":
                    c += 1
                screen.draw(
                    shapes.rectangle(x0 + start * scale, y0 + r * scale, (c - start) * scale, scale)
                )
            else:
                c += 1

    # Right-hand panel
    tx = x0 + qr_px + 10
    screen.font = large_font
    screen.brush = phosphor
    screen.text("SCAN", tx, 14)
    screen.text("TO EDIT", tx, 26)

    screen.font = small_font
    if flashed:
        screen.brush = green
        screen.text("Saved!", tx, 44)
        screen.brush = white
        screen.text("Press RESET", tx, 55)
        screen.text("to apply", tx, 64)
        # Ground truth read straight back through `import secrets`, plus
        # where it landed — so a wrong/read-only path is obvious on-device.
        screen.brush = gray
        if effective_user is not None:
            screen.text("reads: " + str(effective_user)[:12], tx, 78)
        if flashed_paths:
            screen.text("-> " + flashed_paths[0], tx, 88)
    else:
        screen.brush = white
        screen.text("v%d" % applied_version, tx, 44)
        screen.brush = gray
        screen.text(status_line, tx, 55)
        # Live value the badge imports right now; flips when a write lands.
        if effective_user is not None:
            screen.brush = phosphor
            screen.text("user:", tx, 70)
            screen.brush = white
            screen.text(str(effective_user)[:12], tx, 80)

    screen.brush = gray
    screen.text("B=files", tx, screen.height - 22)
    screen.text(badge_id[:10], tx, screen.height - 14)


def wrap_to_width(text, max_w):
    """Break text into pieces that each fit within max_w px, splitting mid-
    token when needed (these are comma-lists with no spaces to break on)."""
    text = str(text)
    rows = []
    line = ""
    for ch in text:
        if screen.measure_text(line + ch)[0] > max_w and line:
            rows.append(line)
            line = ch
        else:
            line += ch
    if line:
        rows.append(line)
    return rows or [""]


ROWS_PER_PAGE = 11
LINE_H = 9


def draw_info_screen():
    global state, info_page, fs_info_rows

    screen.font = small_font
    if fs_info_rows is None:
        fs_info_rows = []
        for line in (fs_info_lines or []):
            fs_info_rows.extend(wrap_to_width(line, 152))

    total = len(fs_info_rows)
    pages = max(1, (total + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
    info_page = max(0, min(info_page, pages - 1))

    screen.brush = phosphor
    screen.text("FILESYSTEM  pg %d/%d" % (info_page + 1, pages), 3, 2)

    screen.brush = white
    start = info_page * ROWS_PER_PAGE
    y = 13
    for row in fs_info_rows[start:start + ROWS_PER_PAGE]:
        screen.text(row, 3, y)
        y += LINE_H

    screen.brush = gray
    screen.text("UP/DN page  B back", 3, 112)

    if io.BUTTON_DOWN in io.pressed or io.BUTTON_C in io.pressed:
        info_page = min(info_page + 1, pages - 1)
    if io.BUTTON_UP in io.pressed or io.BUTTON_A in io.pressed:
        info_page = max(info_page - 1, 0)
    if io.BUTTON_B in io.pressed or io.BUTTON_HOME in io.pressed:
        state = STATE_WAIT


def draw_message(title, lines, color):
    screen.font = large_font
    screen.brush = color
    center_text(title, 30)
    screen.font = small_font
    screen.brush = white
    y = 55
    for line in lines:
        center_text(line, y)
        y += 11


# ---------------------------------------------------------------- main -----


def _update():
    global state, status_line, last_poll, error_detail
    global fs_info_lines, fs_info_rows, info_page

    screen.brush = background
    screen.clear()
    screen.font = small_font

    if state == STATE_INFO:
        draw_info_screen()
        return

    if state == STATE_WIFI:
        if tick_wifi():
            state = STATE_REGISTER
            status_line = "Registering..."
        draw_message("EDITOR", [status_line], phosphor)

    elif state == STATE_REGISTER:
        draw_message("EDITOR", ["Contacting server...", server], phosphor)
        do_register()

    elif state == STATE_QR:
        draw_message("EDITOR", ["Fetching QR code..."], phosphor)
        do_fetch_qr()

    elif state == STATE_WAIT:
        draw_qr()
        if io.BUTTON_B in io.pressed:
            # Show the real filesystem layout so we can see where a write
            # can actually persist vs. what the USB drive exposes.
            fs_info_lines = gather_fs_info()
            fs_info_rows = None
            info_page = 0
            state = STATE_INFO
            return
        force = io.BUTTON_A in io.pressed
        if force or (io.ticks - last_poll) >= POLL_MS:
            last_poll = io.ticks
            do_poll()

    elif state == STATE_ERROR:
        draw_message(
            "ERROR",
            [
                (error_detail or "Something went wrong")[:30],
                "",
                "Check server is running,",
                "press A to retry",
            ],
            red,
        )
        if io.BUTTON_A in io.pressed:
            state = STATE_WIFI
            error_detail = None
            status_line = "Retrying..."


def update():
    # Never let an uncaught error freeze the screen with no explanation —
    # MicroPython would print the traceback only to the serial console.
    # Show it on the LCD so a write failure is always visible on-device.
    try:
        _update()
    except Exception as e:
        text = type(e).__name__ + ": " + str(e)
        print("[editor] CRASH:", text)
        screen.brush = background
        screen.clear()
        screen.font = small_font
        screen.brush = red
        center_text("CRASHED", 8)
        screen.brush = white
        draw_message("", [text[:28], text[28:56]], white)


# ---------------------------------------------------------------- setup ----

badge_id = get_badge_id()
WIFI_SSID, WIFI_PASSWORD = load_settings()

# Seed the live value so the wait screen shows it before the first poll.
_s = reimport_secrets()
effective_user = getattr(_s, "GITHUB_USERNAME", "?") if _s else None

run(update)
