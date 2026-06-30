"""
SEQUENCE defines the order of steps the bot performs, looping back to the
top after the last step. The bot waits until each step's button appears
on screen before clicking it and moving to the next step.

INTERRUPTS are buttons that can pop up at any point (e.g. a level-up
dialog) and take priority over whatever step the bot is currently
waiting on. Clicking an interrupt does NOT advance the sequence -- the
bot goes right back to waiting for the current step's button afterward.

Capture each template with:
    python capture_template.py <name>
"""

SEQUENCE = [
    # verify_click: these screens can be slow to load, so a tap sometimes
    # lands while the game is still mid-transition and gets ignored. The
    # bot re-checks after tapping and retaps if the same button is still
    # showing, instead of assuming the tap worked and waiting forever.
    {"name": "start",  "template": "templates/start.png",  "confidence": 0.85, "wait_before": (2,3), "retry_after":5.0},
    # {"name": "start",  "template": "templates/start.png",  "confidence": 0.85},

    # sometimes a different popup (e.g. Leaderboard) opens instead of the
    # Boost shop -- if time2 hasn't shown up after a few seconds, check
    # whether the Play! button is still there and press it again.
    {"name": "time2", "template": "templates/time2.png", "confidence": 0.85, "retry_after": 3.0},
    {"name": "time2_1", "template": "templates/time2_1.png", "confidence": 0.85, "retry_after": 3.0},
    {"name": "time2_2", "template": "templates/time2_2.png", "confidence": 0.85, "retry_after": 3.0},
    # {"name": "time2_3", "template": "templates/time2_3.png", "confidence": 0.85},
    # the Double Coins Play! tap above resolves instantly into a Result/OK
    # popup (same OK button as end1) before the start2 screen shows up.
    # {"name": "result_ok", "template": "templates/end1.png", "confidence": 0.85},
    {"name": "start2", "template": "templates/start2.png", "confidence": 0.85, "enabled": True, "retry_template": "templates/start.png", "retry_after": 3.0},
    # run1 can appear and disappear fast -- wait almost no time after start2
    # so the bot is already watching for it the instant it shows up.
    {"name": "run1",   "template": "templates/run1.png",   "confidence": 0.85, "post_delay": (0.0, 0.15), "retry_after": 0.3},
    # alternate path: bail out of the race early instead of playing it.
    # wait_before delays the bot's first attempt to look for this step's
    # button, counted from when it started waiting for it (i.e. 14s after
    # run1 was clicked) -- separate from run1's own post_delay so toggling
    # this on/off doesn't affect run1's fast-catch timing.

    {"name": "exit",  "template": "templates/exit.png",  "confidence": 0.85, "enabled": False, "wait_before": (14.5, 16.0), "retry_after": 3.0},
    {"name": "exit1",  "template": "templates/exit2.png",  "confidence": 0.85, "enabled": False, "retry_after": 3.0},
    {"name": "exit2",  "template": "templates/exit3.png",  "confidence": 0.85, "enabled": False, "retry_after": 3.0},

    # set enabled to True to have the bot click run2 again; while False the
    # bot skips it entirely and waits for whatever comes next (you click it).

    {"name": "run2",   "template": "templates/run2.png",   "confidence": 0.85, "enabled": False, "retry_after": 1.0},

    {"name": "end1",   "template": "templates/end1.png",   "confidence": 0.85, "retry_after": 3.0},
    {"name": "end2",   "template": "templates/end2.png",   "confidence": 0.85, "timeout": 5.0, "enabled": True, "retry_after": 3.0},
    # if end3 doesn't show up within 4s, give up on it and go back to start
    {"name": "end3",   "template": "templates/end3.png",   "confidence": 0.85, "timeout": 5.0, "enabled": True, "retry_after": 3.0},
]

INTERRUPTS = [
    {"name": "lvup", "template": "templates/lvup.png", "confidence": 0.85},
    {"name": "confirm", "template": "templates/confirm.png", "confidence": 0.85},
    # in-game disconnect/network-error popup -- instead of tapping a button,
    # force-stop and relaunch the game app (see GAME_PACKAGE below).
    {"name": "reconnect", "template": "templates/reconnect.png", "confidence": 0.85, "action": "restart_app"},
    {"name": "reconnect2", "template": "templates/reconnect2.png", "confidence": 0.85, "action": "restart_app"},
]

# Android package name of the game, force-stopped and relaunched when the
# "reconnect" interrupt above is detected. Find it with:
#   adb shell dumpsys window | findstr mCurrentFocus
GAME_PACKAGE = "com.devsisters.crg"

# How long to wait after relaunching the app before resuming the sequence
# from the top (start screen). The Play! button can take a few seconds past
# the initial load to settle (popups sliding in, etc.), so this has margin
# built in -- see the restart_app test in conversation history.
APP_RELAUNCH_WAIT = 25.0

# Path to LDPlayer's adb.exe and the serial of the running instance.
# find_adb.locate() checks, in order: a per-machine override saved in
# local_settings.json (not committed -- see find_adb.save_adb_path), common
# LDPlayer install locations across drive letters, then "adb" on PATH. This
# hardcoded path is only the last-resort fallback if none of that finds it.
import find_adb
ADB_PATH = find_adb.locate(default=r"E:\LDPlayer\LDPlayer14\adb.exe")
ADB_SERIAL = "127.0.0.1:5555"

# How often to scan the screen while waiting for the next button to appear.
# Kept low so fast-appearing/disappearing buttons (like run1) aren't missed.
SCAN_INTERVAL = 0.15

# Random human-like delay AFTER each click, before the bot looks for the
# next button.
MIN_CLICK_DELAY = 0.85
MAX_CLICK_DELAY = 2.0

# Random jitter (pixels) added to the tap point within the matched
# button, so taps don't land on the exact same pixel every time.
CLICK_JITTER_PX = 4

# For steps with verify_click: how many times to retap if the same button
# is still showing after a tap, and how long to wait before each recheck.
CLICK_RETRY_LIMIT = 4
CLICK_VERIFY_DELAY = 0.6

# Hotkeys (require running the terminal as admin on some systems for global hooks)
PAUSE_HOTKEY = "f8"
QUIT_HOTKEY = "f9"
