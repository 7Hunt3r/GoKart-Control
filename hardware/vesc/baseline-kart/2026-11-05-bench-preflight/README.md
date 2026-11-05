# Read-only bench preflight snapshot

The Heltec diagnostic read complete 482-byte motor and 494-byte application
configuration replies over BLE from the identity-gated baseline VESC. Schema
signatures and full payload consumption were verified with the pinned decoder.
No VESC settings, motor commands, resets or firmware updates were sent.

Compared with the immutable November 5 initial capture, all 187 application
fields and 183 of 189 motor fields are identical. The six differences are
`foc_offsets_current__0..2` and `foc_offsets_voltage__0..2`; see comparison.json.
The setting `foc_offsets_cal_on_boot` is 1. Upstream 6.00 recalculates these six
offsets during boot calibration, so the observed changes are consistent with
calibration; this does not independently prove when the controller rebooted.
[Source](https://github.com/vedderb/bldc/blob/6.00/motor/mcpwm_foc.c).

All drive/current/voltage/timeout/input settings match the initial capture.
This snapshot is used for the temporary diagnostic's exact byte comparison;
no fields are ignored and the original evidence is unchanged. A later mismatch
still blocks arming and requires inspection. These are runtime reads, not a
verified saved-flash backup. The earlier identity payload was not copied here
and presented as a fresh capture; the diagnostic's verified identity gate and
capture scope are recorded in manifest.json.
