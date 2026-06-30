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

SEQUENCE = [{'name': 'start', 'template': 'templates/start.png', 'confidence': 0.85, 'wait_before': (4.0, 5.0)},
 {'name': 'time2', 'template': 'templates/time2.png', 'confidence': 0.85},
 {'name': 'time2_1', 'template': 'templates/time2_1.png', 'confidence': 0.85},
 {'name': 'time2_2', 'template': 'templates/time2_2.png', 'confidence': 0.85},
 {'name': 'time2_3', 'template': 'templates/time2_3.png', 'confidence': 0.85},
 {'name': 'run1', 'template': 'templates/run1.png', 'confidence': 0.85, 'post_delay': (0.0, 0.15)},
 {'name': 'exit',
  'template': 'templates/exit.png',
  'confidence': 0.85,
  'enabled': False,
  'wait_before': (14.5, 16.0)},
 {'name': 'exit1', 'template': 'templates/exit2.png', 'confidence': 0.85, 'enabled': False},
 {'name': 'exit2', 'template': 'templates/exit3.png', 'confidence': 0.85, 'enabled': False},
 {'name': 'run2', 'template': 'templates/run2.png', 'confidence': 0.85},
 {'name': 'end1', 'template': 'templates/end1.png', 'confidence': 0.85},
 {'name': 'end2', 'template': 'templates/end2.png', 'confidence': 0.85, 'timeout': 5.0},
 {'name': 'end3', 'template': 'templates/end3.png', 'confidence': 0.85, 'timeout': 5.0}]

INTERRUPTS = [{'name': 'lvup', 'template': 'templates/lvup.png', 'confidence': 0.85},
 {'name': 'confirm', 'template': 'templates/confirm.png', 'confidence': 0.85}]

# Path to LDPlayer's adb.exe and the serial of the running instance.
ADB_PATH = 'C:\\LDPlayer\\LDPlayer14\\adb.exe'
ADB_SERIAL = '127.0.0.1:5555'

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
