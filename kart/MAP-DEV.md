# Kart display map — dev guide

Welcome, Hunter. This is for working on the **look and feel of the kart's driver-display map**:
the aerial image, the track, the kart arrow, colours, zoom/pan/rotate feel, legibility in
sunlight. Nothing else. You do it all in a **local simulator** on your own laptop — no kart, no
radio, nothing you touch here can reach a real kart until your work is reviewed and deployed.

## Set up (2 minutes)

You need **Python 3.9+** (standard library only — no pip installs) and a browser. Then:

```sh
git clone git@github.com:colinfost3r/GoKart-Control.git
cd GoKart-Control
git checkout -b hunter/map-polish        # your own branch
python3 kart/sim/run-sim.py              # opens the display in your browser
```

`run-sim.py` starts three fake processes (a stand-in pit, a fake kart driving a lap, and the real
display server) and opens the driver screen. The kart drives the course on its own, so the map
moves and the speed changes like the real thing. Ctrl-C stops everything.

```sh
python3 kart/sim/run-sim.py --map pit/server/map/map.manresa.json   # the real race track (SkyFi aerial)
python3 kart/sim/run-sim.py --no-course                             # kart sits still
python3 kart/sim/run-sim.py --port 8080                             # fixed port
```

**There is no build step.** Edit a file under `kart/server/static/`, save, and refresh the browser.

The display is a **1024 x 600** kiosk in the kart. Size your browser window to that — it's what the
driver sees. Try the FOLLOW button, pinch/scroll zoom, and the SUN button (top right): SUN mode is
the high-contrast theme for direct sunlight, and the map has to stay readable in it.

## The files that are "the map"

| File | What's in it |
| --- | --- |
| `kart/server/static/app.js` | The map section (search `------ map ------`, ~line 970 to the end of the draw loop): the projection, the aerial image layer, `drawTrack`, `drawZones` / `drawZoneMarks`, the kart arrow, follow/zoom/heading-up rotation, and the per-frame canvas render. This is where most of the look lives. |
| `kart/server/static/style.css` | Colours, fonts, the HUD over the map, and the `body.sun` (SUN mode) rules that boost map contrast. |
| `pit/server/map/*.jpg` + `map.*.json` | The aerial images and their map packages (`map.shop.json` = the shop yard, `map.manresa.json` = the real track). The JSON sets the image, the geo origin/bounds and the course. |
| `pit/tools/enhance_map.py` | How a raw aerial is turned into the `*_hc.jpg` the display shows (contrast stretch, gamma, local contrast/unsharp, saturation). Re-run it if you want the imagery itself to look better. |

## Stay in your lane

Please only touch the files above. **Out of scope** (leave alone): `firmware/`, the pit server
logic, the game layer, the kart server's Python, and anything about LoRa / VESC / sound / the
multiplayer game. If a map idea needs a change outside these files, flag it in your PR and we'll
pair on it — don't reach into the firmware or server logic.

## Getting your work onto the karts

You never touch a live kart. The path is:

1. Commit to your `hunter/map-polish` branch and push it.
2. Open a pull request. Say what you changed and drop before/after screenshots at 1024 x 600
   (normal **and** SUN mode).
3. Eamon or Colin reviews it, and once it's merged we deploy it to the karts with
   `kart/deploy.sh` (our job, not yours).

Before you open a PR, from the repo root:

```sh
tools/run_tests.sh      # should end "ALL PASSED"
git diff --check        # no stray whitespace
```

Map changes are front-end, so the tests rarely care, but run them anyway so a PR is always green.

Questions about the data the page gets (what's in `S.map`, `S.self`, the zones, the game) — ask
Eamon, or open `http://127.0.0.1:<port>/api/state` in the browser while the sim runs to see the
whole live state object.
