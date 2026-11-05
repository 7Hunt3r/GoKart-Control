# GoKart Control

A dashboard and shared pit system for eight electric karts at Manresa Island,
Connecticut. Each kart uses a Raspberry Pi 3 Model B, a 7-inch LESOWN L7-family touchscreen (1024×600 per supplied manual), a
Heltec V4.3 LoRa/GPS unit and a Bluetooth connection to its VESC. A Pi 4 hosts
the password-protected pit web interface for multiple simultaneous operators.

**The connected development kart is the primary hardware baseline.** The older
M5Stack repository is historical reference, not the specification for this rebuild.

## Start here

1. [Handoff](HANDOFF.md): current status, findings, next tasks and open questions.
2. [Measured VESC baseline](hardware/vesc/baseline-kart/2026-11-05/README.md):
   actual settings, evidence and interpretation.
3. [Requirements](docs/requirements.md): confirmed product behavior and deferred work.
4. [Architecture](docs/architecture.md): components, interfaces and failure boundaries.
5. [Hardware inventory](docs/hardware.md): verified vs reported vs unknown details.
6. [Contributing](CONTRIBUTING.md): repeatable setup and collaboration conventions.

## Current status

As of November 5, 2026:

- Read the connected VESC over USB without configuration writes or motor commands.
- Verified firmware 6.00, hardware identity `75_100_V2`, controller ID 69.
- Decoded 189 motor fields and 187 application fields with both schema signatures
  verified and every payload byte accounted for.
- Archived [eight hardware photos and connector notes](hardware/photos/2026-11-05/README.md),
  identifying the L7 display, Heltec V4.3 and Quectel L76K GPS module.
- Captured a stationary telemetry sample: 76.4 V, 22.3 C ESC temperature, no active fault.
- Prepared offline decode tools and protocol tests using the real captured data.
- **The dashboard, pit server, BLE adapter and LoRa firmware are not implemented.**
- No course image/route is packaged, and no radio/GPS/display integration is tested.

The captured configuration is the current **runtime** configuration. It is not
proof of the saved flash baseline, a tuning recommendation, or a flashable image.
Do not copy this kart's settings to other karts without inspecting their hardware.

## Product scope

| First implementation | Deferred |
| --- | --- |
| VESC telemetry over BLE; MPH dashboard | Driver messaging |
| ECO/NORMAL/SPORT retained pending inspection | Gradual remote stop |
| Offline course map with all eight kart positions | Geofence enforcement |
| Online/race-entry filters | Current-limit increases pending diagnosis |
| Beginner display hides driver-facing fault alerts | Automatic fault-based tuning |
| Pi 4 password-protected shared pit control | |
| Logs and addressed/fleet profile requests | |

Throttle and reverse remain wired to the ESC. Pedal braking is mechanical.
Motor braking is currently configured to 0 A. Motors have no temperature
sensors wired; motor temperature must display as unavailable, not 0 C.

## Local setup

Canonical location: `~/Projects/50 Day/GoKart Control/`.

```sh
git clone git@github.com:colinfost3r/GoKart-Control.git "$HOME/Projects/50 Day/GoKart Control"
cd "$HOME/Projects/50 Day/GoKart Control"
python3 -B -m unittest discover -s tests -v
```

If already cloned, reuse the existing project. Offline tools use Python 3.9+
standard library; no pip dependencies or connected hardware are needed for tests.
Serial capture supports macOS/Linux and requires device permissions. It is not
a Windows serial implementation.

For read-only hardware inspection, follow [tools/vesc/README.md](tools/vesc/README.md).
There is no app launch or deployment command yet.

## Repository layout

```text
README.md                 Project entry point and current status
HANDOFF.md                Next collaborator's starting point
CONTRIBUTING.md            Workflow and verification
AGENTS.md                 Project-specific agent conventions
config/project.json       Planning configuration; not a runtime app config
docs/                     Requirements, architecture, hardware and history
hardware/vesc/            Baseline evidence and decoded settings
tools/vesc/               Allowlisted read-only capture and offline decoder
tests/                    Protocol and baseline regression checks
third_party/vesc_tool/     Pinned 6.00 schemas, license and provenance
.github/                  Pull request template
```

No new application stack is selected. Avoid creating empty service skeletons
until the first runnable milestone has a clear interface and acceptance checks.

## Sources and licensing

- Current baseline: this repository's [controller capture](hardware/vesc/baseline-kart/2026-11-05/README.md).
- Historical app: [vesc-ble-dashboard](https://github.com/eamondoherty618-prog/vesc-ble-dashboard).
- [Upstream schema provenance and license](third_party/vesc_tool/README.md).

The repository is public. No overall redistribution license has been chosen.
Vendored VESC Tool files retain their own GPL-3.0-or-later terms.
