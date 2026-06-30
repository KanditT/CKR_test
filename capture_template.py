"""
Run this while the button you want is visible on screen in LDPlayer.
Captures the current game screen via adb and opens a crop window:
  - drag a box tightly around the button
  - press ENTER/SPACE to confirm, or 'c' to cancel

Usage:
  python capture_template.py start
  python capture_template.py lvup
"""

import sys
import os
import cv2

import config
import adb_client


def main():
    if len(sys.argv) != 2:
        print("Usage: python capture_template.py <name>")
        print("  <name> should match a 'name' entry in config.py SEQUENCE/INTERRUPTS")
        sys.exit(1)

    name = sys.argv[1]

    if not adb_client.is_connected():
        adb_client.connect()
    if not adb_client.is_connected():
        print(f"Could not connect to {config.ADB_SERIAL}. Is LDPlayer running with ADB debugging enabled?")
        sys.exit(1)

    frame = adb_client.screencap()

    box = cv2.selectROI("Drag a box around the button, then press ENTER", frame, showCrosshair=True)
    cv2.destroyAllWindows()

    x, y, w, h = box
    if w == 0 or h == 0:
        print("No region selected, aborting.")
        sys.exit(1)

    crop = frame[y:y + h, x:x + w]
    os.makedirs("templates", exist_ok=True)
    out_path = os.path.join("templates", f"{name}.png")
    cv2.imwrite(out_path, crop)
    print(f"Saved {out_path} ({w}x{h})")


if __name__ == "__main__":
    main()
