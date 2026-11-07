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

echo "--- wifi preference: $PRIMARY_SSID first"
if command -v nmcli >/dev/null; then
  nmcli -t -f NAME,TYPE connection show | while IFS=: read -r name type; do
    [ "$type" = "802-11-wireless" ] || continue
    ssid="$(nmcli -g 802-11-wireless.ssid connection show "$name")"
    prio=10; [ "$ssid" = "$PRIMARY_SSID" ] && prio=20
    nmcli connection modify "$name" connection.autoconnect-priority "$prio" \
      && echo "  $ssid -> priority $prio" || echo "WARN: could not set priority on $name"
  done
fi

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
