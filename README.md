# GoKart Control

An eight-kart dashboard and shared pit system for Manresa Island, Connecticut.
Each Heltec V4.3 owns the kart's VESC Bluetooth connection, L76K GPS and LoRa.
A Raspberry Pi 3 B runs the 7-inch LESOWN display; a Pi 4 serves the shared,
password-protected pit interface through a T-Beam S3 base radio.

**The connected development kart is the hardware baseline.** The old M5Stack
app is historical reference. Driver inputs remain wired directly to the ESC;
accessory/link failures must not change its active settings or replay commands.

## Start here

- [Handoff and next hardware test](HANDOFF.md)
- [Integration/build/commissioning guide](docs/integration.md)
- [Requirements](docs/requirements.md), [architecture](docs/architecture.md),
  [hardware](docs/hardware.md), [collaboration workflow](CONTRIBUTING.md)
- [Measured VESC configuration](hardware/vesc/baseline-kart/2026-11-05/README.md)
- [Pit server, kiosk and simulator](pit/README.md)

## What is available

| Component | State |
| --- | --- |
| `firmware/heltec` | Tested telemetry-only adapter, authenticated maintenance OTA. **Installed on Colin's kart 1**, version 0.2.2-bench; BLE/GPS/LoRa TX verified. |
| `firmware/kart-heltec-v4` | Eamon's integrated kart node with LoRa receive/commands, fleet data and display serial output. BLE is default. Configuration/identity/OTA safeguards added here; compiled and offline-tested, **not installed on Colin's kart**. |
| `firmware/base-tbeam-s3` | Eamon's base modem; same shared GoKartProtocol. |
| `pit/` | Pit server, touchscreen kiosk, simulator, deployment/SD-card tools, course-map presets. Eamon reports Pi 4 + kart 2 radio/ping/OTA working. |
| `firmware/bench-control` | Separate removable motor-test experiments. Half-second 5 A forward probe succeeded over laptop BLE; longer attempts aborted. Not vehicle firmware. |
| Kart Pi display | Serial/map contracts exist; dedicated on-kart display integration remains open. |

The integration branch combines Eamon's work through `d7ade6f` and Colin's saved
telemetry/diagnostic work through `544b498`. Published imagery includes CT ECO
2023 Manresa and shop images; licensed SkyFi images stay local. See [map setup](pit/server/map/README.md).

The next refresh target is implemented: **5 Hz VESC polling and local USB display
data, 2 Hz LoRa per kart**. The integrated node uses PPS or pit-beacon timing;
its `rate 1|2` setting is persistent. Existing nodes saved at 1 Hz need `rate 2`.
Update base and kart firmware together for the new fixed 50 ms slots. The
telemetry recovery image uses unslotted 500 ms ±50 ms broadcasts for bench tests;
eight-kart RF performance is still unverified.

The installed telemetry image cannot accept STOP/START/mode requests. The
integrated node implements those as temporary VESC configuration writes, gated
by a pinned controller UUID, firmware 6.00 and a fresh live-limits read. STOP
never increases the observed current allowance; only current-max scale changes.
Readback verifies every transmitted limit. **START restores an allowance; it
does not command the wheels to spin.** Full hardware validation remains pending.

No VESC firmware or saved configuration was changed. Motor sensors are not wired;
motor temperature is unavailable. Pedal braking is mechanical, with the existing
zero motor-braking current preserved. Top-speed profiles, drift-cutoff tuning,
geofence enforcement and driver messaging remain separate future work.

## Run and check

Canonical clone: `~/Projects/50 Day/GoKart Control/`. Reuse the existing clone.
Git remote: `git@github.com:colinfost3r/GoKart-Control.git`.

```sh
python3 -B -m unittest discover -s tests -v
python3 -I pit/tests/test_core.py
python3 -I pit/tests/test_e2e.py
```

Root tests use Python and a C++ compiler, including sanitizer checks against the
actual VESC capture. Pit tests need pyserial and localhost sockets. No test
actuates a controller. See [integration guide](docs/integration.md) for firmware
builds and the separate diagnostic tests.

For a simulated pit:

```sh
# Separate terminals; the simulator supplies fake karts, clearly marked in the UI.
python3 pit/sim/sim_tbeam.py --tcp 7878
python3 pit/server/pit_server.py --tcp 127.0.0.1:7878
```

Open `http://127.0.0.1:8080/` or `/kiosk`. Remote browsers need the configured
password; deployment credentials and local imagery are not committed.

## Repository layout

```text
config/                     Planning metadata; not live controller settings
docs/                       Decisions, integration, test reports
hardware/                   Immutable photos, settings and test traces
firmware/heltec/             Tested telemetry-only recovery image (PlatformIO)
firmware/kart-heltec-v4/      Integrated command-capable kart node (Arduino CLI)
firmware/base-tbeam-s3/      Pit modem (Arduino CLI)
firmware/libraries/         Shared radio protocol and guarded VESC transport
firmware/bench-control/      Removable motor-test experiments
pit/                        Server, kiosk, simulator, maps and deployment
tools/                      Read-only VESC capture and Heltec provisioning/OTA
tests/                      Protocol and controller regression tests
```

Read the [actual motor-test results](docs/bench-control-test.md): five-second
and thirty-second sustained tests did not complete. Local radio TX is also not
proof of receipt at the pit. Do not confuse simulated, compiled, collaborator-
reported and locally verified results.

The repository is public. No overall redistribution license has been selected.
[Vendored VESC schemas](third_party/vesc_tool/README.md) retain GPL-3.0-or-later;
map provenance is documented separately.
