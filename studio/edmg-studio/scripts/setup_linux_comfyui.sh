#!/usr/bin/env bash
set -euo pipefail

# Linux/Lightning ComfyUI sidecar setup for EDMG Studio.
#
# This script pins ComfyUI and custom nodes to reviewed commits and installs
# dependencies from checked-in snapshots rather than mutable upstream
# requirements at execution time.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONSTRAINTS_DIR="${SCRIPT_DIR}/constraints/comfyui"
# shellcheck source=setup_linux_comfyui.lock.sh
source "${SCRIPT_DIR}/setup_linux_comfyui.lock.sh"

EDMG_STUDIO_HOME="${EDMG_STUDIO_HOME:-${HOME}/edmg-studio-home}"
EDMG_STUDIO_EXTERNAL_DIR="${EDMG_STUDIO_EXTERNAL_DIR:-${EDMG_STUDIO_HOME}/external}"
EDMG_STUDIO_LOGS_DIR="${EDMG_STUDIO_LOGS_DIR:-${EDMG_STUDIO_HOME}/logs}"
COMFY_ROOT="${COMFY_ROOT:-${EDMG_STUDIO_EXTERNAL_DIR}/ComfyUI}"
COMFY_HOST="${COMFY_HOST:-127.0.0.1}"
COMFY_PORT="${COMFY_PORT:-8188}"
COMFY_PYTHON_BIN="${COMFY_PYTHON_BIN:-python}"
COMFY_LOG_DIR="${COMFY_LOG_DIR:-${EDMG_STUDIO_LOGS_DIR}}"
COMFY_LOG_FILE="${COMFY_LOG_FILE:-${COMFY_LOG_DIR}/comfyui.log}"
COMFY_INSTALL_MODELS="${COMFY_INSTALL_MODELS:-0}"
COMFY_START="${COMFY_START:-1}"
COMFY_INSTALL_NODES="${COMFY_INSTALL_NODES:-1}"
COMFY_ALLOW_VERSION_OVERRIDE="${COMFY_ALLOW_VERSION_OVERRIDE:-0}"
COMFY_PID_FILE="${COMFY_PID_FILE:-${COMFY_LOG_DIR}/comfyui.pid}"
HF_HUB_ENABLE_HF_TRANSFER="${HF_HUB_ENABLE_HF_TRANSFER:-1}"
HF_TOKEN_VALUE="${HF_TOKEN:-${HUGGING_FACE_HUB_TOKEN:-}}"
export HF_HUB_ENABLE_HF_TRANSFER

log() {
  echo "[comfyui-linux] $*"
}

warn() {
  echo "[comfyui-linux][warn] $*" >&2
}

fail() {
  echo "[comfyui-linux][error] $*" >&2
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
  if [[ -f "${COMFY_PID_FILE}" ]]; then
    local current_pid
    current_pid="$(tr -d '[:space:]' <"${COMFY_PID_FILE}" 2>/dev/null || true)"
    if [[ "${current_pid}" == "${pid}" ]]; then
      rm -f "${COMFY_PID_FILE}"
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
  fail "Tracked ComfyUI PID ${pid} did not exit cleanly."
}

require_locked_value() {
  local label="$1"
  local actual="$2"
  local locked="$3"
  if [[ "${actual}" != "${locked}" && "${COMFY_ALLOW_VERSION_OVERRIDE}" != "1" ]]; then
    fail "${label} override requested without COMFY_ALLOW_VERSION_OVERRIDE=1."
  fi
}

sync_pinned_repo() {
  local repo_url="$1"
  local ref="$2"
  local dest="$3"
  local label="$4"

  if [[ -d "${dest}/.git" ]]; then
    log "Refreshing ${label} -> ${ref}"
  else
    log "Cloning ${label} -> ${dest}"
    git clone --no-checkout "${repo_url}" "${dest}"
  fi
  git -C "${dest}" remote set-url origin "${repo_url}"
  git -C "${dest}" fetch --depth 1 origin "${ref}"
  git -C "${dest}" checkout --force --detach "${ref}"
  local head
  head="$(git -C "${dest}" rev-parse HEAD)"
  if [[ "${head}" != "${ref}" ]]; then
    fail "${label} checkout drifted (expected ${ref}, got ${head})"
  fi
}

install_snapshot_requirements() {
  local snapshot="$1"
  local label="$2"
  if [[ ! -f "${snapshot}" ]]; then
    fail "Missing checked-in requirements snapshot: ${snapshot}"
  fi
  if ! grep -Eq '^[[:space:]]*[^#[:space:]]' "${snapshot}"; then
    log "No extra Python dependencies declared for ${label}."
    return 0
  fi
  log "Installing ${label} requirements from ${snapshot}"
  "${UV_BIN}" pip install --python "${COMFY_PYTHON_BIN}" -r "${snapshot}"
}

download_to_temp() {
  local url="$1"
  local output="$2"
  if [[ -n "${HF_TOKEN_VALUE}" ]]; then
    curl -fL --retry 3 --retry-delay 2 \
      -H "Authorization: Bearer ${HF_TOKEN_VALUE}" \
      --output "${output}" \
      "${url}"
  else
    curl -fL --retry 3 --retry-delay 2 --output "${output}" "${url}"
  fi
}

download_verified_model() {
  local repo_id="$1"
  local revision="$2"
  local filename="$3"
  local expected_sha="$4"
  local dest_dir="$5"
  local label="$6"
  local dest_path="${dest_dir}/${filename}"
  local tmp_path="${dest_path}.partial.$$"
  local url="https://huggingface.co/${repo_id}/resolve/${revision}/${filename}?download=1"

  [[ -n "${expected_sha}" ]] || fail "${label} SHA-256 is required before download."

  mkdir -p "${dest_dir}"
  if [[ -f "${dest_path}" ]]; then
    local existing_sha
    existing_sha="$(sha256_file "${dest_path}")"
    if [[ "${existing_sha}" == "${expected_sha}" ]]; then
      log "Using cached ${label}: ${dest_path}"
      return 0
    fi
    rm -f "${dest_path}"
  fi

  rm -f "${tmp_path}"
  log "Downloading ${label} from ${repo_id}@${revision}"
  download_to_temp "${url}" "${tmp_path}"
  local actual_sha
  actual_sha="$(sha256_file "${tmp_path}")"
  if [[ "${actual_sha}" != "${expected_sha}" ]]; then
    rm -f "${tmp_path}"
    fail "${label} SHA-256 mismatch (expected ${expected_sha}, got ${actual_sha})"
  fi
  mv -f "${tmp_path}" "${dest_path}"
}

COMFY_REPO_URL="${COMFY_REPO_URL:-${EDMG_LOCKED_COMFYUI_REPO_URL}}"
COMFY_REPO_REF="${COMFY_REPO_REF:-${EDMG_LOCKED_COMFYUI_REF}}"
COMFY_MANAGER_REPO_URL="${COMFY_MANAGER_REPO_URL:-${EDMG_LOCKED_COMFYUI_MANAGER_REPO_URL}}"
COMFY_MANAGER_REPO_REF="${COMFY_MANAGER_REPO_REF:-${EDMG_LOCKED_COMFYUI_MANAGER_REF}}"
COMFY_ANIMATEDIFF_REPO_URL="${COMFY_ANIMATEDIFF_REPO_URL:-${EDMG_LOCKED_ANIMATEDIFF_REPO_URL}}"
COMFY_ANIMATEDIFF_REPO_REF="${COMFY_ANIMATEDIFF_REPO_REF:-${EDMG_LOCKED_ANIMATEDIFF_REF}}"
COMFY_SVD_NODE_REPO_URL="${COMFY_SVD_NODE_REPO_URL:-${EDMG_LOCKED_SVD_NODE_REPO_URL}}"
COMFY_SVD_NODE_REPO_REF="${COMFY_SVD_NODE_REPO_REF:-${EDMG_LOCKED_SVD_NODE_REF}}"

COMFY_SDXL_BASE_REPO="${COMFY_SDXL_BASE_REPO:-${EDMG_LOCKED_SDXL_BASE_REPO}}"
COMFY_SDXL_BASE_REVISION="${COMFY_SDXL_BASE_REVISION:-${EDMG_LOCKED_SDXL_BASE_REVISION}}"
COMFY_SDXL_BASE_FILE="${COMFY_SDXL_BASE_FILE:-${EDMG_LOCKED_SDXL_BASE_FILE}}"
COMFY_SDXL_BASE_SHA256="${COMFY_SDXL_BASE_SHA256:-${EDMG_LOCKED_SDXL_BASE_SHA256}}"

COMFY_SVD_REPO="${COMFY_SVD_REPO:-${EDMG_LOCKED_SVD_REPO}}"
COMFY_SVD_REVISION="${COMFY_SVD_REVISION:-${EDMG_LOCKED_SVD_REVISION}}"
COMFY_SVD_FILE="${COMFY_SVD_FILE:-${EDMG_LOCKED_SVD_FILE}}"
COMFY_SVD_SHA256="${COMFY_SVD_SHA256:-${EDMG_LOCKED_SVD_SHA256}}"

COMFY_ANIMATEDIFF_MODEL_REPO="${COMFY_ANIMATEDIFF_MODEL_REPO:-${EDMG_LOCKED_ANIMATEDIFF_MODEL_REPO}}"
COMFY_ANIMATEDIFF_MODEL_REVISION="${COMFY_ANIMATEDIFF_MODEL_REVISION:-${EDMG_LOCKED_ANIMATEDIFF_MODEL_REVISION}}"
COMFY_ANIMATEDIFF_MODEL_FILE="${COMFY_ANIMATEDIFF_MODEL_FILE:-${EDMG_LOCKED_ANIMATEDIFF_MODEL_FILE}}"
COMFY_ANIMATEDIFF_MODEL_SHA256="${COMFY_ANIMATEDIFF_MODEL_SHA256:-${EDMG_LOCKED_ANIMATEDIFF_MODEL_SHA256}}"

require_locked_value "ComfyUI repo URL" "${COMFY_REPO_URL}" "${EDMG_LOCKED_COMFYUI_REPO_URL}"
require_locked_value "ComfyUI repo ref" "${COMFY_REPO_REF}" "${EDMG_LOCKED_COMFYUI_REF}"
require_locked_value "ComfyUI-Manager repo URL" "${COMFY_MANAGER_REPO_URL}" "${EDMG_LOCKED_COMFYUI_MANAGER_REPO_URL}"
require_locked_value "ComfyUI-Manager repo ref" "${COMFY_MANAGER_REPO_REF}" "${EDMG_LOCKED_COMFYUI_MANAGER_REF}"
require_locked_value "AnimateDiff repo URL" "${COMFY_ANIMATEDIFF_REPO_URL}" "${EDMG_LOCKED_ANIMATEDIFF_REPO_URL}"
require_locked_value "AnimateDiff repo ref" "${COMFY_ANIMATEDIFF_REPO_REF}" "${EDMG_LOCKED_ANIMATEDIFF_REF}"
require_locked_value "Stable Video Diffusion node repo URL" "${COMFY_SVD_NODE_REPO_URL}" "${EDMG_LOCKED_SVD_NODE_REPO_URL}"
require_locked_value "Stable Video Diffusion node repo ref" "${COMFY_SVD_NODE_REPO_REF}" "${EDMG_LOCKED_SVD_NODE_REF}"
require_locked_value "SDXL base repo" "${COMFY_SDXL_BASE_REPO}" "${EDMG_LOCKED_SDXL_BASE_REPO}"
require_locked_value "SDXL base revision" "${COMFY_SDXL_BASE_REVISION}" "${EDMG_LOCKED_SDXL_BASE_REVISION}"
require_locked_value "SDXL base SHA-256" "${COMFY_SDXL_BASE_SHA256}" "${EDMG_LOCKED_SDXL_BASE_SHA256}"
require_locked_value "SVD repo" "${COMFY_SVD_REPO}" "${EDMG_LOCKED_SVD_REPO}"
require_locked_value "SVD revision" "${COMFY_SVD_REVISION}" "${EDMG_LOCKED_SVD_REVISION}"
require_locked_value "SVD SHA-256" "${COMFY_SVD_SHA256}" "${EDMG_LOCKED_SVD_SHA256}"
require_locked_value "AnimateDiff model repo" "${COMFY_ANIMATEDIFF_MODEL_REPO}" "${EDMG_LOCKED_ANIMATEDIFF_MODEL_REPO}"
require_locked_value "AnimateDiff model revision" "${COMFY_ANIMATEDIFF_MODEL_REVISION}" "${EDMG_LOCKED_ANIMATEDIFF_MODEL_REVISION}"
require_locked_value "AnimateDiff model SHA-256" "${COMFY_ANIMATEDIFF_MODEL_SHA256}" "${EDMG_LOCKED_ANIMATEDIFF_MODEL_SHA256}"

require_cmd git
require_cmd curl
require_cmd sha256sum
# shellcheck source=uv_toolchain.sh
source "${SCRIPT_DIR}/uv_toolchain.sh"
UV_BIN="$(edmg_require_uv)"
require_cmd "${COMFY_PYTHON_BIN}"

mkdir -p "$(dirname "${COMFY_ROOT}")" "${COMFY_LOG_DIR}" "${COMFY_ROOT}/custom_nodes"

sync_pinned_repo "${COMFY_REPO_URL}" "${COMFY_REPO_REF}" "${COMFY_ROOT}" "ComfyUI"
install_snapshot_requirements "${CONSTRAINTS_DIR}/comfyui-requirements.txt" "ComfyUI core"

if [[ "${COMFY_INSTALL_NODES}" == "1" ]]; then
  sync_pinned_repo "${COMFY_MANAGER_REPO_URL}" "${COMFY_MANAGER_REPO_REF}" "${COMFY_ROOT}/custom_nodes/comfyui-manager" "ComfyUI-Manager"
  install_snapshot_requirements "${CONSTRAINTS_DIR}/comfyui-manager-requirements.txt" "ComfyUI-Manager"

  sync_pinned_repo "${COMFY_ANIMATEDIFF_REPO_URL}" "${COMFY_ANIMATEDIFF_REPO_REF}" "${COMFY_ROOT}/custom_nodes/ComfyUI-AnimateDiff-Evolved" "ComfyUI-AnimateDiff-Evolved"
  install_snapshot_requirements "${CONSTRAINTS_DIR}/comfyui-animatediff-evolved-requirements.txt" "ComfyUI-AnimateDiff-Evolved"

  sync_pinned_repo "${COMFY_SVD_NODE_REPO_URL}" "${COMFY_SVD_NODE_REPO_REF}" "${COMFY_ROOT}/custom_nodes/ComfyUI-Stable-Video-Diffusion" "ComfyUI-Stable-Video-Diffusion"
  install_snapshot_requirements "${CONSTRAINTS_DIR}/comfyui-stable-video-diffusion-requirements.txt" "ComfyUI-Stable-Video-Diffusion"
fi

mkdir -p \
  "${COMFY_ROOT}/models/checkpoints" \
  "${COMFY_ROOT}/models/svd" \
  "${COMFY_ROOT}/models/animatediff_models"

if [[ "${COMFY_INSTALL_MODELS}" == "1" ]]; then
  log "Installing Hugging Face download helpers"
  "${UV_BIN}" pip install --python "${COMFY_PYTHON_BIN}" -U \
    "huggingface_hub>=0.34.0,<1.0" \
    "hf_transfer==0.1.9" \
    "hf_xet==1.5.1"

  download_verified_model \
    "${COMFY_SDXL_BASE_REPO}" \
    "${COMFY_SDXL_BASE_REVISION}" \
    "${COMFY_SDXL_BASE_FILE}" \
    "${COMFY_SDXL_BASE_SHA256}" \
    "${COMFY_ROOT}/models/checkpoints" \
    "SDXL base checkpoint"

  download_verified_model \
    "${COMFY_SVD_REPO}" \
    "${COMFY_SVD_REVISION}" \
    "${COMFY_SVD_FILE}" \
    "${COMFY_SVD_SHA256}" \
    "${COMFY_ROOT}/models/svd" \
    "Stable Video Diffusion XT 1.1 checkpoint"
  if [[ -f "${COMFY_ROOT}/models/svd/${COMFY_SVD_FILE}" && ! -e "${COMFY_ROOT}/models/svd/svd_xt.safetensors" ]]; then
    ln -s "${COMFY_SVD_FILE}" "${COMFY_ROOT}/models/svd/svd_xt.safetensors"
  fi

  download_verified_model \
    "${COMFY_ANIMATEDIFF_MODEL_REPO}" \
    "${COMFY_ANIMATEDIFF_MODEL_REVISION}" \
    "${COMFY_ANIMATEDIFF_MODEL_FILE}" \
    "${COMFY_ANIMATEDIFF_MODEL_SHA256}" \
    "${COMFY_ROOT}/models/animatediff_models" \
    "AnimateDiff motion adapter"
fi

log "Validating PyTorch"
"${COMFY_PYTHON_BIN}" - <<'PY'
try:
    import torch
    print("torch", torch.__version__)
    print("cuda_build", torch.version.cuda)
    print("cuda_available", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("device_0", torch.cuda.get_device_name(0))
except Exception as exc:
    print("torch_check_error", repr(exc))
PY

if [[ "${COMFY_START}" == "1" ]]; then
  if curl -fsS "http://${COMFY_HOST}:${COMFY_PORT}/object_info" >/dev/null 2>&1; then
    log "ComfyUI is already reachable at http://${COMFY_HOST}:${COMFY_PORT}"
  else
    if [[ -f "${COMFY_PID_FILE}" ]]; then
      existing_pid="$(tr -d '[:space:]' <"${COMFY_PID_FILE}" 2>/dev/null || true)"
      if pid_alive "${existing_pid:-0}"; then
        log "Stopping tracked ComfyUI PID ${existing_pid} before restart."
        stop_tracked_pid "${existing_pid}"
      else
        rm -f "${COMFY_PID_FILE}"
      fi
    fi
    log "Starting ComfyUI at http://${COMFY_HOST}:${COMFY_PORT}"
    (
      cd "${COMFY_ROOT}"
      "${COMFY_PYTHON_BIN}" main.py --listen "${COMFY_HOST}" --port "${COMFY_PORT}"
    ) >"${COMFY_LOG_FILE}" 2>&1 &
    started_pid="$!"
    printf '%s\n' "${started_pid}" >"${COMFY_PID_FILE}"
    if ! wait_for_http "http://${COMFY_HOST}:${COMFY_PORT}/object_info" 90 2; then
      stop_tracked_pid "${started_pid}"
      warn "ComfyUI is not reachable yet. Check ${COMFY_LOG_FILE}."
    fi
  fi
fi

COMFY_OBJECT_INFO_FILE="${COMFY_LOG_DIR}/comfyui-object-info.json"
if curl -fsS "http://${COMFY_HOST}:${COMFY_PORT}/object_info" >"${COMFY_OBJECT_INFO_FILE}" 2>/dev/null; then
  log "ComfyUI capability summary"
  COMFY_OBJECT_INFO_FILE="${COMFY_OBJECT_INFO_FILE}" "${COMFY_PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

obj = json.loads(Path(os.environ["COMFY_OBJECT_INFO_FILE"]).read_text(encoding="utf-8"))
for node in ("ADE_AnimateDiffLoaderGen1", "ADE_StandardStaticContextOptions", "SVDSimpleImg2Vid"):
    print(f"{node}={node in obj}")
PY
else
  rm -f "${COMFY_OBJECT_INFO_FILE}"
  warn "ComfyUI is not reachable yet. Check ${COMFY_LOG_FILE}."
fi

log "Done"
log "ComfyUI root: ${COMFY_ROOT}"
log "ComfyUI URL for EDMG backend: http://${COMFY_HOST}:${COMFY_PORT}"
log "Log file: ${COMFY_LOG_FILE}"
