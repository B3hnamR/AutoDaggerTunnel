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

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_DIM=$'\033[2m'
  C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_CYAN=$'\033[36m'
else
  C_RESET=""
  C_BOLD=""
  C_DIM=""
  C_RED=""
  C_GREEN=""
  C_YELLOW=""
  C_CYAN=""
fi

hr() {
  printf '%s\n' "------------------------------------------------------------"
}

press_enter() {
  read -r -p "Press Enter to continue..." _
}

service_brief_state() {
  if ! systemctl list-unit-files "${APP_NAME}.service" >/dev/null 2>&1; then
    printf '%s' "not-installed"
    return
  fi

  if systemctl is-active --quiet "${APP_NAME}.service"; then
    printf '%s' "active"
    return
  fi

  if systemctl is-enabled --quiet "${APP_NAME}.service" 2>/dev/null; then
    printf '%s' "inactive"
  else
    printf '%s' "disabled"
  fi
}

count_csv_ids() {
  local raw="$1"
  local count=0
  local item=""
  local -a items=()
  IFS=',' read -r -a items <<<"${raw}"
  for item in "${items[@]}"; do
    item="$(echo "${item}" | xargs)"
    [[ -n "${item}" ]] && count=$((count + 1))
  done
  printf '%s' "${count}"
}

get_env_value() {
  local key="$1"
  local default="${2:-}"
  local line=""
  local val=""

  if [[ ! -f "${ENV_FILE}" ]]; then
    printf '%s' "${default}"
    return
  fi

  line="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n 1 || true)"
  if [[ -z "${line}" ]]; then
    printf '%s' "${default}"
    return
  fi

  val="${line#*=}"
  val="${val%\"}"
  val="${val#\"}"
  val="${val//\\\"/\"}"
  val="${val//\\\\/\\}"
  printf '%s' "${val}"
}

set_env_value() {
  local key="$1"
  local value="$2"
  local escaped=""
  escaped="$(env_escape "${value}")"

  mkdir -p "${INSTALL_DIR}"
  if [[ ! -f "${ENV_FILE}" ]]; then
    touch "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"
  fi

  if grep -qE "^${key}=" "${ENV_FILE}"; then
    sed -i "s|^${key}=.*|${key}=\"${escaped}\"|" "${ENV_FILE}"
  else
    printf '%s="%s"\n' "${key}" "${escaped}" >> "${ENV_FILE}"
  fi
}

normalize_ids_csv() {
  local raw="$1"
  local seen=","
  local -a cleaned=()
  local -a items=()
  local id=""

  IFS=',' read -r -a items <<<"${raw}"
  for id in "${items[@]}"; do
    id="$(echo "${id}" | xargs)"
    [[ -z "${id}" ]] && continue
    [[ ! "${id}" =~ ^-?[0-9]+$ ]] && continue
    if [[ "${seen}" == *",${id},"* ]]; then
      continue
    fi
    cleaned+=("${id}")
    seen="${seen}${id},"
  done

  local IFS=','
  printf '%s' "${cleaned[*]}"
}

print_banner() {
  local svc_state mode ids ids_count mode_view state_view
  clear
  svc_state="$(service_brief_state)"
  mode="$(get_env_value "ACCESS_MODE" "-")"
  ids="$(get_env_value "ALLOWED_USER_IDS" "")"
  ids_count="$(count_csv_ids "${ids}")"

  if [[ "${svc_state}" == "active" ]]; then
    state_view="${C_GREEN}${svc_state}${C_RESET}"
  elif [[ "${svc_state}" == "inactive" || "${svc_state}" == "disabled" ]]; then
    state_view="${C_YELLOW}${svc_state}${C_RESET}"
  else
    state_view="${C_RED}${svc_state}${C_RESET}"
  fi

  if [[ "${mode}" == "private" ]]; then
    mode_view="${C_YELLOW}private (${ids_count} ids)${C_RESET}"
  elif [[ "${mode}" == "public" ]]; then
    mode_view="${C_GREEN}public${C_RESET}"
  else
    mode_view="${C_DIM}-${C_RESET}"
  fi

  echo "${C_BOLD}${C_CYAN}AutoDaggerTunnel Manager${C_RESET}"
  hr
  echo "Install dir : ${INSTALL_DIR}"
  echo "Repo        : ${REPO_URL} (${REPO_BRANCH})"
  echo "Service     : ${state_view}"
  echo "Access mode : ${mode_view}"
  hr
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
    press_enter
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

manage_private_ids() {
  local access_mode current_ids ids_to_add ids_to_remove updated_ids
  local item remove_map ans
  local -a keep_list=()
  local -a items=()

  if [[ ! -f "${ENV_FILE}" ]]; then
    echo "[ERROR] Config file not found. Install or Reconfigure first."
    press_enter
    return
  fi

  access_mode="$(get_env_value "ACCESS_MODE" "public")"
  if [[ "${access_mode}" != "private" ]]; then
    echo "[WARN] Bot is currently in public mode."
    read -r -p "Switch to private mode now? [y/N]: " ans
    if [[ "${ans}" =~ ^[Yy]$ ]]; then
      set_env_value "ACCESS_MODE" "private"
      access_mode="private"
      echo "[OK] Access mode set to private."
    else
      press_enter
      return
    fi
  fi

  while true; do
    current_ids="$(normalize_ids_csv "$(get_env_value "ALLOWED_USER_IDS" "")")"
    echo
    hr
    echo "${C_BOLD}Private Mode - Allowed Telegram IDs${C_RESET}"
    hr
    if [[ -n "${current_ids}" ]]; then
      echo "Current IDs: ${current_ids}"
    else
      echo "Current IDs: (empty)"
    fi
    echo
    echo "1) Add ID(s)"
    echo "2) Remove ID(s)"
    echo "3) Restart bot service"
    echo "4) Back"
    echo

    read -r -p "Select [1-4]: " choice
    case "${choice}" in
      1)
        read -r -p "Enter ID(s) to add (comma separated): " ids_to_add
        if ! validate_ids "${ids_to_add}"; then
          echo "[ERROR] Invalid format. Example: 123456,7891011"
          press_enter
          continue
        fi
        updated_ids="$(normalize_ids_csv "${current_ids},${ids_to_add}")"
        set_env_value "ALLOWED_USER_IDS" "${updated_ids}"
        echo "[OK] IDs updated: ${updated_ids}"
        ;;
      2)
        if [[ -z "${current_ids}" ]]; then
          echo "[WARN] Allowed IDs list is already empty."
          press_enter
          continue
        fi

        read -r -p "Enter ID(s) to remove (comma separated): " ids_to_remove
        if ! validate_ids "${ids_to_remove}"; then
          echo "[ERROR] Invalid format. Example: 123456,7891011"
          press_enter
          continue
        fi

        ids_to_remove="$(normalize_ids_csv "${ids_to_remove}")"
        remove_map=",${ids_to_remove},"
        IFS=',' read -r -a items <<<"${current_ids}"
        for item in "${items[@]}"; do
          item="$(echo "${item}" | xargs)"
          [[ -z "${item}" ]] && continue
          if [[ "${remove_map}" == *",${item},"* ]]; then
            continue
          fi
          keep_list+=("${item}")
        done
        updated_ids="$(normalize_ids_csv "$(IFS=','; echo "${keep_list[*]}")")"
        set_env_value "ALLOWED_USER_IDS" "${updated_ids}"
        if [[ -n "${updated_ids}" ]]; then
          echo "[OK] IDs updated: ${updated_ids}"
        else
          echo "[WARN] Allowed IDs list is now empty."
        fi
        ;;
      3)
        systemctl restart "${APP_NAME}.service"
        echo "[OK] Service restarted."
        ;;
      4)
        return
        ;;
      *)
        echo "Invalid option."
        ;;
    esac
    press_enter
  done
}

update_bot_only() {
  if [[ ! -d "${APP_DIR}/.git" ]]; then
    echo "[ERROR] App is not installed yet. Run Install / Update first."
    press_enter
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
  press_enter
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
  press_enter
}

logs_service() {
  echo "Press Ctrl+C to exit logs."
  journalctl -u "${APP_NAME}.service" -f --no-pager
}

show_current_config() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    echo "[INFO] No config file found."
    press_enter
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

  press_enter
}

uninstall_bot() {
  echo
  echo "WARNING: This will completely remove the bot, your configurations, and the database."
  read -r -p "Are you absolutely sure you want to uninstall? [y/N]: " ans
  if [[ ! "${ans}" =~ ^[Yy]$ ]]; then
    echo "Uninstall aborted."
    press_enter
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
    echo "${C_BOLD}Main Menu${C_RESET}"
    hr
    echo " 1) Install / Update"
    echo " 2) Reconfigure bot"
    echo " 3) Manage private allowed IDs"
    echo " 4) Start bot"
    echo " 5) Stop bot"
    echo " 6) Restart bot"
    echo " 7) Service status"
    echo " 8) Live logs"
    echo " 9) Show current config"
    echo "10) Update bot now (pull + restart)"
    echo "11) Uninstall bot"
    echo "12) Exit"
    echo

    read -r -p "Select [1-12]: " choice
    case "${choice}" in
      1) install_or_update ;;
      2) reconfigure_only ;;
      3) manage_private_ids ;;
      4) start_service ; press_enter ;;
      5) stop_service ; press_enter ;;
      6) restart_service ; press_enter ;;
      7) status_service ;;
      8) logs_service ;;
      9) show_current_config ;;
      10) update_bot_only ;;
      11) uninstall_bot ;;
      12) exit 0 ;;
      *)
        echo "Invalid option."
        press_enter
        ;;
    esac
  done
}

require_root
require_systemd
main_menu
