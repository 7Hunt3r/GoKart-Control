# Prebuilt images

> **Current images: kart 0.2.4, base and repeater 0.2.1** (2026-11-07, below). `kart-heltec-v4-ble-0.1.0.merged.bin` and the 0.2.0
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
- `kart-heltec-v4-ble-0.2.1.merged.bin` — SX1262 drive into the KCT8103L front end 16 dBm (about 27 dBm radiated; 0.2.0 used 10 -> ~23), boosted receive gain, stored console `txpower 0..22`, `tx_dbm` / `tx_radiated_dbm` in `status`. **Superseded by 0.2.2** before it was flashed (its radio change is in 0.2.2). After flashing, watch the kart Pi's `dmesg -w` for USB resets while it transmits; `txpower 14` or lower if the Pi's 5 V cannot carry the 750 mA peaks.
  SHA-256 `b24703a90fee717ce627b0815758af39d94b2babd6ea4582739b35998fb62c01`

## 0.2.2 (2026-11-07 evening, kart only: modes are speed caps)

Radio unchanged (interoperates with the 0.2.0 / 0.2.1 base and repeater, which stay on 0.2.1).

- `kart-heltec-v4-ble-0.2.2.merged.bin` — ECO / NORMAL / SPORT are now a top-speed cap plus a torque scale (`config.h MODES[]`: 15 mph + 0.50, 25 mph + 0.75, uncapped + 1.0; the old 80 / 100 / 140 A current-only modes were indistinguishable on the kart), the cap computed from the controller's own poles / gear / wheel like SLOW; a BOOST above the mode's scale lifts the cap one mode up for its duration; `mode_actual` and the write verify check the ERPM bound as well as the scale; a capped mode is `rejected` on a controller without drivetrain numbers; `cap_mph` / `cap_erpm` / `baseline_suspect` in `status`; the OLED shows the cap. **Flashed on kart 2** 2026-11-07 20:14 over WiFi (`python3 ~/Library/Arduino15/packages/esp32/hardware/esp32/3.3.8/tools/espota.py -i 192.168.151.44 -p 3232 -f firmware/build/kart-heltec-v4/kart-heltec-v4.ino.bin`; OTA takes the app image `kart-heltec-v4.ino.bin` from the same build, not the merged image). The mode read-back test is still owed (the VESC was off). Test wheels-up first: after `mode 0`, `status` must show `scale_max` 0.500 and `max_erpm` 6115 on the baseline drivetrain (`mode 1`: 0.750 / 10191; `mode 2`: 1.000 and the controller's own bound back); then the 0.2.1 radio-power watch (`dmesg -w` on the kart Pi for USB resets).
  SHA-256 `c3ecc33fe881f3ccd3b0636e24002a6a690b7a223e1d60b4671565ec187a6b20`

## 0.2.3 (2026-11-09 evening, kart only: speed calibration + pit mode releases SLOW)

Radio unchanged (interoperates with the 0.2.1 base and repeater).

- `kart-heltec-v4-ble-0.2.3.merged.bin` — the mode speed caps are now multiplied by a per-kart speed
  calibration (`config.h SPEED_CAL_DEFAULT 2.48`, console `speedcal X|clear`, status `speed_cal`):
  measured on kart 2 by GPS vs ERPM, the controller's si_* numbers are off by 2.48x, so the 15 / 25 mph
  caps were really 6 / 10 mph and SLOW 5 was 2 mph. A mode command from the pit now releases SLOW (the
  driver's own mode buttons still do not; START still does). Flashed to kart 2 by OTA 2026-11-09.
  SHA-256 `f04cba51e704c429c7cb1106acd147ea452e686c5bd6cf218554d107d55f0ab7`

## 0.2.4 (2026-11-09 night, kart only: timed SLOW for the game's mud)

Radio unchanged (interoperates with the 0.2.1 base and repeater).

- `kart-heltec-v4-ble-0.2.4.merged.bin` — USB console `slow MPH SECS` (SECS 1-60) caps speed for SECS
  seconds and then returns by itself to whatever cap was in force before (a pit SLOW comes back); a timed
  slow never raises a pit cap that is already lower. The kart display uses it for mud (`slow 5 4`): "mud
  mode should actually make the kart go into slow mode". START and a pit mode command still clear every
  SLOW. Flashed to kart 2 by OTA 2026-11-09.
  SHA-256 `2dfc5913466da0c4762649b516dc305920029c2c811645e9caac4caa764c0969`
