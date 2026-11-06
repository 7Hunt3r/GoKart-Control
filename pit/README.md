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
| Server + simulator command loop (TCP and pty), outcome table, SSE stream, login, logs | **Tested on the Mac** (macOS, Python 3.13, pyserial 3.5) with `pit/tests/` and manual runs |
| Kiosk and operator pages | **Rendered on the Mac** in headless Chrome at 1024x600 and 1400 px wide against the simulator. Touch, press-and-hold and the kiosk detail sheet were not exercised with a real touchscreen |
| `gen_cloudinit.py` | **Run on the Mac** with a dummy Wi-Fi password; output inspected (user-data, two-network netplan, payload contents with no secrets/logs) |
| `flash.sh` safety checks | **Run on the Mac**: refuses `/dev/disk0` and a non-external disk. No card has been flashed with this kit |
| Real T-Beam S3 serial link, udev `/dev/tbeam` rule, reconnect on unplug | **Untested** (no T-Beam connected; product string in the rule is from the brief, not read from a board) |
| First boot on the Pi 4, apt package names, NetworkManager priorities | **Untested** |
| cage + Chromium kiosk on Raspberry Pi OS Trixie Lite, HDMI 1024x600, USB touch | **Untested**; see "Kiosk approach" for confidence |
| Python 3.11 compatibility (Trixie ships 3.13) | Not run on 3.11; code avoids 3.12+ features |
| Drivetrain numbers behind wheel mph, battery % curve | Contract values; **uncalibrated**, labelled in the UI |

Simulated karts are not real karts: whenever the source is the simulator the UI
shows a striped SIMULATION banner (driven by the simulator's own
`{"t":"log","msg":"SIMULATION"}` and `stat.fw = "sim-..."`).

## Layout

```text
pit/
  server/pit_server.py     HTTP server, SSE, auth, session log, wiring (stdlib + pyserial)
  server/pitcore.py        fleet state, staleness, derived values, command delivery (pure logic)
  server/link.py           T-Beam link: serial auto-find + reconnect, or TCP to the simulator
  server/static/           index.html (operator), kiosk.html (1024x600 touch), app.js, style.css, login.html
  server/map/              map.json + README (how to add CT ECO imagery; none bundled)
  server/config.example.json   copy to config.json (gitignored): shared password etc.
  sim/sim_tbeam.py         simulated T-Beam (pty or TCP), eight karts, honours commands
  tests/                   test_core.py (unit), test_e2e.py (simulator + server)
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
flags (`--fault 5 --nofix 6 --flaky 7 --offline 8 --novesc 0 --loss 0.1`) are
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
- **Commands.** Seq 1-255 rolling, persisted in `logs/cmdseq.json` across
  restarts, and never a seq that any kart currently reports as `last_cmd_seq`
  (a kart ignores a repeat of its last seq, so reuse would fake an ack).
  Retransmit every 1.5 s, up to 5 retransmits (6 sends), until every addressed
  kart that is online echoes the seq; per-kart outcomes come from `cmd_result`.
  A fleet command has eight outcomes: `pending`, `received`, `accepted`,
  `applied`, `no_vesc`, `rejected`, `unknown` (kart-reported), `no_ack`
  (no echo, outcome unknown), `offline` (not online when sent), `superseded`,
  `cancelled`, `link_lost`. Late echoes still update the table.
  Commands are serialized per kart (a queued command waits while the kart owes
  an echo, or up to 4 s while it reports `accepted`). STOP never queues: it
  cancels queued commands for its karts and marks in-flight ones superseded;
  retransmits of an older fleet command then go out addressed per kart so they
  cannot re-trigger a kart that has since handled the STOP. If the serial link
  drops, in-flight commands become `link_lost` and are never replayed after
  reconnect (requirements: no backlog execution).
- **Race.** Entered karts and race mode are shared server state. RACE ON sends
  the selected mode then START to entered karts and STOP to the others (fleet
  commands when all eight are entered). RACE OFF only clears the flag. The
  online/entered filters are per-browser presentation only.
- **Logs.** `logs/session-YYYYmmdd-HHMMSS.jsonl`, one file per server start:
  every serial line received (`rx`) and sent (`tx`), operator actions (`op`,
  with operator name and IP), command lifecycle and link events. Download from
  the operator page or `GET /api/log/download`.
- **HTTP.** `/` operator UI, `/kiosk`, `/events` (SSE, ~4 Hz full state),
  `GET /api/state`, `GET /api/log/recent?n=100[&lines=1]`, `POST /cmd`
  `{"cmd":"stop|start|mode|ping|slow|boost","kart":1-8|255,"arg":..}` (mode 0-2, slow mph 1-60, boost percent 10-100), `POST /api/race`
  `{"entered":[...],"mode":0-2,"active":true|false}`, `POST /zones`, `POST /zones/enable`,
  `POST /laps/reset` (see "Game zones and laps"), `GET /map/package.json`, `GET /map/zones.json`,
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
- **Editing (operator page `/`).** `ZONES` above the map enters edit mode: `BOOST PAD` / `MUD` /
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
  every page runs the same countdown: a fixed overlay of 5 red lights going out one per second
  (or "GO in N s" for longer counts), then GO green for 2 s, with WebAudio beeps (browsers only
  allow sound after the first touch/click on that page). Kart cards show `GRID` while a kart's
  live scale is 0.0 and it is not stopped (the hold) and flash `GO` when it comes back.
- **Laps.** The pit times laps itself from `tele` fixes: a lap counts when the segment previous
  fix -> current fix crosses the lap line (either direction) and `min_lap_s` (default 20) has
  passed since the previous crossing; the first crossing starts the clock. The crossing time is
  interpolated along the segment when the two fixes are under 5 s apart. Ranking: laps desc,
  then earlier last crossing. Shown as a leaderboard panel (operator side panel, kiosk RACE tab,
  phone KARTS tab), a collapsible overlay on every map, and `P# L# best` on each kart card.
  `/api/state` has `fleet.game`, `fleet.laps` (ranked) and `karts[].lap`. Lap state is memory only
  (server restart clears it) and is also cleared when the lap line moves.
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

## On the Pi

Flash a card with [`sdcard/`](sdcard/README.md); first boot installs everything.
Pi: hostname `gokart-pit` (`gokart-pit.local` via avahi), user `pit`, app in
`/opt/gokart-pit`, config `/opt/gokart-pit/server/config.json` (mode 600).

Services:
- `gokart-pit.service`: `python3 /opt/gokart-pit/server/pit_server.py` as `pit`
  (group dialout), `Restart=always`. No Wi-Fi dependency.
- `gokart-kiosk.service`: cage + Chromium on tty1 (below).
- udev: `/etc/udev/rules.d/99-gokart-tbeam.rules` makes `/dev/tbeam`.

```sh
journalctl -u gokart-pit -f
journalctl -u gokart-kiosk -b
sudo systemctl restart gokart-pit gokart-kiosk
udevadm info -a -n /dev/ttyACM0 | grep -iE 'idVendor|idProduct|product'   # check the rule's match
```

### Updating the app later

From the Mac, with the Pi on the same network:

```sh
pit/deploy.sh                    # rsync pit/ -> pit@gokart-pit.local:/opt/gokart-pit, restart services
pit/deploy.sh gokart-pit.local --install   # also re-run install.sh (packages, units, udev)
```

deploy.sh keeps the Pi's `server/config.json`, `logs/`, map imagery and its edited `map/zones.json`. By hand:
`rsync -av --exclude logs/ --exclude server/config.json pit/ pit@gokart-pit.local:/opt/gokart-pit/`
then `ssh pit@gokart-pit.local sudo systemctl restart gokart-pit gokart-kiosk`.
Change the web password by editing `/opt/gokart-pit/server/config.json` and
restarting `gokart-pit` (logged-in browsers must log in again).

### Kiosk approach (untested on hardware; medium confidence)

`gokart-kiosk.service` runs `cage -d -s -- start-chromium.sh` as `pit` on tty1,
with `PAMName=gokart-kiosk` (`/etc/pam.d/gokart-kiosk` includes
`pam_systemd`). That PAM session registers a logind session on seat0/tty1, and
cage's libseat logind backend uses it to open DRM and input devices without
root and without seatd. This is the pattern from cage's own "Starting Cage on
boot with systemd" wiki page; `getty@tty1` is disabled (SSH and tty2-6 remain).
Chromium runs with `--kiosk --ozone-platform=wayland --touch-events=enabled`
and a dedicated profile; the service waits for `/healthz` before launching and
restarts if Chromium exits.

Why medium: the pattern is documented and used on Debian/Arch, but it has not
been run on this OS image. Specific risks to check first boot:
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
