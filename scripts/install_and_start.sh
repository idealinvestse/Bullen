#!/usr/bin/env bash
# Bullen installer & launcher for Raspberry Pi (Debian-based)
#
# Usage:
#   ./scripts/install_and_start.sh [--apt] [--systemd] [--host=0.0.0.0] [--port=8000] [--config=path]
#                                  [--no-start] [--service-name=bullen] [--user=pi]
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

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$PROJ_DIR/.venv"
REQUIREMENTS="$PROJ_DIR/requirements.txt"
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
  $SUDO apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    libjack-jackd2-dev libsndfile1-dev \
    jackd2 qjackctl
}

create_venv() {
  if [[ ! -d "$VENV" ]]; then
    echo "[VENV] Creating virtual environment at $VENV"
    python3 -m venv "$VENV"
  fi
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  python -m pip install --upgrade pip setuptools wheel
  if [[ -f "$REQUIREMENTS" ]]; then
    pip install -r "$REQUIREMENTS"
  fi
}

prep_dirs() {
  mkdir -p "$RECORDINGS_DIR"
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

export BULLEN_CONFIG
export BULLEN_HOST
export BULLEN_PORT

if [[ "$DO_SYSTEMD" -eq 1 ]]; then
  install_systemd
fi

if [[ "$NO_START" -eq 1 ]]; then
  echo "[DONE] Environment prepared. Not starting app due to --no-start."
  exit 0
fi

echo "[RUN] Starting app: BULLEN_HOST=$BULLEN_HOST BULLEN_PORT=$BULLEN_PORT"
exec "$VENV/bin/python3" "$PROJ_DIR/Bullen.py"
