import ast
import copy
import importlib
import json
import os
import pprint
import queue
import random
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2

import adb_client
import config_loader

try:
    import keyboard
except ImportError:
    keyboard = None

config = config_loader.config
CONFIG_PATH = config_loader.CONFIG_PATH
PREVIEW_MAX_WIDTH = 500
PREVIEW_MAX_HEIGHT = 150
DEFAULT_UI_THEME = "Slate"
THEME_PRESETS = {
    "Slate": {
        "app": "#0f172a",
        "surface": "#111827",
        "panel": "#1f2937",
        "input": "#020617",
        "border": "#334155",
        "text": "#f8fafc",
        "muted": "#94a3b8",
        "accent": "#22c55e",
        "accent_active": "#15803d",
        "accent_text": "#04130a",
        "danger": "#ef4444",
        "danger_active": "#991b1b",
        "warning": "#f59e0b",
        "quiet_active": "#334155",
        "quiet_pressed": "#0f172a",
        "nav_active": "#1e293b",
        "tab_active": "#334155",
        "odd": "#0b1220",
        "disabled": "#64748b",
        "selected_text": "#ffffff",
    },
    "Light": {
        "app": "#e2e8f0",
        "surface": "#f8fafc",
        "panel": "#ffffff",
        "input": "#ffffff",
        "border": "#cbd5e1",
        "text": "#0f172a",
        "muted": "#475569",
        "accent": "#2563eb",
        "accent_active": "#1d4ed8",
        "accent_text": "#ffffff",
        "danger": "#dc2626",
        "danger_active": "#991b1b",
        "warning": "#d97706",
        "quiet_active": "#dbeafe",
        "quiet_pressed": "#bfdbfe",
        "nav_active": "#e2e8f0",
        "tab_active": "#e2e8f0",
        "odd": "#f1f5f9",
        "disabled": "#94a3b8",
        "selected_text": "#ffffff",
    },
    "Brown": {
        "app": "#170d08",
        "surface": "#24160d",
        "panel": "#301f12",
        "input": "#100804",
        "border": "#6f4721",
        "text": "#fff4d6",
        "muted": "#c89b54",
        "accent": "#f4c542",
        "accent_active": "#a86518",
        "accent_text": "#2a1305",
        "danger": "#d83a2e",
        "danger_active": "#8f2118",
        "warning": "#ffb84d",
        "quiet_active": "#3d2918",
        "quiet_pressed": "#1b0f08",
        "nav_active": "#332111",
        "tab_active": "#3d2918",
        "odd": "#1b1009",
        "disabled": "#8f6b3a",
        "selected_text": "#ffffff",
    },
}


def set_theme_globals(theme_name):
    global CURRENT_UI_THEME
    global APP_BG, SURFACE_BG, PANEL_BG, INPUT_BG, BORDER_COLOR
    global TEXT_COLOR, MUTED_COLOR, ACCENT_COLOR, ACCENT_ACTIVE
    global DANGER_COLOR, DANGER_ACTIVE, WARNING_COLOR, THEME

    if theme_name not in THEME_PRESETS:
        theme_name = DEFAULT_UI_THEME
    THEME = THEME_PRESETS[theme_name]
    CURRENT_UI_THEME = theme_name
    APP_BG = THEME["app"]
    SURFACE_BG = THEME["surface"]
    PANEL_BG = THEME["panel"]
    INPUT_BG = THEME["input"]
    BORDER_COLOR = THEME["border"]
    TEXT_COLOR = THEME["text"]
    MUTED_COLOR = THEME["muted"]
    ACCENT_COLOR = THEME["accent"]
    ACCENT_ACTIVE = THEME["accent_active"]
    DANGER_COLOR = THEME["danger"]
    DANGER_ACTIVE = THEME["danger_active"]
    WARNING_COLOR = THEME["warning"]


set_theme_globals(getattr(config, "UI_THEME", DEFAULT_UI_THEME))
DEFAULT_JUMP_TAP = (165, 625)
DEFAULT_SLIDE_SWIPE = (1115, 625, 1115, 625, 140)
DEFAULT_RECORDING_FILE = os.path.join("recordings", "last_recording.json")
RECORDER_ACTIONS = {"jump", "slide"}
RECORDER_KEY_ACTIONS = {"w": "jump", "s": "slide"}
RECORDER_EVENT_TYPES = {"down", "up", "tap"}
RECORDER_INPUT_MODES = ("keyboard", "adb")
DEFAULT_RECORDER_INPUT_MODE = "adb"
DEFAULT_RECORDER_REPLAY_START_DELAY = 2.0
DEFAULT_LOOP_REPLAY_ENABLED = False
DEFAULT_LOOP_REPLAY_TRIGGER_MODE = "step"
DEFAULT_LOOP_REPLAY_TRIGGER_STEP = "pic_for_vid"
DEFAULT_LOOP_REPLAY_TRIGGER_TEMPLATE = "templates/replay_boost_trigger.png"
DEFAULT_LOOP_REPLAY_TRIGGER_CONFIDENCE = 0.80
DEFAULT_LOOP_REPLAY_FILE = DEFAULT_RECORDING_FILE
DEFAULT_LOOP_REPLAY_DELAY = 0.0
DEFAULT_LOOP_REPLAY_TAP_TRIGGER = False
DEFAULT_RECORD_ANCHOR_TEMPLATE = os.path.join("templates", "replay_game_start.png")
RECORD_ANCHOR_CROP = (0.35, 0.22, 0.65, 0.50)
DEFAULT_REPLAY_OVERLAY_SCAN_INTERVAL = 0.08
MAX_LOG_LINES = 250


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


def format_optional(value):
    return "" if value is None else str(value)


def checkbox_text(value):
    return "[x]" if bool(value) else "[ ]"


def default_replay_overlay():
    return {
        "name": "Click Use Relay",
        "template": "templates/run2.png",
        "confidence": 0.85,
        "once": True,
        "cooldown": 2.0,
        "scan_interval": DEFAULT_REPLAY_OVERLAY_SCAN_INTERVAL,
        "start": None,
        "end": None,
    }


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


def config_tuple(name, default, length):
    value = getattr(config, name, default)
    if isinstance(value, (list, tuple)) and len(value) == length:
        return tuple(value)
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


class PageHost(ttk.Frame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.pages = []
        self.current_index = None

    def add(self, page, text=""):
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_remove()
        self.pages.append((page, text))
        if self.current_index is None:
            self.select(0)

    def select(self, target):
        if isinstance(target, int):
            index = target
        else:
            index = next((i for i, (page, _text) in enumerate(self.pages) if page == target), 0)
        if not 0 <= index < len(self.pages):
            return
        if self.current_index is not None:
            self.pages[self.current_index][0].grid_remove()
        self.current_index = index
        self.pages[index][0].grid()

    def index(self, target):
        if isinstance(target, int):
            return target
        return next((i for i, (page, _text) in enumerate(self.pages) if page == target), 0)


class BotApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Cookie Run Classic Runner")
        self.geometry("1240x780")
        self.minsize(1080, 680)
        self.configure(background=APP_BG)
        self.setup_styles()
        self.ui_theme_var = tk.StringVar(value=CURRENT_UI_THEME)

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
        jump_tap = config_tuple("RECORDER_JUMP_TAP", DEFAULT_JUMP_TAP, 2)
        slide_swipe = config_tuple("RECORDER_SLIDE_SWIPE", DEFAULT_SLIDE_SWIPE, 5)
        input_mode = getattr(config, "RECORDER_INPUT_MODE", DEFAULT_RECORDER_INPUT_MODE)
        if input_mode not in RECORDER_INPUT_MODES:
            input_mode = DEFAULT_RECORDER_INPUT_MODE
        self.record_input_mode_var = tk.StringVar(value=input_mode)
        self.record_jump_x_var = tk.StringVar(value=str(jump_tap[0]))
        self.record_jump_y_var = tk.StringVar(value=str(jump_tap[1]))
        self.record_slide_x1_var = tk.StringVar(value=str(slide_swipe[0]))
        self.record_slide_y1_var = tk.StringVar(value=str(slide_swipe[1]))
        self.record_slide_x2_var = tk.StringVar(value=str(slide_swipe[2]))
        self.record_slide_y2_var = tk.StringVar(value=str(slide_swipe[3]))
        self.record_slide_ms_var = tk.StringVar(value=str(slide_swipe[4]))
        self.loop_replay_enabled_var = tk.BooleanVar(
            value=bool(getattr(config, "RECORDER_LOOP_REPLAY_ENABLED", DEFAULT_LOOP_REPLAY_ENABLED))
        )
        self.loop_replay_mode_var = tk.StringVar(
            value=str(getattr(config, "RECORDER_LOOP_TRIGGER_MODE", DEFAULT_LOOP_REPLAY_TRIGGER_MODE))
        )
        self.loop_replay_step_var = tk.StringVar(
            value=str(getattr(config, "RECORDER_LOOP_TRIGGER_STEP", DEFAULT_LOOP_REPLAY_TRIGGER_STEP))
        )
        self.loop_replay_template_var = tk.StringVar(
            value=str(getattr(config, "RECORDER_LOOP_TRIGGER_TEMPLATE", DEFAULT_LOOP_REPLAY_TRIGGER_TEMPLATE))
        )
        self.loop_replay_confidence_var = tk.StringVar(
            value=str(getattr(config, "RECORDER_LOOP_TRIGGER_CONFIDENCE", DEFAULT_LOOP_REPLAY_TRIGGER_CONFIDENCE))
        )
        self.loop_replay_file_var = tk.StringVar(
            value=str(getattr(config, "RECORDER_LOOP_REPLAY_FILE", DEFAULT_LOOP_REPLAY_FILE))
        )
        self.loop_replay_delay_var = tk.StringVar(
            value=str(getattr(config, "RECORDER_LOOP_REPLAY_DELAY", DEFAULT_LOOP_REPLAY_DELAY))
        )
        self.loop_replay_tap_trigger_var = tk.BooleanVar(
            value=bool(getattr(config, "RECORDER_LOOP_TAP_TRIGGER", DEFAULT_LOOP_REPLAY_TAP_TRIGGER))
        )
        self.video_replay_status_var = tk.StringVar(value="No step selected")
        overlay_defaults = default_replay_overlay()
        self.overlay_name_var = tk.StringVar(value=overlay_defaults["name"])
        self.overlay_template_var = tk.StringVar(value=overlay_defaults["template"])
        self.overlay_confidence_var = tk.StringVar(value=str(overlay_defaults["confidence"]))
        self.overlay_once_var = tk.BooleanVar(value=overlay_defaults["once"])
        self.overlay_cooldown_var = tk.StringVar(value=str(overlay_defaults["cooldown"]))
        self.overlay_scan_var = tk.StringVar(value=str(overlay_defaults["scan_interval"]))
        self.overlay_start_var = tk.StringVar(value="")
        self.overlay_end_var = tk.StringVar(value="")
        self.overlay_status_var = tk.StringVar(value="No overlay loaded")

        self.selected_iid = None
        self.status_var = tk.StringVar(value="Idle")
        self.current_step_var = tk.StringVar(value="-")
        self.preview_text_var = tk.StringVar(value="No template selected")
        self.preview_image = None
        self.capture_target_map = {}
        self.editor_dirty = False
        self.loading_editor = False
        self.record_events = []
        self.record_overlays = []
        self.recording = False
        self.record_start_time = None
        self.record_anchor_offset = 0.0
        self.record_anchor_template = DEFAULT_RECORD_ANCHOR_TEMPLATE
        self.record_anchor_marking = False
        self.last_record_anchor_hotkey = 0.0
        self.record_replay_worker = None
        self.replay_stop_event = threading.Event()
        self.recorder_sender_stop = threading.Event()
        self.recorder_adb_queue = queue.Queue()
        self.recorder_adb_lock = threading.Lock()
        self.recorder_poll_stop = threading.Event()
        self.recorder_poll_worker = None
        self.recorder_poll_states = {}
        self.global_recorder_hotkeys = []
        self.recorder_pressed_keys = {}
        self.record_state_var = tk.StringVar(value="Idle")
        self.record_count_var = tk.StringVar(value="0 events")
        self.record_elapsed_var = tk.StringVar(value="0.00s")
        self.record_anchor_var = tk.StringVar(value="-")

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
        for variable in self.edit_vars.values():
            variable.trace_add("write", self.mark_editor_dirty)

        self.create_widgets()
        self.recorder_sender = threading.Thread(target=self.recorder_sender_worker, daemon=True)
        self.recorder_sender.start()
        self.refresh_tree()
        self.load_overlay_settings_from_current_file(silent=True)
        self.log("Ready. Connect LDPlayer, then Start or Test Screen.")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def setup_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        default_font = ("Segoe UI", 10)
        title_font = ("Segoe UI", 18, "bold")
        label_font = ("Segoe UI", 9)
        button_font = ("Segoe UI", 10)
        style.configure(".", font=default_font, background=APP_BG, foreground=TEXT_COLOR)
        style.configure("TFrame", background=APP_BG)
        style.configure("App.TFrame", background=APP_BG)
        style.configure("Header.TFrame", background=APP_BG)
        style.configure("Sidebar.TFrame", background=SURFACE_BG)
        style.configure("Surface.TFrame", background=SURFACE_BG)
        style.configure("Panel.TFrame", background=PANEL_BG)
        style.configure("PageHost.TFrame", background=APP_BG)
        style.configure("TLabel", background=APP_BG, foreground=TEXT_COLOR)
        style.configure("Title.TLabel", background=APP_BG, foreground=TEXT_COLOR, font=title_font)
        style.configure("Muted.TLabel", background=APP_BG, foreground=MUTED_COLOR, font=label_font)
        style.configure("SidebarTitle.TLabel", background=SURFACE_BG, foreground=TEXT_COLOR, font=("Segoe UI", 12, "bold"))
        style.configure("SidebarMuted.TLabel", background=SURFACE_BG, foreground=MUTED_COLOR, font=("Segoe UI", 9))
        style.configure("Status.TLabel", background=APP_BG, foreground=TEXT_COLOR, font=("Segoe UI", 11, "bold"))
        style.configure("Metric.TLabel", background=PANEL_BG, foreground=TEXT_COLOR, font=("Segoe UI", 11, "bold"))
        style.configure("MetricMuted.TLabel", background=PANEL_BG, foreground=MUTED_COLOR, font=("Segoe UI", 8))
        style.configure("TButton", font=button_font, padding=(14, 8), borderwidth=0, focusthickness=0)
        style.configure(
            "Accent.TButton",
            font=("Segoe UI", 10, "bold"),
            padding=(18, 9),
            foreground=THEME["accent_text"],
            background=ACCENT_COLOR,
        )
        style.map(
            "Accent.TButton",
            background=[("active", ACCENT_COLOR), ("pressed", ACCENT_ACTIVE), ("disabled", PANEL_BG)],
            foreground=[("disabled", MUTED_COLOR)],
        )
        style.configure(
            "Danger.TButton",
            font=("Segoe UI", 10, "bold"),
            padding=(18, 9),
            foreground="#fff7f7",
            background=DANGER_COLOR,
        )
        style.map("Danger.TButton", background=[("active", "#ef5a4d"), ("pressed", DANGER_ACTIVE)])
        style.configure(
            "Quiet.TButton",
            font=button_font,
            padding=(14, 8),
            foreground=TEXT_COLOR,
            background=PANEL_BG,
            bordercolor=BORDER_COLOR,
        )
        style.map("Quiet.TButton", background=[("active", THEME["quiet_active"]), ("pressed", THEME["quiet_pressed"])])
        style.configure(
            "Nav.TButton",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
            padding=(14, 11),
            foreground=MUTED_COLOR,
            background=SURFACE_BG,
        )
        style.map("Nav.TButton", background=[("active", THEME["nav_active"])], foreground=[("active", TEXT_COLOR)])
        style.configure(
            "NavSelected.TButton",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
            padding=(14, 11),
            foreground=THEME["accent_text"],
            background=ACCENT_COLOR,
        )
        style.map("NavSelected.TButton", background=[("active", ACCENT_COLOR), ("pressed", ACCENT_ACTIVE)])
        style.configure("TNotebook", background=APP_BG, borderwidth=0, tabmargins=(0, 4, 0, 0))
        style.configure(
            "TNotebook.Tab",
            padding=(18, 8),
            font=("Segoe UI", 10, "bold"),
            background=PANEL_BG,
            foreground=MUTED_COLOR,
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", ACCENT_COLOR), ("active", THEME["tab_active"])],
            foreground=[("selected", THEME["accent_text"])],
        )
        style.configure("TLabelframe", background=PANEL_BG, bordercolor=BORDER_COLOR, relief="solid")
        style.configure(
            "TLabelframe.Label",
            background=PANEL_BG,
            foreground=TEXT_COLOR,
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "Treeview",
            font=("Segoe UI", 9),
            rowheight=31,
            fieldbackground=SURFACE_BG,
            background=SURFACE_BG,
            foreground=TEXT_COLOR,
            borderwidth=0,
            relief="flat",
        )
        style.map("Treeview", background=[("selected", ACCENT_ACTIVE)], foreground=[("selected", THEME["selected_text"])])
        style.configure(
            "Treeview.Heading",
            font=("Segoe UI", 9, "bold"),
            background=PANEL_BG,
            foreground=TEXT_COLOR,
            relief="flat",
            bordercolor=BORDER_COLOR,
        )
        style.configure(
            "TEntry",
            padding=(8, 7),
            fieldbackground=INPUT_BG,
            foreground=TEXT_COLOR,
            insertcolor=TEXT_COLOR,
            bordercolor=BORDER_COLOR,
            lightcolor=BORDER_COLOR,
            darkcolor=BORDER_COLOR,
        )
        style.configure(
            "TCombobox",
            padding=(8, 7),
            fieldbackground=INPUT_BG,
            background=INPUT_BG,
            foreground=TEXT_COLOR,
            arrowcolor=TEXT_COLOR,
            bordercolor=BORDER_COLOR,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", INPUT_BG), ("focus", INPUT_BG), ("!disabled", INPUT_BG)],
            background=[("readonly", INPUT_BG), ("focus", INPUT_BG), ("!disabled", INPUT_BG)],
            foreground=[("readonly", TEXT_COLOR), ("focus", TEXT_COLOR), ("!disabled", TEXT_COLOR)],
            selectbackground=[("readonly", INPUT_BG), ("focus", INPUT_BG)],
            selectforeground=[("readonly", TEXT_COLOR), ("focus", TEXT_COLOR)],
            arrowcolor=[("readonly", ACCENT_COLOR), ("focus", ACCENT_COLOR)],
        )
        self.option_add("*TCombobox*Listbox.background", INPUT_BG)
        self.option_add("*TCombobox*Listbox.foreground", TEXT_COLOR)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT_ACTIVE)
        self.option_add("*TCombobox*Listbox.selectForeground", THEME["selected_text"])
        style.configure("TCheckbutton", background=PANEL_BG, foreground=TEXT_COLOR)
        style.map("TCheckbutton", background=[("active", PANEL_BG)], foreground=[("disabled", MUTED_COLOR)])
        style.configure("Vertical.TScrollbar", background=PANEL_BG, troughcolor=APP_BG, arrowcolor=MUTED_COLOR)

    def create_widgets(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        command_bar = ttk.Frame(self, padding=(18, 16, 18, 12), style="Header.TFrame")
        command_bar.grid(row=0, column=0, sticky="ew")
        command_bar.columnconfigure(1, weight=1)

        title_block = ttk.Frame(command_bar, style="Header.TFrame")
        title_block.grid(row=0, column=0, sticky="w")
        ttk.Label(title_block, text="Cookie Run Runner", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(title_block, text="ADB automation control panel", style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 0)
        )

        status_bar = ttk.Frame(command_bar, style="Header.TFrame")
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

        controls = ttk.Frame(command_bar, style="Header.TFrame")
        controls.grid(row=0, column=2, sticky="e")
        ttk.Button(controls, text="Connect", command=self.connect_adb, style="Quiet.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="Start", command=self.start_loop, style="Accent.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="Kill", command=self.stop_loop, style="Danger.TButton").pack(side=tk.LEFT)

        workspace = ttk.Frame(self, padding=(18, 0, 18, 18), style="App.TFrame")
        workspace.grid(row=1, column=0, sticky="nsew")
        workspace.columnconfigure(1, weight=1)
        workspace.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(workspace, padding=(12, 14), style="Sidebar.TFrame")
        sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 14))
        sidebar.grid_propagate(False)
        sidebar.configure(width=174)
        ttk.Label(sidebar, text="Workspace", style="SidebarTitle.TLabel").pack(anchor="w")
        ttk.Label(sidebar, text="Select a task", style="SidebarMuted.TLabel").pack(anchor="w", pady=(2, 16))

        self.nav_buttons = []
        nav_items = (
            ("Run", "Monitor"),
            ("Steps", "Edit flow"),
            ("Capture", "Templates"),
            ("Record", "Jump/slide"),
            ("Settings", "Device"),
        )
        for index, (label, hint) in enumerate(nav_items):
            button = ttk.Button(
                sidebar,
                text=f"{label}\n{hint}",
                style="Nav.TButton",
                command=lambda page_index=index: self.select_main_page(page_index),
            )
            button.pack(fill=tk.X, pady=(0, 8))
            self.nav_buttons.append(button)

        ttk.Frame(sidebar, height=1, style="Panel.TFrame").pack(fill=tk.X, pady=(8, 12))
        ttk.Label(sidebar, text="Config sync", style="SidebarMuted.TLabel").pack(anchor="w")
        ttk.Label(sidebar, text="Save Config writes back to config.py", style="SidebarMuted.TLabel", wraplength=140).pack(
            anchor="w", pady=(2, 0)
        )

        self.main_tabs = PageHost(workspace, style="PageHost.TFrame")
        self.main_tabs.grid(row=0, column=1, sticky="nsew")

        self.create_run_tab()
        self.create_steps_tab()
        self.create_capture_tab()
        self.create_record_tab()
        self.create_settings_tab()
        self.select_main_page(0)
        self.bind_all("w", lambda event: self.recorder_hotkey(event, "jump"))
        self.bind_all("W", lambda event: self.recorder_hotkey(event, "jump"))
        self.bind_all("s", lambda event: self.recorder_hotkey(event, "slide"))
        self.bind_all("S", lambda event: self.recorder_hotkey(event, "slide"))
        self.bind_all("<F7>", lambda _event: self.mark_record_anchor())

    def select_main_page(self, index):
        self.main_tabs.select(index)
        for button_index, button in enumerate(getattr(self, "nav_buttons", [])):
            button.configure(style="NavSelected.TButton" if button_index == index else "Nav.TButton")

    def create_run_tab(self):
        run = ttk.Frame(self.main_tabs, padding=14)
        run.columnconfigure(0, weight=1)
        run.rowconfigure(1, weight=1)
        self.main_tabs.add(run, text="Run")

        actions = ttk.Frame(run)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(actions, text="Debug what the bot can see right now.", style="Muted.TLabel").pack(
            side=tk.LEFT, padx=(0, 12)
        )
        ttk.Button(actions, text="Test Screen", command=self.test_current_screen, style="Accent.TButton").pack(
            side=tk.LEFT, padx=(0, 8)
        )

        log_frame = ttk.LabelFrame(run, text="Log", padding=(10, 8))
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame,
            height=12,
            wrap="word",
            state="disabled",
            background=INPUT_BG,
            foreground=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            selectbackground=ACCENT_ACTIVE,
            relief="flat",
            font=("Consolas", 9),
            padx=10,
            pady=8,
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

        editor_shell = ttk.Frame(steps)
        editor_shell.grid(row=1, column=1, sticky="nsew")
        editor_shell.columnconfigure(0, weight=1)
        editor_shell.rowconfigure(0, weight=1)

        self.editor_canvas = tk.Canvas(
            editor_shell,
            background=APP_BG,
            borderwidth=0,
            highlightthickness=0,
            yscrollincrement=24,
        )
        editor_scroll = ttk.Scrollbar(editor_shell, orient="vertical", command=self.editor_canvas.yview)
        self.editor_canvas.configure(yscrollcommand=editor_scroll.set)
        self.editor_canvas.grid(row=0, column=0, sticky="nsew")
        editor_scroll.grid(row=0, column=1, sticky="ns")

        editor = ttk.Frame(self.editor_canvas)
        editor_window = self.editor_canvas.create_window((0, 0), window=editor, anchor="nw")

        def sync_editor_scroll(_event=None):
            self.editor_canvas.configure(scrollregion=self.editor_canvas.bbox("all"))

        def sync_editor_width(event):
            self.editor_canvas.itemconfigure(editor_window, width=event.width)

        def scroll_editor(event):
            if event.delta:
                self.editor_canvas.yview_scroll(int(-event.delta / 120), "units")

        editor.bind("<Configure>", sync_editor_scroll)
        self.editor_canvas.bind("<Configure>", sync_editor_width)
        editor_shell.bind("<Enter>", lambda _event: self.editor_canvas.bind_all("<MouseWheel>", scroll_editor))
        editor_shell.bind("<Leave>", lambda _event: self.editor_canvas.unbind_all("<MouseWheel>"))

        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(3, weight=1)

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
            background=INPUT_BG,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
        )
        self.preview_canvas.grid(row=0, column=0, sticky="ew")
        ttk.Label(preview, textvariable=self.preview_text_var).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.preview_canvas.bind("<Configure>", lambda _event: self.update_template_preview())

        replay = ttk.LabelFrame(editor, text="Video Replay", padding=(10, 8))
        replay.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        replay.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            replay,
            text="Run replay on trigger",
            variable=self.loop_replay_enabled_var,
            command=self.on_loop_replay_option_changed,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Button(
            replay,
            text="Make selected VIDEO",
            command=self.use_selected_step_as_replay_trigger,
            style="Quiet.TButton",
        ).grid(row=0, column=2, sticky="e", padx=(8, 0))
        ttk.Label(replay, textvariable=self.video_replay_status_var, style="Muted.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 4)
        )
        self.video_replay_detail = ttk.Frame(replay)
        self.video_replay_detail.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        self.video_replay_detail.columnconfigure(1, weight=1)
        ttk.Label(self.video_replay_detail, text="Offset").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(self.video_replay_detail, textvariable=self.loop_replay_delay_var, width=8).grid(
            row=0, column=1, sticky="w", pady=2, padx=(8, 0)
        )
        ttk.Checkbutton(
            self.video_replay_detail,
            text="Tap trigger",
            variable=self.loop_replay_tap_trigger_var,
            command=self.on_loop_replay_option_changed,
        ).grid(row=0, column=2, sticky="e", padx=(8, 0))
        ttk.Label(self.video_replay_detail, text="File").grid(row=1, column=0, sticky="w", pady=2)
        replay_file = ttk.Frame(self.video_replay_detail)
        replay_file.grid(row=1, column=1, columnspan=2, sticky="ew", pady=2, padx=(8, 0))
        replay_file.columnconfigure(0, weight=1)
        ttk.Entry(replay_file, textvariable=self.loop_replay_file_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(replay_file, text="...", width=3, command=self.browse_loop_replay_file).grid(
            row=0, column=1, padx=(4, 0)
        )
        self.show_video_replay_detail(False)

        advanced = ttk.LabelFrame(editor, text="Advanced", padding=(10, 8))
        advanced.grid(row=3, column=0, sticky="new")
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
            background=INPUT_BG,
            foreground=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            selectbackground=ACCENT_ACTIVE,
            relief="flat",
            font=("Consolas", 9),
            padx=10,
            pady=8,
        )
        self.capture_log.grid(row=0, column=0, sticky="nsew")
        capture_scroll = ttk.Scrollbar(capture_body, orient="vertical", command=self.capture_log.yview)
        capture_scroll.grid(row=0, column=1, sticky="ns")
        self.capture_log.configure(yscrollcommand=capture_scroll.set)

    def create_record_tab(self):
        record = ttk.Frame(self.main_tabs, padding=14)
        record.columnconfigure(0, weight=3)
        record.columnconfigure(1, weight=2)
        record.rowconfigure(2, weight=1)
        self.main_tabs.add(record, text="Record")

        toolbar = ttk.Frame(record)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Button(toolbar, text="Start Rec", command=self.start_recording, style="Accent.TButton").pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(toolbar, text="Stop Rec", command=self.stop_recording, style="Quiet.TButton").pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(toolbar, text="Mark Start (F7)", command=self.mark_record_anchor, style="Accent.TButton").pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(toolbar, text="Play", command=self.play_recording, style="Accent.TButton").pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(toolbar, text="Stop Play", command=self.stop_replay, style="Danger.TButton").pack(
            side=tk.LEFT, padx=(0, 18)
        )
        ttk.Button(toolbar, text="Save", command=self.save_recording, style="Quiet.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="Load", command=self.load_recording, style="Quiet.TButton").pack(side=tk.LEFT)

        status = ttk.LabelFrame(record, text="Recorder State", padding=(12, 10))
        status.grid(row=1, column=0, sticky="ew", pady=(0, 10), padx=(0, 10))
        for column in range(6):
            status.columnconfigure(column, weight=1)
        ttk.Label(status, text="State", style="MetricMuted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(status, textvariable=self.record_state_var, style="Metric.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Label(status, text="Events", style="MetricMuted.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(status, textvariable=self.record_count_var, style="Metric.TLabel").grid(row=1, column=1, sticky="w")
        ttk.Label(status, text="Elapsed", style="MetricMuted.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Label(status, textvariable=self.record_elapsed_var, style="Metric.TLabel").grid(row=1, column=2, sticky="w")
        ttk.Label(status, text="Anchor", style="MetricMuted.TLabel").grid(row=0, column=3, sticky="w")
        ttk.Label(status, textvariable=self.record_anchor_var, style="Metric.TLabel").grid(row=1, column=3, sticky="w")

        actions = ttk.LabelFrame(record, text="Live Input", padding=(12, 10))
        actions.grid(row=1, column=1, sticky="ew", pady=(0, 10))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        ttk.Button(actions, text="Jump (W)", command=lambda: self.trigger_recorder_action("jump"), style="Accent.TButton").grid(
            row=0, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Button(actions, text="Slide (S)", command=lambda: self.trigger_recorder_action("slide"), style="Accent.TButton").grid(
            row=0, column=1, sticky="ew"
        )

        timeline = ttk.LabelFrame(record, text="Timeline", padding=(10, 8))
        timeline.grid(row=2, column=0, sticky="nsew", padx=(0, 10))
        timeline.columnconfigure(0, weight=1)
        timeline.rowconfigure(0, weight=1)
        self.record_tree = self.create_record_tree(timeline)
        self.record_tree.master.grid(row=0, column=0, sticky="nsew")

        input_points = ttk.LabelFrame(record, text="Input Points", padding=(12, 10))
        input_points.grid(row=2, column=1, sticky="new")
        for column in range(4):
            input_points.columnconfigure(column, weight=1)

        ttk.Label(input_points, text="Mode").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.record_input_mode_combo = ttk.Combobox(
            input_points,
            textvariable=self.record_input_mode_var,
            state="readonly",
            values=RECORDER_INPUT_MODES,
        )
        self.record_input_mode_combo.grid(row=0, column=1, columnspan=3, sticky="ew", pady=(0, 8), padx=(6, 10))

        ttk.Label(input_points, text="Jump Tap", style="Metric.TLabel").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(0, 6)
        )
        self.add_recorder_field(input_points, "X", self.record_jump_x_var, 2, 0)
        self.add_recorder_field(input_points, "Y", self.record_jump_y_var, 2, 2)

        ttk.Label(input_points, text="Slide Swipe", style="Metric.TLabel").grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(12, 6)
        )
        self.add_recorder_field(input_points, "X1", self.record_slide_x1_var, 4, 0)
        self.add_recorder_field(input_points, "Y1", self.record_slide_y1_var, 4, 2)
        self.add_recorder_field(input_points, "X2", self.record_slide_x2_var, 5, 0)
        self.add_recorder_field(input_points, "Y2", self.record_slide_y2_var, 5, 2)
        self.add_recorder_field(input_points, "MS", self.record_slide_ms_var, 6, 0)

        loop_replay = ttk.LabelFrame(record, text="Loop Replay", padding=(12, 10))
        loop_replay.grid(row=3, column=1, sticky="ew", pady=(10, 0))
        loop_replay.columnconfigure(1, weight=1)
        ttk.Checkbutton(loop_replay, text="Run replay inside loop", variable=self.loop_replay_enabled_var).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6)
        )
        ttk.Checkbutton(loop_replay, text="Tap trigger before replay", variable=self.loop_replay_tap_trigger_var).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 6)
        )
        ttk.Label(loop_replay, text="Trigger").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Combobox(
            loop_replay,
            textvariable=self.loop_replay_mode_var,
            state="readonly",
            values=("template", "step"),
            width=10,
        ).grid(row=2, column=1, sticky="w", pady=3, padx=(8, 0))
        ttk.Label(loop_replay, text="Step").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(loop_replay, textvariable=self.loop_replay_step_var).grid(
            row=3, column=1, columnspan=2, sticky="ew", pady=3, padx=(8, 0)
        )
        ttk.Label(loop_replay, text="Template").grid(row=4, column=0, sticky="w", pady=3)
        template_row = ttk.Frame(loop_replay)
        template_row.grid(row=4, column=1, columnspan=2, sticky="ew", pady=3, padx=(8, 0))
        template_row.columnconfigure(0, weight=1)
        ttk.Entry(template_row, textvariable=self.loop_replay_template_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(template_row, text="...", width=3, command=self.browse_loop_replay_template).grid(
            row=0, column=1, padx=(4, 0)
        )
        ttk.Label(loop_replay, text="Confidence").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Entry(loop_replay, textvariable=self.loop_replay_confidence_var, width=8).grid(
            row=5, column=1, sticky="w", pady=3, padx=(8, 0)
        )
        ttk.Label(loop_replay, text="Delay").grid(row=6, column=0, sticky="w", pady=3)
        ttk.Entry(loop_replay, textvariable=self.loop_replay_delay_var, width=8).grid(
            row=6, column=1, sticky="w", pady=3, padx=(8, 0)
        )
        ttk.Label(loop_replay, text="File").grid(row=7, column=0, sticky="w", pady=3)
        file_row = ttk.Frame(loop_replay)
        file_row.grid(row=7, column=1, columnspan=2, sticky="ew", pady=3, padx=(8, 0))
        file_row.columnconfigure(0, weight=1)
        ttk.Entry(file_row, textvariable=self.loop_replay_file_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(file_row, text="...", width=3, command=self.browse_loop_replay_file).grid(row=0, column=1, padx=(4, 0))

        overlay = ttk.LabelFrame(record, text="Replay Overlay", padding=(12, 10))
        overlay.grid(row=4, column=1, sticky="ew", pady=(10, 0))
        overlay.columnconfigure(1, weight=1)
        overlay.columnconfigure(3, weight=0)
        ttk.Label(overlay, textvariable=self.overlay_status_var, style="Muted.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 6)
        )
        ttk.Label(overlay, text="Name").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(overlay, textvariable=self.overlay_name_var).grid(
            row=1, column=1, columnspan=3, sticky="ew", pady=3, padx=(8, 0)
        )
        ttk.Label(overlay, text="Template").grid(row=2, column=0, sticky="w", pady=3)
        overlay_template = ttk.Frame(overlay)
        overlay_template.grid(row=2, column=1, columnspan=3, sticky="ew", pady=3, padx=(8, 0))
        overlay_template.columnconfigure(0, weight=1)
        ttk.Entry(overlay_template, textvariable=self.overlay_template_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(overlay_template, text="...", width=3, command=self.browse_overlay_template).grid(
            row=0, column=1, padx=(4, 0)
        )
        ttk.Label(overlay, text="Confidence").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(overlay, textvariable=self.overlay_confidence_var, width=8).grid(
            row=3, column=1, sticky="w", pady=3, padx=(8, 0)
        )
        ttk.Checkbutton(overlay, text="Once", variable=self.overlay_once_var).grid(row=3, column=2, sticky="e")
        ttk.Label(overlay, text="Cooldown").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(overlay, textvariable=self.overlay_cooldown_var, width=8).grid(
            row=4, column=1, sticky="w", pady=3, padx=(8, 0)
        )
        ttk.Label(overlay, text="Scan").grid(row=4, column=2, sticky="w", pady=3, padx=(8, 0))
        ttk.Entry(overlay, textvariable=self.overlay_scan_var, width=8).grid(row=4, column=3, sticky="e", pady=3)
        ttk.Label(overlay, text="Start").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Entry(overlay, textvariable=self.overlay_start_var, width=8).grid(
            row=5, column=1, sticky="w", pady=3, padx=(8, 0)
        )
        ttk.Label(overlay, text="End").grid(row=5, column=2, sticky="w", pady=3, padx=(8, 0))
        ttk.Entry(overlay, textvariable=self.overlay_end_var, width=8).grid(row=5, column=3, sticky="e", pady=3)
        overlay_buttons = ttk.Frame(overlay)
        overlay_buttons.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Button(overlay_buttons, text="Load JSON", command=self.load_current_replay_file, style="Quiet.TButton").pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(overlay_buttons, text="Test", command=self.test_overlay_template, style="Quiet.TButton").pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(overlay_buttons, text="Apply", command=self.apply_overlay_edit, style="Accent.TButton").pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(overlay_buttons, text="Save JSON", command=self.save_overlay_to_current_file, style="Quiet.TButton").pack(
            side=tk.LEFT
        )

    def add_recorder_field(self, parent, label, variable, row, column):
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=variable, width=8).grid(row=row, column=column + 1, sticky="ew", pady=3, padx=(6, 10))

    def browse_loop_replay_file(self):
        os.makedirs("recordings", exist_ok=True)
        path = filedialog.askopenfilename(
            title="Select loop replay recording",
            filetypes=(("Recording JSON", "*.json"), ("All files", "*.*")),
            initialdir=os.path.abspath("recordings"),
        )
        if not path:
            return
        try:
            path = os.path.relpath(path, os.getcwd())
        except ValueError:
            pass
        self.loop_replay_file_var.set(path.replace("\\", "/"))
        self.load_overlay_settings_from_current_file(silent=True)

    def browse_loop_replay_template(self):
        path = filedialog.askopenfilename(
            title="Select loop replay trigger template",
            filetypes=(("PNG images", "*.png"), ("All files", "*.*")),
            initialdir=os.path.abspath("templates"),
        )
        if not path:
            return
        try:
            path = os.path.relpath(path, os.getcwd())
        except ValueError:
            pass
        self.loop_replay_template_var.set(path.replace("\\", "/"))

    def browse_overlay_template(self):
        path = filedialog.askopenfilename(
            title="Select replay overlay template",
            filetypes=(("PNG images", "*.png"), ("All files", "*.*")),
            initialdir=os.path.abspath("templates"),
        )
        if not path:
            return
        try:
            path = os.path.relpath(path, os.getcwd())
        except ValueError:
            pass
        self.overlay_template_var.set(path.replace("\\", "/"))

    def current_replay_file_path(self):
        path = self.loop_replay_file_var.get().strip()
        if not path:
            return ""
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        return path

    def set_overlay_editor(self, overlay=None):
        if overlay is None:
            overlay = self.record_overlays[0] if self.record_overlays else default_replay_overlay()
        self.overlay_name_var.set(str(overlay.get("name", "")))
        self.overlay_template_var.set(str(overlay.get("template", "")))
        self.overlay_confidence_var.set(str(overlay.get("confidence", 0.85)))
        self.overlay_once_var.set(bool(overlay.get("once", True)))
        self.overlay_cooldown_var.set(str(overlay.get("cooldown", 2.0)))
        self.overlay_scan_var.set(str(overlay.get("scan_interval", DEFAULT_REPLAY_OVERLAY_SCAN_INTERVAL)))
        self.overlay_start_var.set(format_optional(overlay.get("start")))
        self.overlay_end_var.set(format_optional(overlay.get("end")))
        self.update_overlay_status()

    def update_overlay_status(self):
        count = len(self.record_overlays)
        if count:
            template = self.record_overlays[0].get("template", "-")
            suffix = f" (+{count - 1})" if count > 1 else ""
            self.overlay_status_var.set(f"Loaded: {template}{suffix}")
        else:
            self.overlay_status_var.set("No overlay in JSON")

    def load_overlay_settings_from_current_file(self, silent=False):
        path = self.current_replay_file_path()
        if not path or not os.path.exists(path):
            self.record_overlays = []
            self.set_overlay_editor(default_replay_overlay())
            if not silent:
                messagebox.showerror("Load failed", "Replay JSON file not found.")
            return False
        try:
            with open(path, "r", encoding="utf-8") as file:
                payload = json.load(file)
            self.record_overlays = self.parse_replay_overlays(payload)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            if not silent:
                messagebox.showerror("Load failed", str(exc))
            return False
        self.set_overlay_editor()
        if not silent:
            self.log(f"Replay overlay loaded: {os.path.basename(path)}")
        return True

    def load_current_replay_file(self):
        path = self.current_replay_file_path()
        if not path or not os.path.exists(path):
            messagebox.showerror("Load failed", "Replay JSON file not found.")
            return
        try:
            with open(path, "r", encoding="utf-8") as file:
                payload = json.load(file)
            self.record_events = self.parse_recording_events(payload)
            self.record_overlays = self.parse_replay_overlays(payload)
            if isinstance(payload, dict):
                self.apply_loaded_recorder_points(payload)
                self.record_anchor_offset = float(payload.get("anchor_offset", 0.0))
                self.record_anchor_template = str(payload.get("anchor_template", DEFAULT_RECORD_ANCHOR_TEMPLATE))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("Load failed", str(exc))
            return
        self.record_anchor_var.set(f"{self.record_anchor_offset:.2f}s" if self.record_anchor_offset else "-")
        self.record_elapsed_var.set(f"{self.record_events[-1]['t']:.2f}s" if self.record_events else "0.00s")
        self.refresh_record_tree()
        self.set_overlay_editor()
        self.log(f"Replay JSON loaded: {os.path.basename(path)}")

    def overlay_snapshot(self):
        template = self.overlay_template_var.get().strip()
        if not template:
            return None
        try:
            confidence = float(self.overlay_confidence_var.get().strip() or 0.85)
            cooldown = float(self.overlay_cooldown_var.get().strip() or 2.0)
            scan_interval = float(self.overlay_scan_var.get().strip() or DEFAULT_REPLAY_OVERLAY_SCAN_INTERVAL)
            start_at = parse_optional_float(self.overlay_start_var.get())
            end_at = parse_optional_float(self.overlay_end_var.get())
        except ValueError as exc:
            raise ValueError("Replay overlay numbers are invalid.") from exc
        return {
            "name": self.overlay_name_var.get().strip() or "overlay",
            "template": template,
            "confidence": max(0.0, min(1.0, confidence)),
            "once": bool(self.overlay_once_var.get()),
            "cooldown": max(0.0, cooldown),
            "scan_interval": max(0.03, scan_interval),
            "start": start_at,
            "end": end_at,
        }

    def apply_overlay_edit(self, silent=False):
        try:
            overlay = self.overlay_snapshot()
        except ValueError as exc:
            if not silent:
                messagebox.showerror("Invalid overlay", str(exc))
            return False
        self.record_overlays = [] if overlay is None else [overlay]
        self.update_overlay_status()
        if not silent:
            self.log("Replay overlay applied.")
        return True

    def save_overlay_to_current_file(self):
        if not self.apply_overlay_edit(silent=True):
            return
        path = self.current_replay_file_path()
        if not path or not os.path.exists(path):
            messagebox.showerror("Save failed", "Replay JSON file not found.")
            return
        try:
            with open(path, "r", encoding="utf-8") as file:
                payload = json.load(file)
            if not isinstance(payload, dict):
                raise ValueError("Replay JSON must be an object.")
            payload["replay_overlays"] = self.record_overlays
            with open(path, "w", encoding="utf-8") as file:
                json.dump(payload, file, indent=2)
                file.write("\n")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        self.log(f"Replay overlay saved: {os.path.basename(path)}")

    def test_overlay_template(self):
        try:
            overlay = self.overlay_snapshot()
        except ValueError as exc:
            messagebox.showerror("Invalid overlay", str(exc))
            return
        if overlay is None:
            messagebox.showinfo("No overlay", "Select a template first.")
            return
        try:
            self.apply_adb_config()
            self.ensure_connected()
            frame = adb_client.screencap()
            match = self.matcher.best_match(frame, overlay["template"])
        except Exception as exc:
            messagebox.showerror("Test failed", str(exc))
            return
        if not match:
            self.overlay_status_var.set("Test: template not found")
            self.log("Replay overlay test: template not found.")
            return
        score = float(match["score"])
        threshold = float(overlay["confidence"])
        result = "PASS" if score >= threshold else "LOW"
        self.overlay_status_var.set(f"Test {result}: {score:.3f}/{threshold:.3f}")
        self.log(f"Replay overlay test {result}: {overlay['template']} {score:.3f}/{threshold:.3f}")

    def create_record_tree(self, parent):
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        columns = ("index", "time", "action", "event")
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        headings = {"index": "#", "time": "Time", "action": "Action", "event": "Event"}
        widths = {"index": 60, "time": 120, "action": 150, "event": 90}
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], minwidth=50, stretch=column == "action")
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)
        tree.tag_configure("odd", background=THEME["odd"])
        tree.tag_configure("even", background=SURFACE_BG)
        tree.tag_configure("disabled", foreground=THEME["disabled"])
        return tree

    def create_settings_tab(self):
        settings = ttk.Frame(self.main_tabs, padding=14)
        settings.columnconfigure(1, weight=1)
        self.main_tabs.add(settings, text="Settings")

        ttk.Label(settings, text="Theme").grid(row=0, column=0, sticky="w", pady=3)
        theme_row = ttk.Frame(settings)
        theme_row.grid(row=0, column=1, sticky="ew", pady=3, padx=(8, 0))
        theme_row.columnconfigure(0, weight=1)
        theme_combo = ttk.Combobox(
            theme_row,
            textvariable=self.ui_theme_var,
            values=tuple(THEME_PRESETS.keys()),
            state="readonly",
        )
        theme_combo.grid(row=0, column=0, sticky="ew")
        theme_combo.bind("<<ComboboxSelected>>", lambda _event: self.apply_theme_selection())
        ttk.Button(theme_row, text="Apply", command=self.apply_theme_selection, style="Quiet.TButton").grid(
            row=0, column=1, padx=(6, 0)
        )

        ttk.Label(settings, text="ADB path").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(settings, textvariable=self.adb_path_var).grid(row=1, column=1, sticky="ew", pady=3, padx=(8, 0))
        ttk.Label(settings, text="Serial").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(settings, textvariable=self.adb_serial_var).grid(row=2, column=1, sticky="ew", pady=3, padx=(8, 0))
        ttk.Button(settings, text="Connect", command=self.connect_adb, style="Accent.TButton").grid(
            row=3, column=1, sticky="w", pady=(6, 14), padx=(8, 0)
        )

        loop = ttk.LabelFrame(settings, text="Loop Settings", padding=(10, 8))
        loop.grid(row=4, column=0, columnspan=2, sticky="ew")
        for col in range(12):
            loop.columnconfigure(col, weight=1)
        self.add_setting(loop, "Scan", self.scan_interval_var, 0)
        self.add_setting(loop, "Delay min", self.min_delay_var, 2)
        self.add_setting(loop, "Delay max", self.max_delay_var, 4)
        self.add_setting(loop, "Jitter px", self.jitter_var, 6)
        self.add_setting(loop, "Retry", self.retry_limit_var, 8)
        self.add_setting(loop, "Verify delay", self.verify_delay_var, 10)

        config_buttons = ttk.Frame(settings)
        config_buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(config_buttons, text="Reload Config", command=self.load_config_file, style="Quiet.TButton").pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(config_buttons, text="Save Config", command=self.save_config_file, style="Accent.TButton").pack(side=tk.LEFT)

    def add_setting(self, parent, label, variable, column):
        ttk.Label(parent, text=label).grid(row=0, column=column, sticky="w")
        ttk.Entry(parent, textvariable=variable, width=8).grid(row=0, column=column + 1, sticky="w", padx=(4, 10))

    def apply_theme_selection(self):
        set_theme_globals(self.ui_theme_var.get())
        self.ui_theme_var.set(CURRENT_UI_THEME)
        self.configure(background=APP_BG)
        self.setup_styles()
        self.refresh_theme_widgets()
        self.refresh_tree()
        self.log(f"Theme applied: {CURRENT_UI_THEME}")

    def refresh_theme_widgets(self):
        for text_widget_name in ("log_text", "capture_log"):
            text_widget = getattr(self, text_widget_name, None)
            if text_widget:
                text_widget.configure(
                    background=INPUT_BG,
                    foreground=TEXT_COLOR,
                    insertbackground=TEXT_COLOR,
                    selectbackground=ACCENT_ACTIVE,
                )

        preview_canvas = getattr(self, "preview_canvas", None)
        if preview_canvas:
            preview_canvas.configure(background=INPUT_BG, highlightbackground=BORDER_COLOR)

        editor_canvas = getattr(self, "editor_canvas", None)
        if editor_canvas:
            editor_canvas.configure(background=APP_BG)

        for tree_name in ("sequence_tree", "interrupt_tree", "record_tree"):
            tree = getattr(self, tree_name, None)
            if tree:
                tree.tag_configure("odd", background=THEME["odd"])
                tree.tag_configure("even", background=SURFACE_BG)
                tree.tag_configure("disabled", foreground=THEME["disabled"])

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
            "replay",
            "post_delay",
            "wait_before",
            "timeout",
            "verify_click",
            "retry_after",
            "retry_template",
            "retry_confidence",
        )
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "enabled": "On",
            "name": "Name",
            "confidence": "Conf",
            "template": "Template",
            "replay": "Replay",
            "post_delay": "Post Delay",
            "wait_before": "Wait Before",
            "timeout": "Timeout",
            "verify_click": "Verify",
            "retry_after": "Retry",
            "retry_template": "Retry Template",
            "retry_confidence": "Retry Conf",
        }
        widths = {
            "enabled": 48,
            "name": 132,
            "confidence": 58,
            "template": 280,
            "replay": 68,
            "post_delay": 88,
            "wait_before": 96,
            "timeout": 70,
            "verify_click": 60,
            "retry_after": 58,
            "retry_template": 220,
            "retry_confidence": 78,
        }
        anchors = {
            "enabled": tk.CENTER,
            "name": tk.W,
            "confidence": tk.CENTER,
            "template": tk.W,
            "replay": tk.CENTER,
            "post_delay": tk.CENTER,
            "wait_before": tk.CENTER,
            "timeout": tk.CENTER,
            "verify_click": tk.CENTER,
            "retry_after": tk.CENTER,
            "retry_template": tk.W,
            "retry_confidence": tk.CENTER,
        }
        for column in columns:
            tree.heading(column, text=headings[column], anchor=anchors[column])
            tree.column(
                column,
                width=widths[column],
                minwidth=widths[column],
                stretch=column in {"template", "retry_template"},
                anchor=anchors[column],
            )
        tree.grid(row=0, column=0, sticky="nsew")
        y_scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        y_scrollbar.grid(row=0, column=1, sticky="ns")
        x_scrollbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        x_scrollbar.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=y_scrollbar.set, xscrollcommand=x_scrollbar.set)
        tree.tag_configure("odd", background=THEME["odd"])
        tree.tag_configure("even", background=SURFACE_BG)
        tree.tag_configure("disabled", foreground=THEME["disabled"])
        tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        tree.bind("<Button-1>", self.on_step_tree_click)
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

    def normalize_template_path(self, template_path):
        return str(template_path or "").replace("\\", "/").strip()

    def step_matches_loop_replay(self, step, loop_replay_settings):
        if not loop_replay_settings.get("enabled"):
            return False
        mode = loop_replay_settings.get("mode")
        step_name = step.get("name", "").strip().lower()
        trigger_step = loop_replay_settings.get("trigger_step", "").strip().lower()
        step_template = self.normalize_template_path(step.get("template", ""))
        trigger_template = self.normalize_template_path(loop_replay_settings.get("trigger_template", ""))
        if mode == "step":
            if trigger_step and step_name == trigger_step:
                return True
            return bool(trigger_template and step_template == trigger_template)
        if mode == "template":
            return bool(trigger_template and step_template == trigger_template)
        return False

    def find_loop_replay_step(self, loop_replay_settings=None):
        if loop_replay_settings is None:
            try:
                loop_replay_settings = self.recorder_loop_settings_snapshot()
            except ValueError:
                loop_replay_settings = None
        if loop_replay_settings:
            for index, step in enumerate(self.sequence):
                if self.step_matches_loop_replay(step, loop_replay_settings):
                    return step, index
        selected, index = self.selected_step()
        if selected is not None and self.active_group == "sequence":
            return selected, index
        return None, None

    def find_loop_replay_index(self, steps, loop_replay_settings):
        for index, step in enumerate(steps):
            if self.step_matches_loop_replay(step, loop_replay_settings):
                return index
        return None

    def replay_marker_for_step(self, step):
        try:
            loop_replay_settings = self.recorder_loop_settings_snapshot()
        except ValueError:
            return ""
        return "VIDEO" if self.step_matches_loop_replay(step, loop_replay_settings) else ""

    def refresh_one_tree(self, tree, group, steps):
        selected = tree.selection()
        previous = selected[0] if selected else None
        tree.delete(*tree.get_children())
        for index, step in enumerate(steps):
            iid = f"{group}:{index}"
            tags = ["even" if index % 2 == 0 else "odd"]
            if not step.get("enabled", True):
                tags.append("disabled")
            tree.insert(
                "",
                "end",
                iid=iid,
                tags=tuple(tags),
                values=(
                    checkbox_text(step.get("enabled", True)),
                    step.get("name", ""),
                    step.get("confidence", ""),
                    step.get("template", ""),
                    self.replay_marker_for_step(step),
                    format_delay(step.get("post_delay")),
                    format_delay(step.get("wait_before")),
                    format_optional(step.get("timeout")),
                    checkbox_text(step.get("verify_click", False)),
                    format_optional(step.get("retry_after")),
                    step.get("retry_template", ""),
                    format_optional(step.get("retry_confidence")),
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

    def on_step_tree_click(self, event):
        tree = event.widget
        if tree.identify_region(event.x, event.y) != "cell":
            return None
        if tree.identify_column(event.x) != "#1":
            return None
        iid = tree.identify_row(event.y)
        if not iid:
            return "break"
        tree.selection_set(iid)
        self.on_tree_select()
        self.toggle_selected_enabled()
        return "break"

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

    def mark_editor_dirty(self, *_args):
        if not getattr(self, "loading_editor", False):
            self.editor_dirty = True

    def clear_editor(self):
        self.loading_editor = True
        for key, var in self.edit_vars.items():
            if isinstance(var, tk.BooleanVar):
                var.set(False)
            else:
                var.set("")
        self.update_template_preview("")
        self.update_video_replay_status()
        self.editor_dirty = False
        self.loading_editor = False

    def load_editor(self, step):
        self.loading_editor = True
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
        self.update_video_replay_status(step)
        self.editor_dirty = False
        self.loading_editor = False

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
            fill=MUTED_COLOR,
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

    def on_loop_replay_option_changed(self):
        self.update_video_replay_status()
        if hasattr(self, "sequence_tree"):
            self.refresh_tree()

    def show_video_replay_detail(self, visible):
        detail = getattr(self, "video_replay_detail", None)
        if detail is None:
            return
        if visible:
            detail.grid()
        else:
            detail.grid_remove()

    def update_video_replay_status(self, step=None):
        if not hasattr(self, "video_replay_status_var"):
            return
        if step is None:
            step, _index = self.selected_step()
        if step is None:
            self.video_replay_status_var.set("Select a row to edit replay trigger")
            self.show_video_replay_detail(False)
            return
        if not self.loop_replay_enabled_var.get():
            self.video_replay_status_var.set("Replay disabled")
            self.show_video_replay_detail(False)
            return
        try:
            loop_replay_settings = self.recorder_loop_settings_snapshot()
        except ValueError:
            self.video_replay_status_var.set("Replay settings have invalid number")
            self.show_video_replay_detail(False)
            return
        is_trigger = self.step_matches_loop_replay(step, loop_replay_settings)
        self.show_video_replay_detail(is_trigger)
        if is_trigger:
            self.video_replay_status_var.set(f"This step starts replay. Offset {self.loop_replay_delay_var.get()}s")
        elif loop_replay_settings["mode"] == "step" and loop_replay_settings["trigger_step"]:
            self.video_replay_status_var.set(f"Not this row. Current VIDEO: {loop_replay_settings['trigger_step']}")
        elif loop_replay_settings["mode"] == "template":
            self.video_replay_status_var.set(
                f"Not this row. Current template: {loop_replay_settings['trigger_template'] or '-'}"
            )
        else:
            self.video_replay_status_var.set("No replay trigger set")

    def use_selected_step_as_replay_trigger(self):
        if not self.apply_edit(silent=True):
            return
        step, _index = self.selected_step()
        if step is None:
            return
        name = step.get("name", "").strip()
        if not name:
            messagebox.showerror("Missing name", "Step name is required before it can start replay.")
            return
        template = step.get("template", "").strip()
        self.loop_replay_enabled_var.set(True)
        self.loop_replay_mode_var.set("step")
        self.loop_replay_step_var.set(name)
        self.loop_replay_template_var.set(template)
        self.loop_replay_confidence_var.set(str(step.get("confidence", DEFAULT_LOOP_REPLAY_TRIGGER_CONFIDENCE)))
        self.update_video_replay_status(step)
        self.refresh_tree()
        self.match_summary_var.set(f"Video trigger: {name}")

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

    def apply_edit(self, silent=False):
        step, _index = self.selected_step()
        if step is None:
            messagebox.showinfo("No selection", "Select a step first.")
            return False
        try:
            old_name = step.get("name", "").strip()
            old_template = self.normalize_template_path(step.get("template", ""))
            old_loop_settings = self.recorder_loop_settings_snapshot()
            was_replay_trigger = self.step_matches_loop_replay(step, old_loop_settings)
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
        if was_replay_trigger:
            new_name = step.get("name", "").strip()
            new_template = self.normalize_template_path(step.get("template", ""))
            if old_name and old_loop_settings["trigger_step"].strip() == old_name:
                self.loop_replay_step_var.set(new_name)
            if old_template and self.normalize_template_path(old_loop_settings["trigger_template"]) == old_template:
                self.loop_replay_template_var.set(new_template)
        self.refresh_tree()
        self.update_template_preview(step.get("template", ""))
        self.update_video_replay_status(step)
        self.editor_dirty = False
        if not silent:
            self.match_summary_var.set(f"Applied: {step['name']}")
        return True

    def ensure_editor_applied(self, action_name):
        if not self.editor_dirty:
            return True
        should_apply = messagebox.askyesno(
            "Apply step changes",
            f"Step fields were changed. Apply before {action_name}?",
        )
        if not should_apply:
            return False
        return self.apply_edit(silent=True)

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
        if not self.ensure_editor_applied("capture"):
            return
        step, _index = self.selected_step()
        if step is None:
            return
        name = step.get("name", "").strip() or "template"
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
        default_template = step.get("template", "").strip() or os.path.join("templates", f"{name}.png")
        initial_dir = os.path.abspath(os.path.dirname(default_template) or "templates")
        if not os.path.isdir(initial_dir):
            initial_dir = os.path.abspath("templates")
        initial_file = os.path.basename(default_template) or f"{name}.png"
        out_path = filedialog.asksaveasfilename(
            title="Save captured template",
            defaultextension=".png",
            filetypes=(("PNG images", "*.png"), ("All files", "*.*")),
            initialdir=initial_dir,
            initialfile=initial_file,
        )
        if not out_path:
            self.match_summary_var.set("Capture cancelled")
            return
        crop = frame[y : y + h, x : x + w]
        if not cv2.imwrite(out_path, crop):
            messagebox.showerror("Save failed", f"Could not write template: {out_path}")
            return
        try:
            out_path = os.path.relpath(out_path, os.getcwd())
        except ValueError:
            pass
        out_path = out_path.replace("\\", "/")
        step["name"] = name
        step["template"] = out_path
        step["confidence"] = safe_float(self.edit_vars["confidence"].get(), step.get("confidence", 0.85))
        self.matcher.clear(out_path)
        self.load_editor(step)
        self.refresh_tree()
        self.update_template_preview(out_path)
        self.match_summary_var.set(f"Captured: {name}")
        self.capture_log_message(f"Saved {out_path} ({w}x{h})")

    def test_selected(self):
        step, _index = self.selected_step()
        if step is None:
            messagebox.showinfo("No selection", "Select a step first.")
            return
        if not self.ensure_editor_applied("test"):
            return
        step, _index = self.selected_step()
        self.apply_adb_config()
        try:
            self.ensure_connected()
            frame = adb_client.screencap()
        except Exception as exc:
            messagebox.showerror("Test failed", str(exc))
            return
        match = self.matcher.best_match(frame, step.get("template", ""))
        if not match:
            self.match_summary_var.set(f"Test: {step.get('name')} no template")
            return
        threshold = float(step.get("confidence", 0.85))
        state = "PASS" if match["score"] >= threshold else "LOW"
        self.match_summary_var.set(f"Test {step.get('name')}: {state} {match['score']:.3f}/{threshold:.3f}")
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
                threshold = float(step.get("confidence", 0.85))
                match = self.matcher.best_match(frame, step.get("template", ""))
                score = match["score"] if match else 0.0
                result = "PASS" if score >= threshold else "LOW"
                enabled = bool(step.get("enabled", True))
                rows.append((score, enabled, result, group, step.get("name", ""), threshold))

        rows.sort(key=lambda row: (row[1], row[0]), reverse=True)
        enabled_rows = [row for row in rows if row[1]]
        top = enabled_rows[0] if enabled_rows else (rows[0] if rows else None)
        if not top:
            self.match_summary_var.set("-")
            return

        self.match_summary_var.set(f"Best: {top[4]} {top[0]:.3f}/{top[5]:.3f}")
        self.log(f"Screen test: {top[4]} {top[2]} {top[0]:.3f}/{top[5]:.3f}")

    def capture_log_message(self, message):
        if not hasattr(self, "capture_log"):
            return
        timestamp = time.strftime("%H:%M:%S")
        self.capture_log.configure(state="normal")
        self.capture_log.insert("end", f"[{timestamp}] {message}\n")
        self.capture_log.see("end")
        self.capture_log.configure(state="disabled")

    def recorder_hotkey(self, event, action):
        if self.global_recorder_hotkeys:
            return None
        if not self.recording and getattr(self.main_tabs, "current_index", None) != 3:
            return None
        focus = self.focus_get()
        if focus is not None and str(focus.winfo_class()).lower() in {"entry", "tentry", "text", "tcombobox"}:
            return None
        self.trigger_recorder_action(action)
        return "break"

    def enable_global_recorder_hotkeys(self):
        if keyboard is None or self.global_recorder_hotkeys:
            return
        try:
            handler = keyboard.hook(self.on_global_recorder_key, suppress=False)
            self.global_recorder_hotkeys = [handler]
            self.log("Global recorder keys enabled: W/S down-up, F7 mark start.")
        except Exception as exc:
            self.global_recorder_hotkeys = []
            self.log(f"Global recorder keys unavailable: {exc}")

    def disable_global_recorder_hotkeys(self):
        if keyboard is None or not self.global_recorder_hotkeys:
            return
        for hotkey in self.global_recorder_hotkeys:
            try:
                keyboard.unhook(hotkey)
            except (KeyError, ValueError):
                pass
        self.global_recorder_hotkeys = []
        self.recorder_pressed_keys.clear()

    def start_recorder_key_poll(self):
        if keyboard is None or self.recorder_poll_worker and self.recorder_poll_worker.is_alive():
            return
        self.recorder_poll_stop.clear()
        self.recorder_poll_states = {key: False for key in RECORDER_KEY_ACTIONS}
        self.recorder_poll_states["f7"] = False
        self.recorder_poll_worker = threading.Thread(target=self.recorder_key_poll_worker, daemon=True)
        self.recorder_poll_worker.start()
        self.log("Recorder key polling enabled.")

    def stop_recorder_key_poll(self):
        self.recorder_poll_stop.set()
        self.recorder_poll_states.clear()

    def recorder_key_poll_worker(self):
        while not self.recorder_poll_stop.is_set():
            if not self.recording:
                time.sleep(0.05)
                continue
            now = time.monotonic()
            try:
                for key, action in RECORDER_KEY_ACTIONS.items():
                    pressed = bool(keyboard.is_pressed(key))
                    previous = bool(self.recorder_poll_states.get(key, False))
                    if pressed and not previous:
                        self.after(0, self.record_external_key_event, key, action, "down", now)
                    elif previous and not pressed:
                        self.after(0, self.record_external_key_event, key, action, "up", now)
                    self.recorder_poll_states[key] = pressed

                f7_pressed = bool(keyboard.is_pressed("f7"))
                f7_previous = bool(self.recorder_poll_states.get("f7", False))
                if f7_pressed and not f7_previous and now - self.last_record_anchor_hotkey >= 1.0:
                    self.last_record_anchor_hotkey = now
                    self.after(0, self.mark_record_anchor)
                self.recorder_poll_states["f7"] = f7_pressed
            except Exception as exc:
                self.after(0, self.log, f"Recorder key polling stopped: {exc}")
                break
            time.sleep(0.02)

    def on_global_recorder_key(self, event):
        key = str(event.name).lower()
        event_type = str(event.event_type).lower()
        if key == "f7" and event_type == "down" and self.recording:
            now = time.monotonic()
            if now - self.last_record_anchor_hotkey < 1.0:
                return
            self.last_record_anchor_hotkey = now
            self.after(0, self.mark_record_anchor)
            return
        action = RECORDER_KEY_ACTIONS.get(key)
        if action is None or not self.recording:
            return
        event_time = time.monotonic()
        self.after(0, self.record_external_key_event, key, action, event_type, event_time)

    def recorder_settings_snapshot(self):
        try:
            input_mode = self.record_input_mode_var.get().strip().lower()
            if input_mode not in RECORDER_INPUT_MODES:
                input_mode = DEFAULT_RECORDER_INPUT_MODE
            jump_tap = (int(self.record_jump_x_var.get()), int(self.record_jump_y_var.get()))
            slide_swipe = (
                int(self.record_slide_x1_var.get()),
                int(self.record_slide_y1_var.get()),
                int(self.record_slide_x2_var.get()),
                int(self.record_slide_y2_var.get()),
                int(self.record_slide_ms_var.get()),
            )
        except ValueError as exc:
            raise ValueError("Recorder coordinates must be whole numbers.") from exc
        if slide_swipe[4] < 0:
            raise ValueError("Slide duration must be 0 or more.")
        return {
            "input_mode": input_mode,
            "jump_key": "w",
            "slide_key": "s",
            "replay_start_delay": DEFAULT_RECORDER_REPLAY_START_DELAY,
            "jump_tap": jump_tap,
            "slide_swipe": slide_swipe,
        }

    def recorder_loop_settings_snapshot(self):
        try:
            delay = float(str(self.loop_replay_delay_var.get()).strip() or DEFAULT_LOOP_REPLAY_DELAY)
            confidence = float(
                str(self.loop_replay_confidence_var.get()).strip() or DEFAULT_LOOP_REPLAY_TRIGGER_CONFIDENCE
            )
        except ValueError as exc:
            raise ValueError("Loop replay delay and confidence must be numbers.") from exc
        mode = self.loop_replay_mode_var.get().strip().lower()
        if mode not in {"step", "template"}:
            mode = DEFAULT_LOOP_REPLAY_TRIGGER_MODE
        return {
            "enabled": bool(self.loop_replay_enabled_var.get()),
            "mode": mode,
            "trigger_step": self.loop_replay_step_var.get().strip(),
            "trigger_template": self.loop_replay_template_var.get().strip(),
            "trigger_confidence": max(0.0, min(1.0, confidence)),
            "file": self.loop_replay_file_var.get().strip(),
            "delay": delay,
            "tap_trigger": bool(self.loop_replay_tap_trigger_var.get()),
        }

    def set_recorder_config_vars(self):
        jump_tap = config_tuple("RECORDER_JUMP_TAP", DEFAULT_JUMP_TAP, 2)
        slide_swipe = config_tuple("RECORDER_SLIDE_SWIPE", DEFAULT_SLIDE_SWIPE, 5)
        input_mode = getattr(config, "RECORDER_INPUT_MODE", DEFAULT_RECORDER_INPUT_MODE)
        if input_mode not in RECORDER_INPUT_MODES:
            input_mode = DEFAULT_RECORDER_INPUT_MODE
        self.record_input_mode_var.set(input_mode)
        self.record_jump_x_var.set(str(jump_tap[0]))
        self.record_jump_y_var.set(str(jump_tap[1]))
        self.record_slide_x1_var.set(str(slide_swipe[0]))
        self.record_slide_y1_var.set(str(slide_swipe[1]))
        self.record_slide_x2_var.set(str(slide_swipe[2]))
        self.record_slide_y2_var.set(str(slide_swipe[3]))
        self.record_slide_ms_var.set(str(slide_swipe[4]))
        self.loop_replay_enabled_var.set(
            bool(getattr(config, "RECORDER_LOOP_REPLAY_ENABLED", DEFAULT_LOOP_REPLAY_ENABLED))
        )
        self.loop_replay_mode_var.set(str(getattr(config, "RECORDER_LOOP_TRIGGER_MODE", DEFAULT_LOOP_REPLAY_TRIGGER_MODE)))
        self.loop_replay_step_var.set(str(getattr(config, "RECORDER_LOOP_TRIGGER_STEP", DEFAULT_LOOP_REPLAY_TRIGGER_STEP)))
        self.loop_replay_template_var.set(
            str(getattr(config, "RECORDER_LOOP_TRIGGER_TEMPLATE", DEFAULT_LOOP_REPLAY_TRIGGER_TEMPLATE))
        )
        self.loop_replay_confidence_var.set(
            str(getattr(config, "RECORDER_LOOP_TRIGGER_CONFIDENCE", DEFAULT_LOOP_REPLAY_TRIGGER_CONFIDENCE))
        )
        self.loop_replay_file_var.set(str(getattr(config, "RECORDER_LOOP_REPLAY_FILE", DEFAULT_LOOP_REPLAY_FILE)))
        self.loop_replay_delay_var.set(str(getattr(config, "RECORDER_LOOP_REPLAY_DELAY", DEFAULT_LOOP_REPLAY_DELAY)))
        self.loop_replay_tap_trigger_var.set(
            bool(getattr(config, "RECORDER_LOOP_TAP_TRIGGER", DEFAULT_LOOP_REPLAY_TAP_TRIGGER))
        )

    def crop_record_anchor(self, frame):
        h, w = frame.shape[:2]
        left, top, right, bottom = RECORD_ANCHOR_CROP
        x1 = max(0, min(w - 1, int(w * left)))
        y1 = max(0, min(h - 1, int(h * top)))
        x2 = max(x1 + 1, min(w, int(w * right)))
        y2 = max(y1 + 1, min(h, int(h * bottom)))
        return frame[y1:y2, x1:x2], (x1, y1, x2 - x1, y2 - y1)

    def mark_record_anchor(self):
        if self.record_anchor_marking:
            return
        if not self.recording or self.record_start_time is None:
            messagebox.showinfo("Recorder not running", "Start recording first, then press F7 at the real game start.")
            return
        self.record_anchor_marking = True
        try:
            self.apply_adb_config()
            self.ensure_connected()
            frame = adb_client.screencap()
            crop, (_x, _y, w, h) = self.crop_record_anchor(frame)
            os.makedirs("templates", exist_ok=True)
            out_path = DEFAULT_RECORD_ANCHOR_TEMPLATE
            if not cv2.imwrite(out_path, crop):
                raise RuntimeError(f"Could not write anchor template: {out_path}")

            old_anchor = float(self.record_anchor_offset)
            new_anchor = max(0.0, time.monotonic() - self.record_start_time)
            shifted_events = []
            for event in self.record_events:
                event_time = float(event.get("t", 0.0)) + old_anchor
                shifted_time = round(event_time - new_anchor, 3)
                if shifted_time >= -0.025:
                    shifted_events.append({**event, "t": max(0.0, shifted_time)})
            self.record_events = shifted_events
            self.record_anchor_offset = new_anchor

            try:
                relative_path = os.path.relpath(out_path, os.getcwd())
            except ValueError:
                relative_path = out_path
            relative_path = relative_path.replace("\\", "/")
            self.record_anchor_template = relative_path

            step, _index = self.find_loop_replay_step()
            if step is not None:
                step["template"] = relative_path
                step["confidence"] = min(float(step.get("confidence", 0.8)), 0.78)
                self.loop_replay_step_var.set(step.get("name", ""))
                self.loop_replay_confidence_var.set(str(step.get("confidence", 0.78)))
            self.loop_replay_enabled_var.set(True)
            self.loop_replay_mode_var.set("step")
            self.loop_replay_template_var.set(relative_path)
            self.loop_replay_delay_var.set("0.0")
            self.loop_replay_tap_trigger_var.set(False)
            self.matcher.clear(relative_path)

            self.record_anchor_var.set(f"{new_anchor:.2f}s")
            self.refresh_record_tree()
            self.refresh_tree()
            if step is not None:
                self.update_video_replay_status(step)
                selected, _selected_index = self.selected_step()
                if selected is step:
                    self.load_editor(step)
            self.log(f"Game start anchor marked at {new_anchor:.2f}s: {relative_path} ({w}x{h})")
        except Exception as exc:
            messagebox.showerror("Anchor failed", str(exc))
        finally:
            self.record_anchor_marking = False

    def start_recording(self):
        if self.recording:
            self.log("Recorder is already running.")
            return
        try:
            settings = self.recorder_settings_snapshot()
        except ValueError as exc:
            messagebox.showerror("Invalid recorder input", str(exc))
            return
        self.apply_adb_config()
        if settings["input_mode"] == "adb":
            try:
                self.ensure_connected()
            except Exception as exc:
                messagebox.showerror("Record failed", str(exc))
                return
        self.stop_replay(silent=True)
        self.record_events = []
        self.record_overlays = []
        self.record_anchor_offset = 0.0
        self.record_anchor_template = DEFAULT_RECORD_ANCHOR_TEMPLATE
        self.record_anchor_var.set("-")
        self.refresh_record_tree()
        self.record_start_time = time.monotonic()
        self.recording = True
        self.record_state_var.set("Recording")
        self.status_var.set("Recording")
        self.record_elapsed_var.set("0.00s")
        self.enable_global_recorder_hotkeys()
        self.start_recorder_key_poll()
        self.log("Recording started. Use W=Jump, S=Slide, F7=mark real game start.")
        self.refresh_record_timer()

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        stop_time = time.monotonic()
        for key, action in list(self.recorder_pressed_keys.items()):
            self.append_record_event(action, "up", stop_time)
            self.recorder_pressed_keys.pop(key, None)
        self.disable_global_recorder_hotkeys()
        self.stop_recorder_key_poll()
        self.record_state_var.set("Idle")
        if self.status_var.get() == "Recording":
            self.status_var.set("Idle")
        self.log(f"Recording stopped with {len(self.record_events)} event(s).")
        if not self.record_events:
            self.log("No W/S events recorded after anchor. Press W/S after F7, or run the GUI as Administrator if keys are not detected.")

    def refresh_record_timer(self):
        if not self.recording or self.record_start_time is None:
            return
        elapsed = time.monotonic() - self.record_start_time
        self.record_elapsed_var.set(f"{elapsed:.2f}s")
        self.after(100, self.refresh_record_timer)

    def trigger_recorder_action(self, action, send_live=True):
        if action not in RECORDER_ACTIONS:
            return
        try:
            settings = self.recorder_settings_snapshot()
        except ValueError as exc:
            messagebox.showerror("Invalid recorder input", str(exc))
            return
        event_time = time.monotonic()
        if self.recording:
            self.append_record_event(action, "tap", event_time)
        if send_live:
            self.recorder_adb_queue.put(({"action": action, "event": "tap"}, settings))

    def record_external_key(self, action):
        if not self.recording:
            return
        self.trigger_recorder_action(action, send_live=False)

    def record_external_key_event(self, key, action, event_type, event_time):
        if not self.recording:
            return
        if event_type == "down":
            if key in self.recorder_pressed_keys:
                return
            self.recorder_pressed_keys[key] = action
            self.append_record_event(action, "down", event_time)
        elif event_type == "up":
            if key not in self.recorder_pressed_keys:
                return
            self.recorder_pressed_keys.pop(key, None)
            self.append_record_event(action, "up", event_time)

    def append_record_event(self, action, event_type, event_time):
        if self.record_start_time is None:
            return
        offset = event_time - self.record_start_time - self.record_anchor_offset
        if offset < -0.025:
            return
        offset = max(0.0, offset)
        self.record_events.append({"t": round(offset, 3), "action": action, "event": event_type})
        self.refresh_record_tree()
        event_count = len(self.record_events)
        if event_count <= 6 or event_count % 100 == 0:
            self.log(f"Recorded {action} {event_type} at {offset:.3f}s ({event_count} events)")

    def refresh_record_tree(self):
        if not hasattr(self, "record_tree"):
            return
        self.record_tree.delete(*self.record_tree.get_children())
        for index, event in enumerate(self.record_events, start=1):
            tags = ("even" if index % 2 == 0 else "odd",)
            self.record_tree.insert(
                "",
                "end",
                tags=tags,
                values=(
                    index,
                    f"{float(event.get('t', 0.0)):.3f}s",
                    str(event.get("action", "")).title(),
                    str(event.get("event", "tap")).upper(),
                ),
            )
        self.record_count_var.set(f"{len(self.record_events)} events")

    def recorder_sender_worker(self):
        while not self.recorder_sender_stop.is_set():
            try:
                command, settings = self.recorder_adb_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self.send_recorder_command(command, settings)
            except Exception as exc:
                self.after(0, self.log, f"Recorder input failed: {exc}")
            finally:
                self.recorder_adb_queue.task_done()

    def send_recorder_command(self, command, settings):
        action = str(command.get("action", "")).lower()
        event_type = str(command.get("event", "tap")).lower()
        with self.recorder_adb_lock:
            if settings.get("input_mode") == "keyboard":
                if keyboard is None:
                    raise RuntimeError("keyboard package is unavailable; switch recorder mode to adb.")
                key = settings["jump_key"] if action == "jump" else settings["slide_key"]
                if event_type == "down":
                    keyboard.press(key)
                elif event_type == "up":
                    keyboard.release(key)
                elif event_type == "hold":
                    keyboard.press(key)
                    time.sleep(max(0.0, float(command.get("duration", 0.0))))
                    keyboard.release(key)
                else:
                    keyboard.press_and_release(key)
            elif action == "jump":
                x, y = settings["jump_tap"]
                adb_client.tap(x, y)
            elif action == "slide":
                x1, y1, x2, y2, duration_ms = settings["slide_swipe"]
                if event_type == "up":
                    return
                duration_ms = int(command.get("duration_ms", duration_ms))
                adb_client.swipe(x1, y1, x2, y2, duration_ms)
            else:
                raise ValueError(f"Unknown recorder action: {action}")

    def play_recording(self):
        if self.record_replay_worker and self.record_replay_worker.is_alive():
            self.log("Recording replay is already running.")
            return
        if not self.record_events:
            messagebox.showinfo("No recording", "Record or load a jump/slide timeline first.")
            return
        try:
            settings = self.recorder_settings_snapshot()
        except ValueError as exc:
            messagebox.showerror("Invalid recorder input", str(exc))
            return
        self.apply_adb_config()
        if settings["input_mode"] == "adb":
            try:
                self.ensure_connected()
            except Exception as exc:
                messagebox.showerror("Replay failed", str(exc))
                return
        self.stop_recording()
        self.replay_stop_event.clear()
        settings["replay_overlays"] = [dict(overlay) for overlay in self.record_overlays]
        events = [dict(event) for event in self.record_events]
        self.record_replay_worker = threading.Thread(
            target=self.replay_recording_worker,
            args=(events, settings, False),
            daemon=True,
        )
        self.record_replay_worker.start()

    def prepare_replay_commands(self, events, settings):
        events = sorted((dict(event) for event in events), key=lambda event: float(event.get("t", 0.0)))
        if settings.get("input_mode") != "adb":
            commands = []
            for event in events:
                action = str(event.get("action", "")).lower()
                event_type = str(event.get("event", "tap")).lower()
                if action in RECORDER_ACTIONS and event_type in RECORDER_EVENT_TYPES:
                    commands.append({**event, "event": event_type})
            return commands

        commands = []
        open_down = {}
        default_slide_ms = int(settings["slide_swipe"][4])
        for event in events:
            action = str(event.get("action", "")).lower()
            event_type = str(event.get("event", "tap")).lower()
            if action not in RECORDER_ACTIONS:
                continue
            try:
                event_time = float(event.get("t", 0.0))
            except (TypeError, ValueError):
                continue
            if event_type == "tap":
                commands.append({"t": event_time, "action": action, "event": "tap"})
            elif event_type == "down":
                if action == "jump":
                    commands.append({"t": event_time, "action": "jump", "event": "tap"})
                else:
                    open_down[action] = event_time
            elif event_type == "up":
                down_time = open_down.pop(action, None)
                if action == "slide" and down_time is not None:
                    duration = max(0.03, event_time - down_time)
                    commands.append(
                        {
                            "t": down_time,
                            "action": "slide",
                            "event": "hold",
                            "duration": duration,
                            "duration_ms": int(duration * 1000),
                        }
                    )
        for action, down_time in open_down.items():
            if action == "slide":
                commands.append(
                    {
                        "t": down_time,
                        "action": "slide",
                        "event": "hold",
                        "duration": default_slide_ms / 1000.0,
                        "duration_ms": default_slide_ms,
                    }
                )
        commands.sort(key=lambda command: float(command.get("t", 0.0)))
        return commands

    def replay_recording_worker(self, events, settings, from_loop=False):
        stopped = False
        self.after(0, self.record_state_var.set, "Playing")
        if not from_loop:
            self.after(0, self.status_var.set, "Playing Rec")
        commands = self.prepare_replay_commands(events, settings)
        start_delay = settings.get("replay_start_delay", 0.0) if settings.get("input_mode") == "keyboard" else 0.0
        if start_delay:
            self.after(0, self.log, f"Replay starts in {start_delay:.0f}s. Focus LDPlayer now.")
            delay_until = time.monotonic() + start_delay
            while not self.replay_stop_event.is_set() and time.monotonic() < delay_until:
                time.sleep(0.05)
        if self.replay_stop_event.is_set():
            stopped = True
            self.after(0, self.record_state_var.set, "Stopped")
            if not from_loop:
                self.after(0, self.status_var.set, "Idle")
            self.after(0, self.log, "Replay stopped.")
            return
        self.after(0, self.log, f"Replay started: {len(commands)} event(s).")
        started_at = time.monotonic()
        overlay_stop_event = threading.Event()
        overlay_worker = None
        overlays = settings.get("replay_overlays") or []
        if overlays and settings.get("input_mode") == "adb":
            overlay_worker = threading.Thread(
                target=self.replay_overlay_worker,
                args=(overlays, settings, started_at, overlay_stop_event),
                daemon=True,
            )
            overlay_worker.start()
            self.after(0, self.log, f"Replay overlay armed: {len(overlays)} template(s).")
        try:
            for index, command in enumerate(commands, start=1):
                target_time = started_at + float(command.get("t", 0.0))
                while not self.replay_stop_event.is_set():
                    remaining = target_time - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(0.02, remaining))
                if self.replay_stop_event.is_set():
                    stopped = True
                    break
                self.send_recorder_command(command, settings)
                self.after(0, self.record_elapsed_var.set, f"{float(command.get('t', 0.0)):.2f}s")
                self.after(0, self.record_count_var.set, f"{index}/{len(commands)}")
        except Exception as exc:
            self.after(0, self.log, f"Replay failed: {exc}")
        finally:
            state = "Stopped" if stopped else "Idle"
            self.after(0, self.record_state_var.set, state)
            if not from_loop:
                self.after(0, self.status_var.set, "Idle")
            overlay_stop_event.set()
            if overlay_worker:
                overlay_worker.join(timeout=0.5)
            self.after(0, self.refresh_record_tree)
            self.after(0, self.log, "Replay stopped." if stopped else "Replay finished.")

    def replay_overlay_worker(self, overlays, settings, started_at, overlay_stop_event):
        fired = set()
        last_tap = {}
        last_error = 0.0
        while not self.replay_stop_event.is_set() and not overlay_stop_event.is_set():
            elapsed = time.monotonic() - started_at
            active_overlays = []
            next_scan = DEFAULT_REPLAY_OVERLAY_SCAN_INTERVAL
            for index, overlay in enumerate(overlays):
                if overlay.get("once", True) and index in fired:
                    continue
                start_at = overlay.get("start")
                end_at = overlay.get("end")
                if start_at is not None and elapsed < float(start_at):
                    continue
                if end_at is not None and elapsed > float(end_at):
                    continue
                active_overlays.append((index, overlay))
                next_scan = min(next_scan, float(overlay.get("scan_interval", DEFAULT_REPLAY_OVERLAY_SCAN_INTERVAL)))

            if not active_overlays:
                time.sleep(0.1)
                continue

            try:
                frame = adb_client.screencap()
            except Exception as exc:
                now = time.monotonic()
                if now - last_error >= 3.0:
                    self.after(0, self.log, f"Replay overlay capture failed: {exc}")
                    last_error = now
                time.sleep(0.2)
                continue

            for index, overlay in active_overlays:
                now = time.monotonic()
                cooldown = float(overlay.get("cooldown", 1.5))
                if now - last_tap.get(index, 0.0) < cooldown:
                    continue
                confidence = float(overlay.get("confidence", 0.85))
                match = self.matcher.find(frame, overlay.get("template", ""), confidence)
                if not match:
                    continue
                margin = min(settings["jitter"], match["w"] // 2 - 1, match["h"] // 2 - 1)
                margin = max(margin, 0)
                cx = match["x"] + match["w"] // 2 + random.randint(-margin, margin)
                cy = match["y"] + match["h"] // 2 + random.randint(-margin, margin)
                with self.recorder_adb_lock:
                    adb_client.tap(cx, cy)
                last_tap[index] = now
                if overlay.get("once", True):
                    fired.add(index)
                self.after(
                    0,
                    self.log,
                    f"Replay overlay tapped: {overlay.get('name', 'overlay')} ({match['score']:.3f}/{confidence:.3f})",
                )
                break

            time.sleep(next_scan)

    def stop_replay(self, silent=False):
        self.replay_stop_event.set()
        if not silent:
            self.log("Stopping replay.")

    def save_recording(self):
        if not self.record_events:
            messagebox.showinfo("No recording", "Record at least one jump or slide first.")
            return
        try:
            settings = self.recorder_settings_snapshot()
        except ValueError as exc:
            messagebox.showerror("Invalid recorder input", str(exc))
            return
        os.makedirs("recordings", exist_ok=True)
        path = filedialog.asksaveasfilename(
            title="Save recording",
            defaultextension=".json",
            filetypes=(("Recording JSON", "*.json"), ("All files", "*.*")),
            initialdir=os.path.abspath("recordings"),
            initialfile=os.path.basename(DEFAULT_RECORDING_FILE),
        )
        if not path:
            return
        payload = {
            "version": 2,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "adb_serial": self.adb_serial_var.get().strip(),
            "input_mode": settings["input_mode"],
            "jump_tap": settings["jump_tap"],
            "slide_swipe": settings["slide_swipe"],
            "anchor_offset": self.record_anchor_offset,
            "anchor_template": self.record_anchor_template,
            "replay_overlays": self.record_overlays,
            "events": self.record_events,
        }
        try:
            with open(path, "w", encoding="utf-8") as file:
                json.dump(payload, file, indent=2)
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        try:
            loop_path = os.path.relpath(path, os.getcwd())
        except ValueError:
            loop_path = path
        self.loop_replay_file_var.set(loop_path.replace("\\", "/"))
        self.log(f"Recording saved: {os.path.basename(path)}")

    def load_recording(self):
        os.makedirs("recordings", exist_ok=True)
        path = filedialog.askopenfilename(
            title="Load recording",
            filetypes=(("Recording JSON", "*.json"), ("All files", "*.*")),
            initialdir=os.path.abspath("recordings"),
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as file:
                payload = json.load(file)
            events = self.parse_recording_events(payload)
            overlays = self.parse_replay_overlays(payload)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("Load failed", str(exc))
            return
        self.record_events = events
        self.record_overlays = overlays
        self.record_anchor_offset = float(payload.get("anchor_offset", 0.0)) if isinstance(payload, dict) else 0.0
        self.record_anchor_template = (
            str(payload.get("anchor_template", DEFAULT_RECORD_ANCHOR_TEMPLATE))
            if isinstance(payload, dict)
            else DEFAULT_RECORD_ANCHOR_TEMPLATE
        )
        self.record_anchor_var.set(f"{self.record_anchor_offset:.2f}s" if self.record_anchor_offset else "-")
        self.record_elapsed_var.set(f"{events[-1]['t']:.2f}s" if events else "0.00s")
        try:
            loop_path = os.path.relpath(path, os.getcwd())
        except ValueError:
            loop_path = path
        self.loop_replay_file_var.set(loop_path.replace("\\", "/"))
        self.refresh_record_tree()
        self.set_overlay_editor()
        self.log(f"Recording loaded: {os.path.basename(path)}")

    def parse_recording_events(self, payload):
        raw_events = payload.get("events") if isinstance(payload, dict) else payload
        if not isinstance(raw_events, list):
            raise ValueError("Recording file has no events list.")
        events = []
        for raw_event in raw_events:
            if not isinstance(raw_event, dict):
                continue
            action = str(raw_event.get("action", "")).lower()
            if action not in RECORDER_ACTIONS:
                continue
            event_type = str(raw_event.get("event", "tap")).lower()
            if event_type not in RECORDER_EVENT_TYPES:
                event_type = "tap"
            try:
                offset = float(raw_event.get("t", 0.0))
            except (TypeError, ValueError):
                continue
            events.append({"t": round(max(0.0, offset), 3), "action": action, "event": event_type})
        events.sort(key=lambda event: event["t"])
        return events

    def parse_replay_overlays(self, payload):
        if not isinstance(payload, dict):
            return []
        raw_overlays = payload.get("replay_overlays", [])
        if not isinstance(raw_overlays, list):
            return []
        overlays = []
        for raw_overlay in raw_overlays:
            if not isinstance(raw_overlay, dict):
                continue
            template = str(raw_overlay.get("template", "")).strip()
            if not template:
                continue
            try:
                confidence = float(raw_overlay.get("confidence", 0.85))
                cooldown = float(raw_overlay.get("cooldown", 1.5))
                scan_interval = float(raw_overlay.get("scan_interval", DEFAULT_REPLAY_OVERLAY_SCAN_INTERVAL))
                start_at = raw_overlay.get("start")
                end_at = raw_overlay.get("end")
                start_at = None if start_at is None or str(start_at).strip() == "" else float(start_at)
                end_at = None if end_at is None or str(end_at).strip() == "" else float(end_at)
            except (TypeError, ValueError):
                continue
            overlays.append(
                {
                    "name": str(raw_overlay.get("name", "overlay")).strip() or "overlay",
                    "template": template,
                    "confidence": max(0.0, min(1.0, confidence)),
                    "once": parse_bool(raw_overlay.get("once", True)),
                    "cooldown": max(0.0, cooldown),
                    "scan_interval": max(0.03, scan_interval),
                    "start": start_at,
                    "end": end_at,
                }
            )
        return overlays

    def apply_loaded_recorder_points(self, payload):
        input_mode = payload.get("input_mode")
        if input_mode in RECORDER_INPUT_MODES:
            self.record_input_mode_var.set(input_mode)
        jump_tap = payload.get("jump_tap")
        slide_swipe = payload.get("slide_swipe")
        if isinstance(jump_tap, list) and len(jump_tap) == 2:
            self.record_jump_x_var.set(str(int(jump_tap[0])))
            self.record_jump_y_var.set(str(int(jump_tap[1])))
        if isinstance(slide_swipe, list) and len(slide_swipe) == 5:
            self.record_slide_x1_var.set(str(int(slide_swipe[0])))
            self.record_slide_y1_var.set(str(int(slide_swipe[1])))
            self.record_slide_x2_var.set(str(int(slide_swipe[2])))
            self.record_slide_y2_var.set(str(int(slide_swipe[3])))
            self.record_slide_ms_var.set(str(int(slide_swipe[4])))

    def load_recording_payload(self, path):
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
        return payload, self.parse_recording_events(payload)

    def should_start_loop_replay(self, step, loop_replay_settings):
        if not loop_replay_settings["enabled"] or loop_replay_settings["mode"] != "step":
            return False
        return self.step_matches_loop_replay(step, loop_replay_settings)

    def should_start_loop_replay_for_frame(self, frame, loop_replay_settings):
        if not loop_replay_settings["enabled"] or loop_replay_settings["mode"] != "template":
            return False
        if self.record_replay_worker and self.record_replay_worker.is_alive():
            return False
        template_path = loop_replay_settings["trigger_template"].strip()
        if not template_path:
            return False
        match = self.matcher.best_match(frame, template_path)
        if not match:
            return False
        score = float(match["score"])
        threshold = float(loop_replay_settings["trigger_confidence"])
        if score >= threshold:
            self.after(0, self.match_summary_var.set, f"Replay trigger: {score:.3f}/{threshold:.3f}")
            return True
        return False

    def find_replay_exit_match(self, frame, sequence, loop_replay_settings):
        trigger_index = self.find_loop_replay_index(sequence, loop_replay_settings)
        if trigger_index is None:
            return None
        for index in range(trigger_index + 1, len(sequence)):
            step = sequence[index]
            if not step.get("enabled", True):
                continue
            step_name = step.get("name", "").strip().lower()
            template_name = os.path.basename(step.get("template", "")).strip().lower()
            is_result_step = (
                "ok" in step_name
                or "confirm" in step_name
                or "open all" in step_name
                or "result" in step_name
                or template_name.startswith("end")
            )
            if not is_result_step:
                continue
            match = self.matcher.find(
                frame,
                step.get("template", ""),
                float(step.get("confidence", 0.85)),
            )
            if match:
                return index, step, match
        return None

    def start_loop_replay(self, loop_replay_settings):
        if self.record_replay_worker and self.record_replay_worker.is_alive():
            return
        path = loop_replay_settings["file"]
        if not path:
            self.threadsafe_log("Loop replay skipped: no recording file.")
            return
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        if not os.path.exists(path):
            self.threadsafe_log(f"Loop replay skipped: file not found {loop_replay_settings['file']}")
            return
        try:
            payload, events = self.load_recording_payload(path)
            settings = self.recorder_settings_snapshot()
        except Exception as exc:
            self.threadsafe_log(f"Loop replay failed to load: {exc}")
            return
        settings["input_mode"] = "adb"
        settings["replay_start_delay"] = 0.0
        settings["replay_overlays"] = self.parse_replay_overlays(payload)
        if settings["replay_overlays"]:
            overlay_labels = ", ".join(
                f"{overlay.get('name', 'overlay')} -> {overlay.get('template', '-')}"
                for overlay in settings["replay_overlays"]
            )
            self.threadsafe_log(f"Loop replay overlays loaded: {overlay_labels}")
        else:
            self.threadsafe_log(f"Loop replay overlays: none in {os.path.basename(path)}")
        if loop_replay_settings["delay"]:
            events = [
                {**event, "t": max(0.0, round(float(event.get("t", 0.0)) + loop_replay_settings["delay"], 3))}
                for event in events
            ]
        self.replay_stop_event.clear()
        self.record_replay_worker = threading.Thread(
            target=self.replay_recording_worker,
            args=(events, settings, True),
            daemon=True,
        )
        self.record_replay_worker.start()
        self.threadsafe_log(f"Loop replay started: {os.path.basename(path)}")

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
        try:
            settings = self.settings_snapshot()
            loop_replay_settings = self.recorder_loop_settings_snapshot()
        except ValueError as exc:
            messagebox.showerror("Invalid value", str(exc))
            return
        self.stop_event.clear()
        self.pause_event.clear()
        self.matcher.clear()
        self.status_var.set("Running")
        self.worker = threading.Thread(
            target=self.loop_worker,
            args=(sequence, interrupts, settings, loop_replay_settings),
            daemon=True,
        )
        self.worker.start()

    def stop_loop(self):
        self.stop_event.set()
        self.pause_event.clear()
        self.stop_recording()
        self.stop_replay(silent=True)
        self.status_var.set("Killing")
        self.log("Killing loop...")

    def loop_worker(self, sequence, interrupts, settings, loop_replay_settings):
        try:
            self.ensure_connected()
            self.threadsafe_log("Loop started.")
            seq_index = 0
            step_wait_start = time.monotonic()
            wait_target = None
            last_capture_error = 0.0
            last_step_log_key = None

            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    time.sleep(0.2)
                    continue

                try:
                    frame = adb_client.screencap()
                except Exception as exc:
                    now = time.monotonic()
                    if now - last_capture_error >= 3.0:
                        self.threadsafe_log(f"Screen capture failed: {exc}")
                        last_capture_error = now
                    self.sleep_interruptible(settings["scan_interval"])
                    continue

                if self.record_replay_worker and self.record_replay_worker.is_alive():
                    replay_exit = self.find_replay_exit_match(frame, sequence, loop_replay_settings)
                    if replay_exit:
                        exit_index, exit_step, exit_match = replay_exit
                        exit_name = exit_step.get("name", "step")
                        self.threadsafe_log(f"Replay interrupted by step: {exit_name}")
                        self.stop_replay(silent=True)
                        self.click_match(exit_match, exit_name, settings)
                        seq_index, step_wait_start, wait_target = self.advance_step(sequence, exit_index)
                        last_step_log_key = None
                        self.human_delay(exit_step.get("post_delay"), settings)
                        continue
                    self.sleep_interruptible(settings["scan_interval"])
                    continue

                if self.should_start_loop_replay_for_frame(frame, loop_replay_settings):
                    self.threadsafe_log(f"Start replay from screen trigger: {loop_replay_settings['trigger_template']}")
                    self.start_loop_replay(loop_replay_settings)
                    trigger_index = self.find_loop_replay_index(sequence, loop_replay_settings)
                    if trigger_index is not None and trigger_index >= seq_index:
                        seq_index, step_wait_start, wait_target = self.advance_step(sequence, trigger_index)
                        last_step_log_key = None
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
                        self.threadsafe_log(f"Interrupt: {interrupt.get('name', 'interrupt')}")
                        self.click_match(match, interrupt.get("name", "interrupt"), settings)
                        clicked_interrupt = True
                        break

                if clicked_interrupt:
                    self.human_delay(None, settings)
                    continue

                step = sequence[seq_index]
                step_name = step.get("name", "-")
                self.after(0, self.current_step_var.set, step_name)
                step_log_key = (seq_index, step_name, bool(step.get("enabled", True)))
                if step_log_key != last_step_log_key:
                    status = "Step" if step.get("enabled", True) else "Skip disabled step"
                    self.threadsafe_log(f"{status}: {step_name}")
                    last_step_log_key = step_log_key
                if not step.get("enabled", True):
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
                    replay_triggered = self.should_start_loop_replay(step, loop_replay_settings)
                    should_tap_step = not replay_triggered or loop_replay_settings.get("tap_trigger", True)
                    self.threadsafe_log(
                        f"Matched step: {step_name} ({match['score']:.3f}/{threshold:.3f})"
                    )
                    if should_tap_step:
                        self.click_match(match, step.get("name", "step"), settings)
                    if replay_triggered:
                        self.threadsafe_log(f"Start replay at step: {step_name}")
                        self.start_loop_replay(loop_replay_settings)
                    if should_tap_step and step.get("verify_click"):
                        self.verify_and_retap(step, settings)
                    seq_index, step_wait_start, wait_target = self.advance_step(sequence, seq_index)
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
        self.sleep_interruptible(delay)

    def sleep_interruptible(self, seconds):
        end = time.monotonic() + max(0.0, float(seconds))
        while not self.stop_event.is_set() and time.monotonic() < end:
            if self.pause_event.is_set():
                time.sleep(0.1)
            else:
                time.sleep(min(0.1, end - time.monotonic()))

    def save_config_file(self):
        if self.active_tree().selection() and not self.apply_edit(silent=True):
            return
        try:
            settings = self.settings_snapshot()
            recorder_settings = self.recorder_settings_snapshot()
            loop_replay_settings = self.recorder_loop_settings_snapshot()
        except ValueError as exc:
            messagebox.showerror("Invalid value", str(exc))
            return
        updates = {
            "SEQUENCE": [compact_step(step) for step in self.sequence],
            "INTERRUPTS": [compact_step(step) for step in self.interrupts],
            "UI_THEME": CURRENT_UI_THEME,
            "ADB_PATH": self.adb_path_var.get().strip(),
            "ADB_SERIAL": self.adb_serial_var.get().strip(),
            "SCAN_INTERVAL": settings["scan_interval"],
            "MIN_CLICK_DELAY": settings["min_delay"],
            "MAX_CLICK_DELAY": settings["max_delay"],
            "CLICK_JITTER_PX": settings["jitter"],
            "CLICK_RETRY_LIMIT": settings["retry_limit"],
            "CLICK_VERIFY_DELAY": settings["verify_delay"],
            "RECORDER_INPUT_MODE": recorder_settings["input_mode"],
            "RECORDER_JUMP_TAP": recorder_settings["jump_tap"],
            "RECORDER_SLIDE_SWIPE": recorder_settings["slide_swipe"],
            "RECORDER_LOOP_REPLAY_ENABLED": loop_replay_settings["enabled"],
            "RECORDER_LOOP_TRIGGER_MODE": loop_replay_settings["mode"],
            "RECORDER_LOOP_TRIGGER_STEP": loop_replay_settings["trigger_step"],
            "RECORDER_LOOP_TRIGGER_TEMPLATE": loop_replay_settings["trigger_template"],
            "RECORDER_LOOP_TRIGGER_CONFIDENCE": loop_replay_settings["trigger_confidence"],
            "RECORDER_LOOP_REPLAY_FILE": loop_replay_settings["file"],
            "RECORDER_LOOP_REPLAY_DELAY": loop_replay_settings["delay"],
            "RECORDER_LOOP_TAP_TRIGGER": loop_replay_settings["tap_trigger"],
        }
        try:
            write_config_assignments(CONFIG_PATH, updates)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        self.apply_config_updates(updates)
        self.refresh_tree()
        self.log("Config saved.")

    def load_config_file(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Loop running", "Kill the loop before reloading config.")
            return
        try:
            global config
            config = config_loader.reload_config()
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
        self.ui_theme_var.set(str(getattr(config, "UI_THEME", DEFAULT_UI_THEME)))
        self.apply_theme_selection()
        self.set_recorder_config_vars()
        self.sequence = [step_defaults(step) for step in copy.deepcopy(config.SEQUENCE)]
        self.interrupts = [step_defaults(step) for step in copy.deepcopy(config.INTERRUPTS)]
        self.matcher.clear()
        self.refresh_tree()
        self.clear_editor()
        self.log("Config reloaded.")

    def apply_config_updates(self, updates):
        for name, value in updates.items():
            setattr(config, name, value)

    def log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        line_count = int(float(self.log_text.index("end-1c").split(".")[0]))
        if line_count > MAX_LOG_LINES:
            self.log_text.delete("1.0", f"{line_count - MAX_LOG_LINES + 1}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def threadsafe_log(self, message):
        self.after(0, self.log, message)

    def on_close(self):
        self.recorder_sender_stop.set()
        self.stop_recorder_key_poll()
        self.disable_global_recorder_hotkeys()
        self.stop_replay(silent=True)
        self.stop_recording()
        if self.worker and self.worker.is_alive():
            self.stop_loop()
            self.after(300, self.destroy)
        else:
            self.destroy()


if __name__ == "__main__":
    app = BotApp()
    app.mainloop()
