# Firmware

Two Arduino sketches plus two small shared libraries. **Status: compiles; not yet
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
./build.sh kart                                   # compile kart (wired UART to the VESC)
./build.sh kart-ble                               # compile kart with the BLE transport
./build.sh base                                   # compile base station
./build.sh base upload /dev/cu.usbmodemXXXX       # flash over USB
./build.sh kart ota gokart-1.local [otapassword]  # ArduinoOTA once the board is on WiFi
```

Board settings are inside `build.sh` (Heltec V4: USB CDC on boot, 16 MB flash, 3 MB
app partitions with OTA; T-Beam S3: ESP32S3 Dev Module, USB CDC, 8 MB, OTA-capable
partitions). The Arduino IDE works too: pick the same board and options, and add
`firmware/libraries` to your sketchbook libraries folder or symlink the two folders.

## Over-the-air updates

Both boards store WiFi credentials in flash, never in source. Over USB:

```
wifi <ssid> <password>
otapw <password>        # optional ArduinoOTA password
reboot
```

They then appear as `gokart-N.local` (kart N) and `gokart-pit-radio.local` and accept
`./build.sh kart ota gokart-N.local` from any machine on the same network. Pit WiFi
is the intended place for this; it is not needed on the track.

## Kart wiring (Heltec V4.3)

| Signal | Heltec pin | Notes |
| --- | --- | --- |
| VESC UART | GPIO48 = RX (from VESC TX), GPIO47 = TX (to VESC RX), GND | 3.3 V logic, 115200 baud (`app_uart_baudrate` in the baseline). The VESC COMM port is currently used by its BLE module; that module comes off. **Proposed wiring, not an approved change** per Colin's architecture note; the BLE build exists for the alternative. |
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

Every kart transmits one 46-byte telemetry packet per second at 915 MHz, SF7, 250 kHz,
in a 100 ms slot chosen by kart id and aligned to the GPS PPS edge, so eight karts
fill 800 ms without colliding. The base sends 14-byte commands in the remaining
tail of the second. Commands carry a sequence number that the kart echoes with a
result code in its next telemetry; the host retries until it sees the echo. See
`libraries/GoKartProtocol/GoKartProtocol.h` and `base-tbeam-s3/SERIAL_PROTOCOL.md`.

## What the kart does with a command

- `SET_MODE n`: sends the baseline limits with only `l_current_max_scale` changed
  (ECO 80/140, NORMAL 100/140, SPORT 1.0, straight from the Core2 dashboard), asks the
  VESC for an ack, then re-reads GET_MCCONF and reports `applied` only if the live
  scale matches. Battery current limits are never sent.
- `STOP`: ramps the scale 0.6, 0.3, 0.1, 0 over about 1.5 s and holds. Mechanical
  brakes and hardwired throttle are untouched; the kart coasts.
- `START`: re-applies the requested mode, or the scale observed before the stop.
- Nothing is written at boot, on VESC reconnect, on WiFi/LoRa/GPS loss, or when a
  retransmitted command arrives. `mode_actual` comes from a GET_MCCONF re-read
  every 10 s, so the pit sees the truth, including after a VESC power cycle.
- Build with `-DENABLE_VESC_WRITES=0` for a telemetry-only kart; every write command
  is then answered `rejected`. The default build has writes enabled because the
  pit start/stop and mode buttons are the point; this is the one place this branch
  goes further than `docs/requirements.md` ("profile writes remain disabled until
  validated") and it is flagged for the owner's review.

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
- Not verified: anything on hardware. LoRa link, front-end control, GPS pin
  order, PPS slotting, VESC UART wiring, OLED, OTA, AXP2101 rail map, power draw.
  First bench steps: flash base, confirm `stat` lines and GPS fix on USB; flash
  kart with no VESC attached, confirm it appears in the base's `tele` lines;
  then attach the VESC over USB-powered bench and watch `status`.
