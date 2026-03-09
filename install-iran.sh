#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_MANAGER="${SCRIPT_DIR}/scripts/autodagger-iran-manager.sh"

if [[ -f "${LOCAL_MANAGER}" ]]; then
  exec bash "${LOCAL_MANAGER}"
fi

REPO_OWNER="${AUTO_DAGGER_REPO_OWNER:-B3hnamR}"
REPO_NAME="${AUTO_DAGGER_REPO_NAME:-AutoDaggerTunnel}"
BRANCH="${AUTO_DAGGER_BRANCH:-main}"
MANAGER_URL="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${BRANCH}/scripts/autodagger-iran-manager.sh"

TMP_SCRIPT="$(mktemp /tmp/autodagger-iran-manager.XXXXXX.sh)"
trap 'rm -f "${TMP_SCRIPT}"' EXIT

if command -v curl >/dev/null 2>&1; then
  curl -fsSL "${MANAGER_URL}" -o "${TMP_SCRIPT}"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "${TMP_SCRIPT}" "${MANAGER_URL}"
else
  echo "[ERROR] curl or wget is required to download manager script."
  exit 1
fi

exec bash "${TMP_SCRIPT}"
