from __future__ import annotations

import json
import os
import secrets
import sqlite3
import io
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, Response


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


async def dispatch_device_command(device_id: str, command: str, payload: dict[str, Any]) -> dict[str, Any]:
    command = command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="command is required")
    session = agents.get(device_id)
    if not session:
        raise HTTPException(status_code=404, detail="agent is offline")
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
  <title>Cookie Run Agent Portal</title>
  <style>
    :root {
      color-scheme: dark;
      --bg:#0f172a; --panel:#111827; --card:#1f2937; --line:#334155;
      --text:#f8fafc; --muted:#94a3b8; --accent:#22c55e; --danger:#ef4444; --warn:#f59e0b;
    }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--text); font-family:Segoe UI, Arial, sans-serif; }
    header { display:flex; justify-content:space-between; align-items:center; gap:16px; padding:18px 24px; background:#020617; border-bottom:1px solid var(--line); }
    h1 { margin:0; font-size:20px; }
    main { padding:18px 24px; display:grid; grid-template-columns:340px 1fr; gap:16px; }
    section { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }
    h2 { margin:0 0 12px; font-size:15px; }
    label { display:block; margin:10px 0 4px; color:var(--muted); font-size:12px; }
    input { width:100%; background:#020617; color:var(--text); border:1px solid var(--line); border-radius:6px; padding:10px; }
    button { background:var(--card); color:var(--text); border:1px solid var(--line); border-radius:6px; padding:8px 11px; cursor:pointer; white-space:nowrap; }
    button.primary { background:var(--accent); color:#052e16; border-color:var(--accent); font-weight:700; }
    button.danger { background:var(--danger); color:#fff; border-color:var(--danger); }
    .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .metric { display:grid; grid-template-columns:110px 1fr; gap:6px; font-size:13px; margin:6px 0; }
    .label { color:var(--muted); }
    .mono { font-family:Consolas, monospace; }
    .muted { color:var(--muted); }
    .ok { color:var(--accent); font-weight:700; }
    .bad { color:var(--danger); font-weight:700; }
    .warn { color:var(--warn); font-weight:700; }
    table { width:100%; border-collapse:separate; border-spacing:0 8px; font-size:13px; }
    th { text-align:left; color:var(--muted); font-size:12px; padding:0 8px 2px; }
    td { background:#101827; text-align:left; padding:10px 8px; vertical-align:top; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
    td:first-child { border-left:1px solid var(--line); border-radius:8px 0 0 8px; }
    td:last-child { border-right:1px solid var(--line); border-radius:0 8px 8px 0; }
    #log { height:280px; overflow:auto; background:#020617; border:1px solid var(--line); padding:10px; border-radius:6px; font-family:Consolas, monospace; font-size:12px; }
    #screenshot { max-width:100%; border:1px solid var(--line); border-radius:8px; display:none; margin-top:10px; }
    @media (max-width: 900px) { header { align-items:flex-start; flex-direction:column; } main { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Cookie Run Agent Portal</h1>
      <div class="muted">__PUBLIC_BASE_URL__</div>
    </div>
    <a class="muted" href="/admin">Admin</a>
  </header>
  <main>
    <div>
      <section>
        <h2>License</h2>
        <label>License Key</label>
        <input id="licenseKey" placeholder="CKR-XXXX-XXXX-XXXX-XXXX" />
        <p class="row">
          <button class="primary" onclick="saveLicense()">Connect</button>
          <button onclick="refresh()">Refresh</button>
          <button id="downloadButton" onclick="downloadAgent()" disabled>Download Agent</button>
        </p>
        <div id="licenseInfo" class="muted">Enter your license key.</div>
      </section>
      <section style="margin-top:16px">
        <h2>Latest Screenshot</h2>
        <div class="muted">Use Screenshot after the agent is online.</div>
        <img id="screenshot" alt="LDPlayer screenshot" />
      </section>
    </div>

    <div>
      <section>
        <h2>Device</h2>
        <table>
          <thead><tr><th>Status</th><th>Device</th><th>Actions</th></tr></thead>
          <tbody id="devices"></tbody>
        </table>
      </section>
      <section style="margin-top:16px">
        <h2>Log</h2>
        <div id="log"></div>
      </section>
    </div>
  </main>
  <script>
    const licenseInput = document.getElementById('licenseKey');
    licenseInput.value = localStorage.getItem('ckr_license_key') || '';

    function esc(value) {
      return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
    function licenseKey() {
      return licenseInput.value.trim();
    }
    function saveLicense() {
      localStorage.setItem('ckr_license_key', licenseKey());
      refresh();
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
    async function waitCommand(commandId) {
      for (let attempt = 0; attempt < 60; attempt++) {
        const data = await request(`/api/user/commands/${encodeURIComponent(commandId)}`, {});
        const command = data.command;
        if (!['queued', 'sent'].includes(command.status)) return command;
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      throw new Error(`Command ${commandId} did not finish in time`);
    }
    async function sendCommand(deviceId, command) {
      const sent = await request(`/api/user/devices/${encodeURIComponent(deviceId)}/commands`, {command, payload:{}});
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
    async function refresh() {
      if (!licenseKey()) return;
      try {
        const data = await request('/api/user/summary', {});
        const lic = data.license;
        const cls = data.license_ok ? 'ok' : 'bad';
        downloadButton.disabled = !data.license_ok;
        licenseInfo.innerHTML = `
          <div class="metric"><span class="label">Status</span><span class="${cls}">${esc(data.license_reason)}</span></div>
          <div class="metric"><span class="label">Customer</span><span>${esc(lic.customer_name || '-')}</span></div>
          <div class="metric"><span class="label">Expires</span><span>${esc(lic.expires_at || 'never')}</span></div>
        `;
        devices.innerHTML = data.devices.map(d => `
          <tr>
            <td class="${d.online ? 'ok' : 'bad'}">${d.online ? 'Online' : 'Offline'}</td>
            <td>
              <div class="mono">${esc(d.device_id)}</div>
              <div class="muted">${esc(d.device_name)} ${esc(d.agent_version)}</div>
              <div class="muted">Last seen: ${esc(d.last_seen_at)}</div>
            </td>
            <td class="row">
              <button onclick="sendCommand('${esc(d.device_id)}','status')">Status</button>
              <button onclick="sendCommand('${esc(d.device_id)}','test_ldplayer')">Test LDPlayer</button>
              <button class="primary" onclick="sendCommand('${esc(d.device_id)}','start_bot')">Start</button>
              <button class="danger" onclick="sendCommand('${esc(d.device_id)}','kill_bot')">Kill</button>
              <button onclick="sendCommand('${esc(d.device_id)}','screenshot')">Screenshot</button>
            </td>
          </tr>`).join('') || '<tr><td colspan="3" class="warn">No agent connected yet.</td></tr>';
        log.innerHTML = data.commands.map(c => `<div>[${esc(c.created_at)}] ${esc(c.command)} ${esc(c.status)} ${esc(c.response_json || '')}</div>`).join('');
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
