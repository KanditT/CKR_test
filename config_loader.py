import importlib.util
import json
import os
import sys


def get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = get_app_dir()
CONFIG_PATH = os.path.join(APP_DIR, "config.py")


def runtime_path(*parts):
    return os.path.join(APP_DIR, *parts)


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Missing config.py at {CONFIG_PATH}")

    spec = importlib.util.spec_from_file_location("ckr_runtime_config", CONFIG_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load config.py from {CONFIG_PATH}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reload_config():
    global config
    config = load_config()
    apply_env_overrides(config)
    return config


os.chdir(APP_DIR)
config = load_config()


def normalize_delay(value):
    if isinstance(value, list):
        return tuple(value)
    return value


def normalize_step(step):
    normalized = dict(step)
    for key in ("post_delay", "wait_before"):
        if key in normalized:
            normalized[key] = normalize_delay(normalized[key])
    return normalized


def apply_bot_config(module, bot_config):
    if not isinstance(bot_config, dict):
        return
    device = bot_config.get("device") or {}
    loop = bot_config.get("loop") or {}
    recorder = bot_config.get("recorder") or {}
    if isinstance(bot_config.get("sequence"), list):
        module.SEQUENCE = [normalize_step(step) for step in bot_config["sequence"]]
    if isinstance(bot_config.get("interrupts"), list):
        module.INTERRUPTS = [normalize_step(step) for step in bot_config["interrupts"]]
    if device.get("adb_path"):
        module.ADB_PATH = str(device["adb_path"])
    if device.get("adb_serial"):
        module.ADB_SERIAL = str(device["adb_serial"])
    mapping = {
        "scan_interval": "SCAN_INTERVAL",
        "min_delay": "MIN_CLICK_DELAY",
        "max_delay": "MAX_CLICK_DELAY",
        "jitter": "CLICK_JITTER_PX",
        "retry_limit": "CLICK_RETRY_LIMIT",
        "verify_delay": "CLICK_VERIFY_DELAY",
    }
    for key, attr in mapping.items():
        if key in loop:
            setattr(module, attr, loop[key])
    recorder_mapping = {
        "input_mode": "RECORDER_INPUT_MODE",
        "jump_tap": "RECORDER_JUMP_TAP",
        "slide_swipe": "RECORDER_SLIDE_SWIPE",
        "loop_replay_enabled": "RECORDER_LOOP_REPLAY_ENABLED",
        "loop_trigger_mode": "RECORDER_LOOP_TRIGGER_MODE",
        "loop_trigger_step": "RECORDER_LOOP_TRIGGER_STEP",
        "loop_trigger_template": "RECORDER_LOOP_TRIGGER_TEMPLATE",
        "loop_trigger_confidence": "RECORDER_LOOP_TRIGGER_CONFIDENCE",
        "loop_replay_file": "RECORDER_LOOP_REPLAY_FILE",
        "loop_replay_delay": "RECORDER_LOOP_REPLAY_DELAY",
        "loop_tap_trigger": "RECORDER_LOOP_TAP_TRIGGER",
    }
    for key, attr in recorder_mapping.items():
        if key in recorder:
            value = recorder[key]
            if key in {"jump_tap", "slide_swipe"} and isinstance(value, list):
                value = tuple(value)
            setattr(module, attr, value)


def apply_env_overrides(module):
    bot_config_path = os.getenv("CKR_BOT_CONFIG_PATH")
    bot_config_json = os.getenv("CKR_BOT_CONFIG_JSON")
    try:
        if bot_config_path and os.path.exists(bot_config_path):
            with open(bot_config_path, "r", encoding="utf-8") as file:
                apply_bot_config(module, json.load(file))
        elif bot_config_json:
            apply_bot_config(module, json.loads(bot_config_json))
    except Exception as exc:
        print(f"Could not apply bot config override: {exc}")
    adb_path = os.getenv("CKR_ADB_PATH")
    adb_serial = os.getenv("CKR_ADB_SERIAL")
    if adb_path:
        module.ADB_PATH = adb_path
    if adb_serial:
        module.ADB_SERIAL = adb_serial


apply_env_overrides(config)
