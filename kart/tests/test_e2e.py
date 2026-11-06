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
    def __init__(self, sim_args=(), pit=True):
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
            json.dump({"map_poll_s": 2, "kart_id": 2}, f)
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
