# Collaborator handoff

Updated 2026-11-06 (night), branch `eamon/firmware-lora-starter`. Read this, then
[README.md](README.md) for the architecture and quick starts. Each component has its own
README with full detail: [firmware](firmware/README.md), [pit](pit/README.md),
[kart](kart/README.md), [tools](tools/README.md).

Labels used below: **owner decision** (recorded as decided), **proposal** (built or suggested,
awaiting a decision), **verified** (seen on real hardware, with who/when), **simulated** (tests
and headless renders on a Mac only).

## What it is

Eight electric karts at Manresa Island, CT. Each kart has a VESC 6.00 motor controller with
wired throttle/reverse and mechanical brakes, a Heltec WiFi LoRa 32 V4.3 with a Quectel L76K GPS
that owns the VESC link over BLE, and a Raspberry Pi 3 B with a 7-inch LESOWN L7 1024x600 touch
panel that is display only. The pit has a Raspberry Pi 4 with a touch panel and a LilyGo T-Beam
S3 Core as the LoRa modem; a second T-Beam can act as a one-hop repeater. On top runs an
optional game layer (boost pads, mud, item boxes, laps, race start/finish, running positions,
synthesized sounds).

One to three development karts are planned, including one each for the owner and Eamon. Do not
assume all eight karts are configured identically. The owner's standing instruction: **the
connected kart is the starting point, ahead of the original GitHub app.**

Hardware in hand (from the bench logs):

| Unit | Board | State |
| --- | --- | --- |
| Kart 1 | Colin's Heltec, kart build flashed by Colin 2026-11-05 | Transmits with VESC linked; receives nothing (see Known issues) |
| Kart 2 | Eamon's Heltec V4.3 (`gokart-2.local`, 192.168.151.44) + Pi 3 B `gokart-kart-2` | Running; GPS daughterboard dead |
| Colin's VESC | the baseline controller (fw 6.00, `75_100_V2`, id 69) | Used for both kart 1 and kart 2 tests, one Heltec at a time |
| Pit | Pi 4 `gokart-pit.local` (192.168.151.240 on McLab) + T-Beam S3 on `/dev/tbeam` | Running; rebooted several times 2026-11-06 |
| Repeater | second T-Beam S3 | Running an older build; needs reflash |

## Current state (2026-11-06)

### Verified on hardware (all at 50 Day St, bench distances)

- **VESC baseline** (2026-11-05, USB, read-only): firmware 6.00, hardware `75_100_V2`, controller
  id 69; 189 motor and 187 app fields decoded with schema signatures verified; stationary sample
  76.4 V, ESC 22.3 C, no fault. No settings written.
- **Pit station** (2026-11-05): Pi 4 kiosk at the panel's 1024x600, pit server, T-Beam on
  `/dev/tbeam`, card made with `pit/sdcard`.
- **LoRa link** (2026-11-05): kart 2 received by the pit at 1 Hz, consecutive sequence numbers,
  zero loss, RSSI -13 dBm / SNR 13.5 dB side by side; later at 2 Hz with 50 ms slots. Kart 2 also
  hears kart 1 directly (kart-to-kart positions work).
- **Commands** (2026-11-05, kart 2): `ping` applied in 1-2 s, `stop` -> `no_vesc` and `start` ->
  `rejected` with no VESC attached (the honest answers).
- **Kart 1 over BLE** (2026-11-05, Colin): kart build at 2 Hz with the VESC linked over BLE,
  78.4 V, ESC 24 C, fault 0, received by the pit and kart 2.
- **First remote STOP on a real VESC** (2026-11-05 17:40, Eamon's Heltec as kart 2 pinned to
  Colin's controller by address + UUID, Colin's Heltec unplugged): each ramp step acknowledged and
  read back (scale 0.5, 0.2, 0.0); START restored 1.0.
- **OTA** (2026-11-05): kart 2 updated over WiFi (ArduinoOTA) into the BLE build.
- **Repeater** (2026-11-06): relays 100 % of kart 2's packets and every beacon, zero drops; pit
  drops the duplicate copies, loss stays 0 %; ping via the pit applied in 0.3 s with the repeater
  on; heartbeat chip at 16:43 showed repeater id 1, -3 dBm, carrying kart 2, age ~1 s.
- **Kart Pi 3 B** (2026-11-06 16:26): card from `kart/sdcard` booted, ~6 min to services up;
  cage + Chromium on the 1024x600 HDMI panel; `/dev/heltec` link reading kart 2; shop map pulled
  from the pit. HDMI audio: the vc4-hdmi PCM runs (IEC958, 48 kHz, 2 ch) while the page plays;
  nobody has listened yet.
- **Live chain bench** (2026-11-06, `tools/bench/e2e_bench.py`, kart 2): 16 checks pass (commit
  cc5c406). The kart-display race check was corrected afterwards (8146fd3); no run of the
  corrected check is recorded.
- **Kart 2 GPS outdoors** (2026-11-05 evening): fix with 5-11 satellites, shown on the shop map.

### Simulated only (tests + headless renders on the Mac)

- Game layer end to end: zones editor, GAME ON/OFF, boost / mud / item / lap line / track, laps
  and leaderboard on the pit, geofence engine and racing HUD on the kart. Real pit zones were
  `enabled: false` at deploy; nobody has driven through a zone.
- Race finish, STOP RACE, final results, race length, running positions carried in the beacon
  (27-byte beacon, committed 20:33) and the kart "P3" badge (committed 20:45).
- Kart GPS fusion (5 Hz `gps` line, hdop/sats gating, outlier rejection, 1.2 s dead reckoning),
  pit GPS correction (off by default), demo mode (rendered headless against kart 2's server).
- Pit reboot hardening (persistent Wi-Fi keyfiles + eth0 fallback, persistent journal, health
  chip, check timer, page self-reload, `why_rebooted.sh`): committed and deployed to the live pit
  2026-11-06 20:59 (1227ab4); not yet proven across a real power cut. See
  [pit/README.md](pit/README.md) "If the pit reboots".
- Pace car track recorder (record a kart's laps on the pit, use one as the track route):
  committed + deployed 20:59 (1227ab4), simulator-tested, never driven.
- Test counts at the last commits: pit 53 core + 6 e2e, kart 78 core + 9 mapsync + 5 e2e, VESC
  tools 8 (`tools/run_tests.sh`, all passing 2026-11-06 with the working tree as it stood).

### Open (not started or not yet tried)

- SLOW, BOOST and the race grid hold against a live VESC; wheels-up throttle check under STOP.
- Range test, dead-zone test with the repeater, eight-kart load test, PPS at the base (indoors).
- Touch, map redraw speed and demo mode by eye on the Pi 3 B panel.
- Formal bench validation of link loss / reconnect (requirements acceptance checks) before
  enabling profile writes; saved-flash VESC baseline; controller commercial model and ratings.
- Drift-cutoff logging (see below).

## How to operate a session

**Pit boot.** Power the Pi 4 with the official 5.1 V 3 A supply, T-Beam on USB, panel on HDMI0
with its touch USB on "5V IN + TOUCH". The kiosk opens by itself (`gokart-pit` +
`gokart-kiosk` services). Top bar: radio link, repeater chip (if one is out), GAME chip, SOUND
chip, and a health chip only when something is wrong (UNDERVOLT, throttled, had undervolt). If
the code changed since the last session, run `pit/deploy.sh` from the Mac first (last deployed
2026-11-06 20:59, 1227ab4).

**Kart boot.** Power the kart Pi and the Heltec (a separate regulated 5 V for the Heltec is the
target, not the VESC 3.3 V pin). The Heltec connects only to its pinned VESC; its OLED and the
driver page show the link. The driver page's PIT chip shows how well the kart hears the pit
(`base_rssi`; NO PIT at 0). Maps and zones arrive whenever the kart Pi reaches the pit over Wi-Fi
and survive reboots on the track. In the pit, `ping` each kart once.

**Start a race.** Zones drawn and saved on the operator page (ZONES), GAME ON. In the race panel
(operator page, kiosk RACE tab, or phone): choose 3 / 5 / 10 s and the laps (OPEN, 3, 5, 10), then
hold START RACE 1 s. Every kart holds at zero current (GRID) and gets GO at the same GPS-clock
instant; pages show the five-light countdown.

**Stop a race.** Hold STOP RACE 1 s: laps freeze, karts are placed and shown FINISH, final
results appear; NEW RACE clears them. STOP RACE does not stop karts. To stop karts use STOP per
kart or the hold-to-fire STOP ALL; START releases. GAME OFF is the race director's kill for zone
effects.

**Demo mode.** Kart display: hold the KART badge 3 s (config `demo: true`, the default). A
simulated kart drives the pit's track or zones on the real panel for up to 180 s; nothing is
written to the real Heltec. Hold again to end. Pit side: run the simulators on a laptop
(README quick start).

**When something breaks.**

| Symptom | Do this |
| --- | --- |
| Pit page frozen or blank | Pages reload themselves when the server restarts or their event stream dies for 20 s; else `ssh pit@gokart-pit.local sudo systemctl restart gokart-pit gokart-kiosk`. A JS error once killed every page (duplicate `gc`, fixed in 5ee4b76): check the browser console |
| Pit rebooted / no Wi-Fi | `sudo /opt/gokart-pit/tools/why_rebooted.sh` (add `-2` for the boot before); an UNDERVOLT / "had undervolt" chip means supply or cable. Wi-Fi by hand: pit/README.md "If the pit reboots" |
| Kart missing on the pit | Kart console `status`: `lora_rx`, `lora_rx_bad`, `sync`, `built`. Repeater LOST: check it is powered and reflashed |
| Kart not hearing the pit | Same `status`; compare firmware build with kart 2's; swap boards to isolate (kart 1 case) |
| No VESC link | `blescan`; a VESC connected to anything else (VESC Tool, another Heltec, Colin's bench image) does not advertise. `vescmac` must match |
| No GPS | `gpspin` (edges on RX and PPS), `gpsdump`, `gpstest 9600`, `gpsrecover`, `gpscold`. Indoor fixes are ~200 m off and frozen: test outside |
| Command `no_ack` | Kart offline or out of range; outcome unknown, nothing is replayed. Re-send by hand |
| OTA cannot find `gokart-N.local` | `espota.py -i <ip> -p 3232` (arduino-cli mDNS discovery fails; espota by IP works) |
| T-Beam will not take an upload | Still on Meshtastic: 1200-baud touch (firmware/README.md "Repeater"). Base re-flash from the Pi: `esptool --no-stub`, app only at 0x10000 |
| SD card flash stops at the boot partition | flash.sh now waits 5 min; both real cards needed a manual copy before that fix |
| No sound | Pit: tap once (TAP FOR SOUND, no autoplay flag). Kart: `GET /api/sound-test?alsa=1` from the Pi |
| Shop map dot looks off | Expected 25-40 m with SkyFi at the shop (use the CT ECO preset); also see History 2026-11-05 |

## Known issues

1. **Kart 2 GPS board dead** (2026-11-06): the L76K daughterboard no longer drives its TX line
   (0 edges after a 5 s cold power-off; PPS still ticking). Hardware: replace the GNSS board. Most
   likely damaged by the earlier swapped-pin search driving the module's TX; the firmware now never
   swaps UART pins (6c3bf58).
2. **Repeater needs a reflash** after the beacon change (cfa4796): its build predates the 27-byte
   beacon, so its `gkValidate` rejects the new beacons and it will not relay them (nor send its
   heartbeat, which rides behind the beacon copy). `./build.sh repeater upload ...`.
3. **Pit reboot cause unknown.** On 2026-11-06 the pit Pi rebooted several times, came back with no
   Wi-Fi profile, and the logs were gone (volatile journal; netplan-backed profiles left as 0-byte
   files; the Wi-Fi loss was at 18:10). Wi-Fi and log loss are fixed by 1227ab4 (deployed); the
   reboot itself is not explained. Next occurrence: `why_rebooted.sh`, check for under-voltage.
4. **Kart 1 receives nothing** (Colin's board, 2026-11-05): transmits fine, but hears no beacon and
   no commands. Same firmware as kart 2, so suspect the board (antenna, V4.2 vs V4.3 front end, or
   an older build). Needs its console `status` (`lora_rx`, `lora_rx_bad`, `built`) or a board swap.
   It also must be rebuilt from this branch (decision 2 below).
5. **Nothing has been driven through a real zone yet.** All game evidence is simulated.
6. **Boost and race have not run on a live VESC.** First test: one boost pad on a straight, kart in
   ECO, wheels up, confirm the scale read-back rises and drops after 5 s; then a race with one kart.
7. The 27-byte beacon base build and the kart P-badge firmware are committed but their flashing is
   not recorded (the pit server sends race positions to the base since 1227ab4).
8. One base build (queue dedupe + a different window expression) stopped receiving entirely on
   2026-11-05 and was reverted; cause not understood.
9. `base_rssi` 0 means "not heard", but side by side the true RSSI is 0 to -1 dBm, so the pit can
   show 0 on the bench. Outdoors it reads normally.

## Decisions for Colin

1. **Regen on STOP** (proposal). STOP currently ramps the current scale to zero and the kart
   coasts. The Heltec also sends a brake request to an on-VESC LispBM script
   (`firmware/vesc-lisp/gokart_brake.lisp`), which is a no-op unless the script is loaded and Motor
   Current Max Brake is above 0 (this fleet's baseline is 0 A). Loading the script and enabling
   brake current are per-kart VESC Tool steps and an owner decision; see
   [firmware/vesc-lisp/README.md](firmware/vesc-lisp/README.md).
2. **Rebuild kart 1 from this branch** (request). Its build predates the relay bit, the 47-byte
   telemetry, `slow_mph`, BOOST, RACE and the 27-byte beacon. Prebuilt image and console steps:
   [firmware/releases/README.md](firmware/releases/README.md) (that image is 0.1.0 and also
   predates them: build from source with `./build.sh kart`). Rollback: `pio run -t upload` from
   your branch.
3. **BLE binding: one Heltec per VESC** (owner decision 2026-11-05, recorded by Eamon in person:
   BLE on every kart). The Heltec connects only to the address pinned with `vescmac`, optionally
   also checking the UUID (`vescuuid`); unpinned it scans and never connects. A VESC connected to
   one Heltec does not advertise to another, so a spare board cannot silently grab a kart. Your
   bench image checks the UUID from FW_VERSION; this branch now pins both. Confirm this is the
   fleet rule and how pins are recorded per kart.
4. **VESC writes enabled by default** (deviation flagged for owner review). `ENABLE_VESC_WRITES=1`
   because pit STOP/START/mode is the feature under test; `docs/requirements.md` still says
   profile writes stay disabled until link-loss behaviour is validated. A `0` build answers every
   write `rejected`.
5. **Fleet frame** (open): the base decodes both your 47-byte `GK` bench frame and the
   GoKartProtocol frame; which becomes the fleet protocol is for the integration PR. Your
   `integration/command-config` branch last merged this branch at d98c41d (2026-11-05 16:58);
   everything since is only here.
6. **Game layer** (proposal): ECO/NORMAL karts get up to SPORT torque on a 1.0 boost pad. Whether
   the game is wanted, and the pad scale, are owner calls.

## Doc drift to reconcile

These were found during the 2026-11-06 hygiene pass and are outside this file:

- `docs/requirements.md` and `AGENTS.md` still list remote stop and geofence enforcement as
  deferred and profile writes as disabled; this branch implements remote STOP, SLOW, BOOST, RACE
  and game zones (zones are not enforcement). Owner decision needed before the docs change.
- `firmware/README.md`: header says "not yet run on hardware"; protocol paragraph says 46-byte
  packets at 1 Hz in 100 ms slots (now 47 bytes, 2 Hz, 50 ms slots, 27-byte beacon); STOP ramp
  listed as 0.6/0.3/0.1/0 over 1.5 s (code: 0.5/0.2/0.0 at 150 ms steps); "Not verified" list is
  stale; the table omits the repeater.
- Kart `help` text omits `gpscold`, `gpspin` and `gpsline`.
- `pit/README.md` status table and "Kiosk approach" still say the T-Beam link, first boot and
  kiosk are untested on hardware; `pit/sdcard/README.md` says the kit has not been booted.
- `kart/README.md` status table says first boot, `/dev/heltec` and the kiosk are untested on a
  Pi 3 B (verified 2026-11-06 16:26); its SLOW bullet still describes inference only.
- `tools/bench/e2e_bench.py` promises no write to a kart with a linked VESC without
  `--allow-writes`, but its race check always sends `race 3` (a 3 s grid hold at scale 0).
- `config/project.json` status is still `hardware_baseline_captured_application_not_implemented`.

## The connected kart: findings that matter

See the [full report and capture](hardware/vesc/baseline-kart/2026-11-05/README.md).

| Finding | Consequence |
| --- | --- |
| Firmware 6.00; hardware `75_100_V2`; firmware name `no_hw_limits` | Verify physical controller model/rating and firmware provenance before tuning. The name is not a rating. |
| Motor current max 140 A; battery current max 100 A | Old README's 240 A battery claim is not this kart's current baseline. |
| Absolute-current threshold 240 A | This is a different setting, not the battery-current limit. |
| Motor braking current 0 A; battery regen allowance -100 A | Preserve present behavior; do not enable regen while porting profiles. |
| Field-weakening current max 95 A | Existing behavior to investigate, not evidence it should be increased. |
| ADC and UART; Current Reverse Button | Consistent with hardwired throttle/reverse and a UART BLE module. BLE UUID/device identity still needs verification. |
| Motor-temperature sensor disabled; owner confirms no sensors wired | Show unavailable; do not interpret 0 C as a cool motor. ESC sensing is separate. |
| CAN multi-ESC enabled; controller ID 69 | Configuration does not prove a second controller is physically present. |
| 14 poles, ratio ~2.76923, wheel diameter 0.406 m | Configured values only; physically verify before using for MPH limits. |

GET_MCCONF/GET_APPCONF capture current runtime settings. The saved flash configuration has not
been independently established. The runtime scale was 1.0, but that alone does not prove there
are no temporary overrides elsewhere. Telemetry queries can reset the controller's averaging
accumulators as part of ordinary GET_VALUES behaviour, but do not change drive settings.

## Agreed behaviour (owner decisions)

- The owner approved the [initial telemetry fields](docs/requirements.md#agreed-telemetry-fields):
  kart identity/time, separate wheel/GPS speed, battery voltage/current, motor current, ESC
  temperature, faults, GPS position/quality and connection status. Motor temperature remains
  unavailable. Schema, update rates and stale thresholds were left open (this branch uses 2 Hz
  radio, online <= 5 s, stale <= 15 s: proposals).
- The Heltec owns the VESC link and kart supervisory logic; the Pi is display only. USB VESC
  access remains available for inspection. See [architecture](docs/architecture.md).
- Preserve hardwired driver inputs and mechanical brakes.
- Keep three profile names (ECO / NORMAL / SPORT). Desired eventual behaviour is top-speed limits
  with strong acceleration; do not replace current limits based on historical code.
- Losing the pit radio link should not stop the kart.
- **Reliability first:** loss of display, Heltec, BLE, GPS or pit communication must leave the ESC
  running its installed firmware and current active settings. This supersedes the earlier
  saved-configuration restore requirement. Do not change profiles, reset, stop or write settings
  because a link drops or returns. Reconnect read-only, reconcile actual state, never replay cached
  commands. Hardwired inputs and normal ESC protections remain authoritative; bench verification
  is still required. See [acceptance checks](docs/requirements.md#reliability-and-link-loss-behavior).
- Drivers see their own and other kart positions, with online and race-entry filters.
- MPH. Beginner mode hides driver-facing fault alerts, while pit/logging retains them.
- Driver messages, gradual remote stop and geofence enforcement were deferred (see Doc drift and
  decision 4). A future geofence should work locally without a pit link; the Heltec/BLE design
  cannot guarantee enforcement after a Heltec/BLE failure.
- BLE from the Heltec to the VESC on every kart (2026-11-05, see decision 3).

## Drift cutoff: what is known vs unknown

The owner reports torque cutting out during sustained drifting. We have one stationary sample
with no active fault, which does not rule out transient faults, current/voltage limiting or
thermal issues while driving. Log motor/battery current, pack voltage, ERPM, duty, ESC
temperature, throttle where available, fault state and requested/applied profile together. Read
BMS status only if a BMS is actually connected. Drift wheel speed and GPS ground speed differ; a
wheel-RPM limiter can cut propulsion during a drift at modest ground speed. Do not conclude that
higher current or fault thresholds are the fix without an event log and verified ratings.

## Earlier next-work list (2026-11-05, still valid where not done)

1. Identify the controller's commercial model, motor/battery/BMS ratings and BLE module; check
   whether all development karts match. Display USB goes to "5V IN + TOUCH", not the power-only port.
2. Establish the saved-config baseline without overwriting settings; do not power-cycle or change
   firmware merely to fill a documentation gap.
3. Heltec BLE telemetry spike: device selection, fragmentation and reconnect done on the bench;
   latency under concurrent GPS/LoRa/USB load and independent Heltec power/reset still to measure.
4. Eight-kart simulator and kart/pit views: done (simulated).
5. Heltec serial contract and two-radio bench link: done; eight-kart load test still to do.
6. Bench-verify accessory/link failures and reconnect (no state change, unchanged active settings,
   hardwired input response) before enabling profile writes.
7. Investigate drift cutoff with logs, then propose any tuning as a separate change.

## History

Condensed from the dated sections this file accumulated. Times are local (ET).

**2026-11-05**
- Repository scaffold, requirements, architecture, hardware inventory (Colin). Eight owner photos
  archived: LESOWN L7 7" 1024x600, Heltec HTIT-WB32LAF V4.3 with L76K, Pi 3 Model B V1.2.
- USB read-only VESC capture on `/dev/cu.usbmodem3041` (session-specific; never hardcode it).
  No writes, firmware update, reboot, motor test, CAN enumeration or terminal command.
- Eamon's firmware starter: kart node, base, shared libraries, pit station (26 tests). Both
  sketches compile (esp32 core 3.3.8); temp-limit payload checked against bldc `release_6_00`;
  mcconf offsets from the pinned schema. Base boots on the T-Beam S3 (PMU, SX1262, GNSS
  detected); the T-Beam shipped with Meshtastic and needed a 1200-baud touch for the first flash.
- Radio aligned to Colin's bench transmitter (915.0 MHz, BW500, SF7, CR4/5, sync 0x12, preamble 8);
  the base also decodes his 47-byte `GK` frame (`"bench": true`).
- 16:20 bench: pit live (kiosk 1024x600, SkyFi imagery local-only, site presets); kart 2 received
  at 1 Hz with zero loss, -13 dBm / 13.5 dB; GPS detected at 9600 (no fix indoors); ping applied in
  ~1 s; OTA into the BLE build. **Owner decision: BLE on every kart**; pinned-address-only
  connection (`./build.sh kart` builds BLE; `kart-uart` remains as the wired alternative, which
  was the earlier proposal). Colin's bench image held its VESC so the scan saw none, as expected.
- Bench plan proposed to Colin: prove reception first with his bench image (`id 1`,
  `tx on antenna-confirmed`), then run the command-capable kart build wheels-up. He flashed the
  kart build the same evening.
- 17:00-17:30: kart 2 fix outside (5-11 sats). SkyFi cut 25-40 m off at the shop (registration
  extrapolated from the island), bench preset uses the state ortho; the kart reading is 27 m east of
  the geocoded "50 Day St" point, both on the Harborside block, so the address point may be the
  wrong building (a phone reading at the Heltec's spot would settle it). Pit beacon (1 Hz),
  beacon sync, 2 Hz reporting with 50 ms slots, VESC UUID pin, loss counter, dead reckoning, phone
  page `/m`. A base build that queued commands for the tail window delayed them up to 20 s;
  commands now go out immediately unless the base has PPS. Ping applied in 2 s, stop -> no_vesc
  in 1 s, start -> rejected. Colin's kart 1 at 2 Hz with VESC over BLE (78.4 V, 24 C, fault 0),
  received by pit and kart 2, receives nothing itself.
- 17:40: first remote STOP on a real VESC. Root cause of earlier silent writes: the VESC BLE UART
  module negotiates MTU 23, so the 42-byte temp-config write is sent in 20-byte chunks. Writes echo
  the limits read from that controller at link-up, never capture constants. SLOW (ERPM cap from
  the controller's own poles/gear/wheel) and the regen brake request added.
- Colin's branches the same afternoon: `firmware/heltec-bench-ota` (bench BLE telemetry, LoRa TX,
  GPS, authenticated OTA), `diagnostics/removable-bench-control` (direct BLE motor-test evidence),
  `integration/command-config` (integrates this stack up to d98c41d, OLED status, gated writes).

**2026-11-06**
- Afternoon: repeater built (relay bit 0x80, one hop, 25 ms delay inside the slot); 100 % relayed,
  0 drops, ping 0.3 s. `slow_mph` in telemetry (47-byte packet; 46-byte still accepted); not
  exercised on a live VESC (Colin's VESC was off, `vesc_link false`). Base re-flashed from the Pi
  (`esptool --no-stub`, app at 0x10000); kart 2 by `espota.py -i 192.168.151.44 -p 3232`.
- 16:26: kart Pi 3 B first boot (see Verified). Open points: Pi 3 B Wi-Fi is 2.4 GHz only (does
  McLab offer 2.4 GHz?), Heltec power from the Pi's USB vs separate, pre-loading a map before the
  first pit contact.
- Repeater OLED: on the second T-Beam the QMC6310 compass answers at 0x3C (register 0 reads 0x80)
  and the screen sits at 0x3D; the repeater now picks the address itself.
- 16:43: repeater heartbeat (GK_MSG_REPEATER_INFO every 5 s); pit chip, LOST after 20 s.
- 16:53-17:22: game layer (boost override in firmware, zones contract, pit editor + laps, kart
  geofence + HUD; built partly by subagents), GPS upgrades (GPS+BDS+GLONASS, GGA+RMC, 5 Hz, `gps`
  USB line, hdop/fix_age/base fix on the self line), pit GPS correction (gated >= 6 sats, < 25 m).
  Indoor bench fixes were 200 m off and frozen: the building, not the map (OSM puts 50 Day St
  within 30 m of the marker).
- 17:31: duplicate `gc` declaration in the pit's `updateTop` was a SyntaxError that killed every
  pit page; fixed.
- 17:33-17:54: shop demo track in the lot south of 50 Day St; race start (grid hold +
  synchronized GO on the shared clock), item boxes, track route; base keeps `issued_utc` on
  retransmits so late receivers launch on the same GO; kart remembers the GPS baud and hard-resets
  the L76K when no baud locks for 48 s. Live bench tool: 16 checks pass.
- 18:19: GPS settled at 9600 baud at 5 Hz (the earlier 115200 setting is superseded), pins never
  swapped, console diagnostics; kart 2's L76K found dead (Known issue 1).
- 18:37: UI polish, shared synthesized sounds, STOP RACE, race length, final results, HDMI audio.
  Kart 2 deployed and checked live; pit not deployed (Pi down). Tests then: pit 43 + 3, kart 70 +
  9 + 5.
- Evening: pit Pi reboots; Wi-Fi lost at 18:10 and logs gone (Known issue 3).
- 20:33: beacon carries race state and positions (27 bytes; 22-byte beacons still accepted).
- 20:45: kart HUD race-position badge with chimes (78 kart core tests).
- 20:59: pit hardening (root cause of the Wi-Fi loss: firstboot's `nmcli modify` turned the
  cloud-init profile into `/etc/netplan/90-NM-*.yaml`, which the power cuts truncated to 0 bytes;
  journald was `Storage=volatile`), race positions to the base, pace car recorder; 53 core + 6 e2e
  pit tests; deployed live.

## Collaboration and delivery state

Repository: `git@github.com:colinfost3r/GoKart-Control.git`. The owner requests ongoing pushes
of coherent, checked milestones; fetch and reconcile before pushing; focused branches and PRs
for parallel work ([CONTRIBUTING.md](CONTRIBUTING.md)). No team members have been invited by
this project and no messages sent on anyone's behalf. Never put credentials or the pit password
into source control.
