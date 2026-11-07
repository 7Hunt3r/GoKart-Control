#!/bin/sh
# Run by cage inside gokart-kiosk.service. Opens the pit touch UI full screen.
URL="${GOKART_KIOSK_URL:-http://127.0.0.1:8080/kiosk}"
BIN="$(command -v chromium || command -v chromium-browser)"
PROFILE="$HOME/.config/gokart-kiosk"
mkdir -p "$PROFILE"
# After a power cut Chromium thinks it crashed; clear that so no restore bubble appears.
sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' \
  "$PROFILE/Default/Preferences" 2>/dev/null || true
exec "$BIN" --kiosk --noerrdialogs --disable-infobars --touch-events=enabled \
  --ozone-platform=wayland --no-first-run --password-store=basic \
  --disable-session-crashed-bubble --disable-features=Translate,TranslateUI \
  --autoplay-policy=no-user-gesture-required --overscroll-history-navigation=0 --disable-pinch --check-for-update-interval=31536000 \
  --user-data-dir="$PROFILE" "$URL"
