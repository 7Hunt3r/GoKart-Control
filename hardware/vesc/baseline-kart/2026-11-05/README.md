# Connected kart: VESC baseline inspection

**Primary hardware baseline for the rebuild, as directed by the owner.** Read on
November 5, 2026 through USB on the owner's Mac. No configuration writes, motor
commands, firmware changes or resets were issued.

This report describes **current runtime configuration**, not a confirmed saved
flash backup. A running dashboard or other tool could have applied temporary
limits before the read. Do not import these settings into another controller as
a preset. The actual commercial controller/motor/battery ratings remain unknown.

## Identity and evidence

| Item | Read value |
| --- | --- |
| Firmware | 6.00 |
| Reported hardware | `75_100_V2` |
| Firmware name | `no_hw_limits` |
| Controller/CAN ID | 69 |
| Motor-control mode | FOC |
| Position sensors | Hall Sensors |
| Active application | ADC and UART |
| ADC control | Current Reverse Button |
| UART baud | 115200 |
| USB device | ChibiOS/RT Virtual COM Port |
| Capture port | `/dev/cu.usbmodem3041` (session-specific) |

The firmware name is a reason to verify firmware provenance and physical ratings;
it does not establish the controller's allowable current or voltage, nor prove
that all protections are disabled. No firmware change is proposed here.

The [manifest](manifest.json) records timestamps, read command IDs and SHA-256
hashes. Raw files contain response payloads including the command byte; transport
CRC was checked when received. The entire captured configuration was decoded
using VESC Tool's pinned firmware-6.00 schemas:

| Configuration | Fields | Payload bytes | Matching schema signature |
| --- | --- | --- | --- |
| Motor | 189 | 482 | `2e43a161` |
| Application | 187 | 494 | `1d003a2c` |

Both signatures match and no trailing bytes remain. The decoder also consumes
the full 74-byte telemetry response, including its status byte.

## Drive limits and braking

| Setting | Captured value | Interpretation |
| --- | --- | --- |
| Motor current max | 140 A | Configured phase/motor-current ceiling |
| Motor-current max scale | 1.0 | Full configured forward scale at capture |
| Motor current min/brake | 0 A | Consistent with disabled motor braking |
| Battery current max | 100 A | Distinct from motor current and absolute-current fault threshold |
| Battery current min/regen | -100 A | Regen allowance is nonzero; do not describe every regen setting as zero |
| Absolute current max | 240 A | Separate absolute-current fault threshold; not a battery limit |
| ERPM bounds | -150,000 to +150,000 | Electrical RPM, not road-speed MPH |
| ERPM limit reduction starts | 80% of bound | Configured value; actual load behavior not tested |
| Duty maximum | 0.95 | Configured duty ceiling |
| Watt bounds | -1,500,000 to +1,500,000 W | Broad configured bounds, not physical power capability |
| Field-weakening current max | 95 A | Existing setting; include in load investigation |
| Field-weakening start duty | 0.87 | Existing setting |
| Field-weakening ramp | 0.2 s | Existing setting |

The owner confirms mechanical pedal braking and that regenerative braking is
intentionally off. Preserve that behavior. This inspection did not exercise any
braking mode or establish every possible firmware control path.

## Voltage and thermal settings

| Setting | Captured value |
| --- | --- |
| Minimum/maximum input-voltage settings | 12 / 90 V |
| Battery cutoff reduction start/end | 68 / 60 V |
| ESC temperature reduction start/end | 85 / 100 C |
| Motor temperature settings | 85 / 100 C, but sensor disabled |
| Motor temperature sensor type | Disabled |

The owner confirms **motors have no temperature sensors wired**. Therefore motor
temperature is unavailable and the configured motor-temperature limits cannot
be treated as measured thermal protection on this setup. ESC temperature is a
separate reading. Do not display the sampled motor 0 C as a real temperature.

Battery voltage is owner-reported as 72 V nominal / 84 V full. The 90 V software
limit is not independent evidence of the physical controller's voltage rating.

## Inputs, topology and speed metadata

| Setting | Captured value |
| --- | --- |
| ADC throttle start/end | 0.837 / 2.385 V |
| ADC deadband (`hyst`) | 0.15 |
| Positive/negative ramp | 0.3 / 0.1 s |
| ADC safe start | Regular |
| ADC update rate | 500 Hz |
| ADC multi-ESC | Enabled |
| ADC traction control | Disabled |
| Command timeout | 1000 ms |
| Timeout braking current | 0 A |
| Configured kill-switch mode | Disabled |
| Motor poles | 14 |
| Gear ratio | Approximately 2.769231 |
| Wheel diameter | 0.406 m |
| Battery metadata | Li-ion, 20 cells, 20 Ah |
| BMS type / limit mode | VESC BMS / Unused |

These fields do not verify the physical wheel diameter, gearing, battery rating,
BMS presence or CAN topology. `multi_esc` does not prove another controller is
connected; CAN was not scanned. A disabled configured kill-switch mode does not
establish whether a separate physical power disconnect exists.

The ordinary command timeout is not proof that temporary profile changes revert
to saved settings. The rebuild's saved-config fallback needs a separate verified
mechanism, as described in the architecture document.

## Stationary telemetry sample

| Reading | Value |
| --- | --- |
| Input voltage | 76.4 V |
| ESC temperature | 22.3 C |
| Motor temperature raw | 0 C — invalid/unavailable, sensor disabled |
| Motor and battery current | 0 A |
| ERPM and duty | 0 |
| Fault code | 0 (no active fault at that instant) |
| Timeout / configured kill-switch status | Both inactive |

This is not a driving log or historical-fault query. It does not diagnose the
reported drift cutoff or establish thermal/current headroom.

## What differs from the old app

The old repository described SPORT as 140 A motor / 240 A battery. This kart reads
140 A motor / **100 A battery**. The old source did not send its battery-current
profile values and did not implement the field-weakening changes described in
its README. Treat those descriptions as historical claims, not current settings.

The actual kart also has configured wheel/gear metadata and 95 A field weakening
that must be considered independently of the old UI. No amperage or profile
changes have been made as part of this inspection.

## Files and reproduction

- `raw/`: firmware, telemetry, motor and application response payloads.
- `decoded/`: identity, full motor/app settings with enum labels, and telemetry JSON.
- `manifest.json`: capture procedure, scope, timestamps and payload hashes.

From the repository root:

```sh
python3 -B tools/vesc/decode.py hardware/vesc/baseline-kart/2026-11-05/raw --out /tmp/gokart-baseline-review
python3 -B -m unittest discover -s tests -v
```

Use a fresh output path. The decoder never connects to the VESC. For another
hardware read, use the allowlisted procedure in `tools/vesc/README.md`.
