# Read-only VESC inspection

These tools capture current runtime state and decode it offline. They cannot
write VESC configuration, command motor current/speed, reboot or flash firmware.
Only command IDs 0, 4, 14 and 17 are allowed by `capture.py`.

## Capture

Use Python 3.9+ on macOS/Linux. Close VESC Tool or other serial clients. Have the
owner power and connect the intended controller. Confirm the explicit serial port;
the Mac port used on November 5 was `/dev/cu.usbmodem3041`, which may change.

```sh
python3 -B tools/vesc/capture.py --port /dev/cu.usbmodem3041 --out /tmp/gokart-new-capture
python3 -B tools/vesc/decode.py /tmp/gokart-new-capture --out /tmp/gokart-new-decoded
```

Both output directories must be new. On Linux use the actual `/dev/ttyACM…` or
other identified VESC device. Device permissions are required; do not broadly
change system serial permissions as a workaround. Capture performs no automatic
scanning/probing of unrelated devices and stops after identity if firmware is
not 6.00. Read requests use USB serial at 115200 and never send keepalive/control
commands. The controller remains in charge of existing driver inputs.

A partial capture retains a manifest with `capture_complete: false`; inspect it
before trusting the directory. The raw `.bin` files contain response payloads
(command byte plus data), not entire USB frames. Frames were CRC-checked on receipt.

## Offline decode and verification

```sh
python3 -B tools/vesc/decode.py hardware/vesc/baseline-kart/2026-11-05/raw --out /tmp/gokart-baseline-decoded
python3 -B -m unittest discover -s tests -v
```

The decoder verifies schema CRC32C signatures and exact lengths before accepting
motor/application values. Firmware 6.00 schemas are pinned under `third_party`.
It currently accepts this 74-byte 6.00 telemetry layout only; fail explicitly on
other layouts rather than guessing offsets. Tests use captured payloads and
synthetic corrupt/fragmented frames. They do not access hardware.

These exports are not flash images, not a proven saved-configuration backup,
and not a ready-to-import tuning preset. GET_VALUES reads reset some averaging
accumulators in ordinary VESC operation. No load/fault-history trace is collected.

The initial baseline used a small one-off read script with the same four
allowlisted queries. The maintained capture CLI was prepared afterward and tested
offline; it has not itself been run against hardware yet. The maintained decoder
has been run against the actual captured baseline.
