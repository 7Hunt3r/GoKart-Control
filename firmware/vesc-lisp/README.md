# On-VESC brake script (regen on STOP)

`gokart_brake.lisp` runs inside the VESC (firmware 6.00 LispBM). When the pit presses
STOP, the kart's Heltec ramps the drive current limit to zero AND sends this script a
brake request (`COMM_CUSTOM_APP_DATA` = `"GKB" amps tenths`). The script disables the
pedal app's output for that long, applies the brake current, then releases.

Why a script: the throttle (ADC) app re-asserts the pedal's command hundreds of times
a second, so a brake command sent from outside is overridden within milliseconds.
`app-disable-output` is the supported way to hand the motor to a script briefly.

## Per kart, once, in VESC Tool (owner's decision; saved-config change)
1. Motor Settings > General > Current: set **Motor Current Max Brake** non-zero
   (e.g. -40 A). With 0 A, which is this fleet's baseline, no regen braking is possible
   and the request is a no-op (kart coasts).
2. VESC Dev Tools > LispBM: open `gokart_brake.lisp`, **Upload code**, enable **Run on boot**.
3. With the drive wheels off the ground: press STOP on the pit. The wheels should spin
   up on throttle, then brake to a stop within ~2.5 s when STOP is pressed, and the
   throttle stays dead until START.

## Tuning
`STOP_BRAKE_AMPS` / `STOP_BRAKE_SECS` in `firmware/kart-heltec-v4/config.h` (sent in the
request); `brake-max-secs` in the script caps it. Regen current also flows into the
battery: the VESC's battery regen limit and the BMS charge limit apply.

Status: script written against the 6.00 LispBM API; not yet uploaded to any kart.
