# Heltec V4.3 bench transmitter and VESC adapter

Development firmware for the photo-identified HTIT-WB32LAF V4.3 (ESP32-S3,
16 MB flash). It owns the BLE VESC connection, reads GPS and sends optional
LoRa telemetry. The Pi is not involved. This is **not a production vehicle
controller**: profile writes, motor commands, geofence and remote stop are absent.

## Reliability boundaries

The only VESC requests implemented are `COMM_FW_VERSION` (0) and
`COMM_GET_VALUES` (4). BLE characteristic writes transport those read requests;
they do not change VESC configuration. Identity must match the captured 6.00
controller UUID before telemetry polling starts. No command restores, resets,
stops, changes profiles or replays cached settings on startup or reconnect.

The BLE worker is separate from USB/GPS/radio servicing. Notifications use a
bounded queue and CRC-checked stream parser. Overflow resynchronizes; stale
telemetry becomes unavailable. The display is not needed to run the adapter.
Motor temperature is always unavailable because this kart has no wired sensor.
Stationary telemetry does not prove unchanged throttle response under outages:
the controlled bench acceptance checks in requirements still apply.

## Build and initial USB install

From the repository root, with Python 3 and PlatformIO installed:

```sh
python3 tools/heltec/prepare_local.py
python3 tools/heltec/provision_wifi.py
pio run -d firmware/heltec
pio device list
pio run -d firmware/heltec -t upload --upload-port /dev/cu.YOUR_HELTEC
```

`provision_wifi.py` uses macOS local dialogs, including hidden password entry.
On other systems create the ignored `include/wifi.local.h` with `WIFI_SSID` and
`WIFI_PASSWORD` string definitions, using proper C string escaping. Credentials,
flash backups and built firmware (which contains credentials) must never go in
Git. `prepare_local.py` refuses to replace existing local secrets. Collaborators
must generate their own credentials. Do not commit a Wi-Fi password or a shared
fleet OTA password. Use a 2.4 GHz WPA personal network for this bench build.

The PlatformIO target is a generic ESP32-S3 with explicit V4.3 pins and 16 MB
flash, not a V3 pinout. PSRAM is not required by this build. Two 6 MB application
slots support OTA. Dependencies are pinned in platformio.ini. Before replacing
an existing device, take a local-only flash backup; the development board's
original 16 MB flash is retained under ignored `.local/` on Colin's computer.

## USB console

115200 baud, newline-delimited commands and JSON status/events. Opening the
console is optional; VESC polling runs without it. For PlatformIO:

```sh
pio device monitor -d firmware/heltec --port /dev/cu.YOUR_HELTEC --dtr 1 --rts 0
```

These line states avoid deliberately toggling the native USB reset sequence.
A connection opened midway through an output record may start with a partial
line; ignore it and parse subsequent complete JSON lines.

| Command | Meaning |
| --- | --- |
| `status` | Current validity, telemetry, GPS, radio and maintenance state |
| `help` | Supported commands |
| `tx on antenna-confirmed` | Enable bench LoRa TX only after fitting the antenna |
| `tx off` | Disable periodic LoRa TX |
| `id 0` through `id 8` | Save commissioned fleet ID; 0 means bench/uncommissioned |
| `maintenance on` | Request OTA maintenance; requires fresh, zero-ERPM VESC telemetry |
| `maintenance off` | Close OTA/Wi-Fi and resume read-only BLE discovery |

TX and OTA are **off after every boot**, even if previously enabled. No numeric
motor/configuration commands are accepted. The USB interface is local bench
access, not a network command API. ID 0 blocks transmission until commissioned.
The owner assigned this development kart **ID 1**; reserve another ID for Eamon’s kart.

## OTA on existing Wi-Fi

Park the kart before maintenance. `maintenance on` closes the BLE session
without writing to the VESC, stops radio transmissions and joins provisioned
Wi-Fi. Alternatively hold the board's USER/BOOT button for three seconds while
firmware is running to open maintenance physically; that recovery path does not
require VESC telemetry, so the operator must establish the kart is parked.
Do not hold BOOT while resetting unless intending to enter the ROM USB loader.

The maintenance window lasts ten minutes. The serial status reports `ota_ip`
and `ota_hostname`; OTA uses port 3232 and a unique local password. From a
computer on the same LAN, after building:

```sh
python3 tools/heltec/ota_upload.py HELTEC_IP
```

If needed supply `--host-ip COMPUTER_LAN_IP` for the uploader's return connection.
Use an ordinary local LAN with peer traffic allowed. The helper loads the OTA
password from ignored local credentials without putting it in process arguments
or printing it. It handles fragmented TCP replies and bounds transfer waits. A successful
update reboots only the Heltec. Boot then returns to telemetry operation with
TX and OTA off. The uploaded VESC firmware is never changed by this process.

The updater writes the inactive app slot and uses Arduino/ESP image validation.
This is authenticated local maintenance, not a signed fleet deployment system;
there is no claimed automatic health-check rollback. Keep USB recovery and the
original flash backup. Wi-Fi/OTA failure does not send a VESC command. Interrupted
update and power-failure recovery need separate controlled tests before field use.

## Radio receiver agreement for Eamon

This adapter uses Eamon’s canonical
[`GoKartProtocol.h`](../libraries/GoKartProtocol/GoKartProtocol.h). The integration
includes his branch through `d98c41d`; the transmitter keeps the shared 46-byte
packet and **BW500**. Version `0.2.2-bench` raises VESC polling/local USB output
to 5 Hz and unslotted radio reports to 2 Hz. The base also accepts legacy 47-byte
frames. Version 0.2.1-bench used the same RF settings at 1 Hz. The earlier 0.2.0-bench BW250 image
must be updated to receive on his latest base. No one should independently move
the bandwidth again; change the canonical shared header and update both ends.

Custom raw LoRa, not LoRaWAN/Meshtastic:

| Setting | Bench value |
| --- | --- |
| Carrier | 915.0 MHz |
| Bandwidth | 500 kHz |
| Spreading factor | SF7 |
| Coding rate | 4/5 |
| Sync word | 0x12 |
| Preamble | 8 symbols |
| Header / CRC / IQ | Explicit / enabled / normal |
| SX1262 requested output | 0 dBm; external FEM means this is not antenna EIRP |
| Interval | 500 ms ±50 ms jitter (2 Hz target); 46-byte payload |
| Identity | Protocol 1, fleet 0x4B, telemetry type 1, kart 1, destination 255 |

The packed shared struct is the source of truth for offsets and units. All
multibyte values are little-endian. Tests compile a C++ fixture from that header
and decode it with the Python helper. No second competing packet definition is
maintained in this document.

- `GK_FLAG_VESC_LINK` means a verified sample less than one second old.
- `GK_FLAG_GPS_FIX` means a position less than three seconds old. Never plot
  `(0,0)` when this flag is clear. Speed/heading have no independent validity
  bits in this version; zero can also mean unavailable. Do not use them for
  geofence/control decisions without extending the shared contract.
- Motor temperature is absent/unavailable. Modes are unknown (255), scale is
  unknown (65535), command result is none, and write/PPS/stop flags are clear.
- This adapter has no radio command receiver; pit mode/stop controls cannot
  actuate it. It also does not yet receive other karts for the driver map.
- PPS slotting is not implemented here. Free-running single-kart testing is
  supported; eight-kart collision/load testing remains open.
- Sequence wraps at 256; uptime wraps after 65,536 seconds. There is no boot ID
  or precise sample age on this wire version. Receivers must age packets locally;
  duplicates must not indefinitely refresh data. Session/replay handling needs
  improvement before control deployment.
- Eamon’s current JSON helper emits numeric VESC fields even when `vesc_link`
  is false. Consumers must mask them; our offline decoder already does so.

Radio initialization or TX-done is not proof of reception. Confirm actual
received sequences and values on Eamon’s base. RF power/range and fleet capacity
are not validated. Radio command authentication remains future work. Do not
use the V4.2 PA GPIO46 mapping on this V4.3 board.

Decode a receiver's payload hex with:

```sh
python3 tools/heltec/decode_radio.py HEX_PAYLOAD
```

## Board mapping and sources

- SX1262 SPI: CS8, clock9, MOSI10, MISO11; reset12, busy13, IRQ14.
- V4.3 KCT8103L FEM: supply enable7 HIGH, CSD2 HIGH, CTX5 HIGH for
  transmit/bypass; DIO2 drives CPS. A receiver wanting LNA mode sets CTX5 LOW.
  Eamon must confirm the receiver board revision before applying this mapping.
- L76K: power GPIO34 active low; MCU RX39 (GPS TX net); GPIO38 left as input; reset42 active low,
  awake40 high. UART is receive-only; NMEA at 9600 baud; GPS positioning needs sky view.
- [Manufacturer V4.3 schematic](https://resource.heltec.cn/download/WiFi_LoRa_32_V4/Schematic/HTIT-WB32LAF_V4.3.pdf)
- [Meshtastic V4 hardware implementation](https://github.com/meshtastic/firmware/blob/master/variants/esp32s3/heltec_v4/variant.h)
- [NimBLE client API/example, 2.3.7](https://github.com/h2zero/NimBLE-Arduino/blob/2.3.7/examples/NimBLE_Client/NimBLE_Client.ino)
- [RadioLib, 7.4.0](https://github.com/jgromes/RadioLib/tree/7.4.0)
- [Arduino ESP32 OTA, 2.0.17](https://github.com/espressif/arduino-esp32/tree/2.0.17/libraries/ArduinoOTA)

## Offline checks

```sh
python3 -B -m unittest discover -s tests -v
clang++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined -I firmware/heltec/include firmware/heltec/test/protocol_test.cpp -o /tmp/gkc-protocol-test
/tmp/gkc-protocol-test hardware/vesc/baseline-kart/2026-11-05/raw
pio run -d firmware/heltec
```

The C++ test checks the compiled protocol implementation against actual captured
bytes, rejects every non-allowlisted command, validates identity matching and
exercises fragmented packets, malformed lengths and CRC rejection.

## 0.2.2 rate verification

[Recorded kart-1 check](../../hardware/heltec/2026-11-05-rate-update/README.md):
4.872 Hz VESC samples, 4.993 Hz USB output and 1.973 Hz completed TX over
24.834 seconds, no receive drops/TX errors/faults. A five-second USB-reader
disconnect recovered without a reboot. Output uses bounded 64-byte writes
with a 1 ms timeout; the display reader does not gate BLE polling or radio TX.
Pit receipt and full-fleet scheduling are not established by this check.

## Onboard OLED (0.2.3)

The recovery image now powers and initializes the 128×64 SSD1306 OLED instead
of leaving it black. It shows kart ID, fresh VESC voltage/motor current/fault,
electrical RPM, ESC temperature, GPS fix/satellites, LoRa TX state/count and
read-only/maintenance mode. Display refresh is 2 Hz; it does not change the
5 Hz telemetry poll or 2 Hz radio target. Stale VESC values are labeled unavailable.

The V4.3 mapping is Vext 36 (active low), reset 21, SDA 17, SCL 18, address 0x3c.
U8g2 is pinned to PlatformIO's 2.36.18. Release `Wire` with `Wire.end()` after
the presence probe and before `oled.begin()`: U8g2 configures GPIOs before its
I²C initialization. Reusing the already-initialized probe bus stalled startup
on this board/core; releasing it lets the driver reattach the pins. The failed
initial attempts and successful follow-up are recorded in the
[OLED evidence](../../hardware/heltec/2026-11-05-oled-enable/README.md).

USB `status` includes `oled_present` and `oled_refreshes`. The address probe
acknowledges the controller; it is not a camera check of the visible pixels.
A missing/nonresponding display is skipped, and BLE remains on its separate
worker. No new VESC command or configuration-write API was added.
