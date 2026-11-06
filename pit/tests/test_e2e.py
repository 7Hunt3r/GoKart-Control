"""End-to-end smoke test: simulator (TCP) + pit server on free ports.

Starts real processes, talks HTTP to the server, and checks that a STOP ALL
goes round the full loop (server -> sim -> echoed in tele -> outcome table).
Run:  python3 -I pit/tests/test_e2e.py
"""
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

PIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(PIT, "sim", "sim_tbeam.py")
SERVER = os.path.join(PIT, "server", "pit_server.py")
MAP_DIR = os.path.join(PIT, "server", "map")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Stack:
    """Simulator + server. sim_args: scenario flags; config: server config dict."""

    def __init__(self, sim_args, config=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.sim = subprocess.Popen([sys.executable, "-I", SIM, "--tcp", "0", "--seed", "7"] + sim_args,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        first = self.sim.stdout.readline().split()      # "tcp 127.0.0.1:PORT"
        assert first and first[0] == "tcp", first
        self.http_port = free_port()
        self.map_dir = os.path.join(self.tmp.name, "map")      # zones get written here, never into the repo
        os.makedirs(self.map_dir)
        for name in ("map.json", "zones.json"):
            if os.path.exists(os.path.join(MAP_DIR, name)):
                shutil.copy(os.path.join(MAP_DIR, name), self.map_dir)
        cfg_path = os.path.join(self.tmp.name, "config.json")
        with open(cfg_path, "w") as f:
            json.dump(config or {}, f)
        self.srv = subprocess.Popen([sys.executable, "-I", SERVER, "--tcp", first[1], "--bind", "127.0.0.1",
                                     "--http-port", str(self.http_port), "--config", cfg_path,
                                     "--log-dir", os.path.join(self.tmp.name, "logs"), "--map-dir", self.map_dir],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.cookie = None
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if self.request("GET", "/healthz")[0] == 200:
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("server did not start")

    def request(self, method, path, body=None, ctype="application/json"):
        c = http.client.HTTPConnection("127.0.0.1", self.http_port, timeout=5)
        headers = {}
        if body is not None:
            headers["Content-Type"] = ctype
            body = body if isinstance(body, (bytes, str)) else json.dumps(body)
        if self.cookie:
            headers["Cookie"] = self.cookie
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        data = r.read()
        hdrs = dict(r.getheaders())
        c.close()
        return r.status, data, hdrs

    def state(self):
        st, data, _ = self.request("GET", "/api/state")
        assert st == 200, (st, data[:200])
        return json.loads(data)

    def wait(self, pred, timeout=20.0):
        deadline = time.time() + timeout
        s = None
        while time.time() < deadline:
            s = self.state()
            if pred(s):
                return s
            time.sleep(0.25)
        raise AssertionError("timed out; last state commands: "
                             + json.dumps(s["commands"]["recent"][:2] if s else None))

    def close(self):
        for p in (self.srv, self.sim):
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        self.sim.stdout.close()
        self.tmp.cleanup()


def online_count(s):
    return sum(1 for k in s["fleet"]["karts"] if k["link"] == "online")


class EndToEnd(unittest.TestCase):
    def test_stop_all_eight_applied(self):
        st = Stack(["--offline", "0", "--flaky", "0", "--nofix", "0", "--fault", "5", "--loss", "0"])
        try:
            code, body, _ = st.request("GET", "/kiosk")
            self.assertEqual(code, 200)
            self.assertIn(b"STOP ALL", body)
            self.assertEqual(st.request("GET", "/")[0], 200)
            for path in ("/m", "/mobile"):                  # phone page
                code, body, _ = st.request("GET", path)
                self.assertEqual(code, 200, path)
                for marker in (b'data-page="mobile"', b'id="stopAll"', b'id="cards"', b'id="map"',
                               b'data-tab="log"', b'apple-mobile-web-app-capable', b"manifest.webmanifest"):
                    self.assertIn(marker, body, (path, marker))
            code, body, h = st.request("GET", "/static/manifest.webmanifest")
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body)["start_url"], "/m")
            self.assertEqual(st.request("GET", "/static/icon-180.png")[0], 200)
            s = st.wait(lambda s: online_count(s) == 8)
            self.assertTrue(s["fleet"]["simulation"], "SIMULATION marker not detected")
            self.assertTrue(s["server"]["link"]["connected"])
            k5 = s["fleet"]["karts"][4]
            self.assertEqual(k5["vesc"]["fault"], 5)
            self.assertEqual(k5["fault_name"], "OVER_TEMP_FET")
            self.assertIsNotNone(s["fleet"]["karts"][0]["derived"]["battery_pct"])

            code, body, _ = st.request("POST", "/cmd", {"cmd": "stop", "kart": 255})
            self.assertEqual(code, 200, body)
            cid = json.loads(body)["command"]["id"]

            def all_applied(s):
                lf = s["commands"]["last_fleet"]
                return lf and lf["id"] == cid and list(lf["outcomes"].values()) == ["applied"] * 8
            s = st.wait(all_applied)
            self.assertEqual(len(s["commands"]["last_fleet"]["outcomes"]), 8)
            s = st.wait(lambda s: all(k["status"]["stopped"] for k in s["fleet"]["karts"]))

            self.assertEqual(st.request("GET", "/map/package.json")[0], 200)
            code, body, _ = st.request("GET", "/api/log/recent?n=50")
            self.assertEqual(code, 200)
            self.assertTrue(any(r.get("kind") == "op" and r.get("action") == "cmd" for r in json.loads(body)))
            code, body, _ = st.request("POST", "/cmd", {"cmd": "mode", "kart": 1, "arg": 7})
            self.assertEqual(code, 409)                      # refused and reported, not sent
        finally:
            st.close()

    def test_zones_laps_and_boost(self):
        st = Stack(["--offline", "0", "--flaky", "0", "--nofix", "0", "--fault", "0", "--loss", "0",
                    "--track-scale", "0.3"])
        try:
            code, body, _ = st.request("GET", "/map/package.json")
            pkg = json.loads(body)
            self.assertEqual(pkg["zones"], "/map/zones.json")
            v0 = pkg["zones_version"]
            code, body, _ = st.request("GET", "/map/zones.json")
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body)["version"], v0)
            for page in ("/", "/kiosk", "/m"):
                code, body, _ = st.request("GET", page)
                self.assertIn(b"gameChip" if page != "/" else b"zoneBar", body, page)

            zones = json.loads(subprocess.run([sys.executable, "-I", SIM, "--print-zones", "--track-scale", "0.3"],
                                              capture_output=True, text=True, check=True).stdout)
            bad = json.loads(json.dumps(zones))
            bad["zones"][1]["r_m"] = 2
            code, body, _ = st.request("POST", "/zones", bad)
            self.assertEqual(code, 409, body)
            self.assertIn("3 m minimum", json.loads(body)["error"])
            zones.pop("_comment")
            track = {"points": [z["a"] for z in zones["zones"] if z["type"] == "lapline"]
                     + [[zones["zones"][1]["lat"], zones["zones"][1]["lon"]], [zones["zones"][2]["lat"], zones["zones"][2]["lon"]]],
                     "closed": True, "width_m": 6}
            zones["track"] = track
            code, body, _ = st.request("POST", "/zones", zones)
            self.assertEqual(code, 200, body)
            saved = json.loads(body)["zones"]
            self.assertEqual(saved["version"], v0 + 1)
            self.assertTrue(saved["enabled"])
            self.assertEqual([z["id"] for z in saved["zones"]], ["lap", "b1", "m1", "i1"])
            self.assertEqual(saved["zones"][3]["type"], "item")
            self.assertEqual(saved["track"], dict(track, width_m=6.0))
            with open(os.path.join(st.map_dir, "zones.json")) as f:
                disk = json.load(f)
            self.assertEqual(disk["version"], v0 + 1)
            self.assertIn("_comment", disk)                       # comment kept from the original file
            self.assertEqual(len(disk["track"]["points"]), 3)
            pkg = json.loads(st.request("GET", "/map/package.json")[1])
            self.assertEqual(pkg["zones_version"], v0 + 1)

            code, body, _ = st.request("POST", "/zones/enable", {"enabled": False})
            self.assertEqual((code, json.loads(body)["version"]), (200, v0 + 2))
            self.assertFalse(json.loads(st.request("GET", "/map/zones.json")[1])["enabled"])
            self.assertEqual(st.request("POST", "/zones/enable", {"enabled": "yes"})[0], 409)
            code, body, _ = st.request("POST", "/zones/enable", {"enabled": True})
            self.assertEqual(json.loads(body)["version"], v0 + 3)
            self.assertTrue(json.loads(st.request("GET", "/map/zones.json")[1])["track"]["closed"])   # enable keeps the track

            s = st.wait(lambda s: any(r["laps"] >= 1 for r in s["fleet"]["laps"]), timeout=60)
            board = s["fleet"]["laps"]
            self.assertEqual([r["pos"] for r in board], list(range(1, len(board) + 1)))
            top = board[0]
            self.assertGreaterEqual(top["laps"], 1)
            self.assertIsNotNone(top["best_s"])
            self.assertEqual(s["fleet"]["karts"][top["kart"] - 1]["lap"]["laps"], top["laps"])
            self.assertTrue(s["fleet"]["game"]["timing"])
            self.assertEqual(s["zones"]["version"], v0 + 3)

            code, body, _ = st.request("POST", "/cmd", {"cmd": "boost", "kart": 1, "arg": 100})
            self.assertEqual(code, 200, body)
            s = st.wait(lambda s: s["fleet"]["karts"][0]["vesc"].get("scale") == 1.0, timeout=10)
            st.wait(lambda s: s["fleet"]["karts"][0]["vesc"].get("scale") == 0.75, timeout=12)   # auto-revert

            code, body, _ = st.request("POST", "/laps/reset", {})
            self.assertEqual(code, 200, body)
            time.sleep(0.3)
            self.assertFalse(any(r["laps"] >= 1 for r in st.state()["fleet"]["laps"]))
            rows = json.loads(st.request("GET", "/api/log/recent?n=200")[1])
            acts = {r.get("action") for r in rows if r.get("kind") == "op"}
            self.assertTrue({"zones_save", "zones_enable", "laps_reset"} <= acts, acts)

            # race start: laps reset, one broadcast `race` command with arg, grid hold, then GO
            st.wait(lambda s: any(r["laps"] >= 1 for r in s["fleet"]["laps"]), timeout=60)
            self.assertEqual(st.request("POST", "/race/start", {"secs": 0})[0], 409)
            n_race = sum(1 for c in st.state()["commands"]["recent"] if c["cmd"] == "race")
            code, body, _ = st.request("POST", "/race/start", {"secs": 3})
            self.assertEqual(code, 200, body)
            rs = json.loads(body)
            self.assertEqual((rs["secs"], rs["command"]["cmd"], rs["command"]["kart"], rs["command"]["arg"]), (3, "race", 255, 3))
            s = st.state()
            self.assertFalse(any(r["laps"] >= 1 for r in s["fleet"]["laps"]))
            race = s["fleet"]["race"]
            self.assertTrue(race["started_wall"] and race["go_wall"])
            self.assertTrue(0 < race["go_in_s"] <= 3.1, race["go_in_s"])
            self.assertEqual(sum(1 for c in s["commands"]["recent"] if c["cmd"] == "race"), n_race + 1)
            st.wait(lambda s: sum(1 for k in s["fleet"]["karts"] if k["vesc"].get("scale") == 0.0) >= 6, timeout=4)
            s = st.wait(lambda s: s["fleet"]["race"]["go_in_s"] < 0 and
                        all(k["vesc"].get("scale") != 0.0 for k in s["fleet"]["karts"] if k["link"] == "online"), timeout=8)
            self.assertEqual(s["commands"]["last_fleet"]["cmd"], "race")
            rows = json.loads(st.request("GET", "/api/log/recent?n=300")[1])
            self.assertTrue(any(r.get("action") == "race_start" and r.get("secs") == 3 for r in rows))
        finally:
            st.close()

    def test_auth_and_offline_kart(self):
        st = Stack(["--offline", "8", "--flaky", "0", "--loss", "0"],
                   config={"password": "letmein", "trust_localhost": False})
        try:
            self.assertEqual(st.request("GET", "/api/state")[0], 401)
            self.assertEqual(st.request("GET", "/")[2].get("Location"), "/login")
            self.assertEqual(st.request("GET", "/map/package.json")[0], 200)   # kart displays: public
            self.assertEqual(st.request("GET", "/map/zones.json")[0], 200)     # kart Pis: public too
            self.assertEqual(st.request("POST", "/zones", {"zones": []})[0], 401)
            self.assertEqual(st.request("POST", "/zones/enable", {"enabled": True})[0], 401)
            self.assertEqual(st.request("POST", "/laps/reset", {})[0], 401)
            self.assertEqual(st.request("POST", "/race/start", {"secs": 5})[0], 401)
            self.assertEqual(st.request("GET", "/m")[2].get("Location"), "/login?next=/m")
            self.assertEqual(st.request("GET", "/static/manifest.webmanifest")[0], 200)  # iOS fetches it cookie-less
            code, _, h = st.request("POST", "/login", "password=nope&name=x&next=/m",
                                    ctype="application/x-www-form-urlencoded")
            self.assertEqual(h.get("Location"), "/login?bad=1&next=/m")
            code, _, h = st.request("POST", "/login", "password=letmein&name=phone&next=/m",
                                    ctype="application/x-www-form-urlencoded")
            self.assertEqual(h.get("Location"), "/m")
            code, _, h = st.request("POST", "/login", "password=letmein&name=x&next=http://evil.example/",
                                    ctype="application/x-www-form-urlencoded")
            self.assertEqual(h.get("Location"), "/")                         # open redirect refused
            code, _, h = st.request("POST", "/login", "password=nope&name=x",
                                    ctype="application/x-www-form-urlencoded")
            self.assertEqual(h.get("Location"), "/login?bad=1")
            code, _, h = st.request("POST", "/login", "password=letmein&name=colin",
                                    ctype="application/x-www-form-urlencoded")
            self.assertIn("gkpit=", h.get("Set-Cookie", ""))
            st.cookie = h["Set-Cookie"].split(";")[0]
            st.wait(lambda s: online_count(s) == 7)
            code, body, _ = st.request("POST", "/cmd", {"cmd": "stop", "kart": 255})
            self.assertEqual(code, 200, body)
            cmd = json.loads(body)["command"]
            self.assertTrue(cmd["source"].startswith("colin@"))
            def done(s):
                lf = s["commands"]["last_fleet"]
                return bool(lf) and lf["id"] == cmd["id"] and lf["state"] == "done" \
                    and list(lf["outcomes"].values()).count("applied") == 7
            s = st.wait(done)
            self.assertEqual(s["commands"]["last_fleet"]["outcomes"]["8"], "offline")
            self.assertEqual(s["fleet"]["karts"][7]["link"], "never")
        finally:
            st.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
