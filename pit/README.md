# Pit station (Pi 4): server, touch kiosk, simulator, SD card kit

The pit side of GoKart Control: a Python service that owns the T-Beam S3 LoRa
modem over USB serial, keeps the authoritative fleet/race/command state, logs
the session, and serves a shared web UI to the Pi's own touchscreen (kiosk)
and to password-authenticated browsers on the LAN.

Contract with the modem: [`firmware/base-tbeam-s3/SERIAL_PROTOCOL.md`](../firmware/base-tbeam-s3/SERIAL_PROTOCOL.md)
(base-0.1.0). Telemetry conventions: [`docs/requirements.md`](../docs/requirements.md#agreed-telemetry-fields).

## Status: what is verified and what is not

| Item | Status |
| --- | --- |
| Server + simulator command loop (TCP and pty), outcome table, SSE stream, login, logs | **Tested on the Mac** (macOS, Python 3.13, pyserial 3.5) with `pit/tests/` (70 unit + 7 end-to-end) and manual runs |
| Pit Pi 4: first boot from `sdcard/`, apt packages, NetworkManager keyfiles, `install.sh` | **Running since 2026-11-05** (gokart-pit, 192.168.151.240 on McLab). `install.sh` re-run twice on 2026-11-06: second run changed nothing (keyfiles untouched, no warnings) |
| Real T-Beam S3 serial link, udev `/dev/tbeam` rule | **Verified 2026-11-05** (`/dev/tbeam -> ttyACM0`, kart 2 heard at 1-2 Hz, commands applied). Unplug/replug reconnect: the reconnect loop is exercised against the simulator (TCP drop + return); a physical replug on the Pi has not been tried |
| cage + Chromium kiosk on Raspberry Pi OS Trixie Lite, HDMI 1024x600 (HDMI-A-1), touch | **Running since 2026-11-05.** Layout checked in headless Chrome at 1024x600 for every state (2026-11-07 pass); touch on the real panel used by Eamon |
| Kiosk liveness check (`gokart-check.timer`) | **Fixed 2026-11-06 21:35**: it looked for Chromium in the kiosk unit's cgroup, but Chromium moves itself to a user scope, so it restarted a healthy kiosk every ~2 min (20:47-21:30). Now it walks cage's process tree; no restarts in the following 150 s |
| Panel speakers (ALSA `vc4hdmi0`) | `/etc/asound.conf` written by install.sh on the Pi; sound itself not confirmed by ear |
| Python 3.11 compatibility (Trixie ships 3.13) | Not run on 3.11; code avoids 3.12+ features |
| Drivetrain numbers behind wheel mph, battery % curve | Contract values; **uncalibrated**, labelled in the UI |

Simulated karts are not real karts: whenever the source is the simulator the UI
shows a striped SIMULATION banner (driven by the simulator's own
`{"t":"log","msg":"SIMULATION"}` and `stat.fw = "sim-..."`).

## Layout

```text
pit/
  server/pit_server.py     HTTP server, SSE, auth, session log, wiring (stdlib + pyserial)
  server/health.py         Pi power/thermal health (vcgencmd get_throttled + CPU temp)
  server/pitcore.py        fleet state, staleness, derived values, command delivery (pure logic)
  server/link.py           T-Beam link: serial auto-find + reconnect, or TCP to the simulator
  server/static/           index.html (operator), kiosk.html (1024x600 touch), app.js, watch.js, style.css, login.html
  tools/                   wifi_profiles.py, journal_persist.sh, gokart_check.sh, why_rebooted.sh
  server/map/              map.json + README (how to add CT ECO imagery; none bundled)
  server/config.example.json   copy to config.json (gitignored): shared password etc.
  sim/sim_tbeam.py         simulated T-Beam (pty or TCP), eight karts, honours commands
  tests/                   test_core.py (unit, ~1 s), test_e2e.py (simulator + server processes, ~95 s)
  install.sh               runs ON THE PI; idempotent install/update
  deploy.sh                Mac -> Pi rsync + restart
  systemd/, kiosk/, udev/  units, PAM file + Chromium launcher, /dev/tbeam rule
  sdcard/                  SD card flashing kit (see sdcard/README.md)
  logs/                    session logs (gitignored)
```

## Run it locally (Mac or Linux)

```sh
# terminal 1: simulated T-Beam over a pseudo-terminal; prints e.g. /dev/ttys004
python3 pit/sim/sim_tbeam.py
# terminal 2
python3 pit/server/pit_server.py --port /dev/ttys004
open http://127.0.0.1:8080/        # operator page
open http://127.0.0.1:8080/kiosk   # touch layout (best in a 1024x600 window)
```

TCP instead of a pty: `python3 pit/sim/sim_tbeam.py --tcp 7878` and
`python3 pit/server/pit_server.py --tcp 127.0.0.1:7878`. Simulator scenario
flags (`--fault 5 --nofix 6 --flaky 7 --offline 8 --novesc 0 --loss 0.1 --repeater`) are
described in `sim_tbeam.py --help`; 0 disables one. With a real T-Beam, run the
server with no arguments: it tries `/dev/tbeam`, a USB 303a:1001 match,
`/dev/serial/by-id/*`, `/dev/ttyACM*`, then `/dev/cu.usbmodem*`.

Requests from 127.0.0.1 skip the login. Other browsers need the shared
password in `pit/server/config.json` (copy `config.example.json`); with no
password set, remote browsers are refused.

Tests (no hardware needed):

```sh
python3 -I pit/tests/test_core.py
python3 -I pit/tests/test_e2e.py
```

## Behaviour (implemented here; proposed until reviewed)

- **Fleet state.** A kart is `online` if a `tele` arrived within 5 s, `stale`
  within 15 s, else `offline`, or `never` heard. Link freshness, the VESC group
  (updated only from samples with `vesc_link: true`) and the GPS group (only
  from samples with a valid fix) each keep their own age, so a live radio link
  never makes old VESC data look current. Missing values stay `null`; a
  missing fault is shown as "fault unknown", never as "no fault"; a stale
  `fault: 0` is shown as "no fault (stale)". Positions are never moved to 0,0.
- **Derived values.** Battery % = linear 60.0 V (0 %) to 84.0 V (100 %),
  clamped, flagged "under load" when |batt_a| > 20 A. Wheel mph uses the
  contract formula and is labelled uncalibrated. GPS mph = gps_kph x 0.621371.
- **Telemetry hygiene.** A packet with the same seq as the last one, or up to 16 behind it, within 3 s
  and from the same kart boot, is a late copy (repeater copy, or a repeater copy delivered after the
  next direct packet) and is dropped (`relay_dupes` / `late_dupes` per kart); applying it would move the
  kart back along its path. A kart whose `uptime_s` goes backwards rebooted: its packets are accepted,
  `reboots` counts it and the operator card shows REBOOTED. Seq wrap (255 -> 0) is a normal gap.
  Map trails are the last 30 s, cut at any gap over 3 s (no straight lines across the course).
- **Commands.** `mode` (0-2), `stop`, `start`, `ping`, `slow` (mph 1-60, default 5), `boost` (percent
  10-100, default 100), `race` (1-30 = GO in s, 0 / 101-108 = finish), `rate` (1 or 2 Hz). `kart` 1-8 or
  255; true/false is refused for `kart` and `arg`. Seq 1-255 rolling, persisted in `logs/cmdseq.json`
  across restarts, never a seq that any kart currently reports as `last_cmd_seq` or that is in flight.
  Every line carries `ttl` (config `cmd_ttl_s`, default 10; not on `ping`) and `issued` (unix seconds of
  the first send, the same on every retry; the base uses it without GPS time).
  Retransmit every 1.5 s, up to 5 retransmits (6 sends), until every addressed kart that is online
  echoes the seq; per-kart outcomes come from `cmd_result`. Late echoes still update the table.
  Commands are serialized per kart (a queued command waits while the kart owes an echo, or up to 4 s
  while it reports `accepted`). STOP never queues: it cancels queued commands for its karts and marks
  in-flight ones superseded; retransmits of an older fleet command then go out addressed per kart so
  they cannot re-trigger a kart that has since handled the STOP. If the serial link drops, in-flight
  commands become `link_lost` and are never replayed after reconnect (requirements: no backlog execution).
- **Outcomes, in the same plain words on every page** (API names in brackets): waiting (`queued`),
  sending (`pending`), heard / working (`received` / `accepted`), done (`applied`), no VESC (`no_vesc`),
  refused (`rejected`; "rejected: send a mode first" for a START to a kart with no requested mode, which is
  what a Heltec that rebooted during STOP answers), not confirmed (`unknown`), no answer (`no_ack`, retries
  exhausted), kart offline (`offline`, not heard when sent), replaced (`superseded`), cancelled (`cancelled`,
  dropped from the queue by a STOP), radio lost (`link_lost`). The CMDS tab / outcome panel explains each and
  shows how long the last command waited in the base's queue (`sent.wait_ms`).
- **Fleet report rate.** A 50 ms slot cannot hold a packet and its repeater copy, so while a repeater
  heartbeat has been heard in the last 60 s the pit sends `rate 1` to all karts at once and every 30 s;
  after 120 s with no repeater (also counted from server start) it sends `rate 2` once. These are policy
  commands: they never delay an operator's command (an operator command to the same kart supersedes them),
  never become a card's "last command" or the "last fleet command", and only the newest shows in the list.
  The repeater chip shows the rate (`rep -3 dBm · 1 Hz`); `/api/state` `fleet.rate`.
- **SCALE ≠ MODE.** When a kart's live `scale` differs from its requested mode's scale (ECO 80/140,
  NORMAL 100/140, SPORT 1.0) by more than 0.05 for 10 s while online with fresh VESC data, not stopped,
  not in a race grid hold and not boosted by the pit in the last 7 s, the card shows an amber
  "SCALE ≠ MODE, tap to resend" badge (kiosk: SCALE≠MODE). (The kart's own `revert_missed` flag is on
  its USB `status` line only; the telemetry packet has no spare flag bit for it.)
  tapping it sends that kart's mode again. Start and end are logged (`scale_mismatch`). A kart parked in a
  mud zone for 10 s would also trip it (the pit does not evaluate zones per kart).
- **Race (RACE ON / OFF).** Entered karts and race mode are shared server state. RACE ON sends
  the selected mode then START to entered karts and STOP to the others (fleet
  commands when all eight are entered). RACE OFF only clears the flag. The
  online/entered filters are per-browser presentation only.
- **Logs.** `logs/session-YYYYmmdd-HHMMSS.jsonl`, one file per server start:
  every serial line received (`rx`) and sent (`tx`, race lines included), operator actions (`op`,
  with operator name and IP), command lifecycle and link events. A file that reaches
  `log_max_file_mb` (64) continues in `...-part2.jsonl` etc.; the oldest session files are deleted when
  the folder passes `log_max_total_mb` (1024), never the current one (`logs_pruned` event). The pit wrote
  ~10 MB/hour with one kart at 2 Hz on 2026-11-06. Download the current file from the operator page or
  `GET /api/log/download`. The LOG view also shows the T-Beam's stat line (beacons skipped, command queue,
  dropped, forced, heap) and the pit's line counts.
- **HTTP.** `/` operator UI, `/kiosk`, `/m` (phone; also `/mobile`), `/login`, `/logout`, `/events` (SSE, ~4 Hz;
  at most `sse_max_clients` (24) pages at once, more get 503 and retry; a page that stops reading for 10 s is
  dropped; frames carry the zones document when its version changes and every 30 s, otherwise only
  `zones_version`), `GET /api/whoami`, `GET /track/recording`, `POST /gps_correction {"enabled":bool}`,
  `GET /api/state`, `GET /api/log/recent?n=100[&lines=1]`, `POST /cmd`
  `{"cmd":"stop|start|mode|ping|slow|boost|race|rate","kart":1-8|255,"arg":..}` (mode 0-2, slow mph 1-60, boost percent 10-100, rate 1-2), `POST /api/race`
  `{"entered":[...],"mode":0-2,"active":true|false}`, `POST /zones`, `POST /zones/enable`,
  `POST /laps/reset`, `POST /race/start` `{"secs":1-30,"laps":0-99}`, `POST /race/stop` (see "Game zones and laps"), `GET /map/package.json`, `GET /map/zones.json`,
  `/map/<file>`, `/healthz`. `/map/*` and `/healthz` need no login (kart
  displays fetch the map on the pit Wi-Fi).

## Game zones and laps

The "Mario Kart" layer. Contract: [`server/map/ZONES.md`](server/map/ZONES.md). Status: **tested
on the Mac against the simulator** (unit + e2e tests, headless Chrome screenshots of all three
pages, the editor driven with real mouse events). Not yet tried with real karts.

- **Zones file.** `server/map/zones.json` (next to `map.json`): `version`, `enabled` (master
  switch), `zones` (`boost` / `mud` / `item` circles or polygons, one `lapline`) and an optional
  `track` route (`points`, `closed`, `width_m`). Served without login
  at `GET /map/zones.json`; `GET /map/package.json` carries `"zones": "/map/zones.json"` and
  `"zones_version"` so kart Pis re-fetch on change. A hand edit on the Pi is picked up on the next
  request (mtime check); an invalid file is logged (`zones_invalid`) and ignored.
- **Editing (operator page `/`).** `ZONES` above the map enters edit mode (grouped toolbar: PLACE /
  COURSE / EDIT, the active tool highlighted with its colour, a one-line hint for it, the name and
  radius only when a zone is selected, the track buttons only with the TRACK tool; Fit includes the
  zones and the track): `BOOST PAD` / `MUD` /
  `ITEM BOX` then tap the map (5 m / 6 m / 4 m circle), `TRACK` then tap the course centreline in
  driving order (`UNDO POINT`, `CLOSE LOOP`, `CLEAR`), `LAP LINE` then tap both ends (replaces the old line; one
  per course), `SELECT / MOVE` to pick and drag a zone (drag empty map still pans), `DELETE` then
  tap a zone. The selected zone gets a name field and a radius slider (3-60 m). `SAVE` posts the
  whole document; `CANCEL` drops the draft. Saving warns if someone else saved meanwhile.
  Polygons are not drawn by the editor but are rendered, moved and deleted if present in the file.
- **API (login like `/cmd`, logged as `op` with who).** `POST /zones` `{"zones":[...]}` replaces
  the zone list (`enabled` kept unless given; `track` kept if absent, removed if `null`) after
  validation: types boost/mud/item/lapline, circle `r_m` 3-500, polygon 3-64 points, lap line >= 4 m, unique ids (1-16 of `A-Za-z0-9_-`), at most
  one lap line; `scale` clamped 0.1-1.0, boost `secs` 1-30, `cooldown_s` 0-600 (default 20 boost,
  15 item), `min_lap_s` 5-600; track 2-200 points (3+ if closed), `width_m` 2-20.
  Refusals are HTTP 409 with the reason. Unknown zone types are kept as-is (karts ignore them,
  the pit draws them grey). `POST /zones/enable {"enabled":true|false}` flips the master switch.
  Both bump `version`. `POST /laps/reset` clears lap state.
- **Chips and badges.** Every top-bar chip has one height and font and is coloured only by state;
  GAME is a toggle (ON = amber with a solid dot, OFF = outlined). Card badges (STOPPED, FAULT, GRID,
  GO, FINISHED, SLOW, lap) share one size. Kart cards show the wheel speed with a small "wheel" tag
  (uncalibrated: see the detail sheet / operator note). Every tap shrinks a button to 97 % for 80 ms;
  hold buttons fill a ring.
- **GAME ON/OFF.** Chip in the top bar of all three pages; on the kiosk it is the race
  director's kill for the whole game. Off = zones drawn faded, no lap timing, karts ignore zones.
- **Rendering.** Every pit map draws, from the same map code: boost pads as an amber chevron
  panel inside a faint trigger circle (readable at 5 m), mud (brown), item boxes (blue/white "?"
  box), the lap line (checkered, flag at end a) and the track route (thin dashed line with
  direction chevrons about every 15 m). All faded when the game is off.
- **Item boxes.** The kart rolls the item on entry (boost / super / mud / empty, ZONES.md); the
  pit only stores and draws them and never evaluates them.
- **Race start.** `START RACE` (hold 1 s) with a 3 / 5 / 10 s chooser, in the race panel on the
  operator page, the kiosk RACE tab and the phone. `POST /race/start {"secs":5}` (login, 1-30)
  resets all lap timing, sets `race.started_wall`, `race.go_wall` and sends ONE `race` command
  (arg = secs) to all karts on the usual broadcast path with retries; logged as `race_start`.
  `/api/state` carries `fleet.race.go_in_s` (seconds to GO on the server clock, negative after) so
  every page runs the same countdown: a full-width strip of five 90 px lights (kiosk: over the top
  of the card area; operator: above the cards; phone: top of the scroll area), one more red light
  each second like the karts, "GO in N", green at GO for 2 s, and a green "GO!" flash over the
  whole page. Sounds: a soft tick per second of the hold, a beep on the last three lights, a long
  higher tone at GO, a low double tone when you STOP ALL (`static/sound.js`, shared with the karts,
  quieter "pit" profile). The SOUND chip in the top bar mutes (per browser); it reads TAP FOR SOUND
  until the first touch unlocks audio (the pit kiosk Chromium has no autoplay flag).
  On the phone, START RACE (hold 1 s) then asks "Start race in 5 s?" with a big GO button. Kart cards show `GRID` while a kart's
  live scale is 0.0 and it is not stopped (the hold) and flash `GO` when it comes back.
- **Race length and finish.** The start control has a laps chooser (OPEN = untimed, 3, 5, 10):
  `POST /race/start {"secs":5,"laps":N}`. With N set, a kart that completes N laps is finished with
  its position at that moment and gets `race` 100 + position at once; when every online kart has
  finished, the race ends. `POST /race/stop` (login; STOP RACE, hold 1 s, red outline) ends it at
  any time after GO: laps freeze, every kart not finished yet is placed (laps desc, then earliest last
  crossing; karts with no completed lap last, by id). The state-3 race line goes to the T-Beam first, then
  `race` 100 + position to every placed kart heard in the last 15 s (karts not heard get nothing; they only
  cost beacon slots); logged `race_stop` with `skipped` (auto finishes: `event race_finish`).
  **Before GO the button reads ABORT START** and sends STOP ALL instead (a finish command would release the
  grid hold at once, firmware GK_CMD_RACE); the session is cleared, race state 0 goes out, karts stay stopped
  until START; logged `race_abort`. Crossings during the grid hold never count; laps start at the first
  crossing after GO. A FINAL RESULTS
  panel (position, kart colour, laps, best, time from GO to the last crossing) replaces the live
  leaderboard until NEW RACE (= `POST /laps/reset`, which also clears the session). Kart cards show
  FINISHED P3 (FIN P3 on the kiosk); during a laps race the leaderboard has a "Left" column.
  State: `fleet.race.session` (`state` running|finished, `laps_target`, `finished` {kart: pos},
  `ended_by` laps|stop, `results`). Race command args: 1-30 = GO in s, 0 = finish, 101-108 = finish P1-P8.
- **Running positions to the karts (no Wi-Fi needed).** The server also sends the T-Beam
  `{"t":"race","state":S,"pos":[p1..p8]}` (firmware/base-tbeam-s3/SERIAL_PROTOCOL.md "Race positions"),
  which the base carries in every beacon for 30 s so kart screens can show "P3": state 1 with all 0 at
  START RACE, every 2 s while racing (1 until GO, then 2; finished karts keep their place, then laps desc,
  earliest last crossing; no completed lap = 0), state 3 with the final positions first thing at the end
  and again at 3 s and 6 s, state 0 twice (now and 2 s later) on NEW RACE and on ABORT START. The session log gets one `race_pos` event per change. The simulator
  accepts the line silently.
- **Laps.** The pit times laps itself from `tele` fixes: a lap counts when the segment previous
  fix -> current fix crosses the lap line (either direction) and `min_lap_s` (default 20) has
  passed since the previous crossing; the first crossing starts the clock. The crossing time is
  interpolated along the segment when the two fixes are under 5 s apart. Ranking: laps desc,
  then earlier last crossing; each row also has the gap to the leader (`gap_laps`, or `gap_s` at
  the line on the same lap). Shown as a leaderboard panel (big position, kart colour, laps, last,
  best, gap) (operator side panel, kiosk RACE tab,
  phone KARTS tab), a collapsible overlay on every map, and `P# L# best` on each kart card.
  `/api/state` has `fleet.game`, `fleet.laps` (ranked) and `karts[].lap`. Lap state is memory only
  (server restart clears it). Moving the lap line clears practice timing; during a race (or while its
  results show) completed laps are kept and the next crossing of the new line counts against the last
  crossing of the old one (`min_lap_s` applies). A fix-to-fix move faster than 40 m/s (a GPS jump, e.g. a
  cold start after a kart reboot) never counts as a crossing.
- **BOOST.** Per-kart `BOOST` (next to SLOW) and `BOOST ALL` (hold 1 s) send `cmd "boost"`
  with `arg` 100 (full scale for 5 s; firmware auto-reverts, STOP/START cancel it, never above
  1.0). `POST /cmd {"cmd":"boost","kart":1-8|255,"arg":10-100}`.
- **Simulator.** `sim_tbeam.py` honours `boost` (scale echoed for 5 s, kart speeds up) and `race`
  (scale 0 until GO; STOP keeps the hold, START releases it). Its `--print-zones` output includes
  an item box and a closed track.
  `--print-zones` prints a zones document for its oval (lap line on the bottom straight, boost on
  the top straight, mud on the east bend); `--track-scale 0.3` gives ~15 s laps. To see it:

  ```sh
  python3 pit/sim/sim_tbeam.py --tcp 7878 &
  python3 pit/server/pit_server.py --tcp 127.0.0.1:7878 --map-dir /tmp/pitmap   # copy map/ there first
  python3 pit/sim/sim_tbeam.py --print-zones | curl -s -H 'Content-Type: application/json' -d @- localhost:8080/zones
  ```

  Use a scratch `--map-dir` locally: saving zones rewrites `zones.json` in the map folder.
- **Deploy.** `deploy.sh` copies `zones.json` only when the Pi has none, so zones edited on the
  Pi survive later deploys. `ZONES.md` and the rest of `map/` are pushed as before.

## Pace car: record a track by driving it

Instead of tapping the course in by hand, drive it once with one kart and let the pit turn the
path into the track. Nothing changes on the radio: the pit already gets every kart's fixes (1-2 Hz).

1. Operator page -> **ZONES** -> **PACE CAR** row, or on the phone (`/m`) the **PACE CAR** panel under the
   MAP tab (big REC / STOP / CLEAR and a USE AS TRACK button per lap, for starting laps trackside):
   pick the kart (defaults to the first online one),
   press **REC** and drive the course at a steady pace, staying on the racing line. A red **REC K2**
   chip shows on the operator, kiosk and phone pages while recording, and a red trail is drawn on the
   operator map (dashed once stopped).
2. Laps are detected automatically: a lap closes when the kart comes back within 12 m of the lap's
   start after at least 60 m (closed at the nearest approach). Each lap gets a row: length and time.
   Only good fixes count (fix, 5+ satellites, no jumps beyond speed x dt + 15 m, 1.5 m minimum spacing).
3. **STOP**, then **USE AS TRACK** on the cleanest lap (or **All** for a point-to-point course).
   Choose the width (default 6 m) and keep **auto lap line** ticked to put a 10 m start/finish line
   across the lap's start, perpendicular to the direction of travel (it replaces the existing lap line;
   its `min_lap_s` is capped at 60 % of the driven lap time). The lap is Douglas-Peucker simplified
   (1 m, raised until it fits 200 points), closed, and saved to `zones.json` like the editor's SAVE
   (version bumped, other zones kept). The editor reopens on the new document.

API (login): `POST /track/record {"kart":2,"action":"start"|"stop"|"clear"}`,
`GET /track/recording` (kart, recording, since_wall, n_points, points, laps [{n, length_m, time_s, points}]),
`POST /track/use {"lap":1|"all","width_m":6,"lapline":"auto"|"none","simplify_m":1.0}`; `/api/state`
carries `track_rec` (no points) for the chip. Recordings are saved to
`server/map/recordings/<YYYYmmdd-HHMMSS>-k<N>.json` on every lap and on STOP (gitignored, never
deployed or deleted by deploy.sh); the newest is reloaded when the server starts; CLEAR deletes it.
Session log: `op track_record` (start/stop/clear) and `op track_use` (+ the usual `zones_save`).

## On the Pi

Flash a card with [`sdcard/`](sdcard/README.md); first boot installs everything.
Pi: hostname `gokart-pit` (`gokart-pit.local` via avahi), user `pit`, app in
`/opt/gokart-pit`, config `/opt/gokart-pit/server/config.json` (mode 600). Config keys (all optional;
`config.example.json`): `password`, `gps_correction`, `bind`, `http_port` (8080), `trust_localhost`,
`serial_port` (null = auto-find), `tcp`, `cmd_ttl_s` (10), `origin`, `log_dir`, `health_period_s` (10),
`health_file` (tests), `log_max_file_mb` (64), `log_max_total_mb` (1024), `sse_max_clients` (24).

Services:
- `gokart-pit.service`: `python3 /opt/gokart-pit/server/pit_server.py` as `pit`
  (group dialout), `Restart=always`, `StartLimitIntervalSec=0`. No Wi-Fi dependency.
- `gokart-kiosk.service`: cage + Chromium on tty1 (below), `Restart=always`, `StartLimitIntervalSec=0`.
- `gokart-check.timer` (every 30 s, `tools/gokart_check.sh`): restarts the kiosk if cage has had no
  Chromium process under it for 2 checks (unit up at least 60 s), and gokart-pit if `/healthz` failed 3
  checks in a row (unit up at least 30 s). At most 3 restarts an hour per unit, then one per 10 min; each is
  logged with tag `gokart-check` (`journalctl -t gokart-check`).
- udev: `/etc/udev/rules.d/99-gokart-tbeam.rules` makes `/dev/tbeam`.

```sh
journalctl -u gokart-pit -f
journalctl -u gokart-kiosk -b
sudo systemctl restart gokart-pit gokart-kiosk
udevadm info -a -n /dev/ttyACM0 | grep -iE 'idVendor|idProduct|product'   # check the rule's match
```

### If the pit reboots

On 2026-11-06 the pit Pi rebooted several times, came back with no Wi-Fi profile, and the crash
logs were gone. Both causes are fixed by `install.sh` (and the first boot):

- **Wi-Fi lost:** the profile cloud-init made was netplan-backed. firstboot's old
  `nmcli connection modify` made NetworkManager move it to `/etc/netplan/90-NM-<uuid>.yaml`,
  and the power cuts left those files 0 bytes long. Now `tools/wifi_profiles.py` writes plain
  NetworkManager keyfiles in `/etc/NetworkManager/system-connections/` (atomic write + fsync,
  autoconnect, priority 20 primary / 10 second network, retries forever) plus `gokart-eth0`
  (eth0 DHCP fallback). The Wi-Fi list is `/etc/gokart-pit/wifi.env` (root, 0600; written by
  cloud-init on first boot or pushed by `deploy.sh --install` from `sdcard/secrets.env`, WIFI_* lines only).
  A hand-made profile for the same SSID is adopted in place (same UUID, no disconnect). Empty
  `90-NM-*.yaml` files are deleted and cloud-init network config is disabled after the first boot.
- **Logs lost:** Raspberry Pi OS ships `Storage=volatile`. `tools/journal_persist.sh` adds
  `/etc/systemd/journald.conf.d/90-gokart-persistent.conf` (`Storage=persistent`, `SystemMaxUse=200M`).

Next time, one command:

```sh
ssh pit@gokart-pit.local sudo /opt/gokart-pit/tools/why_rebooted.sh      # previous boot; add -2 for the one before
```

It prints the boot list, the previous boot's last 30 journal lines (and whether it ended with a clean
shutdown or just stopped: power cut / brown-out), under-voltage counts, the current `get_throttled`
flags and temperature, NetworkManager state with each profile's file (`/etc` = persistent, `/run` = not),
and the pit services' restart counts.

Power on the pages: the server samples `vcgencmd get_throttled` and the CPU temperature every 10 s
(`server.health` in `/api/state`; skipped off a Pi) and logs every status change to the session log and
the journal. The kiosk, operator and phone pages show a chip only when something is wrong:
**UNDERVOLT** (red, under-voltage now), **throttled** (amber), **had undervolt** (grey, happened since boot).
Repeated reboots plus "had undervolt" = the supply or cable; use the official 5.1 V 3 A supply.

Self-healing pages: every page reloads itself when the pit server restarts (`server.start_id` changes)
or when its event stream has been dead for 20 s while `/healthz` answers (`static/watch.js`; never in the
first 30 s after loading, so no reload loops; never while there are unsaved zone edits).

Wi-Fi by hand if it is ever missing again: `sudo bash /opt/gokart-pit/install.sh pit` (needs
`/etc/gokart-pit/wifi.env`), or
`sudo nmcli con add type wifi ifname wlan0 con-name McLab ssid McLab wifi-sec.key-mgmt wpa-psk wifi-sec.psk '<pw>' connection.autoconnect yes connection.autoconnect-priority 20`.

### Updating the app later

From the Mac, with the Pi on the same network:

```sh
pit/deploy.sh                    # rsync pit/ -> pit@gokart-pit.local:/opt/gokart-pit, restart services
pit/deploy.sh gokart-pit.local --install   # also re-run install.sh (packages, units, udev, Wi-Fi keyfiles, journal)
```

deploy.sh keeps the Pi's `server/config.json`, `logs/`, map imagery and its edited `map/zones.json`. By hand:
`rsync -av --exclude logs/ --exclude server/config.json pit/ pit@gokart-pit.local:/opt/gokart-pit/`
then `ssh pit@gokart-pit.local sudo systemctl restart gokart-pit gokart-kiosk`.
Change the web password by editing `/opt/gokart-pit/server/config.json` and
restarting `gokart-pit` (logged-in browsers must log in again).

### Panel speakers (HDMI audio)

`install.sh` installs `alsa-utils`, adds the user to `audio` and writes `/etc/asound.conf`
making the first vc4-hdmi card the ALSA default (`GOKART_HDMI_CARD=vc4hdmi1` if the panel is on
the Pi 4's second HDMI port). On the pit Pi (2026-11-06) the panel is on HDMI-A-1 and the file points at
`vc4hdmi0`; the sound itself has not been confirmed by ear. The kiosk Chromium now starts with
`--autoplay-policy=no-user-gesture-required` (2026-11-07, not yet deployed), so countdown sounds play
without a first touch; the SOUND chip still mutes per browser.

### Kiosk approach (running on the pit Pi since 2026-11-05)

`gokart-kiosk.service` runs `cage -d -s -- start-chromium.sh` as `pit` on tty1,
with `PAMName=gokart-kiosk` (`/etc/pam.d/gokart-kiosk` includes
`pam_systemd`). That PAM session registers a logind session on seat0/tty1, and
cage's libseat logind backend uses it to open DRM and input devices without
root and without seatd. This is the pattern from cage's own "Starting Cage on
boot with systemd" wiki page; `getty@tty1` is disabled (SSH and tty2-6 remain).
Chromium runs with `--kiosk --ozone-platform=wayland --touch-events=enabled`
and a dedicated profile; the service waits for `/healthz` before launching and
restarts if Chromium exits.

It worked first time on Raspberry Pi OS Trixie Lite (no seatd needed). Note that Chromium moves
itself into a systemd user scope (`app-org.chromium.Chromium-N.scope`), so it is not in the kiosk
unit's cgroup; `tools/gokart_check.sh` looks for it in cage's process tree. If a new image misbehaves:
1. Package name: install.sh tries `chromium`, then `chromium-browser`.
2. If cage logs "Could not open seat" / libseat errors: `sudo apt install seatd`,
   `sudo systemctl enable --now seatd`, add `pit` to the `_seat` (or `video`)
   group per the Debian seatd package, and set `Environment=LIBSEAT_BACKEND=seatd`
   in the unit.
3. No picture: check `journalctl -u gokart-kiosk` for "no outputs"; a display
   without usable EDID needs `video=HDMI-A-1:1024x600@60` in cmdline.txt.
4. Touch offset/rotation: with one output, cage maps touch to it; a rotated
   mount needs `wlr-randr` or a udev calibration matrix (not handled).

The alternative considered was labwc (Raspberry Pi OS's own Wayland desktop)
with autologin; it pulls in a desktop stack, so cage was chosen as smaller.

## Race day checklist

Before the karts go out:
1. Pit Pi on the official 5.1 V 3 A supply; no UNDERVOLT / "had undervolt" chip on the kiosk.
2. Kiosk top bar: **T-Beam OK**, **radio ok**; no red banner. If the repeater is out, its chip shows
   `rep … dBm · 1 Hz` within a minute (the pit drops the fleet to 1 Hz on its own).
3. Every kart card **ONLINE** with a fresh fix (sats shown, not "last …"), no FAULT, no SCALE≠MODE,
   no "VESC LINK DOWN". Tap a card: battery %, ESC temp, kart-hears-pit RSSI look sane.
4. **PING** one kart and **STOP / START** each kart once with wheels up: outcome **done** on the card.
   A kart that answers START with "rejected: send a mode first" rebooted: send its mode, then START.
5. **GAME ON** only if zones are drawn: a lap line exists (RACE tab says "Waiting for the first
   crossing", not "No lap line yet"); the map shows the track and zones where they belong.
6. Phone: log in at `http://gokart-pit.local:8080/m`, check the STOP ALL hold works from where you stand.
7. Sound: tap SOUND once on the kiosk if it reads TAP FOR SOUND.

Racing:
- START RACE (hold 1 s) with the seconds and laps chosen; the lights and GO! show on every page.
  To cancel during the lights use **ABORT START** (same button, hold 1 s): it sends STOP ALL; START ALL
  releases the karts afterwards.
- STOP RACE (hold 1 s) any time after GO: final results; **NEW RACE** clears them for the next heat.
- **STOP ALL** (hold 1 s) is always the emergency control and beats everything else.

After the session: download the log (operator page) or copy `/opt/gokart-pit/logs/session-*.jsonl`;
after any surprise reboot run `sudo /opt/gokart-pit/tools/why_rebooted.sh` before power-cycling again.
