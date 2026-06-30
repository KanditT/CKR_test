import sys
import time
import random
import cv2
import keyboard

import config
import adb_client

paused = False
running = True


def toggle_pause():
    global paused
    paused = not paused
    print(f"\n[{'PAUSED' if paused else 'RESUMED'}]")


def quit_bot():
    global running
    running = False
    print("\n[STOPPING]")


_template_cache = {}


def load_template(template_path):
    if template_path not in _template_cache:
        _template_cache[template_path] = cv2.imread(template_path, cv2.IMREAD_COLOR)
    return _template_cache[template_path]


def find_template(haystack_bgr, template_path, confidence):
    template = load_template(template_path)
    if template is None:
        print(f"  ! could not load template: {template_path}")
        return None
    result = cv2.matchTemplate(haystack_bgr, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < confidence:
        return None
    h, w = template.shape[:2]
    return {"x": max_loc[0], "y": max_loc[1], "w": w, "h": h, "score": max_val}


def click_match(match, label):
    margin = min(config.CLICK_JITTER_PX, match["w"] // 2 - 1, match["h"] // 2 - 1)
    margin = max(margin, 0)
    cx = match["x"] + match["w"] // 2 + random.randint(-margin, margin)
    cy = match["y"] + match["h"] // 2 + random.randint(-margin, margin)
    adb_client.tap(cx, cy)
    print(f"  tapped '{label}' (score={match['score']:.2f}) at ({cx}, {cy})")


def human_delay(delay_range=None):
    lo, hi = delay_range if delay_range else (config.MIN_CLICK_DELAY, config.MAX_CLICK_DELAY)
    delay = random.uniform(lo, hi)
    print(f"  waiting {delay:.2f}s...")
    end = time.time() + delay
    while running and not paused and time.time() < end:
        time.sleep(0.1)


def connect_if_needed():
    if not adb_client.is_connected():
        adb_client.connect()
    return adb_client.is_connected()


def preload_templates():
    for step in config.SEQUENCE + config.INTERRUPTS:
        if "template" in step:
            load_template(step["template"])
    return len(_template_cache)


def run_loop():
    global running, paused
    running = True
    paused = False

    seq_index = 0
    step_wait_start = time.time()
    wait_target = None

    while running:
        if paused:
            time.sleep(0.2)
            continue

        try:
            frame = adb_client.screencap()
        except Exception as e:
            print(f"  ! screen capture failed: {e}")
            time.sleep(config.SCAN_INTERVAL)
            continue

        interrupted = False
        for interrupt in config.INTERRUPTS:
            match = find_template(frame, interrupt["template"], interrupt["confidence"])
            if match:
                click_match(match, interrupt["name"])
                interrupted = True
                break

        if interrupted:
            human_delay()
            continue

        step = config.SEQUENCE[seq_index]
        if not step.get("enabled", True):
            print(f"  '{step['name']}' disabled, skipping")
            seq_index = (seq_index + 1) % len(config.SEQUENCE)
            step_wait_start = time.time()
            wait_target = None
            print(f"  -> waiting for next step: {config.SEQUENCE[seq_index]['name']}")
            continue

        wait_before = step.get("wait_before")
        if wait_before:
            if wait_target is None:
                wait_target = random.uniform(*wait_before) if isinstance(wait_before, tuple) else wait_before
            if (time.time() - step_wait_start) < wait_target:
                time.sleep(config.SCAN_INTERVAL)
                continue

        match = find_template(frame, step["template"], step["confidence"])
        if match:
            click_match(match, step["name"])
            if step.get("verify_click"):
                for attempt in range(config.CLICK_RETRY_LIMIT):
                    time.sleep(config.CLICK_VERIFY_DELAY)
                    verify_frame = adb_client.screencap()
                    still_there = find_template(verify_frame, step["template"], step["confidence"])
                    if not still_there:
                        break
                    print(f"  '{step['name']}' still showing after tap (attempt {attempt + 1}), retapping")
                    click_match(still_there, step["name"])
            seq_index = (seq_index + 1) % len(config.SEQUENCE)
            step_wait_start = time.time()
            wait_target = None
            next_step = config.SEQUENCE[seq_index]["name"]
            print(f"  -> waiting for next step: {next_step}")
            human_delay(step.get("post_delay"))
        elif step.get("timeout") and (time.time() - step_wait_start) >= step["timeout"]:
            print(f"  '{step['name']}' not found within {step['timeout']}s, skipping it")
            seq_index = (seq_index + 1) % len(config.SEQUENCE)
            step_wait_start = time.time()
            wait_target = None
            next_step = config.SEQUENCE[seq_index]["name"]
            print(f"  -> waiting for next step: {next_step}")
        elif step.get("retry_after") and (time.time() - step_wait_start) >= step["retry_after"]:
            prev_step = config.SEQUENCE[(seq_index - 1) % len(config.SEQUENCE)]
            retry_template = step.get("retry_template", prev_step["template"])
            retry_match = find_template(frame, retry_template, step.get("retry_confidence", 0.85))
            if retry_match:
                print(f"  '{step['name']}' not found, retrying '{prev_step['name']}'")
                click_match(retry_match, f"{step['name']}-retry-{prev_step['name']}")
                step_wait_start = time.time()
                human_delay()
            else:
                time.sleep(config.SCAN_INTERVAL)
        else:
            time.sleep(config.SCAN_INTERVAL)

    print("Bot stopped.")


def main():
    keyboard.add_hotkey(config.PAUSE_HOTKEY, toggle_pause)
    keyboard.add_hotkey(config.QUIT_HOTKEY, quit_bot)

    print(f"Connecting to {config.ADB_SERIAL} via adb...")
    if not connect_if_needed():
        print(f"Could not connect to {config.ADB_SERIAL}.")
        print("Make sure LDPlayer is running and ADB debugging (local connection) is enabled in its settings.")
        sys.exit(1)
    print("Connected.")

    count = preload_templates()
    print(f"Preloaded {count} template(s).")
    print(f"Sequence: {' -> '.join(s['name'] for s in config.SEQUENCE)} -> (loops)")
    print(f"Interrupts watched at every step: {', '.join(i['name'] for i in config.INTERRUPTS)}")
    print(f"Controls: [{config.PAUSE_HOTKEY}] pause/resume   [{config.QUIT_HOTKEY}] quit")
    print("Starting in 3 seconds...")
    time.sleep(3)

    run_loop()


if __name__ == "__main__":
    main()
