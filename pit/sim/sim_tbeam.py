#!/usr/bin/env python3
"""Simulated T-Beam base station speaking SERIAL_PROTOCOL.md (base-0.1.0).

    python3 sim_tbeam.py                 # pseudo-terminal; prints the path to pass to --port
    python3 sim_tbeam.py --tcp 7878      # TCP server instead (pit_server.py --tcp 127.0.0.1:7878)

SIMULATION ONLY. It announces itself with {"t":"log","msg":"SIMULATION"} and
stat.fw = "sim-...", which the pit server turns into a visible banner.

Eight karts drive an oval near an approximate Manresa Island coordinate (not a
surveyed course). Default scenario, each switchable with 0 to disable:
  --fault 5    kart 5 reports VESC fault 5 (OVER_TEMP_FET) with a hot ESC
  --nofix 6    kart 6 has no GPS fix (lat/lon null)
  --flaky 7    kart 7 drops out for ~8 s every ~30 s (goes stale)
  --offline 8  kart 8 never transmits
  --novesc 0   a kart whose VESC link is down (answers commands with no_vesc)
Commands from the host are honoured: echoed in last_cmd_seq/cmd_result,
stop/start/mode change the simulated kart, boost raises the echoed `scale` (and
the speed) for 5 s like the firmware. --loss drops that fraction of command
receptions per kart so the retry path can be watched.

Game zones: `--print-zones` prints a zones.json document matching the oval (a
lap line across the bottom straight, a boost pad on the top straight, mud on the
east bend), ready to POST to the pit's /zones. `--track-scale 0.3` shrinks the
oval so laps come round in about 15 s (used by the e2e test).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import select
import socket
import sys
import threading
import time
import tty

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))
from pitcore import erpm_for_mph  # noqa: E402

FW = "sim-0.1.0"
ORIGIN = (41.0727, -73.4047)   # approximate, unsurveyed point near Manresa Island, Norwalk CT
MODE_MAX_MS = {0: 6.0, 1: 9.0, 2: 12.0}      # simulated top speeds per mode (not real limits)
R_EARTH = 6371000.0


def en_to_latlon(e, n):
    lat = ORIGIN[0] + math.degrees(n / R_EARTH)
    lon = ORIGIN[1] + math.degrees(e / (R_EARTH * math.cos(math.radians(ORIGIN[0]))))
    return lat, lon


TRACK_SCALE = 1.0


def track_dims():
    return 120.0 * TRACK_SCALE, 35.0 * TRACK_SCALE


def sim_zones():
    """zones.json for the simulated oval (see ZONES.md); enabled."""
    L, r = track_dims()
    per = 2 * L + 2 * math.pi * r
    half = min(10.0, r * 0.8)
    ll = lambda e, n: [round(x, 7) for x in en_to_latlon(e, n)]  # noqa: E731
    blat, blon = en_to_latlon(0.0, r)
    mlat, mlon = en_to_latlon(L / 2 + r, 0.0)
    return {"_comment": "Simulator oval zones (pit/sim/sim_tbeam.py --print-zones); not a real course.",
            "version": 1, "enabled": True, "zones": [
                {"id": "lap", "type": "lapline", "name": "Start / finish", "shape": "line",
                 "a": ll(0.0, -r - half), "b": ll(0.0, -r + half),
                 "min_lap_s": 20 if TRACK_SCALE >= 1.0 else max(5, int(per / 24))},
                {"id": "b1", "type": "boost", "name": "Boost pad", "shape": "circle",
                 "lat": round(blat, 7), "lon": round(blon, 7), "r_m": 12, "scale": 1.0, "secs": 5, "cooldown_s": 20},
                {"id": "m1", "type": "mud", "name": "Mud", "shape": "circle",
                 "lat": round(mlat, 7), "lon": round(mlon, 7), "r_m": 15, "scale": 0.5}]}


def oval(s):
    """Point and heading on a 2*120 m straight + r=35 m loop (times --track-scale), s in metres along it."""
    L, r = track_dims()
    per = 2 * L + 2 * math.pi * r
    s %= per
    if s < L:
        return (-L / 2 + s, -r, 90.0)
    s -= L
    if s < math.pi * r:
        a = -math.pi / 2 + s / r
        return (L / 2 + r * math.cos(a), r * math.sin(a), (90.0 - math.degrees(a + math.pi / 2)) % 360)
    s -= math.pi * r
    if s < L:
        return (L / 2 - s, r, 270.0)
    s -= L
    a = math.pi / 2 + s / r
    return (-L / 2 + r * math.cos(a), r * math.sin(a), (90.0 - math.degrees(a + math.pi / 2)) % 360)


class SimKart:
    def __init__(self, k, scen):
        self.k = k
        self.s = k * 45.0
        self.v = 0.0
        self.mode_req = None
        self.mode_actual = 1                   # what the "VESC" is running at boot
        self.mode_pending_at = None
        self.stopped = False
        self.voc = 82.5 - k * 0.9              # open-circuit pack volts
        self.esc_c = 28.0 + k
        self.ah = 0.0
        self.seq = random.randint(0, 255)
        self.last_cmd_seq = 0
        self.cmd_result = 0
        self.fault = 5 if k == scen.fault else 0
        self.nofix = k == scen.nofix
        self.flaky = k == scen.flaky
        self.offline = k == scen.offline
        self.vesc_link = k != scen.novesc
        self.boot = time.monotonic() - random.uniform(100, 900)
        self.target_jitter = random.uniform(-1.0, 1.0)
        self.batt_a = 0.0
        self.slow_mph = 0
        self.boost_scale = None                # timed override (firmware GK_CMD_BOOST), None = off
        self.boost_until = 0.0

    def silent(self, now):
        if self.offline:
            return True
        return self.flaky and (now % 30.0) > 22.0

    def step(self, dt, now):
        if self.mode_pending_at and now >= self.mode_pending_at:
            self.mode_actual = self.mode_req
            self.mode_pending_at = None
            if self.cmd_result == 1:
                self.cmd_result = 2
        top = 0.0 if self.stopped else MODE_MAX_MS.get(self.mode_actual if self.mode_actual is not None else 1)
        if self.boost_scale is not None and now >= self.boost_until:
            self.boost_scale = None            # auto-revert to the mode scale
        if self.boost_scale is not None and not self.stopped:
            top *= self.boost_scale / self.mode_scale()
        top += self.target_jitter + 0.8 * math.sin(now / 7.0 + self.k)
        top = max(0.0, top)
        accel = max(-6.0, min(2.5, (top - self.v) * 0.8))
        self.v = max(0.0, self.v + accel * dt)
        self.s += self.v * dt
        motor_a = 0.0 if self.stopped else max(0.0, accel * 22.0 + self.v * 2.0 + random.uniform(-2, 2))
        self.batt_a = motor_a * min(1.0, 0.25 + self.v / 14.0)
        self.voc = max(66.0, self.voc - self.batt_a * dt / 3600.0 * 0.6)
        self.ah += self.batt_a * dt / 3600.0
        target_t = (84.0 if self.fault else 34.0 + self.batt_a * 0.25)
        self.esc_c += (target_t - self.esc_c) * dt / 40.0
        self.motor_a = motor_a

    def mode_scale(self):
        return {0: 0.5, 1: 0.75, 2: 1.0}.get(self.mode_actual, 0.75)

    def tele(self, rx_ms):
        self.seq = (self.seq + 1) % 256
        e, n, hdg = oval(self.s)
        lat, lon = en_to_latlon(e + random.gauss(0, 0.8), n + random.gauss(0, 0.8))
        fix = not self.nofix
        mph = self.v * 2.23694
        volts = round(self.voc - self.batt_a * 0.045, 2)
        vl = self.vesc_link
        return {
            "t": "tele", "kart": self.k, "seq": self.seq,
            "lat": round(lat, 7) if fix else None, "lon": round(lon, 7) if fix else None,
            "fix": fix, "gps_age_s": 0 if fix else 255, "sats": random.randint(8, 12) if fix else 0,
            "gps_kph": round(self.v * 3.6 + random.gauss(0, 0.3), 1) if fix else None,
            "heading": round(hdg, 1) if fix else None,
            "volts": volts if vl else None, "motor_a": round(self.motor_a, 1) if vl else None,
            "batt_a": round(self.batt_a, 1) if vl else None,
            "erpm": int(erpm_for_mph(mph)) if vl else None,
            "duty": round(min(0.95, self.v / 14.0), 3) if vl else None,
            "esc_c": round(self.esc_c, 1) if vl else None, "fault": self.fault if vl else None,
            "mode_req": self.mode_req, "mode_actual": self.mode_actual if vl else None,
            "scale": ((self.boost_scale if self.boost_scale is not None else {0: 0.5, 1: 0.75, 2: 1.0}.get(self.mode_actual))
                      if not self.stopped else 0.0) if vl else None,
            "stopped": self.stopped, "vesc_link": vl,
            "mode_confirmed": vl and self.mode_req is not None and self.mode_req == self.mode_actual,
            "pps": fix, "wifi": False, "writes": True,
            "last_cmd_seq": self.last_cmd_seq, "cmd_result": self.cmd_result,
            "ah": round(self.ah, 3), "uptime_s": int(time.monotonic() - self.boot),
            "base_rssi": random.randint(-95, -70), "rssi": random.randint(-98, -72), "slow_mph": self.slow_mph, "relayed": False,
            "snr": round(random.uniform(2.0, 9.5), 1), "rx_ms": rx_ms,
        }

    def command(self, msg, now):
        seq = msg.get("seq")
        if seq == self.last_cmd_seq:
            return                              # contract: repeat of last handled seq is ignored
        self.last_cmd_seq = seq
        cmd = msg.get("cmd")
        if cmd == "ping":
            self.cmd_result = 2
            return
        if not self.vesc_link:
            self.cmd_result = 3
            return
        if cmd == "stop":
            self.stopped, self.cmd_result = True, 2
            self.boost_scale = None
        elif cmd == "slow":
            self.slow_mph = int(msg.get("arg") or 5); self.cmd_result = 2
        elif cmd == "start":
            self.stopped, self.cmd_result = False, 2
            self.boost_scale, self.slow_mph = None, 0   # firmware: START clears SLOW and BOOST
        elif cmd == "boost":
            arg = msg.get("arg")
            if self.stopped or not isinstance(arg, int) or not 10 <= arg <= 100:
                self.cmd_result = 4
            else:
                self.boost_scale, self.boost_until, self.cmd_result = arg / 100.0, now + 5.0, 2
        elif cmd == "mode" and msg.get("arg") in (0, 1, 2):
            self.mode_req = msg["arg"]
            self.cmd_result = 1                 # accepted; becomes applied when "re-read" confirms
            self.mode_pending_at = now + 1.2
        else:
            self.cmd_result = 4


class Sim:
    def __init__(self, args):
        self.args = args
        self.karts = [SimKart(k, args) for k in range(1, 9)]
        self.lock = threading.Lock()
        self.out = []                           # writers: callables(bytes)
        self.t0 = time.monotonic()
        self.rx = self.tx = 0
        self.pending = []                       # (due, kart, msg)

    def emit(self, obj):
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        for w in list(self.out):
            try:
                w(data)
            except OSError:
                self.out.remove(w)

    def millis(self):
        return int((time.monotonic() - self.t0) * 1000)

    def handle_line(self, line):
        line = line.strip()
        if not line:
            return
        if not line.startswith("{"):
            word = line.split()[0]
            self.emit({"t": "log", "msg": f"sim: maintenance command '{word}' ignored"})
            return
        try:
            msg = json.loads(line)
        except ValueError:
            self.emit({"t": "log", "msg": "sim: bad json from host"})
            return
        self.tx += 1
        self.emit({"t": "sent", "seq": msg.get("seq"), "cmd": msg.get("cmd"),
                   "kart": msg.get("kart"), "arg": msg.get("arg")})
        now = time.monotonic()
        for sk in self.karts:
            if msg.get("kart") not in (sk.k, 255) or sk.silent(now):
                continue
            if random.random() < self.args.loss:
                continue
            self.pending.append((now + random.uniform(0.15, 0.5), sk, msg))

    def run(self):
        next_tele = {sk.k: time.monotonic() + 0.1 * sk.k for sk in self.karts}
        next_sec = time.monotonic()
        last = time.monotonic()
        while True:
            time.sleep(0.05)
            now = time.monotonic()
            dt, last = now - last, now
            with self.lock:
                for sk in self.karts:
                    sk.step(dt, now)
                due = [p for p in self.pending if p[0] <= now]
                self.pending = [p for p in self.pending if p[0] > now]
                for _, sk, msg in due:
                    sk.command(msg, now)
                for sk in self.karts:
                    if now >= next_tele[sk.k]:
                        next_tele[sk.k] += 1.0
                        if not sk.silent(now):
                            self.rx += 1
                            self.emit(sk.tele(self.millis()))
                if now >= next_sec:
                    next_sec += 1.0
                    online = sum(1 for sk in self.karts if not sk.silent(now))
                    self.emit({"t": "base", "lat": ORIGIN[0], "lon": ORIGIN[1], "fix": True,
                               "sats": 11, "utc": int(time.time()), "pps": True})
                    self.emit({"t": "stat", "fw": FW, "radio": "sx1262", "rx": self.rx, "tx": self.tx,
                               "rx_bad": 0, "uptime_s": int(now - self.t0), "batt_v": 4.05,
                               "wifi": None, "karts_online": online})

    # -- transports ------------------------------------------------------------
    def serve_pty(self):
        master, slave = os.openpty()
        tty.setraw(slave)
        os.set_blocking(master, False)
        path = os.ttyname(slave)

        def write(data):
            try:
                os.write(master, data)
            except BlockingIOError:
                pass                            # nobody reading: drop, like a real USB CDC
        self.out.append(write)
        print(path, flush=True)
        print(f"SIMULATION T-Beam on {path}  (python3 pit/server/pit_server.py --port {path})",
              file=sys.stderr, flush=True)
        self.emit({"t": "log", "msg": "SIMULATION"})
        buf = b""
        while True:
            r, _, _ = select.select([master], [], [], 1.0)
            if not r:
                continue
            try:
                chunk = os.read(master, 4096)
            except (BlockingIOError, OSError):
                time.sleep(0.05)
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                with self.lock:
                    self.handle_line(line.decode("utf-8", "replace"))

    def serve_tcp(self, port):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(4)
        print(f"tcp 127.0.0.1:{srv.getsockname()[1]}", flush=True)
        print(f"SIMULATION T-Beam on TCP 127.0.0.1:{srv.getsockname()[1]}  "
              f"(python3 pit/server/pit_server.py --tcp 127.0.0.1:{srv.getsockname()[1]})",
              file=sys.stderr, flush=True)
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        conn.settimeout(None)
        lock = threading.Lock()

        def write(data):
            with lock:
                conn.sendall(data)
        with self.lock:
            self.out.append(write)
            write(b'{"t":"log","msg":"SIMULATION"}\n')
        f = conn.makefile("rb")
        try:
            for raw in f:
                with self.lock:
                    self.handle_line(raw.decode("utf-8", "replace"))
        except OSError:
            pass
        with self.lock:
            if write in self.out:
                self.out.remove(write)
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tcp", type=int, metavar="PORT", help="serve TCP on 127.0.0.1:PORT instead of a pty (0 = any)")
    ap.add_argument("--fault", type=int, default=5)
    ap.add_argument("--nofix", type=int, default=6)
    ap.add_argument("--flaky", type=int, default=7)
    ap.add_argument("--offline", type=int, default=8)
    ap.add_argument("--novesc", type=int, default=0)
    ap.add_argument("--loss", type=float, default=0.1, help="per-kart command loss fraction")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--track-scale", type=float, default=1.0, help="shrink/grow the oval (0.3 = ~15 s laps)")
    ap.add_argument("--print-zones", action="store_true", help="print zones.json for this oval and exit")
    args = ap.parse_args()
    global TRACK_SCALE
    TRACK_SCALE = max(0.1, args.track_scale)
    if args.print_zones:
        print(json.dumps(sim_zones(), indent=2))
        return
    if args.seed is not None:
        random.seed(args.seed)
    sim = Sim(args)
    threading.Thread(target=sim.run, daemon=True).start()
    try:
        if args.tcp is not None:
            sim.serve_tcp(args.tcp)
        else:
            sim.serve_pty()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
