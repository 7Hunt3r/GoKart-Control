"""Map package cache tests against a local fake pit HTTP server.

Run:  python3 -I kart/tests/test_mapsync.py
"""
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.dont_write_bytecode = True
KART = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(KART, "server"))

from mapsync import MapSync  # noqa: E402

JPEG_A = b"\xff\xd8\xff\xe0" + b"A" * 2000
JPEG_B = b"\xff\xd8\xff\xe0" + b"B" * 3000


class FakePit:
    """Serves /map/package.json and /map/<file> like pit_server.py, from dicts we control."""

    def __init__(self):
        self.package = None
        self.files = {}
        self.hits = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                fake.hits.append(self.path)
                if self.path == "/map/package.json" and fake.package is not None:
                    body, ctype = json.dumps(fake.package).encode(), "application/json"
                elif self.path.startswith("/map/") and self.path[5:] in fake.files:
                    body, ctype = fake.files[self.path[5:]], "image/jpeg"
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

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def dead_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def pkg(version, image="/map/a.jpg", lat=41.094):
    return {"version": version, "image": image,
            "bounds": {"north": 41.0989, "south": 41.0859, "east": -73.4071, "west": -73.4241},
            "origin": {"lat": lat, "lon": -73.4164}, "origin_source": "configured",
            "course": [], "pit": {"lat": 41.094, "lon": -73.4164}, "source": "test", "attribution": "test imagery"}


class MapCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = os.path.join(self.tmp.name, "cache")
        self.pit = FakePit()
        self.logs = []

    def tearDown(self):
        self.pit.close()
        self.tmp.cleanup()

    def sync(self, urls=None):
        return MapSync(urls or [self.pit.url], self.cache, timeout=2, log=self.logs.append)

    def image_hits(self):
        return [h for h in self.pit.hits if not h.endswith("package.json")]

    def test_download_then_versioning(self):
        self.pit.package, self.pit.files = pkg(6), {"a.jpg": JPEG_A}
        m = self.sync()
        self.assertTrue(m.sync_once())
        snap = m.snapshot()
        self.assertEqual(snap["package"]["version"], 6)
        self.assertEqual(snap["package"]["image"], "/map/a.jpg")
        self.assertTrue(snap["sync"]["reachable"])
        with open(m.file_path("a.jpg"), "rb") as f:
            self.assertEqual(f.read(), JPEG_A)

        # same version: no image download, no change
        self.assertFalse(m.sync_once())
        self.assertEqual(len(self.image_hits()), 1)

        # same version, metadata moved (pit origin follows its GPS): package updated, image not refetched
        self.pit.package = pkg(6, lat=41.0941)
        self.assertTrue(m.sync_once())
        self.assertEqual(m.snapshot()["package"]["origin"]["lat"], 41.0941)
        self.assertEqual(len(self.image_hits()), 1)

        # new version with a new image: downloaded, old image pruned
        self.pit.package, self.pit.files = pkg(7, image="/map/b.jpg"), {"b.jpg": JPEG_B}
        self.assertTrue(m.sync_once())
        self.assertEqual(m.snapshot()["package"]["version"], 7)
        self.assertIsNone(m.file_path("a.jpg"))
        self.assertIsNotNone(m.file_path("b.jpg"))

        # new version, same file name: re-downloaded (content may have changed)
        self.pit.package, self.pit.files = pkg(8, image="/map/b.jpg"), {"b.jpg": JPEG_A}
        self.assertTrue(m.sync_once())
        with open(m.file_path("b.jpg"), "rb") as f:
            self.assertEqual(f.read(), JPEG_A)

    def test_offline_uses_cache_and_survives_restart(self):
        self.pit.package, self.pit.files = pkg(3), {"a.jpg": JPEG_A}
        self.sync().sync_once()
        # display restarts on track: pit unreachable
        m = self.sync([dead_url()])
        self.assertEqual(m.snapshot()["package"]["version"], 3)   # loaded from disk before any poll
        self.assertFalse(m.sync_once())
        snap = m.snapshot()
        self.assertFalse(snap["sync"]["reachable"])
        self.assertEqual(snap["package"]["version"], 3)
        self.assertIsNotNone(m.file_path("a.jpg"))

    def test_falls_back_to_second_url(self):
        self.pit.package, self.pit.files = pkg(1), {"a.jpg": JPEG_A}
        m = self.sync([dead_url(), self.pit.url])
        self.assertTrue(m.sync_once())
        self.assertEqual(m.snapshot()["sync"]["pit_url"], self.pit.url)

    def test_bad_image_keeps_old_cache(self):
        self.pit.package, self.pit.files = pkg(1), {"a.jpg": JPEG_A}
        m = self.sync()
        m.sync_once()
        self.pit.package, self.pit.files = pkg(2, image="/map/c.jpg"), {"c.jpg": b"<html>not an image</html>"}
        self.assertFalse(m.sync_once())
        snap = m.snapshot()
        self.assertEqual(snap["package"]["version"], 1)
        self.assertIn("not a JPEG", snap["sync"]["last_error"])
        self.assertIsNotNone(m.file_path("a.jpg"))
        self.assertIsNone(m.file_path("c.jpg"))
        self.assertEqual([n for n in os.listdir(self.cache) if n.startswith(".dl-")], [])

    def test_missing_image_is_refetched(self):
        self.pit.package, self.pit.files = pkg(1), {"a.jpg": JPEG_A}
        m = self.sync()
        m.sync_once()
        os.unlink(os.path.join(self.cache, "a.jpg"))
        m.sync_once()
        self.assertIsNotNone(m.file_path("a.jpg"))

    def test_rejects_unsafe_names(self):
        self.pit.package, self.pit.files = pkg(1, image="/map/evil.sh"), {"evil.sh": JPEG_A}
        m = self.sync()
        self.assertFalse(m.sync_once())
        self.assertIsNone(m.snapshot()["package"])
        for name in ("../package.json", "package.json", ".dl-x", "x.sh", ""):
            self.assertIsNone(m.file_path(name), name)


def zdoc(version, enabled=True, n=1):
    return {"version": version, "enabled": enabled,
            "zones": [{"id": f"b{i}", "type": "boost", "shape": "circle", "lat": 41.094, "lon": -73.4164, "r_m": 10}
                      for i in range(n)]}


def with_zones(p, zv):
    return dict(p, zones="/map/zones.json", zones_version=zv)


class Zones(unittest.TestCase):
    setUp = MapCache.setUp
    tearDown = MapCache.tearDown
    sync = MapCache.sync
    image_hits = MapCache.image_hits

    def zone_hits(self):
        return [h for h in self.pit.hits if h.endswith("zones.json")]

    def test_fetch_on_version_change(self):
        self.pit.package = with_zones(pkg(6), 1)
        self.pit.files = {"a.jpg": JPEG_A, "zones.json": json.dumps(zdoc(1, n=1)).encode()}
        m = self.sync()
        self.assertTrue(m.sync_once())
        snap = m.snapshot()
        self.assertEqual(snap["zones"]["version"], 1)
        self.assertEqual(m.zones()["version"], 1)
        self.assertEqual(snap["package"]["zones_version"], 1)
        with open(os.path.join(self.cache, "zones.json")) as f:
            self.assertEqual(json.load(f), zdoc(1, n=1))
        # same zones_version: not downloaded again
        self.assertFalse(m.sync_once())
        self.assertEqual(len(self.zone_hits()), 1)
        # zones_version bump with the same map version: zones re-fetched, image not
        self.pit.package = with_zones(pkg(6), 2)
        self.pit.files["zones.json"] = json.dumps(zdoc(2, n=3)).encode()
        self.assertTrue(m.sync_once())
        self.assertEqual(len(m.zones()["zones"]), 3)
        self.assertEqual(len(self.zone_hits()), 2)
        self.assertEqual(len([h for h in self.image_hits() if h.endswith(".jpg")]), 1)
        self.assertEqual([n for n in os.listdir(self.cache) if n.startswith(".dl-")], [])
        # restart offline: zones come back from the cache
        m2 = self.sync([dead_url()])
        self.assertEqual(m2.zones()["version"], 2)
        # a deleted cache file is re-fetched at the same version
        os.unlink(os.path.join(self.cache, "zones.json"))
        m3 = self.sync()
        self.assertIsNone(m3.zones())
        m3.sync_once()
        self.assertEqual(m3.zones()["version"], 2)

    def test_absent_zones_is_game_off(self):
        self.pit.package, self.pit.files = pkg(1), {"a.jpg": JPEG_A}
        m = self.sync()
        self.assertTrue(m.sync_once())
        self.assertIsNone(m.zones())
        self.assertIsNone(m.snapshot()["zones"])
        self.assertIsNone(m.snapshot()["package"]["zones_version"])
        self.assertEqual(self.zone_hits(), [])
        # zones appear, then the pit drops them again: cached file removed
        self.pit.package = with_zones(pkg(1), 4)
        self.pit.files["zones.json"] = json.dumps(zdoc(4)).encode()
        self.assertTrue(m.sync_once())
        self.assertEqual(m.zones()["version"], 4)
        self.pit.package = pkg(1)
        self.assertTrue(m.sync_once())
        self.assertIsNone(m.zones())
        self.assertFalse(os.path.exists(os.path.join(self.cache, "zones.json")))

    def test_bad_zones_keep_old_and_map_still_updates(self):
        self.pit.package = with_zones(pkg(1), 1)
        self.pit.files = {"a.jpg": JPEG_A, "zones.json": json.dumps(zdoc(1)).encode()}
        m = self.sync()
        m.sync_once()
        self.pit.package = with_zones(pkg(2, image="/map/b.jpg"), 2)
        self.pit.files = {"b.jpg": JPEG_B, "zones.json": b"{not json"}
        self.assertTrue(m.sync_once())                     # the map still moves to v2
        snap = m.snapshot()
        self.assertEqual(snap["package"]["version"], 2)
        self.assertEqual(snap["package"]["zones_version"], 1)   # kept, so it is retried
        self.assertEqual(m.zones()["version"], 1)
        self.assertIn("zones:", snap["sync"]["zones_error"])
        self.pit.files["zones.json"] = json.dumps(zdoc(2)).encode()
        self.assertTrue(m.sync_once())
        self.assertEqual(m.zones()["version"], 2)
        self.assertIsNone(m.snapshot()["sync"]["zones_error"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
