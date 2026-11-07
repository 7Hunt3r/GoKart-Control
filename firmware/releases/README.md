# Prebuilt images

> **Current images: 0.2.1** (2026-11-07, below). `kart-heltec-v4-ble-0.1.0.merged.bin` and the 0.2.0
> set are kept for rollback only; 0.1.0 predates the relay bit, `slow_mph`, BOOST, RACE and the
> 27-byte beacon. To refresh: `./build.sh kart compile` (also `base`, `repeater`), copy
> `build/<sketch>/<sketch>.ino.merged.bin` here under a new version name, add the row and the
> SHA-256 (`shasum -a 256 <file>`), and bump `FW_VERSION` in each `config.h`.

Built from this branch with `firmware/build.sh` (arduino-cli, esp32 core 3.3.8). No
credentials inside: WiFi/OTA settings live in the board's flash and are entered over
the USB console after flashing. Rebuild from source if you prefer; the hash lets you
check what you got.

| File | Board | Build | SHA-256 |
| --- | --- | --- | --- |
| `kart-heltec-v4-ble-0.1.0.merged.bin` | Heltec WiFi LoRa 32 V4.3 | kart node, VESC over BLE (MTU-chunked writes), per-controller limits, SLOW + brake request, 2 Hz, 16777216 bytes | `9f2994756dc42af61051fbd7cfc24a81caafd142c01f7f0955c53c4353a30799` |

## Flash with esptool (PlatformIO's or pip's)

```sh
esptool.py --chip esp32s3 --port /dev/cu.usbmodemXXXX --baud 921600 \
  --before default_reset --after hard_reset write_flash 0x0 \
  firmware/releases/kart-heltec-v4-ble-0.1.0.merged.bin
```

The merged image includes bootloader, partition table and app, so it goes to address 0.
A board running an Arduino hardware-CDC image (Colin's bench image included) resets
into the bootloader by itself; if esptool says "No serial data received", hold BOOT
while plugging the USB cable in.

## Configure over the USB console (115200, newline-terminated)

```
id 1                                    # fleet id for this kart
vescuuid 2c004c000951343037353739       # the baseline kart's controller UUID (from the 2026-11-05 capture)
blescan                                 # lists every "VESC BLE UART" in range with RSSI (VESC must be powered and not connected to anything else)
vescmac AA:BB:CC:DD:EE:FF               # pin the VESC's address from that list; the board connects only to it
wifi <ssid> <password>                  # optional, for over-the-air updates
reboot
status                                  # vesc_fw 6.00, id_ok true, sync pps/beacon, rate 2
```

Nothing is written to the VESC until a command arrives (pit kiosk or console
`stop` / `start` / `mode 0..2`). First test with the drive wheels off the ground.
Roll back any time with your own image.

## 0.2.0 (2026-11-06 evening, built from this branch after the firmware review pass)

- `kart-heltec-v4-ble-0.2.0.merged.bin` — flash at 0x0 (16 MB layout, BLE transport). Includes the relay bit, slow_mph, BOOST, RACE (grid hold / finish), RATE, the 27-byte beacon with race positions, fixed-pin 9600/5 Hz GPS with recovery, and the review fixes (command history dedupe, write gating, slot timing).
- `base-tbeam-s3-0.2.0.merged.bin` — flash at 0x0 (8 MB layout). Beacon with race state/positions, repeater heartbeat decode, schedule-based command placement, host `race`/`issued` lines.
- `repeater-tbeam-s3-0.2.0.merged.bin` — flash at 0x0. Accepts 27-byte beacons; 400 ms dedupe; late-copy drop. Fleet must run `rate 1` while a repeater is in use.
Bench 2026-11-06 21:33: kart 2 + base on these builds, live bench 16 pass / 0 loss / ping + race + writes outcomes as expected (no VESC linked).

## 0.2.1 (2026-11-07, radio power)

Same radio parameters as 0.2.0 (915.0 MHz, BW500, SF7), so 0.2.0 and 0.2.1 boards interoperate.

- `repeater-tbeam-s3-0.2.1.merged.bin` — SX1262 at 22 dBm (was 20), boosted receive gain, `tx_dbm` in the stat line, `FW_VERSION` finally reports the real version. **Flashed** on the repeater 2026-11-07 12:22 (bench: `rx_bad` 0, heard = relayed, heartbeat decoded by the pit).
  SHA-256 `6a49788d5044981e96b612a0de24ae2ee685dc37fd7447038e9c5b20b4d42fdd`
- `base-tbeam-s3-0.2.1.merged.bin` — same two radio changes, `tx_dbm` in `stat`. **Flashed** on the pit base 2026-11-07 12:25 from the pit Pi (`~/flash_base.sh`, esptool 5.6 in `~/esptool-venv`; the Debian `esptool` package has no ESP32-S3 stub).
  SHA-256 `19976e0be07ed6bed5d6d9375106b120a5a0e76ccb01ce470b6c7ddccbdf264e`
- `kart-heltec-v4-ble-0.2.1.merged.bin` — SX1262 drive into the KCT8103L front end 16 dBm (about 27 dBm radiated; 0.2.0 used 10 -> ~23), boosted receive gain, stored console `txpower 0..22`, `tx_dbm` / `tx_radiated_dbm` in `status`. **Not flashed yet** (kart 2 was off). After flashing, watch the kart Pi's `dmesg -w` for USB resets while it transmits; `txpower 14` or lower if the Pi's 5 V cannot carry the 750 mA peaks.
  SHA-256 `b24703a90fee717ce627b0815758af39d94b2babd6ea4582739b35998fb62c01`
