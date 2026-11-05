# Base station serial contract (T-Beam S3 <-> pit host)

The T-Beam S3 Core ("the lily") is a dumb LoRa modem for the pit host (Pi 4 or a
laptop). It talks newline-delimited JSON over its native USB CDC port at any
baud (USB; 115200 nominal). One JSON object per line, no pretty printing.
Everything the host needs to know about a kart arrives in `tele` lines; the
host never has to decode LoRa bytes. Field names follow the agreed telemetry
fields in `docs/requirements.md` where one exists.

Version: `base-0.1.0`. Status: implemented in firmware, not yet exercised on hardware.

## T-Beam -> host

| `t` | When | Fields |
| --- | --- | --- |
| `tele` | each kart telemetry packet received (about 1 Hz per kart) | `kart` 1-8, `seq`, `lat`/`lon` (decimal degrees, `null` without fix), `fix` bool, `gps_age_s` (255 = never), `sats`, `gps_kph`, `heading`, `volts`, `motor_a`, `batt_a`, `erpm`, `duty` (0-1), `esc_c`, `fault` (0 = none), `mode_req` 0/1/2 or `null`, `mode_actual` 0/1/2 or `null`, `scale` (live VESC current max scale 0-1, `null` unknown), `stopped` bool, `vesc_link` bool, `mode_confirmed` bool, `pps` bool, `wifi` bool, `writes` bool (kart firmware allows VESC writes), `last_cmd_seq`, `cmd_result` (0 none, 1 accepted, 2 applied, 3 no_vesc, 4 rejected, 5 unknown), `ah`, `uptime_s`, `base_rssi` (dBm the kart hears us at), `rssi`, `snr`, `rx_ms` (base millis at receipt) |
| `base` | 1 Hz | `lat`, `lon`, `fix`, `sats`, `utc` (unix seconds or `null`), `pps` bool |
| `stat` | 1 Hz | `fw`, `radio` (`"sx1262"` or `"none"`), `rx`, `tx`, `rx_bad`, `uptime_s`, `batt_v`, `wifi` (IP string or `null`), `karts_online` (heard within 5 s) |
| `sent` | after a command was transmitted | `seq`, `cmd`, `kart`, `arg` |
| `log` | occasionally | `msg` |

Modes: 0 ECO, 1 NORMAL, 2 SPORT. `mode_actual` is what the kart read back from
the VESC; `mode_req` is what it was last told. They differ until the kart's
re-read confirms, and `mode_actual` is `null` after a VESC reboot until a
command is sent, because the kart never writes on its own (owner reliability rule).

Battery percent is a host-side derivation. The kart pack is 20S Li-ion: 60.0 V
empty, 84.0 V full. Use voltage under light load; do not trust it under heavy draw.

Wheel speed is a host-side derivation from `erpm` and the VESC's configured
drivetrain (14 poles, ratio 2.769, wheel 0.406 m from the 2026-11-05 capture):
`wheel_mph = erpm / 7 / 2.769 * (0.406 * pi) * 60 / 1609.34`. Label it
uncalibrated until the drivetrain numbers are physically verified.

## Host -> T-Beam

One JSON object per line:

```json
{"cmd":"mode","kart":3,"arg":1,"seq":17,"ttl":10}
{"cmd":"stop","kart":255,"seq":18,"ttl":10}
{"cmd":"start","kart":255,"seq":19,"ttl":10}
{"cmd":"ping","kart":1,"seq":20}
```

- `kart`: 1-8 or 255 for all karts.
- `seq`: 1-255, assigned by the host, rolling. The kart echoes it in
  `last_cmd_seq` with `cmd_result`. A kart treats a command as a retransmit when
  the source, seq and `issued_utc_s` all match the last command from that source
  (pit and USB are tracked separately), so retries are safe. Because the base
  stamps `issued_utc_s` from GPS time, a restarted host reusing a seq is not
  mistaken for a retransmit once the base has a fix; without GPS time the host
  should avoid reusing the seq a kart currently reports.
- `ttl`: seconds. Karts with GPS time reject a command older than this. 0 or
  absent = no expiry. The base stamps `issued_utc_s` from its own GPS.
- Delivery policy belongs to the host: retransmit every 1.5 s, up to 5 times,
  until every addressed kart that is online reports the seq, then read
  `cmd_result`. A fleet command has eight individual outcomes, never one flag.

Plain-text lines are also accepted for maintenance: `status`,
`wifi <ssid> <password>` (stored on the board for OTA, never echoed), `otapw <password>`,
`reboot`.

## Map package endpoint (host side, for the kart Pis later)

The pit host serves `GET /map/package.json` describing the current course map:
`{"version":n,"image":"/map/course.png","bounds":{"north":..,"south":..,"east":..,"west":..},"origin":{"lat":..,"lon":..},"course":[[lat,lon],...],"pit":{"lat":..,"lon":..}}`.
Kart displays fetch it when they are on the pit WiFi. Nothing map-related goes over LoRa.
