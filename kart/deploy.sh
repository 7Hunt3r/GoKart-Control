#!/bin/bash
# Push this kart/ folder to a running kart Pi and restart its services.
#   kart/deploy.sh [host] [--install]          default host: gokart-kart-2.local
# --install also re-runs install.sh (apt, units, udev, cmdline); otherwise just restarts.
# Keeps the Pi's server/config.json, logs/ and cached course map.
# Excludes start with "/" so they are anchored to the top of kart/ (the source
# dir) and cannot accidentally match a file of the same name deeper down;
# pit/deploy.sh needed a fix for exactly this on 2026-11-05.
# SSH uses the key baked in by the SD card kit (no password). Used against kart 2 on 2026-11-06.
set -euo pipefail
cd "$(dirname "$0")"
HOST="${1:-gokart-kart-2.local}"
U="${KART_USER:-kart}"
rsync -av --delete \
  --exclude '/logs/' --exclude '/cache/' --exclude '/server/config.json' \
  --exclude '/sdcard/secrets.env' --exclude '/sdcard/build/' \
  --exclude '__pycache__/' --exclude '.DS_Store' \
  ./ "$U@$HOST:/opt/gokart-kart/"
if [ "${2:-}" = "--install" ]; then
  # Wi-Fi list for the persistent NetworkManager keyfiles (install.sh -> tools/wifi_profiles.py):
  # only the WIFI_* lines of sdcard/secrets.env, over ssh stdin, root-only on the Pi. GOKART_NO_WIFI_PUSH=1 skips.
  if [ "${GOKART_NO_WIFI_PUSH:-0}" != 1 ] && [ -s sdcard/secrets.env ] && grep -qE '^WIFI_SSID=.+' sdcard/secrets.env; then
    grep -E '^(WIFI2?_(SSID|PASSWORD)|WIFI_COUNTRY)=' sdcard/secrets.env | ssh "$U@$HOST" \
      'sudo install -d -m 700 /etc/gokart-kart && sudo sh -c "umask 077; cat > /etc/gokart-kart/wifi.env.tmp && mv /etc/gokart-kart/wifi.env.tmp /etc/gokart-kart/wifi.env"' \
      && echo "pushed WIFI_* lines -> /etc/gokart-kart/wifi.env"
  fi
  ssh -t "$U@$HOST" "sudo bash /opt/gokart-kart/install.sh $U"
else
  ssh -t "$U@$HOST" "sudo systemctl restart gokart-kart gokart-kart-kiosk && systemctl --no-pager status gokart-kart | head -5"
fi
