# Web-Ui-Beer MVP Plan

Target domain:

```text
https://auto-ckr.beersval.com
```

Single-domain routes:

```text
Web/Admin UI   /admin
Admin API      /api/admin/*
Agent socket   /ws/agent
```

Architecture:

```text
Browser Admin UI
  -> auto-ckr.beersval.com
  -> CKR control server on Droplet
  -> WebSocket
  -> Windows Agent on customer PC
  -> local adb.exe
  -> LDPlayer 14
```

Do not expose LDPlayer ADB to the internet. The Windows Agent always connects outbound to the server.

## Manual License Flow

1. Customer pays manually through LINE/QR.
2. Admin opens `/admin`.
3. Admin generates a license key.
4. Customer puts the key into Windows Agent.
5. Agent binds the key to one device by default.
6. Admin can revoke or reset device binding later.

## Current MVP Scope

Server:

- Manual license generation.
- Device registration through WebSocket.
- Admin command send.
- Command polling until `done` or `error`.
- Command logs.

Agent:

- Reads `agent/config.local.json`.
- Connects to `/ws/agent`.
- Supports `status`, `test_ldplayer`, `start_bot`, `kill_bot`, and `screenshot`.
- Uses LDPlayer 14 default ADB path.

## Next Build Steps

1. Move bot loop logic from Tkinter into `bot_core/`.
2. Make Agent use `bot_core` directly instead of spawning `auto_clicker.py`.
3. Add screenshot preview in admin UI.
4. Add config/template upload and sync.
5. Package Windows Agent as a one-click app.
