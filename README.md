# GoKart Control

Fleet system for eight electric karts at Manresa Island, Connecticut: a supervisor
board on every kart, a shared LoRa radio network, a pit station with touch kiosk and
shared web control, a driver display on every kart, and an optional "Mario Kart" game
layer (boost pads, mud, item boxes, laps, race start and finish).

**Reliability first.** The VESC runs the motor; throttle and reverse stay wired to it and
braking is mechanical. Losing the display, Heltec, BLE, GPS or pit link never changes the
kart's active settings. Nothing is written to a VESC on boot, reconnect or retransmit;
only an explicit pit or console command writes, and only a temporary (`store=0`) setting.
See [docs/requirements.md](docs/requirements.md#reliability-and-link-loss-behavior).

**The connected development kart is the hardware baseline**
([measured capture](hardware/vesc/baseline-kart/2026-11-05/README.md)). The older M5Stack
dashboard repository is historical reference only.

Status in this file is as of **2026-11-06**, branch `eamon/firmware-lora-starter`.
Details, evidence and open decisions: [HANDOFF.md](HANDOFF.md).

## Architecture

```text
            Kart N (x8)                                     Pit
 +------------------------------------+        +----------------------------------+
 | VESC 6.00  <--- BLE --->   Heltec |        | T-Beam S3 "base" --USB--> Pi 4   |
 |  (motor,     pinned addr    V4.3   |  LoRa  |  beacon 1/s, cmds   pit_server   |
 |   wired      + UUID check   L76K   | <====> |                     kiosk 1024x600|
 |   throttle)                 GPS    | 915 MHz|                     web :8080    |
 |                  USB serial  |     |  2 Hz  |                     phone /m     |
 |                 (JSON lines) v     |        +----------------------------------+
 |              Pi 3 B driver display |               ^
 |              7" L7 1024x600 :8081  |   T-Beam S3 "repeater" (optional, one hop)
 +------------------------------------+
```

| Piece | Hardware | Code | Role |
| --- | --- | --- | --- |
| Kart supervisor | Heltec WiFi LoRa 32 V4.3 + Quectel L76K GPS | [firmware/kart-heltec-v4](firmware/kart-heltec-v4) | Owns the VESC link over BLE (owner decision; one Heltec pinned to one VESC by address, UUID check), GPS, LoRa, OLED, USB JSON to the kart Pi, WiFi OTA. Applies STOP / START / ECO-NORMAL-SPORT / SLOW / BOOST / RACE only on an explicit command |
| Radio | SX1262, 915.0 MHz, BW500, SF7, CR4/5, sync 0x12 | [firmware/libraries/GoKartProtocol](firmware/libraries/GoKartProtocol/GoKartProtocol.h) | Each kart reports twice a second in its own 50 ms slot (2 Hz default, `rate 1` for 1 Hz / 100 ms slots), aligned to GPS PPS or to the pit beacon. 47-byte telemetry (46-byte still accepted), 27-byte beacon carrying race state and positions (22-byte still accepted) |
| Pit modem | LilyGo T-Beam S3 Core | [firmware/base-tbeam-s3](firmware/base-tbeam-s3) | Receives every kart, sends commands in their own window, beacons once a second; JSON lines to the Pi per [SERIAL_PROTOCOL.md](firmware/base-tbeam-s3/SERIAL_PROTOCOL.md) |
| Repeater | second T-Beam S3 Core | [firmware/repeater-tbeam-s3](firmware/repeater-tbeam-s3) | Re-sends every packet once, 25 ms later inside the same slot, relay bit 0x80 set; heartbeat every 5 s shown as a pit chip |
| Pit station | Raspberry Pi 4 + HDMI touch panel | [pit/](pit/README.md) | Fleet state, command delivery with per-kart outcomes, race/laps, zones editor, session logs; touch kiosk, password-protected operator page, phone page at `/m` |
| Kart display | Raspberry Pi 3 B + LESOWN L7 7" 1024x600 | [kart/](kart/README.md) | Display only (never talks to the VESC): speed, mode, banners, map from the pit, own geofence engine for the game, sounds |
| On-VESC script (optional) | VESC LispBM | [firmware/vesc-lisp](firmware/vesc-lisp/README.md) | Regen on STOP; not loaded on any kart (owner decision pending) |

**Game layer** (off by default; contract [pit/server/map/ZONES.md](pit/server/map/ZONES.md)).
The pit draws zones and a track on the operator page and serves them with the map package.
Each kart Pi evaluates them on its own 5 Hz GPS and sends the Heltec `boost SECS SCALE`:

- **boost pad**: full current scale for a few seconds (never above 1.0, auto-reverts);
- **mud**: reduced scale while inside;
- **item box**: kart rolls boost / super / mud / empty;
- **lap line**: lap timing on the kart and, independently, on the pit (leaderboard);
- **race**: START RACE holds every kart at scale 0 (grid hold), then GO at the same GPS-clock
  instant on every kart; optional lap count, STOP RACE, final results, running positions
  carried in the beacon so karts show "P3" with no Wi-Fi;
- **track**: a drawn route band, no game effect.

A pit STOP always wins. **Sounds** are synthesized in the browser (`static/sound.js`, the same
file on pit and kart): countdown lights, GO, boost, mud, item roulette, lap, finish, STOP.
Kart panel speakers are fed over HDMI audio.

## Repository map

```text
firmware/           Arduino sketches (kart, base, repeater), shared libraries, build.sh,
                    releases/ (prebuilt kart image), vesc-lisp/ (optional brake script)
pit/                Pi 4 pit station: server/, sim/, tests/, sdcard/ kit, deploy.sh, install.sh
kart/               Pi 3 B kart display: server/, sim/, tests/, sdcard/ kit, deploy.sh, install.sh
tools/              run_tests.sh (all suites), bench/e2e_bench.py (live chain check),
                    vesc/ (read-only VESC capture + offline decoder)    -> tools/README.md
tests/              VESC capture/decoder regression tests (real baseline data)
hardware/           Immutable evidence: VESC baseline capture, hardware photos
docs/               Requirements, architecture, hardware inventory, previous iteration
third_party/        Pinned VESC Tool 6.00 schemas (GPL-3.0-or-later), provenance
config/project.json Planning values mirroring docs/ (not a runtime config)
HANDOFF.md          Current state, how to run a session, known issues, decisions, history
CONTRIBUTING.md     Branches, PRs, tests before pushing, deploy paths, who does what
AGENTS.md           Rules for coding agents working in this repo
```

## Status by feature (2026-11-06)

"Hardware" means seen working on real boards at 50 Day St; "simulator" means unit/e2e
tests and headless renders on a Mac only. Nothing has been run at Manresa Island.

| Feature | Status |
| --- | --- |
| VESC baseline read over USB (fw 6.00, `75_100_V2`, id 69) | **Hardware** 2026-11-05, read-only |
| Kart Heltec: BLE link to a pinned VESC, telemetry (78.4 V, 24 C, fault 0) | **Hardware** (kart 1 on Colin's VESC; kart 2 pinned to Colin's VESC) |
| LoRa kart -> pit telemetry, 2 Hz, zero loss on the bench | **Hardware** (bench only; no range test) |
| Pit -> kart commands: ping / stop / start outcomes | **Hardware**, kart 2 (ping applied 0.3-2 s) |
| Remote STOP on a live VESC (scale 0.5 / 0.2 / 0.0 read back), START back to 1.0 | **Hardware** 2026-11-05 17:40, kart 2 Heltec on Colin's VESC, wheels-up throttle check still to do |
| SLOW (ERPM cap) | Built; in telemetry as `slow_mph`; **not run on a live VESC** |
| Regen on STOP (LispBM script) | Built; **not loaded on any kart** (owner decision) |
| Repeater relay + heartbeat chip | **Hardware** bench (100 % relayed, 0 drops); reflashed 0.2.1 on 2026-11-07 (27-byte beacons, 22 dBm, boosted RX); range not measured |
| Pit Pi 4 kiosk, server, T-Beam on `/dev/tbeam`, SD kit first boot | **Hardware** 2026-11-05 |
| Pit reboot hardening (persistent Wi-Fi keyfiles, eth0 fallback, persistent journal, health chip, check timer, page self-reload, `why_rebooted.sh`) | Committed 2026-11-06 20:59 (1227ab4) and deployed to the live pit; not yet proven across a real power cut. See [pit/README.md](pit/README.md) "If the pit reboots" |
| Pace car: record a track by driving it, use it as the route | Committed + deployed on the pit 20:59 (1227ab4); tested in the simulator, not yet driven |
| Kart Pi 3 B display, SD kit first boot, `/dev/heltec` link, map from the pit | **Hardware** 2026-11-06 16:26 (kart 2); touch and map speed not yet checked by eye |
| Kart HDMI audio | **Hardware** kart 2 (PCM running); nobody has listened yet |
| Kart GPS 5 Hz line, filtering, dead reckoning | Firmware + simulator, not confirmed on a board; kart 2's L76K board **failed** (no TX), replace it |
| Pit GPS correction (pit radio fix vs surveyed marker) | Simulator; off by default |
| Zones editor, GAME ON/OFF, boost / mud / item / lap / track | **Simulator**; zones never driven through on a real kart; boost never run on a live VESC |
| Race start (grid hold + synchronized GO) | Simulator end to end. Live bench run on kart 2 (16 checks pass, 2026-11-06, cc5c406) sent `race` over LoRa; the display-side race check was corrected later and has no recorded run. Grid hold on a live VESC not exercised |
| Race finish, STOP RACE, final results, running positions in the beacon, kart "P3" badge | **Simulator** (beacon firmware committed 2026-11-06 20:33; compiling/flashing it is not recorded) |
| Demo mode on the kart display | Rendered headless against kart 2's server, zero console errors; not yet watched on the panel (simulated kart, nothing written to the Heltec) |
| Profile writes / validated link-loss behaviour (requirements acceptance) | **Not started** as a formal bench validation |
| Radio power: T-Beams 22 dBm + boosted RX (flashed 2026-11-07), kart front end 16 dBm drive (~27 dBm) with stored `txpower` | **Hardware** base + repeater; kart build not yet flashed |
| Range test, dead-zone test, eight-kart load test, drift-cutoff logging | **Not started** (antenna mast for the pit still to build) |

## Quick starts

All commands from the repo root unless noted. Python 3.9+ with `pyserial` for the servers;
the tests need nothing else. Firmware needs `arduino-cli` with `esp32:esp32@3.3.8` (setup in
[firmware/README.md](firmware/README.md#build-on-a-new-machine)).

**Run the tests** (no hardware, about 3 minutes):

```sh
tools/run_tests.sh            # all suites, summary table, exit 1 on any failure
tools/run_tests.sh pit-core   # one suite (names: vesc-tools pit-core pit-e2e kart-core kart-mapsync kart-e2e)
```

**Run the simulators on a Mac**, pit and kart side by side:

```sh
# pit: simulated T-Beam with eight karts + the pit server
python3 pit/sim/sim_tbeam.py --tcp 7878 &
python3 pit/server/pit_server.py --tcp 127.0.0.1:7878 &
open http://127.0.0.1:8080/ http://127.0.0.1:8080/kiosk http://127.0.0.1:8080/m

# kart: fake pit map server + simulated Heltec driving the game course + the kart display
python3 kart/sim/fake_pit.py --port 18080 --zones kart/sim/zones.course.json &
python3 kart/sim/sim_heltec.py --tcp 7979 --course &
python3 kart/server/kart_server.py --tcp 127.0.0.1:7979 --pit-url http://127.0.0.1:18080 &
open http://127.0.0.1:8081/          # best in a 1024x600 window
```

Simulated data always shows a striped SIMULATION banner. Scenario flags: `--help` on each
simulator; more in [pit/README.md](pit/README.md#run-it-locally-mac-or-linux) and
[kart/README.md](kart/README.md#run-it-locally-mac).

**Flash a kart Heltec** (prebuilt image or from source; full steps in
[firmware/releases/README.md](firmware/releases/README.md)):

```sh
cd firmware && ./build.sh kart upload /dev/cu.usbmodemXXXX     # BLE build (fleet default)
# then on the board's USB console (115200, newline):
id 1
blescan                     # VESC powered and not connected to anything else
vescmac AA:BB:CC:DD:EE:FF   # pin this kart's VESC; it connects to nothing else
vescuuid now                # optional: also pin the controller UUID
wifi <ssid> <password>      # optional, for OTA
reboot
status
```

Later updates over Wi-Fi: `./build.sh kart ota gokart-N.local`, or `espota.py -i <ip> -p 3232`
when mDNS fails. Base and repeater: `./build.sh base upload ...` / `./build.sh repeater upload ...`
(a T-Beam still on Meshtastic needs the 1200-baud touch in firmware/README.md).
First test of any write-capable build with the drive wheels off the ground.

**Flash a pit card** (Pi 4; details in [pit/sdcard/README.md](pit/sdcard/README.md)):

```sh
cd pit/sdcard
cp secrets.env.example secrets.env     # fill in Wi-Fi, PI_PASSWORD, PIT_WEB_PASSWORD (gitignored)
diskutil list external physical        # find /dev/diskN
FLASH_CONFIRM=/dev/diskN ./flash.sh /dev/diskN
```

**Flash a kart card** (Pi 3 B; details in [kart/README.md](kart/README.md#sd-card-kit-sdcard)):

```sh
cd kart/sdcard
cp secrets.env.example secrets.env     # set KART_ID, Wi-Fi, PI_PASSWORD, SSH_PUBKEY_FILE
FLASH_CONFIRM=/dev/diskN ./flash.sh /dev/diskN
```

Both kits write Raspberry Pi OS Trixie Lite (default image path
`~/studebaker-headunit/images/2026-10-06-raspios-trixie-arm64-lite.img.xz`, override with
`GOKART_IMG`). First boot installs everything (10-15 min). Delete `sdcard/build/` after
flashing: it contains secrets. App updates afterwards: `pit/deploy.sh` and
`kart/deploy.sh gokart-kart-N.local` from the Mac.

**Run the live bench** (pit -> T-Beam -> LoRa -> kart Heltec -> kart Pi), on the pit Pi:

```sh
scp tools/bench/e2e_bench.py pit@gokart-pit.local:/tmp/
ssh pit@gokart-pit.local python3 /tmp/e2e_bench.py --kart 2
```

Read [tools/README.md](tools/README.md) first: it sends a `race` command, which holds a kart
with a linked VESC at zero current for 3 s.

## Console commands

- **Kart Heltec**: type `help` on its USB console. Current list (from the source):
  `status`, `id N`, `mode 0..2`, `stop`, `start`, `slow [mph]`, `boost SECS SCALE`,
  `race [secs]`, `ping`, `rate 1|2`, `blescan`, `vescmac XX:..|clear`,
  `vescuuid <hex>|now|clear`, `wifi SSID PASS`, `otapw PASS`, `reboot`, and GPS diagnostics
  `gpsreset`, `gpsrecover`, `gpscold`, `gpspin`, `gpsdump`, `gpstest BAUD`, `gpsline on|off`.
  Command semantics: [firmware/README.md](firmware/README.md#what-the-kart-does-with-a-command).
- **Base T-Beam**: JSON command lines and `status` / `wifi` / `otapw` / `reboot`, see
  [SERIAL_PROTOCOL.md](firmware/base-tbeam-s3/SERIAL_PROTOCOL.md).
- **Repeater**: `oled sh1106|ssd1306 [3c|3d|auto]`, `status`, `reboot`.
- **Pit HTTP API**: [pit/README.md](pit/README.md) "Behaviour" and "Game zones and laps".
- **Kart HTTP API**: [kart/README.md](kart/README.md#behaviour).

## Local setup

Canonical location: `~/Projects/50 Day/GoKart Control/` (Eamon's clone is `~/GoKart-Control`).

```sh
git clone git@github.com:colinfost3r/GoKart-Control.git "$HOME/Projects/50 Day/GoKart Control"
cd "$HOME/Projects/50 Day/GoKart Control"
git checkout eamon/firmware-lora-starter
pip3 install pyserial            # pit/kart servers only
tools/run_tests.sh
```

Serial capture tools support macOS/Linux, not Windows. For read-only VESC inspection follow
[tools/vesc/README.md](tools/vesc/README.md). Reference documents:
[requirements](docs/requirements.md), [architecture](docs/architecture.md),
[hardware inventory](docs/hardware.md), [hardware photos](hardware/photos/2026-11-05/README.md),
[telemetry agreement](docs/requirements.md#agreed-telemetry-fields). Some statements there
predate this branch; see "Doc drift" in [HANDOFF.md](HANDOFF.md#doc-drift-to-reconcile).

The captured configuration is the controller's **runtime** configuration, not a verified
saved-flash backup or a tuning recommendation. Do not copy one kart's settings to another
without inspecting its hardware. Motors have no temperature sensors wired: motor temperature
shows as unavailable, never 0 C.

## Sources and licensing

- Current baseline: this repository's [controller capture](hardware/vesc/baseline-kart/2026-11-05/README.md).
- Historical app: [vesc-ble-dashboard](https://github.com/eamondoherty618-prog/vesc-ble-dashboard).
- [Upstream schema provenance and license](third_party/vesc_tool/README.md).

The repository is public. No overall redistribution license has been chosen.
Vendored VESC Tool files retain their own GPL-3.0-or-later terms.
