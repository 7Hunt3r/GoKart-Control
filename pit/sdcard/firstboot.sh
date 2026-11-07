#!/bin/bash
# One-time first-boot steps, run by cloud-init runcmd as root from the unpacked payload.
# Safe to re-run by hand: sudo bash /opt/gokart-pit/sdcard/firstboot.sh pit gokart-pit mclab
set -u
USER_NAME="${1:?user}"
HOST_NAME="${2:-gokart-pit}"
PRIMARY_SSID="${3:-}"
APP=/opt/gokart-pit
echo "=== gokart-pit firstboot $(date) user=$USER_NAME host=$HOST_NAME"

echo "--- journal: persistent from now on (Pi OS default is volatile; crash logs were lost on 2026-11-06)"
sh "$APP/tools/journal_persist.sh" || echo "WARN: journal_persist failed"

echo "--- pit app (packages, services, udev, kiosk, Wi-Fi keyfiles)"
# The kiosk starts after the automatic reboot; don't light the screen mid-install.
# install.sh writes real NetworkManager keyfiles for $PRIMARY_SSID (priority 20) and the optional
# second network (10) from /etc/gokart-pit/wifi.env (cloud-init write_files), plus eth0 DHCP.
# Do NOT `nmcli connection modify` the netplan-generated profiles: that makes NetworkManager move
# them into /etc/netplan/90-NM-*.yaml, which power cuts left empty (Wi-Fi gone after reboot).
GOKART_NO_KIOSK_RESTART=1 bash "$APP/install.sh" "$USER_NAME" || echo "WARN: install.sh failed (see above)"
if [ -n "$PRIMARY_SSID" ] && ! grep -rqsx "ssid=$PRIMARY_SSID" /etc/NetworkManager/system-connections/; then
  echo "WARN: no persistent keyfile for $PRIMARY_SSID in /etc/NetworkManager/system-connections"
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
# The pit web password was delivered via user-data (now in server/config.json); scrub the boot-partition copy.
sed -i -E '\#path: /opt/gokart-pit/server/config.json#,+3 s#^(    content: ).*#\1"REDACTED"#' /boot/firmware/user-data 2>/dev/null || true
rm -f /opt/gokart-pit-payload.tar.gz

# --- display: the LESOWN L7 panel sends no EDID, so KMS falls back to 1024x768.
# Pin the native 1024x600 mode on both micro-HDMI ports (kernel CVT mode; the
# panel's own scaler is bypassed). Verified on the pit Pi 4 on 2026-11-05.
echo "--- display mode: 1024x600 on HDMI-A-1/2"
CMDLINE=/boot/firmware/cmdline.txt
if [ -f "$CMDLINE" ] && ! grep -q "video=HDMI-A-2:1024x600" "$CMDLINE"; then
  sed -i 's/[[:space:]]*$//; s/$/ video=HDMI-A-1:1024x600M@60 video=HDMI-A-2:1024x600M@60/' "$CMDLINE"
fi

echo "=== firstboot done $(date)"
