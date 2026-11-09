# Core2 v1.3 + Heltec: first bench session

Planned for **2026-11-10**. This is a test guide, not a record that hardware passed.
Use branch `v1.5/m5-core2` and [the M5 README](README.md). Initial targets are `m5-demo`,
`m5` and `kart-m5-readonly`. Mode writes are a later, separately supervised check.

## Bring to the bench

- M5Stack **Core2 v1.3 / K010-V13**, Heltec **WiFi LoRa 32 V4.3** with its GPS attached.
- Grove/HY2.0 four-wire cable broken out to the Heltec header; secure connections rather than
  loose wires touching pads. Keep the LoRa antenna attached.
- Two known-good USB-C data cables and adequate USB power, one per board. Label each cable
  so upload ports cannot be confused. The red Grove lead stays disconnected and insulated.
- Optional FAT32 microSD for a course outline. GPS map checks need sky view and a second kart
  transmitting; a USB-only bench inside may show no GPS fix.
- Access to the existing pit dashboard to confirm **kart 1** is being received. Eamon remains
  kart 2. No new fleet ID is needed for an M5 display.

## Wiring, with both boards off

| Standard M5 cable | Core2 PORT.A | Heltec header |
| --- | --- | --- |
| Yellow | GPIO32 / TX in this firmware | GPIO48 / RX |
| White | GPIO33 / RX in this firmware | GPIO47 / TX |
| Black | GND | GND |
| Red | 5 V | **Unconnected and insulated** |

The connector is labeled I2C but the firmware assigns its two signals to **UART, 230400 baud,
8N1, 3.3 V logic**. Use Heltec pads marked **47/48**, not the pads simply marked TX/RX
(GPIO43/44). Verify labels if a cable uses different colors. Power each board by its own USB
connection for this first session; do not join their 5 V power outputs.

Official references: [Core2 v1.3 pin map](https://docs.m5stack.com/en/core/Core2_v1.3),
[Heltec V4 pin map](https://heltec.org/project/wifi-lora-32-v4/).
Internal Core2 touch/PMU/RTC/IMU I2C is on GPIO21/22, separate from these Port A signals.
Actual assembly power, signal integrity and behavior with either device off remain to be tested.

## Prepare without touching hardware

From the repository root, with dependencies from the README installed:

```sh
export ARDUINOJSON_DIR='/path/to/Arduino/libraries/ArduinoJson/src'
tools/run_tests.sh
bash firmware/build.sh m5-demo
bash firmware/build.sh m5
bash firmware/build.sh kart-m5-readonly
python3 tools/m5/export_course.py --zones pit/server/map/zones.json \
  --name 'Shop bench outline' --out firmware/build/m5-bringup/course.json
```

These commands build, run host simulations and export a course; none open a device port or
flash a board. The exporter refuses to overwrite an existing file. The sample is the pit's
shop track, **not a surveyed Manresa course**. Copy the exported file to `/gokart/course.json`
on the SD card when preparing the actual display.

## Ordered physical checks

Before the first flash, identify both explicit serial ports and retain each board's previous
image and commissioning values for rollback. Flash backups may include Wi-Fi/OTA secrets;
keep them local under ignored `firmware/build/bench-private/`, never in Git or a public PR.
Record the source commit and target for each new image. Upload only after choosing the exact
board/port; build success alone does not establish that an image is installed.

| Stage | Setup/action | Expected result |
| --- | --- | --- |
| 1. Display alone | Run `m5-demo`; Heltec link not needed | Screen says **DEMO / NO OUTPUT**. DASH/MAP tabs and FRESH ONLY/STALE TOO toggle respond; eight synthetic dots appear. Mode buttons send nothing. |
| 2. Real telemetry | Run `m5` + `kart-m5-readonly`, with the three wires connected | Demo label disappears. Kart ID is 1; electrical values match Heltec USB `self` telemetry when VESC is linked. Mode controls remain unavailable. |
| 3. Identity/read-only | On Heltec USB console, request `status` | `writes:false`, `transport:"ble"`, intended `vescmac`/`vesc_uuid`, `id_ok:true` and `radio:true`. Before later mode testing, verify nonempty `uuid_pin` matches `vesc_uuid`; `id_ok` alone also allows address-only binding. No motor test or settings write is needed. |
| 4. GPS/map | Allow an outdoor fix; install optional course SD before starting display | Own numbered dot gets a white ring; course outline is visible if loaded. With no fix, GPS speed is `--`, not a fabricated zero/location. |
| 5. Shared pit | View kart 1 in existing pit dashboard; observe another kart on M5 | Pit receives live kart-1 updates; M5 shows the other kart when its position is valid. M5 PIT OK means it heard a beacon, so confirm both directions separately. |
| 6. Link loss | Interrupt telemetry data line, then reconnect; also restart display alone | Own telemetry becomes lost after ~1.5 s, fresh map positions age out after ~3 s, optional stale dots vanish by ~15 s. Reconnect restores data without sending a mode. VESC active settings stay unchanged. |
| 7. Sustained run | Leave telemetry running for 10 minutes; log boot messages and rates | No unexpected resets, repeated freezes or unexplained mode changes. Check both USB and display still work. This measures real hardware; host simulation is not a substitute. |

Change wiring only with power removed. For live link-loss checks use a prepared switch or
secure removable signal connector so adjacent pins cannot be shorted. Keep the grounds joined.
Power-cycle only the display for that step, not the Heltec or the VESC.

The display redraws at **5 Hz**. The current Heltec `self` reports are **2 Hz** (500 ms);
fleet data follows the selected radio rate, normally 2 Hz direct or 1 Hz with the repeater.
Metadata also updates when its command token changes. Capture actual latency and drops rather
than expecting every redraw to contain a new sensor sample.

## Quick diagnosis

| Symptom | First checks |
| --- | --- |
| Display dark | Run standalone `m5-demo`; verify USB power, Core2 power button and correct Core2 target before debugging the Heltec wires. |
| Demo dots still shown | `m5-demo` is still installed. Use `m5` for live telemetry. |
| HELTEC DATA LOST | Common ground, white G33 to Heltec47, both boards powered, correct M5 UART companion target, 230400 baud. |
| VESC DISCONNECTED | Check the Heltec's existing BLE pin/identity and VESC power. The M5 does not own BLE. |
| Electrical data works but no map/speed | GPS needs a valid fix. M5 v1.5 speed is GPS-derived; spinning wheels on a stationary stand will not show road speed. |
| Mode controls unavailable | Expected with `kart-m5-readonly`. Do not switch on writes just to make the first telemetry test pass. |
| PIT LOST or no other kart | Confirm the base/other kart is on, antenna attached and existing fleet/radio settings match. M5 wiring alone does not create LoRa reception. |
| Course absent | FAT32, `/gokart/course.json`, schema1, 2–128 valid points, file <=24576 bytes; restart the display after inserting the card. |

## Record before calling it passed

- Date/operator, board revisions, source commit, targets and which image was actually flashed.
- Rollback image location (private), power arrangement and a wiring photo.
- Kart ID, VESC identity and `writes:false`; strip credentials from anything shared.
- Screen/touch, GPS/course, second kart and pit observations; mark unavailable checks pending.
- Measured report cadence, any resets/freezes, link-loss/recovery outcome and VESC active limits
  before/after. Retain local logs with timing if a problem occurs.

Then update HANDOFF.md with **observed** results. Mode acknowledgements, STOP/SLOW interactions,
controller tuning and motor movement belong to the later supervised control session.
