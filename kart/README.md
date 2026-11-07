# Kart display (Pi 3 B on each kart): server, driver page, simulator, SD card kit

Each kart carries a Raspberry Pi 3 Model B driving a LESOWN L7 7-inch 1024x600
HDMI touchscreen. A Heltec WiFi LoRa 32 V4.3 running
[`firmware/kart-heltec-v4`](../firmware/kart-heltec-v4) plugs into the Pi by
USB-C and is the kart's brain (VESC over BLE, GPS, LoRa). **The Pi is display
only** ([`docs/requirements.md`](../docs/requirements.md)): it never talks to the
VESC; a mode tap is a text request to the Heltec, which decides and answers.

Copied/adapted from [`pit/`](../pit/README.md) (same conventions, same kiosk
recipe, same first-boot approach that ran on the pit Pi 4 on 2026-11-05).

## Status: what is verified and what is not

| Item | Status |
| --- | --- |
| Server + simulator over TCP and pty, parsing, staleness, derivations, command forwarding + echo matching, SSE, session log | **Tested on the Mac** (macOS, Python 3.13, pyserial 3.5): `kart/tests/` and manual runs |
| Map package sync (download on version change, cache, offline restart, fallback URL, bad image) | **Tested on the Mac** against a fake pit HTTP server |
| Driver page | **Rendered on the Mac** in headless Chrome at 1024x600 against the simulator (driving, STOP+SLOW+fault, no fix/no VESC/no pit/no map, heading-up + beginner). Touch, pinch, press-and-hold and the follow-resume timer were **not** exercised with a finger on a real panel |
| Parsing against real Heltec output | Real `cmd`/`log`/`ble` lines from the 2026-11-05 kart-2 console capture; `self` lines use the firmware's exact field set but **no real `self` line** has been fed through yet |
| `gen_cloudinit.py`, `flash.sh` refusal checks | **Run on the Mac** (refuses `/dev/disk0` and a non-external disk). No kart card flashed yet |
| Real Heltec over USB on a Pi 3 B, `/dev/heltec` udev rule | **Running on kart 2** since 2026-11-06 16:26 (`/dev/heltec -> ttyACM0`, real `self`/`gps`/`ble`/`status`/`race` lines). Unplug/replug and a Heltec reboot mid-session: **tested on the Mac** only (`tests/test_e2e.py` `ServerPieces`, TCP stand-in) |
| First boot, apt packages, cage + Chromium kiosk, HDMI 1024x600 pin, USB touch **on a Pi 3 B** | **Running on kart 2** since 2026-11-06 16:26 (Chromium 156, VC4 GPU compositing + accelerated canvas confirmed) |
| Map redraw cost on a Pi 3 B | **Measured on kart 2** 2026-11-06 (quality pass): Chromium 83 % -> 24 % CPU idle, 192 % -> 96 % in demo (top, % of one core); see "Performance" |
| Kiosk watchdog, `rotate: 180`, hardened units, RAM disk cache | **Written and checked locally only** (shell syntax, cmdline rewrite run on a copy); **not yet deployed** to a Pi |
| SLOW banner | From `slow_mph` in `self` lines (firmware 2026-11-06+, what kart 2 runs); **inferred** from `cmd` echoes only for older firmware; see "SLOW" below |
| HDMI panel sound (ALSA default = vc4-hdmi, Chromium WebAudio) | **Verified on kart 2** 2026-11-06: the HDMI PCM runs while the page plays; not listened to |
| Polish pass UI (race takeover, finish, roulette, dark dash) | **Rendered** headless at 1024x600 locally and against kart 2 in demo mode, zero console errors |
| Quality pass UI (2026-11-06 night: link chip, 56 px map buttons, finish chip/badge, layer cache) | **Rendered** headless at 1024x600 in 25 states against a fixture server, zero console errors; layer cache + merged frames **ran on kart 2** in demo mode |
| Game layer (zones sync, boost/mud commands, laps, HUD) | **Tested on the Mac** (unit tests + simulator course e2e, HUD rendered headless at 1024x600). `boost` against a real VESC **not** exercised from the Pi; real pit zones were `enabled: false` at deploy |

Simulated data is labelled: the simulator announces itself and the page shows a
striped SIMULATION banner.

## Layout

```text
kart/
  server/kart_server.py   HTTP + SSE + /cmd + session log + wiring (stdlib + pyserial)
  server/kartcore.py      parsing, staleness, battery/mph, SLOW, command tracking, game engine (pure logic)
  server/link.py          Heltec link: serial auto-find + reconnect, writer thread, or TCP to the simulator
  server/demo.py          demo mode: in-process simulator replacing the Heltec for the page
  server/mapsync.py       pit map package -> local cache (cache/map/), served at /map/<file>
  server/static/          index.html, app.js, style.css (driver page, no CDN)
  server/config.example.json   copy to config.json (gitignored)
  sim/sim_heltec.py       simulated kart Heltec (pty or TCP), honours mode/slow/stop/start/status/boost
  sim/fake_pit.py         fake pit map endpoint for local runs (+ zones.json)
  sim/zones.course.json   SIMULATION game zones matching sim_heltec.py --course
  tests/                  test_core.py, test_mapsync.py, test_e2e.py
  install.sh              runs ON THE PI; idempotent install/update
  deploy.sh               Mac -> kart Pi rsync + restart
  systemd/, kiosk/, udev/ units, PAM file + Chromium launcher (with watchdog), /dev/heltec rule,
                          touch rotation rule (rotate 180 only)
  sdcard/                 SD card kit (flash.sh, gen_cloudinit.py, firstboot.sh, cloud-init/)
  logs/, cache/           runtime (gitignored): session logs; cache/map (map package), cache/game-state.json
```

## Run it locally (Mac)

```sh
python3 kart/sim/fake_pit.py --port 18080 &          # serves pit/server/map/map.shop.json + its image
python3 kart/sim/sim_heltec.py                       # prints e.g. /dev/ttys004
python3 kart/server/kart_server.py --port /dev/ttys004 --pit-url http://127.0.0.1:18080
open http://127.0.0.1:8081/                          # best in a 1024x600 window
```

TCP instead of a pty: `sim_heltec.py --tcp 7979` and `kart_server.py --tcp 127.0.0.1:7979`.
Scenarios: `--nofix`, `--novesc`, `--fault 5`, `--pitloss`, `--pit-stop-at 10
--pit-start-at 20 --pit-slow-at 30` (seconds after start), `--kart N`, `--course`
(game layer, see below).
With a real Heltec on the Mac, run the server with no `--port`: it tries
`/dev/heltec`, a 303a:1001 match, `/dev/serial/by-id/*`, `/dev/ttyACM*`, `/dev/cu.usbmodem*`.

Tests (no hardware, no network):

```sh
python3 -I kart/tests/test_core.py      # parsing, derivations, commands, game engine (85 tests)
python3 -I kart/tests/test_mapsync.py   # map package + zones sync against a fake pit
python3 -I kart/tests/test_e2e.py       # simulator + fake pit + server processes; demo mode; link,
                                        # session log, config, SSE deltas, restart persistence
```

Demo mode on any machine: start the server (with or without a Heltec), then hold the KART badge
3 s on the page, or `curl -XPOST -d '{"on":true}' http://127.0.0.1:8081/api/demo` from the same
machine (`{"on":false}` ends it).

## Behaviour

- **Input** (lines consumed): the Heltec's USB lines (`gkTelemetryToJson`): `self` (2 Hz, this
  kart), `gps` (one per fix, up to 5 Hz: game positions), `fleet` (each other kart heard;
  `rssi`/`snr` as heard by this kart), `race` (countdown / go / position / finish), `cmd`
  (echo of every command, any source), `log`, `ble`, `status`. Anything else that is not a JSON
  object (boot chatter, a partial line after a reconnect, garbage) is logged and counted
  (`rx_bad`), never shown; a line over 4 KB without a newline is dropped.
- **Output** (lines sent, plain text, nothing else ever): `status` (read-only; 1 s after connect,
  1 s after a detected Heltec reboot, then every `status_poll_s`), `mode N` (driver hold), `boost
  SECS SCALE` (game layer only), and from localhost debugging only `slow [mph]`, `stop`, `start`,
  `ping`. In demo mode all of them go to the simulator, never the Heltec.
- **Link** (`link.py`): one reader thread per connection and a separate writer thread, so a stuck
  USB endpoint never stalls reading (pyserial `write_timeout` 0.5 s, no `tcdrain`). A failed write
  closes the port and the reader reconnects. A queued line older than 1 s is dropped and the queue
  is emptied when the link drops: **nothing is replayed after a reconnect**. The state is in
  `/api/state` `server.link` (`state`: `up` / `reconnecting` / `connecting` (never up) / `demo`,
  `attempts`, `error`, `down_s`); the page shows one orange "USB: RECONNECTING #n" chip instead of
  the PIT/VESC/GPS chips and a banner with how long it has been lost. A Heltec reboot (uptime drops)
  re-polls `status`, clears an inferred SLOW and is logged (`heltec_reboot`).
- **Freshness**: own kart is live if a `self` line arrived within 3 s; other
  karts are online <= 5 s, stale <= 15 s, else offline (pit thresholds). VESC
  values come only from samples with `vesc_link: true` and GPS only from
  samples with a fix, each with its own age (the firmware prints 0 for both
  when they are missing). Nothing missing is ever shown as 0: speed reads
  "NO FIX"/"NO DATA", battery "--", positions stay at the last fix with an age.
- **Speed**: GPS ground speed (`gps_kph` x 0.621371) from a fresh fix, labelled
  GPS; wheel mph (contract formula) shown small as "uncalibrated", and in the big
  number (labelled WHEEL) only when there is no GPS fix but fresh VESC data.
- **Battery**: 20S Li-ion, 60.0 V = 0 %, 84.0 V = 100 %, linear, clamped; "under
  load" above 20 A (same code as `pit/server/pitcore.py`, checked by a test).
- **Pit link**: the Heltec reports `base_rssi` non-zero while it has heard the
  pit within 30 s; the chip shows that RSSI, "NO PIT" at 0, "PIT ?" when the
  Heltec itself is silent.
- **Mode**: shows actual (from VESC read-back) with a tick when confirmed, and
  "requested X: not applied yet" while they differ. ECO / NORMAL / SPORT are
  press-and-hold (`mode_hold_ms`, default 500 ms; 0 = tap) to survive bumps.
  The request goes out as `mode N`; the echo (`cmd`, src 254) and later
  `last_cmd_seq`/`cmd_result` give the result (accepted, applied, NO VESC,
  REJECTED, not confirmed, or NO REPLY after 4 s). Nothing is ever resent.
  STOP/START are **not** on the driver page (pit controls them); `POST /cmd`
  still accepts them from localhost for debugging.
- **STOPPED** (red, full width) comes straight from the `stopped` flag.
- **SLOW** (orange): firmware from 2026-11-06 (kart 2) reports it in the `self` line as
  `slow_mph` (0 = off), which the display uses directly. With older firmware (no `slow_mph`) it
  is inferred from command echoes: a SLOW the firmware took (accepted/applied/unknown) stays on
  until a START is echoed or the Heltec reboots (uptime drops); after a display restart with a
  Heltec up more than 2 minutes it is "unknown" and no banner shows until the next SLOW/START.
- **Fault**: `VESC FAULT n NAME` while VESC data is fresh, hidden when
  `beginner_mode` is on (driver presentation only; logs and the pit keep it).
- **Map**: imagery from the cached package placed by its bounds, pit marker,
  course line, other karts as numbered dots (stale = grey dashed + age), own kart
  as a big arrow along the GPS heading, trails (own 120 s, others 30 s). FOLLOW
  keeps the own kart centred (north-up, or `heading_up` in config); a touch,
  drag, pinch or wheel pauses it and it resumes 10 s after the last touch. FOLLOW
  off = free pan. ALL fits every kart + the course. One scheduler draws the map at most
  `map_fps` (10) times a second (30 while a finger is on it), in the same animation frame as the
  DOM update of the latest SSE frame, and only while something changes or moves; nothing is
  drawn while the page is hidden (requestAnimationFrame pauses). See "Performance".
- **Logs**: `logs/session-YYYYmmdd-HHMMSS[-pNN].jsonl`, one per server start, a new part every
  64 MB: every serial line received (`rx`) and sent (`tx`), each request (`op`), echoes, game
  records, link/map/config events. `rx` lines go through a 64 KB buffer flushed every 0.25 s
  (fewer SD writes); everything else is flushed at once. All session logs together stay under
  `max_log_mb` (800): the oldest files go at start, at every rotation, every 5 minutes, and whenever
  the card has under 300 MB free. A full card or an unwritable log dir never stops the display
  (falls back to `/tmp/gokart-kart-logs`; `server.log_errors` counts failed writes). Measured on
  kart 2: about 4.5 MB/hour standing still; driving adds the 5 Hz `gps` lines (estimate: a few
  times that, not measured).
- **HTTP** (0.0.0.0:8081): `GET /` (driver page), `/static/*`, `/events` (SSE, ~5 Hz: the full
  state first, then `{"_d":1, ...}` with only the top-level keys that changed; `heltec`,
  `rx_*`, `gps_lines`, `kiosk` are left out), `/api/state` (everything, once), `/api/kiosk`
  (heartbeat age, for the watchdog), `/map/package.json`, `/map/zones.json` (404 = no game),
  `/map/<image>`, `/healthz`, `GET /api/sound-test[?alsa=1]` (localhost); `POST /cmd`
  (`{"cmd":"mode","arg":0-2}`, `slow` [mph 1-40], `stop`, `start`, `ping`, `status`; localhost
  only unless `remote_cmd: true`), `POST /api/demo` (`{"on":bool}` / `{"toggle":true}`,
  localhost), `POST /api/map/refresh`, `POST /api/heartbeat` (the kiosk page, localhost; frame
  stats land in `/api/state` `kiosk`). Remote browsers can watch the page but not send commands.
- **Kart id**: `kart_id` in config is a label; the Heltec's own id (from `self`) wins. A mismatch
  shows the KART chip red with a "?", the note "Heltec says kart N; display config says M", and is
  logged once (`kart_id_mismatch`, also in the journal).

## Game layer

The "Mario Kart" layer: boost pads, mud and a start/finish line drawn on the pit
operator page. The format and the rules are the contract in
[`pit/server/map/ZONES.md`](../pit/server/map/ZONES.md); this section is the kart's half.

**How zones arrive.** The pit lists `"zones": "/map/zones.json", "zones_version": N`
in `/map/package.json`. `mapsync.py` fetches the file into `cache/map/zones.json`
whenever `N` changes or the file is missing (temp file + rename, before the
package is written, like the image). A package without `zones` = the pit has no
game: the cached file is deleted. A bad zones download keeps the previous zones
and retries on the next poll; it never blocks the map. The cached file survives
reboots, so the game works on track with no Wi-Fi. `GET /map/zones.json` on the
kart shows what it has.

**What the kart does** (`kartcore.GameEngine`, evaluated on every `self` line
with a fix, plus a 4 Hz tick):

- **boost** (circle or polygon): on entry, sends the Heltec `boost <secs> <scale>`
  once. Re-armed only after leaving the zone (> 3 m outside the edge) and
  `cooldown_s` (default 20) after that exit.
- **mud**: on entry `boost 3 <scale>`, repeated every 2 s while inside; leaving
  just stops repeating (the Heltec reverts within 3 s). Inside mud, a boost
  entry is suppressed (mud wins).
- **lapline**: the path between consecutive fixes (<= 5 s apart) crossing a-b in
  either direction counts if `min_lap_s` (default 20) has passed since the last
  crossing. The first crossing starts lap 1; crossing time is interpolated
  between the two fixes. Lap count, current, last and best are kept per kart
  (since the last server start / zones change). Only the first lap line is timed.
- **Position samples**: the Heltec's `{"t":"gps"}` line (one per fix, up to
  5 Hz; firmware 2026-11-06+, `gpsline off` on its console disables it) is the
  source; when none has arrived for 2 s the 2 Hz `self` line's fix is used.
  Filters (`kartcore.GpsFilter`): a sample with `hdop` > 4 or `sats` < 5 is
  "poor" (drawn greyed, "weak GPS: zones paused", never triggers or times a lap;
  `hdop` 99.0 = unknown counts as poor); a jump further than speed x dt + 15 m
  from the last accepted sample is an "outlier" and dropped, unless the next
  sample agrees with it (then accepted as a new track: no lap crossing is
  drawn across the jump). The Doppler heading is used only above 1.5 m/s, else
  the last good one is kept. The HUD dead-reckons the own kart from kph +
  heading for at most 1.2 s between samples.
- **Base correction** (config `gps_correction`, default **off**): positions are
  shifted by (pit surveyed position `pit` in the map package) - (pit radio's live
  fix, `self` `base_lat`/`base_lon` while `base_fix_age_s` < 5), averaged over
  30 s, only when under 25 m. The vector is always in `/api/state` under
  `game.correction` (`applied`, `vector` with east/north/mag metres, `why_not`).
- **Hysteresis** 3 m for every zone; **no fix** = no evaluation, and after 5 s
  without a fix every zone is left and the previous fix forgotten (no lap
  "crossing" across a GPS gap).
- **Entry between samples**: a zone is also entered when the straight path from the previous fix
  (<= 1.5 s old) passes through it (`zone_enter` with `swept: true`), so a fast kart on the 2 Hz
  `self` fallback cannot jump over a 3 m pad. Entered and left on one fix = enter + exit records.
- **Lap line jitter**: after a counted crossing the line is re-armed only once the kart has been
  more than 6 m from it, so GPS jitter while parked on (or gridded at) the line never counts laps;
  before the first full lap the latest crossing of such an episode is the lap start. A fix exactly
  on the line counts once, not twice.
- **Overlaps**: a boost pad and an item box entered on the same fix are both used up (cooldowns,
  roulette) but only the strongest effect (highest scale, then longest) is sent; the other is a
  `suppressed` record (`overlaps <id>`). Mud still wins over both.
- **Timing across restarts**: lap count/last/best/current-lap start and the race state (GO time,
  hold, finish, position) are saved to `cache/game-state.json` on every lap/race event and restored
  when the server restarts **in the same boot** (monotonic clock), within 30 minutes, with the very
  same lap line. Not kept: zone inside/cooldowns, boost overrides (the Heltec reverts on its own),
  positions/trails, SLOW inference, demo state. Entering demo mode parks the real timing and
  leaving it restores it; nothing from the simulated kart survives the switch (late simulator lines
  are dropped by identity under the app lock).
- **Never**: a scale above 1.0 (clamped to 0.10-1.00, secs 1-30), STOP/START/mode/
  SLOW, or anything while the `self` line says **STOPPED** (a boost entered while
  stopped is used up, not saved for START; mud resumes after START if still
  inside). `enabled: false`, no file, or `"game": false` in this kart's
  `config.json` = no commands and no laps. A pit STOP still wins (the firmware
  clears the override); SLOW's ERPM cap stays.
- Every event (`zone_enter`, `zone_exit`, `boost`, `mud`, `suppressed`,
  `lap_start`, `lap`, `lap_ignored`, `game_on`/`game_off`) and every command
  (`ev: cmd`, with `sent`) is a `kind: "game"` record in the session log; boost
  echoes (`cmd` 6) are logged as `ev: cmd_echo` with the firmware's result.
  `/api/state` has it all under `game` (`on`, `off_reason`, `inside`, `lap`,
  `meter`, `events`, `last_result`, `pos` = last filtered sample with `quality`,
  `gps_source`, `gps_lines`, `gps_hz`, `correction`); the zones document is
  `map.zones`; the new `self` GPS fields are under `self.gps` (`hdop_now`,
  `gps_hz`, `fix_age_ms`, `base`).

- **Item boxes** (`type: "item"`, circle or polygon): on entry the kart rolls
  locally (seedable RNG): 45 % BOOST (`boost 5 1.0`), 15 % SUPER (`boost 8 1.0`),
  25 % MUD (`boost 3 0.5`), 15 % empty (nothing sent). Same exit/cooldown rules as
  a boost pad (`cooldown_s`, default 15) and the same gates. Logged as
  `ev: "item"` with `item: boost|super|mud|empty`.
- **Race start** (`{"t":"race","event":"countdown","go_in_ms":N,...}` /
  `{"event":"go"}` from the race firmware): GO = the countdown line's arrival +
  `go_in_ms`; the go line only confirms it (`game.race.go_confirmed`). A go line with no
  countdown seen (display restarted during the hold) starts the race itself (laps reset, a previous
  finish cleared). Grid hold
  lasts until GO, or until a STOP/START echo (the firmware clears the hold).
  During the hold no zone command is sent and lap-line crossings are ignored;
  laps reset at GO and count from the first crossing after it.
- **Running position** (race firmware 2026-11-06 20:30+, from the pit beacon): the
  `self` line's `race_pos` (0 = none, 1-8) and `race_state` (0 idle, 1 countdown,
  2 running, 3 finished), and `{"t":"race","event":"position","position":P,"state":S}`
  on every change. `/api/state` `game.race` has `pos`, `state`, `state_name`, `beacon_state`,
  `pos_prev`, `pos_changed_wall`; the beacon's state is applied from every `self` line (a
  `race_state` event on change). **Beacon state 3** (pit STOP RACE) finishes the race at once with
  the beacon position (`position_source: "beacon_state"`) even if the per-kart finish command has
  not arrived; a later firmware finish only refines the position (`race_finish_cmd`). `state`
  reads 3 "finished" while this kart has a finish and the beacon still says running; beacon state
  1 (next countdown) clears it. On 2026-11-06 01:51 kart 2's Heltec never reported state 3 (2 -> 0
  at NEW RACE), so that gap is upstream of the display; each change is a `race_position` game event
  (`dir`: new / up / down / gone). The FINISH takeover uses the firmware's finish
  position and falls back to the last beacon position (`position_source`).
  The page shows a 72 px "P3" badge in the kart's colour next to the lap panel
  (hidden at 0; after the flag it shows the finishing place) that pops on change, with an ascending two-note when the position
  improves and a descending one when it drops (tones live in app.js because
  sound.js must stay identical to the pit's copy).
- **Gates for every zone command**: game on, kart not STOPPED, not in grid hold,
  a usable GPS sample (hdop <= 4, sats >= 5, not an outlier). Zones may be as
  small as `r_m` 3 (pads are typically 5 m).
- **Track route** (`zones.json` `"track": {"points", "closed", "width_m"}`): no
  game effect; validated (2-200 points, a bad track is ignored and the zones
  still work) and sent to the page as `game.track`.

**Driver page.** Dark racing dash (polish pass 2026-11-06): near-black panels, light-grey labels,
pure white only for the numbers, solid state colours, no glow. Black speedometer card (0-30 mph arc, GPS mph; wheel mph,
labelled WHEEL, only when there is no GPS fix), the mode as a gear badge (E / N / S,
dashed blue while a request is pending), a BOOST meter (the live VESC current
`scale` against the requested mode's scale, marked: amber "BOOST!" above it, brown
"MUD" below it, with the seconds left), a lap panel on the map (lap N, current,
last, best) or a small GAME OFF chip, zones on the map (boost = amber with
chevrons, mud = brown with dots, lap line = checkered; greyed when the game is
off), a thin LAST | BEST strip under the speedometer while the game is on, the PIT chip
coloured by how well the kart hears the pit (`base_rssi` > -90 green, > -105 amber, else red),
and a short full-screen flash (the text pops in) + synthesized sound on boost, mud and each
lap; the BOOST meter pulses while boosting.
Item boxes show as a blue/white "?" box; entering one plays a ~1.2 s roulette
(a 176 px box at the top of the map, left of the map buttons, each icon popping in) ending on the result, with a
spinning star for SUPER.
Every zone keeps its true-scale ring but its marker is at least 24 px across, so
3-5 m pads stay visible at the 70 m follow span. The track is drawn under the
zones as a translucent cyan band `width_m` wide with a dark edge and white
direction chevrons every 15 m; in heading-up mode the ~80 m ahead of the kart is
brighter. Race start: a full-screen takeover (HUD dimmed to 20 %): five 140 px lights in a
full-width dark bar, one more each second, "GO in N" in 120 px type, a soft tick each second
of the hold and a beep on the last three lights, then the whole screen turns green with
"GO!" (200 px) and a long higher tone for 1 s, fading back in 0.5 s. The GRID banner
(checkered, in the STOPPED/SLOW row, same 64 px height) shows during the hold and the BOOST
meter reads GRID. STOPPED/SLOW/connection banners and a VESC fault stay above the takeover.
Race finish (`{"t":"race","event":"finish","position":P}` from the firmware when the pit
sends `race` 0 or 100 + P): a full-screen checkered takeover with "P3" + "FINISH" (or a
huge "FINISH" when P is 0), the kart's laps and best lap and a fanfare, for 6 s, then a 0.5 s
fade; the lap panel then reads FINISHED and the P badge the finishing place until the next
countdown (with the game off, a small "FINISHED P3" chip next to GAME OFF). Laps stop counting
after the flag (`lap_ignored` why `finished`) and start again with the next countdown.
**Map orientation**: `map_orientation` defaults to `heading_up` and
`follow_span_m` to 70: while following, the map turns with the kart (at most
120 deg/s; the heading is held below 1.5 m/s) and the kart sits ~60 % down the
map so more of the track ahead is visible. Touch/pinch still pauses following
(north-up while panning) and FOLLOW resumes it; the round "N" compass (top right, under
the see-through FOLLOW / + / - / ALL buttons, 66 x 56 px) shows where north is. ALL fits the karts,
the course, the zones and the track. The own kart is an arrow with a white outline and a
drop shadow. The safety
banners (STOPPED, SLOW, connection, SIMULATION) sit above the flash and keep their
size; while one shows the speedometer shrinks so the mode buttons stay on screen.
**Sound.** Every sound comes from one WebAudio helper, `static/sound.js` (an identical copy
is in `pit/server/static/`; `tests/test_core.py` checks they match), synthesized only (no
samples, no copyrighted melodies): lights = short mid beeps, GO = long higher tone, grid hold
= soft tick, item box = accelerating ticks then a two-note chime, boost = rising filtered-noise
whoosh + sawtooth sweep, super = an original 6-note jingle with sparkle, mud = low wet "blub",
lap = two-note fanfare, best lap = three ascending notes, finish = fanfare, pit STOP = firm low
double tone. Each is under 1.5 s. The kart profile is louder and fuller than the pit's (an
octave-down layer and a compressor). Config: `"sound": false` silences it, `"sound_volume"`
0-1 (default 0.8) is the master. The speaker button at the bottom right of the map mutes
(remembered per display in localStorage); "TAP TO ENABLE SOUND" shows until audio is unlocked
(the kiosk runs Chromium with `--autoplay-policy=no-user-gesture-required`, so on the kart it
never shows). Every button press shrinks to 97 % for 80 ms; press-and-hold buttons show a
filling ring.

**Panel speakers (HDMI audio).** The L7's speakers are fed by HDMI. `install.sh` installs
`alsa-utils`, adds the user to `audio` and writes `/etc/asound.conf` making the vc4-hdmi card
the ALSA default (`plug` over `hdmi:CARD=vc4hdmi,DEV=0`, which converts to the IEC958 frames
the vc4 driver wants). Debian's Chromium falls back to ALSA when there is no PulseAudio /
PipeWire, so no sound server is installed. Before this the default was card 0 (the 3.5 mm
jack), which is why the panel was silent. The HDMI device is exclusive: Chromium holds it for
about 20 s after the last sound, during which `aplay` reports "busy".
`GET /api/sound-test` (from the Pi itself) makes the open driver page play three start beeps,
GO and a boost even when muted; `?alsa=1` instead plays a 1 s tone with `aplay` (no browser).
**TEST SOUND** shows next to the speaker button while demo mode is on. Verified on kart 2
(2026-11-06): `/proc/asound/card1/pcm0p/sub0/status` goes `RUNNING` (IEC958_SUBFRAME_LE,
48 kHz, 2 ch) while the page plays; the 3.5 mm card stays closed. Nobody has listened to it yet.

**Test with the simulator:**

```sh
python3 kart/sim/fake_pit.py --port 18080 --zones kart/sim/zones.course.json &
python3 kart/sim/sim_heltec.py --tcp 7979 --course &
python3 kart/server/kart_server.py --tcp 127.0.0.1:7979 --pit-url http://127.0.0.1:18080
open http://127.0.0.1:8081/
```

The course loop (70 x 36 m around the shop preset origin) runs through the boost
pad (east straight), the mud (south straight) and across the start/finish line
(north straight); a lap takes about 35 s. The simulator honours `boost SECS SCALE`
like the firmware (scale follows and reverts; STOP/START cancel; rejected while
stopped) and its speed follows the scale. It also prints 5 Hz `gps` lines and the
new `self` GPS fields (`--nogpsline` = self lines only, `--hdop 6` = poor GPS: the
game ignores every sample). Add `--pit-stop-at 20` to see that a
stopped kart sends nothing. `tests/test_e2e.py` runs this course with `--warp 3`.

**Demo mode** (config `"demo": true`, default): press and hold the KART badge
3 s (or `POST /api/demo {"on": true}` from the Pi itself). An in-process copy of
the simulator (`server/demo.py` + `sim/sim_heltec.py`) replaces the Heltec link
for the page: it drives a closed loop at ~15 mph along the pit's track if there
is one, else through the centre of every boost/mud/item zone of the pit's
current zones (forced on: it is a simulation) and across the lap line (a detour
is added when a loop would be shorter than `min_lap_s`), or around a built-in
demo course at the kart's position when the pit has no zones. It starts with
`race 5`; the "beacon" position cycles P3 -> P2 -> P1, one step per lap after GO, and after
3 laps the simulated pit flags it home in P1 (`race 101`), so the FINISH
takeover can be seen on the real panel. The SIMULATION banner says DEMO with the time left. While demoing,
every line the server would send (game commands, mode taps, status polls) goes
to the simulator and the real Heltec's lines are logged (`ignored: "demo"`) but
not shown: **nothing is written to the real Heltec**. It ends after `demo_max_s`
(180 s), on another 3 s hold, or `{"on": false}`, and the page returns to the
real link without a restart (lap timing starts fresh).

**Simulator race**: `race [secs]` / `finish [P]` on its console (or `--race-at S`) does what the
race firmware does (grid hold at zero power, countdown and go lines, STOP/START
cancel). The driver page never sends `race`. `--race-pos "3,2,2,1"` (`--race-pos-step 10`)
plays the beacon's running position for this kart like the race firmware (self fields and
position events; with `--race-at` the sequence starts at the countdown).

**Safety notes.** Zones only change the current scale (motor torque limit)
inside the firmware's own limits and never above 1.0; ECO/NORMAL karts do get up
to SPORT torque on a 1.0 boost pad, which is the point, so place pads on
straights. The race director's kill is the pit's `enabled` switch (karts stop
sending within one poll, 30 s, or at once after a `POST /api/map/refresh`); a pit
STOP is always immediate. GPS is a few metres off: zones under ~10 m feel random.

## How the map arrives from the pit

The pit serves `GET /map/package.json` and `/map/<image>` with no login
([`pit/server/map/README.md`](../pit/server/map/README.md)). The kart server
polls `pit_urls` in order (`http://gokart-pit.local:8080`, then
`http://192.168.151.240:8080`) every 30 s. When the package `version` changes
(or the cached image is missing) it downloads the image to `cache/map/`, checks
it is a JPEG/PNG/WebP, renames it into place and only then writes
`package.json`; the old image is deleted. Same version: only metadata is
refreshed. Pit unreachable: nothing changes and the page keeps drawing the
cached copy, including after a reboot on track. To push a new map: update it on
the pit and **bump `version`**; each kart picks it up the next time it is on the
pit Wi-Fi. Until a kart has seen the pit once, its map has no imagery.

## On the Pi

Flash a card with the SD kit (below); first boot installs everything.
Hostname `gokart-kart-N` (`gokart-kart-N.local`), user `kart`, app in
`/opt/gokart-kart`, config `/opt/gokart-kart/server/config.json`.

- `gokart-kart.service`: `python3 /opt/gokart-kart/server/kart_server.py` as
  `kart` (group dialout), `Restart=always`, `StartLimitIntervalSec=0` (never gives up),
  `Nice=-5`, no Wi-Fi dependency.
- `gokart-kart-kiosk.service`: cage + Chromium (`--kiosk http://127.0.0.1:8081/`)
  on tty1 via the PAM/logind recipe from `pit/kiosk`, `Restart=always`, `StartLimitIntervalSec=0`.
  `kiosk/start-chromium.sh` flags: one renderer, disk cache on the RAM disk (`$XDG_RUNTIME_DIR`),
  `--disable-renderer-accessibility` (Debian's `/etc/chromium.d/default-flags` forces it on; ~5 %
  CPU on kart 2), autoplay without a gesture, no crash/restore bubbles, no background networking
  or component updates. GPU: Debian's defaults already enable GPU rasterization; VC4 compositing
  and the accelerated 2D canvas work on kart 2. Extra flags (e.g. `--disable-gpu` if the GPU path
  misbehaves, or `--remote-debugging-port=9222` for measurements) go in
  `/etc/default/gokart-kart-kiosk` as `GOKART_CHROMIUM_FLAGS="..."`.
- **Kiosk watchdog** (in `start-chromium.sh`): every 10 s it checks that Chromium is running and
  that the page has sent its 5 s heartbeat (`/api/kiosk` `age_s`) within 60 s while the server has
  been up 60 s (90 s grace after start). Otherwise it stops Chromium and exits; cage exits and
  systemd restarts the kiosk. A server restart alone never trips it.
- udev `/etc/udev/rules.d/99-gokart-heltec.rules` makes `/dev/heltec`.
- `install.sh` writes `video=HDMI-A-1:1024x600M@60 video=HDMI-A-2:1024x600M@60`
  into `/boot/firmware/cmdline.txt` (the L7 has no EDID; the Pi 3 B's only port is HDMI-A-1),
  replacing any earlier `video=HDMI-A-*` tokens, so re-running it is safe.
- **Panel mounted ports-down** (`"rotate": 180` in `config.json`, then `sudo bash install.sh`
  and a reboot): the video= tokens get `,rotate=180` (the kernel turns the picture) and
  `/etc/udev/rules.d/98-gokart-touch-rotate.rules` gives every touchscreen the libinput
  calibration matrix `-1 0 1 0 -1 1` so touches land where the picture is. `"rotate": 0` removes
  both. **Documented, not enabled on kart 2, and not yet tried on a panel**: if cage/wlroots does
  not honour the kernel rotation, the fallback is cage's own `-r -r` in the kiosk unit (the touch
  rule would then have to go).

```sh
journalctl -u gokart-kart -f
journalctl -u gokart-kart-kiosk -b
sudo systemctl restart gokart-kart gokart-kart-kiosk
```

### Performance (Pi 3 B, kart 2, 2026-11-06)

`top` (% of one core, 400 = all four), Chromium all processes, page on the real kart (idle: no GPS
fix, nothing moving) and in demo mode (heading-up, 15 mph, track + zones, 3000 x 3044 photo):

| | idle | demo |
| --- | --- | --- |
| before the quality pass | 83 % | 192 % (215 % in a second run) |
| after (layer cache, diffed DOM, one scheduler, merged frames, no 60 fps CSS pulses, a11y off) | 24 % | 96 % |

Page main thread (CDP, demo): `render()` 37 ms avg / 69 ms p95 -> under 5 ms; map draws 13.5/s ->
<= 10/s (`map_fps`); long tasks 55 -> 16 per 24 s. kart_server.py: 2 % idle, 6-8 % in demo (the
demo simulator runs in-process). The page reports its own frame stats every 5 s in `/api/state`
`kiosk.stats` (`draws`, `draw_ms_avg`, `draw_ms_max`, `renders`, `render_ms_avg`, `layer_builds`).

How the map stays cheap: the photo, course, track band, zone shapes and lap line are drawn once
into an offscreen canvas (north-up, current zoom, <= 2048 px for the VC4 texture limit, centred on
the view with ~420 px margin) and only blitted, rotated, per frame. It is rebuilt when its content
changes, when the zoom settles (150 ms; meanwhile the old one is scaled), or when the view drifts
past the margin (~50 m at the follow zoom). The photo goes in only after `decode()`.

### Updating remotely

The SD kit authorizes the Mac's `~/.ssh/id_ed25519.pub` for `kart` and gives
`kart` passwordless sudo, so from the Mac on the same network:

```sh
kart/deploy.sh gokart-kart-2.local             # rsync kart/ -> /opt/gokart-kart, restart both services
kart/deploy.sh gokart-kart-2.local --install   # also re-run install.sh (packages, units, udev, cmdline)
```

deploy.sh keeps the Pi's `server/config.json`, `logs/` and `cache/` (excludes are
anchored to the top of kart/, checked locally with rsync into a scratch dir).
Change the kart id, pit URL or beginner mode by editing
`/opt/gokart-kart/server/config.json` and restarting `gokart-kart`.

### Config (`server/config.json`)

Every key is optional; `config.example.json` lists them all with a `_doc` entry each (keys
starting with `_` are ignored). A bad value (wrong type, out of range, unknown key) is replaced
by the default and reported in the journal (`CONFIG: ...`), the session log and `/api/state`
`server.config_warnings`; a config.json that is not valid JSON means defaults, not a crash loop.

| Key | Default | |
| --- | --- | --- |
| `kart_id` | 2 | 1-8, label only (the Heltec's id wins) |
| `pit_urls` | mDNS, then 192.168.151.240 | map package sources, in order |
| `map_poll_s` | 30 | pit poll period |
| `beginner_mode` | false | hide VESC fault alerts on the page |
| `map_orientation` | heading_up | or north_up (while following) |
| `follow_span_m` / `follow_resume_s` | 70 / 10 | follow zoom; resume after a touch |
| `mode_hold_ms` | 500 | mode button hold (0 = tap) |
| `map_fps` | 10 | max map redraws/s (1-30) |
| `bind` / `http_port` | 0.0.0.0 / 8081 | |
| `remote_cmd` | false | allow /cmd, /api/demo, sound test from other machines |
| `serial_port` / `tcp` | null / null | Heltec device (null = auto) / simulator |
| `status_poll_s` | 60 | `status` request period |
| `log_dir` / `cache_dir` | null | default `<kart>/logs`, `<kart>/cache/map` |
| `max_log_mb` | 800 | all session logs together |
| `game` / `gps_correction` | true / false | game layer; pit-base position correction |
| `demo` / `demo_max_s` | true / 180 | demo mode allowed; its length |
| `sound` / `sound_volume` | true / 0.8 | page sounds |
| `rotate` | 0 | install.sh only: 180 = ports-down mount |

### Troubleshooting

| Symptom | Look at |
| --- | --- |
| Black screen / no page | `systemctl status gokart-kart-kiosk`, `journalctl -u gokart-kart-kiosk -b` (watchdog restarts are logged there) |
| "USB: RECONNECTING" chip | `ls -l /dev/heltec`, `journalctl -u gokart-kart -f` (`link unavailable: ...`), `/api/state` `server.link.error`; replug the Heltec; the display reconnects every 2 s and never resends a command |
| "KART COMPUTER SILENT" | the Heltec is connected but quiet (rebooting, wedged): `/api/state` `rx_lines` not moving; `heltec.reboots_seen` |
| Map but no photo | `/api/state` `map.sync` (`reachable`, `last_error`); the photo only downloads on the pit Wi-Fi |
| "GAME OFF" | `/api/state` `game.off_reason` (no zones from the pit, off at the pit, `game: false` here) |
| KART chip red "2?" | `kart_id` in config.json differs from the Heltec's `id` |
| Laps not counting | `game.events` (`lap_ignored` with `why`), `game.pos.quality` (weak GPS pauses zones and laps) |
| Slow / jerky page | `/api/state` `kiosk.stats` (draws/s, ms per draw, layer rebuilds); `top` on the Pi |
| No sound | `GET /api/sound-test` and `?alsa=1` from the Pi (see "Panel speakers") |
| Config ignored | `journalctl -u gokart-kart \| grep CONFIG`, `/api/state` `server.config_warnings` |

### SD card kit (`sdcard/`)

`secrets.env` (gitignored) holds `KART_ID` (default 2), Wi-Fi (`McLab`, copied
from the pit kit), `PI_USER=kart`, `PI_PASSWORD` (same as the pit Pi), and
`SSH_PUBKEY_FILE`. `gen_cloudinit.py` builds `build/` (cloud-init user-data
with the kart/ payload, the ssh key and `config.json`; netplan Wi-Fi; meta-data).
`flash.sh` keeps every pit safety check (refuses disk0, non-external,
non-removable, virtual or read-only disks, shows the card's contents, needs the
disk id typed or `FLASH_CONFIRM` equal to it), verifies the image SHA-256,
writes with Raspberry Pi Imager, tolerates macOS auto-mounting the boot
partition, verifies the seed files (including the ssh key) and ejects.

```sh
diskutil list external physical          # find N
cd kart/sdcard && FLASH_CONFIRM=/dev/diskN ./flash.sh /dev/diskN
```

For another kart, set `KART_ID` in `secrets.env` first. Same image as the pit
(`~/studebaker-headunit/images/2026-10-06-raspios-trixie-arm64-lite.img.xz`).
Boot with the touchscreen on HDMI and its touch USB on the "5V IN + TOUCH"
port, Heltec on USB. Allow ~15 minutes (packages + one reboot). Logs:
`/var/log/gokart-kart-firstboot.log`, `sudo cloud-init status --long`.
Delete `sdcard/build/` after flashing; it contains secrets.

Known gaps: the Pi 3 B's Wi-Fi is 2.4 GHz only, so `McLab` must offer 2.4 GHz;
`optional: false` makes first boot wait for Wi-Fi.
