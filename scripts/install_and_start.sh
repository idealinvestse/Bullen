#!/usr/bin/env bash
# ==============================================================================
# Bullen Audio Router - Complete Installation & Launch Script
# For Raspberry Pi with Audio Injector Octo (6 in, 8 out)
# ==============================================================================
#
# Usage:
#   ./scripts/install_and_start.sh [OPTIONS]
#
# Common usage:
#   ./scripts/install_and_start.sh --apt --systemd    # Full installation
#   ./scripts/install_and_start.sh                     # Just run the app
#
# Options:
#   --apt            Install OS packages via apt (JACK, Python, etc.)
#   --systemd        Install/enable systemd service for boot startup
#   --fix-all        Fix all known issues (D-Bus, Octo modules, permissions)
#   --host=IP        Override BULLEN_HOST (default: 0.0.0.0)
#   --port=PORT      Override BULLEN_PORT (default: 8000)
#   --config=PATH    Set BULLEN_CONFIG path (default: config.yaml)
#   --no-start       Don't start app after setup
#   --service-name=  Systemd service name (default: bullen)
#   --user=USER      User for systemd service (default: current user)
#   --backend=TYPE   Audio backend: 'jack' or 'dummy' (default: jack)
#   --no-jack-start  Don't start JACK server (use existing)
#   --jack-device=   ALSA device (default: auto-detect Audio Injector)
#   --jack-rate=     Sample rate (default: 48000)
#   --jack-frames=   Frames/period (default: 128)
#   --jack-periods=  Number of periods (default: 2)
#   --help           Show this help message

set -Eeuo pipefail

# ==============================================================================
# Color output for better readability
# ==============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info() { echo -e "${CYAN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_success() { echo -e "${GREEN}[✓]${NC} $*"; }

# ==============================================================================
# Error handling
# ==============================================================================
on_error() {
  local line="${1:-?}"
  log_error "Script failed at line $line"
  if [[ -f /tmp/jackd.log ]]; then
    echo "[jackd.log] Last 50 lines:"
    tail -n 50 /tmp/jackd.log | sed 's/^/  /'
  fi
  if [[ -f /tmp/bullen_install.log ]]; then
    echo "[install.log] Last 50 lines:"
    tail -n 50 /tmp/bullen_install.log | sed 's/^/  /'
  fi
}
trap 'on_error $LINENO' ERR
trap 'log_warn "Interrupted by user"; exit 130' INT

# ==============================================================================
# Paths and configuration
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$PROJ_DIR/.venv"
REQUIREMENTS="$PROJ_DIR/requirements.txt"
REQUIREMENTS_DUMMY="$PROJ_DIR/requirements-dummy.txt"
DEFAULT_CONFIG="$PROJ_DIR/config.yaml"
RECORDINGS_DIR="$PROJ_DIR/recordings"
UPLOADS_DIR="$PROJ_DIR/uploads"
LOG_FILE="/tmp/bullen_install.log"

# ==============================================================================
# Default settings
# ==============================================================================
DO_APT=0
DO_SYSTEMD=0
NO_START=0
FIX_ALL=0
SERVICE_NAME="bullen"
SERVICE_USER="$(id -un)"
BULLEN_HOST="${BULLEN_HOST:-0.0.0.0}"
BULLEN_PORT="${BULLEN_PORT:-8000}"
BULLEN_CONFIG="${BULLEN_CONFIG:-$DEFAULT_CONFIG}"
BACKEND="${BULLEN_BACKEND:-jack}"

# JACK configuration defaults
JACK_DEVICE="${JACK_DEVICE:-auto}"
JACK_SR="${JACK_SR:-48000}"
JACK_FRAMES="${JACK_FRAMES:-128}"
JACK_PERIODS="${JACK_PERIODS:-2}"
USE_PW_JACK=0
NO_JACK_START=0

# ==============================================================================
# Parse command line arguments
# ==============================================================================
for arg in "$@"; do
  case "$arg" in
    --apt) DO_APT=1 ;;
    --systemd) DO_SYSTEMD=1 ;;
    --no-start) NO_START=1 ;;
    --fix-all) FIX_ALL=1 ;;
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
      sed -n '1,30p' "$0"; exit 0 ;;
    *) log_error "Unknown option: $arg"; exit 1 ;;
  esac
done

# ==============================================================================
# System utilities
# ==============================================================================
need_sudo() {
  if [[ $(id -u) -ne 0 ]]; then echo "sudo"; else echo ""; fi
}

command_exists() { 
  command -v "$1" >/dev/null 2>&1
}

# ==============================================================================
# Package installation
# ==============================================================================
install_apt() {
  local SUDO; SUDO=$(need_sudo)
  log_info "Installing OS dependencies..."
  
  # Update package list
  $SUDO apt-get update >> "$LOG_FILE" 2>&1 || log_warn "apt update failed"
  
  # Core packages needed for Bullen
  local pkgs_common=(
    python3 python3-venv python3-pip
    libsndfile1-dev
    dbus dbus-user-session
    git curl wget
  )
  
  # JACK-specific packages
  local pkgs_jack=(
    libjack-jackd2-dev jackd2 qjackctl
    pipewire-audio-client-libraries libspa-0.2-jack
  )
  
  # Audio Injector Octo specific packages
  local pkgs_octo=(
    device-tree-compiler
    i2c-tools
    alsa-utils
    libasound2-dev
  )
  
  local pkgs=("${pkgs_common[@]}")
  if [[ "${BACKEND,,}" == "jack" ]]; then
    pkgs+=("${pkgs_jack[@]}" "${pkgs_octo[@]}")
  fi
  
  # Install packages
  for p in "${pkgs[@]}"; do
    if apt-cache policy "$p" 2>/dev/null | grep -q 'Candidate:'; then
      log_info "Installing $p..."
      $SUDO apt-get install -y --no-install-recommends "$p" >> "$LOG_FILE" 2>&1 || log_warn "Package $p failed to install"
    else
      log_warn "Package $p not available"
    fi
  done
  
  log_success "Package installation complete"
}

# ==============================================================================
# Python environment setup
# ==============================================================================
create_venv() {
  if [[ ! -d "$VENV" ]]; then
    log_info "Creating Python virtual environment..."
    python3 -m venv "$VENV"
  fi
  
  # Activate virtual environment
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  
  # Upgrade pip and install build tools
  log_info "Upgrading pip and installing build tools..."
  python -m pip install --upgrade pip setuptools wheel >> "$LOG_FILE" 2>&1
  
  # Select requirements file based on backend
  local req="$REQUIREMENTS"
  if [[ "${BACKEND,,}" != "jack" && -f "$REQUIREMENTS_DUMMY" ]]; then
    req="$REQUIREMENTS_DUMMY"
    log_info "Using dummy requirements (no JACK)"
  fi
  
  # Install Python dependencies
  if [[ -f "$req" ]]; then
    log_info "Installing Python dependencies from $req..."
    pip install -r "$req" >> "$LOG_FILE" 2>&1 || log_warn "Some packages failed to install"
  else
    log_error "Requirements file not found: $req"
    exit 1
  fi
  
  log_success "Python environment ready"
}

# ==============================================================================
# Directory and file creation
# ==============================================================================
prep_dirs() {
  log_info "Creating necessary directories..."
  
  # Create all required directories
  local dirs=(
    "$RECORDINGS_DIR"
    "$UPLOADS_DIR"
    "$PROJ_DIR/logs"
    "$PROJ_DIR/.config"
  )
  
  for dir in "${dirs[@]}"; do
    if [[ ! -d "$dir" ]]; then
      mkdir -p "$dir"
      log_success "Created $dir"
    fi
  done
  
  # Set proper permissions
  chmod 755 "$RECORDINGS_DIR" "$UPLOADS_DIR"
  
  # Create default config if missing
  if [[ ! -f "$DEFAULT_CONFIG" ]]; then
    log_warn "Config file missing, creating default config.yaml..."
    cat > "$DEFAULT_CONFIG" << 'EOF'
# Bullen Audio Router Configuration
# For Raspberry Pi with Audio Injector Octo

# Audio settings
inputs: 6
outputs: 8
samplerate: 48000
frames_per_period: 128
nperiods: 2

# Initial state
selected_channel: 1
initial_gains_db: [-3.0, -3.0, -3.0, -3.0, -3.0, -3.0]
initial_mutes: [false, false, false, false, false, false]

# Recording
record: false
recordings_dir: "recordings"

# Auto-connect settings (for JACK)
auto_connect_capture: true
auto_connect_playback: true
capture_match: "audioinjector"
playback_match: "audioinjector"

# Advanced features
enable_advanced_features: true

# Noise suppression for call centers
noise_suppression:
  enabled: true
  aggressiveness: 0.7
  enable_cross_channel: true
  vad_enabled: true
  comfort_noise_level: 0.01
  spectral_subtraction_floor: 0.02
  wiener_filter_enabled: true
  adaptive_filter_enabled: true
EOF
    log_success "Created default config.yaml"
  fi
}

# ==============================================================================
# D-Bus fixes for headless operation
# ==============================================================================
fix_dbus_completely() {
  local uid; uid=$(id -u)
  local user; user=$(id -un)
  local SUDO; SUDO=$(need_sudo)
  
  log_info "Fixing D-Bus warnings and issues..."
  
  # Create runtime directory if missing
  if [[ ! -d "/run/user/${uid}" ]]; then
    log_info "Creating runtime directory /run/user/${uid}"
    $SUDO mkdir -p "/run/user/${uid}"
    $SUDO chown "${uid}:${uid}" "/run/user/${uid}"
    $SUDO chmod 700 "/run/user/${uid}"
  fi
  
  # Enable lingering for headless operation
  if command_exists loginctl; then
    log_info "Enabling user lingering for ${user}"
    $SUDO loginctl enable-linger "$user" 2>/dev/null || true
  fi
  
  # Set D-Bus environment to suppress warnings
  export DBUS_SESSION_BUS_ADDRESS="unix:path=/dev/null"
  
  # Add to user's bashrc for permanent fix
  if [[ -f "$HOME/.bashrc" ]] && ! grep -q "DBUS_SESSION_BUS_ADDRESS" "$HOME/.bashrc"; then
    echo 'export DBUS_SESSION_BUS_ADDRESS="unix:path=/dev/null"' >> "$HOME/.bashrc"
    log_success "Added D-Bus fix to ~/.bashrc"
  fi
  
  # Add to system-wide profile for all users
  local profile_d="/etc/profile.d/bullen-dbus-fix.sh"
  if [[ ! -f "$profile_d" ]]; then
    echo '# Fix D-Bus warnings for Bullen Audio Router' | $SUDO tee "$profile_d" > /dev/null
    echo 'export DBUS_SESSION_BUS_ADDRESS="unix:path=/dev/null"' | $SUDO tee -a "$profile_d" > /dev/null
    $SUDO chmod 644 "$profile_d"
    log_success "Created system-wide D-Bus fix"
  fi
  
  # Fix XDG runtime directory
  export XDG_RUNTIME_DIR="/run/user/${uid}"
  
  log_success "D-Bus fixes applied"
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
  ulimit -l unlimited || true
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

# ==============================================================================
# Audio Injector Octo fixes
# ==============================================================================
fix_octo_modules() {
  if [[ "${BACKEND,,}" != "jack" ]]; then return 0; fi
  
  local modprobe_conf="/etc/modprobe.d/audioinjector-octo.conf"
  local SUDO; SUDO=$(need_sudo)
  
  log_info "Checking Audio Injector Octo status..."
  
  # Check if Audio Injector Octo is detected
  if ! lsmod | grep -q "snd_soc_audioinjector_octo"; then
    log_warn "Audio Injector Octo not detected, applying fixes..."
    
    # Create modprobe config for proper module loading order
    if [[ ! -f "$modprobe_conf" ]]; then
      log_info "Creating modprobe configuration for Audio Injector Octo"
      cat << 'EOF' | $SUDO tee "$modprobe_conf" > /dev/null
# Audio Injector Octo module loading configuration
# Ensures proper loading order to avoid codec detection issues
options snd_soc_audioinjector_octo_soundcard index=0
softdep snd_soc_audioinjector_octo_soundcard pre: snd_soc_cs42xx8 snd_soc_cs42xx8_i2c
EOF
      log_success "Created $modprobe_conf"
    fi
    
    # Manual module loading sequence (fixes forum-reported issues)
    log_info "Reloading Audio Injector Octo modules in correct order..."
    
    # First, unload all related modules
    local modules_to_unload=(
      "snd_soc_audioinjector_octo_soundcard"
      "snd_soc_cs42xx8_i2c"
      "snd_soc_cs42xx8"
    )
    
    for mod in "${modules_to_unload[@]}"; do
      if lsmod | grep -q "$mod"; then
        log_info "Unloading $mod..."
        $SUDO modprobe -r "$mod" 2>/dev/null || true
      fi
    done
    
    # Wait for modules to fully unload
    sleep 2
    
    # Load modules in correct order
    log_info "Loading modules in correct order..."
    $SUDO modprobe snd_soc_cs42xx8 || log_warn "Failed to load snd_soc_cs42xx8"
    $SUDO modprobe snd_soc_cs42xx8_i2c || log_warn "Failed to load snd_soc_cs42xx8_i2c"
    $SUDO modprobe snd_soc_audioinjector_octo_soundcard || log_warn "Failed to load snd_soc_audioinjector_octo_soundcard"
    
    # Wait for card to initialize
    sleep 3
    
    # Verify modules loaded
    if lsmod | grep -q "snd_soc_audioinjector_octo"; then
      log_success "Audio Injector Octo modules loaded successfully"
      
      # Check if card is detected by ALSA
      if aplay -l 2>/dev/null | grep -qi "audioinjector"; then
        log_success "Audio Injector Octo card detected by ALSA"
      else
        log_warn "Modules loaded but card not detected by ALSA - may need reboot"
      fi
    else
      log_error "Audio Injector Octo modules failed to load"
      log_warn "You may need to reboot or check hardware connections"
    fi
  else
    log_success "Audio Injector Octo modules already loaded"
  fi
  
  # Additional I2C fix for codec detection issues
  if command_exists i2cdetect; then
    log_info "Checking I2C devices for CS42448 codec..."
    $SUDO i2cdetect -y 1 2>/dev/null | grep -q "48" && log_success "CS42448 codec detected on I2C bus"
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

# ==============================================================================
# Systemd service installation
# ==============================================================================
install_systemd() {
  local SUDO; SUDO=$(need_sudo)
  local UNIT_SRC="$PROJ_DIR/systemd/bullen.service"
  local UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"
  
  log_info "Installing systemd service..."
  
  # Create systemd service file if it doesn't exist
  if [[ ! -f "$UNIT_SRC" ]]; then
    log_warn "Creating systemd service file..."
    mkdir -p "$PROJ_DIR/systemd"
    cat > "$UNIT_SRC" << 'EOF'
[Unit]
Description=Bullen Audio Router - Professional Audio Routing System
After=network.target sound.target

[Service]
Type=simple
User=pi
Group=audio
WorkingDirectory=/home/pi/Bullen
Environment="PATH=/home/pi/Bullen/.venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="BULLEN_CONFIG=/home/pi/Bullen/config.yaml"
Environment="BULLEN_HOST=0.0.0.0"
Environment="BULLEN_PORT=8000"
Environment="BULLEN_BACKEND=jack"
Environment="DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null"
ExecStartPre=/bin/sleep 5
ExecStart=/home/pi/Bullen/.venv/bin/python3 /home/pi/Bullen/Bullen.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    log_success "Created default systemd service file"
  fi
  
  # Create customized service file
  local UNIT_TMP
  UNIT_TMP="$(mktemp)"
  
  # Update service file with current configuration
  sed \
    -e "s|^User=.*|User=$SERVICE_USER|g" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=$PROJ_DIR|g" \
    -e "s|^Environment=\"BULLEN_CONFIG=.*\"|Environment=\"BULLEN_CONFIG=$BULLEN_CONFIG\"|g" \
    -e "s|^Environment=\"BULLEN_HOST=.*\"|Environment=\"BULLEN_HOST=$BULLEN_HOST\"|g" \
    -e "s|^Environment=\"BULLEN_PORT=.*\"|Environment=\"BULLEN_PORT=$BULLEN_PORT\"|g" \
    -e "s|^Environment=\"BULLEN_BACKEND=.*\"|Environment=\"BULLEN_BACKEND=$BACKEND\"|g" \
    -e "s|^Environment=\"PATH=.*\"|Environment=\"PATH=$VENV/bin:/usr/local/bin:/usr/bin:/bin\"|g" \
    -e "s|^ExecStart=.*|ExecStart=$VENV/bin/python3 $PROJ_DIR/Bullen.py|g" \
    "$UNIT_SRC" > "$UNIT_TMP"
  
  # Ensure D-Bus fix is in the service
  if ! grep -q 'DBUS_SESSION_BUS_ADDRESS' "$UNIT_TMP"; then
    sed -i '/\[Service\]/a Environment="DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null"' "$UNIT_TMP"
  fi
  
  # Install the service
  log_info "Installing service to $UNIT_DST..."
  $SUDO install -m 0644 "$UNIT_TMP" "$UNIT_DST"
  rm -f "$UNIT_TMP"
  
  # Reload systemd and enable service
  log_info "Enabling and starting service..."
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable "${SERVICE_NAME}.service"
  $SUDO systemctl restart "${SERVICE_NAME}.service"
  
  # Check service status
  sleep 2
  if $SUDO systemctl is-active --quiet "${SERVICE_NAME}.service"; then
    log_success "Service ${SERVICE_NAME}.service is running"
  else
    log_warn "Service may not be running, check: sudo systemctl status ${SERVICE_NAME}"
  fi
}

# ==============================================================================
# Main execution flow
# ==============================================================================
main() {
  # Start logging
  echo "========================================" >> "$LOG_FILE"
  echo "Bullen Installation started at $(date)" >> "$LOG_FILE"
  echo "========================================" >> "$LOG_FILE"
  
  # Display banner
  echo -e "${CYAN}"
  echo "╔══════════════════════════════════════════════╗"
  echo "║     Bullen Audio Router Installation        ║"
  echo "║     For Raspberry Pi + Audio Injector Octo  ║"
  echo "╚══════════════════════════════════════════════╝"
  echo -e "${NC}"
  
  # System checks
  log_info "Running on: $(uname -a)"
  log_info "User: $(whoami) (uid=$(id -u))"
  
  # Apply all fixes if requested
  if [[ "$FIX_ALL" -eq 1 ]]; then
    log_info "Applying all known fixes..."
    fix_dbus_completely
    fix_octo_modules
  fi
  
  # Install OS packages if requested
  if [[ "$DO_APT" -eq 1 ]]; then
    install_apt
  fi
  
  # Setup Python environment
  create_venv
  
  # Create necessary directories and files
  prep_dirs
  
  # Fix D-Bus issues
  fix_dbus_completely
  
  # Verify Python modules for JACK backend
  if [[ "${BACKEND,,}" == "jack" ]]; then
    verify_python_jack
    fix_octo_modules
  fi
  
  # Export environment variables
  export BULLEN_CONFIG
  export BULLEN_HOST
  export BULLEN_PORT
  export BULLEN_BACKEND="$BACKEND"
  export DBUS_SESSION_BUS_ADDRESS="unix:path=/dev/null"
  
  # Ensure JACK server is running (if using JACK backend)
  if [[ "${BACKEND,,}" == "jack" ]]; then
    if ! ensure_jack_server; then
      log_warn "JACK server not confirmed, attempting to proceed anyway..."
    fi
  fi
  
  # Install systemd service if requested
  if [[ "$DO_SYSTEMD" -eq 1 ]]; then
    install_systemd
  fi
  
  # Exit if no-start flag is set
  if [[ "$NO_START" -eq 1 ]]; then
    log_success "Environment prepared successfully!"
    log_info "Start manually with: $VENV/bin/python3 $PROJ_DIR/Bullen.py"
    exit 0
  fi
  
  # Start the application
  echo ""
  log_success "Starting Bullen Audio Router..."
  log_info "Host: $BULLEN_HOST"
  log_info "Port: $BULLEN_PORT"
  log_info "Backend: $BACKEND"
  log_info "Config: $BULLEN_CONFIG"
  echo ""
  echo -e "${GREEN}════════════════════════════════════════${NC}"
  echo -e "${GREEN}Access the UI at: http://$BULLEN_HOST:$BULLEN_PORT${NC}"
  echo -e "${GREEN}Optimized for 9\" touchscreen (1024x600)${NC}"
  echo -e "${GREEN}════════════════════════════════════════${NC}"
  echo ""
  
  # Run the application
  if [[ "${BACKEND,,}" == "jack" && "$USE_PW_JACK" -eq 1 ]] && command_exists pw-jack; then
    log_info "Using PipeWire JACK wrapper (pw-jack)"
    exec pw-jack "$VENV/bin/python3" "$PROJ_DIR/Bullen.py"
  else
    exec "$VENV/bin/python3" "$PROJ_DIR/Bullen.py"
  fi
}

# Run main function
main "$@"
