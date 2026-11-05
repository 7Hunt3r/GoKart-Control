# Heltec bench verification — November 5, 2026

Tested one owner-supplied HTIT-WB32LAF V4.3 connected by USB to the development
computer, with its LoRa antenna and L76K GPS attached. The powered VESC was the
same UUID/firmware-6.00 controller as the original hardware baseline. The kart
was stationary in the observed samples; no motor actuation test was performed.

## Installed build and recovery

- Installed firmware: `0.1.3-bench` from `firmware/heltec`.
- ESP32-S3 rev 0.2, 16 MB flash, 2 MB embedded PSRAM identified with esptool.
  This build does not require PSRAM.
- Original full flash backed up locally before replacement: 16,777,216 bytes;
  SHA-256 `1a59d34d3b030b864cee5bc6c627cdafac338c3c41c3cfa443b615128b780f46`. Backup is ignored/private because it may contain old
  configuration or credentials. Do not upload it to the public repository.
- Final firmware.bin SHA-256: `c5817296ded2c5a2b1da970fa598ec6c384d0f0047d17b41a457ed91f853728d`. The binary contains device-local
  Wi-Fi/OTA credentials and is also excluded from Git. Rebuilding with another
  collaborator's credentials produces a different hash.
- Dependencies pinned in platformio.ini. Two 6 MB OTA application slots.

## Observed results

| Check | Result |
| --- | --- |
| BLE identity | Nordic UART Service, firmware 6.00, expected baseline UUID matched |
| Live values | About 76.2 V, ESC 24.2 C, 0 ERPM, 0 A, fault code 0 |
| Read cadence | Target 250 ms; fresh telemetry observed with GPS and LoRa running |
| Response interval sample | 49–144 ms, median 80 ms across 21 one-second status samples; not a comprehensive latency distribution |
| BLE receive overflow | 0 reported drops in final observed window |
| GPS serial | 9600 baud on MCU RX39, receive-only; 26,770 characters and 958 checksum-valid sentences at final snapshot |
| GPS positioning | No fix, zero satellites in final snapshot; outdoor validation required |
| LoRa transmitter | Radio initialized; 11 completed transmissions, 0 reported TX errors in final observed window |
| LoRa receiver/range | Not verified; Eamon is preparing the second board |
| Wi-Fi | Joined owner's provisioned LAN during deliberate maintenance |
| OTA installation | Authenticated wireless updates completed; final 0.1.3-bench version verified over USB after reboot |
| OTA rejection | Deliberately incorrect password rejected before firmware transfer |
| Post-update behavior | VESC identity reverified and telemetry resumed; OTA/TX off by default after boot |
| Final bench state | TX explicitly re-enabled for receiver testing; maintenance/Wi-Fi OTA closed |

Motor temperature is unavailable, never treated as a measured 0 C. Zero RPM
and no active fault only describe these stationary observations.

The initial bundled Arduino uploader failed to recognize completion even though
the new image booted. The repository now supplies a bounded, stream-aware
uploader; subsequent live updates returned explicit device success, and the
new boot/version was independently checked. The helper also has local tests
for split TCP success tokens and missing/ambiguous completion.

## Controller impact

Only VESC identity (0) and telemetry (4) queries were sent through BLE. No VESC
settings, profile, firmware, reset, throttle or braking commands were sent.
Heltec USB installation and OTA reset only the Heltec. The current implementation
has no generic VESC-write API. GET_VALUES retains its ordinary averaging-reset
semantics as documented for the baseline inspection.

## What this does not prove

- No reception confirmation, RSSI/SNR, range/coverage or eight-kart load result.
- No outdoor GPS fix or accuracy/map-alignment result.
- No physical throttle/brake response test through BLE loss, accessory power
  failure or OTA. Read-only reconnect succeeded, but vehicle reliability still
  requires the controlled bench/field acceptance checks.
- No independent kart accessory supply, USB backfeed or power-loss validation.
- No interrupted-OTA/power-failure recovery or automatic health rollback test.
- No active profiles, remote commands, geofence, dashboard or pit server.

## Checks and next handoff

13 Python tests passed (baseline decoder, radio contract and local OTA protocol),
as did the native C++ protocol checks with address/undefined-behavior sanitizers.
The final PlatformIO target built and was installed successfully.

Eamon should use the [exact radio settings, V4.3 pin notes and 47-byte frame](../firmware/heltec/README.md#radio-receiver-agreement-for-eamon),
then confirm actual received sequence numbers and decoded values. The transmitter
is uncommissioned kart ID 0; assign 1–8 only after agreeing which physical kart
has each ID. Radio TX must be explicitly enabled again after a future reboot.

All detailed USB logs, Wi-Fi addresses and credentials remain local under
`firmware/heltec/.local/`; this report intentionally records results without
publishing network credentials or GPS coordinates.
