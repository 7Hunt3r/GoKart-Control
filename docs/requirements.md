# Requirements and implementation brief

Updated 2026-11-05. The connected kart is the primary hardware baseline; the
original GitHub app is historical reference. This file separates confirmed
requirements from proposed implementation and unresolved details. See the
[measured baseline](../hardware/vesc/baseline-kart/2026-11-05/README.md) and
[handoff](../HANDOFF.md) for inspection findings and current implementation status.

## Confirmed requirements

- Category: 50 Day. Canonical project: ~/Projects/50 Day/gokart-dashboard/.
- Fleet: eight karts; one to three development karts, including one each for
  the owner and Eamon. The exact number ready for testing is not yet known.
- Site: Manresa Island, Connecticut, USA.
- Kart computer: Raspberry Pi 3 Model B. It has onboard Wi-Fi and BLE; Ethernet
  is also available for development. Installed OS/access is not yet confirmed.
- Display: LESOWN L7 family, 7-inch IPS, 1024×600 per supplied guide; HDMI video
  and USB-HID capacitive touch on the touch variant. Guide revision is 2.1; exact
  product suffix and live display/touch operation still need confirmation.
- Radio/GPS: photo-identified Heltec HTIT-WB32LAF V4.3 with a cabled Quectel L76K
  module and ceramic GPS patch antenna. Packaging lists HF 863–928. Installed
  firmware, radio settings and live GPS operation remain unverified.
- VESC transport: Bluetooth preferred for normal operation. USB is accessible
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

## Proposed architecture

Each kart has a Pi dashboard, a VESC BLE connection, and a USB-connected Heltec.
The Pi handles UI, VESC protocol, local logs and fleet state. The Heltec handles
GPS and LoRa and exchanges framed messages with the Pi. Persist the intended
VESC identity per kart; never select the first device with a common BLE name.

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

## Link-loss behavior and saved-configuration fallback

Confirmed desired behavior:
- Loss of the pit/LoRa link alone: continue driving normally.
- Pi crash or loss of its VESC Bluetooth connection: continue on the VESC's
  saved configuration, NOT the dashboard's NORMAL profile.
- Future geofence: owner wants a settable boundary that disables propulsion
  when breached, including while the pit link is unavailable.

These are requirements, not implemented guarantees. Current upstream
COMM_SET_MCCONF_TEMP mutates runtime limits without a profile lease/expiry.
A BLE disconnect must not be assumed to restore saved configuration. The
ordinary motor-command timeout is not a saved-profile restoration mechanism.
A dead/disconnected Pi cannot send a restore command.

Before enabling temporary profiles, investigate controller-resident support
for an expiring profile/heartbeat lease on the exact installed firmware. If
supported, expiry should restore the verified saved baseline without replacing
hardwired driver inputs or reflashing/restoring the whole controller while
moving. The exact mechanism, timeout and transition behavior require bench
validation. Do not label this fallback operational until tested under power
loss, process failure and BLE disconnect. Do not flash firmware or deploy such
controller logic merely to complete documentation.

A future geofence must be evaluated locally on the kart to survive pit-link
loss. If implemented only on the Pi, it cannot enforce anything after Pi or
VESC-link failure. Guaranteeing enforcement in those conditions requires an
independent controller/GPS/control path and an explicit precedence decision
against saved-configuration fallback. Keep this limitation visible and leave
geofence enforcement deferred until designed. GPS-loss policy, boundary
hysteresis, disable ramp, re-enable behavior and the meaning of 'turn off' are
not yet specified. Mechanical brakes remain directly operated by the driver.

Upstream references (not proof of installed firmware behavior):
https://github.com/vedderb/bldc/blob/master/comm/commands.c
https://github.com/vedderb/bldc/blob/master/timeout.c

## Interface scope

Kart view:
- Large MPH readout with source identified; VESC RPM, pack voltage, input power,
  ESC temperature and course map retained.
- ECO/NORMAL/SPORT with requested/pending/confirmed/failed states.
- Own position plus fleet markers, online and race-entry filters, heading when
  valid, trails, and explicit last-seen age. Filters only change presentation;
  hiding a kart must not remove it from telemetry or command accounting.
- Separate VESC, GPS, radio and pit connectivity states.
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
   to verify. Validate Pi BLE telemetry and identity selection without writes.
3. One Pi, touchscreen and Heltec: check resolution, touch, GPS, boot/reconnect,
   power supply and concurrent radio/Bluetooth performance.
4. Diagnose drift cutoff using configuration and event logs. Design profile
   speed/torque behavior and controller-side saved-baseline fallback; verify
   them before enabling temporary profile writes or changing limits.
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
- Heltec V4.3/L76K serial firmware, GPS operation and RF settings. Assembly is
  photo-identified; packaging lists HF 863–928, but no configuration was read.
- Pit base radio model, LAN/network provisioning and Pi 4 setup/access.
- Course route, map extent and any updated owner-supplied aerial image.
- Controller-side saved-configuration fallback mechanism and timing.
- Driver vs pit permissions for changing profiles; shared pit control is confirmed.
- Pi/display regulated supply, enclosure/weather exposure and shutdown behavior.
- Installed Pi OS and development access, and target date for first field test.
- Deferred: geofence and gradual-stop semantics, failure precedence, and restart.

## Hardware references

- Pi 3 B: https://www.raspberrypi.com/products/raspberry-pi-3-model-b/
- Heltec: https://heltec.org/project/wifi-lora-32-v4/
- Exact V4.3 schematic: https://resource.heltec.cn/download/WiFi_LoRa_32_V4/Schematic/HTIT-WB32LAF_V4.3.pdf
- Heltec expansion kit reference (not an identification of the owner's assembly):
  https://heltec.org/project/wifi-lora-32-v4-expansion-housing/
