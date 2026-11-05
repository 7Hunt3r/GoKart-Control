"""End-to-end smoke test: simulator (TCP) + pit server on free ports.

Starts real processes, talks HTTP to the server, and checks that a STOP ALL
goes round the full loop (server -> sim -> echoed in tele -> outcome table).
Run:  python3 -I pit/tests/test_e2e.py
"""
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

PIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(PIT, "sim", "sim_tbeam.py")
SERVER = os.path.join(PIT, "server", "pit_server.py")


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
        cfg_path = os.path.join(self.tmp.name, "config.json")
        with open(cfg_path, "w") as f:
            json.dump(config or {}, f)
        self.srv = subprocess.Popen([sys.executable, "-I", SERVER, "--tcp", first[1], "--bind", "127.0.0.1",
                                     "--http-port", str(self.http_port), "--config", cfg_path,
                                     "--log-dir", os.path.join(self.tmp.name, "logs")],
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

    def test_auth_and_offline_kart(self):
        st = Stack(["--offline", "8", "--flaky", "0", "--loss", "0"],
                   config={"password": "letmein", "trust_localhost": False})
        try:
            self.assertEqual(st.request("GET", "/api/state")[0], 401)
            self.assertEqual(st.request("GET", "/")[2].get("Location"), "/login")
            self.assertEqual(st.request("GET", "/map/package.json")[0], 200)   # kart displays: public
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
