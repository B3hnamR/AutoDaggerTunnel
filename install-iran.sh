#!/usr/bin/env bash
set -euo pipefail

APP_NAME="autodagger-iran"
BASE_DIR="/opt/${APP_NAME}"
DATA_DIR="${BASE_DIR}/data"
SERVERS_FILE="${DATA_DIR}/servers.tsv"
CONFIG_FILE="${DATA_DIR}/config.env"
MANAGER_COPY="${BASE_DIR}/autodagger-iran-manager.sh"

REPO_OWNER="${AUTO_DAGGER_REPO_OWNER:-B3hnamR}"
REPO_NAME="${AUTO_DAGGER_REPO_NAME:-AutoDaggerTunnel}"
REPO_BRANCH="${AUTO_DAGGER_BRANCH:-main}"

TUN_LOCAL_CIDR_DEFAULT="10.10.10.1/24"
TUN_REMOTE_CIDR_DEFAULT="10.10.10.2/24"

HAVE_SSH=0
HAVE_SCP=0
HAVE_SSHPASS=0
HAVE_SETSID=0

env_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf "%s" "${value}"
}

print_banner() {
  clear
  echo "===================================================="
  echo "         AutoDagger Iran Server Manager"
  echo "===================================================="
  echo "Data dir: ${DATA_DIR}"
  echo
}

press_enter() {
  read -r -p "Press Enter to continue..." _
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

ensure_dependencies() {
  local missing=()

  if command -v ssh >/dev/null 2>&1; then
    HAVE_SSH=1
  else
    missing+=("ssh")
  fi

  if command -v scp >/dev/null 2>&1; then
    HAVE_SCP=1
  else
    missing+=("scp")
  fi

  if command -v sshpass >/dev/null 2>&1; then
    HAVE_SSHPASS=1
  else
    HAVE_SSHPASS=0
  fi

  if command -v setsid >/dev/null 2>&1; then
    HAVE_SETSID=1
  else
    HAVE_SETSID=0
  fi

  if (( ${#missing[@]} > 0 )); then
    echo "[ERROR] Offline mode cannot continue on this server."
    echo "[ERROR] Missing required command(s): ${missing[*]}"
    echo "[ERROR] Please test on another Iran server or install these tools manually."
    exit 1
  fi

  if (( HAVE_SSHPASS == 1 )); then
    echo "[INFO] Auth capability: SSH key + password (sshpass detected)."
  elif (( HAVE_SETSID == 1 )); then
    echo "[INFO] Auth capability: SSH key + password (askpass fallback via setsid)."
  else
    echo "[WARN] Auth capability: SSH key only (password helper not available)."
    echo "[WARN] Password-based servers may fail on this host (missing sshpass and setsid)."
  fi
}

persist_manager_copy() {
  mkdir -p "${BASE_DIR}"
  if [[ -r "${0}" ]]; then
    cp -f "${0}" "${MANAGER_COPY}" 2>/dev/null || true
    chmod +x "${MANAGER_COPY}" 2>/dev/null || true
  fi
}

init_storage() {
  mkdir -p "${DATA_DIR}"
  touch "${SERVERS_FILE}"
  chmod 600 "${SERVERS_FILE}" 2>/dev/null || true

  if [[ ! -f "${CONFIG_FILE}" ]]; then
    cat > "${CONFIG_FILE}" <<EOF
TUNNEL_PORT="443"
PSK="123"
MAP_PROTOCOL="tcp"
MAP_PORT="8080"
TUN_BIP_DEST_IP=""
SSH_CONNECT_TIMEOUT="12"
SSH_COMMAND_TIMEOUT="120"
AUTO_OPTIMIZE="true"
EOF
    chmod 600 "${CONFIG_FILE}" 2>/dev/null || true
  fi
}

load_config() {
  init_storage
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"

  TUNNEL_PORT="${TUNNEL_PORT:-443}"
  PSK="${PSK:-123}"
  MAP_PROTOCOL="${MAP_PROTOCOL:-tcp}"
  MAP_PORT="${MAP_PORT:-8080}"
  TUN_BIP_DEST_IP="${TUN_BIP_DEST_IP:-}"
  SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-12}"
  SSH_COMMAND_TIMEOUT="${SSH_COMMAND_TIMEOUT:-120}"
  AUTO_OPTIMIZE="${AUTO_OPTIMIZE:-true}"
}

save_config() {
  cat > "${CONFIG_FILE}" <<EOF
TUNNEL_PORT="$(env_escape "${TUNNEL_PORT}")"
PSK="$(env_escape "${PSK}")"
MAP_PROTOCOL="$(env_escape "${MAP_PROTOCOL}")"
MAP_PORT="$(env_escape "${MAP_PORT}")"
TUN_BIP_DEST_IP="$(env_escape "${TUN_BIP_DEST_IP}")"
SSH_CONNECT_TIMEOUT="$(env_escape "${SSH_CONNECT_TIMEOUT}")"
SSH_COMMAND_TIMEOUT="$(env_escape "${SSH_COMMAND_TIMEOUT}")"
AUTO_OPTIMIZE="$(env_escape "${AUTO_OPTIMIZE}")"
EOF
  chmod 600 "${CONFIG_FILE}" 2>/dev/null || true
}

validate_port() {
  local value="$1"
  [[ "${value}" =~ ^[0-9]+$ ]] && (( value >= 1 && value <= 65535 ))
}

validate_server_name() {
  local value="$1"
  [[ "${value}" =~ ^[A-Za-z0-9_-]{1,32}$ ]]
}

validate_protocol() {
  local value
  value="$(echo "$1" | tr '[:upper:]' '[:lower:]')"
  [[ "${value}" == "tcp" || "${value}" == "udp" || "${value}" == "both" ]]
}

normalize_protocol() {
  echo "$1" | tr '[:upper:]' '[:lower:]'
}

validate_ipv4() {
  local ip="$1"
  local IFS='.'
  local -a octets
  read -r -a octets <<< "${ip}"
  [[ ${#octets[@]} -eq 4 ]] || return 1
  for octet in "${octets[@]}"; do
    [[ "${octet}" =~ ^[0-9]+$ ]] || return 1
    (( octet >= 0 && octet <= 255 )) || return 1
  done
  return 0
}

parse_host_port() {
  local input="$1"
  local host
  local port

  input="$(echo "${input}" | xargs)"
  if [[ "${input}" == *:* ]]; then
    host="${input%%:*}"
    port="${input##*:}"
  else
    host="${input}"
    port="22"
  fi

  host="$(echo "${host}" | xargs)"
  port="$(echo "${port}" | xargs)"

  if [[ -z "${host}" ]] || ! validate_port "${port}"; then
    return 1
  fi

  printf "%s|%s\n" "${host}" "${port}"
}

next_server_id() {
  awk -F'\t' 'BEGIN{max=0} NF>=1 && $1+0>max{max=$1+0} END{print max+1}' "${SERVERS_FILE}"
}

server_line_by_id() {
  local server_id="$1"
  awk -F'\t' -v id="${server_id}" '$1 == id {print; exit}' "${SERVERS_FILE}"
}

list_servers_table() {
  if [[ ! -s "${SERVERS_FILE}" ]]; then
    echo "No servers saved."
    return
  fi

  echo "Saved servers:"
  while IFS=$'\t' read -r server_id name host port user password; do
    [[ -n "${server_id:-}" ]] || continue
    local auth_mode="key"
    [[ -n "${password:-}" ]] && auth_mode="password"
    echo "- ID ${server_id} | ${name} | ${host}:${port} | user=${user} | auth=${auth_mode}"
  done < "${SERVERS_FILE}"
}

update_server_record() {
  local server_id="$1"
  local name="$2"
  local host="$3"
  local port="$4"
  local user="$5"
  local password="$6"
  local tmp
  tmp="$(mktemp)"

  awk -F'\t' -v OFS='\t' \
    -v id="${server_id}" \
    -v name="${name}" \
    -v host="${host}" \
    -v port="${port}" \
    -v user="${user}" \
    -v password="${password}" \
    '{ if ($1 == id) { $2=name; $3=host; $4=port; $5=user; $6=password } print }' \
    "${SERVERS_FILE}" > "${tmp}"

  mv "${tmp}" "${SERVERS_FILE}"
  chmod 600 "${SERVERS_FILE}" 2>/dev/null || true
}

delete_server_record() {
  local server_id="$1"
  local tmp
  tmp="$(mktemp)"
  awk -F'\t' -v id="${server_id}" '$1 != id {print}' "${SERVERS_FILE}" > "${tmp}"
  mv "${tmp}" "${SERVERS_FILE}"
  chmod 600 "${SERVERS_FILE}" 2>/dev/null || true
}

with_timeout() {
  local timeout_seconds="$1"
  shift

  if command -v timeout >/dev/null 2>&1; then
    timeout --foreground "${timeout_seconds}s" "$@"
  else
    "$@"
  fi
}

ssh_options() {
  echo "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=${SSH_CONNECT_TIMEOUT} -o ServerAliveInterval=10 -o ServerAliveCountMax=3"
}

auth_capability_check() {
  local password="$1"
  if [[ -z "${password}" ]]; then
    return 0
  fi
  if (( HAVE_SSHPASS == 1 )); then
    return 0
  fi
  if (( HAVE_SETSID == 1 )); then
    return 0
  fi
  if [[ -n "${password}" ]]; then
    echo "AUTH_ERROR:password_auth_not_supported_on_this_host"
    return 1
  fi
  return 0
}

run_with_askpass() {
  local password="$1"
  shift

  local askpass_script
  askpass_script="$(mktemp)"
  cat > "${askpass_script}" <<'EOF'
#!/usr/bin/env sh
printf '%s\n' "${SSH_ASKPASS_PASSWORD:-}"
EOF
  chmod 700 "${askpass_script}"

  env \
    SSH_ASKPASS="${askpass_script}" \
    SSH_ASKPASS_REQUIRE=force \
    SSH_ASKPASS_PASSWORD="${password}" \
    DISPLAY="${DISPLAY:-:0}" \
    setsid -w "$@"
  local rc=$?
  rm -f "${askpass_script}"
  return ${rc}
}

run_ssh_command() {
  local host="$1"
  local port="$2"
  local user="$3"
  local password="$4"
  local command="$5"
  local -a opts
  IFS=' ' read -r -a opts <<< "$(ssh_options)"
  if [[ -n "${password}" ]]; then
    if ! auth_capability_check "${password}" >/dev/null 2>&1; then
      return 97
    fi
    if (( HAVE_SSHPASS == 1 )); then
      with_timeout "${SSH_COMMAND_TIMEOUT}" \
        sshpass -p "${password}" ssh "${opts[@]}" -p "${port}" "${user}@${host}" "${command}"
    else
      with_timeout "${SSH_COMMAND_TIMEOUT}" \
        run_with_askpass "${password}" ssh "${opts[@]}" -p "${port}" "${user}@${host}" "${command}" < /dev/null
    fi
  else
    with_timeout "${SSH_COMMAND_TIMEOUT}" \
      ssh "${opts[@]}" -o BatchMode=yes -p "${port}" "${user}@${host}" "${command}"
  fi
}

run_remote_script() {
  local host="$1"
  local port="$2"
  local user="$3"
  local password="$4"
  local -a opts
  IFS=' ' read -r -a opts <<< "$(ssh_options)"
  if [[ -n "${password}" ]]; then
    if ! auth_capability_check "${password}" >/dev/null 2>&1; then
      return 97
    fi
    if (( HAVE_SSHPASS == 1 )); then
      with_timeout "${SSH_COMMAND_TIMEOUT}" \
        sshpass -p "${password}" ssh "${opts[@]}" -p "${port}" "${user}@${host}" "bash -s"
    else
      with_timeout "${SSH_COMMAND_TIMEOUT}" \
        run_with_askpass "${password}" ssh "${opts[@]}" -p "${port}" "${user}@${host}" "bash -s"
    fi
  else
    with_timeout "${SSH_COMMAND_TIMEOUT}" \
      ssh "${opts[@]}" -o BatchMode=yes -p "${port}" "${user}@${host}" "bash -s"
  fi
}

scp_to_remote() {
  local local_file="$1"
  local remote_path="$2"
  local host="$3"
  local port="$4"
  local user="$5"
  local password="$6"
  local -a opts
  IFS=' ' read -r -a opts <<< "$(ssh_options)"
  if [[ -n "${password}" ]]; then
    if ! auth_capability_check "${password}" >/dev/null 2>&1; then
      return 97
    fi
    if (( HAVE_SSHPASS == 1 )); then
      with_timeout "${SSH_COMMAND_TIMEOUT}" \
        sshpass -p "${password}" scp "${opts[@]}" -P "${port}" "${local_file}" "${user}@${host}:${remote_path}"
    else
      with_timeout "${SSH_COMMAND_TIMEOUT}" \
        run_with_askpass "${password}" scp "${opts[@]}" -P "${port}" "${local_file}" "${user}@${host}:${remote_path}" < /dev/null
    fi
  else
    with_timeout "${SSH_COMMAND_TIMEOUT}" \
      scp "${opts[@]}" -o BatchMode=yes -P "${port}" "${local_file}" "${user}@${host}:${remote_path}"
  fi
}

ssh_connectivity_check() {
  local host="$1"
  local port="$2"
  local user="$3"
  local password="$4"

  run_ssh_command "${host}" "${port}" "${user}" "${password}" "id -u >/dev/null 2>&1"
}

remote_core_binary_check() {
  local host="$1"
  local port="$2"
  local user="$3"
  local password="$4"

  run_ssh_command "${host}" "${port}" "${user}" "${password}" "[ -x /usr/local/bin/DaggerConnect ]"
}

show_local_core_notice_once() {
  print_banner
  if [[ -x /usr/local/bin/DaggerConnect ]]; then
    echo "[OK] Core binary detected: /usr/local/bin/DaggerConnect"
  else
    echo "[WARN] Core binary not found on this server."
    echo "Please upload the core file manually to /usr/local/bin/DaggerConnect"
    echo "and run: chmod +x /usr/local/bin/DaggerConnect"
  fi
  echo
  press_enter
}

configure_defaults() {
  load_config
  print_banner
  echo "Current configuration:"
  echo "- Tunnel port     : ${TUNNEL_PORT}"
  echo "- PSK             : ${PSK}"
  echo "- Protocol        : ${MAP_PROTOCOL}"
  echo "- Port            : ${MAP_PORT}"
  echo "- TUN+BIP dest_ip : ${TUN_BIP_DEST_IP:-<not set>}"
  echo "- SSH connect sec : ${SSH_CONNECT_TIMEOUT}"
  echo "- SSH command sec : ${SSH_COMMAND_TIMEOUT}"
  echo "- Auto optimize   : ${AUTO_OPTIMIZE}"
  echo "- Core binary req : /usr/local/bin/DaggerConnect on each target server"
  echo

  local input

  read -r -p "Tunnel port [${TUNNEL_PORT}]: " input
  input="${input:-${TUNNEL_PORT}}"
  if ! validate_port "${input}"; then
    echo "[ERROR] Invalid tunnel port."
    press_enter
    return
  fi
  TUNNEL_PORT="${input}"

  read -r -p "PSK [current kept if empty]: " input
  if [[ -n "${input}" ]]; then
    PSK="${input}"
  fi
  if [[ -z "${PSK}" ]]; then
    echo "[ERROR] PSK cannot be empty."
    press_enter
    return
  fi

  read -r -p "Mapping protocol (tcp/udp/both) [${MAP_PROTOCOL}]: " input
  input="${input:-${MAP_PROTOCOL}}"
  input="$(normalize_protocol "${input}")"
  if ! validate_protocol "${input}"; then
    echo "[ERROR] Invalid protocol. Use tcp, udp, or both."
    press_enter
    return
  fi
  MAP_PROTOCOL="${input}"

  read -r -p "Mapping port [${MAP_PORT}]: " input
  input="${input:-${MAP_PORT}}"
  if ! validate_port "${input}"; then
    echo "[ERROR] Invalid mapping port."
    press_enter
    return
  fi
  MAP_PORT="${input}"

  read -r -p "TUN+BIP dest_ip (client IP, required for bip on server) [${TUN_BIP_DEST_IP:-unset}]: " input
  if [[ -n "${input}" ]]; then
    if ! validate_ipv4 "${input}"; then
      echo "[ERROR] Invalid IPv4 format for TUN+BIP dest_ip."
      press_enter
      return
    fi
    TUN_BIP_DEST_IP="${input}"
  fi

  read -r -p "SSH connect timeout seconds [${SSH_CONNECT_TIMEOUT}]: " input
  if [[ -n "${input}" ]]; then
    if [[ ! "${input}" =~ ^[0-9]+$ ]] || (( input < 3 || input > 120 )); then
      echo "[ERROR] SSH connect timeout must be between 3 and 120."
      press_enter
      return
    fi
    SSH_CONNECT_TIMEOUT="${input}"
  fi

  read -r -p "SSH command timeout seconds [${SSH_COMMAND_TIMEOUT}]: " input
  if [[ -n "${input}" ]]; then
    if [[ ! "${input}" =~ ^[0-9]+$ ]] || (( input < 10 || input > 900 )); then
      echo "[ERROR] SSH command timeout must be between 10 and 900."
      press_enter
      return
    fi
    SSH_COMMAND_TIMEOUT="${input}"
  fi

  read -r -p "Auto optimize remote system after apply? [Y/n]: " input
  if [[ "${input}" =~ ^[Nn]$ ]]; then
    AUTO_OPTIMIZE="false"
  elif [[ "${input}" =~ ^[Yy]$ || -z "${input}" ]]; then
    AUTO_OPTIMIZE="true"
  else
    echo "[ERROR] Invalid choice for optimize option."
    press_enter
    return
  fi

  save_config
  echo "[OK] Configuration saved."
  press_enter
}

add_server() {
  load_config
  print_banner
  echo "Add server"
  echo

  local name host_input parsed host port user password next_id

  while true; do
    read -r -p "Server name (letters/numbers/-/_): " name
    if validate_server_name "${name}"; then
      break
    fi
    echo "[ERROR] Invalid name. Example: ir-node-1"
  done

  while true; do
    read -r -p "Host or host:port (example: 1.2.3.4 or 1.2.3.4:22): " host_input
    if parsed="$(parse_host_port "${host_input}")"; then
      host="${parsed%%|*}"
      port="${parsed##*|}"
      break
    fi
    echo "[ERROR] Invalid host/port input."
  done

  read -r -p "SSH username [root]: " user
  user="${user:-root}"
  if [[ "$(echo "${user}" | tr '[:upper:]' '[:lower:]')" == "root" ]]; then
    user="root"
  fi

  read -r -s -p "SSH password (optional, leave empty for key auth): " password
  echo
  if [[ -n "${password}" && "${HAVE_SSHPASS}" -ne 1 && "${HAVE_SETSID}" -ne 1 ]]; then
    echo "[WARN] Password auth may fail on this host (missing sshpass and setsid)."
  fi

  next_id="$(next_server_id)"
  printf "%s\t%s\t%s\t%s\t%s\t%s\n" "${next_id}" "${name}" "${host}" "${port}" "${user}" "${password}" >> "${SERVERS_FILE}"
  chmod 600 "${SERVERS_FILE}" 2>/dev/null || true
  echo "[OK] Server saved. ID=${next_id}, target=${host}:${port}"
  echo "[INFO] Running SSH connectivity check..."

  if ssh_connectivity_check "${host}" "${port}" "${user}" "${password}" >/dev/null 2>&1; then
    echo "[OK] SSH check: SUCCESS."
  else
    echo "[WARN] SSH check: FAILED. Server is still saved."
  fi
  press_enter
}

edit_server() {
  print_banner
  list_servers_table
  if [[ ! -s "${SERVERS_FILE}" ]]; then
    press_enter
    return
  fi
  echo

  local server_id line
  read -r -p "Server ID to edit: " server_id
  if [[ ! "${server_id}" =~ ^[0-9]+$ ]]; then
    echo "[ERROR] Invalid ID."
    press_enter
    return
  fi

  line="$(server_line_by_id "${server_id}")"
  if [[ -z "${line}" ]]; then
    echo "[ERROR] Server ID not found."
    press_enter
    return
  fi

  local old_name old_host old_port old_user old_password
  IFS=$'\t' read -r _ old_name old_host old_port old_user old_password <<< "${line}"

  local name host_input parsed host port user password

  read -r -p "Name [${old_name}]: " name
  name="${name:-${old_name}}"
  if ! validate_server_name "${name}"; then
    echo "[ERROR] Invalid name."
    press_enter
    return
  fi

  read -r -p "Host or host:port [${old_host}:${old_port}]: " host_input
  host_input="${host_input:-${old_host}:${old_port}}"
  if ! parsed="$(parse_host_port "${host_input}")"; then
    echo "[ERROR] Invalid host/port input."
    press_enter
    return
  fi
  host="${parsed%%|*}"
  port="${parsed##*|}"

  read -r -p "SSH username [${old_user}]: " user
  user="${user:-${old_user}}"
  if [[ "$(echo "${user}" | tr '[:upper:]' '[:lower:]')" == "root" ]]; then
    user="root"
  fi

  read -r -s -p "SSH password [keep current if empty, '-' to clear and use key auth]: " password
  echo
  if [[ "${password}" == "-" ]]; then
    password=""
  else
    password="${password:-${old_password}}"
  fi
  if [[ -n "${password}" && "${HAVE_SSHPASS}" -ne 1 && "${HAVE_SETSID}" -ne 1 ]]; then
    echo "[WARN] Password auth may fail on this host (missing sshpass and setsid)."
  fi

  update_server_record "${server_id}" "${name}" "${host}" "${port}" "${user}" "${password}"
  echo "[OK] Server updated."
  press_enter
}

delete_server() {
  print_banner
  list_servers_table
  if [[ ! -s "${SERVERS_FILE}" ]]; then
    press_enter
    return
  fi
  echo

  local server_id line confirm
  read -r -p "Server ID to delete: " server_id
  if [[ ! "${server_id}" =~ ^[0-9]+$ ]]; then
    echo "[ERROR] Invalid ID."
    press_enter
    return
  fi

  line="$(server_line_by_id "${server_id}")"
  if [[ -z "${line}" ]]; then
    echo "[ERROR] Server ID not found."
    press_enter
    return
  fi

  read -r -p "Delete server ID=${server_id}? [y/N]: " confirm
  if [[ ! "${confirm}" =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    press_enter
    return
  fi

  delete_server_record "${server_id}"
  echo "[OK] Server deleted."
  press_enter
}

write_common_tail() {
  local file="$1"
  cat >> "${file}" <<'EOF'

smux:
  keepalive: 8
  max_recv: 8388608
  max_stream: 8388608
  frame_size: 32768
  version: 2

kcp:
  nodelay: 1
  interval: 10
  resend: 2
  nc: 1
  sndwnd: 1024
  rcvwnd: 1024
  mtu: 1400

advanced:
  tcp_nodelay: true
  tcp_keepalive: 15
  tcp_read_buffer: 4194304
  tcp_write_buffer: 4194304
  websocket_read_buffer: 65536
  websocket_write_buffer: 65536
  websocket_compression: false
  cleanup_interval: 3
  session_timeout: 60
  connection_timeout: 30
  stream_timeout: 120
  max_connections: 2000
  max_udp_flows: 1000
  udp_flow_timeout: 300
  udp_buffer_size: 4194304

obfuscation:
  enabled: false
  min_padding: 16
  max_padding: 512
  min_delay_ms: 0
  max_delay_ms: 0
  burst_chance: 0.15

http_mimic:
  fake_domain: "www.google.com"
  fake_path: "/search"
  user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
  chunked_encoding: false
  session_cookie: true
  custom_headers:
    - "Accept-Language: en-US,en;q=0.9"
    - "Accept-Encoding: gzip, deflate, br"
EOF
}

append_maps_block() {
  local file="$1"
  local target_ip="$2"

  if [[ "${MAP_PROTOCOL}" == "both" ]]; then
    cat >> "${file}" <<EOF
    maps:
      - type: tcp
        bind: "0.0.0.0:${MAP_PORT}"
        target: "${target_ip}:${MAP_PORT}"
      - type: udp
        bind: "0.0.0.0:${MAP_PORT}"
        target: "${target_ip}:${MAP_PORT}"
EOF
  else
    cat >> "${file}" <<EOF
    maps:
      - type: ${MAP_PROTOCOL}
        bind: "0.0.0.0:${MAP_PORT}"
        target: "${target_ip}:${MAP_PORT}"
EOF
  fi
}

generate_quantummux_server_yaml() {
  local file="$1"
  local iface="$2"
  local local_ip="$3"

  cat > "${file}" <<EOF
mode: "server"
psk: "${PSK}"
profile: "latency"
verbose: true
heartbeat: 2

listeners:
  - addr: "0.0.0.0:${TUNNEL_PORT}"
    transport: "quantummux"
EOF
  append_maps_block "${file}" "127.0.0.1"

  {
    echo
    echo "quantummux:"
    [[ -n "${iface}" ]] && echo "  interface: \"${iface}\""
    [[ -n "${local_ip}" ]] && echo "  local_ip: \"${local_ip}\""
    cat <<'EOF'
  mtu: 1280
  snd_wnd: 1024
  rcv_wnd: 1024
  data_shard: 10
  parity_shard: 3
  ttl_base: 64
  ttl_jitter: 8
  tcp_window: 65535
  ack_step_min: 64
  ack_step_max: 512
  tcp_flags: "PA"
  idle_timeout: 60
  icmpv6_mode: true
EOF
  } >> "${file}"

  write_common_tail "${file}"
}

generate_tun_bip_server_yaml() {
  local file="$1"
  local tun_remote_ip
  tun_remote_ip="${TUN_REMOTE_CIDR_DEFAULT%%/*}"

  cat > "${file}" <<EOF
mode: "server"
psk: "${PSK}"
profile: "latency"
verbose: true
heartbeat: 2

listeners:
  - addr: "0.0.0.0:${TUNNEL_PORT}"
    transport: "tun"
EOF
  append_maps_block "${file}" "${tun_remote_ip}"

  cat >> "${file}" <<EOF

tun_transport:
  device_name: "dagger0"
  local_cidr: "${TUN_LOCAL_CIDR_DEFAULT}"
  remote_cidr: "${TUN_REMOTE_CIDR_DEFAULT}"
  mtu: 1320
  health_port: ${TUNNEL_PORT}
  profile: "bip"
  listen_ip: "0.0.0.0"
  dest_ip: "${TUN_BIP_DEST_IP}"
  auto_tuning: true
  tuning_profile: "balanced"
  workers: 0
  batch_size: 2048
EOF

  write_common_tail "${file}"
}

generate_server_service_file() {
  local file="$1"
  cat > "${file}" <<'EOF'
[Unit]
Description=DaggerConnect Server
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/etc/DaggerConnect
ExecStart=/usr/local/bin/DaggerConnect -c /etc/DaggerConnect/server.yaml
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
}

detect_remote_quantum_hints() {
  local host="$1"
  local port="$2"
  local user="$3"
  local password="$4"

  local output
  output="$(
    run_remote_script "${host}" "${port}" "${user}" "${password}" <<'EOF'
IFACE="$(ip route show default 2>/dev/null | awk 'NR==1 {print $5}')"
LOCAL_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="src"){print $(i+1); exit}}')"
printf "%s|%s\n" "${IFACE}" "${LOCAL_IP}"
EOF
  )" || true

  output="$(echo "${output}" | tail -n 1 | tr -d '\r')"
  if [[ "${output}" != *"|"* ]]; then
    echo "|"
  else
    echo "${output}"
  fi
}

extract_remote_error() {
  local output="$1"
  local reason
  reason="$(echo "${output}" | grep -Eo 'REMOTE_ERROR:[^[:space:]]+' | tail -n 1 || true)"
  if [[ -n "${reason}" ]]; then
    echo "${reason#REMOTE_ERROR:}"
    return
  fi

  reason="$(echo "${output}" | tail -n 1 | tr -d '\r')"
  if [[ -z "${reason}" ]]; then
    echo "unknown_error"
  else
    echo "${reason}"
  fi
}

run_remote_prepare_quantummux() {
  local host="$1"
  local port="$2"
  local user="$3"
  local password="$4"
  local output

  output="$(
    run_remote_script "${host}" "${port}" "${user}" "${password}" <<EOF
set -e

if [ "\$(id -u)" -ne 0 ]; then
  echo "REMOTE_ERROR:not_root"
  exit 21
fi

if [ ! -x /usr/local/bin/DaggerConnect ]; then
  echo "REMOTE_ERROR:core_binary_missing"
  echo "Please upload the core file manually to /usr/local/bin/DaggerConnect and run chmod +x /usr/local/bin/DaggerConnect"
  exit 22
fi
mkdir -p /etc/DaggerConnect

mv /tmp/autodagger-server.yaml /etc/DaggerConnect/server.yaml
mv /tmp/DaggerConnect-server.service /etc/systemd/system/DaggerConnect-server.service
chmod 644 /etc/systemd/system/DaggerConnect-server.service

iptables -t raw    -A PREROUTING -p tcp --dport "${TUNNEL_PORT}" -j NOTRACK 2>/dev/null || true
iptables -t raw    -A OUTPUT     -p tcp --sport "${TUNNEL_PORT}" -j NOTRACK 2>/dev/null || true
iptables -t mangle -A OUTPUT     -p tcp --sport "${TUNNEL_PORT}" --tcp-flags RST RST -j DROP 2>/dev/null || true

if command -v iptables-save >/dev/null 2>&1; then
  mkdir -p /etc/iptables
  iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
fi

mkdir -p /etc/network/if-pre-up.d
cat > /etc/network/if-pre-up.d/quantummux-iptables <<'EORULES'
#!/bin/bash
iptables -t raw    -A PREROUTING -p tcp --dport ${TUNNEL_PORT} -j NOTRACK 2>/dev/null || true
iptables -t raw    -A OUTPUT     -p tcp --sport ${TUNNEL_PORT} -j NOTRACK 2>/dev/null || true
iptables -t mangle -A OUTPUT     -p tcp --sport ${TUNNEL_PORT} --tcp-flags RST RST -j DROP 2>/dev/null || true
EORULES
chmod +x /etc/network/if-pre-up.d/quantummux-iptables 2>/dev/null || true

if [ "${AUTO_OPTIMIZE}" = "true" ]; then
  INTERFACE=\$(ip link show | grep "state UP" | head -1 | awk '{print \$2}' | cut -d: -f1)
  [ -z "\${INTERFACE}" ] && INTERFACE="eth0"

  sysctl -w net.core.rmem_max=8388608               >/dev/null 2>&1 || true
  sysctl -w net.core.wmem_max=8388608               >/dev/null 2>&1 || true
  sysctl -w net.core.rmem_default=131072            >/dev/null 2>&1 || true
  sysctl -w net.core.wmem_default=131072            >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_rmem="4096 65536 8388608" >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_wmem="4096 65536 8388608" >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_window_scaling=1           >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_timestamps=1               >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_sack=1                     >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_retries2=6                 >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_syn_retries=2              >/dev/null 2>&1 || true
  sysctl -w net.core.netdev_max_backlog=1000        >/dev/null 2>&1 || true
  sysctl -w net.core.somaxconn=512                  >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_fastopen=3                 >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_low_latency=1              >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_slow_start_after_idle=0    >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_no_metrics_save=1          >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_autocorking=0              >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_mtu_probing=1              >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_keepalive_time=120         >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_keepalive_intvl=10         >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_keepalive_probes=3         >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_fin_timeout=15             >/dev/null 2>&1 || true
  sysctl -w net.ipv4.ip_forward=1                   >/dev/null 2>&1 || true

  if modprobe tcp_bbr 2>/dev/null; then
    sysctl -w net.ipv4.tcp_congestion_control=bbr >/dev/null 2>&1 || true
    sysctl -w net.core.default_qdisc=fq_codel     >/dev/null 2>&1 || true
  fi

  tc qdisc del dev "\${INTERFACE}" root 2>/dev/null || true
  tc qdisc add dev "\${INTERFACE}" root fq_codel limit 500 target 3ms interval 50ms quantum 300 ecn 2>/dev/null || true

  cat > /etc/sysctl.d/99-daggerconnect.conf <<'EOSYS'
net.core.rmem_max=8388608
net.core.wmem_max=8388608
net.core.rmem_default=131072
net.core.wmem_default=131072
net.ipv4.tcp_rmem=4096 65536 8388608
net.ipv4.tcp_wmem=4096 65536 8388608
net.ipv4.tcp_window_scaling=1
net.ipv4.tcp_timestamps=1
net.ipv4.tcp_sack=1
net.ipv4.tcp_retries2=6
net.ipv4.tcp_syn_retries=2
net.core.netdev_max_backlog=1000
net.core.somaxconn=512
net.ipv4.tcp_fastopen=3
net.ipv4.tcp_low_latency=1
net.ipv4.tcp_slow_start_after_idle=0
net.ipv4.tcp_no_metrics_save=1
net.ipv4.tcp_autocorking=0
net.ipv4.tcp_mtu_probing=1
net.ipv4.tcp_keepalive_time=120
net.ipv4.tcp_keepalive_intvl=10
net.ipv4.tcp_keepalive_probes=3
net.ipv4.tcp_fin_timeout=15
net.ipv4.tcp_congestion_control=bbr
net.core.default_qdisc=fq_codel
net.ipv4.ip_forward=1
EOSYS
fi

systemctl daemon-reload
systemctl enable DaggerConnect-server.service >/dev/null 2>&1 || true
systemctl restart DaggerConnect-server.service

if ! systemctl is-active --quiet DaggerConnect-server.service; then
  echo "REMOTE_ERROR:service_start_failed"
  journalctl -u DaggerConnect-server.service -n 20 --no-pager || true
  exit 24
fi
EOF
  )" || {
    local reason
    reason="$(extract_remote_error "${output}")"
    echo "${reason}"
    return 1
  }

  return 0
}

run_remote_prepare_tun_bip() {
  local host="$1"
  local port="$2"
  local user="$3"
  local password="$4"
  local output

  output="$(
    run_remote_script "${host}" "${port}" "${user}" "${password}" <<EOF
set -e

if [ "\$(id -u)" -ne 0 ]; then
  echo "REMOTE_ERROR:not_root"
  exit 31
fi

if [ ! -x /usr/local/bin/DaggerConnect ]; then
  echo "REMOTE_ERROR:core_binary_missing"
  echo "Please upload the core file manually to /usr/local/bin/DaggerConnect and run chmod +x /usr/local/bin/DaggerConnect"
  exit 32
fi
mkdir -p /etc/DaggerConnect

mv /tmp/autodagger-server.yaml /etc/DaggerConnect/server.yaml
mv /tmp/DaggerConnect-server.service /etc/systemd/system/DaggerConnect-server.service
chmod 644 /etc/systemd/system/DaggerConnect-server.service

modprobe tun 2>/dev/null || true

if [ "${AUTO_OPTIMIZE}" = "true" ]; then
  INTERFACE=\$(ip link show | grep "state UP" | head -1 | awk '{print \$2}' | cut -d: -f1)
  [ -z "\${INTERFACE}" ] && INTERFACE="eth0"

  sysctl -w net.core.rmem_max=8388608               >/dev/null 2>&1 || true
  sysctl -w net.core.wmem_max=8388608               >/dev/null 2>&1 || true
  sysctl -w net.core.rmem_default=131072            >/dev/null 2>&1 || true
  sysctl -w net.core.wmem_default=131072            >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_rmem="4096 65536 8388608" >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_wmem="4096 65536 8388608" >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_window_scaling=1           >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_timestamps=1               >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_sack=1                     >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_retries2=6                 >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_syn_retries=2              >/dev/null 2>&1 || true
  sysctl -w net.core.netdev_max_backlog=1000        >/dev/null 2>&1 || true
  sysctl -w net.core.somaxconn=512                  >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_fastopen=3                 >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_low_latency=1              >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_slow_start_after_idle=0    >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_no_metrics_save=1          >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_autocorking=0              >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_mtu_probing=1              >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_keepalive_time=120         >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_keepalive_intvl=10         >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_keepalive_probes=3         >/dev/null 2>&1 || true
  sysctl -w net.ipv4.tcp_fin_timeout=15             >/dev/null 2>&1 || true
  sysctl -w net.ipv4.ip_forward=1                   >/dev/null 2>&1 || true

  if modprobe tcp_bbr 2>/dev/null; then
    sysctl -w net.ipv4.tcp_congestion_control=bbr >/dev/null 2>&1 || true
    sysctl -w net.core.default_qdisc=fq_codel     >/dev/null 2>&1 || true
  fi

  tc qdisc del dev "\${INTERFACE}" root 2>/dev/null || true
  tc qdisc add dev "\${INTERFACE}" root fq_codel limit 500 target 3ms interval 50ms quantum 300 ecn 2>/dev/null || true

  cat > /etc/sysctl.d/99-daggerconnect.conf <<'EOSYS'
net.core.rmem_max=8388608
net.core.wmem_max=8388608
net.core.rmem_default=131072
net.core.wmem_default=131072
net.ipv4.tcp_rmem=4096 65536 8388608
net.ipv4.tcp_wmem=4096 65536 8388608
net.ipv4.tcp_window_scaling=1
net.ipv4.tcp_timestamps=1
net.ipv4.tcp_sack=1
net.ipv4.tcp_retries2=6
net.ipv4.tcp_syn_retries=2
net.core.netdev_max_backlog=1000
net.core.somaxconn=512
net.ipv4.tcp_fastopen=3
net.ipv4.tcp_low_latency=1
net.ipv4.tcp_slow_start_after_idle=0
net.ipv4.tcp_no_metrics_save=1
net.ipv4.tcp_autocorking=0
net.ipv4.tcp_mtu_probing=1
net.ipv4.tcp_keepalive_time=120
net.ipv4.tcp_keepalive_intvl=10
net.ipv4.tcp_keepalive_probes=3
net.ipv4.tcp_fin_timeout=15
net.ipv4.tcp_congestion_control=bbr
net.core.default_qdisc=fq_codel
net.ipv4.ip_forward=1
EOSYS
fi

systemctl daemon-reload
systemctl enable DaggerConnect-server.service >/dev/null 2>&1 || true
systemctl restart DaggerConnect-server.service

if ! systemctl is-active --quiet DaggerConnect-server.service; then
  echo "REMOTE_ERROR:service_start_failed"
  journalctl -u DaggerConnect-server.service -n 20 --no-pager || true
  exit 34
fi
EOF
  )" || {
    local reason
    reason="$(extract_remote_error "${output}")"
    echo "${reason}"
    return 1
  }

  return 0
}

apply_quantummux_to_one() {
  local name="$1"
  local host="$2"
  local port="$3"
  local user="$4"
  local password="$5"

  if ! auth_capability_check "${password}" >/dev/null 2>&1; then
    echo "AUTH_ERROR:password_auth_not_supported_on_this_host"
    return 1
  fi

  if ! ssh_connectivity_check "${host}" "${port}" "${user}" "${password}" >/dev/null 2>&1; then
    echo "SSH_ERROR:ssh_connect_failed"
    return 1
  fi

  local hints iface local_ip
  hints="$(detect_remote_quantum_hints "${host}" "${port}" "${user}" "${password}")"
  iface="${hints%%|*}"
  local_ip="${hints##*|}"
  if ! remote_core_binary_check "${host}" "${port}" "${user}" "${password}" >/dev/null 2>&1; then
    echo "CORE_BINARY_MISSING:Please upload the core file manually to /usr/local/bin/DaggerConnect and run chmod +x /usr/local/bin/DaggerConnect"
    return 1
  fi

  local tmp_yaml tmp_service
  tmp_yaml="$(mktemp)"
  tmp_service="$(mktemp)"
  generate_quantummux_server_yaml "${tmp_yaml}" "${iface}" "${local_ip}"
  generate_server_service_file "${tmp_service}"

  if ! scp_to_remote "${tmp_yaml}" "/tmp/autodagger-server.yaml" "${host}" "${port}" "${user}" "${password}" >/dev/null 2>&1; then
    rm -f "${tmp_yaml}" "${tmp_service}"
    echo "SCP_ERROR:config_upload_failed"
    return 1
  fi

  if ! scp_to_remote "${tmp_service}" "/tmp/DaggerConnect-server.service" "${host}" "${port}" "${user}" "${password}" >/dev/null 2>&1; then
    rm -f "${tmp_yaml}" "${tmp_service}"
    echo "SCP_ERROR:service_upload_failed"
    return 1
  fi

  rm -f "${tmp_yaml}" "${tmp_service}"

  local remote_result
  if ! remote_result="$(run_remote_prepare_quantummux "${host}" "${port}" "${user}" "${password}")"; then
    echo "REMOTE_ERROR:${remote_result}"
    return 1
  fi

  echo "OK"
  return 0
}

apply_tun_bip_to_one() {
  local name="$1"
  local host="$2"
  local port="$3"
  local user="$4"
  local password="$5"

  if ! auth_capability_check "${password}" >/dev/null 2>&1; then
    echo "AUTH_ERROR:password_auth_not_supported_on_this_host"
    return 1
  fi

  if ! ssh_connectivity_check "${host}" "${port}" "${user}" "${password}" >/dev/null 2>&1; then
    echo "SSH_ERROR:ssh_connect_failed"
    return 1
  fi
  if ! remote_core_binary_check "${host}" "${port}" "${user}" "${password}" >/dev/null 2>&1; then
    echo "CORE_BINARY_MISSING:Please upload the core file manually to /usr/local/bin/DaggerConnect and run chmod +x /usr/local/bin/DaggerConnect"
    return 1
  fi

  local tmp_yaml tmp_service
  tmp_yaml="$(mktemp)"
  tmp_service="$(mktemp)"
  generate_tun_bip_server_yaml "${tmp_yaml}"
  generate_server_service_file "${tmp_service}"

  if ! scp_to_remote "${tmp_yaml}" "/tmp/autodagger-server.yaml" "${host}" "${port}" "${user}" "${password}" >/dev/null 2>&1; then
    rm -f "${tmp_yaml}" "${tmp_service}"
    echo "SCP_ERROR:config_upload_failed"
    return 1
  fi

  if ! scp_to_remote "${tmp_service}" "/tmp/DaggerConnect-server.service" "${host}" "${port}" "${user}" "${password}" >/dev/null 2>&1; then
    rm -f "${tmp_yaml}" "${tmp_service}"
    echo "SCP_ERROR:service_upload_failed"
    return 1
  fi

  rm -f "${tmp_yaml}" "${tmp_service}"

  local remote_result
  if ! remote_result="$(run_remote_prepare_tun_bip "${host}" "${port}" "${user}" "${password}")"; then
    echo "REMOTE_ERROR:${remote_result}"
    return 1
  fi

  echo "OK"
  return 0
}

apply_mode_to_all_servers() {
  local mode="$1"
  load_config

  if [[ ! -s "${SERVERS_FILE}" ]]; then
    print_banner
    echo "[ERROR] No servers saved."
    press_enter
    return
  fi

  if [[ "${mode}" == "tun_bip" && -z "${TUN_BIP_DEST_IP}" ]]; then
    print_banner
    echo "[ERROR] TUN+BIP dest_ip is not set."
    echo "Go to Configuration and set 'TUN+BIP dest_ip' first."
    press_enter
    return
  fi

  print_banner
  if [[ "${mode}" == "quantummux" ]]; then
    echo "Applying QUANTUMMUX server config to all saved servers..."
  else
    echo "Applying TUN+BIP server config to all saved servers..."
  fi
  echo

  local total index success failed
  local failure_details
  total="$(wc -l < "${SERVERS_FILE}")"
  index=0
  success=0
  failed=0
  failure_details=""

  while IFS=$'\t' read -r server_id name host port user password; do
    [[ -n "${server_id:-}" ]] || continue
    index=$((index + 1))
    echo "[${index}/${total}] ${name} (${host}:${port}) ..."

    local result
    if [[ "${mode}" == "quantummux" ]]; then
      result="$(apply_quantummux_to_one "${name}" "${host}" "${port}" "${user}" "${password}" || true)"
    else
      result="$(apply_tun_bip_to_one "${name}" "${host}" "${port}" "${user}" "${password}" || true)"
    fi

    if [[ "${result}" == "OK" ]]; then
      success=$((success + 1))
      echo "  [OK] applied and service restarted."
    else
      failed=$((failed + 1))
      echo "  [FAIL] ${result}"
      failure_details+="- ${name} (${host}:${port}) => ${result}"$'\n'
    fi
  done < "${SERVERS_FILE}"

  echo
  echo "Batch completed."
  echo "Summary: success=${success} | failed=${failed} | total=${total}"
  if [[ -n "${failure_details}" ]]; then
    echo
    echo "Failed servers:"
    printf "%s" "${failure_details}"
  fi
  press_enter
}

service_action_all() {
  local action="$1"
  load_config

  if [[ ! -s "${SERVERS_FILE}" ]]; then
    print_banner
    echo "[ERROR] No servers saved."
    press_enter
    return
  fi

  print_banner
  echo "Service action '${action}' on all saved servers..."
  echo

  local total index success failed
  total="$(wc -l < "${SERVERS_FILE}")"
  index=0
  success=0
  failed=0

  while IFS=$'\t' read -r server_id name host port user password; do
    [[ -n "${server_id:-}" ]] || continue
    index=$((index + 1))
    echo "[${index}/${total}] ${name} (${host}:${port}) ..."

    if ! auth_capability_check "${password}" >/dev/null 2>&1; then
      failed=$((failed + 1))
      echo "  [FAIL] auth_error:password_auth_not_supported_on_this_host"
      continue
    fi

    if ! ssh_connectivity_check "${host}" "${port}" "${user}" "${password}" >/dev/null 2>&1; then
      failed=$((failed + 1))
      echo "  [FAIL] ssh_connect_failed"
      continue
    fi

    if run_ssh_command "${host}" "${port}" "${user}" "${password}" "systemctl ${action} DaggerConnect-server.service" >/dev/null 2>&1; then
      success=$((success + 1))
      echo "  [OK]"
    else
      failed=$((failed + 1))
      echo "  [FAIL] systemctl_${action}_failed"
    fi
  done < "${SERVERS_FILE}"

  echo
  echo "Summary: success=${success} | failed=${failed} | total=${total}"
  press_enter
}

status_all_servers() {
  load_config
  print_banner

  if [[ ! -s "${SERVERS_FILE}" ]]; then
    echo "[ERROR] No servers saved."
    press_enter
    return
  fi

  echo "DaggerConnect-server status on all saved servers:"
  echo
  while IFS=$'\t' read -r server_id name host port user password; do
    [[ -n "${server_id:-}" ]] || continue
    printf "%s (%s:%s): " "${name}" "${host}" "${port}"

    if ! auth_capability_check "${password}" >/dev/null 2>&1; then
      echo "AUTH_ERROR (password auth not supported on this host)"
      continue
    fi

    if ! ssh_connectivity_check "${host}" "${port}" "${user}" "${password}" >/dev/null 2>&1; then
      echo "SSH_ERROR"
      continue
    fi

    local status
    status="$(run_ssh_command "${host}" "${port}" "${user}" "${password}" "systemctl is-active DaggerConnect-server.service 2>/dev/null || true" || true)"
    status="$(echo "${status}" | tr -d '\r\n' | xargs)"
    echo "${status:-unknown}"
  done < "${SERVERS_FILE}"

  press_enter
}

show_servers() {
  print_banner
  list_servers_table
  press_enter
}

main_menu() {
  while true; do
    load_config
    print_banner
    echo "1) Add server"
    echo "2) List servers"
    echo "3) Edit server"
    echo "4) Delete server"
    echo "5) Configuration"
    echo "6) Apply QuantumMux (server) to all"
    echo "7) Apply TUN+BIP (server) to all"
    echo "8) Start DaggerConnect-server on all"
    echo "9) Stop DaggerConnect-server on all"
    echo "10) Restart DaggerConnect-server on all"
    echo "11) Status on all"
    echo "12) Exit"
    echo
    echo "Current config: tunnel_port=${TUNNEL_PORT}, protocol=${MAP_PROTOCOL}, map_port=${MAP_PORT}, auto_optimize=${AUTO_OPTIMIZE}"
    if (( HAVE_SSHPASS == 1 )); then
      echo "Auth mode on this host: key + password (sshpass)"
    elif (( HAVE_SETSID == 1 )); then
      echo "Auth mode on this host: key + password (askpass fallback)"
    else
      echo "Auth mode on this host: key only (no sshpass/setsid)"
    fi
    echo

    local choice
    read -r -p "Select [1-12]: " choice
    case "${choice}" in
      1) add_server ;;
      2) show_servers ;;
      3) edit_server ;;
      4) delete_server ;;
      5) configure_defaults ;;
      6) apply_mode_to_all_servers "quantummux" ;;
      7) apply_mode_to_all_servers "tun_bip" ;;
      8) service_action_all "start" ;;
      9) service_action_all "stop" ;;
      10) service_action_all "restart" ;;
      11) status_all_servers ;;
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
ensure_dependencies
persist_manager_copy
init_storage
show_local_core_notice_once
main_menu
