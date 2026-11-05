# Collaborator handoff

Updated November 5, 2026. Start with this file, then read the linked baseline
report before implementing VESC commands.

## What we are building

An eight-kart system at Manresa Island, CT. Each Pi 3 B runs a touchscreen
dashboard and talks to its own VESC by BLE. A USB-connected Heltec supplies GPS
and LoRa. The Pi 4 pit server owns fleet/race state and the base-station radio;
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

The application has not been built. BLE operation with a Pi, LoRa coverage,
GPS reception, touchscreen behavior and failure recovery remain untested.

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

- Keep BLE as the preferred VESC link; USB remains available for inspection.
- Preserve hardwired driver inputs and mechanical brakes.
- Keep three profile names. Desired eventual behavior is top-speed limits with
  strong acceleration, but do not replace current limits based on historical code.
- Losing the pit radio link should not stop the kart.
- If the Pi crashes or its VESC link fails, restore the **saved VESC configuration**,
  not the dashboard NORMAL profile. This is a requirement, not an implemented feature.
- Drivers see their own and other kart positions, with online and race-entry filters.
- MPH. Beginner mode hides driver-facing fault alerts, while pit/logging retains them.
- Driver messages, gradual remote stop and geofence enforcement are deferred.
- A future geofence should operate locally without a pit link. The current
  Pi/BLE architecture cannot guarantee enforcement after Pi/BLE failure.

## Next work, in order

1. Identify the controller's commercial model, motor/battery/BMS ratings and BLE
   module. Check whether all development karts match. Display/GPS photos are now
   available in [the hardware evidence folder](hardware/photos/2026-11-05/README.md);
   connect display USB to 5V IN + TOUCH, not the neighboring power-only port.
2. Establish the saved-config baseline without overwriting settings or disrupting
   driving; coordinate a controlled inspection with the owner. Do not power-cycle
   or change firmware merely to fill a documentation gap.
3. Run a read-only Pi BLE telemetry spike against this kart. Confirm intended
   device selection, packet fragmentation, reconnect, latency and UI responsiveness.
4. Build an explicitly labeled eight-kart simulator and responsive kart/pit views.
   Test filtering, stale/no-fix states, shared browser state and command outcomes.
5. Implement the Heltec serial contract and a two-radio bench link, then load-test
   eight-kart traffic before choosing field update rates.
6. Design and bench-verify controller-side saved-config fallback before enabling
   temporary profile writes. A disconnected Pi cannot send a restore command.
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
`git@github.com:colinfost3r/gokart-dashboard.git` and start with README.md and this
handoff. Use `git status` to distinguish subsequent local work from committed
project state. Future commits and pushes still require the owner's request.

No team members have been invited and no messages have been sent. Use the
owner's chosen GitHub access process when they request it. Do not put credentials
or a shared pit password into source control.
