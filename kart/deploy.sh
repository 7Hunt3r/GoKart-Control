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
  ssh -t "$U@$HOST" "sudo bash /opt/gokart-kart/install.sh $U"
else
  ssh -t "$U@$HOST" "sudo systemctl restart gokart-kart gokart-kart-kiosk && systemctl --no-pager status gokart-kart | head -5"
fi
