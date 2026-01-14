#!/bin/bash
#
# Installs screen-brightnessd (DPMS-aware GPIO button emulator) and its systemd service.
#
# - Must NOT be run as root
# - Requires KlipperScreen to be installed (systemd unit must exist)

set -euo pipefail

SERVICE_BASENAME="screen-brightnessd"
SERVICE_NAME="${SERVICE_BASENAME}.service"
CONFIG_NAME="${SERVICE_BASENAME}.ini"

log() { echo "[install] $*"; }
warn() { echo "[install][WARN] $*" >&2; }
die() { echo "[install][ERROR] $*" >&2; exit 1; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"; }

append_if_missing() {
  local file="$1"
  local needle="$2"
  local content="$3"

  if sudo grep -Fqs "$needle" "$file"; then
    log "Already present in $file: $needle"
  else
    log "Appending to $file: $needle"
    echo "$content" | tee -a "$file" >/dev/null
  fi
}

# ----------------------------
# Preconditions
# ----------------------------

if [[ "${EUID}" -eq 0 ]]; then
  die "Do not run this script as root. Run as your normal user."
fi

require_cmd readlink
require_cmd dirname
require_cmd sed
require_cmd systemctl

SCRIPT_PATH="$(readlink -f "$0")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"
PROJECT_DIR="$(readlink -f "$SCRIPT_DIR/..")"
MAIN_PY="${PROJECT_DIR}/main.py"

[[ -f "$MAIN_PY" ]] || die "Expected main.py at: $MAIN_PY (project dir: $PROJECT_DIR)"

EFFECTIVE_USER="${SUDO_USER:-$USER}"

log "Using user:    ${EFFECTIVE_USER}"
log "Project dir:   ${PROJECT_DIR}"
log "Installer dir: ${SCRIPT_DIR}"

SERVICE_TEMPLATE="${SCRIPT_DIR}/${SERVICE_NAME}"
CONFIG_TEMPLATE="${SCRIPT_DIR}/config.ini"
MOONRAKER_UPDATE_SNIPPET="${SCRIPT_DIR}/moonraker-updateconfig.txt"

[[ -f "$SERVICE_TEMPLATE" ]] || die "Missing service template: $SERVICE_TEMPLATE"
[[ -f "$CONFIG_TEMPLATE" ]] || die "Missing config template:   $CONFIG_TEMPLATE"
[[ -f "$MOONRAKER_UPDATE_SNIPPET" ]] || die "Missing Moonraker snippet: $MOONRAKER_UPDATE_SNIPPET"

# Require KlipperScreen service
if ! systemctl is-enabled KlipperScreen.service > /dev/null; then
  die "KlipperScreen.service not enabled. This installer requires KlipperScreen to be installed."
fi

# Verify expected Klipper/Moonraker paths exist (do NOT create them)
PRINTER_DATA_DIR="/home/${EFFECTIVE_USER}/printer_data"
TARGET_CONFIG_DIR="${PRINTER_DATA_DIR}/config"
TARGET_CONFIG_PATH="${TARGET_CONFIG_DIR}/${CONFIG_NAME}"
MOONRAKER_CONF="${TARGET_CONFIG_DIR}/moonraker.conf"
MOONRAKER_ASVC="${PRINTER_DATA_DIR}/moonraker.asvc"

[[ -d "${PRINTER_DATA_DIR}" ]] || die "Missing directory: ${PRINTER_DATA_DIR} (not creating it; unexpected setup?)"
[[ -d "${TARGET_CONFIG_DIR}" ]] || die "Missing directory: ${TARGET_CONFIG_DIR} (not creating it; unexpected setup?)"
[[ -f "${MOONRAKER_CONF}" ]] || warn "moonraker.conf not found at ${MOONRAKER_CONF} (will skip update snippet append)"
[[ -f "${MOONRAKER_ASVC}" ]] || warn "moonraker.asvc not found at ${MOONRAKER_ASVC} (will skip service registration)"

TARGET_SYSTEMD_SERVICE="/etc/systemd/system/${SERVICE_NAME}"

# ----------------------------
# Install dependencies + permissions
# ----------------------------

log "Installing apt dependencies..."
sudo apt update
sudo apt install -y python3 python3-libgpiod x11-xserver-utils

log "Ensuring dialout group exists..."
sudo groupadd -r -f dialout

log "Installing udev rules for GPIO access (dialout)..."
cat <<'EOF' | sudo tee /etc/udev/rules.d/99-gpio.rules >/dev/null
KERNEL=="gpiochip*", SUBSYSTEM=="gpio", MODE="0660", GROUP="dialout"
SUBSYSTEM=="gpio*", ACTION=="add", RUN+="/bin/chgrp -R dialout $sys$devpath", RUN+="/bin/chmod -R g+rw $sys$devpath"
EOF

log "Adding user '${EFFECTIVE_USER}' to dialout group..."
sudo usermod -a -G dialout "${EFFECTIVE_USER}"

# ----------------------------
# Copy default config (do not overwrite)
# ----------------------------

if [[ -f "${TARGET_CONFIG_PATH}" ]]; then
  log "Config already exists, not overwriting: ${TARGET_CONFIG_PATH}"
else
  log "Installing default config: ${TARGET_CONFIG_PATH}"
  cp "${CONFIG_TEMPLATE}" "${TARGET_CONFIG_PATH}"
fi

# ----------------------------
# Install systemd service (with substitutions)
# ----------------------------

log "Installing systemd unit: ${TARGET_SYSTEMD_SERVICE}"
tmp_unit="$(mktemp)"

sed \
  -e "s|%USER%|${EFFECTIVE_USER}|g" \
  -e "s|%DIRECTORY%|${PROJECT_DIR}|g" \
  "${SERVICE_TEMPLATE}" > "${tmp_unit}"

sudo cp "${tmp_unit}" "${TARGET_SYSTEMD_SERVICE}"
rm -f "${tmp_unit}"

log "Reloading systemd daemon..."
sudo systemctl daemon-reload

log "Enabling ${SERVICE_NAME} (will not start it now; reboot required)."
sudo systemctl enable "${SERVICE_NAME}"

# ----------------------------
# Moonraker update config append (if present)
# ----------------------------

if [[ -f "${MOONRAKER_CONF}" ]]; then
  tmp_mr_cfg="$(mktemp)"
  sed \
    -e "s|%USER%|${EFFECTIVE_USER}|g" \
    -e "s|%DIRECTORY%|${PROJECT_DIR}|g" \
    "${MOONRAKER_UPDATE_SNIPPET}" > "${tmp_mr_cfg}"
  SNIPPET_CONTENT="$(cat "${tmp_mr_cfg}")"
  rm -f "${tmp_mr_cfg}"
  append_if_missing "${MOONRAKER_CONF}" "${SERVICE_BASENAME}" "${SNIPPET_CONTENT}"
else
  warn "Skipping Moonraker update snippet append (moonraker.conf missing)."
fi

# ----------------------------
# Add service to moonraker.asvc (if present)
# ----------------------------

if [[ -f "${MOONRAKER_ASVC}" ]]; then
  append_if_missing "${MOONRAKER_ASVC}" "${SERVICE_BASENAME}" "${SERVICE_BASENAME}"
else
  warn "Skipping moonraker.asvc registration (file missing)."
fi

# ----------------------------
# Final notes
# ----------------------------

echo ""
log "Installation completed."
log "IMPORTANT: A reboot is required for GPIO access to work reliably (group membership + udev rules)."
log "After reboot, check service status with:"
log "  systemctl status ${SERVICE_NAME}"
log "  journalctl -u ${SERVICE_NAME} -f"
