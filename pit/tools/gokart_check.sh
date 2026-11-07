#!/bin/sh
# Run every 30 s by gokart-check.timer (root). Restarts a service that systemd thinks is
# running but is not doing its job; systemd's Restart=always already covers crashes.
#  - gokart-kiosk active but no Chromium process in its cgroup for 2 checks in a row -> restart
#  - gokart-pit active but http://127.0.0.1:8080/healthz failing for 3 checks in a row -> restart
# Each restart is logged to the journal (tag gokart-check).
set -u
ST=/run/gokart-check
mkdir -p "$ST"
log() { echo "$*"; logger -t gokart-check "$*" 2>/dev/null || true; }
age_s() {   # seconds since the unit last became active
  t=$(systemctl show -p ActiveEnterTimestampMonotonic --value "$1" 2>/dev/null)
  up=$(cut -d. -f1 /proc/uptime)
  [ -n "$t" ] && [ "$t" -gt 0 ] 2>/dev/null && echo $(( up - t / 1000000 )) || echo 0
}
miss() {    # miss <name> <limit>: count a failed check, true when the limit is reached
  n=$(( $(cat "$ST/$1" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$ST/$1"; [ "$n" -ge "$2" ]
}
ok() { rm -f "$ST/$1"; }

if systemctl is-active --quiet gokart-kiosk && [ "$(age_s gokart-kiosk)" -ge 60 ]; then
  found=0
  for p in $(cat /sys/fs/cgroup/system.slice/gokart-kiosk.service/cgroup.procs 2>/dev/null); do
    case "$(cat /proc/$p/comm 2>/dev/null)" in chromium*|chrome*) found=1; break ;; esac
  done
  if [ "$found" = 1 ]; then ok kiosk
  elif miss kiosk 2; then log "kiosk: no Chromium process for 2 checks, restarting gokart-kiosk"; ok kiosk; systemctl restart gokart-kiosk
  fi
else ok kiosk
fi

if systemctl is-active --quiet gokart-pit && [ "$(age_s gokart-pit)" -ge 30 ]; then
  if curl -fs -m 5 -o /dev/null http://127.0.0.1:8080/healthz; then ok pit
  elif miss pit 3; then log "pit: /healthz failed 3 checks in a row, restarting gokart-pit"; ok pit; systemctl restart gokart-pit
  fi
else ok pit
fi
exit 0
