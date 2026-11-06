# Game zones (Mario Kart layer) — format contract

Zones live in `pit/server/map/zones.json` next to `map.json`. The pit serves the file at
`GET /map/zones.json` (no login, like the map) and lists it in `GET /map/package.json` as
`"zones": "/map/zones.json", "zones_version": <int>`. The kart Pi (kart/server/mapsync.py) re-fetches
the file whenever `zones_version` changes and keeps it in its map cache; it evaluates zones locally
from its own GPS (the Heltec `self` line, 2 Hz) so the game keeps working with no radio and no Wi-Fi.
The pit evaluates the same file against telemetry for the leaderboard. Editing happens on the pit
operator page (`POST /zones`, login) which rewrites the file and bumps `zones_version`.

```json
{
  "version": 3,                 // bumped on every save (= zones_version in package.json)
  "enabled": true,              // master switch: false = karts ignore every zone, pit hides laps
  "zones": [
    {"id": "b1", "type": "boost", "name": "Boost pad", "shape": "circle",
     "lat": 41.093990, "lon": -73.416370, "r_m": 12,
     "scale": 1.0, "secs": 5, "cooldown_s": 20},
    {"id": "m1", "type": "mud", "name": "Mud", "shape": "polygon",
     "points": [[41.0940, -73.4165], [41.0941, -73.4163], [41.0939, -73.4162]],
     "scale": 0.5},
    {"id": "lap", "type": "lapline", "name": "Start / finish", "shape": "line",
     "a": [41.09400, -73.41660], "b": [41.09400, -73.41610], "min_lap_s": 20}
  ]
}
```

Rules every implementation follows:

- **Coordinates** are WGS84 decimal degrees (same as telemetry). Distances in metres on the local
  tangent plane (use the existing `projection()` / `R_EARTH` helpers, cos(lat) scaling for lon).
- **Minimum sizes**: circle `r_m` >= 8, polygon with >= 3 points; the editor refuses smaller. GPS is a
  few metres off, so zones under ~10 m feel random.
- **boost**: on entry the kart Pi sends the Heltec `boost <secs> <scale>` over USB (firmware
  2026-11-06+: `scale` 0.10-1.00, `secs` 1-30; the Heltec auto-reverts to the mode scale when the
  time is up, and STOP/START cancel it). `scale` is clamped to 1.0 (never above SPORT or above the
  controller's own limits, which the firmware echoes). Re-trigger only after the kart has left the
  zone and `cooldown_s` (default 20) has passed.
- **mud**: while inside, the kart Pi sends `boost 3 <scale>` and repeats it every 2 s so the cap
  lasts exactly as long as the kart is in the zone (plus at most 3 s). On leaving it simply stops
  repeating; the Heltec reverts to the mode scale on its own within 3 s.
- **lapline**: a lap counts when the kart's path (previous fix -> current fix) crosses segment a-b in
  either direction and at least `min_lap_s` has passed since the previous crossing (default 20 s).
  First crossing starts the clock (lap 0 -> "lap 1 running"). Each kart keeps: lap count, last lap,
  best lap, current lap elapsed. The pit does the same per kart from `tele` lines and ranks karts by
  laps completed, then by time of the last crossing (earlier = ahead).
- **Entry hysteresis**: a kart is "inside" when within the shape; "outside" again only once it is
  more than 3 m outside the edge (avoid flapping on the boundary).
- **enabled=false** or a missing file: no triggers, no lap timing; HUD shows the game as off.
- **Safety**: zones never raise the scale above 1.0, never send STOP/START/mode, and a pit STOP
  always wins (the firmware clears the override). The pit's master switch is the race director's
  kill for the whole game. SLOW from the pit is not affected by zones (the ERPM cap stays).

Zone ids are short strings unique within the file; the editor generates them. Unknown `type`
values are ignored by karts and shown greyed by the pit so an old kart build never misreads a new
zone kind.

## Race start (added 2026-11-06 evening)

Command `race` (GK_CMD_RACE = 7, arg = seconds until GO, 1-30, default 5) to all karts. Each Heltec
holds its kart at zero power at once (no brake) and releases at `issued_utc + arg` on the shared
GPS/beacon clock, so every kart launches on the same instant; a kart without time releases `arg`
seconds after receipt. The Heltec prints `{"t":"race","kart":N,"event":"countdown","go_in_ms":N,
"secs":S,"go_utc":U}` on receipt and `{"t":"race","kart":N,"event":"go"}` at release; the kart display
shows the countdown lights from these and resets its laps at GO. A pit STOP during the hold keeps the
kart stopped (GO releases nothing until START); START releases early. The pit resets all lap timing
when it sends `race`, and laps count from the first lap-line crossing after GO.

## Item boxes (added 2026-11-06 evening)

Zone `type: "item"` (circle or polygon, same sizes as boost). On entry the KART rolls a random item
locally and applies it at once, showing a short roulette on the HUD: 45 % BOOST (`boost 5 1.0`),
15 % SUPER (`boost 8 1.0`, star animation), 25 % MUD (`boost 3 0.5`), 15 % nothing ("empty box").
Same cooldown/exit rules as boost pads (`cooldown_s`, default 15). The pit draws item boxes as a
blue/white "?" box and never evaluates their outcome; the kart logs `{"kind":"game","event":"item",
"item":"boost|super|mud|empty"}` to its session log. Items never raise the scale above 1.0 and are
ignored while STOPPED, in grid hold, or with the game off.
