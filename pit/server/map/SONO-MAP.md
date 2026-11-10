# SoNo GUI map

`map.sono.json` is a separate preview preset for Hunter's graphics branch. Select
it with `python3 -I kart/sim/run-sim.py --map pit/server/map/map.sono.json` from the
repository root. It does not replace the active map, shop or Manresa presets.
There is no course baked into the artwork; the simulator supplies a test route.

The 2026-11-10 refinement adds 2,024 building-outline features from the same cached
OSM extract, including relation-member outlines. They have no fill, a thin muted
stroke and lower contrast than streets. No address metadata is included. Map
package version 1002 updates the PNG fallback; the active operational map remains unchanged.

`sono-map.geojson` is the local South Norwalk OpenStreetMap extract, with source
and license metadata. Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright),
available under the [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
The derivative geometry is included here under that license. Keep attribution
visible when displaying the map. The earlier aerial imagery notes below concern
other presets.

`render_sono.py` makes four SVG palettes in `kart/server/static/` using Python's
standard library. The coordinates map linearly to the exact north/south/east/west
edges in `map.sono.json`, matching the existing renderer's projection. Rebuild:

```sh
python3 pit/server/map/render_sono.py
node pit/server/map/render_sono_png.cjs
```

The second development-only step needs Playwright and local Chrome. It renders
`sono-light.png`, a fallback accepted by the existing map cache. All generated
assets are committed; users and the Pi need no Node/browser build tools to use
them. When the SoNo preset is active, the driver chooses a bundled SVG palette
for the selected appearance. Other map packages use their existing images.
