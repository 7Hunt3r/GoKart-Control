#!/usr/bin/env python3
"""Simulated kart Heltec (firmware/kart-heltec-v4) for running the kart display on a Mac.

    python3 kart/sim/sim_heltec.py               # pseudo-terminal; prints the path for --port
    python3 kart/sim/sim_heltec.py --tcp 7979    # TCP instead (kart_server.py --tcp 127.0.0.1:7979)

SIMULATION ONLY. It announces itself with {"t":"log","msg":"SIMULATION"} and a
status reply with fw "sim-...", which the display turns into a striped banner.

What it prints (same shapes as the real firmware's USB output):
  self   2 Hz, own kart driving a loop near 41.0940,-73.4167 (the shop, 50 Day St)
  fleet  ~1 Hz for two other karts (3 and 5); kart 5 drops out for ~10 s every ~40 s
  log    occasional firmware-style log lines; ble every ~6 s; cmd for every command
  status in reply to "status"
Text commands honoured like the firmware's usbCommand(): mode 0|1|2, stop, start,
slow [mph], ping, status, boost SECS SCALE (src 254, own ++usbSeq). Mode changes are
"accepted" at once and "applied" (mode_actual + mode_confirmed) about 0.6 s later.
`boost SECS SCALE` (SECS 1-30, SCALE 0.10-1.00, else a usage log line and no cmd) is
a timed current-scale override like the firmware's GK_CMD_BOOST: cmd line with cmd 6,
arg = scale percent; rejected (4) while stopped, no_vesc (3) without a VESC; the
`scale` field follows it and reverts to the mode scale after SECS ("boost over" log);
STOP/START cancel it. Speed follows the live scale (17 mph at 1.0).
GPS like firmware 2026-11-06+: a {"t":"gps"} line per fix at 5 Hz (--nogpsline = off,
like `gpsline off`), and the self line's USB-only hdop / fix_age_ms / gps_hz /
base_lat / base_lon / base_fix_age_s (the pit radio's fix: 3 m east, 2 m north of the
origin; null while --pitloss has the pit unheard). --hdop X sets the reported HDOP
(e.g. 6 = poor: the kart's game ignores every sample).
Scenario flags: --kart 2 --nofix (own GPS never fixes) --novesc (VESC link down)
--fault N (VESC fault code) --pitloss (pit link drops 20 s of every 60 s)
--pit-stop-at S / --pit-start-at S (the pit sends STOP/START at those seconds)
--pit-slow-at S (the pit sends SLOW 8 at that second).
`race [secs]` (USB, SIMULATION ONLY; the driver page never sends it): like the race firmware's
GK_CMD_RACE, holds the kart at zero power at once (stopped stays false), prints
{"t":"race","event":"countdown","go_in_ms":...} and {"t":"race","event":"go"} at release;
STOP/START cancel the hold. --race-at S makes the pit send `race 5` at that second.
In-process use (kart demo mode): Sim(sim_args(...), path=PolyPath(points)), out callbacks,
sim.stopping.set() to end run().
--course: the game-layer course: the loop is centred on the shop preset origin
(pit/server/map/map.shop.json, 41.09399,-73.41637) so it runs through the boost pad,
the mud and across the start/finish line of kart/sim/zones.course.json (serve that
with fake_pit.py --zones kart/sim/zones.course.json); starts in NORMAL just before
the line. --warp X moves the own kart X times faster along the loop (tests only;
reported speeds stay real).
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

FW = "sim-kart-0.1.0"
ORIGIN = (41.0940, -73.4167)     # near the shop; not a surveyed course
COURSE_ORIGIN = (41.09399, -73.41637)   # --course: shop preset origin (map.shop.json)
R_EARTH = 6371000.0
FULL_SCALE_MPH = 17.0            # sim speed at scale 1.0 (SPORT)
MODE_SCALE = {0: 80.0 / 140.0, 1: 100.0 / 140.0, 2: 1.0}   # firmware config.h MODES[]


def en_to_latlon(e, n):
    lat = ORIGIN[0] + math.degrees(n / R_EARTH)
    lon = ORIGIN[1] + math.degrees(e / (R_EARTH * math.cos(math.radians(ORIGIN[0]))))
    return lat, lon


# A rounded-rectangle loop, 70 m x 36 m, centred on ORIGIN.
LOOP_W, LOOP_H, LOOP_R = 70.0, 36.0, 12.0
_STRAIGHT_X, _STRAIGHT_Y = LOOP_W - 2 * LOOP_R, LOOP_H - 2 * LOOP_R
LOOP_LEN = 2 * _STRAIGHT_X + 2 * _STRAIGHT_Y + 2 * math.pi * LOOP_R


class LoopPath:
    """The default rounded-rectangle loop around ORIGIN (read at call time)."""
    def __init__(self):
        self.length = LOOP_LEN

    def at(self, s):
        e, n, hd = loop_point(s)
        lat, lon = en_to_latlon(e, n)
        return lat, lon, hd


class PolyPath:
    """A closed polyline of [lat, lon] points driven in point order (demo mode)."""
    def __init__(self, points):
        if len(points) < 2:
            raise ValueError("path needs >= 2 points")
        self.ref = (float(points[0][0]), float(points[0][1]))
        cl = math.cos(math.radians(self.ref[0]))
        en = [((p[1] - self.ref[1]) * math.pi / 180 * R_EARTH * cl, (p[0] - self.ref[0]) * math.pi / 180 * R_EARTH)
              for p in points]
        self.segs = []
        acc = 0.0
        for a, b in zip(en, en[1:] + en[:1]):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L > 0:
                self.segs.append((acc, L, a, b))
                acc += L
        self.length = acc

    def at(self, s):
        s %= self.length
        for acc, L, a, b in self.segs:
            if s <= acc + L:
                f = (s - acc) / L
                e, n = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
                hd = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360
                lat = self.ref[0] + math.degrees(n / R_EARTH)
                lon = self.ref[1] + math.degrees(e / (R_EARTH * math.cos(math.radians(self.ref[0]))))
                return lat, lon, hd
        return self.ref[0], self.ref[1], 0.0


def loop_point(s):
    """(east, north, heading_deg) at arc length s along the loop (clockwise seen from above)."""
    s %= LOOP_LEN
    hx, hy = LOOP_W / 2 - LOOP_R, LOOP_H / 2 - LOOP_R
    q = math.pi * LOOP_R / 2
    segs = [("line", (-hx, LOOP_H / 2), (hx, LOOP_H / 2), _STRAIGHT_X),
            ("arc", (hx, hy), 90, q), ("line", (LOOP_W / 2, hy), (LOOP_W / 2, -hy), _STRAIGHT_Y),
            ("arc", (hx, -hy), 0, q), ("line", (hx, -LOOP_H / 2), (-hx, -LOOP_H / 2), _STRAIGHT_X),
            ("arc", (-hx, -hy), 270, q), ("line", (-LOOP_W / 2, -hy), (-LOOP_W / 2, hy), _STRAIGHT_Y),
            ("arc", (-hx, hy), 180, q)]
    for seg in segs:
        length = seg[-1]
        if s <= length:
            if seg[0] == "line":
                (x0, y0), (x1, y1) = seg[1], seg[2]
                f = s / length
                hd = math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360
                return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, hd
            (cx, cy), a0 = seg[1], seg[2]
            a = math.radians(a0 - math.degrees(s / LOOP_R))     # clockwise
            hd = (math.degrees(-a) + 180) % 360                  # tangent of a clockwise circle
            return cx + LOOP_R * math.cos(a), cy + LOOP_R * math.sin(a), hd
        s -= length
    return 0.0, 0.0, 0.0


class SimKart:
    def __init__(self, k, phase, mode=1, nofix=False, novesc=False, fault=0, path=None, full_mph=FULL_SCALE_MPH):
        self.k, self.s, self.mode = k, phase, mode
        self.path = path or LoopPath()
        self.full_mph = full_mph
        self.race_go = None             # mono of GO while in grid hold
        self.mode_actual, self.confirmed = mode, True
        self.mph = 0.0
        self.stopped = False
        self.slow_mph = None
        self.nofix, self.novesc, self.fault = nofix, novesc, fault
        self.volts = 82.0 - k * 0.7
        self.ah = 0.0
        self.seq = 0
        self.last_cmd_seq, self.cmd_result = 0, 0
        self.pending = None             # (apply_at, mode)
        self.ov = None                  # (scale, until_mono): boost / mud override

    def eff_scale(self):
        if self.stopped or self.race_go is not None:
            return 0.0
        if self.ov:
            return self.ov[0]
        return MODE_SCALE.get(self.mode_actual, 0.75)

    def target_mph(self):
        if self.stopped or self.novesc:
            return 0.0
        v = self.full_mph * self.eff_scale()
        return min(v, self.slow_mph) if self.slow_mph else v

    def step(self, dt, now):
        if self.pending and now >= self.pending[0]:
            self.mode_actual, self.confirmed, self.cmd_result = self.pending[1], True, 2
            self.pending = None
        tgt = self.target_mph()
        rate = 6.0 if tgt < self.mph else 3.0
        self.mph += max(-rate * dt, min(rate * dt, tgt - self.mph))
        self.s += self.mph * 0.44704 * dt
        amps = 8.0 + 2.5 * self.mph
        self.volts = max(60.5, self.volts - amps * dt / 3600 * 0.6)
        self.ah += amps * dt / 3600

    def gps_line(self, ms, hdop):
        lat, lon, hd = self.path.at(self.s)
        return {"t": "gps", "kart": self.k, "lat": round(lat, 7), "lon": round(lon, 7),
                "kph": round(self.mph * 1.609344, 1), "heading": round(hd, 1), "hdop": hdop, "sats": 10, "ms": ms}

    def tele(self, t, rssi=0.0, snr=0.0, rx_ms=0, base_rssi=0, uptime=0, extra=None):
        lat, lon, hd = self.path.at(self.s)
        fix = not self.nofix
        vup = not self.novesc
        erpm = int(self.mph * 1609.34 / 60 / (0.406 * math.pi) * 2.769 * 7)
        self.seq = (self.seq + 1) % 256
        scale = round(self.eff_scale(), 3)
        return {"t": t, "kart": self.k, "seq": self.seq,
                "lat": round(lat, 7) if fix else None, "lon": round(lon, 7) if fix else None,
                "fix": fix, "gps_age_s": 0 if fix else 255, "sats": 9 if fix else 3,
                "gps_kph": round(self.mph * 1.609344, 1) if fix else 0.0,
                "heading": round(hd, 1) if fix else 0.0,
                "volts": round(self.volts, 1) if vup else 0.0,
                "motor_a": round(4 + 2.2 * self.mph, 1) if vup else 0.0,
                "batt_a": round(3 + 1.4 * self.mph, 1) if vup else 0.0,
                "erpm": erpm if vup else 0, "duty": round(min(0.95, self.mph / 25), 2) if vup else 0.0,
                "esc_c": (round(31.0 + self.mph * 0.4, 1) if not self.fault else 92.5) if vup else 0.0,
                "fault": self.fault if vup else 0,
                "mode_req": self.mode, "mode_actual": self.mode_actual if vup else None,
                "scale": scale if vup else None,
                "stopped": self.stopped, "vesc_link": vup, "mode_confirmed": self.confirmed and vup and not self.stopped,
                "pps": fix, "wifi": False, "writes": True,
                "last_cmd_seq": self.last_cmd_seq, "cmd_result": self.cmd_result,
                "ah": round(self.ah, 2), "uptime_s": uptime % 65536, "base_rssi": base_rssi,
                "rssi": rssi, "snr": snr, "rx_ms": rx_ms, **(extra or {})}


def sim_args(**over):
    """Default command-line options as a namespace, for in-process use."""
    a = build_parser().parse_args([])
    for k, v in over.items():
        setattr(a, k, v)
    return a


class Sim:
    def __init__(self, args, path=None, full_mph=FULL_SCALE_MPH, start_mph=None):
        self.args = args
        self.stopping = threading.Event()
        self.lock = threading.RLock()
        self.out = []
        self.t0 = time.monotonic()
        self.usb_seq = 0
        self.boost_secs = 0
        self.me = SimKart(args.kart, 15.0 if args.course and path is None else 0.0, nofix=args.nofix,
                          novesc=args.novesc, fault=args.fault, path=path, full_mph=full_mph)
        if args.course:
            self.me.mph = FULL_SCALE_MPH * MODE_SCALE[1]     # already rolling at the line
        if start_mph is not None:
            self.me.mph = start_mph
        others = [k for k in (3, 5) if k != args.kart] or [4, 6]
        L = self.me.path.length
        self.fleet = [SimKart(others[0], 45.0 % L, mode=2, path=path), SimKart(others[1], 120.0 % L, mode=0, path=path)]
        self.pit_done = set()

    def uptime(self):
        return int(time.monotonic() - self.t0) + self.args.uptime0

    def emit(self, obj):
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        for w in list(self.out):
            try:
                w(data)
            except OSError:
                if w in self.out:
                    self.out.remove(w)

    def log(self, msg):
        self.emit({"t": "log", "kart": self.me.k, "msg": msg})

    # -- commands (same branches as the firmware's handleCommand) -----------------
    def command(self, src, cmd, arg):
        k = self.me
        if src == 254:
            self.usb_seq = (self.usb_seq + 1) % 256
            seq = self.usb_seq
        else:
            seq = random.randint(1, 255)
        k.last_cmd_seq = seq
        res = 1
        if cmd == 4:
            res = 2
        elif cmd == 1:
            if arg > 2:
                res = 4
            else:
                k.mode, k.confirmed = arg, False
                if k.novesc:
                    res = 3
                elif not k.stopped:
                    self.log(f"vesc write scale {MODE_SCALE[arg]:.3f}")
                    k.pending = (time.monotonic() + 0.6, arg)
        elif cmd == 6:
            if arg < 10 or arg > 100 or k.stopped:
                res = 4
            elif k.novesc:
                res = 3
            else:
                secs = self.boost_secs or 5
                k.ov = (arg / 100.0, time.monotonic() + secs)
                self.log(f"boost: scale {arg / 100.0:.2f} for {secs} s")
                self.log(f"vesc write scale {arg / 100.0:.3f}")
            self.boost_secs = 0
        elif cmd == 7 and (arg == 0 or 101 <= arg <= 108):  # race finish (0 = no position, 100 + P)
            k.race_go = None
            self.emit({"t": "race", "kart": k.k, "event": "finish", "position": 0 if arg == 0 else arg - 100})
            self.log(f"race: finish P{0 if arg == 0 else arg - 100}")
        elif cmd == 7:                                       # race: grid hold, GO arg seconds later
            if arg < 1 or arg > 30:
                res = 4
            else:
                k.race_go, k.ov = time.monotonic() + arg, None
                self.emit({"t": "race", "kart": k.k, "event": "countdown", "go_in_ms": arg * 1000, "secs": arg,
                           "go_utc": 0})
                self.log(f"race: grid hold, GO in {arg * 1000} ms")
                if k.novesc:
                    res = 3
        elif cmd == 2:
            if k.novesc:
                res = 3
            else:
                k.stopped, k.confirmed, k.ov, k.race_go = True, False, None, None
                for sc in (0.5, 0.2, 0.0):
                    self.log(f"vesc write scale {sc:.3f}")
        elif cmd == 3:
            k.stopped, k.slow_mph, k.ov, k.race_go = False, None, None, None
            if k.novesc:
                res = 3
            else:
                k.pending = (time.monotonic() + 0.6, k.mode)
                self.log(f"vesc write scale {MODE_SCALE[k.mode]:.3f}")
        elif cmd == 5:
            if k.novesc:
                res = 3
            else:
                mph = arg or 5
                k.slow_mph = float(mph)
                self.log(f"slow: cap {mph} mph = {mph * 1609.34 / 60 / (0.406 * math.pi) * 2.769 * 7:.0f} ERPM")
        else:
            res = 4
        k.cmd_result = res
        self.emit({"t": "cmd", "kart": k.k, "src": src, "seq": seq, "cmd": cmd, "arg": arg, "result": res})

    def status(self):
        k = self.me
        lat, lon, _ = k.path.at(k.s)
        self.emit({"t": "status", "kart": k.k, "fw": FW, "built": "sim", "writes": True, "transport": "ble",
                   "radio": True, "radio_status": 0, "lora_tx": self.uptime() * 2, "lora_rx": self.uptime(),
                   "lora_rx_bad": 0, "gps_locked": True, "gps_cfg": "9600", "pps_count": self.uptime(),
                   "vesc_fw": "6.05", "vesc_hw": "SIM", "vesc_packets": self.uptime() * 10, "vesc_crc_err": 0,
                   "vesc_limits": not k.novesc, "rate": 2, "sync": "pps", "pit_karts_online": 3,
                   "gps_fix": not k.nofix, "gps_sats": 9, "gps_hdop": 0.9, "gps_age_ms": 200,
                   "gps_lat": lat if not k.nofix else 0.0, "gps_lon": lon if not k.nofix else 0.0, "heap": 180000})

    def handle_line(self, line):
        line = line.strip()
        if not line:
            return
        word, _, rest = line.partition(" ")
        rest = rest.strip()
        try:
            arg = int(rest) if rest else 0
        except ValueError:
            arg = 0
        if word == "boost":
            parts = rest.split()
            try:
                secs = int(parts[0]) if parts else 5
                sc = float(parts[1]) if len(parts) > 1 else 1.0
            except ValueError:
                secs, sc = 0, 0.0
            if secs < 1 or secs > 30 or sc < 0.1 or sc > 1.0:
                self.log("usage: boost SECS(1-30) SCALE(0.10-1.00)")
            else:
                self.boost_secs = secs
                self.command(254, 6, int(round(sc * 100)))
        elif word == "status":
            self.status()
        elif word == "mode" and rest:
            self.command(254, 1, arg)
        elif word == "race":
            self.command(254, 7, arg if rest else 5)
        elif word == "finish":                               # SIMULATION: as if the pit sent race 100+P
            self.command(254, 7, 100 + arg if rest and arg else 0)
        elif word in ("stop", "start", "ping", "slow"):
            self.command(254, {"stop": 2, "start": 3, "ping": 4, "slow": 5}[word], arg)
        elif word == "help":
            self.log("commands: status | mode 0..2 | stop | start | slow [mph] | boost SECS SCALE | race [secs] | ping (SIMULATION)")
        else:
            self.log(f"unknown command: {line}")

    # -- main loop ---------------------------------------------------------------
    def pit_link_up(self, now):
        return not (self.args.pitloss and (now - self.t0) % 60.0 >= 40.0)

    def run(self):
        last = time.monotonic()
        next_self = last
        next_gps = last + 0.1
        next_fleet = [last + 0.3, last + 0.8]
        next_ble = last + 1.0
        next_log = last + 4.0
        while not self.stopping.is_set():
            time.sleep(0.05)
            now = time.monotonic()
            dt, last = now - last, now
            el = now - self.t0
            with self.lock:
                for name, at, src_cmd in (("race", self.args.race_at, (7, 5)),
                                          ("stop", self.args.pit_stop_at, (2, 0)),
                                          ("start", self.args.pit_start_at, (3, 0)),
                                          ("slow", self.args.pit_slow_at, (5, 8))):
                    if at is not None and el >= at and name not in self.pit_done:
                        self.pit_done.add(name)
                        self.command(0, *src_cmd)
                if self.me.race_go is not None and now >= self.me.race_go:
                    self.me.race_go = None
                    self.emit({"t": "race", "kart": self.me.k, "event": "go"})
                    self.log("race: GO")
                if self.me.ov and now >= self.me.ov[1]:
                    self.me.ov = None
                    self.log(f"boost over, back to {self.me.eff_scale():.2f}")
                    self.log(f"vesc write scale {self.me.eff_scale():.3f}")
                self.me.step(dt * self.args.warp, now)
                for f in self.fleet:
                    f.step(dt, now)
                if now >= next_gps and not self.args.nofix:
                    next_gps += 0.2
                    if not self.args.nogpsline:
                        self.emit(self.me.gps_line(int(el * 1000), self.args.hdop))
                if now >= next_self:
                    next_self += 0.5
                    up = self.pit_link_up(now)
                    blat, blon = en_to_latlon(3.0, 2.0)
                    extra = {"hdop": 99.0 if self.args.nofix else self.args.hdop, "fix_age_ms": 999999 if self.args.nofix else 150,
                             "gps_hz": 5, "base_lat": round(blat, 7) if up else None,
                             "base_lon": round(blon, 7) if up else None, "base_fix_age_s": 1 if up else None}
                    self.emit(self.me.tele("self", rx_ms=int(el * 1000), uptime=self.uptime(),
                                           base_rssi=-71 + random.randint(-6, 6) if up else 0, extra=extra))
                for i, f in enumerate(self.fleet):
                    if now >= next_fleet[i]:
                        next_fleet[i] += 1.0
                        if i == 1 and el % 40.0 > 30.0:      # second kart drops out ~10 s in every 40 s
                            continue
                        self.emit(f.tele("fleet", rssi=-80 - 6 * i + random.randint(-4, 4),
                                         snr=round(8.5 - 2 * i + random.uniform(-1, 1), 1),
                                         rx_ms=int(el * 1000), base_rssi=-70, uptime=self.uptime()))
                if now >= next_ble:
                    next_ble += 6.0
                    self.emit({"t": "ble", "kart": self.me.k, "pinned": "sim:00:00:00:00:01",
                               "connected": not self.args.novesc, "candidates": []})
                if now >= next_log:
                    next_log += 17.0
                    self.log(random.choice(["gps found: 9600 baud", "vesc write verify ok (scale 0.714)",
                                            "lora rx ok", "vesc fw 6.05 SIM uuid 00sim"]))

    # -- transports ----------------------------------------------------------------
    def serve_pty(self):
        master, slave = os.openpty()
        tty.setraw(slave)
        os.set_blocking(master, False)
        path = os.ttyname(slave)

        def write(data):
            try:
                os.write(master, data)
            except BlockingIOError:
                pass
        self.out.append(write)
        print(path, flush=True)
        print(f"SIMULATION Heltec kart {self.me.k} on {path}  (python3 kart/server/kart_server.py --port {path})",
              file=sys.stderr, flush=True)
        self.log("SIMULATION")
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
        print(f"SIMULATION Heltec kart {self.me.k} on TCP 127.0.0.1:{srv.getsockname()[1]}  "
              f"(python3 kart/server/kart_server.py --tcp 127.0.0.1:{srv.getsockname()[1]})",
              file=sys.stderr, flush=True)
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        lock = threading.Lock()

        def write(data):
            with lock:
                conn.sendall(data)
        with self.lock:
            self.out.append(write)
            write((json.dumps({"t": "log", "kart": self.me.k, "msg": "SIMULATION"}) + "\n").encode())
        try:
            for raw in conn.makefile("rb"):
                with self.lock:
                    self.handle_line(raw.decode("utf-8", "replace"))
        except OSError:
            pass
        with self.lock:
            if write in self.out:
                self.out.remove(write)
        conn.close()


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tcp", type=int, metavar="PORT", help="serve TCP on 127.0.0.1:PORT instead of a pty (0 = any)")
    ap.add_argument("--kart", type=int, default=2)
    ap.add_argument("--nofix", action="store_true")
    ap.add_argument("--novesc", action="store_true")
    ap.add_argument("--fault", type=int, default=0)
    ap.add_argument("--pitloss", action="store_true")
    ap.add_argument("--pit-stop-at", type=float)
    ap.add_argument("--pit-start-at", type=float)
    ap.add_argument("--pit-slow-at", type=float)
    ap.add_argument("--uptime0", type=int, default=5, help="Heltec uptime at sim start (s)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--course", action="store_true", help="drive the game course (kart/sim/zones.course.json)")
    ap.add_argument("--warp", type=float, default=1.0, help="own kart moves this many times faster (tests)")
    ap.add_argument("--nogpsline", action="store_true", help="no 5 Hz gps lines (firmware `gpsline off`)")
    ap.add_argument("--hdop", type=float, default=0.9, help="reported HDOP (> 4 = poor: the game ignores it)")
    ap.add_argument("--race-at", type=float, help="the pit sends `race 5` at this second")
    return ap


def main():
    args = build_parser().parse_args()
    if args.course:
        global ORIGIN
        ORIGIN = COURSE_ORIGIN
    if args.seed is not None:
        random.seed(args.seed)
    sim = Sim(args)
    threading.Thread(target=sim.run, daemon=True).start()
    try:
        sim.serve_tcp(args.tcp) if args.tcp is not None else sim.serve_pty()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
