from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse


DATA_DIR = Path(os.getenv("CKR_DATA_DIR", "server/data"))
DB_PATH = Path(os.getenv("CKR_DB_PATH", str(DATA_DIR / "ckr_control.sqlite3")))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "dev-admin-token")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
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


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/admin")


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_page() -> str:
    return ADMIN_HTML.replace("__PUBLIC_BASE_URL__", PUBLIC_BASE_URL)


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


@app.post("/api/admin/devices/{device_id}/commands")
async def send_command(device_id: str, request: Request, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    body = await request.json()
    command = str(body.get("command", "")).strip()
    payload = body.get("payload") or {}
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


@app.get("/api/admin/commands/{command_id}")
def get_command(command_id: int, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    with db_conn() as conn:
        command = conn.execute("select * from command_logs where id = ?", (command_id,)).fetchone()
    if command is None:
        raise HTTPException(status_code=404, detail="command not found")
    return {"command": dict(command)}


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
    main { padding:18px 24px; display:grid; gap:16px; grid-template-columns: 360px 1fr; }
    section { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }
    h2 { margin:0 0 12px; font-size:15px; }
    label { display:block; margin:10px 0 4px; color:var(--muted); font-size:12px; }
    input, textarea, select { width:100%; background:#020617; color:var(--text); border:1px solid var(--line); border-radius:6px; padding:9px; }
    button { background:var(--card); color:var(--text); border:1px solid var(--line); border-radius:6px; padding:8px 11px; cursor:pointer; }
    button.primary { background:var(--accent); color:#052e16; border-color:var(--accent); font-weight:700; }
    button.danger { background:var(--danger); color:white; border-color:var(--danger); }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th, td { text-align:left; padding:8px; border-bottom:1px solid var(--line); vertical-align:top; }
    th { color:var(--muted); font-size:12px; }
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
        <label>LINE Name</label><input id="lineName" placeholder="LINE display name" />
        <label>Days</label><input id="days" placeholder="30 (blank = no expiry)" />
        <label>Max Devices</label><input id="maxDevices" value="1" />
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
        line_name: lineName.value,
        days: days.value,
        max_devices: maxDevices.value,
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
