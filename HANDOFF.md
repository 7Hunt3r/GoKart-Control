# Collaborator handoff

Updated November 5, 2026. Start with this file, then read the linked baseline
report before implementing VESC commands.

## What we are building

An eight-kart system at Manresa Island, CT. The Heltec owns each kart's VESC
BLE connection, telemetry, GPS, LoRa and eventual supervisory profile logic.
Each Pi 3 B only runs the touchscreen interface/map, connected to the Heltec
through proposed USB serial. The Pi 4 pit server owns fleet/race state and the base-station radio;
multiple password-authenticated browsers share control through that server.
There are one to three development karts planned, including one each for the
owner and Eamon. Do not assume all eight karts have identical configurations.

The owner's latest instruction is explicit: **use the connected kart as the
starting point, ahead of the original GitHub app.** Preserve existing behavior
while establishing a trustworthy telemetry and configuration baseline.

## What is completed

- Repository scaffold, requirements, architecture and hardware inventory.
- Eight owner hardware photos archived with hashes and an identification guide.
  Display: LESOWN L7 family, 7-inch 1024×600 per manual, HDMI + USB touch.
  Radio/GPS: Heltec HTIT-WB32LAF V4.3 with Quectel L76K and patch antenna.
  Board pictured: Pi 3 Model B V1.2. These are identifications, not live tests.
- USB read-only inspection of one controller on `/dev/cu.usbmodem3041` on this Mac.
  That path is session-specific; do not hardcode it into application code.
- Runtime configuration and a stationary telemetry sample captured and decoded.
- Raw response payloads, capture manifest/hashes and schema provenance retained.
- Read-only capture/offline decode tools, and tests against the real baseline.

No controller settings were written. No firmware update, reboot, motor test,
CAN enumeration, terminal command or motor actuation was performed. Telemetry
queries can reset the controller's averaging accumulators as part of ordinary
GET_VALUES behavior, but do not change drive settings.

The application has not been built. BLE operation with a Heltec, LoRa coverage,
GPS reception, touchscreen behavior and failure recovery remain untested.

## Firmware starter (branch `eamon/firmware-lora-starter`, November 5, 2026)

Eamon's branch adds the first runnable firmware; see [firmware/README.md](firmware/README.md).

- `firmware/kart-heltec-v4`: Heltec V4.3 kart node. VESC link (wired UART by default,
  BLE build available), 5 Hz telemetry poll, read-only GET_MCCONF reconciliation of
  the live current scale, GPS with PPS-aligned LoRa slot, one 46-byte telemetry packet
  per second, OLED status, USB JSON lines for the kart Pi, WiFi credentials in flash,
  ArduinoOTA. Mode/stop/start are applied only on an explicit pit or USB command;
  nothing is written on boot, reconnect or retransmit (reliability rule honored).
  **Deviation flagged for owner review:** the default build has VESC writes enabled
  (`ENABLE_VESC_WRITES=1`) because pit start/stop/mode is the feature Eamon is testing;
  `docs/requirements.md` still says writes stay disabled until validated. A `0` build
  answers every write `rejected`.
- `firmware/base-tbeam-s3`: pit LoRa modem on the LilyGo T-Beam S3 Core, JSON over USB
  per [SERIAL_PROTOCOL.md](firmware/base-tbeam-s3/SERIAL_PROTOCOL.md), "karts online"
  OLED, ArduinoOTA.
- `firmware/libraries/`: shared packet format and a self-contained VESC 6.00 UART layer
  (the old Core2 sketch needed a hand-edited VescUart; this does not).
- `pit/`: Pi 4 pit station. `pit/server` (Python stdlib + pyserial): T-Beam serial link
  with reconnect, fleet state with explicit online/stale/offline and null-not-zero,
  command delivery with retries and eight per-kart outcomes, JSONL session logs,
  password-protected operator page plus a 1024x600 touch kiosk page (per-kart
  ECO/NORMAL/SPORT/STOP/START, hold-to-fire STOP ALL, race entry filters and race
  mode, local-meters map with optional georeferenced imagery). `pit/sim` fakes eight
  karts over a pty so the whole loop runs on a laptop; 26 unit/end-to-end tests pass.
  `pit/sdcard` is a cloud-init SD kit (hostname gokart-pit, kiosk via cage+Chromium,
  udev `/dev/tbeam`, systemd units). Tested only on the Mac against the simulator;
  nothing has run on a Pi yet. See [pit/README.md](pit/README.md).

Verified: both sketches compile (esp32 core 3.3.8); temp-limit payload checked against
bldc release_6_00; mcconf offsets computed from the pinned schema; the base firmware
boots on Eamon's T-Beam S3 with PMU, SX1262 and GNSS detected and USB JSON flowing;
the pit server drove it over USB and a command was transmitted over LoRa.
Not verified: any received LoRa packet, GPS fix/PPS, the kart node on hardware.

Radio reconciliation with Colin's `firmware/heltec-bench-ota` (PR #2): this branch now
uses the bench radio settings (915.0 MHz, BW500, SF7, CR4/5, sync 0x12, preamble 8) and
the T-Beam decodes the bench 47-byte `GK` frame alongside GoKartProtocol frames (byte 0
distinguishes them), emitting it as a `tele` line with `"bench":true`. So Colin's
installed transmitter and Eamon's receiver can be tested without reflashing the Heltec.
Which frame becomes the fleet protocol is still an open decision for the integration PR. The proposed VESC transport is wired UART from the Heltec (the
BLE module comes off the COMM port); `docs/architecture.md` still prefers BLE, so this
is a second point to settle with the owner.

## Bench results, 2026-11-05 16:20 (Eamon, at 50 Day St)

Verified on hardware today, all on branch `eamon/firmware-lora-starter`:

- **Pit station live:** Pi 4 `gokart-pit.local` with the touch kiosk at the panel's native
  1024x600, pit server, T-Beam S3 on `/dev/tbeam`. October 2026 satellite imagery
  (SkyFi, local-only) on the map with site presets for the shop and Manresa Island.
- **First over-the-air link:** Eamon's Heltec V4.3 running `kart-heltec-v4` (kart id 2)
  is received by the T-Beam at 1 Hz with consecutive sequence numbers and zero loss,
  RSSI -13 dBm / SNR 13.5 dB side by side. GPS detected at 9600 baud (no fix indoors).
- **Command loop:** from the pit UI, `ping` came back `applied` in about one second on
  the first transmission; `stop` came back `no_vesc` and `start` `rejected`, the honest
  answers with no VESC attached. Per-kart outcomes show on the kiosk.
- **OTA:** the kart node took a WiFi firmware update (`gokart-2.local`, ArduinoOTA) and
  rebooted into the BLE build. No cable needed for kart firmware from here on.
- **Owner decision (Eamon, in person): BLE from the Heltec to the VESC on every kart.**
  `./build.sh kart` now builds the BLE transport by default; `kart-uart` is the wired
  alternative. The kart connects **only** to the VESC address pinned with `vescmac`;
  unpinned it scans, reports candidates (`{"t":"ble",...}` over USB and on the OLED)
  and never connects. With Colin's bench image holding its VESC's connection, the
  scan sees no VESC at all, as expected (a connected BLE peripheral stops advertising).

Later the same evening (17:00-17:30):
- Kart 2 got a GPS fix outside (5-11 satellites) and appeared on the shop map; the
  SkyFi cut proved 25-40 m off at the shop (registration extrapolated from the island),
  so the bench preset uses the state ortho there. The dot still looked off to Eamon;
  the kart's reading is 27 m east of the geocoded "50 Day St" point and both fall on
  the Harborside block, so the address point itself may be the wrong building. A phone
  reading at the Heltec's spot would settle it.
- Pit beacon (1 Hz), beacon-sync for karts without PPS, 2 Hz reporting with 50 ms
  slots, VESC UUID pin, loss counter and dead reckoning on the kiosk, and a phone page
  at `/m` are in and deployed. A base build that queued commands for the tail window
  delayed them by up to 20 s; commands now go out immediately unless the base has PPS.
  One base build (queue dedupe + a different window expression) stopped receiving
  entirely and was reverted; cause not yet understood.
- Full command loop verified on kart 2 with the fixed base: ping applied in 2 s, stop
  -> no_vesc in 1 s, start -> rejected (no VESC attached). Kart 2 also receives kart 1's
  packets directly (kart-to-kart positions will work).
- **Colin flashed his Heltec with the kart build:** kart 1 transmits at 2 Hz with the
  VESC link up over BLE (78.4 V, ESC 24 C, fault 0) and is received by both the pit and
  kart 2, but it receives nothing itself: no beacon, no commands. Same firmware as kart 2,
  so suspect the board (antenna, front-end variant V4.2 vs V4.3, or an older build).
  Needs its console `status` (lora_rx, lora_rx_bad, built) or a bench swap of boards.

**17:40, first verified remote STOP on a real VESC.** Eamon's Heltec (kart 2) was pinned to
Colin's controller (address + UUID check) with Colin's own Heltec unplugged. Root cause of
the earlier silent writes: the VESC BLE UART module negotiates MTU 23, so the 42-byte
temp-config write has to be sent in 20-byte chunks. Each ramp step was then acknowledged
and read back (scale 0.5, 0.2, 0.0); START restored 1.0. Writes now echo the limits read
from that specific controller at link-up, never capture constants.

**SLOW** (cap speed to N mph via the ERPM limit, using the controller's own poles/gear/wheel)
was added for the pit, kiosk and phone page. **Regen on STOP**: the Heltec now also sends a
brake request to an on-VESC LispBM script, `firmware/vesc-lisp/gokart_brake.lisp`; without
the script, or with Motor Current Max Brake = 0 (this fleet's baseline), it is a no-op and the
kart coasts. Loading the script and enabling brake current are per-kart VESC Tool steps and an
owner decision; see `firmware/vesc-lisp/README.md`.

Not yet: wheels-up throttle check during STOP (next), the brake script on any kart, a range
test, PPS at the base (indoors), alignment field check, kart 1's receive problem.

**For Colin, to run the command-capable build on his kart (his decision):** a prebuilt
image and the console steps are in [firmware/releases/README.md](firmware/releases/README.md); or build it:

```sh
cd firmware && ./build.sh kart upload /dev/cu.usbmodemXXXX   # BLE build
# on the board's USB console:
id 1
vescmac <address>      # the address your bench image logs as ble_candidate address=...
```

Close the bench image's connection first (or power its board off) so the VESC
advertises again. First test with the drive wheels off the ground. Rollback is your
`pio run -t upload`. Open integration item: your image verifies the VESC UUID from
FW_VERSION; mine pins the BLE address instead. Both is best; worth adding to mine.

## Bench test with Colin, 2026-11-05 evening: link first, then kill switch (proposal to Colin)

The pit side is running: Pi 4 (`gokart-pit.local`, 192.168.151.240 on McLab) with the
touch kiosk, the pit server and the T-Beam S3 modem on `/dev/tbeam`, listening on the
bench radio settings (915.0 MHz, BW500, SF7, CR4/5, sync 0x12). It decodes your
47-byte `GK` frame as well as the GoKartProtocol frame.

**Step 1, no decision needed: prove reception.** On your Heltec's USB console:

```
id 1
tx on antenna-confirmed
```

Kart 1 should appear on the pit screen within two seconds with your VESC voltage,
GPS state and RSSI/SNR. The pit logs every line to `pit/logs/session-*.jsonl` on the
Pi, so we get a loss count from your sequence numbers.

**Step 2, your decision: remote stop needs command-capable firmware on the kart.**
`firmware/heltec` (your bench image) cannot receive commands and has no VESC write
path, by design. The only way to stop/start/change mode from the pit is to run
`firmware/kart-heltec-v4` on the kart's Heltec, in its BLE variant, which uses the
same VESC BLE UART module your image uses. What it does, precisely:

- Writes to the VESC only on an explicit pit or USB command (`stop`, `start`,
  `mode N`). Never on boot, reconnect, link loss or retransmit (your reliability rule).
- `stop` = COMM_SET_MCCONF_TEMP with `store=0`, ramping `l_current_max_scale`
  0.6, 0.3, 0.1, 0 over about 1.5 s; the baseline ERPM/duty/watt limits are echoed back
  unchanged and battery current limits are not sent. Mechanical brakes and the
  hardwired throttle are untouched; the kart coasts. `start` re-applies the requested
  mode or the scale observed before the stop. A VESC power cycle returns to saved config.
- After every write it asks for an ack and re-reads GET_MCCONF, and reports
  `applied` only if the live scale matches; otherwise `unknown`.
- Identity: connects only when exactly one `VESC BLE UART` is in range, or to a pinned
  address (`vescmac AA:BB:CC:DD:EE:FF` on the console). It does not check the UUID
  the way your image does; that is a gap worth closing in the integration.
- It has never run on hardware. Your image is backed up and rebuilds from your branch,
  so this is reversible in a minute.

Suggested first test: kart on a stand or drive wheels off the ground, one person at
the kart, the other at the pit screen. Press STOP on Kart 1; expect `accepted` then
`applied` and the STOPPED badge. Press START; expect the same. Then try a throttle
blip with the wheels in the air under STOP (no torque expected) and after START.

**How to flash it** (either works):

- From Eamon's Mac: plug the Heltec in and run `cd firmware && ./build.sh kart-ble upload /dev/cu.usbmodemXXXX`,
  then on its console `id 1` (and `wifi <ssid> <pw>` + `reboot` if you want OTA later).
- From your Mac, with arduino-cli: see "Build on a new machine" in [firmware/README.md](firmware/README.md).

Going back: `pio run -d firmware/heltec -t upload` from your branch, as documented in your README.

## 2026-11-06 afternoon (Eamon, 50 Day St): repeater, SLOW in telemetry, kart display kit

- **LoRa repeater built and running** on the second T-Beam S3 Core (`firmware/repeater-tbeam-s3`,
  see firmware/README.md "Repeater"). Bench: hears kart 2 and the pit beacon, relays 100 % with
  zero drops. Pit counts the copies (`relay_dupes`) and drops them; loss stays 0 %; ping via the
  pit applied in 0.3 s with the repeater on. Range and a real dead-zone test are still to do.
  Protocol change: header version byte bit 0x80 marks a relayed packet; every receiver now
  masks it (`gkValidate`). **Colin: rebuild kart 1 from this branch** or it will not understand
  the 47-byte telemetry/relay bit (old 46-byte packets are still accepted by the pit).
- **SLOW is now in telemetry** (`slow_mph`, 0 = off) so the pit shows a `SLOW n MPH` badge and the
  kart display no longer has to infer it from command replies. Not exercised against a live VESC
  today (Colin's VESC was off: `vesc_link false`).
- **Kart Pi 3 B display kit** under `kart/` (server on :8081, driver page with map from the pit,
  SD card kit, simulator, 29 tests). **First boot on the real Pi 3 B worked 2026-11-06 16:26**
  (card flashed from `kart/sdcard`, ~6 min to services up): cage + Chromium on the 1024x600 HDMI
  panel, `/dev/heltec` link up reading kart 2, shop map pulled from the pit into the cache.
  Driver-page rendering, touch and map speed on the Pi 3 B still need eyes-on checks.
  Open points from the build: Pi 3 B is 2.4 GHz only (does McLab offer 2.4 GHz?), Heltec power
  from the Pi's USB vs separate, pre-loading a map before first pit contact.
- Repeater OLED quirk: on the second T-Beam the QMC6310 compass answers at 0x3C and the screen is at
  0x3D; the repeater now reads register 0 of 0x3C (0x80 = compass) and picks the address itself.
- Boards: base re-flashed from the Pi (`esptool --no-stub`, app only at 0x10000), kart 2 by
  `espota.py -i 192.168.151.44 -p 3232` (arduino-cli's mDNS discovery of gokart-2.local fails;
  espota by IP works).

## 2026-11-06 evening: game layer ("Mario Kart"), GPS upgrades, repeater heartbeat

- **Game layer is in, off by default.** Contract: pit/server/map/ZONES.md. Pit: ZONES editor on the
  operator page (boost pad, mud, lap line), GAME ON/OFF on every page, BOOST per kart / BOOST ALL,
  laps + leaderboard from telemetry. Kart Pi: zones arrive with the map package, geofence engine
  runs on the kart's own 5 Hz GPS, sends `boost SECS SCALE` to the Heltec, racing HUD (speedo,
  gear, boost meter, laps, flash + sound). Firmware: GK_CMD_BOOST timed scale override, auto-revert,
  STOP/START cancel, never above 1.0. 36+48 unit tests, 3+3 e2e. **Not yet driven**: no zones drawn,
  boost never run against a live VESC. First test: draw one boost pad on a straight, kart in ECO,
  wheels-up, confirm the scale read-back rises and drops after 5 s.
- **GPS**: L76K now GPS+BDS+GLONASS, GGA+RMC only, 5 Hz at 115200 (configured after every
  (re)detect); `{"t":"gps"}` USB line per fix; self line adds hdop/fix_age_ms/gps_hz and the pit
  radio's fix (`base_lat/lon`). Kart Pi gates on hdop/sats, rejects jumps, holds heading below
  1.5 m/s, dead-reckons 1.2 s. Pit: measured position-domain correction (pit radio fix vs the
  surveyed `pit` marker) shown as a chip, applied only when tapped on and under 25 m with >= 6 sats.
  Indoor bench fixes are 200 m off and frozen; that is the building, not the map (OSM puts
  50 Day St within 30 m of the marker).
- **Repeater heartbeat** (GK_MSG_REPEATER_INFO every 5 s behind the beacon copy): pit shows a
  repeater chip with RSSI, battery and which karts it carries; LOST after 20 s. Repeater OLED needed
  address detection (compass at 0x3C on the second T-Beam, screen at 0x3D).
- Bench note: `base_rssi` 0 in telemetry means "not heard" but side by side the real RSSI is 0 to
  -1 dBm, so the pit may show 0 on the bench; outdoors it reads normally.

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

GET_MCCONF/GET_APPCONF capture current runtime settings. The saved flash
configuration has not been independently established. The current runtime scale
is 1.0, but that alone does not prove there are no temporary overrides elsewhere.

## Agreed behavior

- The owner approved the [initial telemetry fields](docs/requirements.md#agreed-telemetry-fields):
  kart identity/time, separate wheel/GPS speed, battery voltage/current, motor
  current, ESC temperature, faults, GPS position/quality and connection status.
  Motor temperature remains unavailable. Proposed units and validity conventions
  are documented there; schema, update rates and stale thresholds remain open.
- The Heltec owns the BLE VESC link and kart supervisory logic; the Pi is display
  only. USB VESC access remains available for inspection. This supersedes the
  earlier Pi-owned VESC architecture. See [architecture](docs/architecture.md).
- Preserve hardwired driver inputs and mechanical brakes.
- Keep three profile names. Desired eventual behavior is top-speed limits with
  strong acceleration, but do not replace current limits based on historical code.
- Losing the pit radio link should not stop the kart.
- **Reliability first:** loss of display, Heltec, BLE, GPS or pit communication
  must leave the ESC running its installed firmware and current active settings.
  This supersedes the earlier saved-configuration restore requirement. Do not
  change profiles, reset, stop or write settings because a link drops or returns.
  Reconnect read-only, reconcile actual state, and never replay cached commands.
  Hardwired inputs and normal ESC protections remain authoritative; bench
  verification is still required. See [acceptance checks](docs/requirements.md#reliability-and-link-loss-behavior).
- Drivers see their own and other kart positions, with online and race-entry filters.
- MPH. Beginner mode hides driver-facing fault alerts, while pit/logging retains them.
- Driver messages, gradual remote stop and geofence enforcement are deferred.
- A future geofence should operate locally without a pit link. The current
  Heltec/BLE architecture cannot guarantee enforcement after Heltec/BLE failure.

## Next work, in order

1. Identify the controller's commercial model, motor/battery/BMS ratings and BLE
   module. Check whether all development karts match. Display/GPS photos are now
   available in [the hardware evidence folder](hardware/photos/2026-11-05/README.md);
   connect display USB to 5V IN + TOUCH, not the neighboring power-only port.
2. Establish the saved-config baseline without overwriting settings or disrupting
   driving; coordinate a controlled inspection with the owner. Do not power-cycle
   or change firmware merely to fill a documentation gap.
3. Run a read-only Heltec BLE telemetry spike against this kart. Confirm intended
   device selection, packet fragmentation and reconnect. Measure latency while
   GPS, LoRa and USB display traffic run concurrently; verify display loss does
   not block VESC servicing. Validate independent Heltec power/reset behavior.
4. Build an explicitly labeled eight-kart simulator and responsive kart/pit views.
   Test filtering, stale/no-fix states, shared browser state and command outcomes.
5. Implement the Heltec serial contract and a two-radio bench link, then load-test
   eight-kart traffic before choosing field update rates.
6. Bench-verify brief/sustained accessory and link failures plus reconnect:
   no state-changing commands, unchanged active settings and uninterrupted
   hardwired input response with normal protections. Validate this before
   enabling profile writes. Treat actual ESC reboot/persistence separately.
7. Investigate drift cutoff with logs, then propose any tuning as a separate change.

The [requirements](docs/requirements.md) contain broader acceptance criteria.

## Drift cutoff: what is known vs unknown

The owner reports torque cutting out during sustained drifting. We have only
one stationary sample, with no active fault. That does not rule out transient
faults, current/voltage limiting or thermal issues during driving.

Log motor/battery current, pack voltage, ERPM, duty, ESC temperature, throttle
where available, fault state and requested/applied profile together. Motor
temperature is unavailable on these motors. Read existing BMS status if a BMS
is actually connected; its configuration entry alone does not prove one is.

Drift wheel speed and GPS ground speed differ. A wheel-RPM limiter can reduce
propulsion during a drift even at modest ground speed. Do not conclude that
higher battery current, motor current or fault thresholds are the fix without
an event log and verified component ratings.

## Collaboration and delivery state

The documentation, inspection tools, baseline capture and hardware photos are
versioned together in this repository. Collaborators should clone or update from
`git@github.com:colinfost3r/GoKart-Control.git` and start with README.md and this
handoff. Use `git status` to distinguish subsequent local work from committed
project state. The owner now requests ongoing pushes for collaboration with
Eamon: publish coherent, checked milestones and fetch/reconcile remote changes
before pushing. Use focused branches/PRs for parallel implementation. See
[CONTRIBUTING.md](CONTRIBUTING.md) for source ownership and conflict handling.

No team members have been invited and no messages have been sent. Use the
owner's chosen GitHub access process when they request it. Do not put credentials
or a shared pit password into source control.
