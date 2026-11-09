#!/bin/bash
# One-time first-boot steps, run by cloud-init runcmd as root from the unpacked payload.
# Safe to re-run by hand: sudo bash /opt/gokart-kart/sdcard/firstboot.sh kart gokart-kart-2 McLab 2
# Adapted from pit/sdcard/firstboot.sh. Flashed and booted kart 2 (Pi 3 B) 2026-11-06.
set -u
USER_NAME="${1:?user}"
HOST_NAME="${2:-gokart-kart}"
PRIMARY_SSID="${3:-}"
KART_ID="${4:-}"
APP=/opt/gokart-kart
echo "=== gokart-kart firstboot $(date) user=$USER_NAME host=$HOST_NAME kart=$KART_ID"

echo "--- kart display app (packages, services, udev, kiosk, HDMI mode)"
# The kiosk starts after the automatic reboot; don't light the screen mid-install.
GOKART_NO_KIOSK_RESTART=1 bash "$APP/install.sh" "$USER_NAME" "$KART_ID" || echo "WARN: install.sh failed (see above)"

echo "--- wifi: persistent keyfiles are written by install.sh from /etc/gokart-kart/wifi.env ($PRIMARY_SSID first)"
# Do NOT `nmcli connection modify` the netplan-generated profiles (the pit lost its Wi-Fi that way on
# 2026-11-06, kart 2 on 2026-11-09): NetworkManager moves them into /etc/netplan/90-NM-*.yaml and a
# power cut can leave that file empty. tools/wifi_profiles.py writes plain keyfiles instead.

echo "--- tailscale (optional)"
if [ -s /root/tailscale.key ] && [ "$(tr -d '[:space:]"' < /root/tailscale.key)" != "" ]; then
  if curl -fsSL https://tailscale.com/install.sh | sh; then
    tailscale up --auth-key=file:/root/tailscale.key --ssh --hostname="$HOST_NAME" \
      && echo "tailscale joined" || echo "WARN: tailscale up failed. Re-run: sudo tailscale up --ssh"
  else
    echo "WARN: tailscale install failed (no internet?)"
  fi
else
  echo "no TAILSCALE_AUTHKEY: skipped"
fi
shred -u /root/tailscale.key 2>/dev/null || rm -f /root/tailscale.key
sed -i -E 's/tskey-auth-[A-Za-z0-9_-]+/REDACTED/g' /boot/firmware/user-data 2>/dev/null || true
rm -f /opt/gokart-kart-payload.tar.gz

echo "=== firstboot done $(date)"
