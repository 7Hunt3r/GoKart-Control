# M5 display UART v1

Local connection only: 230400 baud, 8N1, 3.3 V, newline-delimited ASCII. This adds no LoRa
messages and changes no USB contract. Enabled by `GK_M5_UART=1` in the Heltec BLE build.

Heltec -> M5:
- `self` / `fleet`: unchanged output of `GoKartProtocol::gkTelemetryToJson`; `self` retains its
  USB extras. M5 ignores unknown additional fields. A zero sensor reading without the relevant
  `vesc_link` / `fix` flag is unavailable. Own data expires after 1.5 s; valid GPS after 3 s of reported fix age;
  map ghosts expire after 15 s of reported fix age. Receipt does not reset the GPS age clock.
  Own `fix_age_ms` takes precedence when present; otherwise `gps_age_s` has one-second
  quantization, so exact physical fix age can be up to one second greater than reported. The base's RSSI zero sentinel is not used to infer connectivity:
  metadata's `pit` uses the actual time of the last beacon.
- `display`: emitted after every own report (2 Hz / 500 ms), and immediately when a token
  rotates because of expiry, readiness or VESC link-session changes. Format below. Metadata
  expires after 700 ms. Display redraw is 5 Hz; that does not change the source report rate.

```json
{"t":"display","v":1,"kart":1,"boot":1234,"token":5678,"mode_ready":true,
 "request":0,"result":0,"radio":true,"pit":true,"base_rssi":-70,"rate":2,
 "speed_cal":2.48,"drops":0,
 "modes":[{"mph":15,"scale":0.5},{"mph":25,"scale":0.75},{"mph":0,"scale":1.0}]}
```

Values above are illustrative. `modes` and `speed_cal` are read from the Heltec at runtime;
M5 performs no wheel-RPM conversion and has no duplicated calibration. `mph:0` means uncapped
within the VESC's own limits. `boot` is a nonzero random 32-bit boot identifier. `token` is a
nonzero random, single-use token replaced at least once per second; zero means unavailable.
Readiness/VESC link-session changes also invalidate tokens and publish updated metadata.
A frame already in flight at expiry, or lost to UART backpressure, can still time out as unknown;
it is not automatically retried. Expiry never changes driving state.

`request` is the last recognized M5 request ID; `result` uses existing GoKartProtocol codes:
0 none, 1 accepted/pending, 2 VESC acknowledged and read-back verified, 3 no VESC, 4 rejected,
5 unknown. A reused command-history slot becomes unknown, never falsely applied. M5 only
matches its own pending request. Actual selection highlight comes from `self.mode_actual`
and `self.mode_confirmed`, not a touch or an acceptance response.

M5 -> Heltec (the only supported request):

```text
m5mode TTTTTTTT IIIIIIII M CCCC\n
```

Exactly 31 characters before newline: eight hexadecimal digits for token, eight for the
nonzero request ID, a single mode digit 0..2, and four hexadecimal CRC digits. Spaces are fixed.
CRC is CRC16/XMODEM (polynomial 0x1021, initial 0), over the first 26 bytes, excluding the space
before CRC. The shared `M5DisplayLink.h` encoder/decoder is authoritative. Other console text
is ignored. A partial line with an inter-byte gap >200 ms or an overflowing line is discarded
through its next newline. Failed CRC/expired/replayed tokens execute nothing; the display times
out to unknown rather than resending. The freshness token is not a security credential.

After validation the request uses source **0xFE** and the shared local sequence counter, exactly
like the Pi's USB mode request. Thus driver requests cannot clear pit SLOW. While STOP/GRID,
busy writing, OTA, baseline suspect, missing UUID pin or controller unavailable, the adapter
rejects the request and does not remember a future action. The VESC write path and radio command
acknowledgements remain Eamon's implementation. USB and M5 are alternatives for normal driver
operation; simultaneous USB console inspection remains supported.
