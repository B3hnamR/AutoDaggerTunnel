# AutoDaggerTunnel

Telegram bot for automated DaggerConnect client deployment/testing on your outbound servers.

## What it does

- Save outbound servers (name, host, ssh user, ssh password)
- List / edit / delete saved servers
- Start tunnel tests for one or multiple targets (`IP:PORT` queue)
- Select tunnel mode before test:
  - `quantummux`: configure + run + auto log diagnostics
  - `tun+bip`: configure only (manual validation required)
- For each saved server:
  - SSH connect
  - Install/update DaggerConnect binary
  - Write `/etc/DaggerConnect/client.yaml` with given target
  - Write systemd client service
  - Start service and stream live diagnostic events into Telegram
  - Detect known failure pattern (disconnect/reconnect/streams=0 or oom-kill)
  - If failure pattern is detected: cleanup client config/service automatically
- Send per-server report + final summary
  - End summary includes successful servers per target

## One-line install on Linux server

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/B3hnamR/AutoDaggerTunnel/main/install.sh)
```

This opens the manager menu.

## Manager menu (bash)

- Install / Update
- Reconfigure bot (token, public/private mode, allowed telegram IDs, psk, timeouts)
- Start / Stop / Restart
- Status
- Live logs
- Update bot now (pull latest + reinstall deps + restart service)

## Telegram bot access mode

During setup:

- `public`: everyone can use the bot
- `private`: only allowed telegram user IDs can use it (comma separated list)

You can always change this with `Reconfigure bot`.

## Runtime paths

- App: `/opt/autodaggertunnel/app`
- Env: `/opt/autodaggertunnel/.env`
- DB: `/opt/autodaggertunnel/data/servers.db`
- Service: `autodaggertunnel.service`

## Manual service commands

```bash
systemctl start autodaggertunnel.service
systemctl stop autodaggertunnel.service
systemctl restart autodaggertunnel.service
systemctl status autodaggertunnel.service
journalctl -u autodaggertunnel.service -f
```

## Notes

- For remote servers, SSH user should be `root` (or have root-equivalent privileges).
- Unknown log pattern is reported as `manual_review` for manual validation.
- Known bad pattern triggers automatic cleanup on the tested remote server.
