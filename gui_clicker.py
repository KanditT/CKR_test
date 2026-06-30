import ast
import copy
import importlib
import os
import pprint
import random
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2

import adb_client
import config

CONFIG_PATH = "config.py"
PREVIEW_MAX_WIDTH = 500
PREVIEW_MAX_HEIGHT = 150
APP_BG = "#f3f5f7"
SURFACE_BG = "#ffffff"
TEXT_COLOR = "#17202a"
MUTED_COLOR = "#667085"
ACCENT_COLOR = "#0f766e"
DANGER_COLOR = "#b42318"


def step_defaults(step):
    data = {
        "enabled": True,
        "name": "",
        "template": "",
        "confidence": 0.85,
        "post_delay": None,
        "wait_before": None,
        "timeout": None,
        "verify_click": False,
        "retry_after": None,
        "retry_template": "",
        "retry_confidence": None,
    }
    data.update(step)
    return data


def parse_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_optional_float(value):
    value = str(value).strip()
    if not value:
        return None
    return float(value)


def parse_delay(value):
    value = str(value).strip()
    if not value:
        return None
    value = value.strip("()[]")
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        return (float(parts[0]), float(parts[1]))
    raise ValueError("delay must be empty, a number, or min,max")


def format_delay(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{value[0]}, {value[1]}"
    return str(value)


def safe_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def compact_step(step):
    compact = {
        "name": step.get("name", ""),
        "template": step.get("template", ""),
        "confidence": float(step.get("confidence", 0.85)),
    }
    if not step.get("enabled", True):
        compact["enabled"] = False
    if step.get("post_delay") is not None:
        compact["post_delay"] = tuple(step["post_delay"]) if isinstance(step["post_delay"], list) else step["post_delay"]
    if step.get("wait_before") is not None:
        compact["wait_before"] = tuple(step["wait_before"]) if isinstance(step["wait_before"], list) else step["wait_before"]
    if step.get("timeout") is not None:
        compact["timeout"] = float(step["timeout"])
    if step.get("verify_click"):
        compact["verify_click"] = True
    if step.get("retry_after") is not None:
        compact["retry_after"] = float(step["retry_after"])
    if step.get("retry_template"):
        compact["retry_template"] = step["retry_template"]
    if step.get("retry_confidence") is not None:
        compact["retry_confidence"] = float(step["retry_confidence"])

    known_keys = {
        "enabled",
        "name",
        "template",
        "confidence",
        "post_delay",
        "wait_before",
        "timeout",
        "verify_click",
        "retry_after",
        "retry_template",
        "retry_confidence",
    }
    for key, value in step.items():
        if key not in known_keys and value is not None:
            compact[key] = value
    return compact


def write_config_assignments(path, updates):
    with open(path, "r", encoding="utf-8") as file:
        source = file.read()

    tree = ast.parse(source)
    ranges = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in updates:
                ranges[target.id] = (node.lineno - 1, node.end_lineno)

    lines = source.splitlines(keepends=True)
    missing = []
    replacements = []
    for name, value in updates.items():
        replacement = f"{name} = {pprint.pformat(value, width=110, sort_dicts=False)}\n"
        if name not in ranges:
            missing.append(replacement)
            continue
        start, end = ranges[name]
        replacements.append((start, end, replacement))

    for start, end, replacement in sorted(replacements, reverse=True):
        lines[start:end] = [replacement]

    if missing:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.extend(["\n", *missing])

    with open(path, "w", encoding="utf-8") as file:
        file.write("".join(lines))


class TemplateMatcher:
    def __init__(self, log):
        self.cache = {}
        self.log = log

    def clear(self, template_path=None):
        if template_path is None:
            self.cache.clear()
        else:
            self.cache.pop(template_path, None)

    def load_template(self, template_path):
        if template_path not in self.cache:
            template = cv2.imread(template_path, cv2.IMREAD_COLOR)
            if template is not None:
                self.cache[template_path] = template
                return template
            return None
        return self.cache[template_path]

    def best_match(self, frame, template_path):
        template = self.load_template(template_path)
        if template is None:
            return None
        if frame.shape[0] < template.shape[0] or frame.shape[1] < template.shape[1]:
            return None

        result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        h, w = template.shape[:2]
        return {
            "x": max_loc[0],
            "y": max_loc[1],
            "w": w,
            "h": h,
            "score": float(max_val),
        }

    def find(self, frame, template_path, confidence):
        match = self.best_match(frame, template_path)
        if not match or match["score"] < confidence:
            return None
        return match


class BotApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Cookie Run Classic Runner")
        self.geometry("1240x780")
        self.minsize(1080, 680)
        self.configure(background=APP_BG)
        self.setup_styles()

        self.sequence = [step_defaults(step) for step in copy.deepcopy(config.SEQUENCE)]
        self.interrupts = [step_defaults(step) for step in copy.deepcopy(config.INTERRUPTS)]
        self.active_group = "sequence"
        self.worker = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.matcher = TemplateMatcher(self.threadsafe_log)

        self.adb_path_var = tk.StringVar(value=config.ADB_PATH)
        self.adb_serial_var = tk.StringVar(value=config.ADB_SERIAL)
        self.scan_interval_var = tk.StringVar(value=str(config.SCAN_INTERVAL))
        self.min_delay_var = tk.StringVar(value=str(config.MIN_CLICK_DELAY))
        self.max_delay_var = tk.StringVar(value=str(config.MAX_CLICK_DELAY))
        self.jitter_var = tk.StringVar(value=str(config.CLICK_JITTER_PX))
        self.retry_limit_var = tk.StringVar(value=str(config.CLICK_RETRY_LIMIT))
        self.verify_delay_var = tk.StringVar(value=str(config.CLICK_VERIFY_DELAY))

        self.selected_iid = None
        self.status_var = tk.StringVar(value="Idle")
        self.current_step_var = tk.StringVar(value="-")
        self.preview_text_var = tk.StringVar(value="No template selected")
        self.preview_image = None
        self.capture_target_map = {}

        self.edit_vars = {
            "name": tk.StringVar(),
            "template": tk.StringVar(),
            "confidence": tk.StringVar(),
            "post_delay": tk.StringVar(),
            "wait_before": tk.StringVar(),
            "timeout": tk.StringVar(),
            "retry_after": tk.StringVar(),
            "retry_template": tk.StringVar(),
            "retry_confidence": tk.StringVar(),
            "enabled": tk.BooleanVar(value=True),
            "verify_click": tk.BooleanVar(value=False),
        }

        self.create_widgets()
        self.refresh_tree()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def setup_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        default_font = ("Segoe UI", 10)
        title_font = ("Segoe UI", 16, "bold")
        label_font = ("Segoe UI", 9)
        button_font = ("Segoe UI", 10)
        style.configure(".", font=default_font, background=APP_BG, foreground=TEXT_COLOR)
        style.configure("TFrame", background=APP_BG)
        style.configure("Surface.TFrame", background=SURFACE_BG)
        style.configure("TLabel", background=APP_BG, foreground=TEXT_COLOR)
        style.configure("Title.TLabel", background=APP_BG, foreground=TEXT_COLOR, font=title_font)
        style.configure("Muted.TLabel", background=APP_BG, foreground=MUTED_COLOR, font=label_font)
        style.configure("Status.TLabel", background=APP_BG, foreground=TEXT_COLOR, font=("Segoe UI", 10, "bold"))
        style.configure("TButton", font=button_font, padding=(14, 7))
        style.configure("Accent.TButton", font=button_font, padding=(16, 8), foreground="#ffffff", background=ACCENT_COLOR)
        style.map("Accent.TButton", background=[("active", "#115e59"), ("pressed", "#134e4a")])
        style.configure("Danger.TButton", font=button_font, padding=(16, 8), foreground="#ffffff", background=DANGER_COLOR)
        style.map("Danger.TButton", background=[("active", "#912018"), ("pressed", "#7a271a")])
        style.configure("Quiet.TButton", font=button_font, padding=(12, 7))
        style.configure("TNotebook", background=APP_BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 9), font=("Segoe UI", 10))
        style.configure("TLabelframe", background=APP_BG, bordercolor="#d0d5dd")
        style.configure("TLabelframe.Label", background=APP_BG, foreground=TEXT_COLOR, font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", font=("Segoe UI", 9), rowheight=26, fieldbackground=SURFACE_BG, background=SURFACE_BG)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("TEntry", padding=(4, 4))
        style.configure("TCombobox", padding=(4, 4))

    def create_widgets(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        command_bar = ttk.Frame(self, padding=(16, 14, 16, 10))
        command_bar.grid(row=0, column=0, sticky="ew")
        command_bar.columnconfigure(1, weight=1)

        title_block = ttk.Frame(command_bar)
        title_block.grid(row=0, column=0, sticky="w")
        ttk.Label(title_block, text="Cookie Run Runner", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(title_block, text="ADB bot control, template capture, and match debug", style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 0)
        )

        status_bar = ttk.Frame(command_bar)
        status_bar.grid(row=0, column=1, sticky="ew", padx=(28, 20))
        status_bar.columnconfigure(5, weight=1)
        ttk.Label(status_bar, text="State", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(status_bar, textvariable=self.status_var, style="Status.TLabel", width=12).grid(
            row=1, column=0, sticky="w", padx=(0, 18)
        )
        ttk.Label(status_bar, text="Current", style="Muted.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(status_bar, textvariable=self.current_step_var, style="Status.TLabel", width=18).grid(
            row=1, column=1, sticky="w", padx=(0, 18)
        )
        self.match_summary_var = tk.StringVar(value="-")
        ttk.Label(status_bar, text="Match", style="Muted.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Label(status_bar, textvariable=self.match_summary_var, style="Status.TLabel").grid(row=1, column=2, sticky="w")

        controls = ttk.Frame(command_bar)
        controls.grid(row=0, column=2, sticky="e")
        ttk.Button(controls, text="Connect", command=self.connect_adb, style="Quiet.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="Start", command=self.start_loop, style="Accent.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="Pause", command=self.toggle_pause, style="Quiet.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="Stop", command=self.stop_loop, style="Danger.TButton").pack(side=tk.LEFT)

        self.main_tabs = ttk.Notebook(self)
        self.main_tabs.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))

        self.create_run_tab()
        self.create_steps_tab()
        self.create_capture_tab()
        self.create_settings_tab()

    def create_run_tab(self):
        run = ttk.Frame(self.main_tabs, padding=14)
        run.columnconfigure(0, weight=1)
        run.rowconfigure(1, weight=1)
        run.rowconfigure(2, weight=1)
        self.main_tabs.add(run, text="Run")

        actions = ttk.Frame(run)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(actions, text="Test Current Screen", command=self.test_current_screen, style="Accent.TButton").pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(actions, text="Save Config", command=self.save_config_file, style="Quiet.TButton").pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(actions, text="Reload Config", command=self.load_config_file, style="Quiet.TButton").pack(side=tk.LEFT)

        match_frame = ttk.LabelFrame(run, text="Current Screen Match", padding=(10, 8))
        match_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        match_frame.columnconfigure(0, weight=1)
        match_frame.rowconfigure(0, weight=1)
        columns = ("result", "enabled", "group", "name", "score", "threshold", "template")
        self.match_tree = ttk.Treeview(match_frame, columns=columns, show="headings")
        headings = {
            "result": "Result",
            "enabled": "On",
            "group": "Group",
            "name": "Step",
            "score": "Score",
            "threshold": "Need",
            "template": "Template",
        }
        widths = {
            "result": 70,
            "enabled": 50,
            "group": 80,
            "name": 130,
            "score": 80,
            "threshold": 80,
            "template": 360,
        }
        for column in columns:
            self.match_tree.heading(column, text=headings[column])
            self.match_tree.column(column, width=widths[column], stretch=column == "template")
        self.match_tree.grid(row=0, column=0, sticky="nsew")
        match_scroll = ttk.Scrollbar(match_frame, orient="vertical", command=self.match_tree.yview)
        match_scroll.grid(row=0, column=1, sticky="ns")
        self.match_tree.configure(yscrollcommand=match_scroll.set)

        log_frame = ttk.LabelFrame(run, text="Log", padding=(10, 8))
        log_frame.grid(row=2, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame,
            height=12,
            wrap="word",
            state="disabled",
            background=SURFACE_BG,
            foreground=TEXT_COLOR,
            relief="flat",
            font=("Consolas", 9),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def create_steps_tab(self):
        steps = ttk.Frame(self.main_tabs, padding=14)
        steps.columnconfigure(0, weight=3)
        steps.columnconfigure(1, weight=2)
        steps.rowconfigure(1, weight=1)
        self.main_tabs.add(steps, text="Steps")

        toolbar = ttk.Frame(steps)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Button(toolbar, text="Add", command=self.add_step, style="Quiet.TButton").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(toolbar, text="Delete", command=self.delete_step, style="Quiet.TButton").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(toolbar, text="Up", command=lambda: self.move_step(-1), style="Quiet.TButton").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(toolbar, text="Down", command=lambda: self.move_step(1), style="Quiet.TButton").pack(side=tk.LEFT, padx=(0, 18))
        ttk.Button(toolbar, text="Capture", command=self.capture_selected, style="Quiet.TButton").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(toolbar, text="Test", command=self.test_selected, style="Quiet.TButton").pack(side=tk.LEFT, padx=(0, 18))
        ttk.Button(toolbar, text="Apply", command=self.apply_edit, style="Accent.TButton").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(toolbar, text="Save Config", command=self.save_config_file, style="Quiet.TButton").pack(side=tk.LEFT)

        self.tabs = ttk.Notebook(steps)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        self.tabs.bind("<<NotebookTabChanged>>", self.on_tab_changed)

        self.sequence_tree = self.create_tree(self.tabs)
        self.interrupt_tree = self.create_tree(self.tabs)
        self.tabs.add(self.sequence_tree.master, text="Sequence")
        self.tabs.add(self.interrupt_tree.master, text="Interrupts")

        editor = ttk.Frame(steps)
        editor.grid(row=1, column=1, sticky="nsew")
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(2, weight=1)

        basic = ttk.LabelFrame(editor, text="Step", padding=(10, 8))
        basic.grid(row=0, column=0, sticky="ew")
        basic.columnconfigure(1, weight=1)
        self.add_edit_row(basic, "Name", "name", 0)
        self.add_edit_row(basic, "Template", "template", 1, browse=True)
        self.add_edit_row(basic, "Confidence", "confidence", 2)
        ttk.Checkbutton(basic, text="Enabled", variable=self.edit_vars["enabled"]).grid(row=3, column=0, sticky="w")
        ttk.Checkbutton(basic, text="Verify click", variable=self.edit_vars["verify_click"]).grid(row=3, column=1, sticky="w")

        preview = ttk.LabelFrame(editor, text="Template Preview", padding=(10, 8))
        preview.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        preview.columnconfigure(0, weight=1)
        self.preview_canvas = tk.Canvas(
            preview,
            height=PREVIEW_MAX_HEIGHT,
            background=SURFACE_BG,
            highlightthickness=1,
            highlightbackground="#c8c8c8",
        )
        self.preview_canvas.grid(row=0, column=0, sticky="ew")
        ttk.Label(preview, textvariable=self.preview_text_var).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.preview_canvas.bind("<Configure>", lambda _event: self.update_template_preview())

        advanced = ttk.LabelFrame(editor, text="Advanced", padding=(10, 8))
        advanced.grid(row=2, column=0, sticky="new")
        advanced.columnconfigure(1, weight=1)
        self.add_edit_row(advanced, "Post delay", "post_delay", 0)
        self.add_edit_row(advanced, "Wait before", "wait_before", 1)
        self.add_edit_row(advanced, "Timeout", "timeout", 2)
        self.add_edit_row(advanced, "Retry after", "retry_after", 3)
        self.add_edit_row(advanced, "Retry template", "retry_template", 4, browse=True)
        self.add_edit_row(advanced, "Retry conf", "retry_confidence", 5)

    def create_capture_tab(self):
        capture = ttk.Frame(self.main_tabs, padding=14)
        capture.columnconfigure(0, weight=1)
        capture.rowconfigure(1, weight=1)
        self.main_tabs.add(capture, text="Capture")

        picker = ttk.LabelFrame(capture, text="Capture Workflow", padding=(12, 10))
        picker.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        picker.columnconfigure(1, weight=1)
        self.capture_target_var = tk.StringVar()
        ttk.Label(picker, text="Step").grid(row=0, column=0, sticky="w")
        self.capture_step_combo = ttk.Combobox(picker, textvariable=self.capture_target_var, state="readonly")
        self.capture_step_combo.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        self.capture_step_combo.bind("<<ComboboxSelected>>", lambda _event: self.select_capture_target())
        ttk.Button(picker, text="Capture Template", command=self.capture_selected_from_picker, style="Accent.TButton").grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(picker, text="Test Match", command=self.test_selected_from_picker, style="Quiet.TButton").grid(
            row=0, column=3, padx=(0, 8)
        )
        ttk.Button(picker, text="Save", command=self.save_config_file, style="Quiet.TButton").grid(row=0, column=4)

        capture_body = ttk.Frame(capture)
        capture_body.grid(row=1, column=0, sticky="nsew")
        capture_body.columnconfigure(0, weight=1)
        capture_body.rowconfigure(0, weight=1)
        self.capture_log = tk.Text(
            capture_body,
            height=10,
            wrap="word",
            state="disabled",
            background=SURFACE_BG,
            foreground=TEXT_COLOR,
            relief="flat",
            font=("Consolas", 9),
        )
        self.capture_log.grid(row=0, column=0, sticky="nsew")
        capture_scroll = ttk.Scrollbar(capture_body, orient="vertical", command=self.capture_log.yview)
        capture_scroll.grid(row=0, column=1, sticky="ns")
        self.capture_log.configure(yscrollcommand=capture_scroll.set)

    def create_settings_tab(self):
        settings = ttk.Frame(self.main_tabs, padding=14)
        settings.columnconfigure(1, weight=1)
        self.main_tabs.add(settings, text="Settings")

        ttk.Label(settings, text="ADB path").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(settings, textvariable=self.adb_path_var).grid(row=0, column=1, sticky="ew", pady=3, padx=(8, 0))
        ttk.Label(settings, text="Serial").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(settings, textvariable=self.adb_serial_var).grid(row=1, column=1, sticky="ew", pady=3, padx=(8, 0))
        ttk.Button(settings, text="Connect", command=self.connect_adb, style="Accent.TButton").grid(
            row=2, column=1, sticky="w", pady=(6, 14), padx=(8, 0)
        )

        loop = ttk.LabelFrame(settings, text="Loop Settings", padding=(10, 8))
        loop.grid(row=3, column=0, columnspan=2, sticky="ew")
        for col in range(4):
            loop.columnconfigure(col, weight=1)
        self.add_setting(loop, "Scan", self.scan_interval_var, 0)
        self.add_setting(loop, "Delay min", self.min_delay_var, 2)
        self.add_setting(loop, "Delay max", self.max_delay_var, 4)
        self.add_setting(loop, "Jitter px", self.jitter_var, 6)
        self.add_setting(loop, "Retry", self.retry_limit_var, 8)
        self.add_setting(loop, "Verify delay", self.verify_delay_var, 10)

        config_buttons = ttk.Frame(settings)
        config_buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(config_buttons, text="Reload Config", command=self.load_config_file, style="Quiet.TButton").pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(config_buttons, text="Save Config", command=self.save_config_file, style="Accent.TButton").pack(side=tk.LEFT)

    def add_setting(self, parent, label, variable, column):
        ttk.Label(parent, text=label).grid(row=0, column=column, sticky="w")
        ttk.Entry(parent, textvariable=variable, width=8).grid(row=0, column=column + 1, sticky="w", padx=(4, 10))

    def add_edit_row(self, parent, label, key, row, browse=False):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        if browse:
            frame = ttk.Frame(parent)
            frame.columnconfigure(0, weight=1)
            frame.grid(row=row, column=1, sticky="ew", pady=2)
            ttk.Entry(frame, textvariable=self.edit_vars[key]).grid(row=0, column=0, sticky="ew")
            ttk.Button(frame, text="...", width=3, command=lambda field=key: self.browse_template(field)).grid(
                row=0, column=1, padx=(4, 0)
            )
        else:
            ttk.Entry(parent, textvariable=self.edit_vars[key]).grid(row=row, column=1, sticky="ew", pady=2)

    def create_tree(self, parent):
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        columns = (
            "enabled",
            "name",
            "confidence",
            "template",
            "retry_after",
        )
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "enabled": "On",
            "name": "Name",
            "confidence": "Conf",
            "template": "Template",
            "retry_after": "Retry",
        }
        widths = {
            "enabled": 44,
            "name": 150,
            "confidence": 70,
            "template": 360,
            "retry_after": 70,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], minwidth=40, stretch=column == "template")
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)
        tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        tree.bind("<Double-1>", self.toggle_selected_enabled)
        return tree

    def active_tree(self):
        return self.sequence_tree if self.active_group == "sequence" else self.interrupt_tree

    def active_steps(self):
        return self.sequence if self.active_group == "sequence" else self.interrupts

    def refresh_tree(self):
        self.refresh_one_tree(self.sequence_tree, "sequence", self.sequence)
        self.refresh_one_tree(self.interrupt_tree, "interrupts", self.interrupts)
        self.refresh_capture_targets()

    def refresh_one_tree(self, tree, group, steps):
        selected = tree.selection()
        previous = selected[0] if selected else None
        tree.delete(*tree.get_children())
        for index, step in enumerate(steps):
            iid = f"{group}:{index}"
            tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    "yes" if step.get("enabled", True) else "no",
                    step.get("name", ""),
                    step.get("confidence", ""),
                    step.get("template", ""),
                    "" if step.get("retry_after") is None else step.get("retry_after"),
                ),
            )
        if previous and tree.exists(previous):
            tree.selection_set(previous)

    def refresh_capture_targets(self):
        if not hasattr(self, "capture_step_combo"):
            return
        values = []
        self.capture_target_map = {}
        for index, step in enumerate(self.sequence):
            label = f"{index + 1:02d}. {step.get('name', '')}  [Sequence]"
            values.append(label)
            self.capture_target_map[label] = ("sequence", index)
        for index, step in enumerate(self.interrupts):
            label = f"{index + 1:02d}. {step.get('name', '')}  [Interrupt]"
            values.append(label)
            self.capture_target_map[label] = ("interrupts", index)
        current = self.capture_target_var.get()
        self.capture_step_combo.configure(values=values)
        if current in values:
            self.capture_target_var.set(current)
        elif values:
            self.capture_target_var.set(values[0])
        else:
            self.capture_target_var.set("")

    def select_capture_target(self):
        value = self.capture_target_var.get()
        if not value:
            return False
        target = self.capture_target_map.get(value)
        if target is None:
            return False
        group, index = target

        if group == "sequence":
            steps = self.sequence
            tree = self.sequence_tree
            self.tabs.select(self.sequence_tree.master)
        elif group == "interrupts":
            steps = self.interrupts
            tree = self.interrupt_tree
            self.tabs.select(self.interrupt_tree.master)
        else:
            return False
        if not 0 <= index < len(steps):
            return False

        self.active_group = group
        iid = f"{group}:{index}"
        tree.selection_set(iid)
        tree.see(iid)
        self.load_editor(steps[index])
        return True

    def on_tab_changed(self, _event=None):
        index = self.tabs.index(self.tabs.select())
        self.active_group = "sequence" if index == 0 else "interrupts"
        self.selected_iid = None
        self.clear_editor()

    def on_tree_select(self, event=None):
        tree = event.widget if event else self.active_tree()
        selection = tree.selection()
        if not selection:
            return
        iid = selection[0]
        group, raw_index = iid.split(":", 1)
        self.active_group = "sequence" if group == "sequence" else "interrupts"
        self.selected_iid = iid
        steps = self.sequence if self.active_group == "sequence" else self.interrupts
        index = int(raw_index)
        if 0 <= index < len(steps):
            self.load_editor(steps[index])

    def clear_editor(self):
        for key, var in self.edit_vars.items():
            if isinstance(var, tk.BooleanVar):
                var.set(False)
            else:
                var.set("")
        self.update_template_preview("")

    def load_editor(self, step):
        self.edit_vars["name"].set(step.get("name", ""))
        self.edit_vars["template"].set(step.get("template", ""))
        self.edit_vars["confidence"].set(str(step.get("confidence", "")))
        self.edit_vars["post_delay"].set(format_delay(step.get("post_delay")))
        self.edit_vars["wait_before"].set(format_delay(step.get("wait_before")))
        self.edit_vars["timeout"].set("" if step.get("timeout") is None else str(step.get("timeout")))
        self.edit_vars["retry_after"].set("" if step.get("retry_after") is None else str(step.get("retry_after")))
        self.edit_vars["retry_template"].set(step.get("retry_template", ""))
        self.edit_vars["retry_confidence"].set(
            "" if step.get("retry_confidence") is None else str(step.get("retry_confidence"))
        )
        self.edit_vars["enabled"].set(bool(step.get("enabled", True)))
        self.edit_vars["verify_click"].set(bool(step.get("verify_click", False)))
        self.update_template_preview(step.get("template", ""))

    def make_preview_image(self, image_bgr):
        h, w = image_bgr.shape[:2]
        scale = min(PREVIEW_MAX_WIDTH / w, PREVIEW_MAX_HEIGHT / h, 1.0)
        if scale < 1.0:
            resized = cv2.resize(
                image_bgr,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        else:
            resized = image_bgr
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        ph, pw = rgb.shape[:2]
        ppm = f"P6\n{pw} {ph}\n255\n".encode("ascii") + rgb.tobytes()
        return tk.PhotoImage(data=ppm, format="PPM")

    def draw_preview_message(self, message):
        if not hasattr(self, "preview_canvas"):
            return
        self.preview_canvas.delete("all")
        self.preview_image = None
        width = max(self.preview_canvas.winfo_width(), PREVIEW_MAX_WIDTH)
        self.preview_canvas.create_text(
            width // 2,
            PREVIEW_MAX_HEIGHT // 2,
            text=message,
            fill="#555555",
            anchor="center",
        )
        self.preview_text_var.set(message)

    def update_template_preview(self, template_path=None):
        if not hasattr(self, "preview_canvas"):
            return
        if template_path is None:
            template_path = self.edit_vars["template"].get().strip()
        template_path = str(template_path or "").strip()
        if not template_path:
            self.draw_preview_message("No template selected")
            return

        image = cv2.imread(template_path, cv2.IMREAD_COLOR)
        if image is None:
            self.draw_preview_message(f"Cannot load: {template_path}")
            return

        self.preview_canvas.delete("all")
        self.preview_image = self.make_preview_image(image)
        width = max(self.preview_canvas.winfo_width(), PREVIEW_MAX_WIDTH)
        self.preview_canvas.create_image(
            width // 2,
            PREVIEW_MAX_HEIGHT // 2,
            image=self.preview_image,
            anchor="center",
        )
        h, w = image.shape[:2]
        self.preview_text_var.set(f"{template_path} ({w}x{h})")

    def selected_step(self):
        tree = self.active_tree()
        selection = tree.selection()
        if not selection:
            return None, None
        group, raw_index = selection[0].split(":", 1)
        steps = self.sequence if group == "sequence" else self.interrupts
        index = int(raw_index)
        if 0 <= index < len(steps):
            self.active_group = group
            return steps[index], index
        return None, None

    def apply_edit(self):
        step, _index = self.selected_step()
        if step is None:
            messagebox.showinfo("No selection", "Select a step first.")
            return False
        try:
            step["name"] = self.edit_vars["name"].get().strip()
            step["template"] = self.edit_vars["template"].get().strip()
            step["confidence"] = float(self.edit_vars["confidence"].get())
            step["post_delay"] = parse_delay(self.edit_vars["post_delay"].get())
            step["wait_before"] = parse_delay(self.edit_vars["wait_before"].get())
            step["timeout"] = parse_optional_float(self.edit_vars["timeout"].get())
            step["retry_after"] = parse_optional_float(self.edit_vars["retry_after"].get())
            step["retry_template"] = self.edit_vars["retry_template"].get().strip()
            step["retry_confidence"] = parse_optional_float(self.edit_vars["retry_confidence"].get())
            step["enabled"] = bool(self.edit_vars["enabled"].get())
            step["verify_click"] = bool(self.edit_vars["verify_click"].get())
        except ValueError as exc:
            messagebox.showerror("Invalid value", str(exc))
            return False
        self.refresh_tree()
        self.update_template_preview(step.get("template", ""))
        self.log(f"Applied step: {step['name']}")
        return True

    def add_step(self):
        steps = self.active_steps()
        steps.append(step_defaults({"name": "new_step", "template": "templates/new_step.png"}))
        self.refresh_tree()
        tree = self.active_tree()
        iid = f"{self.active_group}:{len(steps) - 1}"
        tree.selection_set(iid)
        tree.see(iid)
        self.on_tree_select()

    def delete_step(self):
        step, index = self.selected_step()
        if step is None:
            return
        steps = self.active_steps()
        del steps[index]
        self.refresh_tree()
        self.clear_editor()

    def move_step(self, delta):
        step, index = self.selected_step()
        if step is None:
            return
        steps = self.active_steps()
        new_index = index + delta
        if new_index < 0 or new_index >= len(steps):
            return
        steps[index], steps[new_index] = steps[new_index], steps[index]
        self.refresh_tree()
        iid = f"{self.active_group}:{new_index}"
        tree = self.active_tree()
        tree.selection_set(iid)
        tree.see(iid)

    def toggle_selected_enabled(self, _event=None):
        step, _index = self.selected_step()
        if step is None:
            return
        step["enabled"] = not step.get("enabled", True)
        self.load_editor(step)
        self.refresh_tree()

    def browse_template(self, field="template"):
        path = filedialog.askopenfilename(
            title="Select template",
            filetypes=(("PNG images", "*.png"), ("All files", "*.*")),
            initialdir=os.path.abspath("templates"),
        )
        if path:
            try:
                path = os.path.relpath(path, os.getcwd())
            except ValueError:
                pass
            self.edit_vars[field].set(path)
            if field == "template":
                self.update_template_preview(path)

    def connect_adb(self):
        self.apply_adb_config()
        self.status_var.set("Connecting")
        self.log(f"Connecting to {config.ADB_SERIAL}...")
        try:
            if not adb_client.is_connected():
                adb_client.connect()
            if adb_client.is_connected():
                self.status_var.set("Connected")
                self.log("Connected.")
            else:
                self.status_var.set("Not connected")
                self.log("Could not connect. Check LDPlayer and ADB debugging.")
        except Exception as exc:
            self.status_var.set("Connect failed")
            self.log(f"Connect failed: {exc}")

    def apply_adb_config(self):
        config.ADB_PATH = self.adb_path_var.get().strip()
        config.ADB_SERIAL = self.adb_serial_var.get().strip()

    def capture_selected(self):
        step, _index = self.selected_step()
        if step is None:
            messagebox.showinfo("No selection", "Select a step first.")
            return
        name = self.edit_vars["name"].get().strip() or step.get("name", "template")
        if not name:
            messagebox.showerror("Missing name", "Step name is required.")
            return
        self.apply_adb_config()
        try:
            self.ensure_connected()
            frame = adb_client.screencap()
        except Exception as exc:
            messagebox.showerror("Capture failed", str(exc))
            return

        self.log("Drag a box around the button in the OpenCV window, then press Enter.")
        box = cv2.selectROI("Capture template: press Enter to save, C to cancel", frame, showCrosshair=True)
        cv2.destroyAllWindows()
        x, y, w, h = [int(value) for value in box]
        if w == 0 or h == 0:
            self.log("Capture cancelled.")
            return

        os.makedirs("templates", exist_ok=True)
        out_path = os.path.join("templates", f"{name}.png")
        crop = frame[y : y + h, x : x + w]
        cv2.imwrite(out_path, crop)
        out_path = out_path.replace("\\", "/")
        step["name"] = name
        step["template"] = out_path
        step["confidence"] = safe_float(self.edit_vars["confidence"].get(), step.get("confidence", 0.85))
        self.matcher.clear(out_path)
        self.load_editor(step)
        self.refresh_tree()
        self.update_template_preview(out_path)
        self.log(f"Saved template {out_path} ({w}x{h}).")
        self.capture_log_message(f"Saved {out_path} ({w}x{h})")

    def test_selected(self):
        step, _index = self.selected_step()
        if step is None:
            messagebox.showinfo("No selection", "Select a step first.")
            return
        if not self.apply_edit():
            return
        self.apply_adb_config()
        try:
            self.ensure_connected()
            frame = adb_client.screencap()
        except Exception as exc:
            messagebox.showerror("Test failed", str(exc))
            return
        match = self.matcher.best_match(frame, step.get("template", ""))
        if not match:
            self.log(f"Test {step.get('name')}: no readable template.")
            return
        threshold = float(step.get("confidence", 0.85))
        state = "PASS" if match["score"] >= threshold else "LOW"
        self.log(
            f"Test {step.get('name')}: {state} score={match['score']:.3f} "
            f"threshold={threshold:.3f} loc=({match['x']}, {match['y']})"
        )
        self.capture_log_message(
            f"{state} {step.get('name')} score={match['score']:.3f} need={threshold:.3f}"
        )

    def capture_selected_from_picker(self):
        if self.select_capture_target():
            self.capture_selected()

    def test_selected_from_picker(self):
        if self.select_capture_target():
            self.test_selected()

    def test_current_screen(self):
        self.apply_adb_config()
        try:
            self.ensure_connected()
            frame = adb_client.screencap()
        except Exception as exc:
            messagebox.showerror("Test failed", str(exc))
            return

        rows = []
        for group, steps in (("sequence", self.sequence), ("interrupts", self.interrupts)):
            for step in steps:
                template_path = step.get("template", "")
                threshold = float(step.get("confidence", 0.85))
                match = self.matcher.best_match(frame, template_path)
                score = match["score"] if match else 0.0
                result = "PASS" if score >= threshold else "LOW"
                enabled = bool(step.get("enabled", True))
                rows.append((score, enabled, result, group, step.get("name", ""), threshold, template_path))

        rows.sort(key=lambda row: (row[1], row[0]), reverse=True)
        self.match_tree.delete(*self.match_tree.get_children())
        for score, enabled, result, group, name, threshold, template_path in rows:
            self.match_tree.insert(
                "",
                "end",
                values=(result, "yes" if enabled else "no", group, name, f"{score:.3f}", f"{threshold:.3f}", template_path),
            )

        if rows:
            top = rows[0]
            self.match_summary_var.set(f"Best: {top[4]} {top[0]:.3f}/{top[5]:.3f}")
            self.log(f"Current screen best enabled match: {top[4]} score={top[0]:.3f}")

    def capture_log_message(self, message):
        if not hasattr(self, "capture_log"):
            return
        timestamp = time.strftime("%H:%M:%S")
        self.capture_log.configure(state="normal")
        self.capture_log.insert("end", f"[{timestamp}] {message}\n")
        self.capture_log.see("end")
        self.capture_log.configure(state="disabled")

    def ensure_connected(self):
        if not adb_client.is_connected():
            adb_client.connect()
        if not adb_client.is_connected():
            raise RuntimeError(f"Could not connect to {config.ADB_SERIAL}")

    def settings_snapshot(self):
        return {
            "scan_interval": safe_float(self.scan_interval_var.get(), config.SCAN_INTERVAL),
            "min_delay": safe_float(self.min_delay_var.get(), config.MIN_CLICK_DELAY),
            "max_delay": safe_float(self.max_delay_var.get(), config.MAX_CLICK_DELAY),
            "jitter": safe_int(self.jitter_var.get(), config.CLICK_JITTER_PX),
            "retry_limit": safe_int(self.retry_limit_var.get(), config.CLICK_RETRY_LIMIT),
            "verify_delay": safe_float(self.verify_delay_var.get(), config.CLICK_VERIFY_DELAY),
        }

    def start_loop(self):
        if self.worker and self.worker.is_alive():
            self.log("Loop is already running.")
            return
        self.apply_adb_config()
        sequence = copy.deepcopy(self.sequence)
        interrupts = copy.deepcopy(self.interrupts)
        if not sequence:
            messagebox.showerror("No sequence", "Add at least one sequence step.")
            return
        self.stop_event.clear()
        self.pause_event.clear()
        self.matcher.clear()
        self.status_var.set("Running")
        self.worker = threading.Thread(
            target=self.loop_worker,
            args=(sequence, interrupts, self.settings_snapshot()),
            daemon=True,
        )
        self.worker.start()

    def stop_loop(self):
        self.stop_event.set()
        self.pause_event.clear()
        self.status_var.set("Stopping")
        self.log("Stopping loop...")

    def toggle_pause(self):
        if not self.worker or not self.worker.is_alive():
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.status_var.set("Running")
            self.log("Resumed.")
        else:
            self.pause_event.set()
            self.status_var.set("Paused")
            self.log("Paused.")

    def loop_worker(self, sequence, interrupts, settings):
        try:
            self.threadsafe_log(f"Connecting to {config.ADB_SERIAL} via adb...")
            self.ensure_connected()
            self.threadsafe_log("Loop started.")
            seq_index = 0
            step_wait_start = time.monotonic()
            wait_target = None

            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    time.sleep(0.2)
                    continue

                try:
                    frame = adb_client.screencap()
                except Exception as exc:
                    self.threadsafe_log(f"Screen capture failed: {exc}")
                    self.sleep_interruptible(settings["scan_interval"])
                    continue

                clicked_interrupt = False
                for interrupt in interrupts:
                    if not interrupt.get("enabled", True):
                        continue
                    match = self.matcher.find(
                        frame,
                        interrupt.get("template", ""),
                        float(interrupt.get("confidence", 0.85)),
                    )
                    if match:
                        self.click_match(match, interrupt.get("name", "interrupt"), settings)
                        clicked_interrupt = True
                        break

                if clicked_interrupt:
                    self.human_delay(None, settings)
                    continue

                step = sequence[seq_index]
                self.after(0, self.current_step_var.set, step.get("name", "-"))
                if not step.get("enabled", True):
                    self.threadsafe_log(f"Skip disabled step: {step.get('name')}")
                    seq_index, step_wait_start, wait_target = self.advance_step(sequence, seq_index)
                    continue

                wait_before = step.get("wait_before")
                if wait_before:
                    if wait_target is None:
                        wait_target = self.pick_delay(wait_before)
                    if time.monotonic() - step_wait_start < wait_target:
                        self.sleep_interruptible(settings["scan_interval"])
                        continue

                threshold = float(step.get("confidence", 0.85))
                match = self.matcher.best_match(frame, step.get("template", ""))
                if match:
                    self.after(
                        0,
                        self.match_summary_var.set,
                        f"{step.get('name', '-')}: {match['score']:.3f}/{threshold:.3f}",
                    )
                    if match["score"] < threshold:
                        match = None
                if match:
                    self.click_match(match, step.get("name", "step"), settings)
                    if step.get("verify_click"):
                        self.verify_and_retap(step, settings)
                    seq_index, step_wait_start, wait_target = self.advance_step(sequence, seq_index)
                    next_step = sequence[seq_index].get("name", "-")
                    self.threadsafe_log(f"Waiting for next step: {next_step}")
                    self.human_delay(step.get("post_delay"), settings)
                    continue

                timeout = step.get("timeout")
                if timeout and time.monotonic() - step_wait_start >= float(timeout):
                    self.threadsafe_log(f"{step.get('name')} not found within {timeout}s; skipping.")
                    seq_index, step_wait_start, wait_target = self.advance_step(sequence, seq_index)
                    continue

                retry_after = step.get("retry_after")
                if retry_after and time.monotonic() - step_wait_start >= float(retry_after):
                    retry_template = step.get("retry_template") or "templates/start.png"
                    retry_confidence = step.get("retry_confidence")
                    if retry_confidence is None:
                        retry_confidence = step.get("confidence", 0.85)
                    retry_match = self.matcher.find(frame, retry_template, float(retry_confidence))
                    if retry_match:
                        self.threadsafe_log(
                            f"{step.get('name')} not found after {retry_after}s; tapping retry template."
                        )
                        self.click_match(retry_match, f"{step.get('name')}-retry", settings)
                        step_wait_start = time.monotonic()
                        wait_target = None
                        self.human_delay(None, settings)
                        continue

                self.sleep_interruptible(settings["scan_interval"])
        except Exception as exc:
            self.threadsafe_log(f"Loop error: {exc}")
        finally:
            self.after(0, self.status_var.set, "Stopped")
            self.after(0, self.current_step_var.set, "-")
            self.threadsafe_log("Loop stopped.")

    def verify_and_retap(self, step, settings):
        for attempt in range(settings["retry_limit"]):
            if self.stop_event.is_set():
                return
            self.sleep_interruptible(settings["verify_delay"])
            if self.pause_event.is_set():
                return
            frame = adb_client.screencap()
            match = self.matcher.find(frame, step.get("template", ""), float(step.get("confidence", 0.85)))
            if not match:
                return
            self.threadsafe_log(f"{step.get('name')} still visible after tap; retap {attempt + 1}.")
            self.click_match(match, step.get("name", "step"), settings)

    def advance_step(self, sequence, index):
        next_index = (index + 1) % len(sequence)
        return next_index, time.monotonic(), None

    def click_match(self, match, label, settings):
        margin = min(settings["jitter"], match["w"] // 2 - 1, match["h"] // 2 - 1)
        margin = max(margin, 0)
        cx = match["x"] + match["w"] // 2 + random.randint(-margin, margin)
        cy = match["y"] + match["h"] // 2 + random.randint(-margin, margin)
        adb_client.tap(cx, cy)
        self.threadsafe_log(f"Tapped {label} score={match['score']:.3f} at ({cx}, {cy})")

    def pick_delay(self, delay_value):
        if isinstance(delay_value, list):
            delay_value = tuple(delay_value)
        if isinstance(delay_value, tuple) and len(delay_value) == 2:
            return random.uniform(float(delay_value[0]), float(delay_value[1]))
        if delay_value is None:
            return None
        return float(delay_value)

    def human_delay(self, delay_value, settings):
        if delay_value is None:
            delay = random.uniform(settings["min_delay"], settings["max_delay"])
        else:
            delay = self.pick_delay(delay_value)
        self.threadsafe_log(f"Wait {delay:.2f}s")
        self.sleep_interruptible(delay)

    def sleep_interruptible(self, seconds):
        end = time.monotonic() + max(0.0, float(seconds))
        while not self.stop_event.is_set() and time.monotonic() < end:
            if self.pause_event.is_set():
                time.sleep(0.1)
            else:
                time.sleep(min(0.1, end - time.monotonic()))

    def save_config_file(self):
        if self.active_tree().selection() and not self.apply_edit():
            return
        settings = self.settings_snapshot()
        updates = {
            "SEQUENCE": [compact_step(step) for step in self.sequence],
            "INTERRUPTS": [compact_step(step) for step in self.interrupts],
            "ADB_PATH": self.adb_path_var.get().strip(),
            "ADB_SERIAL": self.adb_serial_var.get().strip(),
            "SCAN_INTERVAL": settings["scan_interval"],
            "MIN_CLICK_DELAY": settings["min_delay"],
            "MAX_CLICK_DELAY": settings["max_delay"],
            "CLICK_JITTER_PX": settings["jitter"],
            "CLICK_RETRY_LIMIT": settings["retry_limit"],
            "CLICK_VERIFY_DELAY": settings["verify_delay"],
        }
        try:
            write_config_assignments(CONFIG_PATH, updates)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        self.apply_config_updates(updates)
        self.log(f"Synced {CONFIG_PATH}.")

    def load_config_file(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Loop running", "Stop the loop before reloading config.")
            return
        try:
            importlib.reload(config)
        except Exception as exc:
            messagebox.showerror("Reload failed", str(exc))
            return
        self.adb_path_var.set(config.ADB_PATH)
        self.adb_serial_var.set(config.ADB_SERIAL)
        self.scan_interval_var.set(str(config.SCAN_INTERVAL))
        self.min_delay_var.set(str(config.MIN_CLICK_DELAY))
        self.max_delay_var.set(str(config.MAX_CLICK_DELAY))
        self.jitter_var.set(str(config.CLICK_JITTER_PX))
        self.retry_limit_var.set(str(config.CLICK_RETRY_LIMIT))
        self.verify_delay_var.set(str(config.CLICK_VERIFY_DELAY))
        self.sequence = [step_defaults(step) for step in copy.deepcopy(config.SEQUENCE)]
        self.interrupts = [step_defaults(step) for step in copy.deepcopy(config.INTERRUPTS)]
        self.matcher.clear()
        self.refresh_tree()
        self.clear_editor()
        self.log(f"Reloaded {CONFIG_PATH}.")

    def apply_config_updates(self, updates):
        for name, value in updates.items():
            setattr(config, name, value)

    def log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def threadsafe_log(self, message):
        self.after(0, self.log, message)

    def on_close(self):
        if self.worker and self.worker.is_alive():
            self.stop_loop()
            self.after(300, self.destroy)
        else:
            self.destroy()


if __name__ == "__main__":
    app = BotApp()
    app.mainloop()
