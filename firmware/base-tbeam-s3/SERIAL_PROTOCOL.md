# Base station serial contract (T-Beam S3 <-> pit host)

The T-Beam S3 Core ("the lily") is a dumb LoRa modem for the pit host (Pi 4 or a
laptop). It talks newline-delimited JSON over its native USB CDC port at any
baud (USB; 115200 nominal). One JSON object per line, no pretty printing.
Everything the host needs to know about a kart arrives in `tele` lines; the
host never has to decode LoRa bytes. Field names follow the agreed telemetry
fields in `docs/requirements.md` where one exists.

Version: `base-0.1.0`. Status: running on the T-Beam S3; host link, command transmit, kart reception,
beacon and repeater relay exercised on the bench (2026-11-05/06).

## T-Beam -> host

| `t` | When | Fields |
| --- | --- | --- |
| `tele` | each kart telemetry packet received (2 Hz per kart by default, 1 Hz with `rate 1`; a repeater copy arrives as a second line with `relayed` true) | `kart` 1-8, `seq`, `lat`/`lon` (decimal degrees, `null` without fix), `fix` bool, `gps_age_s` (255 = never), `sats`, `gps_kph`, `heading`, `volts`, `motor_a`, `batt_a`, `erpm`, `duty` (0-1), `esc_c`, `fault` (0 = none), `mode_req` 0/1/2 or `null`, `mode_actual` 0/1/2 or `null`, `scale` (live VESC current max scale 0-1, `null` unknown), `stopped` bool, `vesc_link` bool, `mode_confirmed` bool, `pps` bool, `wifi` bool, `writes` bool (kart firmware allows VESC writes), `last_cmd_seq`, `cmd_result` (0 none, 1 accepted, 2 applied, 3 no_vesc, 4 rejected, 5 unknown), `ah`, `uptime_s`, `base_rssi` (dBm the kart hears us at), `rssi`, `snr`, `rx_ms` (base millis at receipt), `slow_mph` (SLOW cap in mph, 0 = off; firmware 2026-11-06+), `session` (0 or the host kart of the freeplay game this kart is in), `atk_to` (0, a kart id, or 255 = everyone in its session: an attack in flight), `atk_item` (1 shell, 2 lightning), `atk_seq`, `ack_from`/`ack_seq` (the hit this kart last accepted, held 5 s), `score` (hits landed this session) — all 0 from firmware before kart-0.2.5 (47-byte packets); docs/freeplay-multiplayer.md, `relayed` bool (packet came through a LoRa repeater; the pit drops a relayed copy of a packet it already heard directly) |
| `tele` with `"bench":true` | each frame from Colin's bench Heltec (firmware/heltec, 47-byte `GK` frame, every 2 s) | Same field names as above where the frame carries them; `mode_*`, `scale`, `duty`, `ah` are `null`, `stopped`/`writes`/`pps` are `false`. Extra: `boot_id`, `hdop`, `sample_age_ms`. `kart` is 0 until that board is commissioned with `id N`. |
| `base` | 1 Hz | `lat`, `lon`, `fix`, `sats`, `utc` (unix seconds or `null`), `pps` bool |
| `repeater` | every 5 s per repeater while it hears the pit beacon | `id` (1 = first repeater), `seq`, `heard`, `relayed` (its counters, 16-bit wrap), `batt_v` (0 = USB only), `pit_rssi` (how the repeater hears the pit), `rssi`/`snr` (how the pit hears the repeater), `uptime_min`, `karts` (ids it heard in the last 10 s) |
| `stat` | 1 Hz | `fw`, `radio` (`"sx1262"` or `"none"`), `rx`, `tx`, `rx_bad`, `uptime_s`, `batt_v`, `wifi` (IP string or `null`), `karts_online` (heard within 5 s), `bench_rx`, `bench_age_s` (`null` until a bench frame is heard), `beacon_tx`, `sync` (`pps` = own GPS pulse, `karts` = derived from karts that report a PPS lock, `free`), `relay_rx` (packets heard via a repeater), `beacon_skipped` (beacon not sent: a command went out < 150 ms earlier), `cmd_q` (commands waiting), `cmd_dropped` (queue full), `cmd_forced` (sent after 600 ms without a clear spot in the slot plan), `tx_dbm` (the base's SX1262 output setting; 22 since 0.2.1), `heap` |
| `status` | reply to the text command `status` | `fw`, `radio`, `radio_status`, `pmu`, `gps_locked`, `gps_cfg`, `pps_count`, `wifi`, `ssid`, `heap` |
| `sent` | after a command was transmitted | `seq`, `cmd`, `kart`, `arg`, `ok` (radio accepted it), `wait_ms` (time it waited in the base's queue) |
| `log` | occasionally | `msg` |

Modes: 0 ECO, 1 NORMAL, 2 SPORT. Each is a top-speed cap plus a motor-current scale
(kart firmware `config.h MODES[]`, 0.2.2+: ECO 15 mph / 0.50, NORMAL 25 mph / 0.75, SPORT
uncapped / 1.0). `mode_actual` is the mode whose scale AND cap the kart read back from
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

One JSON object per line. The pit (pit/server/link.py) writes each line preceded by a bare
newline and paces them at least 20 ms apart: on 2026-11-06 a 470-byte burst (STOP RACE = eight
finish commands + the race line in 7 ms) lost its tail in the base's USB receive buffer while the
base was transmitting, and the leftover fragment turned the next line into `bad json`. The base
ignores empty lines, so the leading newline also terminates any such fragment.

```json
{"cmd":"mode","kart":3,"arg":1,"seq":17,"ttl":10,"issued":1794016260}
{"cmd":"stop","kart":255,"seq":18,"ttl":10}
{"cmd":"start","kart":255,"seq":19,"ttl":10}
{"cmd":"ping","kart":1,"seq":20}
{"cmd":"slow","kart":2,"arg":5,"seq":21,"ttl":10}
{"cmd":"boost","kart":3,"arg":100,"seq":22,"ttl":10}
{"cmd":"race","kart":255,"arg":5,"seq":23,"ttl":10}
{"cmd":"rate","kart":255,"arg":1,"seq":24,"ttl":10}
{"t":"race","state":2,"pos":[2,1,0,3,0,0,0,0]}
```

| `cmd` | `arg` | Effect on the kart |
| --- | --- | --- |
| `mode` | 0 ECO, 1 NORMAL, 2 SPORT | Writes the mode's current scale and top-speed ERPM cap to the VESC (both confirmed by re-read); `rejected` if the controller has no drivetrain numbers for the cap |
| `stop` | - | Ramps to stop and holds until START; cancels BOOST (SLOW stays) |
| `start` | - | Releases STOP; clears SLOW and BOOST |
| `ping` | - | Echo only (`cmd_result` applied), no VESC write |
| `slow` | mph 1-60 (0/absent = 5) | ERPM cap at that speed until START (firmware 2026-11-06+) |
| `race` | 1-30 = grid hold now, GO `arg` s after `issued_utc_s`; 0 or 101-108 = finish (position `arg`-100) | See pit/server/map/ZONES.md "Race start" / "Race finish". The kart prints `{"t":"race",...}` events on USB |
| `boost` | scale percent 10-100 | Timed current-scale override for 5 s, then auto-reverts to the mode scale; never above 1.0; rejected while stopped; STOP/START cancel it (firmware 2026-11-06+). A scale above the mode's also lifts the mode's speed cap one mode up for the same time (0.2.2+). The USB form `boost SECS SCALE` on the kart sets other durations (game zones) |
| `rate` | 1 or 2 (Hz) | Telemetry report rate, saved on the kart; no VESC involvement (firmware 2026-11-07+). The pit sends `rate 1` to all karts while a repeater heartbeat is heard (now, then every 30 s) and `rate 2` once after 120 s without one: a 50 ms slot cannot hold a 23 ms packet and its 23 ms repeater copy |
| (`"t":"race"`) | `state` 0-3, `pos[8]` | Not a command (no seq, no echo, no reply): race state + running positions carried in the base's beacons for 30 s, see "Race positions" below |

- `kart`: 1-8 or 255 for all karts.
- `seq`: 1-255, assigned by the host, rolling. The kart echoes it in
  `last_cmd_seq` with `cmd_result` (radio telemetry carries pit commands only; the
  kart's own console/kart-Pi commands never appear there). A kart treats a command as
  a retransmit when seq, cmd, arg and `issued_utc_s` match one of the last 8 commands
  from the pit within 30 s, and then reports that command's result again without
  re-applying it; so a retry or a late repeater copy of an older command (e.g. a START
  after a newer STOP) cannot act twice. The base keeps the first `issued_utc_s` for a
  retry of the same seq/cmd/kart within 60 s. Without GPS time at the base
  `issued_utc_s` is 0, TTLs are not enforced, and the host should avoid reusing a seq
  a kart currently reports (pitcore does).
- Validation at the base: `kart` 1-8/255, `seq` 1-255, `mode` arg 0-2, `slow` 0-60,
  `boost` 10-100, `race` 0-30 or 100-108, any arg 0-255, `ttl` 0-65535; anything else
  is logged and not sent. A host line over 299 bytes is dropped (logged), never parsed truncated.
- Placement: STOP goes to the front of the base's queue and removes queued non-STOP
  commands for the same kart. With a time reference (`sync` `pps` or `karts`) a command
  waits for a spot where no online kart (at its observed 1 or 2 Hz) and no beacon is on
  the air, at most 600 ms; without one it goes out at once. Commands are spaced 50 ms
  (room for a repeater copy).
- `ttl`: seconds. Karts with GPS or beacon time reject a command older than this
  (`cmd_result` 4). 0 or absent = no expiry. The base stamps `issued_utc_s` from its own GPS.
- `issued` (optional, unix seconds): used by the base as `issued_utc_s` when it has no GPS time
  (indoors), so TTL and race GO still work. The pit always sends it, with the same value on every
  retry of a command (the time of its first transmission).
- Delivery policy belongs to the host: retransmit every 1.5 s, up to 5 times,
  until every addressed kart that is online reports the seq, then read
  `cmd_result`. A fleet command has eight individual outcomes, never one flag.

Plain-text lines are also accepted for maintenance: `status`, `help`,
`wifi <ssid> <password>` (stored on the board for OTA, never echoed), `otapw <password>`,
`reboot`.

### Race positions (host -> T-Beam, added 2026-11-06)

`{"t":"race","state":S,"pos":[p1,...,p8]}` — S: 0 idle, 1 countdown, 2 running, 3 finished; p = running position
1-8 (0 = none). The base puts them into every beacon for the next 30 s (send at least every 10 s while racing).
Each kart prints `{"t":"race","kart":N,"event":"position","position":P,"state":S}` when its own value changes
and carries `race_pos`/`race_state` on its USB `self` line, so the kart display can show "P3" with no Wi-Fi.

Pit host schedule (pit/server/pit_server.py `_race_tick`, revised 2026-11-07): state 1 with all positions 0 the
moment START RACE is pressed, then every 2 s while the race runs (state 1 until GO, then 2, positions from the live
leaderboard: finished karts keep their finishing place, the rest by completed laps desc then earliest last
crossing, no completed lap = 0). On STOP RACE the state-3 line with the final positions goes out FIRST, then one
finish command (`race` 100 + position) per kart heard in the last 15 s (karts not heard get none: every command
costs a beacon slot at the base); state 3 is repeated at 3 s and 6 s. STOP RACE before GO is ABORT START: STOP to
all karts (a finish would release the grid hold at once), no finish commands, then state 0. NEW RACE / reset laps /
abort send state 0 twice (now and 2 s later). Every line is in the session log as `tx`; `race_pos` events record
each change once.

Kart 2 kept showing state 2 for 4+ s after STOP RACE on 2026-11-06 (log session-20261106-204606): the old order
(eight finish commands, seven of them to karts never heard, then the race line, in one burst) lost the race line in
the base's USB buffer, the 3 s repeat arrived glued to the leftover fragment (`bad json`), and the operator pressed
NEW RACE before the 6 s repeat. Pit-side fix as above; the kart side is not involved.

## Map package endpoint (host side, for the kart Pis later)

The pit host serves `GET /map/package.json` describing the current course map:
`{"version":n,"image":"/map/course.png","bounds":{"north":..,"south":..,"east":..,"west":..},"origin":{"lat":..,"lon":..},"course":[[lat,lon],...],"pit":{"lat":..,"lon":..}}`.
Kart displays fetch it when they are on the pit WiFi. Nothing map-related goes over LoRa.
