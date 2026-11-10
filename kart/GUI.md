# Hunter's driver display preview

This graphics branch starts from Eamon's actual driver display at
`b48c5ad3a7eca250ee8e119c96c344ee98f68587` (`eamon/firmware-lora-starter`).
Its publishing destination is **7Hunt3r/GoKart-Control**, branch
`gui/driver-map-display`. A fork records its relationship to the shared project,
but saving here does not update that project. No upstream PR or deployment is
part of this work.

## Open the simulator

From the repository root, with Python 3.9+:

```sh
python3 -I kart/sim/run-gui.py
```

This graphics-only runner starts a fake pit, simulated kart and the real driver server
on local ports, then opens the display. Add `--no-open` to print the address
without opening a browser. Ctrl-C stops it. Refresh after editing files in
`kart/server/static/`; no build step is needed for layout changes.

The street artwork is South Norwalk (SoNo), Connecticut. `kart/sim/zones.gui.json`
supplies one sample route for both the track overlay and all three fake karts,
using the existing simulator's `PolyPath` input. The sample traces North Main,
Marshall, North Water and Washington streets in the bundled OSM extract. It is
an illustrative graphics fixture, not a designated, surveyed or approved course.
This replaces the old preview's mismatched kart loop and default pit track.
The production server, simulator engine, operational `map.json` and existing
map/zone presets are unchanged.

## Graphics

- At the target 1024×600 resolution, the map fills the main area with roughly 70%
  clear of the instrument panel. A smoky blue-gray overlay with 6% transparency carries
  the readings; a faint CRT texture and seven-segment numerals retain the retro
  character. Braun/Sony-inspired labels use a consistent grid and spacing, with
  supporting readings sized to fit their cells (22 px at 1024×600, up to 38 px on larger screens).
  The speed gauge is a true 180-degree semicircle with a heavier arc and the
  original bold DSEG7 font. Visible digit shapes are centered over MPH; the blank
  bearing beside a segmented 1 does not shift the reading. Wider three-digit
  readings shrink to preserve clearance inside the arc. ECO is green,
  NORMAL blue and SPORT pink; a solid fill identifies the active mode.
  Full-width alerts remain above it. At narrow phone widths the panels stack.
- The default is a stationary, zoomed-out view of the whole course. The camera
  fits the track into the clear map area and does not chase or rotate with karts.
  **North up** starts off: the course's first straight sets a fixed orientation.
  The toggle switches to geographic north; **TRACK** restores the course framing.
  **AREA** opens a north-up South Norwalk overview; **ALL** frames the fleet once.
  Manual pan and pinch zoom preserve the selected orientation. These controls
  affect presentation only.
- Buildings use thin, subdued outlines and faint fills from the same OSM extract.
  Place labels stay at a fixed screen size; rail corridors use two thin, lightly dashed lines. All kart trails
  draw before all numbered circles, so a later trail cannot cross over a label.
- Supporting readings show existing pack voltage, electrical RPM, ESC temperature
  and input power calculated from reported battery current × voltage. Missing or
  stale new readings show `--`. Battery warning thresholds are retained. Rising
  bars show pit signal; the small face reflects link/alert state, not a safety verdict.
- **Display** opens light/dark, high contrast and brightness controls. The map
  has a matching palette for each light/contrast combination, with no live tile
  server dependency. The small OpenStreetMap contributor credit remains visible.
  Settings close after three seconds without interaction. Adjusting a control
  restarts the timer; an active touch/slider drag keeps the panel open until release.
- Lap timing is docked below the profile controls in the instrument panel, with
  a compact digital timer and aligned LAST/BEST readings. It does not cover the map.
- Supporting measurements sit in a distinct background with thin cell dividers.
  Their regular-weight segmented digits and extra cell padding separate them from
  the bold speed readout. Taller screens give these cells more space and move the
  drive modes down; the 1024×600 layout keeps the normal panel visible without scrolling.
- Contrast and the existing **SUN** button stay synchronized. Preferences persist
  only in browser storage; no controller/configuration request is sent.
- Brightness adjusts page rendering from 30–100% (80% is neutral). It does not
  operate the physical backlight. The settings panel stays readable when dimmed.
- Existing mode press-and-hold, game actions, alerts and sound are retained.
  The map camera follows Hunter's stationary-view preference. No server, firmware
  or telemetry code is changed. No kart has been connected or deployed to for this
  graphics work.

`retro.css` contains this presentation layer. `instruments.js` reads the same
state as the existing display and makes no network requests. DSEG fonts by
[keshikan](https://github.com/keshikan/DSEG) (v0.46, SIL Open Font License 1.1)
are embedded unchanged in `font-segments.css`, including the original Classic
Bold face; the license is bundled beside it.
There is no online font service or new server dependency.

Map sources, rebuilding and attribution are in
[the map asset guide](../pit/server/map/SONO-MAP.md).

## Validation

Run the project's required offline suites before publication:

```sh
tools/run_tests.sh
```

Browser checks cover the 70% layout at 1024×600, all four map palettes, SUN and
contrast synchronization, persistent brightness, no new commands from settings,
the existing mode hold gesture, narrow-screen layout and JavaScript errors.
On 2026-11-10, all seven offline suites passed (262 tests, six existing skips).
The simulator browser checks above passed, including the unchanged `/cmd` mode
payload after a hold and no command after a short tap. Source comparison also
confirmed that vehicle command and game-action handlers are retained.
STOP/fault, disconnected and race-countdown screen fixtures also rendered with
their alerts visible and no JavaScript errors.
The later camera/typography refinement also checks stable course framing during
moving telemetry, North up off by default, orientation-preserving pan, complete
track visibility, a mathematically semicircular gauge and trail-before-marker
paint order. Vehicle command handlers remain unchanged.
The bold instrument revision checks every speed from 0 through 199 for optical
centering and arc clearance, verifies that the normal instrument panel fits
without scrolling at 1024×600, and checks that all 35 route segments coincide
with source-map streets and all fake karts use that same path.
These are simulator checks; physical screen performance, touch and outdoor
readability require later hardware review.

## Pit information

This page is the driver display. The pit dashboard is a separate screen with the same shared visual system:
`pit/server/static/kiosk.html` and the operator page `index.html`. It includes all
eight kart slots, connection state, battery, speed, temperature, faults, GPS, radio,
commands and race status. See `pit/GUI.md` for the matching graphics-demo runner.
The independent pit simulator uses its own eight-kart sample data on the same SoNo
route; it is not the same live feed as this three-kart graphics fixture. No vehicle
or race behavior is changed by this display branch.

## Shared visual system

`kart/server/static/display-theme.css`, `display.js`, `font-segments.css` and the
four SoNo SVGs are the source assets for both static roots. After changing a shared
asset, run `python3 tools/sync_gui_assets.py`; use `--check` to verify parity. This
keeps the two existing servers unchanged. The shared display dialog supports light,
dark, high contrast and page brightness, and closes after three idle seconds.

The softened electronic concept uses translucent smoky blue-gray panels, pale cyan
numerals with a restrained glow in standard dark mode, fine borders and mint/blue/pink
profile controls. High contrast removes the glow/texture and makes panels opaque.
Day mode retains dark readable digits on cool pale blue panels. The working layout,
70% clear map, centered speed and button positions are retained; the map-control
buttons are slightly larger (58 px wide, 52 px tall). Alert colors and status text
retain their original meaning. Generated concept images are design references,
not screenshots of implemented functionality or geographic evidence.

The final reference-matching pass retains the working layout and larger map controls,
uses a yellow driver pointer, quiet trail lines and more widely spaced chevrons, and
docks lap timing below the profile controls in the instrument panel. Supporting labels
are smaller regular-weight text; readings stay inside their individual grid cells.
