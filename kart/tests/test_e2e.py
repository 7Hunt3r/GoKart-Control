"""End-to-end smoke test: simulated Heltec (TCP) + fake pit map server + kart server.

Starts real processes on free ports and talks HTTP to the kart server:
state arrives, the map is downloaded from the "pit" and served locally, a mode
request goes server -> Heltec text command -> cmd echo -> applied, the pit's
STOP/START/SLOW show up, and the session log has every serial line.
Run:  python3 -I kart/tests/test_e2e.py
"""
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

KART = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(KART)
SIM = os.path.join(KART, "sim", "sim_heltec.py")
FAKE_PIT = os.path.join(KART, "sim", "fake_pit.py")
SERVER = os.path.join(KART, "server", "kart_server.py")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 4000


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Stack:
    def __init__(self, sim_args=(), pit=True, zones=None, config=None):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.procs = []
        # map package: a copy of the shop preset's JSON + a small stand-in image (pit/ is read-only)
        with open(os.path.join(REPO, "pit", "server", "map", "map.shop.json")) as f:
            m = json.load(f)
        os.makedirs(os.path.join(t, "pitmap"))
        self.map_json = os.path.join(t, "pitmap", "map.json")
        with open(self.map_json, "w") as f:
            json.dump(m, f)
        with open(os.path.join(t, "pitmap", m["image"]), "wb") as f:
            f.write(JPEG)
        self.image = m["image"]
        if zones is not None:                                   # fake_pit serves zones.json next to map.json
            with open(os.path.join(t, "pitmap", "zones.json"), "w") as f:
                json.dump(zones, f)
        pit_url = f"http://127.0.0.1:{free_port()}"           # nothing listening = unreachable pit
        if pit:
            fp = subprocess.Popen([sys.executable, "-I", FAKE_PIT, "--port", "0", "--map", self.map_json],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            self.procs.append(fp)
            pit_url = fp.stdout.readline().strip()
        sim = subprocess.Popen([sys.executable, "-I", SIM, "--tcp", "0", "--seed", "3"] + list(sim_args),
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.procs.append(sim)
        first = sim.stdout.readline().split()
        assert first and first[0] == "tcp", first
        self.port = free_port()
        self.logs = os.path.join(t, "logs")
        cfg = os.path.join(t, "config.json")
        with open(cfg, "w") as f:
            json.dump(dict({"map_poll_s": 2, "kart_id": 2}, **(config or {})), f)
        self.procs.append(subprocess.Popen(
            [sys.executable, "-I", SERVER, "--tcp", first[1], "--bind", "127.0.0.1", "--http-port", str(self.port),
             "--config", cfg, "--log-dir", self.logs, "--cache-dir", os.path.join(t, "cache"), "--pit-url", pit_url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if self.request("GET", "/healthz")[0] == 200:
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("kart server did not start")

    def request(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def state(self):
        st, data = self.request("GET", "/api/state")
        assert st == 200
        return json.loads(data)

    def wait(self, pred, timeout=15.0, what=""):
        deadline = time.time() + timeout
        s = None
        while time.time() < deadline:
            s = self.state()
            if pred(s):
                return s
            time.sleep(0.2)
        raise AssertionError(f"timed out waiting for {what}; commands={json.dumps(s and s['commands'][:2])}")

    def close(self):
        for p in reversed(self.procs):
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
            if p.stdout:
                p.stdout.close()
        self.tmp.cleanup()


class EndToEnd(unittest.TestCase):
    def test_driver_loop(self):
        st = Stack(["--pit-stop-at", "6", "--pit-start-at", "9", "--pit-slow-at", "11"])
        try:
            code, body = st.request("GET", "/")
            self.assertEqual(code, 200)
            for marker in (b'data-mode="0"', b'data-mode="2"', b'id="map"', b'id="stopBanner"', b'id="slowBanner"'):
                self.assertIn(marker, body)
            self.assertNotIn(b"STOP ALL", body)                     # stop/start stay with the pit
            for f in ("app.js", "style.css"):
                self.assertEqual(st.request("GET", "/static/" + f)[0], 200)

            s = st.wait(lambda s: s["self"]["link"] == "online" and len(s["fleet"]) == 2
                        and s["self"]["gps"]["fix_fresh"], what="self + 2 fleet karts")
            self.assertTrue(s["simulation"])
            self.assertTrue(s["server"]["link"]["connected"])
            self.assertEqual(s["self"]["heltec_id"], 2)
            self.assertEqual(sorted(k["id"] for k in s["fleet"]), [3, 5])
            self.assertIsNotNone(s["self"]["derived"]["battery_pct"])
            self.assertIsNotNone(s["self"]["derived"]["gps_mph"])
            self.assertEqual(s["pit_link"]["state"], "ok")
            self.assertIs(s["slow"]["active"], False)                # sim Heltec booted 5 s ago

            # map: fetched from the fake pit, cached, served locally
            s = st.wait(lambda s: s["map"]["package"] is not None, what="map package")
            pkg = s["map"]["package"]
            self.assertEqual(pkg["image"], "/map/" + st.image)
            self.assertEqual(pkg["version"], 6)
            code, data = st.request("GET", pkg["image"])
            self.assertEqual((code, data), (200, JPEG))
            self.assertEqual(st.request("GET", "/map/package.json")[0], 200)

            # driver mode request -> Heltec text command -> echo -> applied
            code, body = st.request("POST", "/cmd", {"cmd": "mode", "arg": 2})
            self.assertEqual(code, 200, body)
            cid = json.loads(body)["command"]["id"]
            s = st.wait(lambda s: s["commands"] and s["commands"][0]["id"] == cid and s["commands"][0]["result_name"] == "applied"
                        and s["self"]["status"]["mode_actual"] == 2 and s["self"]["status"]["mode_confirmed"],
                        what="mode 2 applied")
            self.assertEqual(s["commands"][0]["line"], "mode 2")
            self.assertEqual(s["commands"][0]["state"], "echoed")

            # validation: bad arg and unknown command are refused, nothing sent
            self.assertEqual(st.request("POST", "/cmd", {"cmd": "mode", "arg": 7})[0], 409)
            self.assertEqual(st.request("POST", "/cmd", {"cmd": "reboot"})[0], 409)

            # the pit's STOP, START and SLOW (scripted in the simulator)
            st.wait(lambda s: s["self"]["status"]["stopped"] is True, what="pit STOP")
            st.wait(lambda s: s["self"]["status"]["stopped"] is False, what="pit START")
            s = st.wait(lambda s: s["slow"]["active"] is True, what="pit SLOW")
            self.assertEqual(s["slow"]["mph"], 8)
            code, _ = st.request("POST", "/cmd", {"cmd": "start"})   # debug path (not on the driver page)
            self.assertEqual(code, 200)
            st.wait(lambda s: s["slow"]["active"] is False, what="START clears SLOW")

            # SSE stream delivers state
            c = http.client.HTTPConnection("127.0.0.1", st.port, timeout=5)
            c.request("GET", "/events")
            r = c.getresponse()
            self.assertEqual(r.getheader("Content-Type"), "text/event-stream")
            buf = b""
            while b"\ndata: " not in buf and len(buf) < 200000:
                buf += r.read1(65536)
            c.close()

            # session log: rx lines and our tx lines
            logs = [f for f in os.listdir(st.logs) if f.startswith("session-")]
            self.assertEqual(len(logs), 1)
            with open(os.path.join(st.logs, logs[0])) as f:
                recs = [json.loads(x) for x in f]
            kinds = {r["kind"] for r in recs}
            self.assertTrue({"rx", "tx", "op", "event"} <= kinds, kinds)
            self.assertIn("mode 2", [r["line"] for r in recs if r["kind"] == "tx"])
            self.assertTrue(any('"t":"self"' in r["line"] for r in recs if r["kind"] == "rx"))
        finally:
            st.close()

    def test_game_course(self):
        """Simulator --course through the shipped course zones: boost, mud, laps, HUD state."""
        with open(os.path.join(KART, "sim", "zones.course.json")) as f:
            zones = json.load(f)
        for z in zones["zones"]:
            if z["type"] == "lapline":
                z["min_lap_s"] = 5                   # --warp 3: a lap of the 191 m loop takes ~12 s
        st = Stack(["--course", "--warp", "3"], zones=zones)
        try:
            code, body = st.request("GET", "/")
            for marker in (b'id="speedo"', b'id="gear"', b'id="boostMeter"', b'id="lapPanel"', b'id="gameChip"',
                           b'id="flash"', b'id="stopBanner"', b'id="slowBanner"', b'id="connBanner"'):
                self.assertIn(marker, body)
            s = st.wait(lambda s: s["game"]["on"], what="game on (zones fetched)")
            self.assertEqual(s["map"]["zones"]["version"], 1)
            self.assertEqual(s["map"]["package"]["zones_version"], 1)
            self.assertEqual(st.request("GET", "/map/zones.json")[0], 200)
            kinds = set()

            def lapped(s):
                kinds.add(s["game"]["meter"]["kind"])
                return s["game"]["lap"]["count"] >= 1
            s = st.wait(lapped, timeout=45, what="one lap completed")
            g = s["game"]
            self.assertTrue(g["lap"]["running"])
            self.assertGreater(g["lap"]["last_s"], 5)
            self.assertEqual(g["lap"]["best_s"], g["lap"]["last_s"])
            self.assertIsNotNone(g["lap"]["current_s"])
            self.assertTrue({"boost", "mud"} <= kinds, kinds)      # HUD meter saw both while lapping
            self.assertEqual(g["gps_source"], "gps")                 # 5 Hz gps lines drive the game
            self.assertGreater(s["gps_lines"], 20)
            self.assertEqual(s["rx_bad"], 0)
            self.assertEqual(g["pos"]["quality"], "good")
            self.assertEqual(s["self"]["gps"]["gps_hz"], 5)
            cv = g["correction"]                                      # off by default, vector still shown
            self.assertFalse(cv["applied"])
            self.assertAlmostEqual(cv["vector"]["mag_m"], 3.6, places=1)   # sim pit radio 3 m E, 2 m N
            self.assertGreaterEqual(g["cmds_sent"], 2)                # boost + at least one mud (timing-dependent)
            self.assertIn(g["last_result"]["result_name"], ("accepted", "applied"))
            logs = [f for f in os.listdir(st.logs) if f.startswith("session-")]
            with open(os.path.join(st.logs, logs[0])) as f:
                recs = [json.loads(x) for x in f]
            tx = [r["line"] for r in recs if r["kind"] == "tx"]
            self.assertIn("boost 5 1.00", tx)
            self.assertIn("boost 3 0.50", tx)
            self.assertFalse([x for x in tx if x.split()[0] in ("stop", "start", "mode", "slow")], tx)
            game = [r for r in recs if r["kind"] == "game"]
            evs = {r["ev"] for r in game}
            self.assertTrue({"game_on", "zone_enter", "zone_exit", "cmd", "boost", "mud", "lap_start", "lap"} <= evs, evs)
            self.assertTrue(all(r["sent"] for r in game if r["ev"] == "cmd"))
            # the simulator applied the override: a self line with scale 1.0 while in NORMAL
            rx = [json.loads(r["line"]) for r in recs if r["kind"] == "rx" and '"t":"self"' in r["line"]]
            self.assertTrue(any(x["scale"] == 1.0 and x["mode_req"] == 1 for x in rx))
            self.assertTrue(any(x["scale"] == 0.5 for x in rx))
        finally:
            st.close()

    def test_no_pit_no_cache(self):
        st = Stack(["--nofix"], pit=False)
        try:
            s = st.wait(lambda s: s["self"]["link"] == "online" and s["map"]["sync"]["reachable"] is False,
                        what="self online, pit unreachable")
            self.assertIsNone(s["map"]["package"])
            self.assertIsNone(s["self"]["gps"]["last_fix"])
            self.assertIsNone(s["self"]["derived"]["gps_mph"])        # no fix -> no speed, not 0
            self.assertFalse(s["self"]["gps"]["fix_now"])
        finally:
            st.close()


class FakeLink:
    """Stands in for the real Heltec link: records every write."""
    instances = []

    def __init__(self, on_line, on_state, port=None, tcp=None, log=print):
        self.on_line, self.on_state = on_line, on_state
        self.connected = True
        self.description = "fake real Heltec"
        self.writes = []
        FakeLink.instances.append(self)

    def start(self):
        pass

    def stop(self):
        pass

    def write_text(self, text):
        self.writes.append(text)
        return True


class DemoMode(unittest.TestCase):
    """Demo mode in-process: the real link is never written to; lines go to the simulator."""

    def test_demo_never_writes_to_the_real_link(self):
        sys.path.insert(0, os.path.join(KART, "server"))
        import kart_server
        tmp = tempfile.TemporaryDirectory()
        real_link = kart_server.Link
        kart_server.Link = FakeLink
        app = None
        try:
            cfg = dict(kart_server.DEFAULTS, log_dir=os.path.join(tmp.name, "logs"),
                       cache_dir=os.path.join(tmp.name, "cache"), pit_urls=[f"http://127.0.0.1:{free_port()}"],
                       map_poll_s=60, demo_max_s=60)
            app = kart_server.App(cfg)
            app.start()
            real = FakeLink.instances[-1]
            real.on_line('{"t":"self","kart":2,"fix":true,"lat":41.0932,"lon":-73.4187,"stopped":false,"vesc_link":false}')
            info = app.demo_start("test")
            self.assertTrue(info["active"])
            self.assertEqual(info["zones"], "builtin")
            app.command({"cmd": "mode", "arg": 2}, "test")              # a driver tap while demoing
            real.on_line('{"t":"self","kart":2,"fix":true,"lat":1.0,"lon":1.0,"stopped":true}')   # ignored
            deadline = time.time() + 30
            s = app.state()
            while time.time() < deadline:
                s = app.state()
                if s["game"]["cmds_sent"] >= 1 and s["game"]["race"]["go_age_s"] is not None:
                    break
                time.sleep(0.3)
            self.assertTrue(s["simulation"])
            self.assertTrue(s["demo"]["active"])
            self.assertTrue(s["game"]["on"])
            self.assertIsNotNone(s["game"]["race"]["go_age_s"])            # race 5 countdown ran, GO happened
            self.assertGreaterEqual(s["game"]["cmds_sent"], 1)
            self.assertFalse(s["self"]["status"]["stopped"])               # the real kart's STOPPED line was ignored
            self.assertEqual(s["map"]["zones"]["version"], "demo-builtin")
            self.assertEqual(real.writes, [])                              # NOTHING to the real Heltec
            self.assertIn("mode 2", app.demo.writes)
            self.assertTrue(any(w.startswith("boost ") for w in app.demo.writes))
            app.demo_stop("test")
            s = app.state()
            self.assertFalse(s["demo"]["active"])
            self.assertFalse(s["simulation"])
            real.on_line('{"t":"self","kart":2,"fix":true,"lat":41.0932,"lon":-73.4187,"stopped":false,"vesc_link":false}')
            self.assertEqual(app.state()["self"]["link"], "online")         # back on the real link, no restart
            self.assertEqual(real.writes, [])
            with open(app.log.path) as f:
                recs = [json.loads(x) for x in f]
            self.assertTrue(all(r.get("demo") for r in recs if r["kind"] == "tx"))
            self.assertTrue(any(r.get("ignored") == "demo" for r in recs if r["kind"] == "rx"))
        finally:
            kart_server.Link = real_link
            if app:
                app.stopping.set()
                if app.demo:
                    app.demo_stop("test")
                app.maps.stop()
            tmp.cleanup()

    def test_sim_race_countdown_over_tcp(self):
        st = Stack(["--course", "--race-at", "3", "--race-pos", "3,2,1", "--race-pos-step", "2"])
        try:
            s = st.wait(lambda s: s["game"]["race"]["hold"], what="grid hold from the sim's race line")
            self.assertFalse(s["self"]["status"]["stopped"])               # grid hold is not STOPPED
            self.assertEqual((s["game"]["race"]["pos"], s["game"]["race"]["state_name"]), (3, "countdown"))
            self.assertLessEqual(s["game"]["race"]["go_in_s"], 5.0)
            s = st.wait(lambda s: s["game"]["race"]["go_confirmed"], timeout=10, what="GO line")
            self.assertFalse(s["game"]["race"]["hold"])
            self.assertEqual(st.request("POST", "/cmd", {"cmd": "race", "arg": 5})[0], 409)   # never from the screen
            s = st.wait(lambda s: s["game"]["race"]["pos"] == 1 and s["self"]["status"]["race_pos"] == 1, timeout=10,
                        what="P3 -> P2 -> P1 after GO (event, then the next self line)")
            r = s["game"]["race"]
            self.assertEqual((r["state"], r["pos_prev"]), (2, 2))
            self.assertIsNotNone(r["pos_changed_wall"])
            self.assertEqual(s["self"]["status"]["race_pos"], 1)
            self.assertEqual(s["rx_bad"], 0)
            with open(os.path.join(st.logs, os.listdir(st.logs)[0])) as f:
                recs = [json.loads(x) for x in f]
            dirs = [r["dir"] for r in recs if r["kind"] == "game" and r.get("ev") == "race_position"]
            self.assertEqual(dirs, ["new", "up", "up"])
        finally:
            st.close()


class ServerPieces(unittest.TestCase):
    """Session log rotation, config validation, the Heltec link, SSE deltas: no processes."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(KART, "server"))
        import kart_server
        import link
        cls.ks, cls.link = kart_server, link

    def test_session_log_rotates_and_prunes(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.path.join(d, "session-20000101-000000.jsonl")
            with open(old, "w") as f:
                f.write("x" * 50000)
            log = self.ks.SessionLog(d, max_mb=0.06, part_mb=0.01)      # 60 KB total, 10 KB parts
            for i in range(600):
                log.write("rx", line="y" * 100, i=i)
            log.flush()
            files = sorted(os.listdir(d))
            self.assertNotIn(os.path.basename(old), files)            # oldest went first
            self.assertGreater(log.part, 3)
            self.assertIn(os.path.basename(log.path), files)          # the open part is never deleted
            total = sum(os.path.getsize(os.path.join(d, f)) for f in files)
            self.assertLess(total, 0.06 * 1024 * 1024 + 12000)
            log.write("event", what="x")                              # non-rx records are flushed at once
            with open(log.path) as f:
                self.assertIn('"what":"x"', f.read())
            log.close()

    def test_session_log_unwritable_dir_falls_back(self):
        log = self.ks.SessionLog("/proc/definitely/not/here", max_mb=10)
        log.write("event", what="ok")
        log.close()
        self.assertTrue(os.path.exists(log.path))

    def test_config_validation(self):
        cfg, w = self.ks.validate_config(dict(self.ks.DEFAULTS, map_fps=500, kart_id=9, sound="yes", bogus=1,
                                              map_orientation="sideways", rotate=90, pit_urls="http://x"))
        self.assertEqual((cfg["map_fps"], cfg["kart_id"], cfg["sound"], cfg["map_orientation"], cfg["rotate"]),
                         (10, 2, True, "heading_up", 0))
        self.assertEqual(cfg["pit_urls"], ["http://x"])
        self.assertEqual(len(w), 6, w)
        cfg, w = self.ks.validate_config(dict(self.ks.DEFAULTS))
        self.assertEqual(w, [])
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.json")
            with open(p, "w") as f:
                f.write("{broken")
            cfg, w = self.ks.load_config(p)
            self.assertEqual(cfg["kart_id"], 2)
            self.assertIn("using the defaults", w[0])
        with open(os.path.join(KART, "server", "config.example.json")) as f:
            ex = json.load(f)
        self.assertEqual({k for k in ex if not k.startswith("_")}, set(self.ks.DEFAULTS))   # example complete
        self.assertEqual(set(ex["_doc"]) - {"_"}, set(self.ks.DEFAULTS))                  # and documented
        self.assertEqual(self.ks.validate_config({k: v for k, v in ex.items() if k != "_doc"})[1], [])

    def test_link_garbage_partial_reconnect_no_replay(self):
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(2)
        port = srv.getsockname()[1]
        lines, states = [], []
        L = self.link.Link(lines.append, lambda c, d: states.append(c), tcp=f"127.0.0.1:{port}", log=lambda m: None)
        L.start()
        try:
            c, _ = srv.accept()
            c.sendall(b"\xff\xfe garbage\n{\"t\":\"self\",")                 # garbage, then half a line
            time.sleep(0.2)
            c.sendall(b"\"kart\":2}\r\n" + b"z" * 5000)                     # rest of it, then overlong junk
            deadline = time.time() + 3
            while len(lines) < 3 and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(lines[1], '{"t":"self","kart":2}')
            self.assertIn("overlong", lines[2])
            self.assertEqual(L.state, "up")
            self.assertTrue(L.write_text("status"))
            c.settimeout(2)
            self.assertEqual(c.recv(100), b"status\n")
            c.close()                                                   # unplug
            deadline = time.time() + 3
            while L.state != "reconnecting" and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(L.state, "reconnecting")
            self.assertFalse(L.write_text("mode 1"))                    # refused while down: never queued
            c2, _ = srv.accept()                                        # reconnect within RECONNECT_S
            c2.settimeout(1.0)
            deadline = time.time() + 3
            while L.state != "up" and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(L.state, "up")
            with self.assertRaises(socket.timeout):
                c2.recv(100)                                            # nothing replayed after the reconnect
            self.assertEqual(states, [True, False, True])
            self.assertTrue(L.write_text("ping", wait=False))           # queued from the reader thread: no wait
            self.assertEqual(c2.recv(100), b"ping\n")
            c2.close()
        finally:
            L.stop()
            srv.close()

    def test_sse_sends_deltas(self):
        st = Stack(["--nofix"])
        try:
            st.wait(lambda s: s["self"]["link"] == "online", what="self online")
            c = http.client.HTTPConnection("127.0.0.1", st.port, timeout=5)
            c.request("GET", "/events")
            r = c.getresponse()
            frames = []
            while len(frames) < 4:
                line = r.fp.readline().decode()
                if line.startswith("data: "):
                    frames.append(json.loads(line[6:]))
            c.close()
            self.assertNotIn("_d", frames[0])
            self.assertIn("map", frames[0])
            self.assertNotIn("heltec", frames[0])                       # /api/state only
            self.assertTrue(all(f.get("_d") == 1 for f in frames[1:]))
            self.assertTrue(all("map" not in f and "config" not in f for f in frames[1:]))   # unchanged: not resent
            self.assertIn("heltec", st.state())
        finally:
            st.close()

    def test_demo_lines_after_stop_are_dropped(self):
        tmp = tempfile.TemporaryDirectory()
        real_link = self.ks.Link
        self.ks.Link = FakeLink
        try:
            cfg = dict(self.ks.DEFAULTS, log_dir=os.path.join(tmp.name, "logs"), cache_dir=os.path.join(tmp.name, "cache"),
                       pit_urls=[f"http://127.0.0.1:{free_port()}"], map_poll_s=60)
            app = self.ks.App(cfg)
            real = FakeLink.instances[-1]
            app.demo_start("test")
            d = app.demo
            app.demo_stop("test")
            app._on_demo_line('{"t":"self","kart":2,"fix":true,"lat":1.0,"lon":1.0}', d)   # late line from the old demo
            self.assertIsNone(app.state()["self"]["gps"]["last_fix"])
            real.on_line('{"t":"self","kart":2,"fix":true,"lat":41.09,"lon":-73.41}')
            self.assertEqual(app.state()["self"]["gps"]["last_fix"]["lat"], 41.09)
            app.maps.stop()
            # the simulator's reader thread may still be delivering a line that was in flight at
            # demo_stop: wait for it, then make sure it did not save the simulated kart's game timing
            # as the real kart's (game-state.json lives next to the cache dir)
            for _ in range(150):
                if not any(t.name.startswith("demo-") for t in threading.enumerate()):
                    break
                time.sleep(0.02)
            self.assertFalse(os.path.exists(os.path.join(tmp.name, "game-state.json")),
                             "a late demo line wrote the simulator's game state")
        finally:
            self.ks.Link = real_link
            app.log.close()
            tmp.cleanup()

    def test_game_timing_survives_server_restart(self):
        doc = {"version": 1, "enabled": True, "zones": [
            {"id": "lap", "type": "lapline", "shape": "line", "a": [41.0, -73.0], "b": [41.0001, -73.0], "min_lap_s": 2}]}
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "cache", "map")
            os.makedirs(cache)
            with open(os.path.join(cache, "package.json"), "w") as f:
                json.dump({"version": 1, "zones_version": 1}, f)
            with open(os.path.join(cache, "zones.json"), "w") as f:
                json.dump(doc, f)
            real_link = self.ks.Link
            self.ks.Link = FakeLink
            try:
                cfg = dict(self.ks.DEFAULTS, log_dir=os.path.join(d, "logs"), cache_dir=cache,
                           pit_urls=[f"http://127.0.0.1:{free_port()}"], map_poll_s=60)
                a1 = self.ks.App(cfg)
                a1.start()
                time.sleep(0.4)                                          # ticker loads the zones
                real = FakeLink.instances[-1]
                for lon, dt in ((-73.00005, 0), (-72.99995, 0.3), (-72.9998, 2.5), (-73.0001, 2.5)):   # start, away, lap 1
                    time.sleep(dt)
                    real.on_line(json.dumps({"t": "self", "kart": 2, "fix": True, "lat": 41.00005, "lon": lon,
                                             "gps_kph": 20, "heading": 90, "stopped": False}))
                self.assertEqual(a1.state()["game"]["lap"]["count"], 1)
                a1.stopping.set(); a1.maps.stop()
                self.assertTrue(os.path.exists(a1.game_state_path))
                a2 = self.ks.App(cfg)                                    # "restart"
                a2.start()
                time.sleep(0.6)
                lap = a2.state()["game"]["lap"]
                self.assertEqual((lap["count"], lap["running"]), (1, True))
                a2.stopping.set(); a2.maps.stop()
            finally:
                self.ks.Link = real_link


class PiHealthE2E(unittest.TestCase):
    """server.health in /api/state from the sampling thread (health_file stands in for vcgencmd)."""

    def test_health_field_transitions_and_chip(self):
        tmp = tempfile.mkdtemp()
        hf = os.path.join(tmp, "throttled")
        with open(hf, "w") as f:
            f.write("throttled=0x0\n")
        st = Stack(["--nofix"], pit=False, config={"health_file": hf, "health_period_s": 0.3})
        try:
            h = st.wait(lambda s: s["server"]["health"]["status"] == "ok", what="health ok")["server"]["health"]
            self.assertTrue(h["available"])
            self.assertEqual(h["throttled"], "0x0")
            with open(hf, "w") as f:
                f.write("throttled=0x50005\n")
            h = st.wait(lambda s: s["server"]["health"]["status"] == "undervolt", what="undervolt")["server"]["health"]
            self.assertEqual(h["throttled"], "0x50005")
            for k in ("undervolt_now", "throttled_now", "undervolt_since_boot", "throttled_since_boot"):
                self.assertTrue(h["flags"][k], k)
            # demo mode still works and the (real) Pi health stays in the state
            code, body = st.request("POST", "/api/demo", {"on": True})
            self.assertEqual(code, 200, body)
            s = st.wait(lambda s: s["demo"]["active"], what="demo on")
            self.assertEqual(s["server"]["health"]["status"], "undervolt")
            self.assertEqual(st.request("POST", "/api/demo", {"on": False})[0], 200)
            with open(hf, "w") as f:
                f.write("throttled=0x50000\n")
            st.wait(lambda s: s["server"]["health"]["status"] == "had_undervolt", what="had_undervolt")
            body = st.request("GET", "/")[1]
            self.assertIn(b'id="healthChip"', body)
            self.assertIn(b"healthChip(S.server.health)", st.request("GET", "/static/app.js")[1])
            time.sleep(1.2)                                             # session log flushes at least once a second
            ev = []
            for name in sorted(os.listdir(st.logs)):
                with open(os.path.join(st.logs, name)) as f:
                    ev += [(r["prev"], r["status"]) for r in map(json.loads, f) if r.get("what") == "health"]
            self.assertEqual(ev, [("ok", "undervolt"), ("undervolt", "had_undervolt")])
        finally:
            st.close()
            shutil.rmtree(tmp)

    def test_health_unavailable_off_pi(self):
        if shutil.which("vcgencmd"):
            self.skipTest("running on a Pi")
        st = Stack(["--nofix"], pit=False)
        try:
            h = st.state()["server"]["health"]
            self.assertFalse(h["available"])
            self.assertEqual(h["status"], "unavailable")
        finally:
            st.close()


if __name__ == "__main__":
    unittest.main(verbosity=1)
