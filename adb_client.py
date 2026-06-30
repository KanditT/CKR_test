import subprocess

import cv2
import numpy as np

import config_loader


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_adb(args, **kwargs):
    config = config_loader.config
    return subprocess.run(
        [config.ADB_PATH, *args],
        creationflags=CREATE_NO_WINDOW,
        **kwargs,
    )


def _adb(*args):
    config = config_loader.config
    return _run_adb(
        ["-s", config.ADB_SERIAL, *args],
        capture_output=True,
    )


def screencap():
    result = _adb("exec-out", "screencap", "-p")
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"adb screencap failed: {result.stderr.decode(errors='ignore')}")
    arr = np.frombuffer(result.stdout, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("failed to decode screencap PNG")
    return img


def tap(x, y):
    _adb("shell", "input", "tap", str(int(x)), str(int(y)))


def swipe(x1, y1, x2, y2, duration_ms=120):
    _adb(
        "shell",
        "input",
        "swipe",
        str(int(x1)),
        str(int(y1)),
        str(int(x2)),
        str(int(y2)),
        str(int(duration_ms)),
    )


def is_connected():
    config = config_loader.config
    result = _run_adb(["devices"], capture_output=True, text=True)
    return any(
        line.startswith(config.ADB_SERIAL) and "device" in line
        for line in result.stdout.splitlines()
    )


def connect():
    config = config_loader.config
    _run_adb(["connect", config.ADB_SERIAL], capture_output=True)
