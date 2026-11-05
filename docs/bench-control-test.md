# Isolated motor-test diagnostic — development findings

The owner requested remote bench control, removable from the normal code, with
the wheels lifted. The latest requested outcome is **a five-second wheel spin
at mid speed**. That test has **not run**. No positive-current request was sent.
The planned next step is base-station motor testing through Eamon’s hub, after
local control and its release behavior are validated.

## Physical board at handoff

Restored `firmware/heltec` version `0.2.0-bench`, kart ID 1. Fresh VESC identity and
telemetry resumed without resetting the kart: zero ERPM, zero motor current,
fault 0; LoRa had 18 TX completions, zero errors in the recovery window. Radio TX
was left on, maintenance off, VESC writes disabled. This recovery shows the
normal BLE telemetry path still works; it does not prove the diagnostic reliable.

## What the prototype contains

`firmware/bench-control` is a separate removable PlatformIO project. The default
preflight build cannot arm or send command 6/63. An explicit diagnostic target
adds a 1 A maximum request, a 250 ms probe, and a five-second software deadline.
Its coarse target is 150 electrical RPM with a 500 ERPM abort threshold; these
are prototype limits, not a calibrated “mid speed” or verified physical limit.
Normal telemetry code was not given a motor-write API.

It checks the exact VESC UUID/firmware and motor/application bytes, neutral ADC,
zero RPM/current at arm, faults, sample ages and host keepalives. A finite 300 ms
VESC application-output timer is renewed separately from current commands.
Asynchronous acknowledged GATT writes keep the control state machine responsive
while awaiting an acknowledgment. Values/ADC reads are batched in one write in
the latest draft. These mechanisms still need successful end-to-end validation.

## Observed failures and useful evidence

- Read-only preflight eventually read complete 482-byte motor and 494-byte app
  configurations. Initial exact comparison failed only on six FOC calibration
  offsets; every other motor field and every application field matched.
  The [new immutable snapshot](../hardware/vesc/baseline-kart/2026-11-05-bench-preflight/README.md)
  records that comparison and is used by the diagnostic. No settings were written.
- With the reviewed snapshot, identity/settings matched, live samples were fresh,
  neutral ADC was about 0.852–0.858 V (decoded level about 0.010–0.014), and
  current/RPM/fault were zero. Full-byte matching remains enabled.
- Zero-current ten-second trials on prototype 0.2.0 and 0.2.1 aborted early with
  `telemetry_stale`. Each recorded zero positive requests and three zero-current
  requests including cleanup. The BLE acknowledgment/release sequence completed; physical handoff remains
  unverified and no positive-current torque test occurred. These were earlier ten-second dry-run builds, before the
  owner's later five-second request.
- Redundant current refreshes were reduced; batched sample reads were added.
  Attempts with 0.2.2/0.2.3 stopped reporting during preflight before arming.
  No successful sustained zero-current trial is claimed. A shorter requested BLE
  connection interval did not establish a reliable fix and was removed.
- The latest source is `bench-control-0.3.0-draft`, now capped at five seconds.
  It is built/offline checked only and **not installed on the kart**.
- Detailed serial logs are local/ignored. Runtime results are not proof of
  controller-side timer behavior, physical forward direction or stopping distance.

## Next engineering acceptance gates

1. Locate intermittent preflight stalls with transport/task tracing; distinguish
   GATT discovery, notifications, parser and USB output. Do not guess a root cause
   from missing serial output alone.
2. Establish measured throughput and fair scheduling for finite ADC leases,
   current refreshes, values and ADC reads. Preserve explicit failure behavior;
   do not increase drive limits to make a test appear successful.
3. Pass a complete zero-current handoff and host/BLE-loss checks. Verify release
   and unchanged hardwired-input behavior with an observer and physical cutoff.
4. Run a brief low-current probe and have the observer confirm physical forward.
   Agree a concrete speed for the requested five-second spin; MPH calibration
   and the meaning of “mid speed” are not established by this prototype.
5. Restore normal read-only telemetry after diagnostics. Only then extend the
   tested bench command flow to Eamon’s hub with explicit arming, bounded duration,
   authentication and result reporting. No LoRa motor commands are enabled now.
