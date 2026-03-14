# AutoDaggerTunnel

AutoDaggerTunnel is a Telegram bot for automated DaggerConnect client deployment and tunnel testing across your outbound servers.

It is designed for fast target queue testing (`IP:PORT`) with live status updates, SSH automation, and clear end-of-run summaries.

## Core capabilities

- Manage outbound servers in bot UI:
  - Add
  - List
  - Edit
  - Delete (with confirmation)
  - On-demand SSH connectivity check
- Run tests against one or multiple targets in queue mode
- Run on:
  - All saved servers
  - One selected server
- Stream live progress in a single updating Telegram message
- Save final per-server results + final queue summary

## Supported tunnel modes

- `quantummux`:
  - Auto-configure client
  - Restart client service
  - Auto-log diagnostics
  - Pattern-based fail detection + cleanup on fail
- `ghostmux`:
  - Auto-configure client
  - Restart client service
  - Auto-log diagnostics
  - Pattern-based fail detection + cleanup on fail
- `tun+bip`:
  - Config-only mode
  - Manual verification required

## Test result logic

- `success`: connection signal without known failure pattern
- `failed_pattern`: known bad behavior detected (cleanup applied)
- `manual_review`: no known bad pattern, but no clear success signal
- `ssh_error`: SSH connection/auth/runtime SSH failure
- `setup_error`: remote setup/config/systemd failure
- `cancelled`: stopped by user during execution

Known fail patterns currently include:
- unstable reconnect loops (`disconnected/reconnect/streams=0`)
- reconnect attempt storm
- persistent reconnect loop pattern
- OOM kill signal

## One-line install (Linux)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/B3hnamR/AutoDaggerTunnel/main/install.sh)
```

This launches the manager menu.

## Manager menu (bash)

- Install / Update
- Reconfigure bot settings
- Start / Stop / Restart bot service
- Service status
- Live logs
- Show current config (token masked)
- Update bot now (pull + deps + restart)
- Uninstall

## Setup configuration

During manager setup:

- Telegram bot token
- Access mode:
  - `public`
  - `private` (allow-list Telegram user IDs)
- Default PSK
- Test window seconds
- SSH connect timeout
- SSH command timeout
- DaggerConnect binary URL

## Runtime paths

- App: `/opt/autodaggertunnel/app`
- Virtualenv: `/opt/autodaggertunnel/venv`
- Env file: `/opt/autodaggertunnel/.env`
- Data dir: `/opt/autodaggertunnel/data`
- DB: `/opt/autodaggertunnel/data/servers.db`
- Service: `/etc/systemd/system/autodaggertunnel.service`

## Service commands

```bash
systemctl start autodaggertunnel.service
systemctl stop autodaggertunnel.service
systemctl restart autodaggertunnel.service
systemctl status autodaggertunnel.service
journalctl -u autodaggertunnel.service -f
```

## Requirements

- Linux host with `systemd`
- Root access on manager host
- Outbound internet access for initial install/update
- Reachable SSH from manager host to outbound servers
- Root (or equivalent) privileges on outbound servers for DaggerConnect/service operations

## Notes

- Existing remote DaggerConnect client config/service is overwritten by bot-managed config.
- If known failure patterns are detected in auto-log modes, bot cleans remote client config/service automatically.
- `tun+bip` mode is configuration-only by design; validate tunnel manually.
