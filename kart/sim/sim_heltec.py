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
slow [MPH] [SECS], ping, status, boost SECS SCALE (src 254, own ++usbSeq). Mode changes are
"accepted" at once and "applied" (mode_actual + mode_confirmed) about 0.6 s later.
`boost SECS SCALE` (SECS 1-30, SCALE 0.10-1.00, else a usage log line and no cmd) is
a timed current-scale override like the firmware's GK_CMD_BOOST: cmd line with cmd 6,
arg = scale percent; rejected (4) while stopped, no_vesc (3) without a VESC; the
`scale` field follows it and reverts to the mode scale after SECS ("boost over" log);
STOP/START cancel it. Speed follows the live scale (17 mph at 1.0) under the mode's speed cap
(MODE_TOP_MPH; a boost lifts it to the highest mode whose scale it reaches, like firmware 0.2.5).
SLOW like firmware kart-0.2.4: `slow MPH SECS` (SECS 1-60) caps MPH for SECS s and then returns to
the previous cap (an untimed pit SLOW that was in force comes back; a timed slow never lifts a lower
pit cap); `slow [MPH]` is untimed (until START). The self line reports `slow_mph` (0 = none) and the
status reply fw "sim-kart-0.2.5" (0.2.4's timed slow + multiplayer, below). --fw-old acts like kart-0.2.3: fw "sim-kart-0.2.3" and `slow 5 4`
reads as a PERMANENT slow 5 (the firmware's atoi), which is why the kart only sends it to 0.2.4+.
GPS like firmware 2026-11-06+: a {"t":"gps"} line per fix at 5 Hz (--nogpsline = off,
like `gpsline off`), and the self line's USB-only hdop / fix_age_ms / gps_hz /
base_lat / base_lon / base_fix_age_s (the pit radio's fix: 3 m east, 2 m north of the
origin; null while --pitloss has the pit unheard). --hdop X sets the reported HDOP
(e.g. 6 = poor: the kart's game ignores every sample).
Scenario flags: --kart 2 --nofix (own GPS never fixes) --novesc (VESC link down)
--fault N (VESC fault code) --pitloss (pit link drops 20 s of every 60 s)
--pit-stop-at S / --pit-start-at S (the pit sends STOP/START at those seconds)
--pit-slow-at S (the pit sends SLOW 8 at that second).
--race-pos "3,2,2,1" (--race-pos-step 10): the pit beacon's running position for this kart,
stepping through the list every N s (from GO when a race countdown runs, else from start; with
--race-at nothing before that countdown; the last value holds). Like the race firmware: self lines carry "race_pos" (0 = none) and "race_state"
(0 idle, 1 countdown, 2 running, 3 finished), and {"t":"race","event":"position","position":P,
"state":S} is printed whenever either changes.
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
MULTIPLAYER FREEPLAY like firmware kart-0.2.5 (docs/freeplay-multiplayer.md sections 3/4; fw "sim-kart-0.2.5";
--fw-old ignores all of it with an "unknown command" log line and prints none of the new fields):
`session host` (we host, kart 5 joins us 3 s later) | `session join N` (only while N is heard hosting, else a
"rejected" log line and no change) | `session leave`; `attack N shell|lightning` / `attack all ...` (rejected
without a session or a heard peer in it; a peer acks ~1 s later: `acked`, score +1; `all` runs 5 s and ends
`done` with every peer that acked). Prints {"t":"game"} once a second while in a session or a host is heard,
at once on every change and once when it all goes back to nothing; {"t":"attack"} sent + one terminal line;
self/fleet lines carry session/atk_to/atk_item/atk_seq/ack_from/ack_seq/score. A joined kart leaves by itself
when its host is not heard for 10 s ("host lost") or stops hosting ("host left").
--peer-host: fleet kart 3 hosts from the start and kart 5 joins it after 5 s. --hit-at S: at second S (once we
are in a session) a peer in it (kart 3 first) shells us: a {"t":"hit"} line and the same timed `slow 5 4` as
the USB command (skipped, applied:false, while STOPPED / grid hold / no VESC). The fleet karts drive the same
loop at their own speeds, so with --warp the own kart laps them and they come within 25 m now and then.
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

FW = "sim-kart-0.2.5"            # "sim-" = the display's SIMULATION banner; kart-0.2.4 = timed `slow MPH SECS`,
                                 # kart-0.2.5 = multiplayer freeplay (`session`, `attack`, game/hit/attack lines)
FW_OLD = "sim-kart-0.2.3"        # --fw-old: before timed slow and multiplayer
ITEM_CODES = {"shell": 1, "lightning": 2}
ITEM_NAMES = {v: k for k, v in ITEM_CODES.items()}
HIT_SECS = {"shell": 4, "lightning": 3}
ADDR_ALL = 255
ORIGIN = (41.0940, -73.4167)     # near the shop; not a surveyed course
COURSE_ORIGIN = (41.09399, -73.41637)   # --course: shop preset origin (map.shop.json)
R_EARTH = 6371000.0
FULL_SCALE_MPH = 17.0            # sim speed at scale 1.0 (SPORT)
MODE_SCALE = {0: 0.50, 1: 0.75, 2: 1.0}      # firmware config.h MODES[] (checked by firmware/tests/test_protocol.py)
MODE_TOP_MPH = {0: 15, 1: 25, 2: 0}          # firmware speed cap per mode, 0 = none; a boost lifts it (cap_mph)


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
        self.slow_until = None          # mono end of a timed slow (firmware 0.2.4 slowTimed / slowUntilMs)
        self.slow_prev = None           # the untimed cap a timed slow replaced (None = none)
        self.nofix, self.novesc, self.fault = nofix, novesc, fault
        self.volts = 82.0 - k * 0.7
        self.ah = 0.0
        self.seq = 0
        self.last_cmd_seq, self.cmd_result = 0, 0
        self.pending = None             # (apply_at, mode)
        self.ov = None                  # (scale, until_mono): boost / mud override
        # multiplayer freeplay (fleet karts; firmware kart-0.2.5 telemetry fields)
        self.session = 0
        self.plan = None                # (session, at_mono): a scripted join/leave
        self.last_heard = None          # mono of its last fleet frame (the "heard in the last 10 s" rule)
        self.atk = (0, 0, 0, 0.0)       # (to, item code, seq, until_mono) repeated in its frames
        self.ack = (0, 0, 0.0)          # (from, seq, until_mono)

    def eff_scale(self):
        if self.stopped or self.race_go is not None:
            return 0.0
        if self.ov:
            return self.ov[0]
        return MODE_SCALE.get(self.mode_actual, 0.75)

    def cap_mph(self):
        """The firmware's speed cap for the live state (kart-0.2.5): a boost above the mode's scale (not mud)
        lifts it to the highest mode whose scale the boost reaches (0.75 -> NORMAL, 1.0 -> SPORT), an in-between
        scale still one mode; then SLOW on top; None = uncapped."""
        m = self.mode_actual if self.mode_actual is not None else 1
        if self.ov and self.ov[0] > MODE_SCALE.get(m, 1.0) + 0.001 and m < 2:
            reach = max(x for x, sc in MODE_SCALE.items() if sc <= self.ov[0] + 0.001)
            m = max(m + 1, reach)
        cap = MODE_TOP_MPH.get(m) or None
        if self.slow_mph and (cap is None or self.slow_mph < cap):
            cap = self.slow_mph
        return cap

    def target_mph(self):
        if self.stopped or self.novesc:
            return 0.0
        v = self.full_mph * self.eff_scale()
        cap = self.cap_mph()
        return min(v, cap) if cap else v

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
        erpm = int(self.mph * 1609.34 / 60 / (0.406 * math.pi) * 2.769 * 7 * 2.48)   # x WHEEL_CAL (kartcore)
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


def parse_pos_seq(text):
    """"3,2,2,1" -> [3, 2, 2, 1] (each 0-8); empty/None -> []."""
    if not text:
        return []
    out = []
    for x in str(text).split(","):
        p = int(x.strip())
        if not 0 <= p <= 8:
            raise ValueError(f"race position {p} not in 0-8")
        out.append(p)
    return out


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
        self.slow_secs = 0
        self.fw = FW_OLD if getattr(args, "fw_old", False) else FW
        self.pos_seq = parse_pos_seq(getattr(args, "race_pos", None))
        self.pos_step = float(getattr(args, "race_pos_step", None) or 10.0)
        self.pos_t0 = time.monotonic()
        # with a scheduled race (--race-at) the beacon has no position until the countdown
        self.pos_wait = getattr(args, "race_at", None) is not None
        self.race_seen = bool(self.pos_seq) and not self.pos_wait
        self.race_finished = False
        self.race_pos, self.race_state = 0, 0
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
        # multiplayer freeplay (firmware kart-0.2.5; docs/freeplay-multiplayer.md sections 3/4)
        self.multi = self.fw != FW_OLD
        self.session = 0
        self.score = 0
        self.atk_seq = 0
        self.atk = None                 # {"to", "item", "seq", "t0", "acked": [], "ack_at": {kart: mono}}
        self.ack = None                 # (from, seq, until_mono)
        self.ended = None
        self.game_next = 0.0
        self.game_had = False           # the last game line had something (session or a host heard)
        self.hit_done = False
        self.peer_host = bool(getattr(args, "peer_host", False))
        if self.peer_host:
            h = self.fleet[0]
            h.session = h.k                                  # fleet kart 3 hosts from the start ...
            self.fleet[1].plan = (h.k, self.t0 + 5.0)        # ... and kart 5 joins it after 5 s

    def position_at(self, now):
        """(race_pos, race_state) the beacon would give this kart at `now`."""
        if self.race_finished:
            state = 3
        elif self.me.race_go is not None:
            state = 1
        elif self.race_seen:
            state = 2
        else:
            state = 0
        if not self.pos_seq or (self.pos_wait and not self.race_seen):
            return 0, state
        if now < self.pos_t0:
            return self.pos_seq[0], state
        return self.pos_seq[min(len(self.pos_seq) - 1, int((now - self.pos_t0) / self.pos_step))], state

    def update_position(self, now):
        p, st = self.position_at(now)
        if (p, st) != (self.race_pos, self.race_state):
            self.race_pos, self.race_state = p, st
            self.emit({"t": "race", "kart": self.me.k, "event": "position", "position": p, "state": st})

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
            self.race_finished = True
            self.emit({"t": "race", "kart": k.k, "event": "finish", "position": 0 if arg == 0 else arg - 100})
            self.log(f"race: finish P{0 if arg == 0 else arg - 100}")
        elif cmd == 7:                                       # race: grid hold, GO arg seconds later
            if arg < 1 or arg > 30:
                res = 4
            else:
                k.race_go, k.ov = time.monotonic() + arg, None
                self.pos_t0, self.race_seen, self.race_finished = k.race_go, True, False
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
            k.slow_until, k.slow_prev = None, None
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
                secs, self.slow_secs = self.slow_secs, 0
                if secs and k.slow_mph and k.slow_until is None and k.slow_mph <= mph:
                    self.log(f"slow {secs} s: pit cap {k.slow_mph:.0f} mph already lower, kept")
                else:
                    if secs:
                        if k.slow_until is None:
                            k.slow_prev = k.slow_mph
                        k.slow_until = time.monotonic() + secs
                    else:
                        k.slow_until, k.slow_prev = None, None
                    k.slow_mph = float(mph)
                    erpm = mph * 1609.34 / 60 / (0.406 * math.pi) * 2.769 * 7
                    self.log(f"slow: cap {mph} mph = {erpm:.0f} ERPM" + (f" for {secs} s" if secs else ""))
            self.slow_secs = 0
        else:
            res = 4
        k.cmd_result = res
        self.emit({"t": "cmd", "kart": k.k, "src": src, "seq": seq, "cmd": cmd, "arg": arg, "result": res})

    def status(self):
        k = self.me
        lat, lon, _ = k.path.at(k.s)
        self.emit({"t": "status", "kart": k.k, "fw": self.fw, "built": "sim", "writes": True, "transport": "ble",
                   "radio": True, "radio_status": 0, "lora_tx": self.uptime() * 2, "lora_rx": self.uptime(),
                   "lora_rx_bad": 0, "gps_locked": True, "gps_cfg": "9600", "pps_count": self.uptime(),
                   "vesc_fw": "6.05", "vesc_hw": "SIM", "vesc_packets": self.uptime() * 10, "vesc_crc_err": 0,
                   "vesc_limits": not k.novesc, "rate": 2, "sync": "pps", "pit_karts_online": 3,
                   "gps_fix": not k.nofix, "gps_sats": 9, "gps_hdop": 0.9, "gps_age_ms": 200,
                   "gps_lat": lat if not k.nofix else 0.0, "gps_lon": lon if not k.nofix else 0.0, "heap": 180000,
                   **({"session": self.session, "hosting": self.hosting(), "score": self.score} if self.multi else {})})

    # -- multiplayer freeplay (firmware kart-0.2.5) ----------------------------------
    def hosting(self):
        return self.session != 0 and self.session == self.me.k

    def heard(self, f, now):
        return f.last_heard is not None and now - f.last_heard <= 10.0

    def peers(self, now):
        return sorted(f.k for f in self.fleet if self.session and f.session == self.session and self.heard(f, now))

    def hosts(self, now):
        return sorted(f.k for f in self.fleet if f.session == f.k and self.heard(f, now))

    def emit_game(self, now):
        a = self.atk
        self.game_had = bool(self.session or self.hosts(now))
        self.game_next = now + 1.0
        self.emit({"t": "game", "kart": self.me.k, "session": self.session, "hosting": self.hosting(),
                   "peers": self.peers(now), "hosts": self.hosts(now), "score": self.score,
                   "attack": {"to": a["to"], "item": a["item"], "seq": a["seq"], "age_ms": int((now - a["t0"]) * 1000)}
                   if a else None, "ended": self.ended})

    def set_session(self, sess, now, ended=None):
        if sess != self.session:
            self.score = 0                                   # reset on session change
        self.session, self.ended = sess, ended
        self.emit_game(now)

    def attack_line(self, to, item, seq, result, acked=None, why=None):
        self.emit({"t": "attack", "kart": self.me.k, "to": to, "item": item, "seq": seq, "result": result,
                   "acked": sorted(acked or []), "why": why})

    def session_cmd(self, rest, now):
        parts = rest.split()
        if parts[:1] == ["host"]:
            self.set_session(self.me.k, now)
            self.log(f"session: hosting (G{self.me.k})")
        elif parts[:1] == ["leave"]:
            self.set_session(0, now)
            self.log("session: left")
        elif parts[:1] == ["join"] and len(parts) == 2 and parts[1].isdigit():
            n = int(parts[1])
            f = next((x for x in self.fleet if x.k == n), None)
            if f is None or not self.heard(f, now) or f.session != n:
                self.log(f"session: join {n} rejected ({'not heard' if f is None or not self.heard(f, now) else 'not hosting'})")
            else:
                self.set_session(n, now)
                self.log(f"session: joined K{n}'s game")
        else:
            self.log("usage: session host | session join N | session leave")

    def attack_cmd(self, rest, now):
        parts = rest.split()
        if len(parts) != 2 or parts[1] not in ITEM_CODES or not (parts[0] == "all" or parts[0].isdigit()):
            self.log("usage: attack N|all shell|lightning")
            return
        to = ADDR_ALL if parts[0] == "all" else int(parts[0])
        item = parts[1]
        why = None
        f = next((x for x in self.fleet if x.k == to), None)
        if not self.session:
            why = "no session"
        elif to != ADDR_ALL and (f is None or not self.heard(f, now)):
            why = "not heard"
        elif to != ADDR_ALL and f.session != self.session:
            why = "not in your game"
        if why:
            self.attack_line(to, item, 0, "rejected", why=why)
            self.log(f"attack {parts[0]} {item}: rejected ({why})")
            return
        if self.atk:
            o = self.atk
            self.attack_line(o["to"], o["item"], o["seq"], "replaced", acked=o["acked"])
        self.atk_seq = self.atk_seq % 255 + 1
        targets = self.peers(now) if to == ADDR_ALL else [to]
        self.atk = {"to": to, "item": item, "seq": self.atk_seq, "t0": now, "acked": [],
                    "ack_at": {k: now + 1.0 for k in targets}}
        self.attack_line(to, item, self.atk_seq, "sent")
        self.log(f"attack {parts[0]} {item} seq {self.atk_seq}")
        self.emit_game(now)

    def multi_step(self, now, el):
        if not self.multi:
            return
        # scripted fleet: kart 5 joins our game 3 s after we host, else (--peer-host) kart 3's after 5 s
        j = self.fleet[1]
        want = self.me.k if self.hosting() else (self.fleet[0].k if self.peer_host else 0)
        if j.session != want and (j.plan is None or j.plan[0] != want):
            j.plan = (want, max(self.t0 + 5.0, now + 3.0) if want else now)
        if j.plan and now >= j.plan[1]:
            j.session, j.plan = j.plan[0], None
        # a joined kart leaves by itself when its host is lost / left
        if self.session and not self.hosting():
            h = next((x for x in self.fleet if x.k == self.session), None)
            if h is None or not self.heard(h, now):
                self.set_session(0, now, ended="host lost")
            elif h.session != h.k:
                self.set_session(0, now, ended="host left")
        # our attack in flight: acks ~1 s after launch, 5 s window
        a = self.atk
        if a:
            for k, at in a["ack_at"].items():
                f = next((x for x in self.fleet if x.k == k), None)
                if now >= at and k not in a["acked"] and f is not None and f.session == self.session:
                    a["acked"].append(k)
                    self.score = min(255, self.score + 1)
                    f.ack = (self.me.k, a["seq"], now + 5.0)
            if a["to"] != ADDR_ALL and a["acked"]:
                self.atk = None
                self.attack_line(a["to"], a["item"], a["seq"], "acked", acked=a["acked"])
                self.emit_game(now)
            elif now - a["t0"] >= 5.0:
                self.atk = None
                self.attack_line(a["to"], a["item"], a["seq"], "done" if a["to"] == ADDR_ALL else "timeout",
                                 acked=a["acked"])
                self.emit_game(now)
        # --hit-at S: a peer in our game (kart 3 first) shells us
        hit_at = getattr(self.args, "hit_at", None)
        if hit_at is not None and not self.hit_done and el >= hit_at and self.session:
            src = [f for f in self.fleet if f.session == self.session and self.heard(f, now)]
            src.sort(key=lambda f: f is not self.fleet[0])
            if src:
                self.hit(src[0], "shell", now)
        if now >= self.game_next and (self.session or self.hosts(now)):
            self.emit_game(now)
        elif self.game_had and not (self.session or self.hosts(now)):
            self.emit_game(now)                              # once when it all goes back to nothing

    def hit(self, f, item, now):
        self.hit_done = True
        seq = random.randint(1, 255)
        f.atk = (self.me.k, ITEM_CODES[item], seq, now + 1.0)
        self.ack = (f.k, seq, now + 5.0)
        k, secs = self.me, HIT_SECS[item]
        why = "stopped" if k.stopped else "grid hold" if k.race_go is not None else "no vesc" if k.novesc else None
        if why is None:                                      # the same timed SLOW as `slow 5 SECS`
            if k.slow_mph and k.slow_until is None and k.slow_mph <= 5:
                self.log(f"hit: pit cap {k.slow_mph:.0f} mph already lower, kept")
            else:
                if k.slow_until is None:
                    k.slow_prev = k.slow_mph
                k.slow_until, k.slow_mph = now + secs, 5.0
                self.log(f"slow: cap 5 mph for {secs} s (hit by K{f.k})")
        self.emit({"t": "hit", "kart": k.k, "from": f.k, "item": item, "seq": seq, "secs": secs,
                   "applied": why is None, "why": why})

    def multi_fields(self, owner, now):
        """The 7 telemetry fields for our self line (owner None) or a fleet kart's frames."""
        if owner is None:
            a = self.atk
            ack = self.ack if self.ack and now < self.ack[2] else (0, 0, 0)
            return {"session": self.session, "atk_to": a["to"] if a else 0,
                    "atk_item": ITEM_CODES[a["item"]] if a else 0, "atk_seq": a["seq"] if a else 0,
                    "ack_from": ack[0], "ack_seq": ack[1], "score": self.score}
        at = owner.atk if now < owner.atk[3] else (0, 0, 0, 0)
        ack = owner.ack if now < owner.ack[2] else (0, 0, 0)
        return {"session": owner.session, "atk_to": at[0], "atk_item": at[1], "atk_seq": at[2],
                "ack_from": ack[0], "ack_seq": ack[1], "score": 0}

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
        elif word == "slow":
            parts = rest.split()
            try:
                mph = int(parts[0]) if parts else 0
                secs = int(parts[1]) if len(parts) > 1 else 0
            except ValueError:
                mph, secs = 0, 0
            if self.fw == FW_OLD:
                secs = 0                                     # kart-0.2.3: atoi("5 4") = 5, untimed
            if secs < 0 or secs > 60:
                self.log("usage: slow [MPH] [SECS 1-60]")
            else:
                self.slow_secs = secs
                self.command(254, 5, mph)
        elif word in ("stop", "start", "ping"):
            self.command(254, {"stop": 2, "start": 3, "ping": 4, "slow": 5}[word], arg)
        elif word == "session" and self.multi:
            self.session_cmd(rest, time.monotonic())
        elif word == "attack" and self.multi:
            self.attack_cmd(rest, time.monotonic())
        elif word == "help":
            self.log("commands: status | mode 0..2 | stop | start | slow [MPH] [SECS] | boost SECS SCALE | race [secs] | ping"
                     + (" | session host|join N|leave | attack N|all shell|lightning" if self.multi else "") + " (SIMULATION)")
        else:
            self.log(f"unknown command: {line}")

    # -- main loop ---------------------------------------------------------------
    def expire_slow(self, now):
        """Timed slow over (firmware 0.2.4): back to the pit cap it replaced, or none."""
        k = self.me
        if k.slow_until is not None and now >= k.slow_until:
            k.slow_until = None
            k.slow_mph, k.slow_prev = k.slow_prev, None
            self.log(f"timed slow over, pit cap {k.slow_mph:.0f} mph back" if k.slow_mph else "timed slow over")

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
                self.update_position(now)
                if self.me.ov and now >= self.me.ov[1]:
                    self.me.ov = None
                    self.log(f"boost over, back to {self.me.eff_scale():.2f}")
                    self.log(f"vesc write scale {self.me.eff_scale():.3f}")
                self.expire_slow(now)
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
                             "base_lon": round(blon, 7) if up else None, "base_fix_age_s": 1 if up else None,
                             "race_pos": self.race_pos, "race_state": self.race_state,
                             "slow_mph": int(round(self.me.slow_mph or 0)),
                             **(self.multi_fields(None, now) if self.multi else {})}
                    self.emit(self.me.tele("self", rx_ms=int(el * 1000), uptime=self.uptime(),
                                           base_rssi=-71 + random.randint(-6, 6) if up else 0, extra=extra))
                for i, f in enumerate(self.fleet):
                    if now >= next_fleet[i]:
                        next_fleet[i] += 1.0
                        if i == 1 and el % 40.0 > 30.0:      # second kart drops out ~10 s in every 40 s
                            continue
                        f.last_heard = now
                        self.emit(f.tele("fleet", rssi=-80 - 6 * i + random.randint(-4, 4),
                                         snr=round(8.5 - 2 * i + random.uniform(-1, 1), 1),
                                         rx_ms=int(el * 1000), base_rssi=-70, uptime=self.uptime(),
                                         extra=self.multi_fields(f, now) if self.multi else None))
                self.multi_step(now, el)
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
    ap.add_argument("--fw-old", action="store_true", help="act like kart-0.2.3: no timed `slow MPH SECS` (reads as untimed)")
    ap.add_argument("--course", action="store_true", help="drive the game course (kart/sim/zones.course.json)")
    ap.add_argument("--warp", type=float, default=1.0, help="own kart moves this many times faster (tests)")
    ap.add_argument("--nogpsline", action="store_true", help="no 5 Hz gps lines (firmware `gpsline off`)")
    ap.add_argument("--hdop", type=float, default=0.9, help="reported HDOP (> 4 = poor: the game ignores it)")
    ap.add_argument("--race-at", type=float, help="the pit sends `race 5` at this second")
    ap.add_argument("--race-pos", help='running positions from the pit beacon, e.g. "3,2,2,1"')
    ap.add_argument("--race-pos-step", type=float, default=10.0, help="seconds per --race-pos entry")
    ap.add_argument("--peer-host", action="store_true",
                    help="multiplayer: fleet kart 3 hosts a freeplay game, kart 5 joins it after 5 s")
    ap.add_argument("--hit-at", type=float, help="multiplayer: a peer in our session shells us at this second")
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
