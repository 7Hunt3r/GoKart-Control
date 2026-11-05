# Collaborator handoff

Updated November 5, 2026. This integration combines Eamon's `d98c41d` with
Colin's telemetry/diagnostic `544b498`, plus guarded configuration transactions.
Use [docs/integration.md](docs/integration.md) for exact build/commissioning steps.

## What is physically running

**Colin, kart 1:** Heltec `firmware/heltec` 0.2.3-bench, telemetry only. BLE identity
verified; fresh values showed 0 A, 0 ERPM, fault 0 after all motor tests. Radio TX
is enabled, maintenance off, 46-byte GoKartProtocol at 915 MHz/BW500/SF7/CR4/5.
Measured on the earlier 0.2.2 rate update: 4.872 Hz VESC, 4.993 Hz USB and 1.973 Hz
radio TX over 24.834 seconds, zero drops/errors. USB reader disconnect/reconnect
passed without reboot. [Evidence](hardware/heltec/2026-11-05-rate-update/README.md).
The current 0.2.3 image powers the small OLED and shows live VESC/GPS/radio
status at 2 Hz. Its startup and telemetry checks are recorded in the
[OLED follow-up](hardware/heltec/2026-11-05-oled-enable/README.md).
The owner has now [confirmed visible live text](hardware/heltec/2026-11-05-oled-enable/OWNER_CONFIRMATION.md)
on the small OLED.
GPS NMEA is received but no outdoor fix is verified. Actual receipt of kart 1 at
Eamon's base remains unconfirmed. No VESC configuration/firmware was written.

**Eamon, kart 2 / pit:** his latest handoff reports the Pi 4 kiosk and T-Beam
running at 50 Day St, 1 Hz reception from his Heltec, ping applied in about one
second, and Wi-Fi OTA working. STOP returned no_vesc / START rejected with no
VESC attached. These are collaborator-reported tests, not a real motor-stop
validation. Do not assign ID 1 to his board or ID 2 to Colin's board.

**New integrated kart build:** compiles for BLE and UART; the configuration
safeguards have native tests using the real capture. It has **not** replaced
Colin's installed image. Source updates and Git merges do not deploy hardware.

## What changed for integration

- One canonical `GoKartProtocol` header and the existing base serial contract
  serve both kart implementations and the pit. Telemetry payload remains 46
  bytes; the target is now 5 Hz local VESC/USB and 2 Hz LoRa per kart.
- Eamon's 22-byte pit beacons, rolling packet-loss display, 20 fps moving-map
  interpolation and public shop imagery are included. GPS acquisition rate is
  unchanged; faster rendering/radio does not produce new GPS fixes.
- Both 1 Hz and 2 Hz integrated karts use identical 50 ms ID slots, avoiding
  mixed-rate overlap. The base sends commands at 415–485 ms and its beacon after 905 ms. Update
  base and karts together; issue `rate 2` on nodes with a stored 1 Hz setting.
  Tests cover all 256 mixtures of eight kart rates and full packet airtime.
- BLE is the integrated kart default. It requires a pinned BLE MAC to connect
  and a pinned VESC UUID plus firmware 6.00 before any configuration write.
- Every temporary-limit transaction first reads fresh settings, changes only
  current-max scale, and checks all transmitted limits after acknowledgment.
  Battery-current fields are omitted and checked for preservation.
- STOP's 0.5 / 0.2 / 0 steps are clamped to the newly read scale. A kart already
  below a step cannot have its allowance raised by STOP. On failure the ramp ends with an
  unknown result; it does not retry itself or resume after reconnection.
- Recent command outcomes are retained for duplicate replies, including rejected
  overlapping requests. No command backlog executes when a link returns.
- The integrated node now requires password-authenticated, explicitly opened
  stationary maintenance for OTA. Maintenance pauses BLE/LoRa without touching
  VESC settings, expires after ten minutes, and is closed after reboot.
- Eamon's pit app, maps, simulator and deploy tools are in this same branch;
  the tested telemetry recovery image and removable diagnostics remain separate.

## Immediate next hardware work

1. With the currently installed telemetry image, confirm **kart 1 reception**
   on Eamon's pit and capture its packet sequence/RSSI. It is already TX-enabled;
   after a reboot it needs `tx on antenna-confirmed` again.
2. Review the integrated build and commission the correct ID, BLE MAC, UUID and
   OTA password before replacing the installed image. Keep the telemetry image
   and its credentials available for rollback. Only one BLE client may own the
   VESC at a time; close laptop/VESC Tool sessions first.
3. Run read-only identity/settings checks, then an explicitly supervised
   wheels-up STOP/START test through the pit. Validate actual readback, wired
   throttle behavior, unknown outcomes and loss/reconnect behavior. START is
   not a wheel-spin command. These steps are not already passed.
4. Validate GPS fixes, beacon/PPS sync and eight-kart loss/coverage at 2 Hz;
   retain `rate 1` as a test fallback on integrated nodes. Then integrate the
   kart Pi touchscreen/map view. A 10 Hz local target awaits measurement.

Do not automatically flash as part of a Git pull. The owner requested basic
telemetry after the motor experiments; that remains the current hardware state.

## Motor-test progress preserved

The owner requested removable control and ultimately 30 seconds at 10 A.
A direct Mac Bluetooth runner, using VESC Tool-style current commands, produced
an owner-confirmed half-second forward movement with a 5 A request. The
five-second / 5 A attempt stopped at its 500 ERPM guard; the thirty-second / 10 A
attempt stopped at its 2,000 ERPM guard. Both returned to zero with no reported
VESC fault. Neither sustained-duration test completed.

The separate Heltec diagnostic still has preflight/transport stalls; direct
laptop success does not fix that path. The first 1 A probe had a result-reporting
bug, corrected before later tests. [Full report](docs/bench-control-test.md) and
[immutable traces](hardware/vesc/baseline-kart/2026-11-05-direct-ble-bench/README.md)
record those limits honestly. No positive current is commanded by the integrated
STOP/START path, which modifies temporary allowances instead.

## Baseline and decisions that must survive integration

- Connected kart evidence outranks the historical M5Stack README. Runtime reads
  are not proof of saved flash. Keep all captures immutable.
- VESC 6.00, `75_100_V2`, UUID `2c004c000951343037353739`, CAN ID 69;
  BLE module `d8:94:35:be:e5:32`. Firmware label `no_hw_limits` is not a rating.
- Motor max 140 A, battery max 100 A, absolute-current fault threshold 240 A.
  No motor-temperature sensors wired. ESC temperature is available separately.
- ADC + UART, hardwired throttle/reverse. Pedal brake mechanical; motor braking
  limit 0 A, battery regen allowance -100 A. Preserve the actual behavior.
- Configured 14 poles / ratio ~2.76923 / 0.406 m wheels require physical validation
  before relying on MPH. GPS and driven-wheel speed differ during a drift.
- Eight karts, Pi 3 B display only, Heltec owns VESC/GPS/LoRa. Pi 4 serves the
  password-protected shared pit. LESOWN L7 family uses HDMI plus USB touch.
- Reliability first: accessory/link loss leaves current active VESC settings
  untouched. No default/profile restore, heartbeat-based driving lease, reset,
  or cached command replay. Reconnect read-only; retain normal ESC protections.
- ECO/NORMAL/SPORT labels stay. Eamon's prototype uses historical current scales;
  the owner ultimately wants speed caps with strong acceleration. Those are
  not yet calibrated or approved final limits. Drift cutoff needs event logs,
  not speculative current increases.
- Driver messaging, geofence enforcement and deployed remote-stop behavior remain
  deferred. Bench implementation/testing is separate from field readiness.
- Driver map shows own/all karts with online/race-entry filters. MPH. Beginner
  driver display hides fault alerts; pit/logging retains them.

## Collaboration and evidence

Use focused branches/PRs, fetch before work and before pushing, and never force
push over Eamon's work. Changes are integrated here, not written onto his branch.
The owner authorized ongoing coherent commits/pushes. No messages were sent to
collaborators. Credentials, locally built device binaries and licensed local imagery stay out
of Git. Eamon's existing prebuilt image is preserved in `firmware/releases`,
clearly marked as predating the integration safeguards; build current source
for the integrated node. See [CONTRIBUTING](CONTRIBUTING.md).

[Requirements](docs/requirements.md), [architecture](docs/architecture.md),
[hardware](docs/hardware.md), [baseline](hardware/vesc/baseline-kart/2026-11-05/README.md),
and [configuration review](docs/eamon-config-review.md) retain the detailed context.
