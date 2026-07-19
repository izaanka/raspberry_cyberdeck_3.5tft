#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install.sh — Cyberdeck Terminal Installer
#
# NO LCD kernel driver needed. Drives the ILI9486 display directly via SPI.
# HDMI output is completely unaffected.
#
# Usage:  sudo ./install.sh
#
# Prerequisites:
#   • Raspberry Pi OS (Desktop, Bookworm recommended)
#   • SPI interface enabled (this script does it automatically)
#   • Internet connection (for pip packages and optional font download)
# ─────────────────────────────────────────────────────────────────────────────
set -e

INSTALL_DIR="/opt/cyberdeck-term"
CONFIG_DIR="/etc/cyberdeck-term"
SERVICE_NAME="cyberdeck-term"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
die()     { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Run with sudo: sudo ./install.sh"

echo -e "\n${BOLD}${CYAN}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${CYAN}║   Cyberdeck Terminal — Installer          ║${RESET}"
echo -e "${BOLD}${CYAN}║   SPI direct-drive  •  HDMI untouched     ║${RESET}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════╝${RESET}\n"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Step 1: Enable SPI interface ─────────────────────────────────────────────
info "Enabling SPI interface…"

# raspi-config non-interactive (works on Bullseye + Bookworm)
if command -v raspi-config &>/dev/null; then
    raspi-config nonint do_spi 0
    success "SPI enabled via raspi-config"
else
    # Fallback: add to /boot/firmware/config.txt (Bookworm path)
    BOOT_CFG="/boot/firmware/config.txt"
    [[ -f "$BOOT_CFG" ]] || BOOT_CFG="/boot/config.txt"
    if ! grep -q "^dtparam=spi=on" "$BOOT_CFG"; then
        echo "dtparam=spi=on" >> "$BOOT_CFG"
        success "dtparam=spi=on added to $BOOT_CFG (takes effect after reboot)"
        warn "Reboot required for SPI to activate — run install.sh again after reboot"
    else
        success "SPI already enabled in $BOOT_CFG"
    fi
fi

# ── Step 2: Python dependencies ───────────────────────────────────────────────
info "Installing Python dependencies…"
# --break-system-packages needed on Bookworm (PEP 668)
pip3 install --quiet --break-system-packages \
    "luma.lcd" \
    pygame \
    pyte \
    evdev \
    "RPi.GPIO" \
    psutil \
    Pillow 2>&1 | grep -Ev "^(Requirement|$)" || true
success "Python packages ready"

# ── Step 3: System fonts ──────────────────────────────────────────────────────
info "Checking for DejaVu monospace font…"
if ! dpkg -l fonts-dejavu-core &>/dev/null 2>&1; then
    apt-get install -y -q fonts-dejavu-core
fi
success "DejaVu font available"

# ── Step 4: Create directories and copy files ─────────────────────────────────
info "Installing to ${INSTALL_DIR}…"
mkdir -p "${INSTALL_DIR}/fonts"
mkdir -p "${CONFIG_DIR}"

# Link system fonts into install dir
SYS_NORMAL="/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
SYS_BOLD="/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
[[ -f "$SYS_NORMAL" ]] && ln -sf "$SYS_NORMAL" "${INSTALL_DIR}/fonts/DejaVuSansMono.ttf"
[[ -f "$SYS_BOLD"   ]] && ln -sf "$SYS_BOLD"   "${INSTALL_DIR}/fonts/DejaVuSansMono-Bold.ttf"

cp "${SCRIPT_DIR}/cyberdeck-term.py" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/sysinfo.py"        "${INSTALL_DIR}/"
chmod 755 "${INSTALL_DIR}/cyberdeck-term.py"
chmod 755 "${INSTALL_DIR}/sysinfo.py"
success "Program files installed"

# ── Step 5: Config (preserve existing) ───────────────────────────────────────
CONFIG_DEST="${CONFIG_DIR}/config.ini"
if [[ -f "${CONFIG_DEST}" ]]; then
    warn "Config exists at ${CONFIG_DEST} — not overwritten"
    warn "To reset:  sudo cp ${SCRIPT_DIR}/config.ini ${CONFIG_DEST}"
else
    cp "${SCRIPT_DIR}/config.ini" "${CONFIG_DEST}"
    success "Config installed at ${CONFIG_DEST}"
fi

# ── Step 6: Systemd service ───────────────────────────────────────────────────
info "Installing systemd service…"
cp "${SCRIPT_DIR}/cyberdeck-term.service" \
   "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
success "Service enabled (starts on every boot)"

# ── Step 7: Convenience wrapper ───────────────────────────────────────────────
cat > /usr/local/bin/cyberdeck-term <<'WRAPPER'
#!/usr/bin/env bash
case "$1" in
    start)   sudo systemctl start   cyberdeck-term ;;
    stop)    sudo systemctl stop    cyberdeck-term ;;
    restart) sudo systemctl restart cyberdeck-term ;;
    status)  sudo systemctl status  cyberdeck-term ;;
    log)     sudo journalctl -u cyberdeck-term -f ;;
    edit)    sudo nano /etc/cyberdeck-term/config.ini ;;
    *)
        echo "Cyberdeck Terminal — control script"
        echo ""
        echo "  cyberdeck-term start|stop|restart|status|log"
        echo "  cyberdeck-term edit        # open config in nano"
        echo ""
        echo "  Config:  /etc/cyberdeck-term/config.ini"
        echo "  Rotate:  edit config → set rotation = 0|90|180|270 → restart"
        ;;
esac
WRAPPER
chmod 755 /usr/local/bin/cyberdeck-term
success "Command 'cyberdeck-term' available system-wide"

# ── Step 8: SPI device check ──────────────────────────────────────────────────
if ls /dev/spidev0.* &>/dev/null 2>&1; then
    success "/dev/spidev0.0 found — SPI is active now"
    NEED_REBOOT=false
else
    warn "SPI device not yet visible — a reboot is needed"
    warn "After reboot, run:  sudo systemctl start ${SERVICE_NAME}"
    NEED_REBOOT=true
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}Installation complete!${RESET}"
echo ""
echo -e "  ${CYAN}Edit config:${RESET}  cyberdeck-term edit"
echo -e "  ${CYAN}Start now:${RESET}    cyberdeck-term start"
echo -e "  ${CYAN}View logs:${RESET}    cyberdeck-term log"
echo ""
echo -e "  ${YELLOW}Orientation:${RESET}  edit config → rotation = 0|90|180|270 → restart"
echo -e "  ${YELLOW}Font size:${RESET}   edit config → font_size = 11-16 → restart"
echo ""
if [[ "$NEED_REBOOT" == "true" ]]; then
    echo -e "  ${RED}⚠  Reboot required for SPI to activate:  sudo reboot${RESET}"
    echo ""
fi
