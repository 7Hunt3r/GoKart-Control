# Eamon's read-only / command-firmware handoff

Reviewed November 5, 2026 at `origin/eamon/firmware-lora-starter` commit
`d91422b6673f8fba4059540cc9f9fd5e07940117`. The relevant instructions were added in
[`4164bc1`](https://github.com/colinfost3r/GoKart-Control/commit/4164bc15d451ffa687e84cb30365d3d415fc7a17),
in **HANDOFF.md, “Bench test with Colin … link first, then kill switch”** and the
firmware README's “What the kart does with a command” section. The newer
`d91422b` adds bench results, makes BLE the default and requires a pinned BLE address.

**“Read-only” refers to the installed `firmware/heltec` image, not filesystem or
README permissions.** It only reads the VESC and sends LoRa telemetry. It has no
pit-command receiver or VESC-write path. Our separate direct-laptop motor test
does not add those capabilities to the installed Heltec.

## His proposed sequence and current status

1. Prove reception with kart ID 1 and `tx on antenna-confirmed`. This is already
   configured on Colin's restored 0.2.1-bench image. Fresh BLE telemetry and local
   TX completion were verified; actual receipt at his pit is still unconfirmed
   here. His latest handoff reports the Pi 4 pit/kiosk running and successful
   reception/ping/OTA with his kart 2 Heltec, without a VESC attached. Those are
   collaborator-reported results, not receipt of our kart 1.
2. For pit STOP/START/mode testing, use `firmware/kart-heltec-v4` built as
   `kart` (BLE is now the default). Its `config.h` defaults to `ENABLE_VESC_WRITES=1`; setting it to
   `0` rejects write commands. The newer `kart-uart` target is the
   optional wired variant; `kart-ble` was the old target name. Commission the
   exact BLE MAC using `vescmac`; unpinned firmware now scans without connecting.
3. The proposed command test is still a separate deployment/validation step.
   The owner has now requested basic telemetry and review, not an immediate
   flash of that alternate image. No alternate image was installed during this
   review and no VESC configuration was changed.

The radio agreement already matches: 915 MHz, BW500, SF7, CR4/5, sync 0x12.
His text still describes our legacy 47-byte frame, but the installed image now
uses his canonical 46-byte GoKartProtocol. His receiver accepts that format.
No radio-format change is needed merely to prove reception.

## What “configuration” means here

His command-capable firmware sends `COMM_SET_MCCONF_TEMP` (48) with `store=0`,
not a firmware upload or saved-flash write. It changes runtime limits:

- STOP requests motor-current scales 0.6, 0.3, 0.1, then 0.
- START re-applies a requested mode, or the scale captured before STOP.
- ECO/NORMAL/SPORT use 80/140, 100/140 and 1.0 current scales from the old app.
- The payload also includes current-min scale, ERPM, duty and watt limits.
  Battery-current fields are omitted.
- It requests an acknowledgment, re-reads motor configuration and reports
  applied only after the live current-max scale matches the requested scale.

This reduces or restores available throttle torque. **START does not command
wheel rotation.** It is distinct from our direct-current bench runner and from
the owner's eventual preference for top-speed profiles with strong acceleration.
A temporary configuration is not a momentary lease: link loss does not by itself
restore the previous scale. That must be understood when testing STOP/START.

## Concrete issues to reconcile before adopting the write path

This is source review, not an exhaustive audit or a hardware certification.

- **STOP must never raise the current allowance.** The fixed first step is 0.6.
  ECO is 80/140 ≈ 0.571, so STOP initially raises that allowance before lowering
  it. Any live scale below 0.6 has the same problem. Start from a fresh observed
  scale and ensure every step is non-increasing.
- **Preserve actual unrelated runtime limits.** `startWrite()` populates ERPM,
  duty, watt and current-min scale from `BASE_*` constants, not from the latest
  read configuration. They match this original capture but are not necessarily
  the live values or another kart's values. Copy fresh verified limits and
  change only the intended field; verify every field the payload can affect.
- **Pin and verify the intended controller.** His latest BLE selection now
  requires a pinned MAC, which resolves the earlier name-only selection issue.
  It still lacks a full VESC UUID check. Carry over our UUID/firmware checks
  before writes as well as pinning the BLE module.
- **Keep deployment policy explicit.** His default enables writes and optional
  OTA authentication; our normal image disables writes and requires authenticated
  maintenance-only OTA. Preserve the agreed maintenance/authentication behavior
  when integrating. Do not adopt a different policy by flashing defaults.
- **Keep scope explicit.** The owner deferred fleet remote stop and tuning, later
  authorizing separate bench experiments. The alternate implementation and its
  config writes have not run on this kart. Receive-only integration and command
  integration need separate, accurately reported test results.

Source locations at the reviewed commit:
[handoff](https://github.com/colinfost3r/GoKart-Control/blob/d91422b6673f8fba4059540cc9f9fd5e07940117/HANDOFF.md),
[firmware README](https://github.com/colinfost3r/GoKart-Control/blob/d91422b6673f8fba4059540cc9f9fd5e07940117/firmware/README.md),
[kart config](https://github.com/colinfost3r/GoKart-Control/blob/d91422b6673f8fba4059540cc9f9fd5e07940117/firmware/kart-heltec-v4/config.h),
[kart command implementation](https://github.com/colinfost3r/GoKart-Control/blob/d91422b6673f8fba4059540cc9f9fd5e07940117/firmware/kart-heltec-v4/kart-heltec-v4.ino),
and [temporary-limit encoding](https://github.com/colinfost3r/GoKart-Control/blob/d91422b6673f8fba4059540cc9f9fd5e07940117/firmware/libraries/VescLink/VescLink.cpp).

Eamon's branch remains separate. Fetching and reviewing it does not deploy it,
merge conflicting documentation, or modify his work. Colin's diagnostic progress
and this reconciliation are published together on the diagnostic draft branch.
