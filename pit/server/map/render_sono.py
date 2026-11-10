"""Rebuild the four offline SoNo street-map SVGs with only Python's stdlib.

Coordinates are projected linearly to the exact WGS84 outer bounds in map.sono.json,
matching the existing renderer. No route or vehicle positions are baked in.
Source geometry and ODbL notice: sono-map.geojson and SONO-MAP.md.
"""
import html
import json
import math
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


def render():
    features = json.loads((HERE / "sono-map.geojson").read_text())["features"]
    for name, (bg, water, road, major, rail, ink, accent) in PALETTES.items():
        high = name.endswith("-high")
        scale = WIDTH / 716
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
                 '<title>South Norwalk, Connecticut — map data © OpenStreetMap contributors</title>',
                 '<desc>Data: https://www.openstreetmap.org/copyright · ODbL 1.0. Area reference; no course route.</desc>',
                 f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{bg}"/>']
        for kind in ("water", "road", "rail", "label"):
            for f in features:
                p, g = f["properties"], f["geometry"]
                if p["kind"] != kind:
                    continue
                if kind == "label":
                    x, y = project(g["coordinates"])
                    station = "station" in p["name"]
                    if station:
                        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{4*scale}" fill="{accent}" stroke="{bg}" stroke-width="{1.5*scale}"/>')
                        y += 18 * scale
                    parts.append(f'<text x="{x:.2f}" y="{y:.2f}" fill="{ink}" stroke="{bg}" stroke-width="{5*scale}" paint-order="stroke" stroke-linejoin="round" text-anchor="middle" dominant-baseline="middle" font-family="Arial,sans-serif" font-size="{14*scale}">{html.escape(p["name"])}</text>')
                    continue
                polygon = g["type"] == "Polygon"
                d = " ".join(path(ring, True) for ring in g["coordinates"]) if polygon else path(g["coordinates"])
                is_major = p["class"] in ("primary", "secondary", "tertiary", "motorway")
                color = water if kind == "water" else rail if kind == "rail" else major if is_major else road
                width = 1.2 if kind == "rail" else (4.5 if high else 3.5) if is_major else (2.5 if high else 2)
                style = f'fill="{water}" stroke="none"' if kind == "water" and polygon else f'fill="none" stroke="{color}" stroke-width="{width*scale}"'
                if kind == "rail":
                    style += f' stroke-dasharray="{4*scale} {3*scale}"'
                parts.append(f'<path d="{d}" {style} stroke-linecap="round" stroke-linejoin="round"/>')
        parts.append('</svg>')
        (HERE.parents[2] / "kart/server/static" / f"sono-{name}.svg").write_text("\n".join(parts) + "\n")


if __name__ == "__main__":
    render()
