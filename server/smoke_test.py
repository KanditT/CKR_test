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
import zipfile
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


def request_bytes(path: str, method: str = "GET", payload: dict | None = None) -> bytes:
    data = None
    headers = {"x-admin-token": ADMIN_TOKEN}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.read()


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
                break
            await asyncio.sleep(0.2)
        else:
            raise RuntimeError("admin command did not complete")

        user_summary = request_json("/api/user/summary", "POST", {"license_key": license_key})
        assert user_summary["license"]["license_key"] == license_key, user_summary
        assert user_summary["devices"][0]["device_id"] == "SMOKE-DEVICE", user_summary

        user_command = request_json(
            "/api/user/devices/SMOKE-DEVICE/commands",
            "POST",
            {"license_key": license_key, "command": "test_ldplayer", "payload": {}},
        )
        user_command_id = user_command["command_id"]
        command_message = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert command_message["type"] == "command", command_message
        assert command_message["id"] == user_command_id, command_message
        assert command_message["payload"]["bot_config"]["device"]["adb_serial"] == "127.0.0.2:5555", command_message
        assert command_message["payload"]["bot_config"]["loop"]["scan_interval"] == 0.07, command_message
        await ws.send(
            json.dumps(
                {
                    "type": "command_result",
                    "id": user_command_id,
                    "status": "done",
                    "response": {"ok": True, "from": "user-smoke"},
                }
            )
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            result = request_json(
                f"/api/user/commands/{user_command_id}",
                "POST",
                {"license_key": license_key},
            )
            if result["command"]["status"] == "done":
                return
            await asyncio.sleep(0.2)
        raise RuntimeError("user command did not complete")


def exercise_user_download(license_key: str) -> None:
    html = request_bytes("/user")
    assert b"Cookie Run User Control" in html, "user page did not render"
    zip_bytes = request_bytes("/api/user/download-agent", "POST", {"license_key": license_key})
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as file:
        file.write(zip_bytes)
        zip_path = Path(file.name)
    try:
        with zipfile.ZipFile(zip_path, "r") as agent_zip:
            names = agent_zip.namelist()
            assert "config.local.json" in names, names
            config = json.loads(agent_zip.read("config.local.json").decode("utf-8"))
            assert config["license_key"] == license_key, config
            assert config["server_url"] == WS_URL, config
    finally:
        zip_path.unlink(missing_ok=True)


def exercise_user_config(license_key: str) -> None:
    config_response = request_json("/api/user/config", "POST", {"license_key": license_key})
    config = config_response["config"]
    config["device"]["adb_serial"] = "127.0.0.2:5555"
    config["loop"]["scan_interval"] = 0.07
    config["sequence"][0]["enabled"] = False
    saved = request_json("/api/user/config/save", "POST", {"license_key": license_key, "config": config})
    assert saved["config"]["device"]["adb_serial"] == "127.0.0.2:5555", saved
    assert saved["config"]["sequence"][0]["enabled"] is False, saved


def main() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="ckr-smoke-"))
    download_dir = tmp_dir / "downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(download_dir / "CookieRunAgent-portable.zip", "w", zipfile.ZIP_DEFLATED) as agent_zip:
        agent_zip.writestr("CookieRunAgent.exe", "smoke")
        agent_zip.writestr("config.local.json", "{}")
    env = os.environ.copy()
    env["ADMIN_TOKEN"] = ADMIN_TOKEN
    env["PUBLIC_BASE_URL"] = BASE_URL
    env["AGENT_SERVER_URL"] = WS_URL
    env["CKR_DATA_DIR"] = str(tmp_dir)
    env["CKR_DB_PATH"] = str(tmp_dir / "ckr_control.sqlite3")
    env["CKR_DOWNLOAD_DIR"] = str(download_dir)

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
        exercise_user_download(license_key)
        exercise_user_config(license_key)
        asyncio.run(exercise_agent_flow(license_key))
        request_json(f"/api/admin/licenses/{license_key}", "DELETE")
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
