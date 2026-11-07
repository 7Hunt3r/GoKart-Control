# Firmware

Three Arduino sketches plus two small shared libraries. **Status: all three run on bench
hardware** (kart 2 Heltec V4.3, pit T-Beam S3, repeater T-Beam S3; see "Verified vs not").
Everything here follows the owner's reliability rule in `docs/requirements.md`: a kart never
writes to its VESC because a link dropped or returned; boot and reconnect are read-only. The
write invariants are listed at the top of `kart-heltec-v4/kart-heltec-v4.ino` (W1-W6).

| Folder | Board | Role |
| --- | --- | --- |
| `kart-heltec-v4/` | Heltec WiFi LoRa 32 V4.3 + Quectel L76K | Kart supervisor: VESC link, GPS, LoRa, OLED, USB JSON for the kart Pi, WiFi OTA |
| `base-tbeam-s3/` | LilyGo T-Beam S3 Core (Supreme) | Pit LoRa modem: receives all karts, transmits commands and the 1 Hz beacon, USB JSON for the Pi 4, WiFi OTA |
| `repeater-tbeam-s3/` | spare T-Beam S3 Core | Plug-anywhere one-hop LoRa repeater for dead zones (see "Repeater") |
| `libraries/GoKartProtocol/` | all three | Packet structs, radio parameters, airtime + slot plan (with compile-time checks), JSON encoder |
| `tests/test_protocol.py` | host | `python3 -I firmware/tests/test_protocol.py`: struct layout, nibble helpers, airtime/slot plan, JSON field order, Pi reader field names (compiles the header with the host C++ compiler when present) |
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
./build.sh repeater                               # compile the LoRa repeater
./build.sh all                                    # compile kart, base and repeater
./build.sh test                                   # host-side protocol tests (python3, no board needed)
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
otapw <password>        # optional ArduinoOTA password
reboot
```

They then appear as `gokart-N.local` (kart N) and `gokart-pit-radio.local` and accept
`./build.sh kart ota gokart-N.local` from any machine on the same network. Pit WiFi
is the intended place for this; it is not needed on the track.

## Kart wiring (Heltec V4.3)

| Signal | Heltec pin | Notes |
| --- | --- | --- |
| VESC BLE (default) | none | Connects only to the address pinned with `vescmac AA:BB:CC:DD:EE:FF`. Unpinned: `blescan` lists every `VESC BLE UART` in range with RSSI. A VESC already connected to another device does not advertise. |
| VESC UART (`kart-uart` build) | GPIO48 = RX (from VESC TX), GPIO47 = TX (to VESC RX), GND | 3.3 V logic, 115200 baud (`app_uart_baudrate` in the baseline). The VESC COMM port is currently used by its BLE module; that module comes off. **Proposed wiring, not an approved change** per Colin's architecture note; the BLE build exists for the alternative. |
| GPS | Heltec GNSS connector, L76K add-on | Power GPIO34 (active low), reset 42, wake 40, PPS 41, UART on 38/39. The firmware auto-detects 9600/38400/115200 and a swapped TX/RX. |
| LoRa | on-board SX1262 + KCT8103L front end | Front-end power GPIO7, enable GPIO2, TX/RX select GPIO5, DIO2 drives the amplifier's mode pin. From the V4.3 schematic and Meshtastic's heltec_v4 variant. |
| OLED | on-board | Vext GPIO36 active low. |
| Power | USB-C 5 V | Do not power the Heltec from the VESC's 3.3 V COMM pin; the amplifier draws too much on transmit. A regulated 5 V from the kart's accessory supply, independent of the display Pi, is the target. |

Transmit power (0.2.1, 2026-11-07): the SX1262 drives the KCT8103L front end at 16 dBm by
default, about 27 dBm radiated. Heltec's gain table for this part (carried in Meshtastic's
`LoRaFEMInterface.cpp`): drive 0-13 dBm adds 13 dB, 14-15 adds 12, 16-17 adds 11, 18 adds 10, 19
adds 9, 20 adds 8, 21 adds 7; the board is rated 28 +/- 1 dBm and tops out from 18 dBm drive.
The old 10 dBm setting radiated about 23 dBm. The datasheet puts supply current near 400 mA at
22 dBm and 750 mA at 27 dBm during the 23 ms packet, so a kart whose Heltec hangs off the Pi's
USB may see 5 V sag: `txpower 10..14` (stored) backs it off until the Heltec has its own 5 V.
Every build also turns on the SX1262's boosted receive gain (about 2 dB better sensitivity for
0.7 mA). The pit base and the repeater run their SX1262 at 22 dBm, its maximum (they have no
front end). Radio parameters themselves (BW500/SF7) are unchanged: see "Range" below.

Range: nothing beats antenna height and placement. The pit base wants a real 915 MHz omni
(3-6 dBi) on a mast above the trailer/people line with short low-loss coax; the repeater belongs
on a light pole or roof edge mid-track with a clear view of both the pit and the far side; the
kart antenna stays vertical on top of the dash, away from the frame and the driver's body. A
wider modem budget (SF8 at BW500, +2.5 dB) would double the packet airtime and no longer fits a
repeater copy in the 100 ms slot at 1 Hz, so it needs a shorter telemetry packet first (owner
decision). Keep BW500: a fixed 915 MHz channel narrower than 500 kHz falls outside the FCC
digital-modulation rule that allows this power.

## Kart USB console (115200, newline-terminated; `help` prints this list)

| Command | Effect |
| --- | --- |
| `status` | one `{"t":"status"}` line (radio, GPS, VESC limits, identity, `limits_ok`, `revert_missed`, `cap_mph` / `cap_erpm` (the speed cap the kart wants right now), `baseline_suspect` (the limits read at link-up look like this firmware's own caps: key the kart off/on), sync, heap) |
| `id N` | kart id 1-8 (stored; reboot renames the OTA host) |
| `txpower 0..22` | SX1262 drive into the front end in dBm (stored). Radiated is drive + gain from the table above: 10 = ~23 dBm, 14 = ~26, 16 = ~27 (default), 18+ = ~28. `status` shows `tx_dbm` and `tx_radiated_dbm` |
| `mode 0..2`, `stop`, `start`, `ping`, `slow [mph]`, `race [secs]` | same as the pit command (USB source, own seq numbers) |
| `boost SECS SCALE` | timed scale override, 1-30 s, 0.10-1.00 (game zones / mud) |
| `rate 1\|2` | report rate (default 2 Hz; use 1 Hz when the fleet relies on a repeater) |
| `wifi SSID PASS`, `otapw PASS` | stored in flash for OTA; reboot to apply |
| `vescmac AA:BB:..\|clear`, `blescan` | pin the VESC BLE address / list candidates |
| `vescuuid <24 hex>\|now\|clear` | pin the controller UUID; writes then wait for a matching FW_VERSION reply |
| `gpsline on\|off` | the 5 Hz `{"t":"gps"}` line (one per fix epoch) |
| `gpsreset`, `gpsrecover`, `gpscold`, `gpspin`, `gpsdump`, `gpstest BAUD` | GNSS diagnostics (block the loop for up to ~10 s; bench only) |
| `reboot` | restart |

A console line longer than 199 bytes is ignored with a log line (never executed truncated).

## Base wiring (T-Beam S3 Core)

Nothing to wire: SX1262, u-blox GNSS, AXP2101 power manager and the optional SH1106
OLED are on board. USB-C to the pit host. The AXP2101 rails are enabled at boot using
the map Meshtastic uses for this board (GNSS ALDO4, LoRa ALDO3, OLED/sensors ALDO1).
If your Supreme has an LR1121 radio instead of an SX1262 the radio init fails and the
status line says so; that variant is not handled yet.

## Repeater (any extra T-Beam S3 Core)

`firmware/repeater-tbeam-s3` turns a spare T-Beam into a plug-anywhere LoRa repeater for dead
zones. No configuration: power it from a USB brick and leave it. It listens on the fleet channel
and re-sends every packet it hears exactly once, 25 ms after reception. At **1 Hz** that copy lands
inside the same kart's 100 ms slot (23 ms packet + 25 ms + 23 ms copy). At **2 Hz** (the kart
default) it does not: the 50 ms slot only holds the original, and the copy lands on the next kart's
slot. **A fleet that depends on a repeater should run `rate 1` on every kart** (the compile-time
slot checks in `GoKartProtocol.h` and `tests/test_protocol.py` encode exactly this). A copy that
cannot start within 4 ms of its due time is dropped and counted as `late`.
Copies carry `GK_HDR_RELAY_BIT` (0x80) in the header version byte: a repeater never relays a
relayed packet (one hop), the pit drops a copy of a packet it already heard directly and shows
"via repeater" on karts it only hears through the repeater, and a kart syncs its slot timing from
a relayed beacon only when it has not heard the pit directly for 1.5 s.

```
./build.sh repeater upload /dev/cu.usbmodemXXXX
```

A T-Beam still running Meshtastic ignores the normal reset; open its port at 1200 baud and drop
DTR (`python3 -c 'import serial,time; s=serial.Serial(PORT,1200); s.dtr=False; time.sleep(.3)'`),
wait for the new ROM-bootloader port to appear and upload to that one. Boards already on our
firmware upload normally.

Every 5 s, right behind its copy of the pit beacon, it sends a 16-byte heartbeat (counters, battery, how it
hears the pit, which karts it heard); the pit pages show it as a `repeater` chip and flag it LOST after 20 s.

OLED: `heard n relayed n`, pit RSSI and age, and per-kart RSSI. The screen's I2C address is picked at boot:
on the second T-Beam the QMC6310 compass sits at 0x3C (register 0 reads 0x80) and the screen at 0x3D, so a
fixed 0x3C drives the compass and the screen stays dark. The boot log says which address and driver were
chosen; `oled sh1106|ssd1306 [3c|3d|auto]` on the USB console overrides and reboots. USB prints one JSON `stat` line a
second (`heard`, `relayed`, `hb_tx`, `dupes`, `dropped`, `late`, `rx_bad`, `pit_rssi`, `pit_age_s`, `batt_v`,
`uptime_s`, `oled*`, `i2c`, `karts`). Opening
the USB port with a terminal resets the board (USB-CDC DTR), which is harmless.

Placement: one repeater per dead zone, where it can hear the pit directly. Two repeaters in range
of each other would both relay the same packet at the same instant and collide; use one, or place
them so they cover different karts. Measured on the bench 2026-11-06: 100 % of kart 2's packets
and every beacon relayed, zero drops; range not yet measured.

## Protocol in one paragraph

Every kart transmits a 47-byte telemetry packet (23 ms on air) at 915 MHz, SF7, 500 kHz,
twice a second by default (`rate 1|2`), in a slot chosen by kart id: 50 ms slots in two
rounds per second at 2 Hz, 100 ms slots at 1 Hz. Slots align to the GPS PPS edge, or to the
pit beacon when a kart has no PPS. The pit sends a 27-byte beacon at 905 ms (UTC, its own fix,
sync phase, race state and positions) and 14-byte commands wherever its slot plan shows no
online kart on the air. A repeater re-sends packets once with the relay bit (0x80 in the
version byte). Commands carry a sequence number that the kart echoes with a result code in its
next telemetry; the host retries until it sees the echo, and the kart recognises retries and
repeater copies (seq + cmd + arg + issue time, last 8 per source) so they are never applied twice.
Receivers still accept 46-byte telemetry and 22-byte beacons from older builds, and the base
decodes Colin's 47-byte bench `GK` frame. See `libraries/GoKartProtocol/GoKartProtocol.h`
(slot plan with compile-time checks) and `base-tbeam-s3/SERIAL_PROTOCOL.md`.

## What the kart does with a command

- `SET_MODE n`: sends the limits read from this controller with two fields changed: the
  motor-current scale and the ERPM bound, which is a **top-speed cap** computed from this
  controller's own poles / gear ratio / wheel diameter (the SLOW arithmetic). The table is
  `config.h MODES[]`: ECO 0.50 (70 A) + 15 mph, NORMAL 0.75 (105 A) + 25 mph, SPORT 1.0 (140 A)
  + the controller's own bound (no cap). The cap is never raised above the controller's own
  bound; reverse is capped at the same speed. The kart asks the VESC for an ack, then re-reads
  GET_MCCONF and reports `applied` only if the live scale **and** ERPM bound match; `mode_actual`
  is likewise the mode whose scale and cap the VESC is running (a controller at its full bound
  with ECO's scale is not in ECO). A capped mode is `rejected` when the controller reports no
  drivetrain numbers. Battery current limits are never sent. While stopped or in a race grid hold
  the mode is remembered and applied at START / GO; during a BOOST the boost scale stays until
  its timer ends and then the new mode applies. (Up to 0.2.1 the modes only moved the current
  scale, 80 / 100 / 140 A, and were indistinguishable on the kart: a current cap shapes the first
  second of a launch and nothing after it.)
- `STOP`: aborts any write in flight, then ramps the scale 0.5, 0.2, 0.0 (`STOP_RAMP`, 150 ms
  steps, each acknowledged and re-read, so about 0.5-1 s) and holds; also asks the optional VESC
  brake script for regen. Cancels BOOST and race hold. Mechanical brakes and hardwired throttle
  are untouched; without the script the kart coasts.
- `START`: clears STOP, SLOW, BOOST and race hold and applies the requested mode, or the scale
  this controller had when the kart first read it (kept across a BLE drop, so START still
  restores the real limit after a reconnect during a STOP). If no mode was ever requested and
  that scale is 0 (Heltec rebooted during a stop), START answers `rejected`: send a mode.
- `SLOW n`, `BOOST pct`, `RACE secs`: see `base-tbeam-s3/SERIAL_PROTOCOL.md` and
  `pit/server/map/ZONES.md`. A BOOST whose scale is above the mode's also lifts the speed cap one
  mode up for its duration (ECO -> NORMAL's 25 mph, NORMAL -> uncapped; `config.h BOOST_LIFTS_CAP`);
  MUD (scale at or below the mode's) keeps the cap, and SLOW always wins when lower. BOOST and
  race hold end on the kart's own timer; race GO is `issued + secs` on the shared GPS/beacon
  clock at millisecond resolution.
- Every write requires: writes compiled in, the link up, GET_MCCONF read in THIS link session,
  and (with a `vescuuid` pin) a matching FW_VERSION reply from this session.
- `cmd_result` in radio telemetry answers pit commands only; console/kart-Pi commands are
  reported on USB (`cmd` lines and the `self` line), so their kart-local seq numbers never
  alias the pit's.
- Nothing is written at boot, on VESC reconnect, on WiFi/LoRa/GPS loss, or when a
  retransmitted command arrives. If a BOOST/MUD timer or race GO falls due while the VESC link
  is down, nothing is written then or on reconnect; `status` shows `revert_missed` and the
  re-read `mode_actual` tells the pit, which can resend the mode. `mode_actual` comes from a GET_MCCONF re-read
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

- Verified: all three sketches compile for their boards (arduino-cli, esp32 3.3.8);
  `tests/test_protocol.py` passes (struct layout, airtime, slot plan, JSON order);
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
- Verified on hardware since (2026-11-05/06, see HANDOFF.md): LoRa both ways between kart 2,
  kart 1 and the pit; GPS fix outdoors (5-11 sats); remote STOP/START on a real VESC over BLE
  with ack + read-back; WiFi OTA on the kart; the repeater relaying kart 2 and the beacon.
- Not verified: PPS slotting on the air (indoor bench has no PPS), 8-kart load, the base's
  PPS-kart edge derivation and schedule-based command placement (2026-11-06 night quality pass:
  compiled and host-tested only), the VESC UART transport, the brake script on any kart,
  BOOST/RACE against a live VESC, power draw, range.
