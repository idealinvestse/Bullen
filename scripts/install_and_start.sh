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
REQUIREMENTS_DEV="$PROJ_DIR/requirements-dev.txt"
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
JACK_DEVICE="${JACK_DEVICE:-auto}"
JACK_SR="${JACK_SR:-48000}"
JACK_FRAMES="${JACK_FRAMES:-128}"
JACK_PERIODS="${JACK_PERIODS:-2}"
USE_PW_JACK=0
NO_JACK_START=0
FIX_DBUS=0

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
    --fix-dbus) FIX_DBUS=1 ;;
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
    dbus dbus-user-session
  )
  local pkgs_jack=(
    libjack-jackd2-dev jackd2 qjackctl
    pipewire-audio-client-libraries libspa-0.2-jack
  )
  local pkgs=("${pkgs_common[@]}")
  if [[ "${BACKEND,,}" == "jack" ]]; then
    pkgs+=("${pkgs_jack[@]}")
    # Add Audio Injector Octo specific packages for reliable detection
    pkgs+=("device-tree-compiler" "i2c-tools")
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
  # Choose base requirements based on backend (jack vs dummy)
  local base_req="$REQUIREMENTS"
  if [[ "${BACKEND,,}" != "jack" && -f "$REQUIREMENTS_DUMMY" ]]; then
    base_req="$REQUIREMENTS_DUMMY"
  fi
  if [[ -f "$base_req" ]]; then
    echo "[PIP] Installing base requirements from $base_req"
    pip install -r "$base_req"
  else
    echo "[WARN] Requirements file not found: $base_req"
  fi
  # Always install dev requirements if present (tests, tooling)
  if [[ -f "$REQUIREMENTS_DEV" ]]; then
    echo "[PIP] Installing development requirements from $REQUIREMENTS_DEV"
    pip install -r "$REQUIREMENTS_DEV"
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

# Detect if a per-user D-Bus session is available and usable
has_user_dbus() {
  local uid; uid=$(id -u)
  local rundir="${XDG_RUNTIME_DIR:-/run/user/$uid}"
  [[ -S "$rundir/bus" ]]
}

# Ensure DBUS_SESSION_BUS_ADDRESS points to the user bus if it exists
ensure_user_dbus_env() {
  if ! has_user_dbus; then
    return 1
  fi
  if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
    local uid; uid=$(id -u)
    local rundir="${XDG_RUNTIME_DIR:-/run/user/$uid}"
    export DBUS_SESSION_BUS_ADDRESS="unix:path=${rundir}/bus"
    log_info "Exported DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS}"
  fi
  return 0
}

# Check and report system and user D-Bus availability
check_dbus_status() {
  local uid; uid=$(id -u)
  local ok_sys=0 ok_user=0
  if [[ -S /run/dbus/system_bus_socket ]]; then
    ok_sys=1; log_info "System D-Bus: OK (/run/dbus/system_bus_socket)"
  else
    log_warn "System D-Bus: MISSING (/run/dbus/system_bus_socket not found)"
  fi
  if has_user_dbus; then
    ok_user=1; log_info "User D-Bus: OK (/run/user/${uid}/bus)"
  else
    log_warn "User D-Bus: MISSING (/run/user/${uid}/bus not found)"
  fi
  # Try exporting session bus env if user bus exists
  ensure_user_dbus_env || true
  return $(( ok_sys==1 && ok_user==1 ? 0 : 1 ))
}

# Attempt to repair user D-Bus in headless setups
repair_user_dbus() {
  local uid; uid=$(id -u)
  local user; user=$(id -un)
  local SUDO; SUDO=$(need_sudo)
  log_info "Attempting to repair user D-Bus for ${user} (uid ${uid})"
  # Ensure runtime dir exists
  if [[ ! -d "/run/user/${uid}" ]]; then
    log_info "Enabling lingering for ${user} to allow user manager at boot"
    $SUDO loginctl enable-linger "$user" || log_warn "loginctl enable-linger failed"
  fi
  # Try to start user services which also establish user D-Bus on modern systems
  if command_exists systemctl; then
    # Export XDG_RUNTIME_DIR if not set but path exists
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${uid}}"
    if [[ -d "$XDG_RUNTIME_DIR" ]]; then
      log_info "Using XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
    fi
    # Try to start PipeWire stack (often depends on user D-Bus)
    systemctl --user enable --now pipewire.service 2>/dev/null || true
    systemctl --user enable --now wireplumber.service 2>/dev/null || true
  fi
  # Re-check status after attempts
  check_dbus_status || log_warn "User D-Bus still not available; you may need to relogin/reboot or install dbus-user-session"
}

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
    # Prefer Audio Injector Octo (or similar) if present; otherwise use first card index
    local preferred
    preferred=$(aplay -l 2>/dev/null | awk '/^card [0-9]+:/{print tolower($0)}' | grep -m1 -E 'audioinjector|octo|cs42|cs42448' || true)
    if [[ -n "$preferred" ]]; then
      # Extract the numeric card index after "card " and before ':'
      local idx
      idx=$(printf '%s\n' "$preferred" | sed -n 's/^card \([0-9][0-9]*\):.*/\1/p' | head -n1)
      if [[ -n "$idx" ]]; then
        dev="hw:${idx}"
      fi
    fi
    if [[ "$dev" == "hw:0" ]]; then
      # Fallback: first card index from aplay -l
      local first
      first=$(aplay -l 2>/dev/null | sed -n 's/^card \([0-9][0-9]*\):.*/\1/p' | head -n1)
      if [[ -n "$first" ]]; then
        dev="hw:${first}"
      fi
    fi
  fi
  echo "$dev"
}

start_jackd_alsa() {
  local dev="$JACK_DEVICE"
  if [[ -z "$dev" || "$dev" == "auto" ]]; then dev="$(pick_default_alsa_device)"; fi
  log_info "Starting jackd (ALSA) on $dev @ ${JACK_SR} Hz, frames ${JACK_FRAMES}, periods ${JACK_PERIODS}"
  # Try to set memory lock limits for better real-time performance, but continue if not permitted
  if ! ulimit -l unlimited 2>/dev/null; then
    log_warn "Unable to set unlimited locked memory. This may affect real-time audio performance."
    log_warn "To fix this, either run with sudo privileges or modify /etc/security/limits.conf"
  fi
  nohup jackd -R -P95 -d alsa -d "$dev" -r "$JACK_SR" -p "$JACK_FRAMES" -n "$JACK_PERIODS" >/tmp/jackd.log 2>&1 &
  sleep 0.5
  if wait_for_jack 20 0.5; then return 0; fi
  log_warn "jackd ALSA failed to come up; see /tmp/jackd.log"
  return 1
}

start_jackdbus() {
  if command_exists jack_control; then
    # Require a valid per-user D-Bus; otherwise jack_control will fail (X11/autolaunch or no /run/user/$UID/bus)
    if ! ensure_user_dbus_env; then
      log_warn "Skipping jackdbus start: user D-Bus not available (no /run/user/$(id -u)/bus)"
      return 1
    fi
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

# Check and fix Audio Injector Octo module loading issues
fix_octo_modules() {
  if [[ "${BACKEND,,}" != "jack" ]]; then return 0; fi
  
  local modprobe_conf="/etc/modprobe.d/audioinjector-octo.conf"
  local SUDO; SUDO=$(need_sudo)
  
  # Check if Audio Injector Octo is detected
  if ! lsmod | grep -q "snd_soc_audioinjector_octo"; then
    log_info "Audio Injector Octo not detected, checking module dependencies"
    
    # Create modprobe config to ensure proper module loading order
    if [[ ! -f "$modprobe_conf" ]]; then
      log_info "Creating Audio Injector Octo modprobe configuration"
      echo "softdep snd_soc_audioinjector_octo_soundcard pre: snd_soc_cs42xx8 snd_soc_cs42xx8_i2c" | $SUDO tee "$modprobe_conf" >/dev/null
    fi
    
    # Try manual module loading sequence (common fix from forum)
    log_info "Attempting manual module loading sequence"
    $SUDO modprobe -r snd_soc_audioinjector_octo_soundcard 2>/dev/null || true
    $SUDO modprobe -r snd_soc_cs42xx8_i2c 2>/dev/null || true
    $SUDO modprobe -r snd_soc_cs42xx8 2>/dev/null || true
    sleep 1
    $SUDO modprobe snd_soc_cs42xx8
    $SUDO modprobe snd_soc_cs42xx8_i2c
    $SUDO modprobe snd_soc_audioinjector_octo_soundcard
    sleep 2
    
    # Check if modules loaded successfully
    if lsmod | grep -q "snd_soc_audioinjector_octo"; then
      log_info "Audio Injector Octo modules loaded successfully"
    else
      log_warn "Audio Injector Octo modules failed to load - card may not be detected"
    fi
  fi
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
  
  # Fix Audio Injector Octo module issues before starting JACK
  fix_octo_modules
  
  # Try ALSA jackd first
  if start_jackd_alsa; then return 0; fi
  # Prefer PipeWire wrapper before jackdbus to avoid D-Bus/X11 issues on headless
  if command_exists pw-jack; then
    log_warn "Using PipeWire JACK wrapper (pw-jack). Skipping jackd/jackdbus."
    USE_PW_JACK=1
    return 0
  fi
  # Try jackdbus last (only works with a user D-Bus session)
  if start_jackdbus; then return 0; fi
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
check_dbus_status || true
if [[ "$FIX_DBUS" -eq 1 ]]; then
  repair_user_dbus
fi
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
