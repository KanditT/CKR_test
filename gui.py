"""
Mini control panel for the bot. Run with:
    python gui.py

Lets you flip the same config.SEQUENCE "enabled"/"retry_after" switches
you'd otherwise hand-edit in config.py, then Start/Stop the bot without
touching a terminal.
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

import config
import auto_clicker
import find_adb

bot_thread = None


def get_step(name):
    for step in config.SEQUENCE:
        if step["name"] == name:
            return step
    return None


def set_enabled(names, enabled):
    for name in names:
        step = get_step(name)
        if step is not None:
            step["enabled"] = enabled


def set_retry(name, seconds_text):
    step = get_step(name)
    if step is None:
        return
    seconds_text = seconds_text.strip()
    if seconds_text:
        step["retry_after"] = float(seconds_text)
    else:
        step.pop("retry_after", None)


def apply_settings():
    set_enabled(("time2", "time2_1", "time2_2"), boost_var.get())
    set_enabled(("run1",), bust_var.get())
    set_enabled(("run2",), second_run_var.get())
    set_enabled(("exit", "exit1", "exit2"), quick_exit_var.get())
    set_enabled(("end2", "end3"), open_box_var.get())

    if quick_exit_var.get():
        secs = float(exit_seconds_var.get())
        get_step("exit")["wait_before"] = (secs, secs + 1.5)

    set_retry("time2", boost_retry_var.get())
    set_retry("start2", start2_retry_var.get())
    set_retry("run1", run1_retry_var.get())
    set_retry("run2", run2_retry_var.get())
    set_retry("exit", exit_retry_var.get())
    set_retry("end2", openbox_retry_var.get())


def start_bot():
    global bot_thread
    if bot_thread and bot_thread.is_alive():
        return
    try:
        apply_settings()
    except ValueError:
        messagebox.showerror("Invalid input", "Retry after / Exit after fields must be numbers.")
        return

    status_var.set("Connecting...")
    root.update_idletasks()
    if not auto_clicker.connect_if_needed():
        messagebox.showerror("Connection failed", f"Could not connect to {config.ADB_SERIAL}.\nIs LDPlayer running?")
        status_var.set("Stopped")
        return

    auto_clicker.preload_templates()
    bot_thread = threading.Thread(target=auto_clicker.run_loop, daemon=True)
    bot_thread.start()
    status_var.set("Running")
    start_btn.config(state=tk.DISABLED)
    stop_btn.config(state=tk.NORMAL)


def stop_bot():
    auto_clicker.running = False
    status_var.set("Stopped")
    start_btn.config(state=tk.NORMAL)
    stop_btn.config(state=tk.DISABLED)


def locate_adb():
    path = filedialog.askopenfilename(
        title="Select LDPlayer's adb.exe",
        filetypes=[("adb.exe", "adb.exe"), ("All files", "*.*")],
    )
    if not path:
        return
    find_adb.save_adb_path(path)
    config.ADB_PATH = path
    adb_path_var.set(path)


root = tk.Tk()
root.title("Bot Control")

status_var = tk.StringVar(value="Stopped")

boost_var = tk.BooleanVar(value=get_step("time2").get("enabled", True))
bust_var = tk.BooleanVar(value=get_step("run1").get("enabled", True))
second_run_var = tk.BooleanVar(value=get_step("run2").get("enabled", False))
quick_exit_var = tk.BooleanVar(value=get_step("exit").get("enabled", False))
open_box_var = tk.BooleanVar(value=get_step("end2").get("enabled", True))

_exit_wait_before = get_step("exit").get("wait_before", (14.5, 16.0))
exit_seconds_var = tk.StringVar(value=str(_exit_wait_before[0]))

boost_retry_var = tk.StringVar(value=str(get_step("time2").get("retry_after", "")))
start2_retry_var = tk.StringVar(value=str(get_step("start2").get("retry_after", "")))
run1_retry_var = tk.StringVar(value=str(get_step("run1").get("retry_after", "")))
run2_retry_var = tk.StringVar(value=str(get_step("run2").get("retry_after", "")))
exit_retry_var = tk.StringVar(value=str(get_step("exit").get("retry_after", "")))
openbox_retry_var = tk.StringVar(value=str(get_step("end2").get("retry_after", "")))

pad = {"padx": 6, "pady": 4}

top = ttk.Frame(root)
top.grid(row=0, column=0, sticky="ew", **pad)
ttk.Label(top, textvariable=status_var, font=("", 11, "bold")).pack(side="left")
start_btn = ttk.Button(top, text="START", command=start_bot)
start_btn.pack(side="right", padx=4)
stop_btn = ttk.Button(top, text="STOP", command=stop_bot, state=tk.DISABLED)
stop_btn.pack(side="right", padx=4)

adb_path_var = tk.StringVar(value=config.ADB_PATH)
adb_row = ttk.Frame(root)
adb_row.grid(row=1, column=0, sticky="ew", **pad)
ttk.Label(adb_row, text="adb.exe:").pack(side="left")
ttk.Label(adb_row, textvariable=adb_path_var, foreground="gray").pack(side="left", padx=4)
ttk.Button(adb_row, text="Locate...", command=locate_adb).pack(side="right")

ttk.Separator(root).grid(row=2, column=0, columnspan=3, sticky="ew", pady=4)

rows = ttk.Frame(root)
rows.grid(row=3, column=0, **pad)


def add_row(r, check_var, label, retry_var):
    ttk.Checkbutton(rows, text=label, variable=check_var).grid(row=r, column=0, sticky="w", **pad)
    ttk.Label(rows, text="Retry after (s):").grid(row=r, column=3, sticky="e", **pad)
    ttk.Entry(rows, textvariable=retry_var, width=6).grid(row=r, column=4, **pad)


add_row(0, boost_var, "Buy Boost (time2 chain)", boost_retry_var)

ttk.Label(rows, text="Start2 (Play trigger, always on)").grid(row=1, column=0, sticky="w", **pad)
ttk.Label(rows, text="Retry after (s):").grid(row=1, column=3, sticky="e", **pad)
ttk.Entry(rows, textvariable=start2_retry_var, width=6).grid(row=1, column=4, **pad)

add_row(2, bust_var, "Bust (run1)", run1_retry_var)
add_row(3, second_run_var, "Second run (run2)", run2_retry_var)

ttk.Checkbutton(rows, text="Quick exit (exit)", variable=quick_exit_var).grid(row=4, column=0, sticky="w", **pad)
ttk.Label(rows, text="Exit after (s):").grid(row=4, column=1, sticky="e", **pad)
ttk.Entry(rows, textvariable=exit_seconds_var, width=6).grid(row=4, column=2, **pad)
ttk.Label(rows, text="Retry after (s):").grid(row=4, column=3, sticky="e", **pad)
ttk.Entry(rows, textvariable=exit_retry_var, width=6).grid(row=4, column=4, **pad)

add_row(5, open_box_var, "Open box (end2/end3)", openbox_retry_var)

root.mainloop()
