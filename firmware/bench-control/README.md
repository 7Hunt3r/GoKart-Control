# Removable wheels-up bench diagnostic

**Development diagnostic, not vehicle firmware.** This separate PlatformIO target
is for the owner-requested low-current forward test. The normal `firmware/heltec`
build never includes it. Delete this folder to remove the implementation; no
production motor-command hooks or radio command API need to be removed.

Status: **draft; not ready for a powered motor test.** Offline tests and builds
pass, but live zero-current handoff trials aborted on stale telemetry and some
preflight attempts stopped reporting. No nonzero-current request was sent, no
probe was run, and no five-second spin was completed. The requested “mid speed”
has not been translated into a verified RPM target; this prototype's low-current,
150-ERPM target must not be described as that requested test.

The physical Heltec has been restored to the tested `0.2.1-bench` telemetry image (shared 46-byte frame, BW500),
ID 1, TX enabled, fresh VESC values, zero current/RPM/fault. No kart reset was
needed for that recovery. The diagnostic is isolated on a draft branch/PR.
See [hardware test findings](../../../docs/bench-control-test.md).

The default build is `bench_preflight`, which cannot arm and rejects motor/app
handoff writes at the transport layer. `bench_diagnostic` explicitly compiles in
those commands; it is for development after resolving the failures below, not
an approved working motor-control image.

## What it does

- Boot/reconnect are read-only and disarmed. No cached pulse resumes.
- Connect only to the observed BLE address, then verify the captured VESC UUID
  and firmware 6.00. Compare complete GET_MCCONF/GET_APPCONF replies against the
  reviewed runtime snapshot bytes, without ignored fields. No configuration or firmware writes.
- Read values and decoded ADC. Arming requires recent matching settings, zero
  ERPM/current, neutral throttle, no fault, fresh samples and the USB host.
- `dry-run`: five seconds of **zero-current commands**, with the same temporary
  ADC handoff used by the pulse. This is state-changing even though it requests
  no torque; do not describe it as passive telemetry.
- `probe`: at most 250 ms of requested operation. It requests positive 1 A motor
  current; this is not yet known to be physical forward. Do not silently raise
  it if static friction prevents visible rotation.
- `run positive-forward-confirmed`: up to five seconds after the physical probe
  direction has been confirmed. Request 1 A below 100 ERPM and zero above 150
  ERPM, with hysteresis. This is a coarse low-speed test, not precision speed PID.
- Abort on stale values/ADC (>350 ms), missing host heartbeat (>600 ms), BLE
  loss, non-neutral throttle, fault, >3 A observed motor current, ERPM below -50
  or above 500, ESC temperature outside 0–60 C, voltage outside 60–85 V, explicit
  abort, or duration expiry. These conservative diagnostic thresholds are not
  tuning advice or independently enforced hardware limits.

## How control returns to the pedals

The captured kart uses the VESC ADC application, which normally overwrites
remote current requests. The diagnostic sends `COMM_APP_DISABLE_OUTPUT` (63)
with **300 ms**, `forward_CAN=false`, then `COMM_SET_CURRENT` (6) with only 0 or
1,000 mA. While the test runs, it renews this finite timer. It never requests the
indefinite -1 form. Queries use only IDs 0, 4, 14, 17 and 32.

On completion/abort it attempts zero current and immediate ADC re-enable. If
BLE or the Heltec fails, the controller's finite app timer is expected to restore
hardwired input independently. The captured app timeout is also 1,000 ms with
zero brake current. Neither mechanism has been physically verified for this
custom `no_hw_limits` image. Telemetry identity alone does not establish binary
provenance. A physical power cutoff remains necessary for testing.

**Keep feet off the pedals throughout.** Releasing the ADC handoff restores
whatever the physical inputs request. The command deadlines are software
limits; BLE scheduling can delay delivery. They are not a certified stopping
bound. Zero current means coasting, not regenerative or mechanical braking.
This temporary bench behavior does not change the normal reliability policy:
ordinary vehicle communications loss never changes drive state.

## Build and offline checks

From the repository root:

```sh
clang++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined -I firmware/heltec/include -I firmware/bench-control/include firmware/bench-control/test/control_test.cpp -o /tmp/gkc-bench-test
/tmp/gkc-bench-test
pio run -d firmware/bench-control -e bench_preflight
pio run -d firmware/bench-control -e bench_diagnostic
```

Run `python3 -B -m unittest discover -s firmware/bench-control/test -v` for the
USB runner checks. Native tests cover request encodings, paired sample reads, arming, current
and duration bounds, stale data, disconnects, neutral/fault/temperature/RPM
checks, clock rollover and zero-current dry run. These do not prove physical
motor behavior. BLE integration remains a separate acceptance step.

## Planned controlled test sequence — blocked until diagnostic failures are fixed

1. Support the driven wheels securely; clear people/tools from wheels and chain.
   Release both pedals. One person observes rotation with access to physical
   power cutoff. Do not perform while Eamon is expecting continuous telemetry.
2. Preserve the known-good `firmware/heltec` build and local credentials. This
   diagnostic uses the same 16 MB / two-app partition layout but has **no Wi-Fi,
   OTA, GPS or radio service**. USB is required to restore the normal image.
3. Install only when ready for this isolated test:

   ```sh
   pio run -d firmware/bench-control -e bench_preflight -t upload --upload-port /dev/cu.YOUR_HELTEC
   ```

4. Use a Python environment with pyserial; first observe without actuation:

   ```sh
   python firmware/bench-control/console.py --port /dev/cu.YOUR_HELTEC
   ```

   Require matching identity/settings, neutral ADC and fresh zero-ERPM values.
   Baseline mismatch blocks arming; investigate, never bypass the byte comparison.
5. Only after the diagnostic transport passes review and read-only validation,
   explicitly build/install `-e bench_diagnostic` to exercise the zero-current
   handoff and then a brief direction probe. **The trials in this session failed
   before any torque probe, so do not proceed as if these are passed checks.**

   ```sh
   python firmware/bench-control/console.py --port /dev/cu.YOUR_HELTEC --test dry-run --wheels-up-neutral
   python firmware/bench-control/console.py --port /dev/cu.YOUR_HELTEC --test probe --wheels-up-neutral
   ```

   Inspect the live trace and physical return to zero torque before proceeding.
   Validate host/BLE-loss behavior on the zero-current stage before a torque test.
   A nonzero fault, stale samples, ambiguous result or unexpected motion ends
   the test. Do not automatically retry or reinterpret an aborted test as success.
6. Only after positive current is physically confirmed forward:

   ```sh
   python firmware/bench-control/console.py --port /dev/cu.YOUR_HELTEC --test five-seconds --wheels-up-neutral --positive-is-forward
   ```

   The runner sends keepalives, logs JSON to stdout and attempts abort on exit.
   Ctrl-C is a software abort, not a substitute for the physical cutoff.
7. Restore the telemetry image via USB and verify its version, kart ID 1,
   read-only BLE telemetry and GPS. Re-enable radio TX with
   `tx on antenna-confirmed`; radio/OTA remain off after every boot.

   ```sh
   pio run -d firmware/heltec -t upload --upload-port /dev/cu.YOUR_HELTEC
   ```

Record traces and outcomes before claiming the requested five-second test passed.
Local `.pio` output is ignored. No credentials are used by this diagnostic.

## Evidence used for the exact comparison

The [fresh bench snapshot](../../../hardware/vesc/baseline-kart/2026-11-05-bench-preflight/README.md)
preserves the read-only motor/application capture. Compared with the first
capture, only six boot-calibration offsets differ; all drive limits and all
application settings match. The generator embeds the complete reviewed replies
into build output. A further difference still blocks arming. Nothing was written
to the VESC to make it match.

## Protocol evidence

The command definitions and finite application timer were reviewed in the
upstream VESC 6.00 sources:
[command handlers](https://github.com/vedderb/bldc/blob/6.00/comm/commands.c),
[application timer](https://github.com/vedderb/bldc/blob/6.00/applications/app.c),
[ADC loop](https://github.com/vedderb/bldc/blob/6.00/applications/app_adc.c), and
[command IDs](https://github.com/vedderb/bldc/blob/6.00/datatypes.h).
The connected custom firmware still needs the staged physical checks above.
