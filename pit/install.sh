#!/bin/bash
# Install / update the GoKart pit station ON THE PI (Raspberry Pi OS Trixie Lite).
#
#   sudo bash install.sh [user]        # default user: pit
#
# Idempotent: safe to re-run after every update. Run from /opt/gokart-pit (first
# boot does this) or from any copy of pit/ (it is rsynced into /opt/gokart-pit,
# keeping server/config.json and logs/). Run on the pit Pi 4 (2026-11-05 onwards; second run checked idempotent 2026-11-06).
set -uo pipefail
[ "$(id -u)" -eq 0 ] || exec sudo bash "$0" "$@"

PIT_USER="${1:-pit}"
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/gokart-pit
WARN=0
warn() { echo "WARN: $*"; WARN=1; }
id "$PIT_USER" >/dev/null 2>&1 || { echo "user $PIT_USER does not exist"; exit 1; }
echo "=== gokart-pit install $(date) user=$PIT_USER src=$SRC"

# Network + journal first: they matter most after a bad reboot and need nothing from apt.
echo "--- journal: persistent across reboots, capped at 200M"
sh "$SRC/tools/journal_persist.sh" || warn "journal_persist failed"

echo "--- network: persistent NetworkManager keyfiles (Wi-Fi + eth0 DHCP fallback)"
# The Wi-Fi list comes from /etc/gokart-pit/wifi.env (written by cloud-init on first boot,
# or pushed by deploy.sh --install), GOKART_WIFI_ENV, or a copy of sdcard/secrets.env next
# to this script. Only the WIFI_* lines are kept, root-only. Passwords are never printed.
WIFI_ENV=/etc/gokart-pit/wifi.env
for f in "${GOKART_WIFI_ENV:-}" "$SRC/sdcard/secrets.env"; do
  if [ -n "$f" ] && [ -s "$f" ] && [ "$f" != "$WIFI_ENV" ] && grep -qE '^WIFI_SSID=.+' "$f"; then
    install -d -m 700 /etc/gokart-pit
    ( umask 077; grep -E '^(WIFI2?_(SSID|PASSWORD)|WIFI_COUNTRY)=' "$f" > "$WIFI_ENV.tmp" ) && mv "$WIFI_ENV.tmp" "$WIFI_ENV"
    echo "wifi env: WIFI_* lines from $f -> $WIFI_ENV"
    break
  fi
done
if ! command -v nmcli >/dev/null; then
  warn "nmcli not found: Wi-Fi profiles not written"
elif [ -s "$WIFI_ENV" ]; then
  chmod 600 "$WIFI_ENV"
  python3 "$SRC/tools/wifi_profiles.py" "$WIFI_ENV" || warn "wifi_profiles.py failed (Wi-Fi profile not written)"
else
  echo "no $WIFI_ENV: Wi-Fi profiles left as they are (only the eth0 fallback is ensured)"
  python3 "$SRC/tools/wifi_profiles.py" /dev/null || warn "wifi_profiles.py failed"
fi
# NetworkManager-written netplan files that a power cut left at 0 bytes (the 2026-11-06 failure)
for y in /etc/netplan/90-NM-*.yaml; do
  [ -f "$y" ] && [ ! -s "$y" ] && rm -f "$y" && echo "removed empty $y"
done
# cloud-init must never rewrite the network config after the first boot (profiles live in keyfiles now)
if [ -d /etc/cloud/cloud.cfg.d ]; then
  echo "network: {config: disabled}" > /etc/cloud/cloud.cfg.d/99-gokart-no-network-config.cfg
fi

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
  # map/ holds operator-added imagery: add/refresh files, never delete. zones.json is edited live
  # on the Pi (operator page ZONES), so it is only copied when the Pi has none.
  rsync -a --exclude recordings/ --exclude 'zones.json' --exclude 'zones.json.*' --exclude '*.tmp' \
    "$SRC/server/map/" "$DEST/server/map/"
  [ -f "$DEST/server/map/zones.json" ] || { [ -f "$SRC/server/map/zones.json" ] && cp "$SRC/server/map/zones.json" "$DEST/server/map/zones.json"; }
fi
mkdir -p "$DEST/logs"
if [ ! -f "$DEST/server/config.json" ]; then
  cp "$DEST/server/config.example.json" "$DEST/server/config.json"
  warn "created server/config.json with an EMPTY password: only the local kiosk can connect until you set one"
fi
chmod 600 "$DEST/server/config.json"
chown -R "$PIT_USER:$PIT_USER" "$DEST"
chmod +x "$DEST/kiosk/start-chromium.sh" "$DEST/install.sh" "$DEST"/tools/*.sh "$DEST"/tools/*.py 2>/dev/null || true

echo "--- groups"
for g in dialout video render input; do
  getent group "$g" >/dev/null && usermod -aG "$g" "$PIT_USER"
done

echo "--- sound: ALSA default = the pit panel's HDMI speakers (vc4-hdmi); Chromium falls back to ALSA (no PipeWire/Pulse)"
apt-get install -y --no-install-recommends alsa-utils || warn "alsa-utils failed"
getent group audio >/dev/null && usermod -aG audio "$PIT_USER"
# vc4-hdmi wants IEC958 frames: the "hdmi" pcm converts, "plug" fixes rate/format. Exclusive (no dmix):
# while Chromium plays, `aplay` waits for the device. GOKART_HDMI_CARD overrides the card (Pi 4: vc4hdmi0 / vc4hdmi1).
HDMI_CARD="${GOKART_HDMI_CARD:-}"
if [ -z "$HDMI_CARD" ]; then
  for c in vc4hdmi vc4hdmi0 vc4hdmi1; do [ -e "/proc/asound/$c" ] && { HDMI_CARD="$c"; break; }; done
fi
if [ -n "$HDMI_CARD" ]; then
  cat > /etc/asound.conf <<EOF
# Written by GoKart install.sh: game sounds out of the HDMI panel's speakers ($HDMI_CARD).
pcm.!default {
  type plug
  slave.pcm "hdmi:CARD=$HDMI_CARD,DEV=0"
}
ctl.!default {
  type hw
  card $HDMI_CARD
}
EOF
  echo "/etc/asound.conf -> $HDMI_CARD (test: speaker-test -c2 -t sine -l1)"
else
  warn "no vc4-hdmi sound card found (/proc/asound); game sounds go to the default ALSA device"
fi

echo "--- udev: /dev/tbeam"
install -m 644 "$DEST/udev/99-gokart-tbeam.rules" /etc/udev/rules.d/99-gokart-tbeam.rules
udevadm control --reload-rules && udevadm trigger --subsystem-match=tty || true

echo "--- avahi (gokart-pit.local)"
systemctl enable --now avahi-daemon || warn "avahi-daemon failed"

echo "--- systemd units"
install -m 644 "$DEST/kiosk/pam-gokart-kiosk" /etc/pam.d/gokart-kiosk
for unit in gokart-pit.service gokart-kiosk.service gokart-check.service gokart-check.timer; do
  sed "s/@PIT_USER@/$PIT_USER/g" "$DEST/systemd/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl disable getty@tty1.service 2>/dev/null || true   # tty1 belongs to the kiosk; ssh + tty2-6 remain
systemctl set-default graphical.target
systemctl enable gokart-pit.service gokart-kiosk.service
systemctl enable --now gokart-check.timer || warn "gokart-check.timer failed"
systemctl restart gokart-pit.service || warn "gokart-pit failed to start: journalctl -u gokart-pit"
if [ "${GOKART_NO_KIOSK_RESTART:-0}" != 1 ]; then
  systemctl restart gokart-kiosk.service || warn "kiosk failed to start: journalctl -u gokart-kiosk"
fi

for u in gokart-pit gokart-check.timer; do
  echo "$u: $(systemctl is-active "$u")"
done
echo "after a surprise reboot: sudo /opt/gokart-pit/tools/why_rebooted.sh"
echo "=== install done (warnings: $WARN) $(date)"
exit 0
