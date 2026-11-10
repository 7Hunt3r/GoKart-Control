#!/bin/bash
# Install / update the GoKart kart display ON THE KART PI (Pi 3 B, Raspberry Pi OS Trixie Lite arm64).
#
#   sudo bash install.sh [user] [kart_id]     # defaults: kart, (kart_id only used if config.json is new)
#
# Idempotent: safe to re-run after every update. Run from /opt/gokart-kart (first
# boot does this) or from any copy of kart/ (rsynced into /opt/gokart-kart,
# keeping server/config.json, logs/ and the map cache).
# Adapted from pit/install.sh (ran on the pit Pi 4, 2026-11-05). Ran on kart 2 (Pi 3 B) 2026-11-06.
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

# Network + journal first: they matter most after a bad reboot and need nothing from apt.
echo "--- journal: persistent across reboots, capped at 200M"
sh "$SRC/tools/journal_persist.sh" || warn "journal_persist failed"

echo "--- network: persistent NetworkManager keyfiles (Wi-Fi + eth0 DHCP fallback)"
# Same hardening as pit/install.sh. The 2026-11-06 pit failure: `nmcli connection modify` on the
# cloud-init Wi-Fi profile moved it into /etc/netplan/90-NM-*.yaml, and power cuts left that file
# 0 bytes, so the Pi came back with no Wi-Fi. Kart 2 lost its Wi-Fi the same way on 2026-11-09 after
# brown-out reboots. The Wi-Fi list comes from /etc/gokart-kart/wifi.env (pushed by deploy.sh
# --install, or written by cloud-init), GOKART_WIFI_ENV, or a copy of sdcard/secrets.env next to
# this script. Only the WIFI_* lines are kept, root-only. Passwords are never printed.
WIFI_ENV=/etc/gokart-kart/wifi.env
for f in "${GOKART_WIFI_ENV:-}" "$SRC/sdcard/secrets.env"; do
  if [ -n "$f" ] && [ -s "$f" ] && [ "$f" != "$WIFI_ENV" ] && grep -qE '^WIFI_SSID=.+' "$f"; then
    install -d -m 700 /etc/gokart-kart
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
# NetworkManager-written netplan files that a power cut left at 0 bytes
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
cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
if kid:
    cfg["kart_id"] = int(kid)
json.dump(cfg, open(f"{d}/config.json", "w"), indent=2)
PY
  echo "created server/config.json (kart_id $(python3 -c "import json;print(json.load(open('$DEST/server/config.json'))['kart_id'])"))"
fi
chown -R "$KART_USER:$KART_USER" "$DEST"
chmod 755 "$DEST/logs" "$DEST/cache" "$DEST/cache/map"
chmod +x "$DEST/kiosk/start-chromium.sh" "$DEST/install.sh" "$DEST/deploy.sh" "$DEST/sim/sim_heltec.py" 2>/dev/null || true
cfg_get() {   # cfg_get KEY DEFAULT -> value from server/config.json
  python3 - "$DEST/server/config.json" "$1" "$2" <<'PY' 2>/dev/null || echo "$2"
import json, sys
try:
    v = json.load(open(sys.argv[1])).get(sys.argv[2])
except (OSError, ValueError):
    v = None
print(sys.argv[3] if v is None else v)
PY
}

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

echo "--- display: LESOWN L7 has no EDID; pin 1024x600 (Pi 3 B has one HDMI = HDMI-A-1); rotate from config.json"
# "rotate": 0 (default) or 180 (panel mounted ports-down). 180 = the kernel turns the picture
# (video=...,rotate=180) and a udev rule turns the touch input to match (libinput calibration matrix).
ROTATE="$(cfg_get rotate 0)"
case "$ROTATE" in 0|180) ;; *) warn "config rotate=$ROTATE not supported (0 or 180); using 0"; ROTATE=0 ;; esac
CMDLINE=/boot/firmware/cmdline.txt
if [ -f "$CMDLINE" ]; then
  # rewrite our video= tokens (any previous rotation) and keep everything else; one line, idempotent
  if python3 - "$CMDLINE" "$ROTATE" <<'PY'
import sys
path, rot = sys.argv[1], sys.argv[2]
old = open(path).read()
toks = [t for t in old.split() if not t.startswith(("video=HDMI-A-1:", "video=HDMI-A-2:"))]
suffix = ",rotate=180" if rot == "180" else ""
toks += [f"video=HDMI-A-1:1024x600M@60{suffix}", f"video=HDMI-A-2:1024x600M@60{suffix}"]
new = " ".join(toks) + "\n"
if new.split() != old.split():
    open(path, "w").write(new)
    print(f"cmdline.txt: video= set for rotate {rot} (takes effect after a reboot)")
PY
  then :; else warn "could not update $CMDLINE"; fi
else
  warn "$CMDLINE not found; HDMI mode not pinned"
fi
if [ "$ROTATE" = 180 ]; then
  install -m 644 "$DEST/udev/98-gokart-touch-rotate.rules" /etc/udev/rules.d/98-gokart-touch-rotate.rules
else
  rm -f /etc/udev/rules.d/98-gokart-touch-rotate.rules
fi
udevadm control --reload-rules && udevadm trigger --subsystem-match=input || true

echo "--- systemd units"
# Wi-Fi self-heal timer (tools/wifi_watch.sh): a Pi 3 B that comes back into range does not always
# rejoin on its own (2026-11-09); the timer rescans, re-ups the profile, cycles the radio, then reloads
# the driver, escalating only while wlan0 stays disconnected.
install -m 644 "$SRC/systemd/gokart-wifi-watch.service" "$SRC/systemd/gokart-wifi-watch.timer" /etc/systemd/system/ 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now gokart-wifi-watch.timer >/dev/null 2>&1 || warn "gokart-wifi-watch.timer not enabled"
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
