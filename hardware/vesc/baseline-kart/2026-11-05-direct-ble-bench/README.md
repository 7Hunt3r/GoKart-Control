# Direct Bluetooth bench evidence — November 5, 2026

Owner-confirmed wheels raised, pedals released, observer at physical cutoff.
This is **actuation evidence**, not a read-only inspection or saved configuration.
No VESC configuration/firmware writes, resets or motor detection were performed.
Motor temperature is null because no sensor is wired.

| Trace | Result |
| --- | --- |
| `direct-ble-readonly.jsonl` | Exact UUID, firmware and complete reviewed runtime settings matched; 11 stationary samples. |
| `direct-ble-zero.jsonl` | Five-second zero-current loop completed; 0 ERPM/current/fault. |
| `direct-ble-probe.jsonl` | One 1 A request; no rotation in telemetry or observer report. Script incorrectly reported completed despite decoded ADC 0.055 exceeding its then-0.02 guard. Fixed before later trials; do not count this as a guard pass. |
| `direct-ble-probe-5a.jsonl` | Two 5 A requests over a 500 ms window; 380 ERPM, peak reported current 4.91 A. Owner saw about half a second forward. Release writes completed at 628 ms; subsequent samples returned to 0 ERPM/current, fault 0. |
| `direct-ble-five-seconds-5a.jsonl` | Aborted at 1,261 ms sample when 580 ERPM exceeded the 500 ERPM guard. Release writes completed at 1,380 ms. Coast-down sample reached 682 ERPM, then zero. Fault remained 0. **Five-second test did not complete.** |
| `direct-ble-thirty-seconds-10a.jsonl` | Owner requested 30 s / 10 A. Tapered-current test aborted at 750 ms sample when 2,474 ERPM exceeded the 2,000 ERPM guard. Peak reported current 8.54 A; release writes completed at 868 ms. Subsequent samples 1,414 → 754 → 0 ERPM, current 0, fault 0. **Thirty-second test did not complete.** |
| `restored-telemetry.json` | Normal Heltec 0.2.1-bench, kart 1, fresh BLE, BW500/46-byte LoRa TX, maintenance off, writes disabled, current/RPM/fault 0. Selected status fields only; network details omitted. |

GET_VALUES current is averaged since the preceding read; it is not an
instantaneous current ceiling. ERPM is electrical speed, not wheel RPM. Values
may lag motion and software guards cannot prevent overshoot between samples.
Release means zero-current request and ADC re-enable, followed by observed zero;
it is not an active braking or stopping-distance guarantee.

The running ADC threshold was changed to 0.10 after inspecting the exact matched
configuration's 0.15 deadband and upstream ADC implementation. Stationary arming
remains at 0.02. This changed only the diagnostic guard, not the VESC settings.

The Heltec was in maintenance to release BLE during laptop tests. Maintenance
expiry briefly reclaimed the peripheral before the first 30-second attempt;
opening a new maintenance window resolved discovery. On the owner's instruction,
all motor testing ended and basic telemetry was restored.

These results establish direct laptop BLE current control and a short physically
confirmed forward movement. They do **not** validate the Heltec diagnostic,
sustained speed control, link-loss handling under power, or base-station control.
The [test report](../../../../docs/bench-control-test.md) records next work.
