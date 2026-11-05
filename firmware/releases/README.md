# Prebuilt images

Built from this branch with `firmware/build.sh` (arduino-cli, esp32 core 3.3.8). No
credentials inside: WiFi/OTA settings live in the board's flash and are entered over
the USB console after flashing. Rebuild from source if you prefer; the hash lets you
check what you got.

| File | Board | Build | SHA-256 |
| --- | --- | --- | --- |
| `kart-heltec-v4-ble-0.1.0.merged.bin` | Heltec WiFi LoRa 32 V4.3 | kart node, VESC over BLE, writes enabled, 2 Hz, 16777216 bytes | `46db276d41ab990bbc67fb05c9782261ad58cec95ed84bc10428436b4b3d76d6` |

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
