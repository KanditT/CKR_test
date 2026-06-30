import subprocess

import cv2
import numpy as np

import config


def _adb(*args):
    return subprocess.run(
        [config.ADB_PATH, "-s", config.ADB_SERIAL, *args],
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


def is_connected():
    result = subprocess.run([config.ADB_PATH, "devices"], capture_output=True, text=True)
    return any(
        line.startswith(config.ADB_SERIAL) and "device" in line
        for line in result.stdout.splitlines()
    )


def connect():
    subprocess.run([config.ADB_PATH, "connect", config.ADB_SERIAL], capture_output=True)
