# Hunter's pit graphics preview

This is presentation work on `7Hunt3r/GoKart-Control`, branch
`gui/driver-map-display`. It does not deploy to hardware, update the upstream
project, or change the pit server, vehicle commands, race rules or firmware.

## Working demo

From the repository root, with Python 3.9+:

```sh
python3 -I pit/sim/run-gui.py
```

The runner prints the local address and opens `/kiosk`. Use the same address with
`/` for the full operator page, `/m` for phone/tablet, or `/login` for the login
design. `--port 64803 --no-open` selects a port and leaves browser opening to you.
Ctrl-C stops the preview processes. Refresh after editing the static files.

The existing eight-kart simulator provides battery, speed, temperature, GPS,
radio, faults, stale and offline states, and command responses. Its original
source is unchanged. A process-local position adapter follows the driver's
`kart/sim/zones.gui.json` route, with the same offline South Norwalk map. The
route is an illustrative public-street graphics fixture, not an approved or
surveyed driving course. Temporary config, map and logs are isolated from the
operational files. No serial, LoRa or real vehicle connection is opened.

This pit demo and the driver demo use independent sample feeds. Their kart
readings and lap times therefore do not match, although their route does.

## Consistent graphics

- Driver, kiosk, operator, mobile, login, kart detail, command outcomes, race
  results, map leaderboard and editing drawers share the same fonts, colors,
  fine separators and touch-control styling.
- Primary speed/battery readings use locally bundled DSEG7 numerals. Labels use
  Helvetica-family sans serif, with smaller supporting text and room between
  groups. Full telemetry remains accessible in each kart's detail view.
- Smoky blue-gray surfaces and a restrained cyan electronic glow separate the
  instruments from the map. ECO / NORMAL / SPORT use mint, blue and pink.
  Fault, stopped, stale and unconfirmed states retain distinct colors and text.
- Light, dark, high contrast and page brightness use the same browser-local
  Display controls as the driver. The dialog hides after three idle seconds;
  interaction resets the timer and a held slider remains open. These controls
  send no vehicle commands. Preferences persist per browser origin.
- On small pit screens, the fleet list scrolls instead of compressing readings
  to fit eight cards. The map remains prominent. The map leaderboard has a dedicated strip above the canvas, so it does not
  cover the track. It scrolls horizontally where needed; full race standings
  are available in the Race view.
- All trails and headings render before all opaque kart number circles, so a
  later kart's line cannot cover an earlier number, even for stale markers.
- The SoNo map uses subdued building shading and pairs of lightly dashed rails.
  It shares the driver map assets and retains OpenStreetMap attribution.

Shared sources live in `kart/server/static`. Run
`python3 tools/sync_gui_assets.py` after changing shared assets, or add `--check`
to verify the two served copies match. The pit-specific layout is in
`pit/server/static/retro.css`; original base styles remain available underneath.

## Review boundary

Local browser checks cover layouts, theme switching, map layering, display idle
behavior, representative faults/offline states and unchanged command payloads.
The repository's seven test suites are run before publishing this graphics work.
Physical display performance, touch and outdoor legibility still need hardware
review before rollout. Nothing in this demo connects to or deploys onto a kart.
