# Removable bench control — actual test results

Updated November 5, 2026. **Motor tests are paused at the owner's request. Basic
Heltec telemetry is restored.** The owner requested a five-second spin, then
30 seconds at 10 A. Neither sustained-duration test completed. A half-second
5 A probe did move forward, confirmed by the observer.

## Installed board and vehicle state

The Heltec runs `firmware/heltec` **0.2.1-bench**, kart ID 1, shared 46-byte
GoKartProtocol at 915 MHz / BW500 / SF7 / CR4/5. Maintenance is off, radio TX is
on, VESC writes are disabled. Following the tests, fresh identity-verified
telemetry showed 0 ERPM, 0 A, fault 0; the recovery window had 12 TX completions
and zero TX errors. Receipt at Eamon's base has not been independently verified.
No VESC configuration, firmware, motor detection, reset or CAN-forwarding command
was sent during these tests. No kart reset was needed.

The normal image was used only for telemetry/maintenance. All motor requests
came from the separate diagnostic. The ordinary rule remains unchanged: accessory
or communication loss must not alter the ESC's active settings or wired controls.

## Direct laptop Bluetooth findings

At the owner's suggestion, the Mac connected directly to `VESC BLE UART` using
Bleak 0.22.3 and Nordic UART. Identity and complete motor/app configuration bytes
matched the reviewed snapshot before each actuation attempt. This bypassed the
Heltec diagnostic's unresolved transport stalls.

The command path matches VESC Tool's current control: command 6 with signed
milliamps. Command 63 temporarily suppresses the hardwired ADC application's
output for 300 ms, renewed during the bench test; `forward_CAN=false`. Release
attempts command 6 at 0 A followed by command 63 at 0 ms. No configuration write
is involved. The owner confirmed raised wheels, released pedals and access to
physical cutoff, and later confirmed forward direction.

| Test | Evidence and outcome |
| --- | --- |
| Read-only preflight | Exact identity/settings match; paired values/ADC reads usually about 120 ms. |
| Five-second zero-current loop | Completed without motion/current/fault. |
| 1 A / 250 ms probe | Current response, no rotation; owner saw no movement. Its final sample exceeded the script's initial ADC guard, but the first implementation still printed completed. That reporting defect was fixed and regression-tested before later trials. |
| 5 A / 500 ms probe | Reached 380 ERPM and 4.91 A reported current. Owner: forward for about half a second. Release completed at 628 ms; subsequent values returned to zero, fault 0. |
| 5 A / five-second attempt | Speed guard triggered at 1,261 ms: 580 ERPM exceeded the 500 limit. Release completed at 1,380 ms, coast-down peaked at 682 ERPM, then zero. Fault 0. **Aborted, not a passed five-second test.** |
| Up to 10 A / thirty-second attempt | Current tapered toward a 600 ERPM target, with a 2,000 ERPM guard. Samples rose 288 → 1,918 → 2,474 ERPM; abort at 750 ms, release complete at 868 ms. Peak reported current 8.54 A. Subsequent readings 1,414 → 754 → 0 ERPM, current/fault 0. **Aborted, not a passed thirty-second test.** |

[Immutable traces and capture notes](../hardware/vesc/baseline-kart/2026-11-05-direct-ble-bench/README.md)
preserve the complete measurements, including failures. An initial 30-second
attempt failed peripheral discovery before sending commands: the Heltec's
maintenance timer had expired and reclaimed BLE. Reopening maintenance resolved
that client conflict; it was not a VESC motor failure.

The current running ADC guard allows decoded level <=0.10, below the matched
configuration's 0.15 deadband. The starting neutral check remains <=0.02.
The rise to about 0.055 under current was within that existing deadband; the
VESC calibration was not changed. The cause of the voltage shift is not proven.

GET_VALUES current is averaged, and the read/write loop has latency. The
observed speed overshoot demonstrates that host-side current tapering is not a
validated speed controller. Zero current is coasting, not active braking.
Readback of zero after the test does not establish a guaranteed stopping bound.
The requested mid-speed target and physical MPH calibration remain unestablished.

## Separate Heltec prototype

`firmware/bench-control` is removable and excluded from `firmware/heltec`.
Its default `bench_preflight` cannot arm or send motor/app-handoff commands.
`bench_diagnostic` explicitly enables the prototype's 1 A, 250 ms probe and
five-second window, with 150 ERPM target and 500 ERPM guard. These are separate
from the laptop runner's newer limits.

Earlier Heltec 0.2.0/0.2.1 ten-second zero-current trials aborted on stale
telemetry; each sent zero positive-current requests. Versions 0.2.2/0.2.3 stalled
in preflight. The 0.3.1 draft separates USB I/O from BLE work with queues and
worker-stage tracing; its motor-disabled live trial still stalled around
preflight. It was replaced with the normal 0.2.1-bench image. No positive-current
Heltec diagnostic test has succeeded. The working laptop path does not resolve
that firmware transport issue.

Both PlatformIO variants build; native sanitizer checks and 11 diagnostic Python
tests pass. The 13 repository tests also pass, including localhost OTA tests
run outside the filesystem/network sandbox. Those checks do not validate
physical release under link/host failure.

## Configuration comparison

The [fresh snapshot](../hardware/vesc/baseline-kart/2026-11-05-bench-preflight/README.md)
differs from the original capture only in six boot-calibration offsets; all
other 183 motor fields and all 187 application fields match. Complete reviewed
bytes are still required. No settings were written to force a match.

## Next work

1. Keep the installed kart on basic telemetry and prove reception at Eamon's hub.
2. Reconcile [Eamon's command-firmware proposal](eamon-config-review.md) before
   enabling writes or replacing the installed image. STOP/START changes current
   limits; START is not a wheel-spin command.
3. Before resuming motor tests, establish bounded speed control and release
   behavior under real BLE timing, rather than increasing guards until a test
   passes. Preserve the actual failed traces and observer requirements.
4. Resolve the separate Heltec preflight stall before porting validated bench
   control to it or extending that control to LoRa.

## Protocol sources

[VESC Tool keyboard handling](https://github.com/vedderb/vesc_tool/blob/master/mainwindow.cpp),
[VESC Tool command encoding](https://github.com/vedderb/vesc_tool/blob/master/commands.cpp),
[VESC 6.00 command handlers](https://github.com/vedderb/bldc/blob/6.00/comm/commands.c),
[ADC application/deadband](https://github.com/vedderb/bldc/blob/6.00/applications/app_adc.c),
and [finite application timer](https://github.com/vedderb/bldc/blob/6.00/applications/app.c).
These source references do not prove binary provenance of the connected custom
`no_hw_limits` firmware.
