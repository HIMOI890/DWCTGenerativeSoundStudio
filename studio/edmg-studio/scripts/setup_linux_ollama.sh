#!/usr/bin/env bash
set -euo pipefail

# Linux/Lightning Ollama sidecar setup for EDMG Studio.
#
# This stages a reviewed Ollama release locally under EDMG_STUDIO_EXTERNAL_DIR
# instead of piping a mutable installer into sh.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=setup_linux_ollama.lock.sh
source "${SCRIPT_DIR}/setup_linux_ollama.lock.sh"

EDMG_STUDIO_HOME="${EDMG_STUDIO_HOME:-${HOME}/edmg-studio-home}"
EDMG_STUDIO_DATA_DIR="${EDMG_STUDIO_DATA_DIR:-${EDMG_STUDIO_HOME}/data}"
EDMG_STUDIO_MODELS_DIR="${EDMG_STUDIO_MODELS_DIR:-${EDMG_STUDIO_HOME}/models}"
EDMG_STUDIO_CACHE_DIR="${EDMG_STUDIO_CACHE_DIR:-${EDMG_STUDIO_HOME}/cache}"
EDMG_STUDIO_LOGS_DIR="${EDMG_STUDIO_LOGS_DIR:-${EDMG_STUDIO_HOME}/logs}"
EDMG_STUDIO_EXTERNAL_DIR="${EDMG_STUDIO_EXTERNAL_DIR:-${EDMG_STUDIO_HOME}/external}"
OLLAMA_HOST_VALUE="${OLLAMA_HOST:-127.0.0.1:${OLLAMA_PORT:-11434}}"
OLLAMA_MODELS="${OLLAMA_MODELS:-${EDMG_STUDIO_MODELS_DIR}/ollama}"
OLLAMA_LOG_DIR="${OLLAMA_LOG_DIR:-${EDMG_STUDIO_LOGS_DIR}}"
OLLAMA_LOG_FILE="${OLLAMA_LOG_FILE:-${OLLAMA_LOG_DIR}/ollama.log}"
EDMG_AI_OLLAMA_MODEL="${EDMG_AI_OLLAMA_MODEL:-nemotron-3-ultra:cloud}"
OLLAMA_START="${OLLAMA_START:-1}"
OLLAMA_PULL_MODEL="${OLLAMA_PULL_MODEL:-1}"
OLLAMA_SIGNIN="${OLLAMA_SIGNIN:-0}"
OLLAMA_START_PUBLIC="${OLLAMA_START_PUBLIC:-0}"
OLLAMA_ALLOW_VERSION_OVERRIDE="${OLLAMA_ALLOW_VERSION_OVERRIDE:-0}"
OLLAMA_PID_FILE="${OLLAMA_PID_FILE:-${OLLAMA_LOG_DIR}/ollama.pid}"
OLLAMA_RELEASES_DIR="${OLLAMA_RELEASES_DIR:-${EDMG_STUDIO_EXTERNAL_DIR}/_installers/ollama}"

log() {
  echo "[ollama-linux] $*"
}

warn() {
  echo "[ollama-linux][warn] $*" >&2
}

fail() {
  echo "[ollama-linux][error] $*" >&2
  exit 1
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "Missing required command: $1"
  fi
}

sha256_file() {
  sha256sum "$1" | awk '{print $1}'
}

wait_for_http() {
  local url="$1"
  local attempts="$2"
  local delay_s="$3"
  local i
  for ((i = 0; i < attempts; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$delay_s"
  done
  return 1
}

pid_alive() {
  local pid="${1:-0}"
  [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1
}

remove_pid_file_if_matching() {
  local pid="$1"
  if [[ -f "${OLLAMA_PID_FILE}" ]]; then
    local current_pid
    current_pid="$(tr -d '[:space:]' <"${OLLAMA_PID_FILE}" 2>/dev/null || true)"
    if [[ "${current_pid}" == "${pid}" ]]; then
      rm -f "${OLLAMA_PID_FILE}"
    fi
  fi
}

stop_tracked_pid() {
  local pid="$1"
  if ! pid_alive "${pid}"; then
    remove_pid_file_if_matching "${pid}"
    return 0
  fi
  kill "${pid}" >/dev/null 2>&1 || true
  local i
  for ((i = 0; i < 100; i++)); do
    if ! pid_alive "${pid}"; then
      remove_pid_file_if_matching "${pid}"
      return 0
    fi
    sleep 0.1
  done
  kill -9 "${pid}" >/dev/null 2>&1 || true
  for ((i = 0; i < 50; i++)); do
    if ! pid_alive "${pid}"; then
      remove_pid_file_if_matching "${pid}"
      return 0
    fi
    sleep 0.1
  done
  fail "Tracked Ollama PID ${pid} did not exit cleanly."
}

download_verified_file() {
  local url="$1"
  local expected_sha="$2"
  local dest="$3"
  local label="$4"
  local tmp_file="${dest}.partial.$$"

  mkdir -p "$(dirname "${dest}")"
  if [[ -f "${dest}" ]]; then
    local existing_sha
    existing_sha="$(sha256_file "${dest}")"
    if [[ "${existing_sha}" == "${expected_sha}" ]]; then
      log "Using cached ${label}: ${dest}"
      return 0
    fi
    rm -f "${dest}"
  fi

  rm -f "${tmp_file}"
  log "Downloading ${label}: ${url}"
  curl -fL --retry 3 --retry-delay 2 --output "${tmp_file}" "${url}"
  local actual_sha
  actual_sha="$(sha256_file "${tmp_file}")"
  if [[ "${actual_sha}" != "${expected_sha}" ]]; then
    rm -f "${tmp_file}"
    fail "${label} SHA-256 mismatch (expected ${expected_sha}, got ${actual_sha})"
  fi
  mv -f "${tmp_file}" "${dest}"
}

resolve_arch_key() {
  case "$(uname -m)" in
    x86_64|amd64) echo "amd64" ;;
    aarch64|arm64) echo "arm64" ;;
    *) fail "Unsupported Linux architecture: $(uname -m)" ;;
  esac
}

resolve_ollama_asset_for_arch() {
  case "$1" in
    amd64)
      echo "${EDMG_LOCKED_OLLAMA_ASSET_AMD64}|${EDMG_LOCKED_OLLAMA_SHA256_AMD64}"
      ;;
    arm64)
      echo "${EDMG_LOCKED_OLLAMA_ASSET_ARM64}|${EDMG_LOCKED_OLLAMA_SHA256_ARM64}"
      ;;
    *)
      fail "No locked Ollama asset is defined for architecture $1"
      ;;
  esac
}

require_locked_release() {
  local arch_key="$1"
  local locked_release="${EDMG_LOCKED_OLLAMA_VERSION}"
  local locked_asset_sha
  locked_asset_sha="$(resolve_ollama_asset_for_arch "${arch_key}")"
  local locked_asset="${locked_asset_sha%%|*}"
  local locked_sha="${locked_asset_sha##*|}"

  OLLAMA_RELEASE_TAG="${EDMG_OLLAMA_VERSION:-${locked_release}}"
  OLLAMA_RELEASE_ASSET="${OLLAMA_RELEASE_ASSET:-${locked_asset}}"
  OLLAMA_RELEASE_SHA256="${OLLAMA_RELEASE_SHA256:-${locked_sha}}"

  if [[ "${OLLAMA_ALLOW_VERSION_OVERRIDE}" != "1" ]] && {
    [[ "${OLLAMA_RELEASE_TAG}" != "${locked_release}" ]] ||
    [[ "${OLLAMA_RELEASE_ASSET}" != "${locked_asset}" ]] ||
    [[ "${OLLAMA_RELEASE_SHA256}" != "${locked_sha}" ]]
  }; then
    fail "Ollama release override requested without OLLAMA_ALLOW_VERSION_OVERRIDE=1."
  fi
  if [[ -z "${OLLAMA_RELEASE_SHA256}" ]]; then
    fail "OLLAMA_RELEASE_SHA256 is required."
  fi
}

resolve_ollama_binary() {
  local root="$1"
  if [[ -x "${root}/bin/ollama" ]]; then
    echo "${root}/bin/ollama"
    return 0
  fi
  if [[ -x "${root}/ollama" ]]; then
    echo "${root}/ollama"
    return 0
  fi
  local candidate
  candidate="$(find "${root}" -maxdepth 3 -type f -name ollama -perm -111 2>/dev/null | head -n 1 || true)"
  [[ -n "${candidate}" ]] || return 1
  echo "${candidate}"
}

install_local_ollama_release() {
  local archive_path="$1"
  local install_root="$2"
  local extract_root="${install_root}.extract.$$"

  rm -rf "${extract_root}"
  mkdir -p "${extract_root}"
  tar --zstd -xf "${archive_path}" -C "${extract_root}"

  local extracted_bin
  extracted_bin="$(resolve_ollama_binary "${extract_root}")" || {
    rm -rf "${extract_root}"
    fail "Unable to find ollama binary after extracting ${archive_path}"
  }

  rm -rf "${install_root}"
  mv -f "${extract_root}" "${install_root}"

  OLLAMA_BIN="$(resolve_ollama_binary "${install_root}")" || fail "Installed Ollama binary missing under ${install_root}"
  OLLAMA_BIN_DIR="$(dirname "${OLLAMA_BIN}")"
}

export EDMG_STUDIO_HOME
export OLLAMA_MODELS
export EDMG_AI_MODE="${EDMG_AI_MODE:-local}"
export EDMG_AI_PROVIDER="${EDMG_AI_PROVIDER:-ollama}"
export EDMG_AI_OLLAMA_URL="${EDMG_AI_OLLAMA_URL:-http://127.0.0.1:${OLLAMA_PORT:-11434}}"
export EDMG_AI_OLLAMA_MODEL

if [[ "${OLLAMA_START_PUBLIC}" == "1" ]]; then
  warn "OLLAMA_START_PUBLIC=1 exposes Ollama beyond localhost. Only use this behind a firewall."
else
  OLLAMA_HOST_VALUE="127.0.0.1:${OLLAMA_PORT:-11434}"
fi
export OLLAMA_HOST="${OLLAMA_HOST_VALUE}"

require_cmd curl
require_cmd sha256sum
require_cmd tar

ARCH_KEY="$(resolve_arch_key)"
require_locked_release "${ARCH_KEY}"

OLLAMA_RELEASE_URL="https://github.com/ollama/ollama/releases/download/${OLLAMA_RELEASE_TAG}/${OLLAMA_RELEASE_ASSET}"
OLLAMA_INSTALL_ROOT="${OLLAMA_INSTALL_ROOT:-${EDMG_STUDIO_EXTERNAL_DIR}/ollama/${OLLAMA_RELEASE_TAG}-${ARCH_KEY}}"
OLLAMA_ARCHIVE_PATH="${OLLAMA_RELEASES_DIR}/${OLLAMA_RELEASE_TAG}/${OLLAMA_RELEASE_ASSET}"

mkdir -p \
  "${OLLAMA_MODELS}" \
  "${OLLAMA_LOG_DIR}" \
  "${EDMG_STUDIO_DATA_DIR}" \
  "${EDMG_STUDIO_CACHE_DIR}" \
  "${EDMG_STUDIO_EXTERNAL_DIR}" \
  "${OLLAMA_RELEASES_DIR}"

download_verified_file "${OLLAMA_RELEASE_URL}" "${OLLAMA_RELEASE_SHA256}" "${OLLAMA_ARCHIVE_PATH}" "Ollama release archive"
install_local_ollama_release "${OLLAMA_ARCHIVE_PATH}" "${OLLAMA_INSTALL_ROOT}"
PATH="${OLLAMA_BIN_DIR}:${PATH}"
export PATH
export EDMG_OLLAMA_BIN="${OLLAMA_BIN}"

log "Using pinned Ollama release ${OLLAMA_RELEASE_TAG} from ${OLLAMA_BIN}"

if [[ "${OLLAMA_SIGNIN}" == "1" ]]; then
  log "Starting Ollama sign-in. Open the printed URL in your browser."
  "${OLLAMA_BIN}" signin
fi

if [[ "${OLLAMA_START}" == "1" ]]; then
  if curl -fsS "http://127.0.0.1:${OLLAMA_PORT:-11434}/api/version" >/dev/null 2>&1; then
    log "Ollama is already reachable at http://127.0.0.1:${OLLAMA_PORT:-11434}"
  else
    if [[ -f "${OLLAMA_PID_FILE}" ]]; then
      existing_pid="$(tr -d '[:space:]' <"${OLLAMA_PID_FILE}" 2>/dev/null || true)"
      if pid_alive "${existing_pid:-0}"; then
        log "Stopping tracked Ollama PID ${existing_pid} before restart."
        stop_tracked_pid "${existing_pid}"
      else
        rm -f "${OLLAMA_PID_FILE}"
      fi
    fi
    log "Starting Ollama at ${OLLAMA_HOST}"
    env OLLAMA_HOST="${OLLAMA_HOST}" OLLAMA_MODELS="${OLLAMA_MODELS}" "${OLLAMA_BIN}" serve >"${OLLAMA_LOG_FILE}" 2>&1 &
    started_pid="$!"
    printf '%s\n' "${started_pid}" >"${OLLAMA_PID_FILE}"
    if ! wait_for_http "http://127.0.0.1:${OLLAMA_PORT:-11434}/api/version" 60 2; then
      stop_tracked_pid "${started_pid}"
      fail "Ollama is not reachable. Check ${OLLAMA_LOG_FILE}."
    fi
  fi
fi

if ! curl -fsS "http://127.0.0.1:${OLLAMA_PORT:-11434}/api/version" >/dev/null 2>&1; then
  fail "Ollama is not reachable. Check ${OLLAMA_LOG_FILE}."
fi

log "Ollama API is reachable"
curl -fsS "http://127.0.0.1:${OLLAMA_PORT:-11434}/api/version" || true
echo

if [[ "${OLLAMA_PULL_MODEL}" == "1" ]]; then
  log "Pulling ${EDMG_AI_OLLAMA_MODEL}"
  if ! OLLAMA_HOST="http://127.0.0.1:${OLLAMA_PORT:-11434}" OLLAMA_MODELS="${OLLAMA_MODELS}" "${OLLAMA_BIN}" pull "${EDMG_AI_OLLAMA_MODEL}"; then
    if [[ "${EDMG_AI_OLLAMA_MODEL}" == *":cloud"* ]]; then
      cat >&2 <<EOF
[ollama-linux][error] Failed to pull cloud model ${EDMG_AI_OLLAMA_MODEL}.

Cloud models require Ollama sign-in on this machine:

  OLLAMA_SIGNIN=1 bash scripts/setup_linux_ollama.sh

Open the printed URL, complete sign-in, then rerun this script.
EOF
    fi
    exit 1
  fi
fi

cat >"${EDMG_STUDIO_HOME}/ollama.env" <<EOF
export EDMG_AI_MODE=local
export EDMG_AI_PROVIDER=ollama
export EDMG_AI_OLLAMA_URL=http://127.0.0.1:${OLLAMA_PORT:-11434}
export EDMG_AI_OLLAMA_MODEL=${EDMG_AI_OLLAMA_MODEL}
export OLLAMA_MODELS=${OLLAMA_MODELS}
export EDMG_OLLAMA_BIN=${OLLAMA_BIN}
export PATH=${OLLAMA_BIN_DIR}:\$PATH
EOF

OLLAMA_CHAT_TEST_FILE="${OLLAMA_LOG_DIR}/ollama-chat-test.json"
log "Testing ${EDMG_AI_OLLAMA_MODEL}"
if ! curl -fsS "http://127.0.0.1:${OLLAMA_PORT:-11434}/api/chat" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${EDMG_AI_OLLAMA_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"Say ok.\"}],\"stream\":false}" >"${OLLAMA_CHAT_TEST_FILE}"; then
  rm -f "${OLLAMA_CHAT_TEST_FILE}"
  if [[ "${EDMG_AI_OLLAMA_MODEL}" == *":cloud"* ]]; then
    warn "Chat test failed. If the response is Unauthorized, rerun with OLLAMA_SIGNIN=1 and restart Ollama."
  else
    warn "Chat test failed. Inspect ${OLLAMA_LOG_FILE}."
  fi
else
  log "Chat test response written to ${OLLAMA_CHAT_TEST_FILE}"
fi

log "Done"
log "EDMG Ollama env: ${EDMG_STUDIO_HOME}/ollama.env"
log "Backend exports:"
echo "  export EDMG_AI_MODE=local"
echo "  export EDMG_AI_PROVIDER=ollama"
echo "  export EDMG_AI_OLLAMA_URL=http://127.0.0.1:${OLLAMA_PORT:-11434}"
echo "  export EDMG_AI_OLLAMA_MODEL=${EDMG_AI_OLLAMA_MODEL}"
