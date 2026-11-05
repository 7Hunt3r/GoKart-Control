#!/bin/bash
# Install / update the GoKart pit station ON THE PI (Raspberry Pi OS Trixie Lite).
#
#   sudo bash install.sh [user]        # default user: pit
#
# Idempotent: safe to re-run after every update. Run from /opt/gokart-pit (first
# boot does this) or from any copy of pit/ (it is rsynced into /opt/gokart-pit,
# keeping server/config.json and logs/). UNTESTED ON HARDWARE.
set -uo pipefail
[ "$(id -u)" -eq 0 ] || exec sudo bash "$0" "$@"

PIT_USER="${1:-pit}"
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/gokart-pit
WARN=0
warn() { echo "WARN: $*"; WARN=1; }
id "$PIT_USER" >/dev/null 2>&1 || { echo "user $PIT_USER does not exist"; exit 1; }
echo "=== gokart-pit install $(date) user=$PIT_USER src=$SRC"

echo "--- packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update || warn "apt-get update failed (no network?)"
apt-get install -y --no-install-recommends python3 python3-serial avahi-daemon curl rsync \
  dbus libpam-systemd fonts-dejavu-core || warn "base packages failed"
# Kiosk: cage (wlroots kiosk compositor) + Chromium + Mesa GL for the Pi 4 V3D GPU.
apt-get install -y cage libgl1-mesa-dri || warn "cage failed"
apt-get install -y chromium || apt-get install -y chromium-browser || warn "chromium failed"

echo "--- app files -> $DEST"
mkdir -p "$DEST"
if [ "$SRC" != "$DEST" ]; then
  rsync -a --delete \
    --exclude 'logs/' --exclude 'server/config.json' --exclude 'server/map/' \
    --exclude 'sdcard/secrets.env' --exclude 'sdcard/build/' --exclude '__pycache__/' \
    "$SRC/" "$DEST/"
  # map/ holds operator-added imagery: add/refresh files, never delete
  rsync -a "$SRC/server/map/" "$DEST/server/map/"
fi
mkdir -p "$DEST/logs"
if [ ! -f "$DEST/server/config.json" ]; then
  cp "$DEST/server/config.example.json" "$DEST/server/config.json"
  warn "created server/config.json with an EMPTY password: only the local kiosk can connect until you set one"
fi
chmod 600 "$DEST/server/config.json"
chown -R "$PIT_USER:$PIT_USER" "$DEST"
chmod +x "$DEST/kiosk/start-chromium.sh" "$DEST/install.sh" 2>/dev/null || true

echo "--- groups"
for g in dialout video render input; do
  getent group "$g" >/dev/null && usermod -aG "$g" "$PIT_USER"
done

echo "--- udev: /dev/tbeam"
install -m 644 "$DEST/udev/99-gokart-tbeam.rules" /etc/udev/rules.d/99-gokart-tbeam.rules
udevadm control --reload-rules && udevadm trigger --subsystem-match=tty || true

echo "--- avahi (gokart-pit.local)"
systemctl enable --now avahi-daemon || warn "avahi-daemon failed"

echo "--- systemd units"
install -m 644 "$DEST/kiosk/pam-gokart-kiosk" /etc/pam.d/gokart-kiosk
for unit in gokart-pit.service gokart-kiosk.service; do
  sed "s/@PIT_USER@/$PIT_USER/g" "$DEST/systemd/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl disable getty@tty1.service 2>/dev/null || true   # tty1 belongs to the kiosk; ssh + tty2-6 remain
systemctl set-default graphical.target
systemctl enable gokart-pit.service gokart-kiosk.service
systemctl restart gokart-pit.service || warn "gokart-pit failed to start: journalctl -u gokart-pit"
if [ "${GOKART_NO_KIOSK_RESTART:-0}" != 1 ]; then
  systemctl restart gokart-kiosk.service || warn "kiosk failed to start: journalctl -u gokart-kiosk"
fi

echo "=== install done (warnings: $WARN) $(date)"
exit 0
