from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_BOT_CONFIG: dict[str, Any] = {
    "device": {
        "adb_path": "C:\\LDPlayer\\LDPlayer14\\adb.exe",
        "adb_serial": "127.0.0.1:5555",
    },
    "loop": {
        "scan_interval": 0.05,
        "min_delay": 0.85,
        "max_delay": 2.0,
        "jitter": 4,
        "retry_limit": 4,
        "verify_delay": 0.6,
    },
    "recorder": {
        "input_mode": "adb",
        "jump_tap": [165, 625],
        "slide_swipe": [1115, 625, 1115, 625, 140],
        "loop_replay_enabled": False,
        "loop_trigger_mode": "template",
        "loop_trigger_step": "Click Use Booster",
        "loop_trigger_template": "templates/replay_game_start.png",
        "loop_trigger_confidence": 0.78,
        "loop_replay_file": "recordings/Record_001.json",
        "loop_replay_delay": -0.5,
        "loop_tap_trigger": False,
    },
    "sequence": [
        {"name": "Click Play!", "template": "templates/start.png", "confidence": 0.85, "wait_before": [4.0, 8.0]},
        {"name": "Reset Click (Heart)", "template": "templates/reset click2.png", "confidence": 0.85},
        {"name": "Click Buy Booster", "template": "templates/Buy Boost.png", "confidence": 0.85},
        {"name": "Click Buy", "template": "templates/new_step.png", "confidence": 0.85},
        {"name": "Click Buy Relay", "template": "templates/relay.png", "confidence": 0.85},
        {"name": "Click Buy", "template": "templates/new_step.png", "confidence": 0.85},
        {
            "name": "Click 1200",
            "template": "templates/time2.png",
            "confidence": 0.85,
            "retry_after": 3.0,
            "retry_template": "templates/start.png",
        },
        {"name": "Click Multi", "template": "templates/time2_1.png", "confidence": 0.85},
        {"name": "Click Multi-Buy", "template": "templates/time2_2.png", "confidence": 0.85},
        {"name": "Click Play!", "template": "templates/time2_3.png", "confidence": 0.85},
        {"name": "start2", "template": "templates/start2.png", "confidence": 0.85, "enabled": False},
        {"name": "Click Use Booster", "template": "templates/run1.png", "confidence": 0.85, "post_delay": [0.0, 0.15]},
        {
            "name": "Click Use Booster",
            "template": "templates/replay_game_start.png",
            "confidence": 0.78,
            "enabled": False,
        },
        {
            "name": "exit",
            "template": "templates/exit.png",
            "confidence": 0.85,
            "enabled": False,
            "wait_before": [14.5, 16.0],
        },
        {"name": "exit1", "template": "templates/exit2.png", "confidence": 0.85, "enabled": False},
        {"name": "exit2", "template": "templates/exit3.png", "confidence": 0.85, "enabled": False},
        {"name": "Click Use Relay", "template": "templates/run2.png", "confidence": 0.85},
        {"name": "Click OK", "template": "templates/end1.png", "confidence": 0.85, "wait_before": [2.0, 4.0]},
        {"name": "Click Open all", "template": "templates/end2.png", "confidence": 0.85, "timeout": 5.0},
        {"name": "Click Confirm", "template": "templates/end3.png", "confidence": 0.85, "timeout": 5.0},
    ],
    "interrupts": [
        {"name": "lvup", "template": "templates/lvup.png", "confidence": 0.85},
        {"name": "confirm", "template": "templates/confirm.png", "confidence": 0.85},
    ],
}


def default_bot_config() -> dict[str, Any]:
    return deepcopy(DEFAULT_BOT_CONFIG)
