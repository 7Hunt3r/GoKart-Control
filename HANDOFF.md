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

During the initial USB inspection and normal Heltec telemetry/OTA tests, no VESC
settings, firmware, resets, motor commands, CAN enumeration or terminal commands
were sent. Later isolated diagnostic trials sent zero-current requests and finite
ADC handoff/release commands. Direct laptop Bluetooth subsequently produced a
physically confirmed half-second forward movement at a 5 A request; longer
5 s / 5 A and 30 s / 10 A attempts aborted at diagnostic speed limits and returned
to zero. No VESC configuration or firmware was written. See the diagnostic
findings below. Telemetry
queries can reset the controller's averaging accumulators as part of ordinary
GET_VALUES behavior, but do not change drive settings.

No dashboard/pit app is implemented on this branch. Eamon has added a pit server,
kiosk and simulator on PR #1; see the integration note below. Heltec BLE telemetry,
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
| ADC and UART; Current Reverse Button | Consistent with hardwired throttle/reverse and a UART BLE module. BLE UUID/device identity verified during Heltec bench testing. |
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
5. Confirm actual reception on Eamon’s T-Beam S3 base using the shared 46-byte
   GoKartProtocol/BW500 contract. This development kart is assigned ID 1. TX is
   opt-in; PPS slotting and eight-kart load testing remain open. Obtain an
   outdoor GPS fix. See the bench report for installed-image verification.
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

Eamon’s `eamon/firmware-lora-starter` is at `d91422b` (PR #1) when reviewed. His `4164bc1` handoff proposes
radio reception first, then temporary STOP/START/mode configuration writes. See
[configuration integration review](docs/eamon-config-review.md). Recent work also
adds the pit setup and maps, with a 50 Day St bench preset.
It contains the T-Beam S3 base, another kart starter, a Pi 4 pit server/kiosk,
simulator and SD-card setup. His latest notes report kart 2 → T-Beam reception, ping responses and OTA
verified without a VESC attached; no receipt of our kart 1 or outdoor fix yet.
Those are collaborator-reported results, not tests performed on this computer.

The owner chose his receiver contract to drive integration. Our adapter now
includes his **unchanged canonical GoKartProtocol library** and uses its 46-byte
frame and 1 Hz reporting. Eamon’s latest base moved to BW500 and supports the
legacy 47-byte packet too. Our 0.2.1-bench image now follows BW500 while retaining
the shared 46-byte packet; 0.2.0-bench was BW250 and must be updated. This
kart is **ID 1** by owner decision; do not assign that ID to another powered kart.
Shared bytes/settings match; successful over-air reception remains unverified.
The detailed [receiver agreement](firmware/heltec/README.md#radio-receiver-agreement-for-eamon)
records remaining validity, session and slot-timing limitations.

Keep the tested BLE adapter in `firmware/heltec`. Eamon’s latest kart starter
now defaults to BLE with a required pinned MAC and enabled writes. Its mode/STOP/START logic
has not been validated on this kart and is not imported by adopting the packet
format. Our adapter transmits telemetry only, with unknown modes/scale and
writes disabled. Retain authenticated maintenance-only OTA. No changes have
been made to Eamon’s branch and no remote commands are handled here.

The owner requested a removable bench-control diagnostic and has now paused
motor testing to save progress and return to basic telemetry. The separate
`firmware/bench-control` folder contains both the stalled Heltec prototype and
`direct_ble.py`, a laptop runner using VESC Tool-style current commands. A 5 A,
half-second probe moved forward as physically confirmed by the owner. The
requested five-second test aborted at 580 ERPM; the later 30-second / 10 A test
aborted at 2,474 ERPM. Both returned to zero without a VESC fault. Neither
sustained test completed. See [test report](docs/bench-control-test.md) and
[immutable traces](hardware/vesc/baseline-kart/2026-11-05-direct-ble-bench/README.md).

The physical Heltec is back on **0.2.1-bench**, kart 1, BW500, 46-byte telemetry,
TX enabled, maintenance off and VESC writes disabled. Fresh telemetry showed
0 ERPM, 0 A and fault 0. Do not flash either unvalidated motor-control image
as a consequence of pulling repository updates. The immediate next integration
step is Eamon's reception check; local TX completion does not prove receipt.

Eamon's STOP/START proposal changes temporary motor-current limits; START does
not command the wheels to spin. It is separate from the direct-current bench
runner and from the owner's future top-speed profiles. The review records the
fixed STOP ramp, hardcoded unrelated limits, UUID and OTA differences that need
reconciliation before adopting his command-capable image. No changes have been
made to his branch or to the installed VESC configuration.

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
