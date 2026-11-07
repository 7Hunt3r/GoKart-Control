#!/bin/sh
# Make the systemd journal persistent (survives reboots) and cap it at 200 MB.
# Raspberry Pi OS ships /usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf
# (Storage=volatile), so /var/log/journal alone is not enough: this drop-in sorts after it.
# Idempotent; run as root by install.sh and sdcard/firstboot.sh.
set -u
CONF=/etc/systemd/journald.conf.d/90-gokart-persistent.conf
WANT='# Written by GoKart pit tools/journal_persist.sh: keep logs across reboots (see why_rebooted.sh).
[Journal]
Storage=persistent
SystemMaxUse=200M'
mkdir -p /etc/systemd/journald.conf.d /var/log/journal
systemd-tmpfiles --create --prefix /var/log/journal 2>/dev/null || true
if [ "$(cat "$CONF" 2>/dev/null)" != "$WANT" ]; then
  printf '%s\n' "$WANT" > "$CONF"
  systemctl restart systemd-journald && journalctl --flush 2>/dev/null
  echo "journal: persistent, SystemMaxUse=200M ($CONF written, journald restarted)"
else
  journalctl --flush 2>/dev/null || true
  echo "journal: persistent already ($CONF)"
fi
