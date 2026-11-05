#!/bin/bash
# Push this pit/ folder to a running pit Pi and restart the services.
#   pit/deploy.sh [host] [--install]
# --install also re-runs install.sh (apt, units, udev); otherwise just restarts.
# Keeps the Pi's server/config.json, logs/ and any map imagery. Used against the real pit Pi on 2026-11-05.
set -euo pipefail
cd "$(dirname "$0")"
HOST="${1:-gokart-pit.local}"
U="${PIT_USER:-pit}"
rsync -av --delete \
  --exclude 'logs/' --exclude 'server/config.json' --exclude 'server/map/' --exclude 'config.json' --exclude 'map/' \
  --exclude 'sdcard/secrets.env' --exclude 'sdcard/build/' --exclude '__pycache__/' --exclude '.DS_Store' \
  ./ "$U@$HOST:/opt/gokart-pit/"
rsync -av --exclude '.DS_Store' server/map/ "$U@$HOST:/opt/gokart-pit/server/map/"
if [ "${2:-}" = "--install" ]; then
  ssh -t "$U@$HOST" "sudo bash /opt/gokart-pit/install.sh $U"
else
  ssh -t "$U@$HOST" "sudo systemctl restart gokart-pit gokart-kiosk && systemctl --no-pager status gokart-pit | head -5"
fi
