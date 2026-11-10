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
        self.version = m["version"]
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
            self.assertEqual(pkg["version"], st.version)              # the shop preset's (6 -> 7 on 2026-11-09)
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
                           b'id="flash"', b'id="stopBanner"', b'id="slowBanner"', b'id="connBanner"',
                           b'id="sunBtn"', b'id="freeBtn"', b'id="freeChip"'):
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
            self.assertIn("slow 5 4", tx)                             # mud = timed SLOW (sim reports kart-0.2.4)
            self.assertNotIn("boost 4 0.50", tx)
            self.assertEqual((g["fw"], g["mud_cmd"]), ("sim-kart-0.2.5", "slow"))     # sim fw 0.2.5 since multiplayer
            self.assertFalse([x for x in tx if x.split()[0] in ("stop", "start", "mode")], tx)
            self.assertFalse([x for x in tx if x.split()[0] == "slow" and x != "slow 5 4"], tx)
            game = [r for r in recs if r["kind"] == "game"]
            evs = {r["ev"] for r in game}
            self.assertTrue({"game_on", "zone_enter", "zone_exit", "cmd", "boost", "mud", "lap_start", "lap"} <= evs, evs)
            self.assertTrue(all(r["sent"] for r in game if r["ev"] == "cmd"))
            # the simulator applied the override: a self line with scale 1.0 while in NORMAL
            rx = [json.loads(r["line"]) for r in recs if r["kind"] == "rx" and '"t":"self"' in r["line"]]
            self.assertTrue(any(x["scale"] == 1.0 and x["mode_req"] == 1 for x in rx))
            self.assertTrue(any(x["slow_mph"] == 5 for x in rx))     # the Heltec's own SLOW (banner source)
            self.assertFalse(any(x["scale"] == 0.5 for x in rx))     # mud no longer touches the scale
        finally:
            st.close()

    def test_freeplay_without_pit_zones(self):
        """Pit with NO zones + the simulator driving its loop: freeplay on -> one lap records the course,
        boxes appear on it, the kart drives through at least one and the roll goes out as a boost line."""
        st = Stack(["--course", "--warp", "3"], config={"freeplay_boxes": 3})
        try:
            s = st.wait(lambda s: s["self"]["gps"]["fix_fresh"] and s["map"]["sync"]["reachable"], what="fix + pit")
            self.assertFalse(s["game"]["on"])
            self.assertEqual(s["freeplay"]["phase"], "off")
            code, body = st.request("POST", "/api/freeplay", {"on": True})
            self.assertEqual(code, 200, body)
            self.assertEqual(json.loads(body)["freeplay"]["phase"], "recording")
            eco = json.loads(body)["freeplay"]["eco"]                       # switched on: ECO through POST /cmd's path
            self.assertEqual((eco["outcome"], eco["line"]), ("sent", "mode 0"))
            s = st.wait(lambda s: s["self"]["status"]["mode_actual"] == 0 and s["self"]["status"]["mode_confirmed"],
                        what="freeplay ECO applied")
            c = next(c for c in s["commands"] if c["id"] == eco["id"])
            self.assertEqual((c["who"], c["line"], c["result_name"]), ("freeplay", "mode 0", "applied"))
            self.assertEqual(st.request("POST", "/api/freeplay", {"on": True})[0], 200)   # already on: no 2nd ECO
            self.assertEqual(st.request("POST", "/api/freeplay", {"on": "yes"})[0], 400)
            s = st.wait(lambda s: s["freeplay"]["phase"] == "ready" and len(s["freeplay"]["boxes"]) == 3,
                        timeout=45, what="course recorded + 3 boxes")
            f = s["freeplay"]
            self.assertAlmostEqual(f["course"]["length_m"], 191, delta=12)    # the simulator's loop
            self.assertEqual(f["course"]["source"], "recorded")
            self.assertTrue(s["game"]["on"])
            self.assertEqual(s["game"]["source"], "freeplay")
            self.assertTrue(s["game"]["track"]["closed"])
            self.assertEqual(s["map"]["zones"]["source"], "freeplay")       # the HUD draws the boxes
            self.assertEqual(st.request("GET", "/map/zones.json")[0], 404)  # the pit's file is untouched
            s = st.wait(lambda s: s["freeplay"]["collected"] >= 1, timeout=45, what="a box collected")
            logs = [f for f in os.listdir(st.logs) if f.startswith("session-")]
            with open(os.path.join(st.logs, logs[0])) as fh:
                recs = [json.loads(x) for x in fh]
            game = [r for r in recs if r["kind"] == "game"]
            self.assertTrue([r for r in game if r["ev"] == "item" and r.get("source") == "freeplay"])
            self.assertTrue([r for r in game if r["ev"] == "freeplay_collected"])
            tx = [r["line"] for r in recs if r["kind"] == "tx"]
            # the game sends boost (and slow for mud on 0.2.4+); freeplay sent ECO exactly once
            self.assertFalse([x for x in tx if x.split()[0] not in ("boost", "slow", "mode", "status")], tx)
            self.assertEqual([x for x in tx if x.split()[0] == "mode"], ["mode 0"], tx)
            self.assertFalse([x for x in tx if x.split()[0] == "slow" and x != "slow 5 4"], tx)
            fm = [r for r in game if r["ev"] == "freeplay_mode"]
            self.assertEqual([(r["outcome"], r["mode"]) for r in fm], [("sent", 0)])
            self.assertTrue(os.path.exists(os.path.join(st.tmp.name, "freeplay.json")))   # next to game-state.json (dirname of --cache-dir)
            code, body = st.request("POST", "/api/freeplay", {"toggle": True})
            self.assertEqual(json.loads(body)["freeplay"]["phase"], "off")
            s = st.wait(lambda s: not s["game"]["on"], what="freeplay off -> no game")
            self.assertIsNone(s["game"]["source"])
        finally:
            st.close()

    def test_freeplay_multiplayer(self):
        """Sim --peer-host: kart 3 hosts, we join (peers 3 + 5), kart 3 shells us (--hit-at: slow 5 for 4 s),
        a box gives a held item, we fire it at a peer (shell in range, or lightning at all) -> acked, score >= 1;
        then leave and host our own game (kart 5 joins us)."""
        st = Stack(["--course", "--warp", "3", "--peer-host", "--hit-at", "14"], config={"freeplay_boxes": 4})
        try:
            mp = lambda s: s["freeplay"]["multi"]   # noqa: E731
            s = st.wait(lambda s: s["self"]["gps"]["fix_fresh"] and mp(s)["fw_ok"], what="fix + fw kart-0.2.5")
            self.assertEqual((mp(s)["session"], mp(s)["held"], mp(s)["attack"]), (0, None, None))
            self.assertEqual(st.request("POST", "/api/freeplay", {"join": 3})[0], 409)        # freeplay off
            self.assertEqual(st.request("POST", "/api/freeplay", {"on": True})[0], 200)
            st.wait(lambda s: mp(s)["hosts"] == [3], what="kart 3's invite")
            for bad in ({"join": 9}, {"join": "3"}, {"host": 1}, {"use": True, "leave": True}, {"join": True}):
                self.assertEqual(st.request("POST", "/api/freeplay", bad)[0], 400, bad)
            code, body = st.request("POST", "/api/freeplay", {"use": True})
            self.assertEqual((code, json.loads(body)["error"]), (409, "nothing held"))
            code, body = st.request("POST", "/api/freeplay", {"join": 3})
            self.assertEqual(code, 200, body)
            self.assertEqual(json.loads(body)["freeplay"]["multi"]["wish"], 3)
            s = st.wait(lambda s: mp(s)["session"] == 3 and [p["id"] for p in mp(s)["peers"]] == [3, 5],
                        what="in K3's game with K3 + K5")
            self.assertFalse(mp(s)["hosting"])
            self.assertTrue({("peer", 3), ("peer", 5)} <= {(e["ev"], e.get("id")) for e in mp(s)["events"]})
            # kart 3 shells us at 14 s: hit line + the firmware's timed slow 5 for 4 s
            s = st.wait(lambda s: mp(s)["last_hit"] is not None, timeout=25, what="hit by K3")
            h = mp(s)["last_hit"]
            self.assertEqual((h["from"], h["item"], h["secs"], h["applied"]), (3, "shell", 4, True))
            st.wait(lambda s: s["slow"]["active"] is True and s["slow"]["mph"] == 5, timeout=5, what="slow 5")
            st.wait(lambda s: s["slow"]["active"] is False, timeout=8, what="the hit's slow over")
            # a box gives a held item; fire it (a shell only while a peer is within 25 m)
            used = None
            deadline = time.time() + 150
            while used is None and time.time() < deadline:
                s = st.state()
                held = mp(s)["held"]
                if held and (held["item"] == "lightning" or any(p["in_range"] for p in mp(s)["peers"])):
                    code, body = st.request("POST", "/api/freeplay", {"use": True})
                    if code == 200:
                        used = held["item"]
                        self.assertIsNone(json.loads(body)["freeplay"]["multi"]["held"])
                    else:
                        self.assertEqual(json.loads(body)["error"], "no one in range")   # moved apart meanwhile
                time.sleep(0.25)
            self.assertIsNotNone(used, "no held item used within 150 s")
            s = st.wait(lambda s: mp(s)["attack"] and mp(s)["attack"]["result"] in ("acked", "done")
                        and mp(s)["score"] >= 1, timeout=10, what="attack acked")
            a = mp(s)["attack"]
            self.assertEqual((a["item"], a["to"] == 255), (used, used == "lightning"))
            self.assertTrue(a["acked"])
            # leave, then host our own game: kart 5 joins us 3 s later
            self.assertEqual(st.request("POST", "/api/freeplay", {"leave": True})[0], 200)
            self.assertEqual(st.request("POST", "/api/freeplay", {"host": True})[0], 200)
            s = st.wait(lambda s: mp(s)["hosting"] and mp(s)["session"] == 2
                        and [p["id"] for p in mp(s)["peers"]] == [5], timeout=10, what="hosting with K5")
            logs = [f for f in os.listdir(st.logs) if f.startswith("session-")]
            with open(os.path.join(st.logs, logs[0])) as fh:
                recs = [json.loads(x) for x in fh]
            game = [r for r in recs if r["kind"] == "game"]
            evs = {r["ev"] for r in game}
            self.assertTrue({"freeplay_join", "freeplay_session", "freeplay_hit", "freeplay_item_held",
                             "freeplay_use", "freeplay_attack", "freeplay_leave", "freeplay_host"} <= evs, evs)
            self.assertTrue(all(r.get("source") == "freeplay" for r in game if r["ev"].startswith("freeplay_")))
            tx = [r["line"] for r in recs if r["kind"] == "tx"]
            self.assertIn("session join 3", tx)
            self.assertIn("session leave", tx)
            self.assertIn("session host", tx)
            self.assertTrue([x for x in tx if x.startswith("attack ")], tx)
            held = [r for r in game if r["ev"] == "item" and r.get("held")]
            self.assertTrue(held and all(r["table"] == "multi" for r in held))
            ops = [r for r in recs if r["kind"] == "op" and r.get("action") == "freeplay"]
            self.assertTrue(any(r.get("refused") == "nothing held" for r in ops))
            # freeplay off leaves the session
            self.assertEqual(st.request("POST", "/api/freeplay", {"on": False})[0], 200)
            st.wait(lambda s: mp(s)["session"] == 0 and mp(s)["wish"] is None, timeout=5, what="left on freeplay off")
        finally:
            st.close()

    def test_freeplay_multiplayer_old_firmware(self):
        """kart-0.2.3: no multiplayer; host/join are refused (409) and nothing is sent."""
        st = Stack(["--fw-old", "--peer-host"], pit=False)
        try:
            st.wait(lambda s: s["heltec"]["status"].get("fw") == "sim-kart-0.2.3", what="old fw status")
            self.assertEqual(st.request("POST", "/api/freeplay", {"on": True})[0], 200)
            code, body = st.request("POST", "/api/freeplay", {"host": True})
            self.assertEqual(code, 409)
            self.assertIn("kart-0.2.5", json.loads(body)["error"])
            self.assertEqual(st.request("POST", "/api/freeplay", {"join": 3})[0], 409)
            m = st.state()["freeplay"]["multi"]
            self.assertEqual((m["fw_ok"], m["session"], m["hosts"], m["peers"]), (False, 0, [], []))
            self.assertEqual(st.request("POST", "/api/freeplay", {"on": False})[0], 200)
            logs = [f for f in os.listdir(st.logs) if f.startswith("session-")]
            with open(os.path.join(st.logs, logs[0])) as fh:
                tx = [json.loads(x).get("line") for x in fh if '"kind":"tx"' in x or '"kind": "tx"' in x]
            self.assertFalse([x for x in tx if x and x.split()[0] in ("session", "attack")], tx)
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


class SimTimedSlow(unittest.TestCase):
    """The simulator's SLOW against the firmware kart-0.2.4 contract, in-process (no processes)."""

    def sim(self, **kw):
        sys.path.insert(0, os.path.join(KART, "sim"))
        import sim_heltec
        sim = sim_heltec.Sim(sim_heltec.sim_args(**kw))
        out = []
        sim.out.append(lambda b: out.append(json.loads(b)))
        return sim, out

    def test_timed_slow_reverts_and_old_firmware_keeps_it(self):
        sim, out = self.sim()
        sim.status()
        self.assertEqual(out[-1]["fw"], "sim-kart-0.2.5")            # 0.2.5 (multiplayer) keeps the 0.2.4 timed slow
        sim.handle_line("slow 5 4")
        self.assertEqual((out[-1]["t"], out[-1]["cmd"], out[-1]["arg"], out[-1]["src"]), ("cmd", 5, 5, 254))
        k = sim.me
        self.assertEqual(k.slow_mph, 5.0)
        sim.expire_slow(k.slow_until - 0.1)
        self.assertEqual(k.slow_mph, 5.0)
        sim.expire_slow(k.slow_until)
        self.assertIsNone(k.slow_mph)                                 # timed slow over: no cap
        sim.command(0, 5, 8)                                          # pit SLOW 8 (untimed)
        sim.handle_line("slow 5 4")
        self.assertEqual(k.slow_mph, 5.0)
        sim.expire_slow(k.slow_until)
        self.assertEqual(k.slow_mph, 8.0)                             # the pit's cap comes back
        self.assertIsNone(k.slow_until)
        sim.command(0, 5, 3)                                          # pit SLOW 3, lower than mud's 5
        sim.handle_line("slow 5 4")
        self.assertEqual((k.slow_mph, k.slow_until), (3.0, None))     # a timed slow never lifts it
        self.assertIn('"slow_mph": 3', json.dumps(k.tele("self", extra={"slow_mph": int(k.slow_mph)})))
        old, out = self.sim(fw_old=True)
        old.status()
        self.assertEqual(out[-1]["fw"], "sim-kart-0.2.3")
        old.handle_line("slow 5 4")
        self.assertEqual((old.me.slow_mph, old.me.slow_until), (5.0, None))    # 0.2.3: a PERMANENT slow 5
        old.handle_line("start")
        self.assertIsNone(old.me.slow_mph)


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
        cfg, w = self.ks.validate_config(dict(self.ks.DEFAULTS, map_fps=500, kart_id=9, sound="yes", sun_mode=1, bogus=1,
                                              map_orientation="sideways", rotate=90, pit_urls="http://x",
                                              freeplay="on", freeplay_boxes=9))
        self.assertEqual((cfg["map_fps"], cfg["kart_id"], cfg["sound"], cfg["sun_mode"], cfg["map_orientation"], cfg["rotate"]),
                         (10, 2, True, True, "heading_up", 0))
        self.assertEqual((cfg["freeplay"], cfg["freeplay_boxes"]), (True, 3))
        self.assertEqual(cfg["pit_urls"], ["http://x"])
        self.assertEqual(len(w), 9, w)
        cfg, w = self.ks.validate_config(dict(self.ks.DEFAULTS, freeplay=False, freeplay_boxes=6))
        self.assertEqual((cfg["freeplay"], cfg["freeplay_boxes"], w), (False, 6, []))
        self.assertIn("freeplay", self.ks.PUBLIC_CFG)
        self.assertIn("freeplay_boxes", self.ks.PUBLIC_CFG)
        self.assertIs(self.ks.validate_config(dict(self.ks.DEFAULTS, sun_mode=False))[0]["sun_mode"], False)
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
