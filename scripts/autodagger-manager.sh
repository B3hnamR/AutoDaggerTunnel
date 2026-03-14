#!/usr/bin/env bash
set -euo pipefail

APP_NAME="autodaggertunnel"
INSTALL_DIR="/opt/${APP_NAME}"
APP_DIR="${INSTALL_DIR}/app"
VENV_DIR="${INSTALL_DIR}/venv"
DATA_DIR="${INSTALL_DIR}/data"
ENV_FILE="${INSTALL_DIR}/.env"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"

REPO_URL="${AUTO_DAGGER_REPO_URL:-https://github.com/B3hnamR/AutoDaggerTunnel.git}"
REPO_BRANCH="${AUTO_DAGGER_BRANCH:-main}"

REPO_OWNER="${AUTO_DAGGER_REPO_OWNER:-B3hnamR}"
REPO_NAME="${AUTO_DAGGER_REPO_NAME:-AutoDaggerTunnel}"
DAGGER_BINARY_URL_DEFAULT="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${REPO_BRANCH}/DaggerConnect"

print_banner() {
  clear
  echo "=============================================="
  echo "         AutoDaggerTunnel Manager"
  echo "=============================================="
  echo "Install dir : ${INSTALL_DIR}"
  echo "Repo        : ${REPO_URL} (${REPO_BRANCH})"
  echo
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "[ERROR] Run as root."
    exit 1
  fi
}

require_systemd() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "[ERROR] systemctl not found. This script requires a systemd-based Linux server."
    exit 1
  fi
}

pkg_install() {
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y git python3 python3-venv python3-pip ca-certificates curl
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y git python3 python3-pip python3-virtualenv ca-certificates curl
  elif command -v yum >/dev/null 2>&1; then
    yum install -y git python3 python3-pip python3-virtualenv ca-certificates curl
  else
    echo "[ERROR] Unsupported package manager. Install git/python3/python3-venv manually."
    exit 1
  fi
}

clone_or_update_repo() {
  mkdir -p "${INSTALL_DIR}"

  if [[ -d "${APP_DIR}/.git" ]]; then
    echo "[INFO] Updating existing repository..."
    git -C "${APP_DIR}" fetch --all --prune
    git -C "${APP_DIR}" checkout "${REPO_BRANCH}"
    git -C "${APP_DIR}" pull --ff-only origin "${REPO_BRANCH}"
  else
    echo "[INFO] Cloning repository..."
    rm -rf "${APP_DIR}"
    git clone --depth 1 --branch "${REPO_BRANCH}" "${REPO_URL}" "${APP_DIR}"
  fi
}

setup_python_env() {
  echo "[INFO] Preparing python virtual environment..."
  python3 -m venv "${VENV_DIR}"
  "${VENV_DIR}/bin/pip" install --upgrade pip
  "${VENV_DIR}/bin/pip" install -r "${APP_DIR}/bot/requirements.txt"
}

env_escape() {
  local val="$1"
  val="${val//\\/\\\\}"
  val="${val//\"/\\\"}"
  printf "%s" "${val}"
}

validate_ids() {
  local value="$1"
  if [[ -z "${value}" ]]; then
    return 1
  fi

  IFS=',' read -r -a ids <<<"${value}"
  for id in "${ids[@]}"; do
    id="$(echo "${id}" | xargs)"
    if [[ ! "${id}" =~ ^-?[0-9]+$ ]]; then
      return 1
    fi
  done
  return 0
}

prompt_config_and_write_env() {
  local bot_token access_mode allowed_ids default_psk test_window ssh_connect ssh_cmd dagger_url

  echo
  echo "[CONFIG] Telegram bot settings"
  while true; do
    read -r -p "Enter BOT_TOKEN: " bot_token
    if [[ -n "${bot_token}" ]]; then
      break
    fi
    echo "BOT_TOKEN cannot be empty."
  done

  echo
  echo "Select access mode:"
  echo "1) Public (all users can use bot)"
  echo "2) Private (only allowed Telegram user IDs)"

  while true; do
    read -r -p "Choice [1-2]: " mode_choice
    if [[ "${mode_choice}" == "1" ]]; then
      access_mode="public"
      allowed_ids=""
      break
    elif [[ "${mode_choice}" == "2" ]]; then
      access_mode="private"
      while true; do
        read -r -p "Allowed Telegram user IDs (comma separated): " allowed_ids
        if validate_ids "${allowed_ids}"; then
          break
        fi
        echo "Invalid format. Example: 123456,7891011"
      done
      break
    else
      echo "Please choose 1 or 2."
    fi
  done

  read -r -p "Default PSK [123]: " default_psk
  default_psk="${default_psk:-123}"

  read -r -p "Test window seconds [75]: " test_window
  test_window="${test_window:-75}"

  read -r -p "SSH connect timeout seconds [12]: " ssh_connect
  ssh_connect="${ssh_connect:-12}"

  read -r -p "SSH command timeout seconds [45]: " ssh_cmd
  ssh_cmd="${ssh_cmd:-45}"

  read -r -p "Dagger binary URL [${DAGGER_BINARY_URL_DEFAULT}]: " dagger_url
  dagger_url="${dagger_url:-${DAGGER_BINARY_URL_DEFAULT}}"

  mkdir -p "${DATA_DIR}"

  cat > "${ENV_FILE}" <<EOF
BOT_TOKEN="$(env_escape "${bot_token}")"
ACCESS_MODE="$(env_escape "${access_mode}")"
ALLOWED_USER_IDS="$(env_escape "${allowed_ids}")"
DEFAULT_PSK="$(env_escape "${default_psk}")"
APP_BASE_DIR="${INSTALL_DIR}"
DATA_DIR="${DATA_DIR}"
DB_PATH="${DATA_DIR}/servers.db"
KEY_FILE="${DATA_DIR}/secret.key"
TEST_WINDOW_SECONDS="$(env_escape "${test_window}")"
SSH_CONNECT_TIMEOUT="$(env_escape "${ssh_connect}")"
SSH_COMMAND_TIMEOUT="$(env_escape "${ssh_cmd}")"
DAGGER_BINARY_URL="$(env_escape "${dagger_url}")"
EOF

  chmod 600 "${ENV_FILE}"
  echo "[OK] Config written to ${ENV_FILE}"
}

create_service() {
  cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=AutoDaggerTunnel Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}/bot
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python -m autodagger_tunnel.app
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "${APP_NAME}.service" >/dev/null 2>&1 || true
  echo "[OK] Systemd service ready: ${APP_NAME}.service"
}

install_or_update() {
  pkg_install
  clone_or_update_repo
  setup_python_env

  if [[ ! -f "${ENV_FILE}" ]]; then
    prompt_config_and_write_env
  else
    echo "[INFO] Existing config found at ${ENV_FILE}"
    read -r -p "Do you want to reconfigure now? [y/N]: " answer
    if [[ "${answer}" =~ ^[Yy]$ ]]; then
      prompt_config_and_write_env
    fi
  fi

  create_service

  read -r -p "Start bot service now? [Y/n]: " run_now
  if [[ ! "${run_now}" =~ ^[Nn]$ ]]; then
    systemctl restart "${APP_NAME}.service"
    echo "[OK] Bot started."
  fi

  echo
  echo "Manager command for later:"
  echo "bash ${APP_DIR}/scripts/autodagger-manager.sh"
  echo "Logs: journalctl -u ${APP_NAME}.service -f"
}

reconfigure_only() {
  if [[ ! -d "${APP_DIR}" ]]; then
    echo "[ERROR] App is not installed yet. Run Install/Update first."
    read -r -p "Press Enter to continue..." _
    return
  fi

  prompt_config_and_write_env
  create_service
  echo "[OK] Reconfigured."
  read -r -p "Restart service now? [Y/n]: " ans
  if [[ ! "${ans}" =~ ^[Nn]$ ]]; then
    systemctl restart "${APP_NAME}.service"
  fi
}

update_bot_only() {
  if [[ ! -d "${APP_DIR}/.git" ]]; then
    echo "[ERROR] App is not installed yet. Run Install / Update first."
    read -r -p "Press Enter to continue..." _
    return
  fi

  pkg_install

  local before_ref after_ref
  before_ref="$(git -C "${APP_DIR}" rev-parse --short HEAD 2>/dev/null || echo "unknown")"

  clone_or_update_repo
  setup_python_env

  if [[ ! -f "${ENV_FILE}" ]]; then
    echo "[WARN] Env file not found. Please configure bot settings."
    prompt_config_and_write_env
  fi

  create_service
  systemctl restart "${APP_NAME}.service"

  after_ref="$(git -C "${APP_DIR}" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
  echo "[OK] Bot updated and restarted. Commit: ${before_ref} -> ${after_ref}"
  read -r -p "Press Enter to continue..." _
}

start_service() {
  systemctl start "${APP_NAME}.service"
  echo "[OK] Service started."
}

stop_service() {
  systemctl stop "${APP_NAME}.service"
  echo "[OK] Service stopped."
}

restart_service() {
  systemctl restart "${APP_NAME}.service"
  echo "[OK] Service restarted."
}

status_service() {
  systemctl status "${APP_NAME}.service" --no-pager || true
  read -r -p "Press Enter to continue..." _
}

logs_service() {
  echo "Press Ctrl+C to exit logs."
  journalctl -u "${APP_NAME}.service" -f --no-pager
}

show_current_config() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    echo "[INFO] No config file found."
    read -r -p "Press Enter to continue..." _
    return
  fi

  echo "Current config (${ENV_FILE}):"
  while IFS= read -r line; do
    if [[ "${line}" == BOT_TOKEN=* ]]; then
      echo "BOT_TOKEN=***hidden***"
    else
      echo "${line}"
    fi
  done < "${ENV_FILE}"

  read -r -p "Press Enter to continue..." _
}

uninstall_bot() {
  echo
  echo "WARNING: This will completely remove the bot, your configurations, and the database."
  read -r -p "Are you absolutely sure you want to uninstall? [y/N]: " ans
  if [[ ! "${ans}" =~ ^[Yy]$ ]]; then
    echo "Uninstall aborted."
    read -r -p "Press Enter to continue..." _
    return
  fi

  echo "[INFO] Stopping and disabling service..."
  systemctl stop "${APP_NAME}.service" >/dev/null 2>&1 || true
  systemctl disable "${APP_NAME}.service" >/dev/null 2>&1 || true

  echo "[INFO] Removing systemd service file..."
  rm -f "${SERVICE_FILE}"
  systemctl daemon-reload

  echo "[INFO] Removing installation directory..."
  rm -rf "${INSTALL_DIR}"

  echo "[OK] AutoDaggerTunnel has been successfully uninstalled."
  echo "You can safely exit this script now."
  exit 0
}

main_menu() {
  while true; do
    print_banner
    echo "1) Install / Update"
    echo "2) Reconfigure bot"
    echo "3) Start bot"
    echo "4) Stop bot"
    echo "5) Restart bot"
    echo "6) Service status"
    echo "7) Live logs"
    echo "8) Show current config"
    echo "9) Update bot now (pull + restart)"
    echo "10) Uninstall bot"
    echo "11) Exit"
    echo

    read -r -p "Select [1-11]: " choice
    case "${choice}" in
      1) install_or_update ;;
      2) reconfigure_only ;;
      3) start_service ; read -r -p "Press Enter to continue..." _ ;;
      4) stop_service ; read -r -p "Press Enter to continue..." _ ;;
      5) restart_service ; read -r -p "Press Enter to continue..." _ ;;
      6) status_service ;;
      7) logs_service ;;
      8) show_current_config ;;
      9) update_bot_only ;;
      10) uninstall_bot ;;
      11) exit 0 ;;
      *)
        echo "Invalid option."
        read -r -p "Press Enter to continue..." _
        ;;
    esac
  done
}

require_root
require_systemd
main_menu
