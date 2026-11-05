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
access, not a network command API. ID 0 does not claim this baseline kart is kart 1.

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
| Interval | 2 seconds, 47-byte payload |

These are single-transmitter bench settings, not a finalized eight-kart airtime,
RF compliance or range configuration. Do not use the V4.2 PA GPIO46 code on V4.3.
Radio initialization or TX-done is not proof of reception; a matching receiver
must confirm payload CRC/content and sequence numbers.

All multibyte integers below are little-endian. Signed fields use two's complement.

| Offset | Bytes | Field |
| --- | --- | --- |
| 0 | 2 | ASCII `GK` |
| 2 | 1 | Version 1 |
| 3 | 1 | Kart ID; 0 bench, 1–8 commissioned |
| 4 | 4 | Random boot/session ID |
| 8 | 4 | Sequence number |
| 12 | 4 | Sender uptime ms (not UTC) |
| 16 | 2 | VESC sample age ms; 65535 unknown, 65534 saturated |
| 18 | 1 | Flags: bit 0 VESC fresh/verified, bit 1 GPS position fresh |
| 19 | 1 | VESC fault; 255 unavailable |
| 20 | 2 | Battery voltage ×10 |
| 22 | 2 | Signed battery amps ×10 |
| 24 | 2 | Signed motor amps ×10 |
| 26 | 4 | Signed electrical RPM |
| 30 | 2 | Signed ESC temperature C ×10 |
| 32 | 4 | Signed latitude degrees ×10^7 |
| 36 | 4 | Signed longitude degrees ×10^7 |
| 40 | 2 | GPS speed cm/s; 65535 unavailable |
| 42 | 1 | Satellites; 255 unavailable |
| 43 | 2 | HDOP ×100; 65535 unavailable |
| 45 | 2 | GPS position age ms; 65535 unknown, 65534 saturated |

Invalid measurements may occupy zero bytes; validity flags govern interpretation.
Never plot `(0,0)` for no fix. Sample freshness is <2 seconds at the sender for
this bench build. The receiver must add its own elapsed time and not preserve
freshness indefinitely. Track session ID with sequence; duplicates do not make
samples newer. Motor temperature is absent/unavailable. GPS speed has its own
unavailable sentinel and is not wheel-derived speed. No commands are received
or executed over LoRa, and packets are not authenticated in this telemetry-only
bench version. Fleet control authentication is future work.

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
