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
AGENT_SERVER_URL=wss://auto-ckr.beersval.com/ws/agent
CKR_DATA_DIR=/app/data
CKR_DB_PATH=/app/data/ckr_control.sqlite3
CKR_DOWNLOAD_DIR=/app/downloads
```

Copy the built portable agent zip to:

```text
/opt/ckr-control/server/downloads/CookieRunAgent-portable.zip
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

1. Open admin: `https://auto-ckr.beersval.com/admin`.
2. Enter admin token.
3. Generate a license.
4. Open user portal: `https://auto-ckr.beersval.com/user`.
5. Enter the generated license key.
6. Click `Download Agent`.
7. Extract the zip on Windows.
8. Double-click `CookieRunAgent.exe`.
9. Confirm the user portal can only see devices for that license.
10. Confirm device is Online in both admin and user portal.
11. Send `Status`.
12. Send `Test LDPlayer`.
13. Send `Start`.
14. Send `Kill`.
