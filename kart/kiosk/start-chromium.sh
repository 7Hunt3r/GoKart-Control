#!/bin/bash
# Run by cage inside gokart-kart-kiosk.service. Opens the driver page full screen and watches it.
# Adapted from pit/kiosk/start-chromium.sh (worked on the pit Pi 4, 2026-11-05); running on kart 2
# (Pi 3 B) since 2026-11-06.
#
# Flags (Pi 3 B, 1 GB RAM, VC4 GPU):
#   one renderer; disk cache in RAM ($XDG_RUNTIME_DIR, tmpfs) so the SD card is not written;
#   --disable-renderer-accessibility: Debian's /etc/chromium.d/default-flags forces renderer
#     accessibility on, which costs main-thread time on every DOM update (measured on kart 2);
#   --autoplay-policy: the game sounds (WebAudio) play with no touch first;
#   no crash/restore bubbles, no first-run, no background networking or component updates (on track
#     there is no internet and nothing should phone home).
# Extra flags: GOKART_CHROMIUM_FLAGS in /etc/default/gokart-kart-kiosk (e.g. --disable-gpu, or
# --remote-debugging-port=9222 for measurements).
#
# Watchdog: if Chromium exits, or the page stops sending its 5 s heartbeat to the server
# (POST /api/heartbeat; renderer crash "Aw, Snap!", hung page) for WATCHDOG_S while the server is up,
# Chromium is stopped and this script exits; cage then exits and systemd restarts the kiosk
# (Restart=always). Every restart is logged to the journal (journalctl -u gokart-kart-kiosk).
URL="${GOKART_KIOSK_URL:-http://127.0.0.1:8081/}"
BIN="$(command -v chromium || command -v chromium-browser)"
PROFILE="$HOME/.config/gokart-kart-kiosk"
CACHE="${XDG_RUNTIME_DIR:-/tmp}/gokart-kart-kiosk-cache"
WATCHDOG_S="${GOKART_WATCHDOG_S:-60}"     # page silent this long (server up) = restart
GRACE_S=90                                 # after start: Chromium + page load on a Pi 3 B
mkdir -p "$PROFILE" "$CACHE"
# After a power cut Chromium thinks it crashed; clear that so no restore bubble appears.
sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' \
  "$PROFILE/Default/Preferences" 2>/dev/null || true
# shellcheck disable=SC2086
"$BIN" --kiosk --noerrdialogs --disable-infobars --touch-events=enabled \
  --ozone-platform=wayland --no-first-run --password-store=basic \
  --disable-session-crashed-bubble --disable-features=Translate,TranslateUI,MediaRouter \
  --overscroll-history-navigation=0 --disable-pinch --check-for-update-interval=31536000 \
  --renderer-process-limit=1 --disk-cache-dir="$CACHE" --disk-cache-size=16777216 \
  --disable-renderer-accessibility --disable-background-networking --disable-component-update \
  --disable-breakpad --force-device-scale-factor=1 \
  --autoplay-policy=no-user-gesture-required \
  $GOKART_CHROMIUM_FLAGS \
  --user-data-dir="$PROFILE" "$URL" &
PID=$!
trap 'kill $PID 2>/dev/null; exit 0' TERM INT
START=$(date +%s)
while sleep 10; do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "kiosk watchdog: chromium exited; restarting the kiosk"
    exit 1
  fi
  [ $(( $(date +%s) - START )) -lt "$GRACE_S" ] && continue
  # {"age_s": seconds since the page's last heartbeat or null, "server_up_s": ...}
  K="$(curl -fs -m 3 http://127.0.0.1:8081/api/kiosk)" || continue      # server down: not the page's fault
  if python3 - "$K" "$WATCHDOG_S" <<'PY'
import json, sys
k, limit = json.loads(sys.argv[1]), float(sys.argv[2])
age, up = k.get("age_s"), k.get("server_up_s") or 0
sys.exit(0 if up > limit and (age is None or age > limit) else 1)
PY
  then
    echo "kiosk watchdog: no heartbeat from the driver page for ${WATCHDOG_S} s ($K); restarting Chromium"
    kill "$PID" 2>/dev/null
    sleep 5
    kill -9 "$PID" 2>/dev/null
    exit 1
  fi
done
