#!/bin/bash
# One command for "why did the pit Pi reboot / lose its network?"
#   sudo /opt/gokart-pit/tools/why_rebooted.sh [boot]     # boot: -1 = previous (default), -2, ...
# Prints: the boot list, the last 30 journal lines of that boot (a clean shutdown ends with
# "Journal stopped"/"reboot: ..."; a power cut just stops mid-stream), under-voltage counts,
# current throttled flags + temperature, NetworkManager state and profile files, and the
# pit services. Needs the persistent journal (tools/journal_persist.sh, installed by install.sh).
set -u
[ "$(id -u)" -eq 0 ] || exec sudo bash "$0" "$@"
B="${1:--1}"
hr() { printf '\n=== %s\n' "$*"; }

hr "boots (journal storage: $(ls -d /var/log/journal/*/ >/dev/null 2>&1 && echo persistent || echo VOLATILE: earlier boots are lost))"
journalctl --list-boots --no-pager 2>/dev/null | tail -8
echo "uptime: $(uptime -p 2>/dev/null)  since $(uptime -s 2>/dev/null)"

hr "last 30 journal lines of boot $B"
if journalctl -b "$B" -n 0 >/dev/null 2>&1; then
  journalctl -b "$B" -n 30 --no-pager -o short-iso
  if journalctl -b "$B" --no-pager -o cat 2>/dev/null | tail -200 | grep -qE "Journal stopped|reboot: (Restarting|Power down)|systemd-shutdown"; then
    echo "--> boot $B ended with a CLEAN shutdown/reboot (someone or something asked for it)"
  else
    echo "--> boot $B has NO clean-shutdown marker: power loss, brown-out, kernel hang or watchdog reset"
  fi
else
  echo "no journal for boot $B (journal was volatile before tools/journal_persist.sh ran)"
fi

hr "under-voltage / throttling reported by the kernel"
for b in "$B" 0; do
  n=$(journalctl -k -b "$b" --no-pager 2>/dev/null | grep -ci "under-voltage\|undervoltage")
  echo "boot $b: $n under-voltage kernel messages"
done
journalctl -k -b 0 --no-pager 2>/dev/null | grep -i "voltage" | tail -5
echo "pit server health transitions (this boot):"
journalctl -b 0 -u gokart-pit --no-pager -o short-iso 2>/dev/null | grep " health " | tail -10 || true

hr "throttled flags now"
if command -v vcgencmd >/dev/null; then
  T=$(vcgencmd get_throttled | cut -d= -f2)
  echo "get_throttled=$T  temp=$(vcgencmd measure_temp | cut -d= -f2)  core=$(vcgencmd measure_volts core | cut -d= -f2)"
  v=$((T))
  for bit in "0:UNDER-VOLTAGE NOW" "1:ARM frequency capped now" "2:THROTTLED NOW" "3:soft temp limit now" \
             "16:under-voltage has occurred since boot" "17:freq capping has occurred" "18:throttling has occurred" "19:soft temp limit has occurred"; do
    (( v >> ${bit%%:*} & 1 )) && echo "  ${bit#*:}"
  done
  [ "$v" -eq 0 ] && echo "  all clear since boot"
else
  echo "vcgencmd not found (not a Raspberry Pi?)"
fi

hr "NetworkManager"
nmcli -f STATE,CONNECTIVITY general 2>/dev/null
nmcli device status 2>/dev/null
echo "profiles (FILENAME in /etc = persistent, /run = lost at reboot):"
nmcli -t -f NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY,FILENAME connection show 2>/dev/null | sed 's/^/  /'
ls -la /etc/NetworkManager/system-connections/ 2>/dev/null | sed 's/^/  /'
for y in /etc/netplan/*.yaml; do [ -e "$y" ] && echo "  netplan: $y ($(stat -c %s "$y") bytes)"; done
echo "NetworkManager, boot $B (last 10 lines):"
journalctl -q -b "$B" -u NetworkManager -n 10 --no-pager -o short-iso 2>/dev/null

hr "pit services"
for u in gokart-pit gokart-kiosk gokart-check.timer; do
  printf '  %-20s %s (restarts: %s)\n' "$u" "$(systemctl is-active "$u")" "$(systemctl show -p NRestarts --value "$u" 2>/dev/null)"
done
journalctl -q -b 0 -t gokart-check -n 5 --no-pager -o short-iso 2>/dev/null
