#!/bin/bash
# Install / update the GoKart kart display ON THE KART PI (Pi 3 B, Raspberry Pi OS Trixie Lite arm64).
#
#   sudo bash install.sh [user] [kart_id]     # defaults: kart, (kart_id only used if config.json is new)
#
# Idempotent: safe to re-run after every update. Run from /opt/gokart-kart (first
# boot does this) or from any copy of kart/ (rsynced into /opt/gokart-kart,
# keeping server/config.json, logs/ and the map cache).
# Adapted from pit/install.sh (ran on the pit Pi 4, 2026-11-05). UNTESTED ON A PI 3 B.
set -uo pipefail
[ "$(id -u)" -eq 0 ] || exec sudo bash "$0" "$@"

KART_USER="${1:-kart}"
KART_ID="${2:-}"
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/gokart-kart
WARN=0
warn() { echo "WARN: $*"; WARN=1; }
id "$KART_USER" >/dev/null 2>&1 || { echo "user $KART_USER does not exist"; exit 1; }
echo "=== gokart-kart install $(date) user=$KART_USER src=$SRC"

echo "--- packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update || warn "apt-get update failed (no network?)"
apt-get install -y --no-install-recommends python3 python3-serial avahi-daemon libnss-mdns curl rsync \
  dbus libpam-systemd fonts-dejavu-core || warn "base packages failed"
# Kiosk: cage (wlroots kiosk compositor) + Chromium + Mesa GL (Pi 3 B: VC4 via vc4-kms-v3d).
apt-get install -y cage libgl1-mesa-dri || warn "cage failed"
apt-get install -y chromium || apt-get install -y chromium-browser || warn "chromium failed"

echo "--- app files -> $DEST"
mkdir -p "$DEST"
if [ "$SRC" != "$DEST" ]; then
  # leading "/" anchors each pattern to the top of kart/ (learned from pit/deploy.sh)
  rsync -a --delete \
    --exclude '/logs/' --exclude '/cache/' --exclude '/server/config.json' \
    --exclude '/sdcard/secrets.env' --exclude '/sdcard/build/' --exclude '__pycache__/' --exclude '.DS_Store' \
    "$SRC/" "$DEST/"
fi
mkdir -p "$DEST/logs" "$DEST/cache/map"
if [ ! -f "$DEST/server/config.json" ]; then
  python3 - "$DEST/server" "$KART_ID" <<'PY'
import json, sys
d, kid = sys.argv[1], sys.argv[2]
cfg = json.load(open(f"{d}/config.example.json"))
cfg.pop("_comment", None)
if kid:
    cfg["kart_id"] = int(kid)
json.dump(cfg, open(f"{d}/config.json", "w"), indent=2)
PY
  echo "created server/config.json (kart_id $(python3 -c "import json;print(json.load(open('$DEST/server/config.json'))['kart_id'])"))"
fi
chown -R "$KART_USER:$KART_USER" "$DEST"
chmod +x "$DEST/kiosk/start-chromium.sh" "$DEST/install.sh" "$DEST/sim/sim_heltec.py" 2>/dev/null || true

echo "--- groups"
for g in dialout video render input; do
  getent group "$g" >/dev/null && usermod -aG "$g" "$KART_USER"
done

echo "--- sound: ALSA default = the panel's HDMI speakers (vc4-hdmi); Chromium falls back to ALSA (no PipeWire/Pulse)"
apt-get install -y --no-install-recommends alsa-utils || warn "alsa-utils failed"
getent group audio >/dev/null && usermod -aG audio "$KART_USER"
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

echo "--- udev: /dev/heltec"
install -m 644 "$DEST/udev/99-gokart-heltec.rules" /etc/udev/rules.d/99-gokart-heltec.rules
udevadm control --reload-rules && udevadm trigger --subsystem-match=tty || true

echo "--- avahi ($(hostname).local) + mDNS lookups (gokart-pit.local)"
systemctl enable --now avahi-daemon || warn "avahi-daemon failed"

echo "--- display: LESOWN L7 has no EDID; pin 1024x600 (Pi 3 B has one HDMI = HDMI-A-1)"
CMDLINE=/boot/firmware/cmdline.txt
if [ -f "$CMDLINE" ]; then
  if ! grep -q "video=HDMI-A-1:1024x600" "$CMDLINE"; then
    sed -i 's/[[:space:]]*$//; s/$/ video=HDMI-A-1:1024x600M@60 video=HDMI-A-2:1024x600M@60/' "$CMDLINE"
    echo "cmdline.txt: added 1024x600 video= (takes effect after a reboot)"
  fi
else
  warn "$CMDLINE not found; HDMI mode not pinned"
fi

echo "--- systemd units"
install -m 644 "$DEST/kiosk/pam-gokart-kart-kiosk" /etc/pam.d/gokart-kart-kiosk
for unit in gokart-kart.service gokart-kart-kiosk.service; do
  sed "s/@KART_USER@/$KART_USER/g" "$DEST/systemd/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl disable getty@tty1.service 2>/dev/null || true   # tty1 belongs to the kiosk; ssh + tty2-6 remain
systemctl set-default graphical.target
systemctl enable gokart-kart.service gokart-kart-kiosk.service
systemctl restart gokart-kart.service || warn "gokart-kart failed to start: journalctl -u gokart-kart"
if [ "${GOKART_NO_KIOSK_RESTART:-0}" != 1 ]; then
  systemctl restart gokart-kart-kiosk.service || warn "kiosk failed to start: journalctl -u gokart-kart-kiosk"
fi

echo "=== install done (warnings: $WARN) $(date)"
exit 0
