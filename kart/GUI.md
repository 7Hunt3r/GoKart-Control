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
python3 -I kart/sim/run-sim.py --map pit/server/map/map.sono.json
```

The existing runner starts a fake pit, simulated kart and the real driver server
on local ports, then opens the display. Add `--no-open` to print the address
without opening a browser. Ctrl-C stops it. Refresh after editing files in
`kart/server/static/`; no build step is needed for layout changes.

The street artwork is South Norwalk (SoNo), Connecticut. The simulator supplies
the moving kart, lap route and game overlays. These are test data, not a surveyed
or approved route. The operational pit's `map.json` and its presets are untouched.

## Graphics

- At the target 1024×600 resolution, the map fills the main area with roughly 70%
  clear of the instrument panel. A charcoal overlay with 7% transparency carries
  the readings; a faint CRT texture, seven-segment numerals, segmented labels,
  cyan accents and amber mode selection add restrained retro racing styling.
  Full-width alerts remain above it. At narrow phone widths the panels stack.
- **AREA** opens a South Norwalk street overview; **FOLLOW** returns to the
  existing driver camera. These controls only move the map camera.
- Supporting readings show existing pack voltage, electrical RPM, ESC temperature
  and input power calculated from reported battery current × voltage. Missing or
  stale new readings show `--`. Battery warning thresholds are retained. Rising
  bars show pit signal; the small face reflects link/alert state, not a safety verdict.
- **Display** opens light/dark, high contrast and brightness controls. The map
  has a matching palette for each light/contrast combination, with no live tile
  server dependency. The small OpenStreetMap contributor credit remains visible.
- Contrast and the existing **SUN** button stay synchronized. Preferences persist
  only in browser storage; no controller/configuration request is sent.
- Brightness adjusts page rendering from 30–100% (80% is neutral). It does not
  operate the physical backlight. The settings panel stays readable when dimmed.
- Existing mode press-and-hold, game actions, alerts, sound, map following,
  heading and zoom handlers are retained. No server, firmware or telemetry code
  is changed. No kart has been connected or deployed to for this graphics work.

`retro.css` contains this presentation layer. `instruments.js` reads the same
state as the existing display and makes no network requests. DSEG fonts by
[keshikan](https://github.com/keshikan/DSEG) (v0.46, SIL Open Font License 1.1)
are embedded unchanged in `font-segments.css`; the license is bundled beside it.
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
confirmed the state/render, command, game and map-input handlers are retained.
STOP/fault, disconnected and race-countdown screen fixtures also rendered with
their alerts visible and no JavaScript errors.
These are simulator checks; physical screen performance, touch and outdoor
readability require later hardware review.
