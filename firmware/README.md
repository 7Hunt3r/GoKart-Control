# Firmware

Two integrated Arduino sketches plus shared libraries. The tested PlatformIO
telemetry recovery image remains in `heltec/`; removable experiments remain in
`bench-control/`. See [integration guide](../docs/integration.md). **Status: compiles; not yet
run on hardware** except where this file says otherwise. Everything here follows the
owner's reliability rule in `docs/requirements.md`: a kart never writes to its VESC
because a link dropped or returned; boot and reconnect are read-only.

| Folder | Board | Role |
| --- | --- | --- |
| `kart-heltec-v4/` | Heltec WiFi LoRa 32 V4.3 + Quectel L76K | Kart supervisor: VESC link, GPS, LoRa, OLED, USB JSON for the kart Pi, WiFi OTA |
| `base-tbeam-s3/` | LilyGo T-Beam S3 Core (Supreme) | Pit LoRa modem: receives all karts, transmits commands, USB JSON for the Pi 4, WiFi OTA |
| `libraries/GoKartProtocol/` | both | Packet structs, radio parameters, slot timing, JSON encoder |
| `libraries/VescLink/` | kart | VESC 6.00 UART framing: FW_VERSION, GET_VALUES, GET_MCCONF (read-only), SET_MCCONF_TEMP with ack |

The old M5Stack Core2 dashboard is the ancestor of the VESC side (same temp-limit
command, same three modes). It depended on a hand-edited VescUart library; this code
has its own framing so a stock Arduino install builds it.

## Build and flash

Needs arduino-cli with `esp32:esp32` 3.3.x and these libraries installed through the
Library Manager: RadioLib 7.x, TinyGPSPlus, U8g2, XPowersLib, ArduinoJson 7,
NimBLE-Arduino 2.x (BLE variant only).

```sh
cd firmware
./build.sh kart                                   # compile kart (VESC over BLE, fleet default)
./build.sh kart-uart                              # compile kart with the wired UART transport
./build.sh base                                   # compile base station
./build.sh base upload /dev/cu.usbmodemXXXX       # flash over USB
./build.sh kart ota gokart-1.local [otapassword]  # ArduinoOTA once the board is on WiFi
```

Board settings are inside `build.sh` (Heltec V4: USB CDC on boot, 16 MB flash, 3 MB
app partitions with OTA; T-Beam S3: ESP32S3 Dev Module, USB CDC, 8 MB, OTA-capable
partitions). The Arduino IDE works too: pick the same board and options, and add
`firmware/libraries` to your sketchbook libraries folder or symlink the two folders.

## Build on a new machine

```sh
brew install arduino-cli                      # macOS; Linux: see arduino.github.io/arduino-cli
arduino-cli config init
arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index && arduino-cli core install esp32:esp32@3.3.8
arduino-cli lib install RadioLib TinyGPSPlus U8g2 XPowersLib ArduinoJson NimBLE-Arduino
git clone -b eamon/firmware-lora-starter git@github.com:colinfost3r/GoKart-Control.git
cd GoKart-Control/firmware
./build.sh kart                               # Heltec V4.3, VESC over BLE
./build.sh kart upload /dev/cu.usbmodemXXXX
```

The board's native USB resets into the bootloader on its own when it is running an
Arduino hardware-CDC image (Colin's bench image and these builds both are). If the
upload says "No serial data received", hold BOOT while plugging the cable in.

## Over-the-air updates

Both boards store WiFi credentials in flash, never in source. Over USB:

```
wifi <ssid> <password>
otapw <password>        # required for integrated kart OTA
reboot
```

The integrated kart requires a non-empty `otapw` and local `maintenance on`
with a verified, fresh, stationary VESC and idle commands. It pauses BLE/LoRa
without writing the VESC and opens authenticated OTA for ten minutes. Use
`maintenance off` to close it. OTA is off after reboot. The base's existing OTA
workflow is unchanged. See [integration guide](../docs/integration.md#maintenance-ota).

## Kart wiring (Heltec V4.3)

| Signal | Heltec pin | Notes |
| --- | --- | --- |
| VESC BLE (default) | none | Connects only to the address pinned with `vescmac AA:BB:CC:DD:EE:FF`. Unpinned: `blescan` lists every `VESC BLE UART` in range with RSSI. A VESC already connected to another device does not advertise. |
| VESC UART (`kart-uart` build) | GPIO48 = RX (from VESC TX), GPIO47 = TX (to VESC RX), GND | 3.3 V logic, 115200 baud (`app_uart_baudrate` in the baseline). The VESC COMM port is currently used by its BLE module; that module comes off. **Proposed wiring, not an approved change** per Colin's architecture note; the BLE build exists for the alternative. |
| GPS | Heltec GNSS connector, L76K add-on | Power GPIO34 (active low), reset 42, wake 40, PPS 41, UART on 38/39. The firmware auto-detects 9600/38400/115200 and a swapped TX/RX. |
| LoRa | on-board SX1262 + KCT8103L front end | Front-end power GPIO7, enable GPIO2, TX/RX select GPIO5, DIO2 drives the amplifier's mode pin. From the V4.3 schematic and Meshtastic's heltec_v4 variant. |
| OLED | on-board | Vext GPIO36 active low. |
| Power | USB-C 5 V | Do not power the Heltec from the VESC's 3.3 V COMM pin; the amplifier draws too much on transmit. A regulated 5 V from the kart's accessory supply, independent of the display Pi, is the target. |

Transmit power: the SX1262 is set to 10 dBm and the V4.3 front end adds roughly
7-11 dB. Measure before raising it.

## Base wiring (T-Beam S3 Core)

Nothing to wire: SX1262, u-blox GNSS, AXP2101 power manager and the optional SH1106
OLED are on board. USB-C to the pit host. The AXP2101 rails are enabled at boot using
the map Meshtastic uses for this board (GNSS ALDO4, LoRa ALDO3, OLED/sensors ALDO1).
If your Supreme has an LR1121 radio instead of an SX1262 the radio init fails and the
status line says so; that variant is not handled yet.

## Protocol in one paragraph

Integrated karts default to two 46-byte telemetry packets per second at 915 MHz,
SF7, BW500, CR4/5. Both supported rates (`rate 1|2`) use the same 50 ms ID slots;
2 Hz adds a second round 500 ms later. Slots follow GPS PPS or the pit beacon;
without sync, fallback is jittered and may collide. The base sends a 22-byte
beacon and 14-byte commands after 905 ms. Update base and karts together; old
100 ms slot schedules are incompatible. Local VESC polling and USB display
output are 5 Hz. Colin's recovery image uses the same packet/RF settings at
2 Hz but is unslotted; the base also decodes historical 47-byte frames. Commands carry a sequence number that the kart echoes with a
result code in its next telemetry; the host retries until it sees the echo. See
`libraries/GoKartProtocol/GoKartProtocol.h` and `base-tbeam-s3/SERIAL_PROTOCOL.md`.

## What the kart does with a command

First commission the BLE MAC (`vescmac`) and VESC UUID (`vescuuid HEX24`). Writes
require firmware 6.00 and the pinned UUID; a matching advertisement alone is
insufficient. `status` reports identity verification. See the per-kart setup in
[the integration guide](../docs/integration.md#commission-the-integrated-node-before-command-testing).

- Each step requests fresh GET_MCCONF, copies the live unrelated limits, sends
  `COMM_SET_MCCONF_TEMP` with `store=0`, requests an ack, and verifies the complete
  transmitted subset plus unchanged battery-current limits. Timeout/mismatch
  ends the transaction with unknown; it is not automatically retried.
- SET_MODE uses the historical ECO 80/140, NORMAL 100/140, SPORT 1.0 current scales.
  These are prototype behavior, not the owner's final top-speed profiles.
- STOP clamps each 0.5 / 0.2 / 0 step to the newly observed scale, so it
  never increases the allowance. The ramp ends on any failed step. STOPPED is
  reported only after the observed scale is zero. The 150 ms step interval is
  a minimum; fresh reads/ack/readback add latency, so this is not a guaranteed
  half-second stop. Mechanical brakes are unchanged.
- START re-applies the requested mode or the scale freshly captured before STOP.
  It does not request wheel movement. No configuration write occurs on boot,
  reconnect or communication loss, and pending transactions do not resume.
- STOP may supersede an active transaction, whose outcome becomes unknown. Other
  overlaps are rejected; recent duplicate requests report their cached result.
- `ENABLE_VESC_WRITES=1` is still this integrated command build's default, with
  identity/freshness gates. A `0` build rejects writes. Colin's installed
  `firmware/heltec` remains telemetry-only and has no command/write path.

These safeguards are compiled/offline-tested and not yet validated against a
real VESC through the pit. See [current handoff](../HANDOFF.md).

## GET_MCCONF field offsets

The kart reads live limits straight out of the 482-byte firmware-6.00 motor
configuration reply. Offsets (from the command byte) were computed from the pinned
schema `third_party/vesc_tool/6.00/parameters_mcconf.xml` with the same type rules as
`tools/vesc/decode.py`, and the reply's schema signature `2e43a161` is checked
before anything is trusted:

| Field | Offset | Encoding |
| --- | --- | --- |
| l_current_max / min | 9 / 13 | float32 auto |
| l_in_current_max / min | 17 / 21 | float32 auto |
| l_abs_current_max | 25 | float32 auto |
| l_min_erpm / l_max_erpm | 29 / 33 | float32 auto |
| l_battery_cut_start / end | 55 / 59 | float32 auto |
| l_min_duty / l_max_duty | 74 / 76 | int16 / 10000 |
| l_watt_max / l_watt_min | 78 / 82 | float32 auto |
| l_current_max_scale / min_scale | 86 / 88 | int16 / 10000 |
| si_motor_poles | 452 | uint8 |
| si_gear_ratio / si_wheel_diameter | 453 / 457 | float32 auto |
| si_battery_cells / si_battery_ah | 462 / 463 | uint8 / float32 auto |

## Verified vs not

- Verified: both sketches compile for their boards (arduino-cli, esp32 3.3.8);
  the temp-limit payload layout matches bldc `release_6_00` `comm/commands.c`
  (battery limits optional, read only when present); command ids 4/14/48 match
  the 6.00 enum; mcconf offsets computed from the pinned schema.
- Verified on hardware (2026-11-05, Eamon's Heltec V4.3, kart id 2): both kart builds boot;
  LoRa transmit received by the T-Beam at 1 Hz with zero loss on the bench; GPS detected at
  9600 baud; pit commands received and answered (`ping` applied, `stop` no_vesc without a
  VESC); WiFi join and ArduinoOTA update; BLE scan runs without blocking the radio.
- Verified on hardware (2026-11-05, Eamon's T-Beam S3 Core): the base firmware
  boots, the AXP2101 rail map is right (`pmu ok`), the SX1262 initialises (`radio ok`),
  the GNSS is found at 9600 baud, and `stat`/`base`/`status` lines flow over USB.
  The board shipped running Meshtastic; the first flash needed a 1200-baud touch
  to reach the bootloader, after which it enumerates as "USB JTAG/serial debug
  unit" and normal uploads work.
- Not verified: GPS fix/PPS, real VESC STOP/START with the new guards,
  modified maintenance OTA, field coverage, UART wiring and power isolation.
  Earlier kart-2 radio/ping/OTA results above are collaborator-reported. See the
  shared handoff for the installed kart-1 state and next reception test.

## Integration validation

See [tested toolchain and native/simulator checks](../docs/integration.md#build-and-test).
The native tests exercise the real VESC parser and write transaction against the
captured identity/configuration, including lower-than-requested-step STOP, preservation of
non-default limits, wrong identity, missing replies, disconnect, readback mismatch,
transport failure and timer rollover. Compilation/tests do not deploy firmware.
