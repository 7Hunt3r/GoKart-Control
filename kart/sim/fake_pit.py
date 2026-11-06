#!/usr/bin/env python3
"""Fake pit map endpoint for running the kart display on a Mac (SIMULATION ONLY).

    python3 kart/sim/fake_pit.py --port 18080 \
        --map pit/server/map/map.shop.json        # image is read from the same folder

Serves GET /map/package.json in the shape pit_server.py's map_package() returns
("image": "/map/<file>"), and GET /map/<file> for that one image, read-only.
Point the kart server at it: kart_server.py --pit-url http://127.0.0.1:18080

Game zones (pit/server/map/ZONES.md): with a zones file (default: zones.json next
to the map JSON, if present; --zones PATH to pick one, e.g. kart/sim/zones.course.json;
--zones none = no game) the package also carries "zones": "/map/zones.json" and
"zones_version" (the file's "version"), and GET /map/zones.json serves the file.
Both are re-read on every request, so editing the file + bumping "version" is
picked up by the kart on its next poll.
"""
from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def zones_from(zones_json):
    if not zones_json or not os.path.isfile(zones_json):
        return None
    with open(zones_json) as f:
        return json.load(f)


def package_from(map_json, zones_json=None):
    with open(map_json) as f:
        m = json.load(f)
    img = m.get("image")
    z = zones_from(zones_json)
    zf = {"zones": "/map/zones.json", "zones_version": z.get("version", 0)} if z is not None else {}
    return {**zf, "version": m.get("version", 0),
            "image": f"/map/{os.path.basename(img)}" if img else None,
            "bounds": m.get("bounds"),
            "origin": m.get("origin"), "origin_source": "configured" if m.get("origin") else None,
            "course": m.get("course") or [], "pit": m.get("pit"),
            "source": m.get("source"), "attribution": m.get("attribution")}


def make_server(map_json, port=0, bind="127.0.0.1", zones_json="auto"):
    folder = os.path.dirname(os.path.abspath(map_json))
    if zones_json == "auto":
        zones_json = os.path.join(folder, "zones.json")
    elif zones_json in (None, "none"):
        zones_json = None

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            pkg = package_from(map_json, zones_json)     # re-read each time, like the pit
            if self.path == "/map/package.json":
                body, ctype = json.dumps(pkg).encode(), "application/json"
            elif self.path == "/map/zones.json" and pkg.get("zones"):
                body, ctype = json.dumps(zones_from(zones_json)).encode(), "application/json"
            elif pkg["image"] and self.path == pkg["image"]:
                with open(os.path.join(folder, os.path.basename(pkg["image"])), "rb") as f:
                    body, ctype = f.read(), "image/jpeg"
            elif self.path == "/healthz":
                body, ctype = b"ok", "text/plain"
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((bind, port), H)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--map", default=os.path.join(here, "..", "..", "pit", "server", "map", "map.shop.json"))
    ap.add_argument("--zones", default="auto",
                    help="zones file (default: zones.json next to --map if present; 'none' = no game)")
    a = ap.parse_args()
    srv = make_server(a.map, a.port, zones_json=a.zones)
    print(f"http://127.0.0.1:{srv.server_address[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
