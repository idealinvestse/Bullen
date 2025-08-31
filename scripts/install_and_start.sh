#!/usr/bin/env bash
# Bullen installer & launcher for Raspberry Pi (Debian-based)
#
# Usage:
#   ./scripts/install_and_start.sh [--apt] [--systemd] [--host=0.0.0.0] [--port=8000] [--config=path]
#                                  [--no-start] [--service-name=bullen] [--user=pi]
#                                  [--backend=jack|dummy]
#                                  [--no-jack-start] [--jack-device=hw:0]
#                                  [--jack-rate=48000] [--jack-frames=128] [--jack-periods=2]
#
# Flags:
#   --apt          Install OS packages via apt (jack headers, sndfile, python venv/pip)
#   --systemd      Install/enable systemd service to run at boot
#   --host=...     Override BULLEN_HOST (default 0.0.0.0)
#   --port=...     Override BULLEN_PORT (default 8000)
#   --config=...   Set BULLEN_CONFIG (default: project config.yaml)
#   --no-start     Do not start foreground app after setup
#   --service-name Name of systemd unit (default: bullen)
#   --user=...     User to run the systemd service as (default: current user)
#   --backend=...  Audio backend to use: 'jack' (full) or 'dummy' (no JACK, UI-only). Can also set env BULLEN_BACKEND.
#   --no-jack-start  Do not attempt to start a JACK server (use existing or pw-jack wrapper if available)
#   --jack-device     ALSA device for jackd (e.g., hw:0, hw:audioinjector)
#   --jack-rate       Sample rate for jackd (default 48000)
#   --jack-frames     Frames/period for jackd (default 128)
#   --jack-periods    Number of periods for jackd (default 2)

set -Eeuo pipefail

on_error() {
  local line="${1:-?}"
  echo "[ERR] Script failed at line $line"
  if [[ -f /tmp/jackd.log ]]; then
    echo "[jackd.log] tail -n 100:"
    tail -n 100 /tmp/jackd.log | sed 's/^/[jackd] /'
  fi
}
trap 'on_error $LINENO' ERR
trap 'echo "[INT] Interrupted"; exit 130' INT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$PROJ_DIR/.venv"
REQUIREMENTS="$PROJ_DIR/requirements.txt"
REQUIREMENTS_DUMMY="$PROJ_DIR/requirements-dummy.txt"
DEFAULT_CONFIG="$PROJ_DIR/config.yaml"
RECORDINGS_DIR="$PROJ_DIR/recordings"

DO_APT=0
DO_SYSTEMD=0
NO_START=0
SERVICE_NAME="bullen"
SERVICE_USER="$(id -un)"
BULLEN_HOST="${BULLEN_HOST:-0.0.0.0}"
BULLEN_PORT="${BULLEN_PORT:-8000}"
BULLEN_CONFIG="${BULLEN_CONFIG:-$DEFAULT_CONFIG}"
BACKEND="${BULLEN_BACKEND:-jack}"

# JACK configuration defaults (can be overridden via flags or env)
JACK_DEVICE="${JACK_DEVICE:-hw:0}"
JACK_SR="${JACK_SR:-48000}"
JACK_FRAMES="${JACK_FRAMES:-128}"
JACK_PERIODS="${JACK_PERIODS:-2}"
USE_PW_JACK=0
NO_JACK_START=0

for arg in "$@"; do
  case "$arg" in
    --apt) DO_APT=1 ;;
    --systemd) DO_SYSTEMD=1 ;;
    --no-start) NO_START=1 ;;
    --service-name=*) SERVICE_NAME="${arg#*=}" ;;
    --user=*) SERVICE_USER="${arg#*=}" ;;
    --host=*) BULLEN_HOST="${arg#*=}" ;;
    --port=*) BULLEN_PORT="${arg#*=}" ;;
    --config=*) BULLEN_CONFIG="${arg#*=}" ;;
    --backend=*) BACKEND="${arg#*=}" ;;
    --no-jack-start) NO_JACK_START=1 ;;
    --jack-device=*) JACK_DEVICE="${arg#*=}" ;;
    --jack-rate=*) JACK_SR="${arg#*=}" ;;
    --jack-frames=*) JACK_FRAMES="${arg#*=}" ;;
    --jack-periods=*) JACK_PERIODS="${arg#*=}" ;;
    -h|--help)
      sed -n '1,25p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

need_sudo() {
  if [[ $(id -u) -ne 0 ]]; then echo "sudo"; else echo ""; fi
}

install_apt() {
  local SUDO; SUDO=$(need_sudo)
  echo "[APT] Installing OS dependencies..."
  $SUDO apt-get update
  # Install packages individually if available to avoid failing the whole batch
  local pkgs_common=(
    python3 python3-venv python3-pip
    libsndfile1-dev
  )
  local pkgs_jack=(
    libjack-jackd2-dev jackd2 qjackctl
    pipewire-audio-client-libraries libspa-0.2-jack
  )
  local pkgs=("${pkgs_common[@]}")
  if [[ "${BACKEND,,}" == "jack" ]]; then
    pkgs+=("${pkgs_jack[@]}")
  fi
  for p in "${pkgs[@]}"; do
    if apt-cache policy "$p" 2>/dev/null | grep -q 'Candidate:'; then
      $SUDO apt-get install -y --no-install-recommends "$p" || echo "[WARN] [APT] Package $p failed to install; continuing"
    else
      echo "[WARN] [APT] Package $p not available; skipping"
    fi
  done
}

create_venv() {
  if [[ ! -d "$VENV" ]]; then
    echo "[VENV] Creating virtual environment at $VENV"
    python3 -m venv "$VENV"
  fi
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  python -m pip install --upgrade pip setuptools wheel
  local req="$REQUIREMENTS"
  if [[ "${BACKEND,,}" != "jack" && -f "$REQUIREMENTS_DUMMY" ]]; then
    req="$REQUIREMENTS_DUMMY"
  fi
  if [[ -f "$req" ]]; then
    pip install -r "$req"
  else
    echo "[WARN] Requirements file not found: $req"
  fi
}

prep_dirs() {
  mkdir -p "$RECORDINGS_DIR"
}

# ---------- JACK detection and startup (with fallbacks) ----------
command_exists() { command -v "$1" >/dev/null 2>&1; }

log_info() { echo "[INFO] $*"; }
log_warn() { echo "[WARN] $*"; }
log_error() { echo "[ERROR] $*"; }

jack_running() {
  if command_exists jack_lsp && jack_lsp >/dev/null 2>&1; then return 0; fi
  return 1
}

wait_for_jack() {
  local tries=${1:-10} delay=${2:-0.5}
  for _ in $(seq 1 "$tries"); do
    if jack_running; then return 0; fi
    sleep "$delay"
  done
  return 1
}

pick_default_alsa_device() {
  local dev="hw:0"
  if command_exists aplay; then
    local card
    card=$(aplay -l 2>/dev/null | awk '/^card [0-9]+:/{print $2}' | sed 's/,//' | head -n1)
    if [[ -n "$card" ]]; then dev="hw:${card}"; fi
  fi
  echo "$dev"
}

start_jackd_alsa() {
  local dev="$JACK_DEVICE"
  if [[ -z "$dev" || "$dev" == "auto" ]]; then dev="$(pick_default_alsa_device)"; fi
  log_info "Starting jackd (ALSA) on $dev @ ${JACK_SR} Hz, frames ${JACK_FRAMES}, periods ${JACK_PERIODS}"
  ulimit -l unlimited || true
  nohup jackd -R -P95 -d alsa -d "$dev" -r "$JACK_SR" -p "$JACK_FRAMES" -n "$JACK_PERIODS" >/tmp/jackd.log 2>&1 &
  sleep 0.5
  if wait_for_jack 20 0.5; then return 0; fi
  log_warn "jackd ALSA failed to come up; see /tmp/jackd.log"
  return 1
}

start_jackdbus() {
  if command_exists jack_control; then
    log_info "Attempting to start JACK via jackdbus"
    jack_control start || true
    if wait_for_jack 20 0.5; then return 0; fi
  fi
  return 1
}

start_jackd_dummy() {
  log_warn "Starting jackd with dummy backend as last resort"
  nohup jackd -R -P95 -d dummy -r "$JACK_SR" -p "$JACK_FRAMES" -n "$JACK_PERIODS" >/tmp/jackd.log 2>&1 &
  sleep 0.5
  if wait_for_jack 10 0.5; then return 0; fi
  return 1
}

ensure_jack_server() {
  if [[ "$NO_JACK_START" -eq 1 ]]; then
    log_warn "Skipping JACK server startup due to --no-jack-start"
    return 0
  fi
  if jack_running; then
    log_info "JACK server already running"
    return 0
  fi
  # Try ALSA jackd first
  if start_jackd_alsa; then return 0; fi
  # Try jackdbus
  if start_jackdbus; then return 0; fi
  # PipeWire fallback: use pw-jack to wrap client
  if command_exists pw-jack; then
    log_warn "Falling back to PipeWire JACK wrapper (pw-jack). Not starting jackd."
    USE_PW_JACK=1
    return 0
  fi
  # Last resort: dummy backend
  if start_jackd_dummy; then return 0; fi
  log_error "Unable to start or access any JACK server."
  return 1
}

verify_python_jack() {
  log_info "Verifying Python JACK and soundfile modules"
  if ! "$VENV/bin/python3" - <<'PY' >/dev/null 2>&1
import sys
try:
    import jack, soundfile  # type: ignore
except Exception:
    sys.exit(1)
PY
  then
    log_warn "Python JACK or soundfile import failed. The app may not start correctly."
  fi
}

install_systemd() {
  local SUDO; SUDO=$(need_sudo)
  local UNIT_SRC="$PROJ_DIR/systemd/bullen.service"
  local UNIT_TMP
  UNIT_TMP="$(mktemp)"
  if [[ ! -f "$UNIT_SRC" ]]; then
    echo "Missing systemd unit at $UNIT_SRC"; exit 1
  fi
  # Rewrite service paths to match this machine
  sed \
    -e "s|^User=.*|User=$SERVICE_USER|g" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=$PROJ_DIR|g" \
    -e "s|^Environment=BULLEN_CONFIG=.*|Environment=BULLEN_CONFIG=$BULLEN_CONFIG|g" \
    -e "s|^Environment=PATH=.*|Environment=PATH=$VENV/bin:%(PATH)s|g" \
    -e "s|^ExecStart=.*|ExecStart=$VENV/bin/python3 $PROJ_DIR/Bullen.py|g" \
    "$UNIT_SRC" > "$UNIT_TMP"

  # Ensure backend environment is present
  if ! grep -q '^Environment=BULLEN_BACKEND=' "$UNIT_TMP"; then
    printf '\nEnvironment=BULLEN_BACKEND=%s\n' "$BACKEND" >> "$UNIT_TMP"
  else
    sed -i -e "s|^Environment=BULLEN_BACKEND=.*|Environment=BULLEN_BACKEND=$BACKEND|g" "$UNIT_TMP"
  fi

  echo "[SYSTEMD] Installing unit as /etc/systemd/system/${SERVICE_NAME}.service"
  $SUDO install -m 0644 "$UNIT_TMP" "/etc/systemd/system/${SERVICE_NAME}.service"
  rm -f "$UNIT_TMP"
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now "${SERVICE_NAME}.service"
  echo "[SYSTEMD] Service ${SERVICE_NAME}.service enabled and started."
}

if [[ "$DO_APT" -eq 1 ]]; then
  install_apt
fi

create_venv
prep_dirs
if [[ "${BACKEND,,}" == "jack" ]]; then
  verify_python_jack
fi

export BULLEN_CONFIG
export BULLEN_HOST
export BULLEN_PORT
export BULLEN_BACKEND="$BACKEND"

if [[ "${BACKEND,,}" == "jack" ]]; then
  if ! ensure_jack_server; then
    echo "[WARN] [JACK] Proceeding without confirmed JACK server; will try to run anyway."
  fi
fi

if [[ "$DO_SYSTEMD" -eq 1 ]]; then
  install_systemd
fi

if [[ "$NO_START" -eq 1 ]]; then
  echo "[DONE] Environment prepared. Not starting app due to --no-start."
  exit 0
fi

echo "[RUN] Starting app: BULLEN_HOST=$BULLEN_HOST BULLEN_PORT=$BULLEN_PORT BACKEND=${BACKEND}"
if [[ "${BACKEND,,}" == "jack" && "$USE_PW_JACK" -eq 1 ]] && command_exists pw-jack; then
  echo "[RUN] Using pw-jack wrapper"
  exec pw-jack "$VENV/bin/python3" "$PROJ_DIR/Bullen.py"
else
  exec "$VENV/bin/python3" "$PROJ_DIR/Bullen.py"
fi
