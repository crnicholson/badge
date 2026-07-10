import sys
import os

sys.path.insert(0, "/system/apps/vault")
os.chdir("/system/apps/vault")

from badgeware import screen, PixelFont, brushes, shapes, io, run, get_battery_level

screen.font = PixelFont.load("/system/assets/fonts/ark.ppf")

background = brushes.color(13, 17, 23)
phosphor = brushes.color(211, 250, 55)
dim = brushes.color(211, 250, 55, 140)

# alternating "$ command" / output lines, typed out one at a time
LINES = [
    "$ whoami",
    "octocat",
    "$ git log -1 --oneline",
    "1337c0d found the secret menu",
    "$ cat achievements.txt",
    "[x] entered the konami code",
    "$ sudo make me a sandwich",
    "okay.",
    "$ echo $REWARD",
    "unlimited internet points",
]

LINE_INTERVAL = 700  # ms per newly revealed line
LINE_H = 8
START_Y = 18
HOLD_MS = 1200  # pause on the full script before looping

start_ticks = None


def init():
    global start_ticks
    start_ticks = io.ticks


def format_uptime(ms):
    seconds = int(ms / 1000)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def update():
    elapsed = io.ticks - start_ticks

    screen.brush = background
    screen.draw(shapes.rectangle(0, 0, 160, 120))

    screen.brush = phosphor
    screen.text("VAULT // ACCESS GRANTED", 5, 5)

    cycle_len = len(LINES) * LINE_INTERVAL + HOLD_MS
    progress = elapsed % cycle_len
    revealed = min(len(LINES), int(progress / LINE_INTERVAL) + 1)

    y = START_Y
    for i in range(revealed):
        screen.brush = phosphor if i % 2 == 0 else dim
        screen.text(LINES[i], 5, y)
        y += LINE_H

    # blinking cursor while waiting for the next line
    if revealed < len(LINES) and int(io.ticks / 300) % 2 == 0:
        screen.brush = phosphor
        screen.text("_", 5, y)

    screen.brush = dim
    screen.text(f"uptime {format_uptime(io.ticks)}", 5, 108)
    screen.text(f"batt {int(get_battery_level())}%", 115, 108)

    return None


if __name__ == "__main__":
    run(update, init=init)
