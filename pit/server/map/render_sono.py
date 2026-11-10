"""Rebuild the four offline SoNo street-map SVGs with only Python's stdlib.

Coordinates are projected linearly to the exact WGS84 outer bounds in map.sono.json,
matching the existing renderer. No route or vehicle positions are baked in.
Source geometry and ODbL notice: sono-map.geojson and SONO-MAP.md.
"""
import json
import math
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOUNDS = {"north": 41.106, "south": 41.091, "east": -73.405, "west": -73.427}
WIDTH = 1600
HEIGHT = round(WIDTH * (BOUNDS["north"] - BOUNDS["south"]) /
               ((BOUNDS["east"] - BOUNDS["west"]) * math.cos(math.radians(41.0985))))
PALETTES = {
    "light": ("#e7e9df", "#bad3d9", "#fffdf5", "#d49c4e", "#596a66", "#172323", "#795000"),
    "dark": ("#242f2a", "#153944", "#77887c", "#c3ae70", "#95aaa0", "#eef4ef", "#f2c35e"),
    "light-high": ("#f2f4eb", "#a5d2e4", "#506e59", "#795000", "#273c31", "#08130d", "#795000"),
    "dark-high": ("#06100a", "#064354", "#c5e0ce", "#ffe191", "#e3efe7", "#ffffff", "#ffe191"),
}


def project(point):
    lon, lat = point
    return ((lon - BOUNDS["west"]) / (BOUNDS["east"] - BOUNDS["west"]) * WIDTH,
            (BOUNDS["north"] - lat) / (BOUNDS["north"] - BOUNDS["south"]) * HEIGHT)


def path(points, closed=False):
    return " ".join(("M" if i == 0 else "L") + f"{x:.2f},{y:.2f}"
                    for i, (x, y) in enumerate(map(project, points))) + (" Z" if closed else "")


def rail_corridors(features):
    """Generalize parallel OSM rail ways into two quiet corridor symbols.

    Keep the original geometry in the GeoJSON; this is only cartographic styling.
    The main line runs east/west, the branch north/south. Sample before taking
    medians so short OSM ways do not get more weight than long ways.
    """
    for name, axis in (("New Haven Line", 0), ("Danbury Branch", 1)):
        bins = {}
        for f in features:
            if f["properties"]["kind"] != "rail" or f["properties"].get("name") != name:
                continue
            points = [project(p) for p in f["geometry"]["coordinates"]]
            for a, b in zip(points, points[1:]):
                count = max(1, math.ceil(math.dist(a, b) / 4))
                for i in range(count):
                    p = [a[j] + (b[j] - a[j]) * i / count for j in (0, 1)]
                    bins.setdefault(round(p[axis] / 8), []).append(p[1 - axis])
        points = []
        for k in sorted(bins):
            p = [0, 0]
            p[axis], p[1 - axis] = k * 8, statistics.median(bins[k])
            points.append(p)
        # A small moving average removes sampling steps, preserving the corridor.
        smooth = [[sum(p[j] for p in points[max(0, i-1):i+2]) / len(points[max(0, i-1):i+2])
                   for j in (0, 1)] for i in range(len(points))]
        yield smooth


def rail_pair(points, offset):
    for side in (-1, 1):
        out = []
        for i, p in enumerate(points):
            a, b = points[max(0, i-1)], points[min(len(points)-1, i+1)]
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy) or 1
            out.append((p[0] - side * offset * dy / length, p[1] + side * offset * dx / length))
        yield " ".join(("M" if i == 0 else "L") + f"{x:.2f},{y:.2f}" for i, (x, y) in enumerate(out))


def render():
    features = json.loads((HERE / "sono-map.geojson").read_text())["features"]
    for name, (bg, water, road, major, rail, ink, accent) in PALETTES.items():
        high = name.endswith("-high")
        scale = WIDTH / 716
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
                 '<title>South Norwalk, Connecticut — map data © OpenStreetMap contributors</title>',
                 '<desc>Data: https://www.openstreetmap.org/copyright · ODbL 1.0. Area reference; no course route.</desc>',
                 f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{bg}"/>']
        for kind in ("water", "building", "road", "rail", "label"):
            if kind == "rail":
                for corridor in rail_corridors(features):
                    for d in rail_pair(corridor, .55 * scale):
                        parts.append(f'<path class="rail-corridor" d="{d}" fill="none" stroke="{rail}" stroke-width="{.24*scale}" stroke-opacity="{.4 if high else .28}" stroke-dasharray="{2.5*scale} {3.5*scale}" stroke-linecap="butt"/>')
                continue
            for f in features:
                p, g = f["properties"], f["geometry"]
                if p["kind"] != kind:
                    continue
                if kind == "label":
                    # Labels are painted by the driver at a fixed screen size.
                    continue
                polygon = g["type"] == "Polygon"
                d = " ".join(path(ring, True) for ring in g["coordinates"]) if polygon else path(g["coordinates"])
                if kind == "building":
                    parts.append(f'<path class="building-outline" d="{d}" fill="{rail if polygon else "none"}" fill-opacity="{.09 if high else .06}" stroke="{rail}" stroke-width="{.32*scale}" stroke-opacity="{.4 if high else .28}" stroke-linejoin="round"/>')
                    continue
                is_major = p["class"] in ("primary", "secondary", "tertiary", "motorway")
                color = water if kind == "water" else rail if kind == "rail" else major if is_major else road
                width = .7 if kind == "rail" else (4.5 if high else 3.5) if is_major else (2.5 if high else 2)
                style = f'fill="{water}" stroke="none"' if kind == "water" and polygon else f'fill="none" stroke="{color}" stroke-width="{width*scale}"'
                if kind == "rail":
                    style += f' stroke-dasharray="{4*scale} {3*scale}" stroke-opacity=".45"'
                parts.append(f'<path d="{d}" {style} stroke-linecap="round" stroke-linejoin="round"/>')
        parts.append('</svg>')
        (HERE.parents[2] / "kart/server/static" / f"sono-{name}.svg").write_text("\n".join(parts) + "\n")


if __name__ == "__main__":
    render()
