#!/bin/bash
# Push this pit/ folder to a running pit Pi and restart the services.
#   pit/deploy.sh [host] [--install]
# --install also re-runs install.sh (apt, units, udev); otherwise just restarts.
# Keeps the Pi's server/config.json, logs/, any map imagery and its edited map/zones.json. Used against the real pit Pi on 2026-11-05.
set -euo pipefail
cd "$(dirname "$0")"
HOST="${1:-gokart-pit.local}"
U="${PIT_USER:-pit}"
rsync -av --delete \
  --exclude 'logs/' --exclude 'server/config.json' --exclude 'server/map/' --exclude 'config.json' --exclude 'map/' \
  --exclude 'sdcard/secrets.env' --exclude 'sdcard/build/' --exclude '__pycache__/' --exclude '.DS_Store' \
  ./ "$U@$HOST:/opt/gokart-pit/"
# Map folder: map.json presets, README/ZONES.md and imagery are pushed (never deleted on the Pi).
# zones.json is edited live on the Pi (operator page ZONES) so it is only copied when the Pi has none.
rsync -av --exclude '.DS_Store' --exclude 'zones.json' --exclude '*.tmp' --exclude 'recordings/' server/map/ "$U@$HOST:/opt/gokart-pit/server/map/"
rsync -av --ignore-existing server/map/zones.json "$U@$HOST:/opt/gokart-pit/server/map/zones.json"
if [ "${2:-}" = "--install" ]; then
  # Wi-Fi list for the persistent NetworkManager keyfiles (install.sh -> tools/wifi_profiles.py):
  # only the WIFI_* lines of sdcard/secrets.env, over ssh stdin, root-only on the Pi. GOKART_NO_WIFI_PUSH=1 skips.
  if [ "${GOKART_NO_WIFI_PUSH:-0}" != 1 ] && [ -s sdcard/secrets.env ] && grep -qE '^WIFI_SSID=.+' sdcard/secrets.env; then
    grep -E '^(WIFI2?_(SSID|PASSWORD)|WIFI_COUNTRY)=' sdcard/secrets.env | ssh "$U@$HOST" \
      'sudo install -d -m 700 /etc/gokart-pit && sudo sh -c "umask 077; cat > /etc/gokart-pit/wifi.env.tmp && mv /etc/gokart-pit/wifi.env.tmp /etc/gokart-pit/wifi.env"' \
      && echo "pushed WIFI_* lines -> /etc/gokart-pit/wifi.env"
  fi
  # Run install.sh as a transient systemd unit so a dropped ssh session (Wi-Fi blip while the
  # profiles are rewritten) cannot kill it half way with the services stopped.
  ssh -t "$U@$HOST" "sudo systemd-run --quiet --wait --pipe --collect --unit=gokart-install-\$(date +%s) bash /opt/gokart-pit/install.sh $U"
else
  ssh -t "$U@$HOST" "sudo systemctl restart gokart-pit gokart-kiosk && systemctl --no-pager status gokart-pit | head -5"
fi
