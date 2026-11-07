#!/bin/sh
# Run every 30 s by gokart-check.timer (root). Restarts a service that systemd thinks is
# running but is not doing its job; systemd's Restart=always already covers crashes.
#  - gokart-kiosk active but no Chromium process under its cage for 2 checks in a row -> restart
#  - gokart-pit active but http://127.0.0.1:8080/healthz failing for 3 checks in a row -> restart
# Each restart is logged to the journal (tag gokart-check). The unit-age gates (60 s / 30 s) plus the
# miss counts mean at most one restart per ~2 min; after 3 in an hour, at most one per 10 min.
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
restart() {  # restart <unit> <why>: at most 3 restarts an hour, then one per 10 min (never a tight loop)
  now=$(cut -d. -f1 /proc/uptime); f="$ST/restarts-$1"
  recent=$(awk -v n="$now" '$1 > n - 3600' "$f" 2>/dev/null); cnt=$(printf '%s\n' "$recent" | grep -c .)
  last=$(printf '%s\n' "$recent" | tail -1)
  if [ "$cnt" -ge 3 ] && [ -n "$last" ] && [ $(( now - last )) -lt 600 ]; then
    log "$1: $2, but already restarted $cnt times in the last hour: waiting (next try >= 10 min after the last)"; return
  fi
  printf '%s\n' $recent "$now" > "$f"
  log "$1: $2, restarting"; systemctl restart "$1"
}

if systemctl is-active --quiet gokart-kiosk && [ "$(age_s gokart-kiosk)" -ge 60 ]; then
  # Chromium moves itself into a systemd user scope (app-org.chromium.Chromium-N.scope), so it is NOT in
  # the kiosk unit's cgroup: look for it in cage's process tree instead. (The cgroup check restarted a
  # healthy kiosk every ~2 min on the pit Pi from 20:47 to 21:30 on 2026-11-06.)
  main=$(systemctl show -p MainPID --value gokart-kiosk 2>/dev/null)
  found=$(ps -e -o pid=,ppid=,comm= | awk -v m="${main:-0}" '{p[$1]=$2; c[$1]=$3}
    END {n=0; for (x in c) if (c[x] ~ /^chrom/) {y=x; for (i=0; i<64 && y>1; i++) {if (y==m) {n++; break}; y=p[y]}} print (n>0)}')
  if [ "$found" = 1 ]; then ok kiosk
  elif miss kiosk 2; then ok kiosk; restart gokart-kiosk "no Chromium process for 2 checks"
  fi
else ok kiosk
fi

if systemctl is-active --quiet gokart-pit && [ "$(age_s gokart-pit)" -ge 30 ]; then
  if curl -fs -m 5 -o /dev/null http://127.0.0.1:8080/healthz; then ok pit
  elif miss pit 3; then ok pit; restart gokart-pit "/healthz failed 3 checks in a row"
  fi
else ok pit
fi
exit 0
