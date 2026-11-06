#!/bin/sh
# Run by cage inside gokart-kart-kiosk.service. Opens the driver page full screen.
# Adapted from pit/kiosk/start-chromium.sh (worked on the pit Pi 4, 2026-11-05).
# Pi 3 B (1 GB RAM): one renderer, small disk cache. Extra flags can be added via
# GOKART_CHROMIUM_FLAGS in /etc/default/gokart-kart-kiosk (e.g. --disable-gpu).
URL="${GOKART_KIOSK_URL:-http://127.0.0.1:8081/}"
BIN="$(command -v chromium || command -v chromium-browser)"
PROFILE="$HOME/.config/gokart-kart-kiosk"
mkdir -p "$PROFILE"
# After a power cut Chromium thinks it crashed; clear that so no restore bubble appears.
sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' \
  "$PROFILE/Default/Preferences" 2>/dev/null || true
# shellcheck disable=SC2086
exec "$BIN" --kiosk --noerrdialogs --disable-infobars --touch-events=enabled \
  --ozone-platform=wayland --no-first-run --password-store=basic \
  --disable-session-crashed-bubble --disable-features=Translate,TranslateUI,MediaRouter \
  --overscroll-history-navigation=0 --disable-pinch --check-for-update-interval=31536000 \
  --renderer-process-limit=1 --disk-cache-size=33554432 \
  $GOKART_CHROMIUM_FLAGS \
  --user-data-dir="$PROFILE" "$URL"
