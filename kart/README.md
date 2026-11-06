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
| Real Heltec over USB on a Pi 3 B, `/dev/heltec` udev rule, reconnect on unplug | **Untested** on a kart Pi (same 303a:1001 ESP32-S3 USB as the pit T-Beam, whose rule works) |
| First boot, apt packages, cage + Chromium kiosk, HDMI 1024x600 pin, USB touch **on a Pi 3 B** | **Untested** (the recipe works on the pit Pi 4; Pi 3 B differences: 1 GB RAM, VC4 GPU, 2.4 GHz-only Wi-Fi, single HDMI) |
| Map redraw cost on a Pi 3 B (3000x3044 JPEG, <=10 fps while moving) | **Untested**; north-up draws only the visible part of the image |
| SLOW banner | From `slow_mph` in `self` lines (firmware 2026-11-06+), else **inferred** from `cmd` echoes; see "SLOW" below |
| Game layer (zones sync, boost/mud commands, laps, HUD) | **Tested on the Mac** (unit tests + simulator course e2e, HUD rendered headless at 1024x600). `boost` against a real VESC **not** exercised from the Pi; real pit zones were `enabled: false` at deploy |

Simulated data is labelled: the simulator announces itself and the page shows a
striped SIMULATION banner.

## Layout

```text
kart/
  server/kart_server.py   HTTP + SSE + /cmd + session log + wiring (stdlib + pyserial)
  server/kartcore.py      parsing, staleness, battery/mph, SLOW inference, command tracking (pure logic)
  server/link.py          Heltec link: serial auto-find + reconnect, or TCP to the simulator (from pit/)
  server/mapsync.py       pit map package -> local cache (cache/map/), served at /map/<file>
  server/static/          index.html, app.js, style.css (driver page, no CDN)
  server/config.example.json   copy to config.json (gitignored)
  sim/sim_heltec.py       simulated kart Heltec (pty or TCP), honours mode/slow/stop/start/status/boost
  sim/fake_pit.py         fake pit map endpoint for local runs (+ zones.json)
  sim/zones.course.json   SIMULATION game zones matching sim_heltec.py --course
  tests/                  test_core.py, test_mapsync.py, test_e2e.py
  install.sh              runs ON THE PI; idempotent install/update
  deploy.sh               Mac -> kart Pi rsync + restart
  systemd/, kiosk/, udev/ units, PAM file + Chromium launcher, /dev/heltec rule
  sdcard/                 SD card kit (flash.sh, gen_cloudinit.py, firstboot.sh, cloud-init/)
  logs/, cache/           runtime (gitignored)
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
python3 -I kart/tests/test_core.py
python3 -I kart/tests/test_mapsync.py
python3 -I kart/tests/test_e2e.py
```

## Behaviour

- **Input**: the Heltec's USB lines (`gkTelemetryToJson`): `self` (2 Hz, this
  kart), `fleet` (each other kart heard; `rssi`/`snr` as heard by this kart),
  `cmd`, `log`, `ble`, `status` (the server sends `status` on connect and every 60 s).
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
- **SLOW** (orange): telemetry has no SLOW flag (all 8 flag bits are used), so
  the display infers it from command echoes: a SLOW the firmware took
  (accepted/applied/unknown) stays on until a START is echoed or the Heltec
  reboots (uptime drops). After a display restart with a Heltec that has been
  up more than 2 minutes, SLOW is "unknown" and no banner shows until the next
  SLOW/START.
- **Fault**: `VESC FAULT n NAME` while VESC data is fresh, hidden when
  `beginner_mode` is on (driver presentation only; logs and the pit keep it).
- **Map**: imagery from the cached package placed by its bounds, pit marker,
  course line, other karts as numbered dots (stale = grey dashed + age), own kart
  as a big arrow along the GPS heading, trails (own 120 s, others 30 s). FOLLOW
  keeps the own kart centred (north-up, or `heading_up` in config); a touch,
  drag, pinch or wheel pauses it and it resumes 10 s after the last touch. FOLLOW
  off = free pan. ALL fits every kart + the course. Redraws on each SSE frame
  (5 Hz) and at most `map_fps` (10) between frames, only while something moves.
- **Logs**: `logs/session-YYYYmmdd-HHMMSS.jsonl`, one per server start: every
  serial line received (`rx`) and sent (`tx`), each request (`op`), echoes,
  link and map events. Oldest files are deleted beyond `max_log_mb` (800).
- **HTTP** (0.0.0.0:8081): `/`, `/events`, `/api/state`, `/map/zones.json`, `POST /cmd`
  (`{"cmd":"mode","arg":0-2}`, `slow` [mph], `stop`, `start`, `ping`, `status`;
  localhost only unless `remote_cmd: true`), `POST /api/map/refresh`,
  `/map/package.json`, `/map/<file>`, `/healthz`. Remote browsers can watch the
  page but not send commands.

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

**Driver page.** Black speedometer card (0-30 mph arc, GPS mph; wheel mph,
labelled WHEEL, only when there is no GPS fix), the mode as a gear badge (E / N / S,
dashed blue while a request is pending), a BOOST meter (the live VESC current
`scale` against the requested mode's scale, marked: amber "BOOST!" above it, brown
"MUD" below it, with the seconds left), a lap panel on the map (lap N, current,
last, best) or a small GAME OFF chip, zones on the map (boost = amber with
chevrons, mud = brown with dots, lap line = checkered; greyed when the game is
off), and a short full-screen flash + synthesized sound (WebAudio, no files) on
boost, mud and each lap. `"sound": false` in config.json mutes it. The safety
banners (STOPPED, SLOW, connection, SIMULATION) sit above the flash and keep their
size; while one shows the speedometer shrinks so the mode buttons stay on screen.
The kiosk runs Chromium with `--autoplay-policy=no-user-gesture-required` so the
sounds play with no touch first.

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
  `kart` (group dialout), `Restart=always`, no Wi-Fi dependency.
- `gokart-kart-kiosk.service`: cage + Chromium (`--kiosk http://127.0.0.1:8081/`)
  on tty1 via the PAM/logind recipe from `pit/kiosk`. Extra Chromium flags for
  the Pi 3 B (e.g. `--disable-gpu` if the GPU path misbehaves) go in
  `/etc/default/gokart-kart-kiosk` as `GOKART_CHROMIUM_FLAGS="..."`.
- udev `/etc/udev/rules.d/99-gokart-heltec.rules` makes `/dev/heltec`.
- `install.sh` appends `video=HDMI-A-1:1024x600M@60 video=HDMI-A-2:1024x600M@60`
  to `/boot/firmware/cmdline.txt` (the L7 has no EDID; the Pi 3 B's only port is HDMI-A-1).

```sh
journalctl -u gokart-kart -f
journalctl -u gokart-kart-kiosk -b
sudo systemctl restart gokart-kart gokart-kart-kiosk
```

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
