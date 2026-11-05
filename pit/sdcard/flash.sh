#!/bin/bash
# Flash Raspberry Pi OS Lite (Trixie, arm64) + GoKart pit first-boot config to a micro SD card.
#
#   pit/sdcard/flash.sh /dev/diskN        (find N with: diskutil list external physical)
#
# 1. generates build/{user-data,network-config,meta-data} from secrets.env (pit/ app rides inside user-data)
# 2. refuses anything that is not an external/SD, physical, removable, writable disk; shows what is on it
# 3. writes + verifies the image with Raspberry Pi Imager --cli (macOS asks for an admin password)
# 4. mounts the FAT boot partition and drops in the cloud-init files
# 5. verifies what landed on the card and ejects it
# Adapted from ~/studebaker-headunit/flash.sh (same checks, same Imager invocation).
set -euo pipefail
cd "$(dirname "$0")"

# The image is shared with the studebaker kit (already downloaded and SHA-256 verified there).
IMG="${GOKART_IMG:-$HOME/studebaker-headunit/images/2026-10-06-raspios-trixie-arm64-lite.img.xz}"
IMG_URL=https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06/2026-10-06-raspios-trixie-arm64-lite.img.xz
IMG_SHA256=483db18a48da399b5b7022ffae9a07bc6d99daab30e593aedac09b69ff422843            # of the .img.xz download
IMG_EXTRACTED_SHA256=b8a393dc1c3701450b5fffbfe74368a98fb6eab7415ce456cc0cead886ea8780  # of the decompressed .img (what Imager checks)
IMAGER="/Applications/Raspberry Pi Imager.app/Contents/MacOS/rpi-imager"

DISK="${1:-}"
if [[ ! "$DISK" =~ ^/dev/disk[0-9]+$ ]]; then
  echo "usage: $0 /dev/diskN     (find N with: diskutil list external physical)"; exit 2
fi
[ -x "$IMAGER" ] || { echo "Raspberry Pi Imager not found at: $IMAGER"; exit 2; }

# --- 1. config ---------------------------------------------------------------
python3 gen_cloudinit.py
PI_USER="$(cat build/pi-user)"
PI_HOST="$(cat build/pi-hostname)"

# --- 2. disk safety ------------------------------------------------------------
INFO="$(diskutil info "$DISK")"
[ "$DISK" != /dev/disk0 ] || { echo "REFUSING: $DISK is the Mac's boot drive"; exit 3; }
echo "$INFO" | grep -qE '^\s*(Device Location:\s+External|Protocol:\s+Secure Digital)' || { echo "REFUSING: $DISK is neither External nor an SD card"; exit 3; }
echo "$INFO" | grep -qE '^\s*Removable Media:\s+Removable' || { echo "REFUSING: $DISK is not Removable"; exit 3; }
echo "$INFO" | grep -qE '^\s*Virtual:\s+No' || { echo "REFUSING: $DISK is virtual"; exit 3; }
echo "$INFO" | grep -qE '^\s*Media Read-Only:\s+No' || { echo "REFUSING: $DISK is write-protected (slide the LOCK switch on the SD adapter)"; exit 3; }
SIZE="$(echo "$INFO" | grep -E '^\s*Disk Size:' | sed -E 's/.*Disk Size:\s+//')"
NAME="$(echo "$INFO" | grep -E 'Device / Media Name:' | sed -E 's/.*Name:\s+//')"
echo
echo "About to ERASE $DISK  ($NAME, $SIZE). Current contents:"
diskutil list "$DISK"
for part in $(diskutil list "$DISK" | awk '/[0-9]:/{print $NF}' | grep -E "^$(basename "$DISK")s[0-9]+$"); do
  MP="$(diskutil info "/dev/$part" | { grep -E '^\s*Mount Point:' || true; } | sed -E 's/.*Mount Point:\s+//')"
  if [ -n "$MP" ] && [ -d "$MP" ]; then
    echo "--- files on /dev/$part ($MP):"; ls -la "$MP" | head -25
  fi
done
echo
if [ "${FLASH_CONFIRM:-}" = "$DISK" ]; then
  CONFIRM="$DISK"; echo "FLASH_CONFIRM matches $DISK; proceeding without a prompt"
else
  read -r -p "Type the disk id ($DISK) to continue: " CONFIRM < /dev/tty
fi
[ "$CONFIRM" = "$DISK" ] || { echo "aborted"; exit 1; }

# --- 3. image + write ----------------------------------------------------------
if [ ! -f "$IMG" ]; then
  echo "downloading image..."; mkdir -p "$(dirname "$IMG")"
  curl -L --fail --retry 3 -o "$IMG.part" "$IMG_URL" && mv "$IMG.part" "$IMG"
fi
echo "$IMG_SHA256  $IMG" | shasum -a 256 -c - || { echo "image checksum mismatch; delete $IMG and rerun"; exit 4; }
diskutil unmountDisk "$DISK"
# Imager hashes the DECOMPRESSED stream; passing the .xz hash makes it write data but withhold the partition table.
"$IMAGER" --cli --sha256 "$IMG_EXTRACTED_SHA256" --disable-eject "$IMG" "$DISK"

# --- 4. boot partition ---------------------------------------------------------
BOOT=""
for i in $(seq 1 40); do
  diskutil mount "${DISK}s1" >/dev/null 2>&1 || true
  BOOT="$(diskutil info "${DISK}s1" | grep -E '^\s*Mount Point:' | sed -E 's/.*Mount Point:\s+//')"
  [ -n "$BOOT" ] && [ -d "$BOOT" ] && break
  sleep 2
done
[ -n "$BOOT" ] && [ -f "$BOOT/config.txt" ] || { echo "could not mount boot partition ${DISK}s1"; exit 5; }
echo "boot partition at $BOOT"
cp build/user-data build/network-config build/meta-data "$BOOT/"

# --- 5. verify + eject ---------------------------------------------------------
echo "--- verify"
head -1 "$BOOT/user-data" | grep -q '^#cloud-config$' || { echo "user-data header missing"; exit 6; }
grep -q "name: \"$PI_USER\"" "$BOOT/user-data" || { echo "user-data missing user"; exit 6; }
grep -q "hostname: \"$PI_HOST\"" "$BOOT/user-data" || { echo "user-data missing hostname"; exit 6; }
grep -q 'path: /opt/gokart-pit-payload.tar.gz' "$BOOT/user-data" || { echo "user-data missing app payload"; exit 6; }
grep -q 'wifis:' "$BOOT/network-config" || { echo "network-config bad"; exit 6; }
grep -q '^instance-id:' "$BOOT/meta-data" || { echo "meta-data bad"; exit 6; }
cmp -s build/user-data "$BOOT/user-data" || { echo "user-data on card differs from build/"; exit 6; }
find "$BOOT" -name '._*' -delete 2>/dev/null || true
sync
diskutil eject "$DISK"
echo
echo "DONE. Card is ejected; put it in the Pi 4 (touchscreen on HDMI0 + touch USB), power on."
echo "First boot installs packages and reboots once (allow ~10 minutes), then the kiosk appears."
echo "  ssh $PI_USER@$PI_HOST.local       web: http://$PI_HOST.local:8080/"
