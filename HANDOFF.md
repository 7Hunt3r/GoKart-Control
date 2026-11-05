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
- Heltec bench firmware and authenticated Wi-Fi OTA are implemented and exercised
  on one board; live VESC UUID/6.00 telemetry, GPS NMEA and radio TX completion
  verified. See [bench results](docs/heltec-bench-test.md) and
  [receiver/OTA instructions](firmware/heltec/README.md).

During VESC inspection and Heltec testing, no VESC settings were written. No VESC
firmware update, VESC reboot, motor test,
CAN enumeration, terminal command or motor actuation was performed. Telemetry
queries can reset the controller's averaging accumulators as part of ordinary
GET_VALUES behavior, but do not change drive settings.

The dashboard and pit application have not been built. Heltec BLE telemetry,
GPS serial reception and OTA reboot/reconnection now work on the bench. Outdoor
GPS fix, receipt on Eamon's radio, LoRa coverage, touchscreen behavior and
physical driving response under link/accessory failures remain untested.

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
3. Extend the working read-only Heltec bench test: quantify latency under GPS,
   LoRa and USB load; verify physical driving response through accessory/link
   failures on a controlled bench. BLE identity/readout and OTA reconnect have
   passed, but independent power and broader failure testing remain open.
4. Build an explicitly labeled eight-kart simulator and responsive kart/pit views.
   Test filtering, stale/no-fix states, shared browser state and command outcomes.
5. Eamon is preparing the second board. Match the published raw LoRa bench
   settings and 47-byte frame, confirm reception/decoding, then load-test fleet
   traffic. Current bench TX is opt-in and uncommissioned kart ID 0; these are
   not final eight-kart radio settings. Obtain an outdoor GPS fix.
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

## Parallel firmware integration note

Eamon published `eamon/firmware-lora-starter` at `21f3741` (PR #1) while the
physical bench adapter was being tested. It contains a T-Beam S3 base and a
separate kart starter. The bench code here remains on `firmware/heltec-bench-ota`.

The radio profiles are currently **incompatible**: this bench uses BW500 and a
47-byte `GK` frame; Eamon's starter uses BW250 and a 46-byte GoKartProtocol frame.
Do not expect reception/decoding until both ends deliberately use one format.
Neither format is declared the final fleet protocol. Reconcile the shared
contract in an integration PR before expanding either implementation.

The starter's current defaults also enable VESC writes and select wired UART,
whereas this tested adapter uses BLE and only identity/telemetry requests. Its
STOP/START command definitions are not authorization to enable deferred remote
stop on this kart. Do not replace the tested image with the starter by accident.
No changes have been made to Eamon's branch.

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
