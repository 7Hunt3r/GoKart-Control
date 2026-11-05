# Shared kart / base / pit integration

This branch combines Eamon's `eamon/firmware-lora-starter` through `d98c41d` and
Colin's `diagnostics/removable-bench-control` through `544b498`. It keeps one
shared radio library and base serial contract. It does not imply that the
integrated command build is already installed or hardware-validated.

## Components and contracts

| Component | Build / role | Hardware status |
| --- | --- | --- |
| `firmware/heltec` | PlatformIO, telemetry-only recovery image | Installed/tested on Colin's kart 1, 0.2.2-bench |
| `firmware/kart-heltec-v4` | Arduino CLI `kart` (BLE), optional `kart-uart` | Eamon tested earlier kart 2 radio/ping/OTA without VESC; new guards compiled/offline-tested only |
| `firmware/base-tbeam-s3` | Arduino CLI `base` | Eamon reports T-Beam reception/command transmission |
| `pit/` | Pi 4 server/kiosk and laptop simulator | Local unit/e2e tests; Eamon reports real Pi kiosk running |
| `firmware/bench-control` | Removable experiments, not normal vehicle firmware | Short direct BLE forward probe succeeded; longer tests aborted |

Radio: GoKartProtocol v1, 46-byte telemetry, 915 MHz, BW500, SF7, CR4/5, sync
0x12, preamble 8, 2 Hz per kart by default (integrated nodes also support 1 Hz). The base also accepts the historical 47-byte
bench frame. Keep `firmware/libraries/GoKartProtocol/GoKartProtocol.h` canonical.
Pit ↔ base JSON is defined in
[`SERIAL_PROTOCOL.md`](../firmware/base-tbeam-s3/SERIAL_PROTOCOL.md).
Kart ↔ display JSON comes from the integrated kart's `self`, `fleet`, `status`
and command lines. The Pi display must not create a competing VESC session.

## Build and test

The integrated kart was compiled with Arduino CLI 1.5.1, ESP32 core 3.3.8,
RadioLib 7.8.1, TinyGPSPlus 1.0.3, U8g2 2.36.19, ArduinoJson 7.4.3 and
NimBLE-Arduino 2.5.2. XPowersLib 0.3.3 is installed for the base. For repeatable
setup, use these versions with the Arduino CLI instructions in firmware/README.

```sh
cd firmware
./build.sh kart
./build.sh kart-uart
./build.sh base
```

No upload is part of those commands. From the repository root:

```sh
python3 -B -m unittest discover -s tests -v
python3 -B -m unittest discover -s firmware/bench-control/test -v
python3 -I pit/tests/test_core.py
python3 -I pit/tests/test_e2e.py
```

Use a Python environment with pyserial for pit tests and localhost socket access.
Root tests compile the real VESC transport/guard with ASan/UBSan and captured
identity/configuration packets. Build success does not establish physical
STOP/START, OTA, or failure behavior on the modified image.

## Commission the integrated node before command testing

The installed telemetry image uses a different console; these instructions apply
only after explicitly installing the integrated `kart` build. Keep the normal
PlatformIO image/credentials available for rollback. Do not flash or send motor
commands automatically as part of repository synchronization.

For Colin's captured kart only:

```text
id 1
vescmac d8:94:35:be:e5:32
vescuuid 2c004c000951343037353739
rate 2
status
```

Do not copy that identity onto Eamon's kart 2. Inspect each kart and pin its own
BLE address and VESC UUID. Without the UUID, telemetry can be read but every
configuration write is rejected. `status` also emits an `identity` record with
`verified`; both firmware 6.00 and the pinned UUID must match. A disconnect
invalidates identity and settings; reconnect reads them again without writes.
The BLE name alone is never permission to write.

`ENABLE_VESC_WRITES=1` remains Eamon's command-build default, with the identity
and fresh-settings gates above. A `0` build rejects configuration writes. This
is not a filesystem “read-only” permission and not the same as the installed
telemetry-only image's complete lack of command handling.

## Temporary configuration behavior

Each explicit STOP/START/mode transaction:

1. Verifies identity/link, requests a new full motor configuration, and validates
   its exact 6.00 schema and finite/ordered limits.
2. Copies current-min scale, ERPM, duty and watt limits from that reply. Changes
   only current-max scale. STOP clamps each step to the observed scale.
3. Sends command 48 with `store=0`, no CAN forwarding or controller division,
   and no optional battery-current limits.
4. Waits for acknowledgment, re-reads configuration, and checks all transmitted
   values plus preservation of battery-current limits. A mismatch, timeout or
   loss gives unknown and ends the ramp without replay.

START restores an allowance and never requests torque directly. The saved
configuration is untouched, but a temporary setting remains active across an
accessory/link outage; it is not a timeout lease. Do not treat a STOP badge as a
mechanical brake or an independently proven stop. Explicit controlled physical
validation is still required before operational use.

Recent command outcomes are cached for the last eight distinct requests. A
retransmission reports the cached outcome and does not reapply that request.
STOP can supersede an in-flight transaction; the prior outcome becomes unknown
and the stop starts with a fresh settings read. Other overlapping requests are
rejected. STOPPED is reported only after readback shows the allowance at zero. The pit keeps its existing serialization,
TTL/retry and per-kart outcome model. Radio authentication and stronger
cross-reboot replay protection remain field-deployment work.

## Maintenance OTA

The integrated kart now requires a non-empty `otapw` and explicit
`maintenance on` over local USB while the intended VESC is verified, fresh,
stationary and no command is pending. Provision Wi-Fi/password locally, never
in source. Maintenance pauses BLE and LoRa without changing VESC settings;
`maintenance off` or ten minutes expiry resumes read-only connection/radio.
OTA is off after reboot. This replaces the earlier always-available/optional-
password OTA policy for this integrated kart; base firmware OTA is unchanged.

The normal `firmware/heltec` recovery image keeps its own already-tested
maintenance workflow. Its radio TX is opt-in after boot; the integrated kart
transmits automatically as part of its commissioned fleet role. Fit its antenna
before powering the integrated radio. The 0.2.2 recovery image was USB-flashed for this rate update; the integrated
command image remains uninstalled.

## Maps and refresh rates

Public CT ECO 2023 Manresa and shop imagery are in Git. The active shop preset
uses `course_shop_cteco.jpg`; it now works from a clean clone. Licensed SkyFi
images remain local-only. The Manresa preset still selects the licensed image;
switch its `image` to `course.jpg` with matching CT ECO attribution when using
public imagery. See [map README](../pit/server/map/README.md).

| Feed | Next target / implementation |
| --- | --- |
| VESC GET_VALUES, both kart images | 200 ms / 5 Hz; actual replies depend on BLE latency |
| Kart USB display feed, both images | 200 ms / 5 Hz; values may repeat if no new sample |
| LoRa kart telemetry | 500 ms / 2 Hz default; integrated console `rate 1|2` persists |
| Base beacon/status | 1 Hz |
| Pit SSE | Existing ~4 Hz full state; independent of radio reception |
| Pit moving map | Eamon's 20 fps interpolation; not 20 fresh GPS fixes per second |
| GNSS module | Existing configuration, unchanged; report fix age/validity |

The integrated kart and base share fixed 50 ms ID slots in two 500 ms rounds.
A 1 Hz kart uses only the first round at the same ID offset. Transmission starts
8–16 ms into its slot; calculated 46-byte airtime is 23.104 ms. The base's 22-byte
beacon starts at/after 905 ms (14.144 ms airtime) and finishes before 985 ms.
Commands use 415–485 ms, with 30 ms between a completed TX and the next start.
This moves Eamon's proposed 815–895 ms command window out of kart 7/8's second
round. The rule also applies when the base is free-running, since beacon clients
follow that same clock. GPS PPS has priority; otherwise karts follow a fresh base beacon.
Without either source, jittered fallback is unslotted and collisions are possible.
A base without PPS only derives its clock from PPS-tagged kart packets, never
from the unslotted recovery transmitter.

**Deploy base and integrated kart images together.** The earlier 1 Hz schedule
used 100 ms slots and can collide with the new second round. Packet formats and
RF settings still match, but packet compatibility is not schedule compatibility.
New integrated nodes default to 2 Hz; a stored 1 Hz preference is preserved until
`rate 2` is explicitly issued. `rate 1` remains available for coverage/load tests.
Firmware identifiers: `kart-0.1.2-config-guard-2hz`, `base-0.1.1-beacon-2hz`,
and telemetry recovery `0.2.2-bench`.

The read-only recovery image sends at 500 ms ±50 ms without receiving beacons or
other karts. It remains suitable for the single-kart bench test, not a claim of
fleet scheduling. The pit simulator now defaults to 2 Hz with `--rate 1` available.

Eight karts at 2 Hz use 36.97% calculated telemetry airtime, before beacons,
commands and retransmissions. This follows [Semtech's airtime formula](https://cdn.sparkfun.com/assets/7/7/3/2/2/SX1276_Datasheet.pdf)
and is not an RF field result. Native tests cover all 256 mixed-rate fleets,
slot boundaries and complete packet occupancy. Measure reception/loss and
command latency with Eamon's base, then all eight karts, before raising rates
again. A later 10 Hz local benchmark is not included in this change.

## Measured kart-1 rate update

The installed recovery image was measured over 24.834 seconds: 121 new VESC
samples (4.872 Hz), 125 USB status records (4.993 Hz), and 49 completed radio
transmissions (1.973 Hz). All samples were identity-verified/fresh, stationary
and fault-free, with zero BLE receive drops or radio TX errors. USB reader
close/reopen for five seconds recovered without reboot; counters continued.
The bounded USB output path was corrected after the first attempt stalled.
These are local measurements, not proof of pit receipt, GPS fix or eight-kart
RF performance. [Raw trace and hashes](../hardware/heltec/2026-11-05-rate-update/README.md).

Validation: 52 automated tests pass (15 root, 11 diagnostics, 24 pit core,
2 pit end-to-end); BLE kart, UART kart, base and recovery images compile.
