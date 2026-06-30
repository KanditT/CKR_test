"""
Finds LDPlayer's adb.exe on the current machine so config.py doesn't need a
hardcoded path that only works on the PC it was written on.

Lookup order:
    1. local_settings.json (per-machine override, not committed to git)
    2. common LDPlayer install locations, searched across drive letters
    3. "adb" on PATH
    4. the hardcoded default passed in by the caller
"""

import glob
import json
import os
import shutil

LOCAL_SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "local_settings.json")

_SEARCH_PATTERNS = [
    "{drive}:/LDPlayer/LDPlayer*/adb.exe",
    "{drive}:/Program Files/LDPlayer/LDPlayer*/adb.exe",
    "{drive}:/Program Files (x86)/LDPlayer/LDPlayer*/adb.exe",
]


def _load_override():
    if not os.path.exists(LOCAL_SETTINGS_PATH):
        return None
    try:
        with open(LOCAL_SETTINGS_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    path = data.get("adb_path")
    if path and os.path.exists(path):
        return path
    return None


def _search_common_locations():
    for drive in "CDEFGHIJ":
        for pattern in _SEARCH_PATTERNS:
            matches = glob.glob(pattern.format(drive=drive))
            if matches:
                return matches[0]
    return None


def locate(default):
    return (
        _load_override()
        or _search_common_locations()
        or shutil.which("adb")
        or default
    )


def save_adb_path(path):
    os.makedirs(os.path.dirname(LOCAL_SETTINGS_PATH), exist_ok=True)
    with open(LOCAL_SETTINGS_PATH, "w") as f:
        json.dump({"adb_path": path}, f, indent=2)
