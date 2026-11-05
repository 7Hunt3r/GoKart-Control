# Heltec bench verification — November 5, 2026

Tested one owner-supplied HTIT-WB32LAF V4.3 connected by USB to the development
computer, with its LoRa antenna and L76K GPS attached. The powered VESC was the
same UUID/firmware-6.00 controller as the original hardware baseline. The kart
was stationary in the observed samples; no motor actuation test was performed.

## Initial 0.1.3 build and recovery

- Initial verified firmware: `0.1.3-bench` from `firmware/heltec`.
- ESP32-S3 rev 0.2, 16 MB flash, 2 MB embedded PSRAM identified with esptool.
  This build does not require PSRAM.
- Original full flash backed up locally before replacement: 16,777,216 bytes;
  SHA-256 `1a59d34d3b030b864cee5bc6c627cdafac338c3c41c3cfa443b615128b780f46`. Backup is ignored/private because it may contain old
  configuration or credentials. Do not upload it to the public repository.
- 0.1.3 firmware.bin SHA-256: `c5817296ded2c5a2b1da970fa598ec6c384d0f0047d17b41a457ed91f853728d`. The binary contains device-local
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

The follow-up below supersedes the initial packet format and uncommissioned ID.
Radio TX must be explicitly enabled again after a future reboot.

All detailed USB logs, Wi-Fi addresses and credentials remain local under
`firmware/heltec/.local/`; this report intentionally records results without
publishing network credentials or GPS coordinates.

## Receiver-compatible follow-up: 0.2.0-bench

The owner assigned **kart ID 1** and chose Eamon’s receiver contract. The shared
GoKartProtocol library is byte-identical to his PR #1 at `dfe83d8`. The new image
uses its 46-byte telemetry packet, 915 MHz, BW250/SF7/CR4/5, sync 0x12 and 1 Hz
reporting with ±50 ms jitter. Modes/scale remain unknown; writes and PPS flags
are clear. This supersedes the old 47-byte/BW500 image.

- Built successfully and passed 13 Python tests, including bytes generated by
  the actual shared C++ header; native VESC parser checks also passed under ASan/UBSan.
- Saved ID 1, then installed 0.2.0 through authenticated OTA. Two initial OTA
  invitations timed out before authentication; reopening maintenance/Wi-Fi
  allowed the next upload to complete. Root cause has not been established.
  Completion was explicitly acknowledged and the new version checked over USB.
- ID 1 persisted across OTA reboot; fresh baseline VESC identity/readout resumed.
- At the final 45-second snapshot: 143 samples, 76.1 V, ESC 24 C, zero ERPM/current,
  fault 0, RX drops 0, response interval 49 ms for that sample.
- GPS: 15,374 characters, 446 valid sentences; no fix, zero satellites indoors.
- Radio: 22 completed transmissions, zero reported TX errors. USB status confirms
  payload length 46 and bandwidth 250 kHz. **No receiver reception is claimed.**
- TX left enabled for receiver testing; maintenance/Wi-Fi closed. No motor or
  configuration commands were sent to the VESC.
- Installed image SHA-256: `a150ced3be5472d125ab460255447c77222f9544403389bc13afbf02196353f3`.

Next: Eamon confirms kart-1 `tele` packets, increasing sequence numbers, voltage
and VESC validity on the T-Beam. All no-fix/stale values must remain unavailable
in the UI. See the [receiver agreement](../firmware/heltec/README.md#radio-receiver-agreement-for-eamon)
for timing, flags and unimplemented command/PPS behavior.

## Latest receiver reconciliation: 0.2.1-bench

While the isolated motor diagnostic was being developed, Eamon moved his base
to BW500 and added support for the old 47-byte format (`e83ad69`, present in
`ab9404a`). The final transmitter follows that current shared header unchanged:
**46-byte GoKartProtocol, 915 MHz, BW500, kart ID 1**. Do not independently switch
either endpoint back to BW250. His receiver's legacy decoder is retained in the
shared library; this transmitter still sends only the shared 46-byte packet.

The 0.2.1-bench image was built, installed via USB and independently checked:
fresh VESC identity/telemetry, zero ERPM/current/fault, ID 1 retained, 46-byte
payload/BW500 reported, 15 TX completions and zero TX errors. TX is enabled;
maintenance is off; VESC writes remain disabled. No cart reset was needed.
Actual receiver reception remains unconfirmed. OTA was exercised on earlier
versions; this final bandwidth-only firmware change was installed by USB.
Image SHA-256: `0c45b7b63debf40243f407fa4380a7ab3b8e4526938072eb174faf3b5349e263`.

The separate [motor diagnostic draft](https://github.com/colinfost3r/GoKart-Control/pull/3)
failed live freshness/preflight validation. It sent zero-current handoff requests
but no positive-current pulse. The requested five-second wheel spin is not
completed. That diagnostic is not the firmware left on the board.


### Later integration status

The observations above describe the telemetry image and earlier diagnostic
trials. Later direct-laptop BLE tests produced a half-second 5 A forward movement;
longer attempts aborted. Basic telemetry was restored. See the complete
[updated motor report](bench-control-test.md), [handoff](../HANDOFF.md) and
[shared integration guide](integration.md). Eamon reports reception of his kart 2;
receipt of this kart 1 remains independently unconfirmed.
