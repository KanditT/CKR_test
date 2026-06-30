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

SEQUENCE = [{'name': 'Click Play!', 'template': 'templates/start.png', 'confidence': 0.85, 'wait_before': (4.0, 8.0)},
 {'name': 'Reset Click (Heart)', 'template': 'templates/reset click2.png', 'confidence': 0.85},
 {'name': 'Click Buy Booster', 'template': 'templates/Buy Boost.png', 'confidence': 0.85},
 {'name': 'Click Buy', 'template': 'templates/new_step.png', 'confidence': 0.85},
 {'name': 'Click Buy Relay', 'template': 'templates/relay.png', 'confidence': 0.85},
 {'name': 'Click Buy', 'template': 'templates/new_step.png', 'confidence': 0.85},
 {'name': 'Click 1200',
  'template': 'templates/time2.png',
  'confidence': 0.85,
  'retry_after': 3.0,
  'retry_template': 'templates/start.png'},
 {'name': 'Click Multi', 'template': 'templates/time2_1.png', 'confidence': 0.85},
 {'name': 'Click Multi-Buy', 'template': 'templates/time2_2.png', 'confidence': 0.85},
 {'name': 'Click Play!', 'template': 'templates/time2_3.png', 'confidence': 0.85},
 {'name': 'start2', 'template': 'templates/start2.png', 'confidence': 0.85, 'enabled': False},
 {'name': 'run1',
  'template': 'templates/run1.png',
  'confidence': 0.85,
  'enabled': False,
  'post_delay': (0.0, 0.15)},
 {'name': 'Click Use Booster', 'template': 'templates/replay_game_start.png', 'confidence': 0.78},
 {'name': 'exit',
  'template': 'templates/exit.png',
  'confidence': 0.85,
  'enabled': False,
  'wait_before': (14.5, 16.0)},
 {'name': 'exit1', 'template': 'templates/exit2.png', 'confidence': 0.85, 'enabled': False},
 {'name': 'exit2', 'template': 'templates/exit3.png', 'confidence': 0.85, 'enabled': False},
 {'name': 'Click Use Relay', 'template': 'templates/run2.png', 'confidence': 0.85, 'enabled': False},
 {'name': 'Click OK', 'template': 'templates/end1.png', 'confidence': 0.85},
 {'name': 'Click Open all', 'template': 'templates/end2.png', 'confidence': 0.85, 'timeout': 5.0},
 {'name': 'Click Confirm', 'template': 'templates/end3.png', 'confidence': 0.85, 'timeout': 5.0}]

INTERRUPTS = [{'name': 'lvup', 'template': 'templates/lvup.png', 'confidence': 0.85},
 {'name': 'confirm', 'template': 'templates/confirm.png', 'confidence': 0.85}]

# Path to LDPlayer's adb.exe and the serial of the running instance.
ADB_PATH = 'C:\\LDPlayer\\LDPlayer14\\adb.exe'
ADB_SERIAL = '127.0.0.1:5555'

# How often to scan the screen while waiting for the next button to appear.
# Kept low so fast-appearing/disappearing buttons (like run1) aren't missed.
SCAN_INTERVAL = 0.05

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

# Recorder input points for jump/slide replay. Tune these once for your
# LDPlayer resolution, then press Save Config in the GUI.
RECORDER_INPUT_MODE = 'adb'
RECORDER_JUMP_TAP = (165, 625)
RECORDER_SLIDE_SWIPE = (1115, 625, 1115, 625, 140)
RECORDER_LOOP_REPLAY_ENABLED = True
RECORDER_LOOP_TRIGGER_MODE = 'template'
RECORDER_LOOP_TRIGGER_STEP = 'Click Use Booster'
RECORDER_LOOP_TRIGGER_TEMPLATE = 'templates/replay_game_start.png'
RECORDER_LOOP_TRIGGER_CONFIDENCE = 0.78
RECORDER_LOOP_REPLAY_FILE = 'recordings/Record_001.json'
RECORDER_LOOP_REPLAY_DELAY = -0.5
RECORDER_LOOP_TAP_TRIGGER = False

# Hotkeys (require running the terminal as admin on some systems for global hooks)
PAUSE_HOTKEY = "f8"
QUIT_HOTKEY = "f9"
