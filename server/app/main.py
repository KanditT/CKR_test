from __future__ import annotations

import json
import os
import secrets
import sqlite3
import io
import zipfile
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.default_config import default_bot_config


DATA_DIR = Path(os.getenv("CKR_DATA_DIR", "server/data"))
DB_PATH = Path(os.getenv("CKR_DB_PATH", str(DATA_DIR / "ckr_control.sqlite3")))
DOWNLOAD_DIR = Path(os.getenv("CKR_DOWNLOAD_DIR", "server/downloads"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "dev-admin-token")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
AGENT_SERVER_URL = os.getenv("AGENT_SERVER_URL", "")
APP_NAME = "Cookie Run Remote Control"

app = FastAPI(title=APP_NAME)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def default_agent_server_url() -> str:
    if AGENT_SERVER_URL:
        return AGENT_SERVER_URL
    if PUBLIC_BASE_URL.startswith("https://"):
        return "wss://" + PUBLIC_BASE_URL.removeprefix("https://").rstrip("/") + "/ws/agent"
    if PUBLIC_BASE_URL.startswith("http://"):
        return "ws://" + PUBLIC_BASE_URL.removeprefix("http://").rstrip("/") + "/ws/agent"
    return PUBLIC_BASE_URL.rstrip("/") + "/ws/agent"


@contextmanager
def db_conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if row is None else dict(row)


def init_db() -> None:
    with db_conn() as conn:
        conn.executescript(
            """
            create table if not exists licenses (
                license_key text primary key,
                status text not null default 'active',
                customer_name text not null default '',
                line_name text not null default '',
                note text not null default '',
                max_devices integer not null default 1,
                expires_at text,
                created_at text not null,
                activated_at text
            );

            create table if not exists devices (
                device_id text primary key,
                license_key text not null,
                device_name text not null default '',
                agent_version text not null default '',
                status text not null default 'offline',
                last_seen_at text not null,
                connected_at text,
                foreign key (license_key) references licenses (license_key)
            );

            create table if not exists command_logs (
                id integer primary key autoincrement,
                device_id text not null,
                command text not null,
                payload_json text not null default '{}',
                status text not null default 'queued',
                response_json text,
                created_at text not null,
                completed_at text
            );

            create table if not exists license_configs (
                license_key text primary key,
                config_json text not null,
                updated_at text not null,
                foreign key (license_key) references licenses (license_key)
            );
            """
        )


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@dataclass
class AgentSession:
    websocket: WebSocket
    device_id: str
    license_key: str
    device_name: str
    agent_version: str
    connected_at: str = field(default_factory=utc_iso)
    last_status: dict[str, Any] = field(default_factory=dict)


agents: dict[str, AgentSession] = {}


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN is not configured")
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")


def generate_license_key() -> str:
    raw = secrets.token_hex(8).upper()
    return "CKR-" + "-".join(raw[index : index + 4] for index in range(0, len(raw), 4))


def license_is_valid(license_row: sqlite3.Row) -> tuple[bool, str]:
    status = license_row["status"]
    if status != "active":
        return False, f"license is {status}"
    expires_at = parse_datetime(license_row["expires_at"])
    if expires_at and expires_at < utc_now():
        return False, "license expired"
    return True, "ok"


def get_license(conn: sqlite3.Connection, license_key: str) -> sqlite3.Row | None:
    return conn.execute("select * from licenses where license_key = ?", (license_key,)).fetchone()


def get_device_count(conn: sqlite3.Connection, license_key: str) -> int:
    row = conn.execute("select count(*) as count from devices where license_key = ?", (license_key,)).fetchone()
    return int(row["count"])


def upsert_device(
    conn: sqlite3.Connection,
    *,
    license_key: str,
    device_id: str,
    device_name: str,
    agent_version: str,
    status: str,
) -> None:
    now = utc_iso()
    existing = conn.execute("select * from devices where device_id = ?", (device_id,)).fetchone()
    if existing:
        conn.execute(
            """
            update devices
               set license_key = ?,
                   device_name = ?,
                   agent_version = ?,
                   status = ?,
                   last_seen_at = ?,
                   connected_at = coalesce(connected_at, ?)
             where device_id = ?
            """,
            (license_key, device_name, agent_version, status, now, now, device_id),
        )
    else:
        conn.execute(
            """
            insert into devices (device_id, license_key, device_name, agent_version, status, last_seen_at, connected_at)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (device_id, license_key, device_name, agent_version, status, now, now),
        )
        conn.execute(
            "update licenses set activated_at = coalesce(activated_at, ?) where license_key = ?",
            (now, license_key),
        )


def summary_payload() -> dict[str, Any]:
    with db_conn() as conn:
        licenses = [dict(row) for row in conn.execute("select * from licenses order by created_at desc").fetchall()]
        devices = [dict(row) for row in conn.execute("select * from devices order by last_seen_at desc").fetchall()]
        commands = [
            dict(row)
            for row in conn.execute(
                "select * from command_logs order by id desc limit 80",
            ).fetchall()
        ]
    for device in devices:
        device["online"] = device["device_id"] in agents
        if device["online"]:
            device["last_status"] = agents[device["device_id"]].last_status
    return {"licenses": licenses, "devices": devices, "commands": commands, "server_time": utc_iso()}


def user_summary_payload(license_key: str) -> dict[str, Any]:
    license_key = license_key.strip()
    with db_conn() as conn:
        license_row = get_license(conn, license_key)
        if license_row is None:
            raise HTTPException(status_code=404, detail="license not found")
        ok, reason = license_is_valid(license_row)
        devices = [
            dict(row)
            for row in conn.execute(
                "select * from devices where license_key = ? order by last_seen_at desc",
                (license_key,),
            ).fetchall()
        ]
        commands = [
            dict(row)
            for row in conn.execute(
                """
                select command_logs.*
                  from command_logs
                  join devices on devices.device_id = command_logs.device_id
                 where devices.license_key = ?
                 order by command_logs.id desc
                 limit 60
                """,
                (license_key,),
            ).fetchall()
        ]
    for device in devices:
        device["online"] = device["device_id"] in agents
        if device["online"]:
            device["last_status"] = agents[device["device_id"]].last_status
    license_data = dict(license_row)
    return {
        "license": license_data,
        "license_ok": ok,
        "license_reason": reason,
        "devices": devices,
        "commands": commands,
        "server_time": utc_iso(),
    }


def to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "x", "[x]"}
    return default


def to_float(value: Any, default: float) -> float:
    if value in (None, ""):
        return default
    return float(value)


def to_int(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    return int(float(value))


def optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def normalize_delay(value: Any) -> float | list[float] | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if len(parts) == 2:
            return [float(parts[0]), float(parts[1])]
        if len(parts) == 1:
            return float(parts[0])
        return None
    if isinstance(value, (list, tuple)):
        parts = [part for part in value if part not in (None, "")]
        if len(parts) == 2:
            return [float(parts[0]), float(parts[1])]
        if len(parts) == 1:
            return float(parts[0])
        return None
    return float(value)


def normalize_step(raw_step: Any) -> dict[str, Any]:
    if not isinstance(raw_step, dict):
        raw_step = {}
    step: dict[str, Any] = {
        "name": str(raw_step.get("name") or "New Step").strip()[:120],
        "template": str(raw_step.get("template") or "").strip()[:260],
        "confidence": max(0.0, min(1.0, to_float(raw_step.get("confidence"), 0.85))),
        "enabled": to_bool(raw_step.get("enabled"), True),
    }
    if to_bool(raw_step.get("verify_click"), False):
        step["verify_click"] = True
    for key in ("post_delay", "wait_before"):
        delay = normalize_delay(raw_step.get(key))
        if delay is not None:
            step[key] = delay
    for key in ("timeout", "retry_after", "retry_confidence"):
        value = optional_float(raw_step.get(key))
        if value is not None:
            step[key] = value
    retry_template = str(raw_step.get("retry_template") or "").strip()
    if retry_template:
        step["retry_template"] = retry_template[:260]
    return step


def normalize_bot_config(candidate: Any) -> dict[str, Any]:
    config = default_bot_config()
    if not isinstance(candidate, dict):
        return config

    device = candidate.get("device") if isinstance(candidate.get("device"), dict) else {}
    config["device"]["adb_path"] = str(device.get("adb_path") or config["device"]["adb_path"]).strip()
    config["device"]["adb_serial"] = str(device.get("adb_serial") or config["device"]["adb_serial"]).strip()

    loop = candidate.get("loop") if isinstance(candidate.get("loop"), dict) else {}
    config["loop"]["scan_interval"] = max(0.01, to_float(loop.get("scan_interval"), config["loop"]["scan_interval"]))
    config["loop"]["min_delay"] = max(0.0, to_float(loop.get("min_delay"), config["loop"]["min_delay"]))
    config["loop"]["max_delay"] = max(config["loop"]["min_delay"], to_float(loop.get("max_delay"), config["loop"]["max_delay"]))
    config["loop"]["jitter"] = max(0, to_int(loop.get("jitter"), config["loop"]["jitter"]))
    config["loop"]["retry_limit"] = max(0, to_int(loop.get("retry_limit"), config["loop"]["retry_limit"]))
    config["loop"]["verify_delay"] = max(0.0, to_float(loop.get("verify_delay"), config["loop"]["verify_delay"]))

    recorder = candidate.get("recorder") if isinstance(candidate.get("recorder"), dict) else {}
    config["recorder"].update(
        {
            "input_mode": str(recorder.get("input_mode") or config["recorder"]["input_mode"]).strip(),
            "loop_replay_enabled": to_bool(recorder.get("loop_replay_enabled"), config["recorder"]["loop_replay_enabled"]),
            "loop_trigger_mode": str(recorder.get("loop_trigger_mode") or config["recorder"]["loop_trigger_mode"]).strip(),
            "loop_trigger_step": str(recorder.get("loop_trigger_step") or config["recorder"]["loop_trigger_step"]).strip(),
            "loop_trigger_template": str(
                recorder.get("loop_trigger_template") or config["recorder"]["loop_trigger_template"]
            ).strip(),
            "loop_trigger_confidence": max(
                0.0,
                min(
                    1.0,
                    to_float(recorder.get("loop_trigger_confidence"), config["recorder"]["loop_trigger_confidence"]),
                ),
            ),
            "loop_replay_file": str(recorder.get("loop_replay_file") or config["recorder"]["loop_replay_file"]).strip(),
            "loop_replay_delay": to_float(recorder.get("loop_replay_delay"), config["recorder"]["loop_replay_delay"]),
            "loop_tap_trigger": to_bool(recorder.get("loop_tap_trigger"), config["recorder"]["loop_tap_trigger"]),
        }
    )
    jump_tap = recorder.get("jump_tap")
    if isinstance(jump_tap, (list, tuple)) and len(jump_tap) >= 2:
        config["recorder"]["jump_tap"] = [to_int(jump_tap[0], 165), to_int(jump_tap[1], 625)]
    slide_swipe = recorder.get("slide_swipe")
    if isinstance(slide_swipe, (list, tuple)) and len(slide_swipe) >= 5:
        config["recorder"]["slide_swipe"] = [to_int(value, 0) for value in slide_swipe[:5]]

    if isinstance(candidate.get("sequence"), list):
        config["sequence"] = [normalize_step(step) for step in candidate["sequence"]]
    if isinstance(candidate.get("interrupts"), list):
        config["interrupts"] = [normalize_step(step) for step in candidate["interrupts"]]
    return config


def get_license_bot_config(license_key: str) -> dict[str, Any]:
    with db_conn() as conn:
        row = conn.execute("select config_json from license_configs where license_key = ?", (license_key,)).fetchone()
    if row is None:
        return default_bot_config()
    try:
        return normalize_bot_config(json.loads(row["config_json"]))
    except (json.JSONDecodeError, TypeError, ValueError):
        return default_bot_config()


def save_license_bot_config(license_key: str, config: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_bot_config(config)
    with db_conn() as conn:
        if get_license(conn, license_key) is None:
            raise HTTPException(status_code=404, detail="license not found")
        conn.execute(
            """
            insert into license_configs (license_key, config_json, updated_at)
            values (?, ?, ?)
            on conflict(license_key) do update set
                config_json = excluded.config_json,
                updated_at = excluded.updated_at
            """,
            (license_key, json.dumps(normalized, separators=(",", ":")), utc_iso()),
        )
    return normalized


async def dispatch_device_command(device_id: str, command: str, payload: dict[str, Any]) -> dict[str, Any]:
    command = command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="command is required")
    session = agents.get(device_id)
    if not session:
        raise HTTPException(status_code=404, detail="agent is offline")
    payload = deepcopy(payload)
    if command in {"test_ldplayer", "start_bot", "screenshot"} and "bot_config" not in payload:
        payload["bot_config"] = get_license_bot_config(session.license_key)
    with db_conn() as conn:
        cursor = conn.execute(
            """
            insert into command_logs (device_id, command, payload_json, status, created_at)
            values (?, ?, ?, 'sent', ?)
            """,
            (device_id, command, json.dumps(payload), utc_iso()),
        )
        command_id = int(cursor.lastrowid)
    await session.websocket.send_json(
        {"type": "command", "id": command_id, "command": command, "payload": payload}
    )
    return {"status": "sent", "command_id": command_id}


def verify_license_device(license_key: str, device_id: str) -> None:
    with db_conn() as conn:
        license_row = get_license(conn, license_key)
        if license_row is None:
            raise HTTPException(status_code=404, detail="license not found")
        ok, reason = license_is_valid(license_row)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
        device = conn.execute(
            "select * from devices where device_id = ? and license_key = ?",
            (device_id, license_key),
        ).fetchone()
        if device is None:
            raise HTTPException(status_code=404, detail="device not found for this license")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/user")


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_page() -> str:
    return ADMIN_HTML.replace("__PUBLIC_BASE_URL__", PUBLIC_BASE_URL)


@app.get("/user", response_class=HTMLResponse, include_in_schema=False)
def user_page() -> str:
    return USER_HTML.replace("__PUBLIC_BASE_URL__", PUBLIC_BASE_URL)


@app.get("/api/admin/summary")
def api_summary(_: None = Header(default=None), x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    return summary_payload()


@app.post("/api/admin/licenses")
async def create_license(request: Request, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    body = await request.json()
    days_raw = str(body.get("days", "")).strip()
    expires_at = None
    if days_raw:
        days = int(days_raw)
        if days > 0:
            expires_at = utc_iso(utc_now() + timedelta(days=days))
    max_devices = max(1, int(body.get("max_devices", 1)))
    license_key = generate_license_key()
    with db_conn() as conn:
        conn.execute(
            """
            insert into licenses
                (license_key, status, customer_name, line_name, note, max_devices, expires_at, created_at)
            values (?, 'active', ?, ?, ?, ?, ?, ?)
            """,
            (
                license_key,
                str(body.get("customer_name", "")).strip(),
                str(body.get("line_name", "")).strip(),
                str(body.get("note", "")).strip(),
                max_devices,
                expires_at,
                utc_iso(),
            ),
        )
    return {"license_key": license_key}


@app.post("/api/admin/licenses/{license_key}/revoke")
def revoke_license(license_key: str, x_admin_token: str | None = Header(default=None)) -> dict[str, str]:
    require_admin(x_admin_token)
    with db_conn() as conn:
        conn.execute("update licenses set status = 'revoked' where license_key = ?", (license_key,))
    return {"status": "revoked"}


@app.post("/api/admin/licenses/{license_key}/reset-device")
def reset_license_device(license_key: str, x_admin_token: str | None = Header(default=None)) -> dict[str, str]:
    require_admin(x_admin_token)
    if any(session.license_key == license_key for session in agents.values()):
        raise HTTPException(status_code=409, detail="Disconnect the active agent first")
    with db_conn() as conn:
        conn.execute("delete from devices where license_key = ?", (license_key,))
        conn.execute("update licenses set activated_at = null where license_key = ?", (license_key,))
    return {"status": "device_reset"}


@app.delete("/api/admin/licenses/{license_key}")
def delete_license(license_key: str, x_admin_token: str | None = Header(default=None)) -> dict[str, str]:
    require_admin(x_admin_token)
    if any(session.license_key == license_key for session in agents.values()):
        raise HTTPException(status_code=409, detail="Disconnect the active agent before deleting this license")
    with db_conn() as conn:
        devices = [
            row["device_id"]
            for row in conn.execute("select device_id from devices where license_key = ?", (license_key,)).fetchall()
        ]
        for device_id in devices:
            conn.execute("delete from command_logs where device_id = ?", (device_id,))
        conn.execute("delete from devices where license_key = ?", (license_key,))
        conn.execute("delete from license_configs where license_key = ?", (license_key,))
        result = conn.execute("delete from licenses where license_key = ?", (license_key,))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="license not found")
    return {"status": "deleted"}


@app.post("/api/admin/devices/{device_id}/commands")
async def send_command(device_id: str, request: Request, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    body = await request.json()
    command = str(body.get("command", "")).strip()
    payload = body.get("payload") or {}
    return await dispatch_device_command(device_id, command, payload)


@app.get("/api/admin/commands/{command_id}")
def get_command(command_id: int, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    with db_conn() as conn:
        command = conn.execute("select * from command_logs where id = ?", (command_id,)).fetchone()
    if command is None:
        raise HTTPException(status_code=404, detail="command not found")
    return {"command": dict(command)}


@app.post("/api/user/summary")
async def user_summary(request: Request) -> dict[str, Any]:
    body = await request.json()
    license_key = str(body.get("license_key", "")).strip()
    if not license_key:
        raise HTTPException(status_code=400, detail="license_key is required")
    return user_summary_payload(license_key)


@app.post("/api/user/config")
async def user_get_config(request: Request) -> dict[str, Any]:
    body = await request.json()
    license_key = str(body.get("license_key", "")).strip()
    if not license_key:
        raise HTTPException(status_code=400, detail="license_key is required")
    with db_conn() as conn:
        license_row = get_license(conn, license_key)
        if license_row is None:
            raise HTTPException(status_code=404, detail="license not found")
        ok, reason = license_is_valid(license_row)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
    return {"config": get_license_bot_config(license_key)}


@app.post("/api/user/config/save")
async def user_save_config(request: Request) -> dict[str, Any]:
    body = await request.json()
    license_key = str(body.get("license_key", "")).strip()
    if not license_key:
        raise HTTPException(status_code=400, detail="license_key is required")
    with db_conn() as conn:
        license_row = get_license(conn, license_key)
        if license_row is None:
            raise HTTPException(status_code=404, detail="license not found")
        ok, reason = license_is_valid(license_row)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
    config = save_license_bot_config(license_key, body.get("config") or {})
    return {"config": config, "status": "saved"}


@app.post("/api/user/devices/{device_id}/commands")
async def user_send_command(device_id: str, request: Request) -> dict[str, Any]:
    body = await request.json()
    license_key = str(body.get("license_key", "")).strip()
    command = str(body.get("command", "")).strip()
    payload = body.get("payload") or {}
    if not license_key:
        raise HTTPException(status_code=400, detail="license_key is required")
    if command not in {"status", "test_ldplayer", "start_bot", "kill_bot", "screenshot"}:
        raise HTTPException(status_code=400, detail="command is not allowed")
    verify_license_device(license_key, device_id)
    return await dispatch_device_command(device_id, command, payload)


@app.post("/api/user/commands/{command_id}")
async def user_get_command(command_id: int, request: Request) -> dict[str, Any]:
    body = await request.json()
    license_key = str(body.get("license_key", "")).strip()
    if not license_key:
        raise HTTPException(status_code=400, detail="license_key is required")
    with db_conn() as conn:
        command = conn.execute(
            """
            select command_logs.*
              from command_logs
              join devices on devices.device_id = command_logs.device_id
             where command_logs.id = ? and devices.license_key = ?
            """,
            (command_id, license_key),
        ).fetchone()
    if command is None:
        raise HTTPException(status_code=404, detail="command not found")
    return {"command": dict(command)}


@app.post("/api/user/download-agent")
async def user_download_agent(request: Request) -> Response:
    body = await request.json()
    license_key = str(body.get("license_key", "")).strip()
    if not license_key:
        raise HTTPException(status_code=400, detail="license_key is required")
    with db_conn() as conn:
        license_row = get_license(conn, license_key)
        if license_row is None:
            raise HTTPException(status_code=404, detail="license not found")
        ok, reason = license_is_valid(license_row)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)

    base_zip = DOWNLOAD_DIR / "CookieRunAgent-portable.zip"
    if not base_zip.exists():
        raise HTTPException(status_code=404, detail="agent download is not available yet")

    agent_config = {
        "server_url": default_agent_server_url(),
        "license_key": license_key,
        "device_name": str(license_row["customer_name"] or "CKR Agent"),
        "adb_path": "C:\\LDPlayer\\LDPlayer14\\adb.exe",
        "adb_serial": "127.0.0.1:5555",
        "python_exe": "",
        "bot_script": "auto_clicker.py",
    }
    output = io.BytesIO()
    with zipfile.ZipFile(base_zip, "r") as source_zip, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target_zip:
        for item in source_zip.infolist():
            normalized = item.filename.replace("\\", "/").lstrip("/")
            if normalized.endswith("config.local.json"):
                continue
            target_zip.writestr(item, source_zip.read(item.filename))
        target_zip.writestr("config.local.json", json.dumps(agent_config, indent=2))
    output.seek(0)
    return Response(
        output.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="CookieRunAgent-portable.zip"'},
    )


@app.websocket("/ws/agent")
async def agent_socket(
    websocket: WebSocket,
    license_key: str = Query(...),
    device_id: str = Query(...),
    device_name: str = Query(""),
    agent_version: str = Query("dev"),
) -> None:
    await websocket.accept()
    license_key = license_key.strip()
    device_id = device_id.strip()
    if not license_key or not device_id:
        await websocket.close(code=4400, reason="license_key and device_id are required")
        return

    with db_conn() as conn:
        license_row = get_license(conn, license_key)
        if license_row is None:
            await websocket.close(code=4403, reason="license not found")
            return
        ok, reason = license_is_valid(license_row)
        if not ok:
            await websocket.close(code=4403, reason=reason)
            return
        existing_device = conn.execute("select * from devices where device_id = ?", (device_id,)).fetchone()
        if existing_device is None and get_device_count(conn, license_key) >= int(license_row["max_devices"]):
            await websocket.close(code=4403, reason="device limit reached")
            return
        if existing_device is not None and existing_device["license_key"] != license_key:
            await websocket.close(code=4403, reason="device is bound to another license")
            return
        upsert_device(
            conn,
            license_key=license_key,
            device_id=device_id,
            device_name=device_name,
            agent_version=agent_version,
            status="online",
        )

    session = AgentSession(
        websocket=websocket,
        device_id=device_id,
        license_key=license_key,
        device_name=device_name,
        agent_version=agent_version,
    )
    agents[device_id] = session
    await websocket.send_json({"type": "hello", "server_time": utc_iso(), "public_base_url": PUBLIC_BASE_URL})

    try:
        while True:
            message = await websocket.receive_json()
            await handle_agent_message(session, message)
    except WebSocketDisconnect:
        pass
    finally:
        if agents.get(device_id) is session:
            agents.pop(device_id, None)
        with db_conn() as conn:
            conn.execute(
                "update devices set status = 'offline', last_seen_at = ? where device_id = ?",
                (utc_iso(), device_id),
            )


async def handle_agent_message(session: AgentSession, message: dict[str, Any]) -> None:
    message_type = str(message.get("type", "")).lower()
    now = utc_iso()
    if message_type in {"status", "hello", "heartbeat"}:
        session.last_status = message.get("status") or message
        with db_conn() as conn:
            conn.execute(
                "update devices set status = 'online', last_seen_at = ? where device_id = ?",
                (now, session.device_id),
            )
    elif message_type == "log":
        with db_conn() as conn:
            conn.execute(
                """
                insert into command_logs (device_id, command, payload_json, status, response_json, created_at, completed_at)
                values (?, 'agent_log', '{}', 'log', ?, ?, ?)
                """,
                (session.device_id, json.dumps({"message": message.get("message", "")}), now, now),
            )
    elif message_type == "command_result":
        command_id = message.get("id")
        if command_id is not None:
            with db_conn() as conn:
                conn.execute(
                    """
                    update command_logs
                       set status = ?,
                           response_json = ?,
                           completed_at = ?
                     where id = ?
                    """,
                    (
                        str(message.get("status", "done")),
                        json.dumps(message.get("response", {})),
                        now,
                        int(command_id),
                    ),
                )


ADMIN_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Cookie Run Control</title>
  <style>
    :root {
      color-scheme: dark;
      --bg:#0f172a; --panel:#111827; --card:#1f2937; --line:#334155;
      --text:#f8fafc; --muted:#94a3b8; --accent:#22c55e; --danger:#ef4444;
    }
    * { box-sizing: border-box; }
    body { margin:0; background:var(--bg); color:var(--text); font-family:Segoe UI, Arial, sans-serif; }
    header { display:flex; justify-content:space-between; align-items:center; padding:18px 24px; border-bottom:1px solid var(--line); background:#020617; }
    h1 { margin:0; font-size:20px; }
    main { padding:18px 24px; display:grid; gap:16px; grid-template-columns: 320px 1fr; }
    section { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }
    h2 { margin:0 0 12px; font-size:15px; }
    label { display:block; margin:10px 0 4px; color:var(--muted); font-size:12px; }
    input, textarea, select { width:100%; background:#020617; color:var(--text); border:1px solid var(--line); border-radius:6px; padding:9px; }
    button { background:var(--card); color:var(--text); border:1px solid var(--line); border-radius:6px; padding:8px 11px; cursor:pointer; white-space:nowrap; }
    button.primary { background:var(--accent); color:#052e16; border-color:var(--accent); font-weight:700; }
    button.danger { background:var(--danger); color:white; border-color:var(--danger); }
    table { width:100%; border-collapse:separate; border-spacing:0 8px; font-size:13px; }
    th { text-align:left; color:var(--muted); font-size:12px; padding:0 8px 2px; }
    td { background:#101827; text-align:left; padding:10px 8px; vertical-align:top; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
    td:first-child { border-left:1px solid var(--line); border-radius:8px 0 0 8px; }
    td:last-child { border-right:1px solid var(--line); border-radius:0 8px 8px 0; }
    .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .pill { display:inline-block; padding:2px 8px; border-radius:999px; background:var(--card); color:var(--muted); }
    .online { color:var(--accent); font-weight:700; }
    .offline { color:var(--muted); }
    .mono { font-family:Consolas, monospace; }
    .muted { color:var(--muted); }
    #log { height:220px; overflow:auto; background:#020617; border:1px solid var(--line); padding:10px; border-radius:6px; font-family:Consolas, monospace; font-size:12px; }
    @media (max-width: 980px) { main { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Cookie Run Remote Control</h1>
      <div class="muted">__PUBLIC_BASE_URL__</div>
    </div>
    <div class="row">
      <input id="adminToken" placeholder="Admin token" style="width:260px" />
      <button onclick="saveToken()">Save Token</button>
      <button onclick="refresh()">Refresh</button>
    </div>
  </header>
  <main>
    <div>
      <section>
        <h2>Generate License</h2>
        <label>Customer</label><input id="customerName" placeholder="Customer name" />
        <label>Note</label><textarea id="note" rows="3"></textarea>
        <p><button class="primary" onclick="generateLicense()">Generate</button></p>
        <div id="generated" class="mono"></div>
      </section>
      <section style="margin-top:16px">
        <h2>Command Log</h2>
        <div id="log"></div>
      </section>
    </div>

    <div>
      <section>
        <h2>Devices</h2>
        <table>
          <thead><tr><th>Status</th><th>Device</th><th>License</th><th>Actions</th></tr></thead>
          <tbody id="devices"></tbody>
        </table>
      </section>
      <section style="margin-top:16px">
        <h2>Licenses</h2>
        <table>
          <thead><tr><th>Key</th><th>Customer</th><th>Status</th><th>Expires</th><th>Actions</th></tr></thead>
          <tbody id="licenses"></tbody>
        </table>
      </section>
    </div>
  </main>
  <script>
    const tokenInput = document.getElementById('adminToken');
    tokenInput.value = localStorage.getItem('ckr_admin_token') || '';

    function headers() {
      return {'content-type':'application/json', 'x-admin-token': tokenInput.value};
    }
    function saveToken() {
      localStorage.setItem('ckr_admin_token', tokenInput.value);
      refresh();
    }
    function esc(value) {
      return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
    async function request(path, options={}) {
      const res = await fetch(path, {...options, headers: {...headers(), ...(options.headers || {})}});
      if (!res.ok) throw new Error(await res.text());
      return await res.json();
    }
    async function generateLicense() {
      const payload = {
        customer_name: customerName.value,
        line_name: '',
        days: '',
        max_devices: 1,
        note: note.value,
      };
      const data = await request('/api/admin/licenses', {method:'POST', body:JSON.stringify(payload)});
      generated.textContent = data.license_key;
      await refresh();
    }
    async function sendCommand(deviceId, command) {
      const sent = await request(`/api/admin/devices/${encodeURIComponent(deviceId)}/commands`, {
        method:'POST',
        body:JSON.stringify({command})
      });
      await waitCommand(sent.command_id);
      await refresh();
    }
    async function waitCommand(commandId) {
      if (!commandId) return;
      for (let attempt = 0; attempt < 60; attempt++) {
        const data = await request(`/api/admin/commands/${encodeURIComponent(commandId)}`);
        const command = data.command;
        if (!['queued', 'sent'].includes(command.status)) return command;
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      throw new Error(`Command ${commandId} did not finish in time`);
    }
    async function revoke(key) {
      await request(`/api/admin/licenses/${encodeURIComponent(key)}/revoke`, {method:'POST', body:'{}'});
      await refresh();
    }
    async function resetDevice(key) {
      await request(`/api/admin/licenses/${encodeURIComponent(key)}/reset-device`, {method:'POST', body:'{}'});
      await refresh();
    }
    async function deleteLicense(key) {
      if (!confirm(`Delete license ${key}? This removes its device and logs too.`)) return;
      await request(`/api/admin/licenses/${encodeURIComponent(key)}`, {method:'DELETE'});
      await refresh();
    }
    async function refresh() {
      try {
        const data = await request('/api/admin/summary');
        devices.innerHTML = data.devices.map(d => `
          <tr>
            <td class="${d.online ? 'online' : 'offline'}">${d.online ? 'Online' : 'Offline'}</td>
            <td><div class="mono">${esc(d.device_id)}</div><div class="muted">${esc(d.device_name)} ${esc(d.agent_version)}</div></td>
            <td class="mono">${esc(d.license_key)}</td>
            <td class="row">
              <button onclick="sendCommand('${esc(d.device_id)}','status')">Status</button>
              <button onclick="sendCommand('${esc(d.device_id)}','test_ldplayer')">Test LDPlayer</button>
              <button class="primary" onclick="sendCommand('${esc(d.device_id)}','start_bot')">Start</button>
              <button class="danger" onclick="sendCommand('${esc(d.device_id)}','kill_bot')">Kill</button>
              <button onclick="sendCommand('${esc(d.device_id)}','screenshot')">Screenshot</button>
            </td>
          </tr>`).join('');
        licenses.innerHTML = data.licenses.map(l => `
          <tr>
            <td class="mono">${esc(l.license_key)}</td>
            <td>${esc(l.customer_name)}<div class="muted">${esc(l.line_name)} ${esc(l.note)}</div></td>
            <td><span class="pill">${esc(l.status)}</span></td>
            <td>${esc(l.expires_at || 'never')}</td>
            <td class="row">
              <button class="danger" onclick="revoke('${esc(l.license_key)}')">Revoke</button>
              <button onclick="resetDevice('${esc(l.license_key)}')">Reset Device</button>
              <button class="danger" onclick="deleteLicense('${esc(l.license_key)}')">Delete</button>
            </td>
          </tr>`).join('');
        log.innerHTML = data.commands.map(c => `<div>[${esc(c.created_at)}] ${esc(c.device_id)} ${esc(c.command)} ${esc(c.status)} ${esc(c.response_json || '')}</div>`).join('');
      } catch (err) {
        log.innerHTML = `<div style="color:#ef4444">${esc(err.message)}</div>` + log.innerHTML;
      }
    }
    setInterval(refresh, 3000);
    refresh();
  </script>
</body>
</html>
"""


USER_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Cookie Run User Control</title>
  <style>
    :root {
      color-scheme: dark;
      --bg:#0b1120; --panel:#111827; --panel2:#151f31; --line:#2f3b52;
      --text:#f8fafc; --muted:#94a3b8; --accent:#22c55e; --blue:#38bdf8;
      --danger:#ef4444; --warn:#f59e0b; --input:#020617;
    }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--text); font-family:Segoe UI, Arial, sans-serif; }
    header { display:flex; justify-content:space-between; align-items:flex-start; gap:18px; padding:18px 24px; background:#020617; border-bottom:1px solid var(--line); }
    h1 { margin:0; font-size:21px; }
    h2 { margin:0 0 12px; font-size:15px; }
    h3 { margin:14px 0 8px; font-size:13px; color:var(--muted); }
    main { padding:18px 24px; display:grid; grid-template-columns:310px 1fr; gap:16px; }
    section, .card { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }
    label { display:block; margin:10px 0 4px; color:var(--muted); font-size:12px; }
    input, select { width:100%; background:var(--input); color:var(--text); border:1px solid var(--line); border-radius:6px; padding:9px; }
    input[type="checkbox"] { width:auto; }
    button { background:var(--panel2); color:var(--text); border:1px solid var(--line); border-radius:6px; padding:8px 11px; cursor:pointer; white-space:nowrap; }
    button.primary { background:var(--accent); color:#052e16; border-color:var(--accent); font-weight:700; }
    button.danger { background:var(--danger); color:#fff; border-color:var(--danger); }
    button.active { border-color:var(--blue); color:#e0f2fe; background:#0c2235; }
    button:disabled { opacity:.48; cursor:not-allowed; }
    table { width:100%; border-collapse:separate; border-spacing:0 7px; font-size:12px; min-width:980px; }
    th { text-align:left; color:var(--muted); font-size:11px; padding:0 7px 2px; }
    td { background:#0f172a; padding:7px; vertical-align:middle; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
    td:first-child { border-left:1px solid var(--line); border-radius:7px 0 0 7px; }
    td:last-child { border-right:1px solid var(--line); border-radius:0 7px 7px 0; }
    td input { padding:6px; font-size:12px; }
    .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .stack { display:grid; gap:10px; }
    .muted { color:var(--muted); }
    .mono { font-family:Consolas, monospace; }
    .ok { color:var(--accent); font-weight:700; }
    .bad { color:var(--danger); font-weight:700; }
    .warn { color:var(--warn); font-weight:700; }
    .metric-grid { display:grid; grid-template-columns:repeat(4, minmax(120px, 1fr)); gap:10px; }
    .metric { background:#0f172a; border:1px solid var(--line); border-radius:8px; padding:10px; }
    .metric .label { color:var(--muted); font-size:11px; margin-bottom:4px; }
    .metric .value { font-size:14px; font-weight:700; }
    .page { display:none; }
    .page.active { display:block; }
    .scroll { overflow:auto; border:1px solid var(--line); border-radius:8px; padding:0 8px; background:#08111f; }
    #log { height:300px; overflow:auto; background:#020617; border:1px solid var(--line); padding:10px; border-radius:6px; font-family:Consolas, monospace; font-size:12px; }
    #screenshot { max-width:100%; border:1px solid var(--line); border-radius:8px; display:none; margin-top:10px; }
    .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    .small { font-size:12px; }
    @media (max-width: 980px) { header, main { display:block; } main { padding:14px; } section { margin-bottom:14px; } .metric-grid, .grid2 { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Cookie Run User Control</h1>
      <div class="muted">__PUBLIC_BASE_URL__</div>
    </div>
    <div class="row">
      <button id="navMonitor" class="active" onclick="selectPage('monitor')">Run Monitor</button>
      <button id="navFlow" onclick="selectPage('flow')">Step Flow</button>
      <button id="navSettings" onclick="selectPage('settings')">Setting Device</button>
    </div>
  </header>
  <main>
    <aside class="stack">
      <section>
        <h2>License</h2>
        <label>License Key</label>
        <input id="licenseKey" placeholder="CKR-XXXX-XXXX-XXXX-XXXX" />
        <p class="row">
          <button class="primary" onclick="saveLicense()">Verify</button>
          <button onclick="refresh()">Refresh</button>
          <button id="downloadButton" onclick="downloadAgent()" disabled>Download Agent</button>
        </p>
        <div id="licenseInfo" class="muted small">Enter your license key.</div>
      </section>
      <section>
        <h2>Selected Device</h2>
        <div id="selectedDevice" class="muted small">No agent connected.</div>
      </section>
    </aside>

    <div>
      <section id="pageMonitor" class="page active">
        <div class="row" style="justify-content:space-between">
          <h2>Run Monitor</h2>
          <div class="row">
            <button onclick="sendSelectedCommand('status')">Status</button>
            <button onclick="sendSelectedCommand('test_ldplayer')">Test LDPlayer</button>
            <button class="primary" onclick="sendSelectedCommand('start_bot')">Run</button>
            <button class="danger" onclick="sendSelectedCommand('kill_bot')">Kill</button>
            <button onclick="sendSelectedCommand('screenshot')">Screenshot</button>
          </div>
        </div>
        <div id="metrics" class="metric-grid"></div>
        <h3>Devices</h3>
        <div class="scroll"><table><thead><tr><th>Status</th><th>Device</th><th>Last Seen</th><th>Actions</th></tr></thead><tbody id="devices"></tbody></table></div>
        <h3>Latest Screenshot</h3>
        <img id="screenshot" alt="LDPlayer screenshot" />
        <h3>Log</h3>
        <div id="log"></div>
      </section>

      <section id="pageFlow" class="page">
        <div class="row" style="justify-content:space-between">
          <h2>Step Flow</h2>
          <div class="row">
            <button id="groupSequence" class="active" onclick="selectGroup('sequence')">Sequence</button>
            <button id="groupInterrupts" onclick="selectGroup('interrupts')">Interrupts</button>
            <button onclick="addStep()">Add Step</button>
            <button class="primary" onclick="saveConfig()">Save Config</button>
            <button onclick="loadConfig(true)">Reload</button>
          </div>
        </div>
        <div class="scroll">
          <table>
            <thead>
              <tr>
                <th>On</th><th>Name</th><th>Conf</th><th>Template</th><th>Replay</th>
                <th>Post Delay</th><th>Wait Before</th><th>Timeout</th><th>Verify</th>
                <th>Retry</th><th>Retry Template</th><th>Retry Conf</th><th>Move</th>
              </tr>
            </thead>
            <tbody id="steps"></tbody>
          </table>
        </div>
      </section>

      <section id="pageSettings" class="page">
        <div class="row" style="justify-content:space-between">
          <h2>Setting Device</h2>
          <div class="row">
            <button onclick="sendSelectedCommand('test_ldplayer')">Test LDPlayer</button>
            <button class="primary" onclick="saveConfig()">Save Config</button>
          </div>
        </div>
        <div class="grid2">
          <div class="card">
            <h2>Device</h2>
            <label>ADB path</label><input id="adbPath" oninput="setConfig('device.adb_path', this.value)" />
            <label>Serial</label><input id="adbSerial" oninput="setConfig('device.adb_serial', this.value)" />
          </div>
          <div class="card">
            <h2>Loop Settings</h2>
            <label>Scan interval</label><input id="scanInterval" oninput="setNumber('loop.scan_interval', this.value)" />
            <label>Delay min</label><input id="delayMin" oninput="setNumber('loop.min_delay', this.value)" />
            <label>Delay max</label><input id="delayMax" oninput="setNumber('loop.max_delay', this.value)" />
            <label>Jitter px</label><input id="jitter" oninput="setNumber('loop.jitter', this.value)" />
            <label>Retry limit</label><input id="retryLimit" oninput="setNumber('loop.retry_limit', this.value)" />
            <label>Verify delay</label><input id="verifyDelay" oninput="setNumber('loop.verify_delay', this.value)" />
          </div>
        </div>
        <div class="card" style="margin-top:12px">
          <h2>Replay</h2>
          <div class="grid2">
            <label><input id="loopReplayEnabled" type="checkbox" onchange="setConfig('recorder.loop_replay_enabled', this.checked)" /> Run replay inside loop</label>
            <label><input id="loopTapTrigger" type="checkbox" onchange="setConfig('recorder.loop_tap_trigger', this.checked)" /> Tap trigger before replay</label>
          </div>
          <div class="grid2">
            <div><label>Trigger mode</label><select id="loopTriggerMode" onchange="setConfig('recorder.loop_trigger_mode', this.value)"><option value="template">template</option><option value="step">step</option></select></div>
            <div><label>Trigger step</label><input id="loopTriggerStep" oninput="setConfig('recorder.loop_trigger_step', this.value)" /></div>
            <div><label>Trigger template</label><input id="loopTriggerTemplate" oninput="setConfig('recorder.loop_trigger_template', this.value)" /></div>
            <div><label>Trigger confidence</label><input id="loopTriggerConfidence" oninput="setNumber('recorder.loop_trigger_confidence', this.value)" /></div>
            <div><label>Replay file</label><input id="loopReplayFile" oninput="setConfig('recorder.loop_replay_file', this.value)" /></div>
            <div><label>Replay delay</label><input id="loopReplayDelay" oninput="setNumber('recorder.loop_replay_delay', this.value)" /></div>
          </div>
        </div>
      </section>
    </div>
  </main>
  <script>
    const licenseInput = document.getElementById('licenseKey');
    licenseInput.value = localStorage.getItem('ckr_license_key') || '';
    let summaryData = null;
    let configData = null;
    let selectedDeviceId = localStorage.getItem('ckr_selected_device') || '';
    let activeGroup = 'sequence';

    function esc(value) {
      return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
    function licenseKey() { return licenseInput.value.trim(); }
    function logLine(message) {
      log.innerHTML = `<div>${esc(new Date().toLocaleTimeString())} ${esc(message)}</div>` + log.innerHTML;
    }
    async function request(path, payload) {
      const res = await fetch(path, {
        method:'POST',
        headers:{'content-type':'application/json'},
        body:JSON.stringify({license_key: licenseKey(), ...(payload || {})})
      });
      if (!res.ok) throw new Error(await res.text());
      return await res.json();
    }
    function selectPage(page) {
      for (const name of ['monitor', 'flow', 'settings']) {
        document.getElementById(`page${name[0].toUpperCase()}${name.slice(1)}`).classList.toggle('active', name === page);
        document.getElementById(`nav${name[0].toUpperCase()}${name.slice(1)}`).classList.toggle('active', name === page);
      }
    }
    async function saveLicense() {
      localStorage.setItem('ckr_license_key', licenseKey());
      configData = null;
      await refresh();
      await loadConfig(false);
    }
    async function downloadAgent() {
      if (!licenseKey()) return;
      const res = await fetch('/api/user/download-agent', {
        method:'POST',
        headers:{'content-type':'application/json'},
        body:JSON.stringify({license_key: licenseKey()})
      });
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'CookieRunAgent-portable.zip';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }
    async function loadConfig(showLog) {
      if (!licenseKey()) return;
      const data = await request('/api/user/config', {});
      configData = data.config;
      renderConfig();
      if (showLog) logLine('Config reloaded.');
    }
    async function saveConfig(showLog=true) {
      if (!configData) await loadConfig(false);
      const data = await request('/api/user/config/save', {config: configData});
      configData = data.config;
      renderConfig();
      if (showLog) logLine('Config saved.');
    }
    function setPath(path, value) {
      const parts = path.split('.');
      let target = configData;
      for (const part of parts.slice(0, -1)) target = target[part];
      target[parts[parts.length - 1]] = value;
    }
    function setConfig(path, value) { if (configData) setPath(path, value); }
    function setNumber(path, value) {
      if (!configData) return;
      const number = value === '' ? 0 : Number(value);
      setPath(path, Number.isFinite(number) ? number : 0);
    }
    function valueText(value) {
      if (Array.isArray(value)) return value.join(', ');
      return value ?? '';
    }
    function parseMaybeNumber(value) {
      if (value === '') return null;
      const number = Number(value);
      return Number.isFinite(number) ? number : null;
    }
    function parseDelay(value) {
      const text = String(value || '').trim();
      if (!text) return null;
      const parts = text.split(',').map(part => Number(part.trim())).filter(Number.isFinite);
      if (parts.length === 2) return parts;
      if (parts.length === 1) return parts[0];
      return null;
    }
    function selectGroup(group) {
      activeGroup = group;
      groupSequence.classList.toggle('active', group === 'sequence');
      groupInterrupts.classList.toggle('active', group === 'interrupts');
      renderSteps();
    }
    function stepList() { return configData ? configData[activeGroup] : []; }
    function replayMarker(step) {
      const recorder = configData?.recorder || {};
      if (!recorder.loop_replay_enabled || activeGroup !== 'sequence') return '';
      const mode = recorder.loop_trigger_mode;
      const byStep = String(step.name || '').trim().toLowerCase() === String(recorder.loop_trigger_step || '').trim().toLowerCase();
      const byTemplate = String(step.template || '').trim() === String(recorder.loop_trigger_template || '').trim();
      if ((mode === 'step' && (byStep || byTemplate)) || (mode === 'template' && byTemplate)) return 'VIDEO';
      return '';
    }
    function updateStep(index, field, value, kind) {
      const step = stepList()[index];
      if (!step) return;
      if (kind === 'bool') step[field] = value;
      else if (kind === 'number') {
        const parsed = parseMaybeNumber(value);
        if (parsed === null) delete step[field]; else step[field] = parsed;
      } else if (kind === 'delay') {
        const parsed = parseDelay(value);
        if (parsed === null) delete step[field]; else step[field] = parsed;
      } else {
        if (value === '' && ['retry_template'].includes(field)) delete step[field]; else step[field] = value;
      }
      if (field === 'template' || field === 'name') renderSteps();
    }
    function addStep() {
      stepList().push({enabled:true, name:'New Step', template:'templates/new_step.png', confidence:0.85});
      renderSteps();
    }
    function deleteStep(index) {
      stepList().splice(index, 1);
      renderSteps();
    }
    function moveStep(index, delta) {
      const list = stepList();
      const next = index + delta;
      if (next < 0 || next >= list.length) return;
      [list[index], list[next]] = [list[next], list[index]];
      renderSteps();
    }
    function renderSteps() {
      if (!configData) return;
      steps.innerHTML = stepList().map((step, index) => `
        <tr>
          <td><input type="checkbox" ${step.enabled !== false ? 'checked' : ''} onchange="updateStep(${index}, 'enabled', this.checked, 'bool')" /></td>
          <td><input value="${esc(step.name || '')}" oninput="updateStep(${index}, 'name', this.value)" /></td>
          <td><input value="${esc(step.confidence ?? '')}" oninput="updateStep(${index}, 'confidence', this.value, 'number')" /></td>
          <td><input value="${esc(step.template || '')}" oninput="updateStep(${index}, 'template', this.value)" /></td>
          <td class="warn">${esc(replayMarker(step))}</td>
          <td><input value="${esc(valueText(step.post_delay))}" oninput="updateStep(${index}, 'post_delay', this.value, 'delay')" /></td>
          <td><input value="${esc(valueText(step.wait_before))}" oninput="updateStep(${index}, 'wait_before', this.value, 'delay')" /></td>
          <td><input value="${esc(step.timeout ?? '')}" oninput="updateStep(${index}, 'timeout', this.value, 'number')" /></td>
          <td><input type="checkbox" ${step.verify_click ? 'checked' : ''} onchange="updateStep(${index}, 'verify_click', this.checked, 'bool')" /></td>
          <td><input value="${esc(step.retry_after ?? '')}" oninput="updateStep(${index}, 'retry_after', this.value, 'number')" /></td>
          <td><input value="${esc(step.retry_template || '')}" oninput="updateStep(${index}, 'retry_template', this.value)" /></td>
          <td><input value="${esc(step.retry_confidence ?? '')}" oninput="updateStep(${index}, 'retry_confidence', this.value, 'number')" /></td>
          <td class="row">
            <button onclick="moveStep(${index}, -1)">Up</button>
            <button onclick="moveStep(${index}, 1)">Down</button>
            <button class="danger" onclick="deleteStep(${index})">Delete</button>
          </td>
        </tr>
      `).join('');
    }
    function setInput(id, value) {
      const element = document.getElementById(id);
      if (element) element.value = value ?? '';
    }
    function renderConfig() {
      if (!configData) return;
      setInput('adbPath', configData.device.adb_path);
      setInput('adbSerial', configData.device.adb_serial);
      setInput('scanInterval', configData.loop.scan_interval);
      setInput('delayMin', configData.loop.min_delay);
      setInput('delayMax', configData.loop.max_delay);
      setInput('jitter', configData.loop.jitter);
      setInput('retryLimit', configData.loop.retry_limit);
      setInput('verifyDelay', configData.loop.verify_delay);
      loopReplayEnabled.checked = !!configData.recorder.loop_replay_enabled;
      loopTapTrigger.checked = !!configData.recorder.loop_tap_trigger;
      loopTriggerMode.value = configData.recorder.loop_trigger_mode || 'template';
      setInput('loopTriggerStep', configData.recorder.loop_trigger_step);
      setInput('loopTriggerTemplate', configData.recorder.loop_trigger_template);
      setInput('loopTriggerConfidence', configData.recorder.loop_trigger_confidence);
      setInput('loopReplayFile', configData.recorder.loop_replay_file);
      setInput('loopReplayDelay', configData.recorder.loop_replay_delay);
      renderSteps();
    }
    function chooseDevice(deviceId) {
      selectedDeviceId = deviceId;
      localStorage.setItem('ckr_selected_device', deviceId);
      renderSummary();
    }
    function currentDevice() {
      const devices = summaryData?.devices || [];
      return devices.find(d => d.device_id === selectedDeviceId) || devices.find(d => d.online) || devices[0];
    }
    async function waitCommand(commandId) {
      for (let attempt = 0; attempt < 90; attempt++) {
        const data = await request(`/api/user/commands/${encodeURIComponent(commandId)}`, {});
        const command = data.command;
        if (!['queued', 'sent'].includes(command.status)) return command;
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      throw new Error(`Command ${commandId} did not finish in time`);
    }
    async function sendSelectedCommand(command) {
      const device = currentDevice();
      if (!device) { logLine('No agent connected.'); return; }
      if (command === 'start_bot') await saveConfig(false);
      const sent = await request(`/api/user/devices/${encodeURIComponent(device.device_id)}/commands`, {command, payload:{}});
      logLine(`Sent ${command} to ${device.device_name || device.device_id}`);
      const result = await waitCommand(sent.command_id);
      if (command === 'screenshot' && result.response_json) {
        try {
          const response = JSON.parse(result.response_json);
          if (response.png_base64) {
            screenshot.src = `data:image/png;base64,${response.png_base64}`;
            screenshot.style.display = 'block';
          }
        } catch (_) {}
      }
      await refresh();
    }
    function renderSummary() {
      const data = summaryData;
      const lic = data?.license || {};
      const device = currentDevice();
      if (device && !selectedDeviceId) selectedDeviceId = device.device_id;
      const cls = data?.license_ok ? 'ok' : 'bad';
      downloadButton.disabled = !data?.license_ok;
      licenseInfo.innerHTML = data ? `
        <div>Status: <span class="${cls}">${esc(data.license_reason)}</span></div>
        <div>Customer: ${esc(lic.customer_name || '-')}</div>
        <div>Expires: ${esc(lic.expires_at || 'never')}</div>
      ` : 'Enter your license key.';
      selectedDevice.innerHTML = device ? `
        <div class="mono">${esc(device.device_id)}</div>
        <div>${esc(device.device_name || '-')} ${esc(device.agent_version || '')}</div>
        <div class="${device.online ? 'ok' : 'bad'}">${device.online ? 'Online' : 'Offline'}</div>
      ` : '<span class="warn">No agent connected.</span>';
      metrics.innerHTML = `
        <div class="metric"><div class="label">License</div><div class="value ${cls}">${esc(data?.license_reason || '-')}</div></div>
        <div class="metric"><div class="label">Agent</div><div class="value ${device?.online ? 'ok' : 'bad'}">${device?.online ? 'Online' : 'Offline'}</div></div>
        <div class="metric"><div class="label">Device</div><div class="value">${esc(device?.device_name || '-')}</div></div>
        <div class="metric"><div class="label">Bot</div><div class="value">${esc(device?.last_status?.bot_running ? 'Running' : '-')}</div></div>
      `;
      devices.innerHTML = (data?.devices || []).map(d => `
        <tr>
          <td class="${d.online ? 'ok' : 'bad'}">${d.online ? 'Online' : 'Offline'}</td>
          <td><div class="mono">${esc(d.device_id)}</div><div class="muted">${esc(d.device_name)} ${esc(d.agent_version)}</div></td>
          <td>${esc(d.last_seen_at)}</td>
          <td class="row"><button class="${d.device_id === selectedDeviceId ? 'active' : ''}" onclick="chooseDevice('${esc(d.device_id)}')">Select</button></td>
        </tr>
      `).join('') || '<tr><td colspan="4" class="warn">No agent connected yet. Download and open the agent.</td></tr>';
      log.innerHTML = (data?.commands || []).map(c => `<div>[${esc(c.created_at)}] ${esc(c.command)} ${esc(c.status)} ${esc(c.response_json || '')}</div>`).join('');
    }
    async function refresh() {
      if (!licenseKey()) return;
      try {
        summaryData = await request('/api/user/summary', {});
        renderSummary();
        if (!configData) await loadConfig(false);
      } catch (err) {
        downloadButton.disabled = true;
        licenseInfo.innerHTML = `<span class="bad">${esc(err.message)}</span>`;
      }
    }
    setInterval(refresh, 3000);
    refresh();
  </script>
</body>
</html>
"""
