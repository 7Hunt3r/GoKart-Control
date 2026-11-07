# Prebuilt images

> **Stale image.** `kart-heltec-v4-ble-0.1.0.merged.bin` predates the relay bit, `slow_mph`,
> BOOST, RACE, the 27-byte beacon and the 2026-11-06 slot/command fixes. Kart 1 (and any new
> kart) must be built from source until this table is refreshed: `cd firmware && ./build.sh kart
> upload /dev/cu.usbmodemXXXX`. To refresh: `./build.sh kart compile`, copy
> `build/kart-heltec-v4/kart-heltec-v4.ino.merged.bin` here under a new version name, update the
> row and the SHA-256 (`shasum -a 256 <file>`), and bump `FW_VERSION` in `kart-heltec-v4/config.h`.

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
