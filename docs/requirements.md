# Requirements and implementation brief

Updated 2026-11-05. The connected kart is the primary hardware baseline; the
original GitHub app is historical reference. This file separates confirmed
requirements from proposed implementation and unresolved details. See the
[measured baseline](../hardware/vesc/baseline-kart/2026-11-05/README.md) and
[handoff](../HANDOFF.md) for inspection findings and current implementation status.

## Confirmed requirements

- Category: 50 Day. Canonical project: ~/Projects/50 Day/GoKart Control/.
- Fleet: eight karts; one to three development karts, including one each for
  the owner and Eamon. The exact number ready for testing is not yet known.
- Site: Manresa Island, Connecticut, USA.
- Kart computer: Raspberry Pi 3 Model B. It has onboard Wi-Fi and BLE; Ethernet
  is also available for development. Installed OS/access is not yet confirmed.
- Display: LESOWN L7 family, 7-inch IPS, 1024×600 per supplied guide; HDMI video
  and USB-HID capacitive touch on the touch variant. Guide revision is 2.1; exact
  product suffix and live display/touch operation still need confirmation.
- Radio/GPS: photo-identified Heltec HTIT-WB32LAF V4.3 with a cabled Quectel L76K
  module and ceramic GPS patch antenna. Packaging lists HF 863–928. One board
  now runs bench firmware with valid GPS NMEA, BLE readout and radio TX completion.
  GPS fix, receipt of Colin’s kart 1 and field range remain pending; Eamon reports
  receipt of his kart 2. See [bench results](heltec-bench-test.md).
- Kart roles: Heltec owns VESC communication, GPS, LoRa and supervisory profile
  logic; the Raspberry Pi 3 is display/interface only. The VESC retains motor
  control and hardwired driver inputs. The installed telemetry image has no
  profile writes; an integrated command build exists for guarded bench testing,
  not yet hardware-validated. See [integration status](integration.md).
- VESC transport: Bluetooth from the Heltec preferred for normal operation. USB is accessible
  for inspection and development; a wired replacement is not the chosen default.
- Throttle, brake-related inputs, and reverse are wired directly to the ESC,
  independent of the dashboard. Pedal braking is mechanical. Motor regenerative
  braking is disabled according to the owner; the runtime motor-braking limit
  reads 0 A. The battery regen allowance remains -100 A; preserve current behavior.
- Battery: owner reports 72 V nominal, 84 V fully charged, supplying the VESC.
  Present variation in achievable top speed with charge is acceptable.
- Keep ECO/NORMAL/SPORT for now. Desired profile behavior primarily limits top
  speed while retaining strong acceleration. Actual limits remain unverified;
  no current or speed settings have been changed or authorized as final values.
- LoRa supports pit telemetry, shared positions, and remote profile requests.
- Driver messages and remote stop are deferred. A future remote stop should
  gradually reduce propulsion; actuator and ramp details remain to be defined.
- Pit: Raspberry Pi 4 server with a base-station LoRa radio, serving a
  password-protected web interface to multiple computers with shared control.
- Kart map: show own position and up to all eight karts; allow online-status
  and current-race-entry filters, as explicitly confirmed by the owner.
- Dashboard units: MPH. Retain speed, VESC RPM, voltage, power, temperatures,
  course map and status. Beginner display mode hides driver-facing fault alerts;
  full faults remain available at the pit and in logs. Motors have no temperature
  sensors wired; show motor temperature as unavailable. It does not alter ESC
  protections or silently select a different power profile.
- Owner can supply high-resolution imagery; public imagery is also acceptable.

## Agreed telemetry fields

The owner approved the following initial telemetry scope. Field names and
representation below are proposed shared implementation conventions; they are
the full product model rather than a promise that every field is already on
the wire. The current shared LoRa/base serial formats are documented in
[integration](integration.md).

| Measurement/status | Proposed field(s) | Units and meaning |
| --- | --- | --- |
| Kart identity | `kart_id` | Stable fleet ID 1–8; separate from the VESC CAN ID |
| Measurement time | `sampled_at`, `received_at` | UTC timestamps when known; preserve acquisition time separately from receiver arrival time |
| Speed | `wheel_speed_mph`, `gps_speed_mph` | Separate wheel-derived and GPS ground speeds; display identifies the selected source |
| Battery voltage | `battery_voltage_v` | Volts at the VESC input |
| Battery current | `battery_current_a` | Amps; positive draw, negative return to battery after adapter sign verification |
| Motor current | `motor_current_a` | VESC-reported motor amps, with sign retained; distinct from battery current |
| ESC temperature | `esc_temperature_c` | Degrees Celsius from controller sensing |
| Motor temperature | `motor_temperature_c` | Unavailable on current karts because sensors are not wired |
| Controller fault | `fault_code`, `fault_name` | Preserve numeric code and firmware-specific interpretation; unknown is not no-fault |
| GPS position | `latitude_deg`, `longitude_deg` | WGS84 decimal degrees, with fix validity and measurement age |
| GPS fix quality | `gps_fix_state`, `satellites_used`, `hdop`, `horizontal_accuracy_m` | Include only measures actually supplied; HDOP is dimensionless, not accuracy in meters |
| Connection status | `vesc_link`, `radio_link`, `pit_link`, `display_link` | Report each connection separately, including last valid data age; GPS validity is separate |

Existing requirements for electrical RPM, duty cycle and input power remain in
scope, especially for drift-cutoff logging. Keep raw measurements separate from
derived values. Wheel speed needs verified poles, gearing and wheel diameter;
until verified, any calculated speed must be labeled uncalibrated. Do not assume
that one speed source can silently replace the other during a drift.

Proposed validity and freshness conventions:

- Every measurement/group carries validity, source and age. Use explicit
  `valid`, `unavailable` or `invalid` states with a reason when needed. Freshness
  is separate: a previously valid sample can become stale.
- Missing/invalid readings use `null`, never a fabricated zero. A valid zero
  remains meaningful for stopped speed, zero current and fault code 0.
- Current motor temperature is `null`, `unavailable`, reason `sensor_not_wired`.
  Do not substitute ESC temperature or treat the raw motor value 0 C as valid.
- VESC and GPS samples can arrive at different times. Updating one source must
  not refresh the other's timestamp or make cached data appear newly measured.
- Use local monotonic elapsed time for freshness. If UTC is unsynchronized,
  mark the timestamp unknown; do not compare monotonic clocks across devices.
  Radio forwarding must preserve sample age and account for transit uncertainty.
- A connected transport does not prove fresh telemetry. Track last valid sample
  separately from connection state; an unheard radio peer is unknown/stale,
  not proof that its kart is powered off.
- Stale positions remain visibly stale or hidden according to the map filter;
  never move an unavailable GPS fix to latitude/longitude zero.
- A stale no-fault sample is not evidence of a currently fault-free controller.
  Beginner mode affects driver presentation only, not recorded telemetry.

Stale thresholds, update rates, radio encoding and the machine-readable schema
remain to be settled during interface implementation and eight-kart airtime
budgeting. Local logging may be faster and more detailed than LoRa summaries.
This agreement does not enable any controller writes or change driving behavior.

## Proposed architecture

Each Heltec owns the VESC BLE connection, telemetry acquisition, GPS, LoRa and
supervisory request validation/serialization. The Pi handles display, map assets
and touchscreen requests; USB serial between Pi and Heltec is proposed. It must
not own a competing VESC session. Persist the intended VESC identity on the
Heltec per kart; never select the first device with a common BLE name. The pit
stores fleet/session logs; persistent local logging/storage remains to be chosen
and must not make Heltec operation depend on the display.

The Pi 4 hosts the pit application and its authoritative fleet/race state, with
an attached base-station radio. Multiple password-authenticated browser clients
may view and operate it simultaneously. Serialize commands per kart and use
state revisions to reject conflicting stale edits. Track which session issued
an action and broadcast outcomes to all clients. Shared control does not mean
multiple competing direct connections to the LoRa serial device.

The initial topology assumes eight kart radios plus a pit radio. Confirm the
actual inventory. Browser access is over the site's LAN/Wi-Fi/Ethernet; LoRa
carries kart data and commands, not web pages. Internet exposure is not required
or implied by the request for a web interface. The pit remains usable locally.

Keep an adapter boundary for optional wired VESC diagnostics. Choose the UI
framework after display identification and Pi performance checks. Keep course
assets local; runtime operation should not depend on cloud connectivity.

## Reliability and link-loss behavior

The owner's latest decision supersedes the earlier saved-configuration restore
requirement: the kart is a reliable vehicle first. The ESC continues running its
installed firmware and current active settings under hardwired driver control.
A communications interruption must not itself change propulsion behavior.
Firmware is the controller software; active settings/profile are separate. No
firmware reload, saved-settings restoration or switch to NORMAL is requested.

Required behavior for loss of display/Pi, Heltec, BLE, GPS or pit/LoRa:

- Do not issue throttle, brake, stop, reset, profile or configuration commands
  merely because a connection, heartbeat or GPS fix disappeared.
- Leave the last successfully applied VESC settings in effect. Do not install a
  communications lease that expires into another driving profile. Heartbeats
  may indicate connectivity only; they must not be needed to sustain propulsion.
- Hardwired inputs and the VESC's own protections retain their normal behavior.
  This policy does not bypass controller faults, electrical protection or
  mechanical brakes, or claim that the kart can continue through ESC power loss.
- Mark missing telemetry/positions stale or unavailable and reconnect quietly.
  The Pi/Heltec startup and reconnect paths begin with read-only reconciliation:
  read actual state before reporting a profile as applied. Do not automatically
  resend the UI's cached/default profile or replay pending commands.
- If a profile command loses its acknowledgement, mark its outcome unknown.
  Query actual settings when communication returns; do not assume either success
  or failure and do not retry a write solely because the connection returned.

Future profile changes require a deliberate new user request, verified limits
and a controlled transition. Command expiry limits when an unexecuted request
may be accepted; it must not expire a successfully applied driving profile.
Profile writes remain disabled until disconnect/reconnect behavior is validated.
An actual ESC reboot may load persisted settings; persistence/startup policy is
still to be designed and must be distinguished from ordinary communication loss.
No ESC timeout, protection, firmware or configuration is being changed now.

Acceptance checks, on a controlled bench setup before driving tests:

- Inspect the actual firmware/application timeout and input behavior; the
  stationary capture alone does not prove uninterrupted drive operation.
- Interrupt the display process, USB link and display power; separately interrupt
  BLE, Heltec operation/power and the pit link. Include brief and sustained loss.
- Verify the supervisory software emits no state-changing commands on loss,
  startup or reconnect, and no default/cached profile is reapplied.
- Verify hardwired input response and active VESC settings remain unchanged by
  those interruptions, with normal controller protections intact. Record evidence.
- Test simultaneous BLE, GPS, LoRa and display load, stale data and lost command
  acknowledgements. Keep controller-power interruption as a separate test case.

Remote stop and geofence enforcement remain deferred. Neither is an implicit
reaction to connectivity or GPS loss. Any future intervention needs explicit
behavior, validation and a separate decision consistent with this reliability
priority. A Heltec geofence could operate independently of the display/pit, but
cannot guarantee enforcement after Heltec or VESC-link failure. Boundary policy,
GPS-loss handling, transitions and restart behavior remain unresolved.

## Interface scope

Kart view:
- Large MPH readout with source identified; VESC RPM, pack voltage, input power,
  ESC temperature and course map retained.
- ECO/NORMAL/SPORT with requested/pending/confirmed/failed states.
- Own position plus fleet markers, online and race-entry filters, heading when
  valid, trails, and explicit last-seen age. Filters only change presentation;
  hiding a kart must not remove it from telemetry or command accounting.
- Separate VESC, GPS, radio, pit and display-to-Heltec connectivity states.
- Explicit no-fix/stale readings rather than zero values on telemetry loss.
- Beginner mode hides driver-facing fault alerts while retaining pit diagnostics
  and logging. Device faults/protection behavior remain active.

Pit view:
- Password-protected shared control from multiple computers through the Pi 4.
- Eight stable kart IDs/colors, per-kart telemetry, faults and link/GPS status.
- Online and race-entry management; no timing/scoring system is assumed yet.
- Addressed and fleet-wide profile requests with individual outcomes.
- Session logs and export.
- Driver messaging, remote gradual-stop and geofence enforcement are deferred.

## Drift cutoff investigation and profile behavior

Owner reports a torque ceiling/cutoff during sustained drifting and would like
stronger sustained torque if the hardware permits it. This is an investigation
request, not evidence that higher configured amperage will solve the symptom.

First obtain controller model/firmware, motor and battery/BMS ratings, actual
configuration, and a log around an event: motor current, battery current,
voltage sag, ERPM, duty cycle, ESC temperature, throttle input where available,
and fault codes. Motor temperature cannot be logged as a valid reading because
no sensors are wired. Distinguish current limiting, speed/duty limiting,
thermal reduction, supply/BMS interruption, and motor-control faults before
proposing changes. Do not increase absolute fault thresholds to mask an event.

Keep the existing three profiles while inspecting the real setup. The desired
future design separates speed caps from available acceleration torque, subject
to verified hardware limits. During a drift, motor/wheel RPM can be high even
when GPS ground speed is low; an ERPM-based cap can reduce drive in that state.
The speed-source choice and limiter behavior must be explicit and tested.
Do not substitute GPS as the sole fast control feedback simply to avoid wheel
speed limiting. No new current values, regen behavior or speed caps are set.

References:
https://vesc-project.com/node/180
https://github.com/vedderb/bldc/blob/master/motor/mc_interface.c

## Course map

Prepare a local map package before going to the course. It contains:
- A cropped/tiled base image, geographic transform/CRS, source and attribution.
- A separately editable course line or boundary, start/finish, and pit location.
- Metadata identifying map version and bounds.

Candidate public source: CT ECO / CT State GIS Office 2023 aerial imagery.
The published dataset offers statewide 3-inch imagery and GeoTIFF downloads:
https://maps.cteco.uconn.edu/data/flight2023/

Verify the imagery date against current site conditions. Aerial imagery does
not establish the intended course route; obtain or draw that route separately.
No map tile, boundary, or course route has been selected yet.

A supplied GeoTIFF or orthomosaic with geographic coordinates is preferred.
A plain image needs georeferencing against known locations and field validation.
Reproject source coordinates to the map's chosen coordinate system; GPS
latitude/longitude cannot be treated directly as image pixels. Keep the original
high-resolution source, but preprocess a small site package for the Pi.

Show GPS fix validity, data age, and accuracy information when provided. Freeze
and visibly mark stale positions rather than presenting extrapolated motion as
measured position. High-resolution imagery does not improve GPS accuracy.

## Radio and command design

Use the US region as the planning target. Confirm the actual board's RF variant,
antennas, and operating parameters before selecting channel and transmit power.
Do not assume that a supported firmware maximum is the correct operating power.

Proposed first topology: scheduled direct kart broadcasts received by the pit
and other karts in range. Do not assume all karts can hear one another. Validate
coverage around the actual course; add pit relaying or a repeater only if needed.
This is a proposed custom protocol, not a decision to flash Meshtastic/MeshCore.

Budget radio airtime across all eight karts and the pit. Separate fast local
VESC/UI updates from lower-rate LoRa summaries. Select payload size, radio
settings, and update interval together after an airtime calculation. Leave
capacity for commands, acknowledgements, and retries. Do not transmit imagery
or the full local telemetry stream over LoRa.

Messages should include protocol version, fleet/network identity, source,
destination, boot/session identity, sequence, message type, and payload.
Position data needs fix validity and age; telemetry needs units and validity.
Use bounded framing and integrity checks on the Pi-to-Heltec link. Authenticate
radio commands and reject replayed, expired, or wrongly addressed commands.

A remote command has a unique ID, intended kart(s), expiry, action, and parameters.
Track queued, transmitted, received, accepted/rejected, applied/failed, and
expired states where the device can substantiate them. Retry with the same ID
and deduplicate at the receiver. Do not execute a backlog after a reconnect.
A fleet command tracks eight individual outcomes rather than one success flag.
Store command history with telemetry for later inspection.

## Build sequence and acceptance

1. Build an eight-kart simulator and dashboard foundation. Verify IDs, map transforms,
   online/race filters, beginner display, stale states and partial profile-command
   delivery. Exercise shared browser control against a single pit authority.
2. Initial USB runtime read of one development kart is complete (firmware 6.00,
   hardware ID 75_100_V2). Commercial model, saved baseline and other karts remain
   to verify. Heltec BLE UUID verification and read-only telemetry now work on
   this baseline; extend validation to the other development karts.
3. One Pi, touchscreen and Heltec: check resolution, touch, GPS, boot/reconnect,
   independent Heltec power and concurrent radio/Bluetooth/USB performance.
   Verify a frozen, disconnected or powered-off display does not block the
   Heltec, and measure actual sample/command latency under load.
4. Diagnose drift cutoff using configuration and event logs. Design profile
   speed/torque behavior and verify that link loss/reconnect leaves active
   settings and hardwired input response unchanged before enabling profile writes.
5. Two-radio link, then eight-kart plus pit load simulation and field tests.
   Measure airtime, packet age/loss, latency, retries, and pit-link loss behavior.
6. Course package and local Pi 4 server deployment. Validate GPS/map alignment,
   coverage, shared authenticated control, daylight UI, power-loss recovery.
7. Later: driver messages, gradual remote stop and geofence enforcement, each
   with explicit behavior and independent verification before activation.

## Details still to identify

- VESC commercial model, number of controllers per kart, BLE identity and whether
  all eight karts match. One USB inspection succeeded: firmware 6.00 and hardware
  ID 75_100_V2. Captured runtime settings are not yet a verified saved-flash baseline.
- Motor, battery/BMS ratings, wheel diameter/gearing/poles, actual profiles and
  saved profile baseline, and logging evidence for the drift cutoff. Runtime
  settings are captured; motor-temperature sensors are absent, as owner confirmed.
- LESOWN exact product suffix and mounted orientation; verify manual-derived
  1024×600 mode and USB-HID touch on the actual Pi. Photos/manual are saved.
- Heltec bench serial/BLE/GPS/LoRa and Wi-Fi OTA implementation is available.
  Still verify outdoor GPS fix, the second radio, fleet airtime, field RF settings
  and power isolation. See the bench report for the exact tested scope.
- Pit base radio model, LAN/network provisioning and Pi 4 setup/access.
- Course route, map extent and any updated owner-supplied aerial image.
- Bench verification of uninterrupted hardwired control during accessory/link
  failures, actual controller timeout behavior, and future profile persistence
  across ESC reboot (distinct from communication loss).
- Driver vs pit permissions for changing profiles; shared pit control is confirmed.
- Pi/display and independently powered Heltec regulated supplies, USB backfeed/
  reset behavior, enclosure/weather exposure and shutdown behavior.
- Installed Pi OS and development access, and target date for first field test.
- Deferred: geofence and gradual-stop semantics, failure precedence, and restart.

## Hardware references

- Pi 3 B: https://www.raspberrypi.com/products/raspberry-pi-3-model-b/
- Heltec: https://heltec.org/project/wifi-lora-32-v4/
- Exact V4.3 schematic: https://resource.heltec.cn/download/WiFi_LoRa_32_V4/Schematic/HTIT-WB32LAF_V4.3.pdf
- Heltec expansion kit reference (not an identification of the owner's assembly):
  https://heltec.org/project/wifi-lora-32-v4-expansion-housing/
