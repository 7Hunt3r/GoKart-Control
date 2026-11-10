#!/bin/sh
# Wi-Fi self-heal for the kart Pi (runs from gokart-wifi-watch.timer every minute, as root).
# Why: on 2026-11-09 kart 2's Pi 3 B came back into Wi-Fi range and never rejoined (profile present,
# autoconnect-retries=0); a brown-out-starved brcmfmac radio wedges until something kicks it.
# Escalates while wlan0 stays disconnected: rescan + `nmcli con up`, then radio off/on, then a driver
# reload. Never touches a connected link. State in /run so a reboot starts clean.
set -u
IFACE="${GOKART_WIFI_IFACE:-wlan0}"
STATE=/run/gokart-wifi-watch
mkdir -p "$STATE"
if ! command -v nmcli >/dev/null 2>&1; then exit 0; fi
st="$(nmcli -t -f DEVICE,STATE device status 2>/dev/null | awk -F: -v i="$IFACE" '$1==i{print $2}')"
case "$st" in
  connected*) rm -f "$STATE/down_since" "$STATE/tries"; exit 0 ;;
esac
now=$(date +%s)
[ -f "$STATE/down_since" ] || echo "$now" > "$STATE/down_since"
down=$(( now - $(cat "$STATE/down_since") ))
[ "$down" -ge 90 ] || exit 0                       # give NetworkManager its own 90 s first
tries=$(cat "$STATE/tries" 2>/dev/null || echo 0); tries=$(( tries + 1 )); echo "$tries" > "$STATE/tries"
prof="$(nmcli -t -f NAME,TYPE connection show 2>/dev/null | awk -F: '$2=="802-11-wireless"{print $1; exit}')"
logger -t gokart-wifi-watch "wlan0 '$st' for ${down}s, try $tries (profile: ${prof:-none})"
if [ "$tries" -le 2 ]; then
  nmcli device wifi rescan >/dev/null 2>&1; sleep 3
  [ -n "$prof" ] && nmcli connection up "$prof" >/dev/null 2>&1
elif [ "$tries" -le 4 ]; then
  nmcli radio wifi off; sleep 3; nmcli radio wifi on; sleep 5
  [ -n "$prof" ] && nmcli connection up "$prof" >/dev/null 2>&1
else
  logger -t gokart-wifi-watch "reloading the Wi-Fi driver (brcmfmac)"
  modprobe -r brcmfmac 2>/dev/null; sleep 2; modprobe brcmfmac 2>/dev/null; sleep 8
  nmcli radio wifi on; [ -n "$prof" ] && nmcli connection up "$prof" >/dev/null 2>&1
  echo 0 > "$STATE/tries"
fi
