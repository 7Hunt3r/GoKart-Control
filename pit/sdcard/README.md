# Pit Pi 4 SD card kit

Builds a headless Raspberry Pi OS Lite (Trixie 2026-10-06, arm64) card for the
pit station. Adapted from the working studebaker-headunit kit (same cloud-init
approach, same `rpi-imager --cli` call, same disk safety checks).

**Status: the kit has not been booted on a Pi yet.** The generator and
flash.sh refusal checks were exercised on the Mac (see ../README.md).

## What first boot does

Raspberry Pi OS Trixie configures itself with cloud-init (NoCloud datasource
seeded from the FAT boot partition). flash.sh writes three files there:

- `user-data`: hostname `gokart-pit`, renames the default user to `pit` (hashed
  password), enables SSH with passwords, writes `/opt/gokart-pit/server/config.json`
  (pit web password), and carries the whole `pit/` folder as a base64 tarball.
  `runcmd` unpacks it to `/opt/gokart-pit` and runs `firstboot.sh`.
- `network-config`: netplan v2 Wi-Fi with two access points (`WIFI_SSID`, then
  the optional `WIFI2_SSID`), rendered by NetworkManager.
  This only gets the first boot online; `user-data` also writes the Wi-Fi list to
  `/etc/gokart-pit/wifi.env` (root, 0600) for the persistent keyfiles below.
- `meta-data`: a fresh instance-id, so cloud-init runs once per flash.

`firstboot.sh` then runs `../install.sh` (apt: python3-serial, cage, chromium,
fonts, avahi; systemd units; udev rule; kiosk PAM file; persistent journal), writes
persistent NetworkManager keyfiles in `/etc/NetworkManager/system-connections` so
`WIFI_SSID` is preferred (20) over the second network (10) with eth0 DHCP as a fallback
(see "If the pit reboots" in ../README.md), installs Tailscale only if `TAILSCALE_AUTHKEY` is set, scrubs the
Tailscale key and the web password from the boot partition copy of user-data,
and cloud-init reboots. After the reboot the kiosk opens on the HDMI screen.

Logs on the Pi: `/var/log/gokart-pit-firstboot.log`, `sudo cloud-init status --long`.

## Flash a card

1. `cp secrets.env.example secrets.env` (already done locally; it is gitignored)
   and fill in every empty value. `gen_cloudinit.py` refuses to build without
   `WIFI_PASSWORD`, `PI_PASSWORD` and `PIT_WEB_PASSWORD`.
2. Insert the card. `diskutil list external physical` and note `/dev/diskN`.
3. `./flash.sh /dev/diskN`. It refuses disk0 and anything not external/SD,
   removable, physical and writable, prints the card's partitions and top-level
   files, and makes you type the disk id. Raspberry Pi Imager then asks macOS
   for an admin password to write. The image is read from
   `~/studebaker-headunit/images/` (override with `GOKART_IMG=...`) and its
   SHA-256 is checked before writing; Imager verifies the decompressed write.
4. The script copies and verifies the seed files, then ejects the card.

Boot the Pi with the touchscreen connected (HDMI0, the port next to USB-C power;
touch USB to the screen's 5V IN + TOUCH port). Allow about 10 minutes for the
first boot (package installs + one reboot).

```sh
ping gokart-pit.local
ssh pit@gokart-pit.local
open http://gokart-pit.local:8080/
```

## Testing the generator without flashing

`GOKART_SECRETS=/path/to/other.env python3 gen_cloudinit.py` builds `build/`
from a different secrets file. Delete `build/` afterwards; it contains secrets.

## Known gaps

- Netplan matches SSIDs case-sensitively. Check the exact spelling of each network.
- `optional: false` on wlan0 makes boot wait for Wi-Fi (up to the
  NetworkManager-wait-online timeout) when neither network is in range. The
  pit services do not depend on Wi-Fi, so they still start after that wait.
- If the touchscreen provides no usable EDID, add
  `video=HDMI-A-1:1024x600@60` to the single line in `/boot/firmware/cmdline.txt`.
  Not applied by default because the L7's EDID behaviour has not been checked.
