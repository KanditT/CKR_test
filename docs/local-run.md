# Local Run

## Server

Install server dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r server\requirements.txt
```

Run the server:

```powershell
$env:ADMIN_TOKEN="dev-admin-token"
$env:PUBLIC_BASE_URL="http://localhost:8000"
$env:CKR_DATA_DIR="server/data"
$env:CKR_DB_PATH="server/data/ckr_control.sqlite3"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir server --host 127.0.0.1 --port 8000
```

Open:

```text
http://localhost:8000/admin
```

Use `dev-admin-token` in the Admin token field.

## Agent

Install agent dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r agent\requirements.txt
```

Create local config:

```powershell
Copy-Item agent\config.example.json agent\config.local.json
```

Edit:

```json
{
  "server_url": "ws://localhost:8000/ws/agent",
  "license_key": "CKR-PASTE-LICENSE-HERE",
  "device_name": "BEERS-PC",
  "adb_path": "C:\\LDPlayer\\LDPlayer14\\adb.exe",
  "adb_serial": "127.0.0.1:5555",
  "python_exe": "",
  "bot_script": "auto_clicker.py"
}
```

Run:

```powershell
.\.venv\Scripts\python.exe agent\agent.py --config agent\config.local.json
```

## Smoke Test

This starts a temporary local server, generates a license, connects a mock agent through WebSocket, sends a command, and polls until the command is complete.

```powershell
.\.venv\Scripts\python.exe server\smoke_test.py
```
