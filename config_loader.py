import importlib.util
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
def apply_env_overrides(module):
    adb_path = os.getenv("CKR_ADB_PATH")
    adb_serial = os.getenv("CKR_ADB_SERIAL")
    if adb_path:
        module.ADB_PATH = adb_path
    if adb_serial:
        module.ADB_SERIAL = adb_serial


apply_env_overrides(config)
