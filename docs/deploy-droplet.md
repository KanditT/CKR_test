# Deploy To Existing Droplet

Target:

```text
auto-ckr.beersval.com -> 159.223.49.95
```

Server path:

```text
/opt/ckr-control
```

## Cloudflare DNS

Create an A record:

```text
Type: A
Name: auto-ckr
Value: 159.223.49.95
Proxy: DNS only or proxied
```

## Droplet Files

Copy this branch to:

```text
/opt/ckr-control
```

Create `/opt/ckr-control/server/.env`:

```text
ADMIN_TOKEN=<long-random-admin-token>
PUBLIC_BASE_URL=https://auto-ckr.beersval.com
CKR_DATA_DIR=/app/data
CKR_DB_PATH=/app/data/ckr_control.sqlite3
```

Start:

```bash
cd /opt/ckr-control/server
docker compose -f compose.yaml up -d --build
```

The container listens on localhost only:

```text
127.0.0.1:8088 -> server:8000
```

## Caddy

Add a new site block without touching the existing project block:

```caddy
auto-ckr.beersval.com {
    reverse_proxy 127.0.0.1:8088
}
```

Reload Caddy:

```bash
caddy reload --config /etc/caddy/Caddyfile
```

## Production Check

1. Open `https://auto-ckr.beersval.com/admin`.
2. Enter admin token.
3. Generate a license.
4. Configure Windows Agent:

```json
{
  "server_url": "wss://auto-ckr.beersval.com/ws/agent",
  "license_key": "CKR-....",
  "adb_path": "C:\\LDPlayer\\LDPlayer14\\adb.exe",
  "adb_serial": "127.0.0.1:5555"
}
```

5. Run agent.
6. Confirm device is Online.
7. Send `Status`.
8. Send `Test LDPlayer`.
9. Send `Start`.
10. Send `Kill`.
