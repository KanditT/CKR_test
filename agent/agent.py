from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import cv2
import websockets


AGENT_VERSION = "0.1.0"
ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.local.json")
DEVICE_ID_PATH = Path(__file__).with_name("device_id.txt")

sys.path.insert(0, str(ROOT_DIR))

import adb_client  # noqa: E402
import config_loader  # noqa: E402


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def get_device_id() -> str:
    if DEVICE_ID_PATH.exists():
        value = DEVICE_ID_PATH.read_text(encoding="utf-8").strip()
        if value:
            return value
    raw = f"{platform.node()}:{uuid.getnode()}:{platform.platform()}".encode("utf-8", errors="ignore")
    digest = hashlib.sha256(raw).hexdigest()[:12].upper()
    device_id = f"WIN-{digest}"
    save_text(DEVICE_ID_PATH, device_id)
    return device_id


def apply_adb_config(settings: dict[str, Any]) -> None:
    config = config_loader.config
    config.ADB_PATH = str(settings.get("adb_path") or config.ADB_PATH)
    config.ADB_SERIAL = str(settings.get("adb_serial") or config.ADB_SERIAL)


class Agent:
    def __init__(self, settings: dict[str, Any]):
        self.settings = settings
        self.device_id = get_device_id()
        self.device_name = str(settings.get("device_name") or platform.node() or self.device_id)
        self.bot_process: subprocess.Popen[str] | None = None
        self.ws = None
        apply_adb_config(settings)

    def build_url(self) -> str:
        server_url = str(self.settings["server_url"]).rstrip()
        params = urlencode(
            {
                "license_key": self.settings["license_key"],
                "device_id": self.device_id,
                "device_name": self.device_name,
                "agent_version": AGENT_VERSION,
            }
        )
        joiner = "&" if "?" in server_url else "?"
        return f"{server_url}{joiner}{params}"

    async def run_forever(self) -> None:
        while True:
            try:
                await self.connect_once()
            except Exception as exc:
                print(f"[agent] disconnected: {exc}")
            await asyncio.sleep(3)

    async def connect_once(self) -> None:
        url = self.build_url()
        print(f"[agent] connecting {url.split('?')[0]} as {self.device_id}")
        async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=8 * 1024 * 1024) as ws:
            self.ws = ws
            print("[agent] connected")
            await self.send_status("connected")
            heartbeat = asyncio.create_task(self.heartbeat_loop())
            try:
                async for raw_message in ws:
                    message = json.loads(raw_message)
                    await self.handle_message(message)
            finally:
                heartbeat.cancel()
                self.ws = None

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.ws is None:
            return
        await self.ws.send(json.dumps(payload))

    async def send_log(self, message: str) -> None:
        print(message)
        await self.send_json({"type": "log", "message": message})

    async def send_status(self, state: str = "online") -> None:
        await self.send_json(
            {
                "type": "status",
                "status": {
                    "state": state,
                    "device_id": self.device_id,
                    "device_name": self.device_name,
                    "agent_version": AGENT_VERSION,
                    "bot_running": self.bot_process is not None and self.bot_process.poll() is None,
                    "adb_path": str(config_loader.config.ADB_PATH),
                    "adb_serial": str(config_loader.config.ADB_SERIAL),
                    "time": time.time(),
                },
            }
        )

    async def heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            await self.send_status("online")

    async def handle_message(self, message: dict[str, Any]) -> None:
        if message.get("type") != "command":
            return
        command_id = int(message.get("id"))
        command = str(message.get("command"))
        payload = message.get("payload") or {}
        await self.send_log(f"[command] {command} #{command_id}")
        try:
            if command == "status":
                response = await self.command_status(payload)
            elif command == "test_ldplayer":
                response = await self.command_test_ldplayer(payload)
            elif command == "start_bot":
                response = await self.command_start_bot(payload)
            elif command == "kill_bot":
                response = await self.command_kill_bot(payload)
            elif command == "screenshot":
                response = await self.command_screenshot(payload)
            else:
                raise ValueError(f"Unknown command: {command}")
            await self.send_json(
                {"type": "command_result", "id": command_id, "status": "done", "response": response}
            )
        except Exception as exc:
            await self.send_json(
                {
                    "type": "command_result",
                    "id": command_id,
                    "status": "error",
                    "response": {"error": str(exc)},
                }
            )

    async def command_status(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "bot_running": self.bot_process is not None and self.bot_process.poll() is None,
            "bot_pid": None if self.bot_process is None else self.bot_process.pid,
        }

    async def command_test_ldplayer(self, _payload: dict[str, Any]) -> dict[str, Any]:
        apply_adb_config(self.settings)
        if not adb_client.is_connected():
            adb_client.connect()
        connected = adb_client.is_connected()
        if not connected:
            raise RuntimeError(f"Could not connect to {config_loader.config.ADB_SERIAL}")
        frame = adb_client.screencap()
        height, width = frame.shape[:2]
        return {"connected": True, "width": width, "height": height}

    async def command_start_bot(self, _payload: dict[str, Any]) -> dict[str, Any]:
        if self.bot_process and self.bot_process.poll() is None:
            return {"already_running": True, "pid": self.bot_process.pid}
        python_exe = str(self.settings.get("python_exe") or sys.executable)
        bot_script = ROOT_DIR / str(self.settings.get("bot_script") or "auto_clicker.py")
        if not bot_script.exists():
            raise RuntimeError(f"Bot script not found: {bot_script}")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.bot_process = subprocess.Popen(
            [python_exe, str(bot_script)],
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        asyncio.create_task(self.stream_bot_output(self.bot_process))
        await self.send_status("bot_started")
        return {"started": True, "pid": self.bot_process.pid, "script": str(bot_script)}

    async def command_kill_bot(self, _payload: dict[str, Any]) -> dict[str, Any]:
        if self.bot_process is None or self.bot_process.poll() is not None:
            self.bot_process = None
            return {"running": False}
        pid = self.bot_process.pid
        self.bot_process.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(self.bot_process.wait), timeout=5)
        except asyncio.TimeoutError:
            self.bot_process.kill()
        self.bot_process = None
        await self.send_status("bot_killed")
        return {"killed": True, "pid": pid}

    async def command_screenshot(self, _payload: dict[str, Any]) -> dict[str, Any]:
        frame = adb_client.screencap()
        ok, png = cv2.imencode(".png", frame)
        if not ok:
            raise RuntimeError("Could not encode screenshot")
        height, width = frame.shape[:2]
        image_b64 = base64.b64encode(png.tobytes()).decode("ascii")
        return {"width": width, "height": height, "png_base64": image_b64}

    async def stream_bot_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        while process.poll() is None:
            line = await asyncio.to_thread(process.stdout.readline)
            if not line:
                await asyncio.sleep(0.1)
                continue
            await self.send_log(f"[bot] {line.rstrip()}")
        code = process.poll()
        await self.send_log(f"[bot] exited with code {code}")
        if self.bot_process is process:
            self.bot_process = None
        await self.send_status("bot_exited")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cookie Run Windows Agent")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to agent config JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        raise SystemExit(f"Missing config: {config_path}. Copy agent/config.example.json to agent/config.local.json")
    settings = load_json(config_path)
    for key in ("server_url", "license_key"):
        if not settings.get(key):
            raise SystemExit(f"Missing required config key: {key}")
    agent = Agent(settings)
    asyncio.run(agent.run_forever())


if __name__ == "__main__":
    main()
