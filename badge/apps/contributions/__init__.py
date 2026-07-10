import sys
import os

sys.path.insert(0, "/system/apps/contributions")
os.chdir("/system/apps/contributions")

from badgeware import io, brushes, shapes, Image, run, PixelFont, screen, file_exists
import network
from urllib.urequest import urlopen
import gc
import json
import math

# ---------------------------------------------------------------- chrome ---

small_font = PixelFont.load("/system/assets/fonts/ark.ppf")
large_font = PixelFont.load("/system/assets/fonts/absolute.ppf")

black = brushes.color(0, 0, 0)
phosphor = brushes.color(211, 250, 55, 150)
white = brushes.color(235, 245, 255)
faded = brushes.color(235, 245, 255, 100)

# GitHub's own 5-step contribution palette (empty -> brightest)
LEVELS = [
    brushes.color(22, 27, 34),
    brushes.color(14, 68, 41),
    brushes.color(0, 109, 50),
    brushes.color(38, 166, 65),
    brushes.color(57, 211, 83),
]

# Same palette, dimmed down for the ambient background texture
LEVELS_BG = [
    brushes.color(22, 27, 34, 60),
    brushes.color(14, 68, 41, 60),
    brushes.color(0, 109, 50, 60),
    brushes.color(38, 166, 65, 55),
    brushes.color(57, 211, 83, 50),
]

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

PAGES = ["graph", "activity", "stats"]

# ------------------------------------------------------------- endpoints ---

WIFI_TIMEOUT = 60
REFRESH_INTERVAL_MS = 5 * 60 * 1000
CONTRIB_URL = "https://github.com/{user}.contribs"
EVENTS_URL = "https://api.github.com/users/{user}/events/public?per_page=8"
DETAILS_URL = "https://api.github.com/users/{user}"
AVATAR_URL = "https://wsrv.nl/?url=https://github.com/{user}.png&w=82&output=png"
# Free IP geolocation lookup: resolves the WiFi network's location to a
# UTC offset (already adjusted for that location's current DST state).
TIMEZONE_URL = "http://ip-api.com/json/?fields=offset"

CONTRIB_FILE = "/gh_contribs_data.json"
EVENTS_FILE = "/gh_contribs_events.json"
USER_FILE = "/gh_contribs_user.json"
AVATAR_FILE = "/gh_contribs_avatar.png"
TIMEZONE_FILE = "/gh_contribs_timezone.json"

APP_VERSION = ""

WIFI_PASSWORD = None
WIFI_SSID = None
GITHUB_TOKEN = None
GITHUB_USERNAME = None
# None means "not yet known" -> auto-detect from the WiFi network's
# location. Set explicitly in secrets.py to override auto-detection.
TIMEZONE_OFFSET_HOURS = None
TIMEZONE_MANUAL = False
# Why the last secrets read failed, shown on the error screen.
secrets_error = None

wlan = None
wifi_up = False
ticks_start = None


def message(text):
    print(text)


def get_connection_details(store):
    # Import the secrets module and read each value with getattr, so one
    # missing name (e.g. an on-device secrets.py without GITHUB_TOKEN or
    # TIMEZONE_OFFSET_HOURS) can never null out the values that DO exist.
    # A from-import of several names raises if ANY of them is absent.
    global WIFI_PASSWORD, WIFI_SSID, GITHUB_TOKEN, GITHUB_USERNAME
    global TIMEZONE_OFFSET_HOURS, TIMEZONE_MANUAL, secrets_error

    if WIFI_SSID is not None and store.handle is not None:
        return True

    try:
        sys.path.insert(0, "/")
        try:
            import secrets
        finally:
            try:
                sys.path.pop(0)
            except Exception:
                pass
        WIFI_SSID = getattr(secrets, "WIFI_SSID", None)
        WIFI_PASSWORD = getattr(secrets, "WIFI_PASSWORD", None)
        GITHUB_USERNAME = getattr(secrets, "GITHUB_USERNAME", None)
        GITHUB_TOKEN = getattr(secrets, "GITHUB_TOKEN", None)
        tz = getattr(secrets, "TIMEZONE_OFFSET_HOURS", None)
        if tz is not None:
            TIMEZONE_OFFSET_HOURS = tz
            TIMEZONE_MANUAL = True
    except Exception as e:
        secrets_error = "import of /secrets.py failed: " + repr(e)
        return False

    if not WIFI_SSID:
        secrets_error = "WIFI_SSID is missing or empty in /secrets.py"
        return False

    if not GITHUB_USERNAME:
        secrets_error = "GITHUB_USERNAME is missing or empty in /secrets.py"
        return False

    store.handle = GITHUB_USERNAME
    return True


def wlan_start():
    """Kick off / poll the WiFi connection. Keeps `wifi_up` equal to the
    REAL connection state every frame. Returns False only when the
    connection attempt has timed out; True means connected OR still
    trying within the timeout window (draw a 'connecting' state then)."""
    global wlan, ticks_start, wifi_up

    if ticks_start is None:
        ticks_start = io.ticks

    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        if not wlan.isconnected():
            wlan.connect(WIFI_SSID, WIFI_PASSWORD)
            print("Connecting to WiFi...")

    wifi_up = wlan.isconnected()

    if wifi_up:
        return True
    return io.ticks - ticks_start < WIFI_TIMEOUT * 1000


def async_fetch_to_disk(url, file, force_update=False, timeout_ms=25000):
    """Fetch a URL to disk as a generator, yielding between chunks."""
    if not force_update and file_exists(file):
        return

    # urlopen with no network up blocks the whole UI loop on DNS for a
    # long time — never touch the network until the connection is real.
    # tick() catches this and simply retries next frame.
    if not wifi_up:
        raise RuntimeError("waiting for wifi")

    start_ticks = io.ticks
    try:
        headers = {"User-Agent": "GitHub Universe Badge 2025"}
        if GITHUB_TOKEN and url.startswith("https://api.github.com"):
            headers["Authorization"] = f"token {GITHUB_TOKEN}"

        response = urlopen(url, headers=headers)
        data = bytearray(512)
        with open(file, "wb") as f:
            while True:
                if timeout_ms is not None and (io.ticks - start_ticks) > timeout_ms:
                    raise TimeoutError(f"Fetch timed out after {timeout_ms} ms")
                length = response.readinto(data)
                if length == 0:
                    break
                f.write(data[:length])
                yield
        del data
        del response
    except Exception as e:
        try:
            if file_exists(file):
                os.remove(file)
        except Exception:
            pass
        if isinstance(e, TimeoutError):
            raise
        raise RuntimeError(f"Fetch from {url} to {file} failed. {e}") from e


# ------------------------------------------------------------- date math ---
# Civil-date -> day-ordinal (Howard Hinnant's days_from_civil), used only to
# get a sortable/comparable day number for streak math. No time module
# dependency, so it behaves identically under MicroPython and CPython.

def days_from_civil(y, m, d):
    y -= 1 if m <= 2 else 0
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def civil_from_days(z):
    z += 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    y += 1 if m <= 2 else 0
    return y, m, d


def parse_iso_date(s):
    parts = s.split("-")
    return days_from_civil(int(parts[0]), int(parts[1]), int(parts[2]))


# ------------------------------------------------------------ parsing ---
# Shared by both the network-fetch path and the instant disk-cache-load
# path, so the two can never drift out of sync with each other.

def parse_user_payload(raw):
    r = json.loads(raw)
    login = r.get("login")
    name = r.get("name") or login
    followers = r.get("followers", 0)
    return name, followers, login


def parse_contrib_payload(raw):
    r = json.loads(raw)
    days = []
    computed_total = 0
    for week in (r.get("weeks") or []):
        try:
            base = parse_iso_date(week["first_day"])
        except Exception:
            continue
        for day in (week.get("contribution_days") or []):
            level = day.get("level", 0)
            count = day.get("count", 0)
            if not (0 <= level < len(LEVELS)):
                level = 0
            days.append((base + day.get("weekday", 0), level, count))
            computed_total += count
    days.sort(key=lambda d: d[0])
    total = r.get("total_contributions") or computed_total
    return days, total


def parse_events_payload(raw):
    r = json.loads(raw)
    events = []
    for e in r[:8]:
        repo = (e.get("repo") or {}).get("name", "?")
        payload = e.get("payload") or {}
        events.append((e.get("type", ""), repo, payload.get("ref"), e.get("created_at", "")))
    return events


def clear_cache():
    for f in (CONTRIB_FILE, EVENTS_FILE, USER_FILE, AVATAR_FILE, TIMEZONE_FILE):
        try:
            if file_exists(f):
                os.remove(f)
        except Exception:
            pass


def local_from_utc(y, m, d, hh, mm):
    """Shift a UTC civil time by TIMEZONE_OFFSET_HOURS, handling day
    rollover, and return (local_ordinal, local_hour, local_minute)."""
    ordinal = days_from_civil(y, m, d)
    total_minutes = hh * 60 + mm + int((TIMEZONE_OFFSET_HOURS or 0) * 60)
    day_shift, minute_of_day = divmod(total_minutes, 1440)
    return ordinal + day_shift, minute_of_day // 60, minute_of_day % 60


# ------------------------------------------------------------------ Store ---

class Store:
    # Fields refreshed silently in the background, in this order. Timezone
    # is deliberately excluded: it's a one-time-per-boot lookup, not
    # something that needs to be re-polled every refresh cycle.
    REFRESH_STEPS = ("_fetch_user", "_fetch_contribs", "_fetch_events", "_fetch_avatar")

    def __init__(self):
        self.handle = None
        self.bootstrapped = False  # have we attempted the instant cache-load yet
        self.refreshing = False
        self._refresh_index = 0
        self._refresh_task = None
        self.reset()

    def reset(self, force_update=False):
        """Full cold reset: null every field, so the app shows the
        blocking 'loading' screen until fresh data arrives again."""
        self.name = None
        self.followers = None
        self.total = None
        self.days = None          # [(ordinal, level, count), ...] sorted
        self.longest = None       # (length, start_ordinal, end_ordinal)
        self.current = None       # int
        self.events = None        # [(kind, repo, ref, when_iso), ...]
        self.avatar = None
        self._task = None
        self._force_update = force_update
        self.refreshing = False
        self._refresh_index = 0
        self._refresh_task = None

    def fully_loaded(self):
        return (self.name is not None and self.days is not None
                and self.events is not None and self.avatar is not None)

    def load_cached(self):
        """Instant, network-free: populate every field straight from the
        on-disk cache, IF it exists and belongs to the configured GitHub
        user. All-or-nothing — a partial/corrupt cache is treated as no
        cache at all rather than showing a half-updated mix."""
        try:
            if not (file_exists(USER_FILE) and file_exists(CONTRIB_FILE)
                    and file_exists(EVENTS_FILE) and file_exists(AVATAR_FILE)):
                return False

            name, followers, login = parse_user_payload(open(USER_FILE, "r").read())
            if login != self.handle:
                return False  # cache belongs to a different GitHub user

            days, total = parse_contrib_payload(open(CONTRIB_FILE, "r").read())
            events = parse_events_payload(open(EVENTS_FILE, "r").read())
            avatar = Image.load(AVATAR_FILE)

            self.name, self.followers = name, followers
            self.days, self.total = days, total
            self.events = events
            self.avatar = avatar
            self._compute_stats()
            gc.collect()
            return True
        except Exception as e:
            message(f"Failed to load cache: {e}")
            return False

    def begin_refresh(self):
        """Kick off a silent background refresh. Existing fields stay on
        screen untouched the whole time; each is only overwritten once
        its own fresh fetch actually succeeds."""
        self.refreshing = True
        self._refresh_index = 0
        self._refresh_task = None
        self._force_update = True

    def refresh_tick(self):
        """Advance one step of an in-progress background refresh. Safe
        to call every frame; it's a no-op once the cycle is done."""
        if self._refresh_index >= len(Store.REFRESH_STEPS):
            self.refreshing = False
            return
        step = getattr(self, Store.REFRESH_STEPS[self._refresh_index])
        if not self._refresh_task:
            self._refresh_task = step()
        try:
            next(self._refresh_task)
        except StopIteration:
            self._refresh_task = None
            self._refresh_index += 1
        except Exception as e:
            message(f"Background refresh step failed: {e}")
            self._refresh_task = None
            self._refresh_index += 1

    # -- fetch steps, chained one at a time. On failure: if this is a
    # cold start (field still None) fall back to a safe default so the
    # app can proceed; if we already have good old data, leave it alone
    # rather than overwrite it with a placeholder. ----------------------

    def _fetch_user(self):
        try:
            yield from async_fetch_to_disk(
                DETAILS_URL.format(user=self.handle), USER_FILE, self._force_update)
            name, followers, _login = parse_user_payload(open(USER_FILE, "r").read())
            self.name = name
            self.followers = followers
        except Exception as e:
            message(f"Failed to fetch user data: {e}")
            if self.name is None:
                self.name = self.handle
            if self.followers is None:
                self.followers = 0
        gc.collect()

    def _fetch_contribs(self):
        try:
            yield from async_fetch_to_disk(
                CONTRIB_URL.format(user=self.handle), CONTRIB_FILE,
                self._force_update, timeout_ms=15000)
            days, total = parse_contrib_payload(open(CONTRIB_FILE, "r").read())
            self.days = days
            self.total = total
            self._compute_stats()
        except Exception as e:
            message(f"Failed to fetch contrib data: {e}")
            if self.days is None:
                self.total = 0
                self.days = []
                self._compute_stats()
        gc.collect()

    def _fetch_timezone(self):
        global TIMEZONE_OFFSET_HOURS
        try:
            yield from async_fetch_to_disk(
                TIMEZONE_URL, TIMEZONE_FILE, self._force_update, timeout_ms=10000)
            r = json.loads(open(TIMEZONE_FILE, "r").read())
            TIMEZONE_OFFSET_HOURS = r.get("offset", 0) / 3600
            del r
        except Exception as e:
            message(f"Failed to detect timezone: {e}")
            if TIMEZONE_OFFSET_HOURS is None:
                TIMEZONE_OFFSET_HOURS = 0
        gc.collect()

    def _fetch_events(self):
        try:
            yield from async_fetch_to_disk(
                EVENTS_URL.format(user=self.handle), EVENTS_FILE, self._force_update)
            self.events = parse_events_payload(open(EVENTS_FILE, "r").read())
        except Exception as e:
            message(f"Failed to fetch events: {e}")
            if self.events is None:
                self.events = []
        gc.collect()

    def _fetch_avatar(self):
        try:
            yield from async_fetch_to_disk(
                AVATAR_URL.format(user=self.handle), AVATAR_FILE, self._force_update)
            self.avatar = Image.load(AVATAR_FILE)
        except Exception as e:
            message(f"Failed to get avatar: {e}")
            if self.avatar is None:
                self.avatar = False

    def _compute_stats(self):
        longest = (0, None, None)
        run_len, run_start = 0, None
        for ordinal, _level, count in self.days:
            if count > 0:
                run_len += 1
                run_start = run_start if run_start is not None else ordinal
                if run_len > longest[0]:
                    longest = (run_len, run_start, ordinal)
            else:
                run_len, run_start = 0, None
        self.longest = longest

        current = 0
        n = len(self.days)
        for i in range(n - 1, -1, -1):
            _, _level, count = self.days[i]
            if count > 0:
                current += 1
            elif i == n - 1:
                continue
            else:
                break
        self.current = current

    # -- cold-start driver: called once per frame until fully_loaded() ---

    def tick(self):
        """Advance the cold-start fetch sequence. Returns a short status
        label, or None once every field is populated."""
        if self.name is None:
            label = "profile"
            step = self._fetch_user
        elif self.days is None:
            label = "contributions"
            step = self._fetch_contribs
        elif TIMEZONE_OFFSET_HOURS is None:
            label = "timezone"
            step = self._fetch_timezone
        elif self.events is None:
            label = "activity"
            step = self._fetch_events
        elif self.avatar is None:
            label = "avatar"
            step = self._fetch_avatar
        else:
            return None

        if not self._task:
            self._task = step()
        try:
            next(self._task)
        except StopIteration:
            self._task = None
        except Exception as e:
            message(f"Fetch step failed: {e}")
            self._task = None
        return label


store = Store()
page = 0
last_refresh = None


# ------------------------------------------------------------------- ui ---

def center_text(text, y, font=small_font):
    screen.font = font
    w, _ = screen.measure_text(text)
    screen.text(text, 80 - (w / 2), y)


def draw_background():
    """Ambient backdrop: a dim, slowly-drifting contribution heatmap.
    Only ever called once the app is fully_loaded(), so store.days is
    always populated here."""
    size, gap = 15, 2
    unit = size + gap
    grid_width = 53 * unit
    xo = int(-math.sin(io.ticks / 45000) * ((grid_width - 160) / 2)
             + (grid_width - 160) / 2)

    for i, (_ordinal, level, _count) in enumerate(store.days):
        col, row = i // 7, i % 7
        x = col * unit - xo
        y = row * unit + 6
        if x + size < 0 or x > 160:
            continue
        screen.brush = LEVELS_BG[level]
        screen.draw(shapes.rounded_rectangle(x, y, size, size, 3))


def draw_page_dots():
    n = len(PAGES)
    spacing = 7
    start_x = 80 - ((n - 1) * spacing) / 2
    for i in range(n):
        x = start_x + i * spacing
        screen.brush = phosphor if i == page else faded
        r = 1.6 if i == page else 1.1
        screen.draw(shapes.circle(x, 117, r))


def draw_fetching(label):
    """Blocking loading screen: only shown on a true cold start (no
    usable cache), when the configured GitHub user changed, or right
    after the cache was manually cleared. Deliberately big and clear
    since the user is stuck waiting on it."""
    dots = "." * ((int(io.ticks / 400) % 3) + 1)

    if not wifi_up:
        screen.font = large_font
        screen.brush = white
        center_text("connecting", 40, large_font)
        screen.font = small_font
        screen.brush = phosphor
        center_text("to wifi" + dots, 62)
        return

    steps = (store.name, store.days, store.events, store.avatar)
    done = sum(1 for s in steps if s is not None)
    total = len(steps)

    screen.font = large_font
    screen.brush = white
    center_text("loading", 40, large_font)
    screen.font = small_font
    screen.brush = phosphor
    center_text(f"{label} ({min(done + 1, total)}/{total}){dots}", 62)


def fmt_num(n):
    # MicroPython's formatter has no thousands separator (f"{n:,}"
    # raises ValueError there), so build it by hand.
    s = str(int(n))
    out = ""
    while len(s) > 3:
        out = "," + s[-3:] + out
        s = s[:-3]
    return s + out


def draw_half_grid(days_slice, weeks, size, unit, top):
    left = (160 - weeks * unit) // 2
    for i, (_ordinal, level, _count) in enumerate(days_slice):
        col, row = i // 7, i % 7
        x = left + col * unit
        y = top + row * unit
        screen.brush = LEVELS[level]
        screen.draw(shapes.rectangle(x, y, size, size))


def draw_graph():
    # _update() only dispatches here once store.fully_loaded() is true.
    # Two ~6-month halves stacked on top of each other, so each half
    # gets roughly double the cell size of a single 53-week strip.
    size, gap = 4, 1
    unit = size + gap
    total_weeks = len(store.days) // 7
    half1_weeks = total_weeks - total_weeks // 2
    half2_weeks = total_weeks // 2
    split = half1_weeks * 7

    half1_top = 16
    half2_top = half1_top + 7 * unit + 4

    screen.font = small_font
    screen.brush = white
    total_text = fmt_num(store.total) + " contributions" if store.total else "0 contributions"
    center_text(total_text, 3)

    draw_half_grid(store.days[:split], half1_weeks, size, unit, half1_top)
    draw_half_grid(store.days[split:], half2_weeks, size, unit, half2_top)

    legend_y = half2_top + 7 * unit + 7
    legend_w = (screen.measure_text("less")[0] + 4
                + 5 * (unit + 1) + 4 + screen.measure_text("more")[0])
    lx = 80 - legend_w / 2
    screen.brush = faded
    screen.text("less", lx, legend_y)
    lx += screen.measure_text("less")[0] + 4
    for lvl in range(5):
        screen.brush = LEVELS[lvl]
        screen.draw(shapes.rectangle(lx, legend_y + 3, size, size))
        lx += unit + 1
    screen.brush = faded
    screen.text("more", lx + 3, legend_y)


EVENT_LABELS = {
    "PushEvent": "PUSH",
    "PullRequestEvent": "PR",
    "IssuesEvent": "ISSUE",
    "IssueCommentEvent": "COMMENT",
    "PullRequestReviewEvent": "REVIEW",
    "CreateEvent": "CREATE",
    "ForkEvent": "FORK",
    "WatchEvent": "STAR",
    "ReleaseEvent": "RELEASE",
}


def format_when(iso):
    # Recent (within the last 4 days): "Tue 14:32" in local time.
    # Older: local date + time, "Jul 03 14:32". "Today" is taken from
    # the contribution calendar's last day, since the badge has no
    # synced clock of its own to compare against.
    try:
        date_part, time_part = iso.split("T")
        y, m, d = (int(p) for p in date_part.split("-"))
        hh, mm = int(time_part[0:2]), int(time_part[3:5])
    except Exception:
        return ""

    ordinal, lh, lm = local_from_utc(y, m, d, hh, mm)
    today_ordinal = store.days[-1][0] if store.days else ordinal
    age = today_ordinal - ordinal

    if age <= 4:
        weekday = WEEKDAYS[(ordinal + 4) % 7]
        return f"{weekday} {lh:02d}:{lm:02d}"

    _ly, lm_, ld = civil_from_days(ordinal)
    return f"{MONTHS[lm_ - 1]} {ld:02d} {lh:02d}:{lm:02d}"


def draw_activity():
    if not store.events:
        screen.brush = white
        center_text("no recent public activity", 60)
        return

    y = 12
    screen.font = small_font
    repo_x = 34
    for kind, repo, _ref, when in store.events:
        tag = EVENT_LABELS.get(kind, "EVENT")
        screen.brush = phosphor
        screen.text(tag, 3, y)

        screen.brush = faded
        when_text = format_when(when)
        tw, _ = screen.measure_text(when_text)
        when_x = 157 - tw
        screen.text(when_text, when_x, y)

        # Measure (never guess) so the repo name can't run into the
        # time column regardless of actual font metrics.
        repo_short = repo.split("/")[-1] if "/" in repo else repo
        max_repo_w = when_x - repo_x - 4
        if screen.measure_text(repo_short)[0] > max_repo_w:
            while repo_short and screen.measure_text(repo_short + "..")[0] > max_repo_w:
                repo_short = repo_short[:-1]
            repo_short += ".."
        screen.brush = white
        screen.text(repo_short, repo_x, y)
        y += 11


AVATAR_SIZE = 82
AVATAR_X, AVATAR_Y = 11, 30
STAT_X = AVATAR_X + AVATAR_SIZE + 7


def draw_avatar_placeholder():
    screen.brush = brushes.color(30, 38, 34)
    screen.draw(shapes.rounded_rectangle(AVATAR_X, AVATAR_Y, AVATAR_SIZE, AVATAR_SIZE, 4))
    screen.brush = phosphor
    center_text("?", AVATAR_Y + AVATAR_SIZE / 2 - 6, large_font)


def draw_stat(label, value, x, y):
    # Value and label are grouped tightly (own pair), with generous
    # clearance before the next stat so blocks read as separate.
    screen.font = large_font
    screen.brush = white
    screen.text(str(value), x, y)
    screen.font = small_font
    screen.brush = phosphor
    screen.text(label, x - 1, y + 13)


def draw_stats():
    screen.font = large_font
    screen.brush = white
    center_text(store.handle, 3, large_font)

    screen.font = small_font
    screen.brush = phosphor
    name = store.name or store.handle
    if len(name) > 24:
        name = name[:22] + ".."
    center_text(name, 17)

    if store.avatar:
        try:
            screen.blit(store.avatar, AVATAR_X, AVATAR_Y)
        except Exception:
            draw_avatar_placeholder()
    else:
        draw_avatar_placeholder()

    longest_len, _longest_start, _longest_end = store.longest

    draw_stat("followers", fmt_num(store.followers) if store.followers else 0, STAT_X, 34)
    draw_stat("streak", f"{longest_len}d", STAT_X, 61)
    draw_stat("current", f"{store.current}d", STAT_X, 88)


def no_secrets_error():
    screen.font = large_font
    screen.brush = white
    center_text("Missing Details!", 8)
    screen.font = small_font
    screen.brush = brushes.color(248, 81, 73)
    y = draw_wrapped(secrets_error or "unknown secrets problem", 4, 30, 152)
    screen.brush = faded
    center_text("Fix /secrets.py on the badge,", y + 18)
    center_text("then reload this app.", y + 28)


def wrap_text(text, x, y):
    lines = text.splitlines()
    for line in lines:
        _, h = screen.measure_text(line)
        screen.text(line, x, y)
        y += h * 0.8


def connection_error():
    screen.font = large_font
    screen.brush = white
    center_text("Connection Failed!", 5)

    screen.text("1:", 10, 63)
    screen.text("2:", 10, 95)

    screen.brush = phosphor
    screen.font = small_font
    wrap_text("""Could not connect\nto the WiFi network.\n\n:-(""", 16, 20)

    wrap_text("""Edit 'secrets.py' to\nset WiFi details and\nGitHub username.""", 30, 65)

    wrap_text("""Reload to see your\nsweet sweet stats!""", 30, 96)


def _update():
    global page, last_refresh, ticks_start, TIMEZONE_OFFSET_HOURS

    screen.brush = black
    screen.draw(shapes.rectangle(0, 0, 160, 120))

    if io.BUTTON_UP in io.pressed or io.BUTTON_A in io.pressed:
        page = (page - 1) % len(PAGES)
    if io.BUTTON_DOWN in io.pressed or io.BUTTON_C in io.pressed:
        page = (page + 1) % len(PAGES)

    # A+C held: wipe the on-disk cache entirely and force a real
    # from-scratch (blocking, visible-progress) fetch.
    if io.BUTTON_A in io.held and io.BUTTON_C in io.held:
        clear_cache()
        store.reset(force_update=True)
        last_refresh = io.ticks
        ticks_start = io.ticks  # fresh wifi-timeout window for the refetch
        if not TIMEZONE_MANUAL:
            # Re-detect in case the badge moved to a new network/location.
            TIMEZONE_OFFSET_HOURS = None

    if not get_connection_details(store):
        no_secrets_error()
        return

    if not store.bootstrapped:
        store.bootstrapped = True
        if store.load_cached():
            # Old data loaded instantly from disk (no network needed) -
            # show it right away, then quietly look for anything newer.
            last_refresh = io.ticks
            store.begin_refresh()

    wlan_ok = wlan_start()

    # B: refresh now, in the background, without disturbing what's on
    # screen (same mechanism as the periodic timer below, on demand).
    if io.BUTTON_B in io.pressed and store.fully_loaded() and not store.refreshing:
        store.begin_refresh()
        last_refresh = io.ticks

    if not store.fully_loaded():
        # True cold start: no usable cache (first ever run, cache just
        # cleared, or the configured GitHub user changed since the
        # cache was written). Nothing to show yet, so block here with
        # clearly visible progress until the first data lands.
        if not wlan_ok:
            connection_error()
            return
        label = store.tick()
        draw_fetching(label or "data")
        return

    # We have something to show. Keep it fresh in the background, but a
    # slow or failed fetch must never interrupt what's already on screen.
    if wlan_ok:
        if TIMEZONE_OFFSET_HOURS is None:
            # Only reachable when data came from load_cached(), which
            # skips the cold tick() sequence entirely (and with it the
            # only other place this gets auto-detected). tick() itself
            # is a no-op past this point since name/days/events/avatar
            # are already set, so this just resolves the timezone.
            store.tick()

        if last_refresh is None:
            last_refresh = io.ticks
        elif io.ticks - last_refresh > REFRESH_INTERVAL_MS and not store.refreshing:
            store.begin_refresh()
            last_refresh = io.ticks
        if store.refreshing:
            store.refresh_tick()

    if page == 0:
        draw_background()
        draw_stats()
    elif page == 1:
        # The graph page already renders the real heatmap at full
        # detail, so the dim ambient copy would just look muddy here.
        draw_graph()
    else:
        draw_background()
        draw_activity()

    draw_page_dots()


def draw_wrapped(text, x, y, max_width, line_height=9):
    words = text.split(" ")
    line = ""
    for word in words:
        candidate = (line + " " + word).strip()
        w, _ = screen.measure_text(candidate)
        if w > max_width and line:
            screen.text(line, x, y)
            y += line_height
            line = word
        else:
            line = candidate
    if line:
        screen.text(line, x, y)
    return y


def update():
    # Any uncaught exception below would otherwise crash the whole app
    # silently (MicroPython dumps the traceback to the serial console,
    # which most people aren't watching) and leave the LCD frozen on
    # whatever was last drawn. Show the real error on-screen instead.
    try:
        _update()
    except Exception as e:
        text = f"{type(e).__name__}: {e}"
        message(f"CRASH in contributions app: {text}")
        screen.brush = black
        screen.draw(shapes.rectangle(0, 0, 160, 120))
        screen.font = small_font
        screen.brush = brushes.color(248, 81, 73)
        center_text("CRASHED", 4, small_font)
        screen.brush = white
        draw_wrapped(text, 4, 18, 152)

    # Tiny build marker so a stale deploy is obvious at a glance.
    screen.font = small_font
    screen.brush = faded
    screen.text(APP_VERSION, 2, 111)


if __name__ == "__main__":
    run(update)
