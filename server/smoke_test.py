from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import websockets


HOST = "127.0.0.1"
PORT = 8765
BASE_URL = f"http://{HOST}:{PORT}"
WS_URL = f"ws://{HOST}:{PORT}/ws/agent"
ADMIN_TOKEN = "smoke-admin-token"


def request_json(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = None
    headers = {"x-admin-token": ADMIN_TOKEN}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_server() -> None:
    deadline = time.time() + 20
    last_error = None
    while time.time() < deadline:
        try:
            request_json("/api/admin/summary")
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"server did not start: {last_error}")


async def exercise_agent_flow(license_key: str) -> None:
    params = urllib.parse.urlencode(
        {
            "license_key": license_key,
            "device_id": "SMOKE-DEVICE",
            "device_name": "Smoke Device",
            "agent_version": "smoke",
        }
    )
    async with websockets.connect(f"{WS_URL}?{params}") as ws:
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert hello["type"] == "hello", hello

        command = request_json(
            "/api/admin/devices/SMOKE-DEVICE/commands",
            "POST",
            {"command": "status", "payload": {}},
        )
        command_id = command["command_id"]
        command_message = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert command_message["type"] == "command", command_message
        assert command_message["id"] == command_id, command_message
        await ws.send(
            json.dumps(
                {
                    "type": "command_result",
                    "id": command_id,
                    "status": "done",
                    "response": {"ok": True, "from": "smoke"},
                }
            )
        )

        deadline = time.time() + 10
        while time.time() < deadline:
            result = request_json(f"/api/admin/commands/{command_id}")
            if result["command"]["status"] == "done":
                return
            await asyncio.sleep(0.2)
        raise RuntimeError("command did not complete")


def main() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="ckr-smoke-"))
    env = os.environ.copy()
    env["ADMIN_TOKEN"] = ADMIN_TOKEN
    env["PUBLIC_BASE_URL"] = BASE_URL
    env["CKR_DATA_DIR"] = str(tmp_dir)
    env["CKR_DB_PATH"] = str(tmp_dir / "ckr_control.sqlite3")

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            HOST,
            "--port",
            str(PORT),
        ],
        cwd=Path(__file__).resolve().parent,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_server()
        created = request_json(
            "/api/admin/licenses",
            "POST",
            {"customer_name": "Smoke", "line_name": "smoke", "days": "1", "max_devices": "1"},
        )
        license_key = created["license_key"]
        asyncio.run(exercise_agent_flow(license_key))
        print("smoke ok")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
