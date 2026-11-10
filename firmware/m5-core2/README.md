# M5 Core2 v1.5

Branch **`v1.5/m5-core2`**, based on Eamon's `b48c5ad` (2026-11-10).
Owner confirmed the display is **Core2 v1.3 / K010-V13** on 2026-11-09.
An alternative driver display for the existing Heltec + pit stack. The Pi v2 display remains
available. This milestone implements telemetry, mode requests, base-station status and a simple
north-up map. **Source/build work only: neither board has been flashed or wired for M5 testing.**

## Ownership and compatibility

```text
VESC <-- BLE --> Heltec V4.3 + L76K <-- LoRa --> existing T-Beam / Pi 4 pit
                     |
                 230400 UART
                     |
                  M5 Core2
```

Heltec owns VESC identity, polling, motor profiles, calibration, LoRa and GPS. Core2 owns display
and touch only. The original M5 BLE client/profile writer is not included. It must be replaced
by this display image before using this topology. No two devices should connect to the same
VESC. The motor's wired controls and protection stay with the VESC.

The M5 targets are **opt-in**. Integration now includes Eamon's kart0.2.6 and 46/47/54-byte
radio support; an idle kart still sends 47 bytes. M5 ignores the multiplayer-only fields.
Active multiplayer requires updated base/repeater0.2.2; this first M5 telemetry test stays
out of game sessions. Normal `kart`, `kart-uart`, base, repeater and Pi builds retain
existing behavior. The M5-specific adapter adds no radio structs, IDs, frequency/rate/slot changes, pit APIs
or USB JSON fields beyond Eamon's merged implementation. The UART mirrors the existing `self` and `fleet` JSON, plus
one new UART-only `display` metadata line. See [SERIAL_PROTOCOL](SERIAL_PROTOCOL.md).
All eight IDs work; Colin remains 1, Eamon remains 2. Do not assign a new ID based on display type.
PIT OK means a recent pit beacon was heard; it does not prove the pit received this kart.
Both display types use the same pit. Local display refresh targets 5 Hz; fleet updates remain
at the configured LoRa rate (currently 2 Hz direct / 1 Hz with a repeater). No physical
eight-kart UART or RF load test is claimed. Current Heltec `self` telemetry arrives at 2 Hz (500 ms);
5 Hz is the screen redraw rate, not a promise of 5 new telemetry samples per second.

## What the display does

- DASH: GPS MPH, pack volts, battery watts, motor/battery amps, ESC temperature, actual mode,
  VESC/link status, fault code, STOP/SLOW state and Heltec-reported speed calibration.
- MAP: own kart plus up to seven other directly/repeater-heard kart positions, labeled by ID;
  own kart gets a white ring. North up, automatic fit, optional offline course outline.
  FRESH ONLY shows fresh positions. STALE TOO shows hollow last-known dots for at most 15 s.
  Missing GPS is unavailable, never an invented position at (0,0). This is not geofencing.
- Hold ECO/NORMAL/SPORT for 650 ms to request. Pending, rejected, unavailable and unknown
  outcomes stay distinct from confirmed mode. Buttons use the Heltec's current cap definitions;
  the M5 does not duplicate its speed calibration or current scales. No mode is selected on boot.
- Driver mode requests cannot release pit SLOW, STOP or race grid hold. The Heltec rejects an
  M5 selection while stopped, holding the grid, busy writing, uncommissioned, OTA-active or
  baseline-suspect; it does not remember that selection for later.
- No wheel-speed estimate is fabricated without GPS. No motor temperature is displayed:
  the motors have no temperature sensors wired. `M5_BEGINNER_MODE` hides the driver fault code,
  leaving VESC protection, telemetry and pit reporting intact.

No aerial maps, race-entry filter (not present in the local telemetry contract), game zones,
FREEPLAY, boost actions, lap counting, sounds, Wi-Fi map sync, or M5 OTA yet. The Heltec's
existing Wi-Fi OTA path remains as upstream; this branch does not add or authorize a deployment.

## Proposed wiring (verify on the actual boards before connecting)

Use the Core2's exposed **PORT.A / Grove** (G32/G33), not the USB connector. GPIO numbers are
for Core2 v1.3 and Heltec HTIT-WB32LAF V4.3; check the exact unit and connector orientation.

| Connection | Core2 | Heltec |
| --- | --- | --- |
| Telemetry receive | G33 (white Grove signal) RX | G47 TX |
| Mode request transmit | G32 (yellow Grove signal) TX | G48 RX |
| Ground | GND (black) | GND |
| Power | Regulated supply to Core2 | Independently adequate regulated supply to Heltec |

UART signals are **3.3 V logic**, 230400 baud, 8N1. For the initial USB-powered bench setup, leave the red 5 V wire disconnected and insulated.
The Heltec pads labeled TX/RX are GPIO43/44; use the pads marked **47/48** for this link.
Do not tie independently powered 5 V outputs together or feed a signal pin from 5 V. The Heltec
must remain powered when the screen loses power. Power budgeting/backfeed and connector pinout
are unverified on this assembly. The earlier Pi-powered Heltec had brownouts; a display's
auxiliary power output is not assumed to support the Heltec's transmit peaks.

Heltec UART2 pins 47/48 are free in the BLE build. `GK_M5_UART` plus `VESC_TRANSPORT_UART` is a
compile-time error: those builds would contend for UART2. USB-C remains available for the
Heltec's existing console/provisioning. GPS UART1 and LoRa pins remain unchanged.

References: [Core2 pin map](https://docs.m5stack.com/en/core/Core2_v1.3),
[Heltec V4](https://heltec.org/project/wifi-lora-32-v4/), existing `kart-heltec-v4/config.h`.

## Build

Same Arduino ESP32 core as Eamon: **esp32:esp32 3.3.8**. Add these pinned display dependencies:

```sh
arduino-cli lib install 'M5Unified@0.2.29' 'M5GFX@0.2.36' 'ArduinoJson@7.4.3'
cd firmware
./build.sh m5                  # real UART display, no BLE or VESC library
./build.sh m5-demo             # standalone synthetic screen data; UART not initialized
./build.sh kart-m5-readonly    # companion Heltec: UART enabled, ALL VESC writes disabled
./build.sh kart-m5             # companion Heltec: guarded mode requests, existing pit commands
```

Other Heltec dependencies remain those in [firmware README](../README.md).
M5 FQBN is `esp32:esp32:m5stack_core2`; this is not a CoreS3 or generic ESP32 image.
Build output stays under ignored `firmware/build/`. Never commit binaries or credentials.

Upload is a separate hardware step: `./build.sh <target> upload <explicit-device-port>`.
First capture/retain each board's existing image and commissioning values for rollback. No flash,
VESC settings write, detection or motor test is part of the build/test commands above.

## Course outline

Draw or record the course with the existing pit editor, then export its `zones.json`:

```sh
python3 tools/m5/export_course.py --zones pit/server/map/zones.json \
  --name 'Shop test course' --out /tmp/m5-course.json
```

Copy the resulting file to a FAT32 microSD card at `/gokart/course.json`, insert into the Core2,
and restart the display. The exporter refuses an existing output and invalid or >128-point
tracks; simplify a large route in the pit first. It exports the track only, no game effects.
The repository's shop route is a demonstration, not a surveyed Manresa course.

Without a valid file, the map fits heard GPS positions and says GPS only. A malformed/oversize
file is ignored. Removing/rebooting the display never writes to the VESC. Course files contain
only geographic geometry, never Wi-Fi credentials or motor limits.

## Reliability and commissioning

- New data replaces stale data; reconnection never sends a mode. No stored desired mode or
  command resend queue exists on the M5. An interrupted request becomes outcome unknown.
- Each request carries a CRC16 and a single-use one-second token from this Heltec boot.
  Tokens are invalidated on readiness/link-session changes. This is accidental corruption/replay
  protection on a physical wire, **not authentication** and not a heartbeat driving lease.
- The adapter accepts only mode requests. It cannot forward arbitrary console commands, change
  credentials, reset boards, set power/current, start/stop the kart or stream motor commands.
- In addition to upstream gates, M5 requests require a pinned VESC UUID and fresh linked state.
  Existing controller limits and the upstream acknowledgement/readback path remain authoritative.
- Whole JSON lines are dropped if the UART TX buffer has insufficient space. Input work is
  bounded, oversized/partial lines are discarded. Blocking a display must not stall radio service.
- Eamon's existing write/OTA policy differs from Colin's older integration safeguards. This
  branch does not merge PR #4 or declare those outstanding integration questions resolved.

The [first-session guide](BRINGUP.md) has a wiring table, expected screens, troubleshooting
and a results checklist for the planned 2026-11-10 bench. First physical sequence, still pending:
1. Inspect the Core2/Heltec pinout and power arrangement; retain rollback images.
2. Flash `m5-demo` to check display/touch independently when the owner authorizes deployment.
3. Use `m5` + `kart-m5-readonly`; verify ID 1, volts/current/ESC values, GPS fix, UART drops and
   simultaneous unchanged USB telemetry. Confirm the existing pit sees the kart and can ping.
4. Test outdoor GPS positions with another kart, course loading, 1/2 Hz and a full eight-kart
   simulated load on the physical UART. Disconnect/reconnect RX/TX, reboot only the display,
   and confirm no VESC configuration writes. Measure actual refresh/latency.
5. Only after reviewing/commissioning the controller, use `kart-m5` for supervised mode readback
   checks. Verify ECO/NORMAL/SPORT, pit SLOW remains through a driver selection, STOP/GRID rejects
   selections, and pending requests are not replayed after cable/BLE loss. No unattended motor test.

## Verification

Host tests exercise the real shared telemetry encoder, JSON parser, expiry/CRC/single-use gate,
request outcomes, boot/link loss, timestamp rollover, map freshness and course export. A second
native test simulates 600 seconds of eight-kart traffic, losses, corrupt frames and restart
recovery; old GPS samples keep their source age instead of becoming fresh upon arrival.
The printed UART utilization is ideal serialization arithmetic, not measured hardware throughput.
These tests need C++17 plus ArduinoJson's installed header directory:

```sh
export ARDUINOJSON_DIR='/path/to/Arduino/libraries/ArduinoJson/src'
python3 -I firmware/tests/test_m5.py
tools/run_tests.sh                 # all existing suites plus M5 tests
```

See [handoff](../../HANDOFF.md) for current build/test results and hardware status.
