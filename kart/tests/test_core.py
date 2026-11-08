"""Unit tests for the kart display logic (no hardware, no network).

Run:  python3 -I kart/tests/test_core.py
"""
import json
import os
import sys
import unittest

sys.dont_write_bytecode = True      # never drop .pyc files into pit/ when comparing with pitcore
KART = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(KART, "server"))

import kartcore as kc  # noqa: E402

# Real lines from the kart-2 Heltec console capture of 2026-11-05 (verbatim).
REAL_CMD = '{"t":"cmd","kart":2,"src":0,"seq":198,"cmd":4,"arg":0,"result":2}'
REAL_STOP = '{"t":"cmd","kart":2,"src":0,"seq":202,"cmd":2,"arg":0,"result":1}'
REAL_START = '{"t":"cmd","kart":2,"src":0,"seq":203,"cmd":3,"arg":0,"result":1}'
REAL_LOG = '{"t":"log","kart":2,"msg":"vesc write scale 0.500"}'
REAL_BLE = '{"t":"ble","kart":2,"pinned":"","connected":false,"candidates":[]}'
# A fleet line from the same capture. The capture tool cut lines at 200 chars, so the
# tail is completed with the firmware's field order (gkTelemetryToJson) and the
# values seen in the capture's other fragments.
REAL_FLEET = ('{"t":"fleet","kart":1,"seq":23,"lat":null,"lon":null,"fix":false,"gps_age_s":255,"sats":0,'
              '"gps_kph":0.0,"heading":0.0,"volts":78.4,"motor_a":0.0,"batt_a":0.0,"erpm":0,"duty":0.00,'
              '"esc_c":24.5,"fault":0,"mode_req":null,"mode_actual":null,"scale":null,"stopped":false,'
              '"vesc_link":true,"mode_confirmed":false,"pps":false,"wifi":false,"writes":true,"last_cmd_seq":0,'
              '"cmd_result":0,"ah":0.00,"uptime_s":10,"base_rssi":0,"rssi":-41,"snr":9.8,"rx_ms":10571}')


def self_line(**over):
    d = json.loads(REAL_FLEET)
    d.update(t="self", kart=2, rssi=0, snr=0.0, base_rssi=-72)
    d.update(over)
    return d


class Derivations(unittest.TestCase):
    def test_battery_same_as_pit(self):
        self.assertEqual(kc.battery_percent(60.0), 0.0)
        self.assertEqual(kc.battery_percent(84.0), 100.0)
        self.assertEqual(kc.battery_percent(72.0), 50.0)
        self.assertEqual(kc.battery_percent(78.4), 76.7)
        self.assertEqual(kc.battery_percent(90.0), 100.0)
        self.assertEqual(kc.battery_percent(50.0), 0.0)
        self.assertIsNone(kc.battery_percent(None))

    def test_matches_pitcore(self):
        sys.path.insert(0, os.path.join(os.path.dirname(KART), "pit", "server"))
        try:
            import pitcore
        except ImportError:
            self.skipTest("pit/ not present")
        for v in (60.0, 66.6, 78.4, 84.0):
            self.assertEqual(kc.battery_percent(v), pitcore.battery_percent(v))
        for e in (0, 1234, -500, 20000):
            self.assertEqual(kc.wheel_mph(e), pitcore.wheel_mph(e))
        self.assertEqual(kc.kph_to_mph(32.2), pitcore.kph_to_mph(32.2))

    def test_mph(self):
        self.assertAlmostEqual(kc.kph_to_mph(100.0), 62.14, places=2)
        self.assertIsNone(kc.kph_to_mph(None))
        self.assertAlmostEqual(kc.wheel_mph(kc.erpm_for_mph(15.0)), 15.0, places=1)
        self.assertIsNone(kc.wheel_mph(None))

    def test_link_status(self):
        self.assertEqual(kc.link_status(None, 100), "never")
        self.assertEqual(kc.link_status(96, 100), "online")
        self.assertEqual(kc.link_status(90, 100), "stale")
        self.assertEqual(kc.link_status(80, 100), "offline")


class Parsing(unittest.TestCase):
    def setUp(self):
        self.d = kc.KartDisplay(configured_id=2)

    def feed(self, obj, now, wall="w"):
        if isinstance(obj, str):
            obj = json.loads(obj)
        return self.d.ingest(obj, now, wall)

    def test_real_lines(self):
        for line in (REAL_CMD, REAL_LOG, REAL_BLE, REAL_FLEET, REAL_STOP):
            self.feed(line, 1.0)
        s = self.d.snapshot(1.5)
        self.assertEqual([k["id"] for k in s["fleet"]], [1])
        k1 = s["fleet"][0]
        self.assertEqual(k1["link"], "online")
        self.assertEqual(k1["status"]["rssi"], -41)
        self.assertEqual(k1["derived"]["battery_pct"], 76.7)
        self.assertIsNone(k1["gps"]["last_fix"])         # fix false: no position, never 0,0
        self.assertIsNone(k1["derived"]["gps_mph"])      # gps_kph 0.0 without a fix is not "0 mph"
        self.assertEqual(s["heltec"]["ble"]["connected"], False)
        self.assertEqual(s["cmds_seen"][-1]["cmd_name"], "stop")
        self.assertEqual(s["cmds_seen"][0]["result_name"], "applied")
        self.assertIn("vesc write scale 0.500", [x["msg"] for x in s["heltec"]["logs"]])

    def test_self_and_staleness(self):
        self.feed(self_line(fix=True, lat=41.094, lon=-73.4167, gps_kph=20.0, heading=90.0, gps_age_s=0), 10.0)
        s = self.d.snapshot(10.5)
        me = s["self"]
        self.assertEqual(me["link"], "online")
        self.assertEqual(me["heltec_id"], 2)
        self.assertFalse(me["id_mismatch"])
        self.assertAlmostEqual(me["derived"]["gps_mph"], 12.43, places=2)
        self.assertEqual(s["pit_link"], {"state": "ok", "rssi": -72, "age_s": 0.0})
        # 3 s without a self line: stale for the driver; pit link unknown, not "lost"
        s = self.d.snapshot(13.6)
        self.assertEqual(s["self"]["link"], "stale")
        self.assertEqual(s["pit_link"]["state"], "unknown")
        self.assertIsNone(s["self"]["derived"]["gps_mph"])   # fix older than 5 s -> no speed
        s = self.d.snapshot(10.0 + 16)
        self.assertEqual(s["self"]["link"], "offline")

    def test_no_fix_keeps_last_position_with_age(self):
        self.feed(self_line(fix=True, lat=41.094, lon=-73.4167, gps_kph=10.0, gps_age_s=0), 1.0)
        self.feed(self_line(fix=False, lat=None, lon=None, gps_kph=0.0, gps_age_s=4), 2.0)
        me = self.d.snapshot(2.0)["self"]
        self.assertEqual(me["gps"]["last_fix"]["lat"], 41.094)
        self.assertFalse(me["gps"]["fix_now"])
        self.assertEqual(me["gps"]["fix_age_s"], 1.0)

    def test_vesc_group_only_from_vesc_link_samples(self):
        self.feed(self_line(volts=80.0, vesc_link=True), 1.0)
        self.feed(self_line(volts=0.0, vesc_link=False), 4.0)    # firmware prints 0.0 when the link is down
        me = self.d.snapshot(4.0)["self"]
        self.assertEqual(me["vesc"]["volts"], 80.0)
        self.assertEqual(me["vesc_age_s"], 3.0)
        self.assertTrue(me["vesc_fresh"])
        self.assertFalse(self.d.snapshot(7.0)["self"]["vesc_fresh"])

    def test_pit_link_lost(self):
        self.feed(self_line(base_rssi=0), 1.0)
        self.assertEqual(self.d.snapshot(1.0)["pit_link"]["state"], "lost")

    def test_id_mismatch_flagged(self):
        self.feed(self_line(kart=3), 1.0)
        me = self.d.snapshot(1.0)["self"]
        self.assertTrue(me["id_mismatch"])
        self.assertEqual(me["id"], 3)

    def test_fleet_staleness_and_bad_id(self):
        self.feed(dict(json.loads(REAL_FLEET), kart=5), 1.0)
        self.feed(dict(json.loads(REAL_FLEET), kart=9), 1.0)
        self.assertEqual(self.d.rx_bad, 1)
        self.assertEqual(self.d.snapshot(7.0)["fleet"][0]["link"], "stale")
        self.assertEqual(self.d.snapshot(17.0)["fleet"][0]["link"], "offline")

    def test_fault_name(self):
        self.feed(self_line(fault=5, vesc_link=True), 1.0)
        self.assertEqual(self.d.snapshot(1.0)["self"]["fault_name"], "OVER_TEMP_FET")

    def test_simulation_marker(self):
        self.feed({"t": "log", "kart": 2, "msg": "SIMULATION"}, 1.0)
        self.assertTrue(self.d.snapshot(1.0)["simulation"])


class Slow(unittest.TestCase):
    def test_unknown_until_evidence(self):
        d = kc.KartDisplay(2)
        d.ingest(self_line(uptime_s=5000), 1.0, "w")
        self.assertIsNone(d.snapshot(1.0)["slow"]["active"])

    def test_fresh_boot_means_not_slow(self):
        d = kc.KartDisplay(2)
        d.ingest(self_line(uptime_s=12), 1.0, "w")
        self.assertIs(d.snapshot(1.0)["slow"]["active"], False)

    def test_slow_then_start(self):
        d = kc.KartDisplay(2)
        d.ingest({"t": "cmd", "kart": 2, "src": 0, "seq": 9, "cmd": 5, "arg": 0, "result": 1}, 1.0, "w")
        s = d.snapshot(1.0)["slow"]
        self.assertTrue(s["active"])
        self.assertEqual(s["mph"], 5)                    # arg 0 = firmware default 5 mph
        d.ingest(json.loads(REAL_START), 2.0, "w")
        self.assertIs(d.snapshot(2.0)["slow"]["active"], False)

    def test_rejected_slow_is_not_slow(self):
        d = kc.KartDisplay(2)
        d.ingest({"t": "cmd", "kart": 2, "src": 0, "seq": 9, "cmd": 5, "arg": 8, "result": 4}, 1.0, "w")
        self.assertIsNone(d.snapshot(1.0)["slow"]["active"])

    def test_reboot_clears_slow(self):
        d = kc.KartDisplay(2)
        d.ingest({"t": "cmd", "kart": 2, "src": 0, "seq": 9, "cmd": 5, "arg": 8, "result": 2}, 1.0, "w")
        d.ingest(self_line(uptime_s=4000), 1.5, "w")
        self.assertTrue(d.snapshot(1.5)["slow"]["active"])
        d.ingest(self_line(uptime_s=3), 2.0, "w")
        self.assertIs(d.snapshot(2.0)["slow"]["active"], False)
        self.assertEqual(d.heltec_reboots, 1)


class Commands(unittest.TestCase):
    def test_text(self):
        self.assertEqual(kc.command_text("mode", 0), "mode 0")
        self.assertEqual(kc.command_text("mode", 2), "mode 2")
        self.assertEqual(kc.command_text("slow"), "slow")
        self.assertEqual(kc.command_text("slow", 8), "slow 8")
        for c in ("stop", "start", "ping", "status"):
            self.assertEqual(kc.command_text(c), c)
        for bad in (("mode", 3), ("mode", None), ("mode", "1"), ("slow", 99), ("slow", True),
                    ("reboot", None), ("wifi", None), ("id", 3)):
            with self.assertRaises(kc.CommandError, msg=bad):
                kc.command_text(*bad)

    def test_tracker_echo_and_refine(self):
        t = kc.CommandTracker()
        r = t.sent("mode", 2, "mode 2", True, 1.0, "w", "kiosk")
        # a pit command echo must not be taken as ours
        self.assertIsNone(t.on_cmd_line({"src": 0, "seq": 50, "cmd": 1, "result": 1}, "w"))
        t.on_cmd_line({"src": 254, "seq": 7, "cmd": 1, "arg": 2, "result": 1}, "w")
        self.assertEqual((r["state"], r["seq"], r["result_name"]), ("echoed", 7, "accepted"))
        t.on_self({"last_cmd_seq": 7, "cmd_result": 2})
        self.assertEqual(r["result_name"], "applied")

    def test_tracker_timeout_and_link(self):
        t = kc.CommandTracker()
        r1 = t.sent("ping", None, "ping", True, 1.0, "w", "kiosk")
        t.tick(1.0 + kc.CommandTracker.ECHO_TIMEOUT_S + 0.1)
        self.assertEqual(r1["state"], "no_echo")
        r2 = t.sent("stop", None, "stop", True, 10.0, "w", "kiosk")
        t.on_link_down()
        self.assertEqual(r2["state"], "link_lost")
        snap = t.snapshot(11.0)
        self.assertEqual(snap[0]["id"], r2["id"])
        self.assertNotIn("sent_mono", snap[0])


# --- game layer (pit/server/map/ZONES.md) ---------------------------------------
import math  # noqa: E402

O = (41.09399, -73.41637)


def ll(e, n):
    """lat/lon of a point e metres east, n metres north of O."""
    return [O[0] + math.degrees(n / kc.R_EARTH), O[1] + math.degrees(e / (kc.R_EARTH * math.cos(math.radians(O[0]))))]


def zones_doc(*zones, enabled=True, version=1):
    return {"version": version, "enabled": enabled, "zones": list(zones)}


def boost_zone(e=0.0, n=0.0, r=10, **kw):
    lat, lon = ll(e, n)
    return {"id": "b1", "type": "boost", "name": "Boost", "shape": "circle", "lat": lat, "lon": lon, "r_m": r,
            "scale": 1.0, "secs": 5, **kw}


def mud_zone(x0=-10, x1=10, y0=-10, y1=10, **kw):
    return {"id": "m1", "type": "mud", "name": "Mud", "shape": "polygon",
            "points": [ll(x0, y0), ll(x1, y0), ll(x1, y1), ll(x0, y1)], "scale": 0.5, **kw}


def lap_zone(**kw):
    return {"id": "lap", "type": "lapline", "name": "S/F", "shape": "line", "a": ll(0, -8), "b": ll(0, 8),
            "min_lap_s": 20, **kw}


def fix(e, n, stopped=False, **kw):
    lat, lon = ll(e, n)
    # 54 km/h (15 m/s): fast enough that the test tracks' long hops are not GPS outliers
    return {"t": "self", "kart": 2, "fix": True, "lat": lat, "lon": lon, "stopped": stopped, "gps_kph": 54.0,
            "heading": 90.0, "vesc_link": True, "mode_req": 1, "scale": 0.75, **kw}


def lines(recs):
    return [r["line"] for r in recs if r["ev"] == "cmd"]


def evs(recs, ev):
    return [r for r in recs if r["ev"] == ev]


class Geometry(unittest.TestCase):
    def test_point_in_polygon(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertTrue(kc.point_in_polygon((5, 5), sq))
        self.assertFalse(kc.point_in_polygon((15, 5), sq))
        self.assertFalse(kc.point_in_polygon((-0.1, 5), sq))
        concave = [(0, 0), (10, 0), (10, 10), (5, 3), (0, 10)]          # a notch from the top
        self.assertFalse(kc.point_in_polygon((5, 8), concave))
        self.assertTrue(kc.point_in_polygon((2, 3), concave))

    def test_circle_and_polygon_distance(self):
        z = kc.Zone(boost_zone(r=10))
        self.assertLess(z.outside_m(*ll(0, 9)), 0)
        self.assertAlmostEqual(z.outside_m(*ll(0, 14)), 4.0, places=1)
        self.assertAlmostEqual(z.outside_m(*ll(13, 0)), 3.0, places=1)
        m = kc.Zone(mud_zone())
        self.assertEqual(m.outside_m(*ll(0, 0)), 0.0)
        self.assertAlmostEqual(m.outside_m(*ll(12, 0)), 2.0, places=1)
        self.assertAlmostEqual(m.outside_m(*ll(13, 14)), 5.0, places=1)    # corner: 3-4-5

    def test_segment_crossing(self):
        self.assertAlmostEqual(kc.segment_crossing((-1, 0), (1, 0), (0, -5), (0, 5)), 0.5)
        self.assertAlmostEqual(kc.segment_crossing((1, 0), (-3, 0), (0, -5), (0, 5)), 0.25)
        self.assertIsNone(kc.segment_crossing((-1, 6), (1, 6), (0, -5), (0, 5)))     # past the end
        self.assertIsNone(kc.segment_crossing((1, 0), (2, 0), (0, -5), (0, 5)))      # short of it
        self.assertIsNone(kc.segment_crossing((0, -9), (0, 9), (0, -5), (0, 5)))     # collinear

    def test_boost_line_never_above_one(self):
        self.assertEqual(kc.boost_line(5, 1.0), "boost 5 1.00")
        self.assertEqual(kc.boost_line(5, 1.7), "boost 5 1.00")
        self.assertEqual(kc.boost_line(99, 0.01), "boost 30 0.10")
        self.assertEqual(kc.boost_line(0, 0.5), "boost 1 0.50")

    def test_parse_ignores_bad_and_unknown(self):
        doc = zones_doc(boost_zone(r=2), {"id": "x", "type": "oil", "shape": "circle"},
                        {"id": "p", "type": "mud", "shape": "polygon", "points": [ll(0, 0), ll(1, 1)]},
                        boost_zone(scale=3.0, r=8) | {"id": "b2"}, lap_zone(), lap_zone() | {"id": "lap2"})
        en, areas, lap, ign = kc.parse_zones(doc)
        self.assertTrue(en)
        self.assertEqual([z.id for z in areas], ["b2"])
        self.assertEqual(areas[0].scale, 1.0)                      # clamped
        self.assertEqual(lap.id, "lap")
        self.assertEqual(sorted(i["id"] for i in ign), ["b1", "lap2", "p", "x"])
        self.assertEqual(kc.parse_zones(None)[:3], (False, [], None))


class Game(unittest.TestCase):
    def engine(self, *zones, enabled=True):
        g = kc.GameEngine()
        recs = g.set_zones(zones_doc(*zones, enabled=enabled), 0.0)
        return g, recs

    @staticmethod
    def drive(_self, g, pts, t0=1.0, dt=0.5, **kw):
        """Feed fixes along pts (e, n) every dt s, ticking 4x per fix. Returns (records, last t)."""
        out, t = [], t0
        for e, n in pts:
            out += g.on_self(fix(e, n, **kw), t)
            for i in range(1, 4):
                out += g.tick(t + i * dt / 4)
            t += dt
        return out, t

    def test_boost_once_on_entry_with_hysteresis_and_cooldown(self):
        g, recs = self.engine(boost_zone(r=10, cooldown_s=20))
        self.assertEqual(evs(recs, "game_on")[0]["version"], 1)
        r = g.on_self(fix(-20, 0), 1.0)
        self.assertEqual(lines(r), [])
        r = g.on_self(fix(-9, 0), 1.5)
        self.assertEqual(lines(r), ["boost 5 1.00"])
        self.assertEqual(evs(r, "zone_enter")[0]["zone"], "b1")
        self.assertEqual(len(evs(r, "boost")), 1)
        # still inside, and just outside the edge but within 3 m: no exit, nothing re-sent
        for t, e in ((2.0, 0), (2.5, 9.5), (3.0, 12.5), (3.5, 12.9)):
            r = g.on_self(fix(e, 0), t)
            self.assertEqual(lines(r) + [x["ev"] for x in evs(r, "zone_exit")], [], e)
        self.assertEqual(g.snapshot(3.5)["inside"], ["b1"])
        # back inside after flapping on the edge: still the same visit, no re-trigger
        self.assertEqual(lines(g.on_self(fix(9, 0), 4.0)), [])
        r = g.on_self(fix(13.5, 0), 4.5)                   # > 3 m outside: exit, cooldown starts
        self.assertEqual(len(evs(r, "zone_exit")), 1)
        r = g.on_self(fix(5, 0), 10.0)                     # re-entry 5.5 s after exit: cooldown
        self.assertEqual(lines(r), [])
        self.assertIn("cooldown", evs(r, "suppressed")[0]["why"])
        g.on_self(fix(30, 0), 11.0)
        r = g.on_self(fix(5, 0), 25.0)                     # 20.5 s after the exit that followed the boost
        self.assertEqual(lines(r), ["boost 5 1.00"])

    def test_cooldown_default_20(self):
        z = boost_zone()
        z.pop("cooldown_s", None)
        g, _ = self.engine(z)
        self.assertEqual(lines(g.on_self(fix(0, 0), 1.0)), ["boost 5 1.00"])
        g.on_self(fix(20, 0), 2.0)
        self.assertEqual(lines(g.on_self(fix(0, 0), 21.0)), [])
        g.on_self(fix(20, 0), 22.0)
        self.assertEqual(lines(g.on_self(fix(0, 0), 22.5)), ["boost 5 1.00"])   # 20.5 s after the 2.0 exit

    def test_mud_repeat_cadence(self):
        g, _ = self.engine(mud_zone())
        sent = []                                          # (t, line)
        t = 0.0
        e = -14.0
        while t < 12.0:
            if abs(t * 2 - round(t * 2)) < 1e-9:           # a fix every 0.5 s, 2 m/s eastbound
                for r in g.on_self(fix(e, 0), t):
                    if r["ev"] == "cmd":
                        sent.append((round(t, 2), r["line"]))
                e += 1.0
            for r in g.tick(t):
                if r["ev"] == "cmd":
                    sent.append((round(t, 2), r["line"]))
            t = round(t + 0.25, 2)
        times = [x[0] for x in sent]
        self.assertTrue(all(x[1] == "boost 3 0.50" for x in sent), sent)
        self.assertEqual(times[0], 2.0)                    # entry at e = -10 (t = 2.0)
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertTrue(all(abs(gp - 2.0) < 0.26 for gp in gaps), gaps)
        # exits at e > 13 (3 m hysteresis) = t 13.5 > 12: inside all along -> 2 s cadence until the end
        self.assertGreaterEqual(len(sent), 5)
        g2, _ = self.engine(mud_zone())
        g2.on_self(fix(0, 0), 1.0)
        g2.on_self(fix(14, 0), 1.5)                        # out (> 3 m)
        self.assertEqual(lines(g2.tick(4.0) + g2.tick(6.0)), [])   # no repeats after leaving

    def test_mud_wins_over_boost(self):
        g, _ = self.engine(mud_zone(), boost_zone(e=8, r=8))
        r = g.on_self(fix(0, 0), 1.0)                      # inside both at once
        self.assertEqual(lines(r), ["boost 3 0.50"])
        self.assertEqual(evs(r, "suppressed")[0]["why"], "inside mud")

    def test_lap_timing_both_directions_and_min_lap(self):
        g, _ = self.engine(lap_zone(min_lap_s=20))
        r = g.on_self(fix(-2, 0), 0.0) + g.on_self(fix(2, 0), 1.0)       # crossing at t = 0.5
        self.assertEqual(len(evs(r, "lap_start")), 1)
        lap = g.snapshot(1.0)["lap"]
        self.assertEqual((lap["count"], lap["running"], lap["current_s"]), (0, True, 0.5))
        g.on_self(fix(12, 0), 5.0)                                       # away from the line: re-armed
        r = g.on_self(fix(2, 0), 9.5) + g.on_self(fix(-2, 0), 10.0)      # back across at 9.75: too soon
        self.assertEqual(evs(r, "lap_ignored")[0]["since_s"], 9.25)
        self.assertEqual(g.snapshot(10.0)["lap"]["count"], 0)
        g.on_self(fix(-12, 1), 20.0)
        r = g.on_self(fix(-2, 1), 29.5) + g.on_self(fix(2, 1), 30.0)     # eastbound, t = 29.75
        self.assertEqual(evs(r, "lap")[0]["lap_s"], 29.25)
        g.on_self(fix(12, 1), 40.0)
        r = g.on_self(fix(2, 1), 54.5) + g.on_self(fix(-2, 1), 55.0)     # westbound counts too, t = 54.75
        lp = evs(r, "lap")
        self.assertEqual(lp[0]["lap_s"], 25.0)
        self.assertTrue(lp[0]["best"])
        s = g.snapshot(56.0)["lap"]
        self.assertEqual((s["count"], s["last_s"], s["best_s"], s["times"]), (2, 25.0, 25.0, [29.25, 25.0]))
        self.assertEqual(s["current_s"], 1.2)
        r = g.on_self(fix(-2, 1), 61.0)                    # crossed, but > 5 s since the last fix: not joined
        self.assertEqual(evs(r, "lap") + evs(r, "lap_ignored"), [])
        self.assertEqual(lines(r), [])                     # a lap line never sends anything

    def test_lap_jitter_on_the_line_never_counts(self):
        """A kart parked on the line (GPS jitter across it for minutes) never completes a lap; the
        last crossing before it drives off is the lap start; a fix exactly on the line counts once."""
        g, _ = self.engine(lap_zone(min_lap_s=20))
        t, r = 0.0, []
        for i in range(120):                                             # 60 s of +-1.5 m jitter
            r += g.on_self(fix(1.5 if i % 2 else -1.5, 0), t)
            t += 0.5
        self.assertEqual(g.snapshot(t)["lap"]["count"], 0)
        self.assertEqual(len(evs(r, "lap")), 0)
        start = g.lap["start"]
        self.assertGreater(start, 50.0)                                  # the latest crossing, not the first
        g.on_self(fix(20, 0), t + 2)                                     # drives off
        g.on_self(fix(-20, 0), t + 25)
        r = g.on_self(fix(-0.0, 0), t + 26) + g.on_self(fix(10, 0), t + 27)   # fix exactly on the line
        self.assertEqual(len(evs(r, "lap")), 1)
        self.assertEqual(len(evs(r, "lap_ignored")), 0)

    def test_fast_kart_cannot_skip_a_small_pad(self):
        """2 Hz self-line fallback at 54 km/h: 7.5 m between fixes jumps over a 3 m pad."""
        g, _ = self.engine(boost_zone(e=0, r=3))
        r = g.on_self(fix(-4, 0), 0.0) + g.on_self(fix(3.5, 0, gps_kph=54.0), 0.5)
        self.assertEqual(lines(r), ["boost 5 1.00"])
        self.assertTrue(evs(r, "zone_enter")[0]["swept"])

    def test_item_overlapping_boost_sends_one_command(self):
        g, _ = self.engine(boost_zone(e=0, r=6), {"id": "i1", "type": "item", "name": "Item", "shape": "circle",
                                                  "lat": ll(0, 0)[0], "lon": ll(0, 0)[1], "r_m": 6})
        g.rng = __import__("random").Random(1)
        r = g.on_self(fix(-10, 0), 0.0) + g.on_self(fix(0, 0), 0.5)
        self.assertEqual(len(lines(r)), 1)
        self.assertEqual(len(evs(r, "item")), 1)
        self.assertEqual(evs(r, "suppressed")[0]["why"].split()[0], "overlaps")

    def test_go_line_without_countdown_starts_the_race(self):
        g = kc.GameEngine()
        g.on_race({"t": "race", "event": "finish", "position": 2}, 1.0)
        r = g.on_race({"t": "race", "event": "go"}, 50.0)                 # restarted display: no countdown seen
        self.assertEqual([x["ev"] for x in r], ["race_go", "race_go_line"])
        rs = g.snapshot(50.0)["race"]
        self.assertEqual((rs["finished"], rs["go_age_s"]), (False, 0.0))
        self.assertEqual([x["ev"] for x in g.on_race({"t": "race", "event": "go"}, 51.0)], ["race_go_line"])

    def test_timing_survives_a_restart_same_lap_line(self):
        g, _ = self.engine(lap_zone(min_lap_s=2))
        g.on_self(fix(-10, 0), 0.0); g.on_self(fix(10, 0), 1.0)          # lap start at 0.5
        g.on_self(fix(-10, 0), 5.0)                                       # lap 1 crossing at 3.0 (westbound)
        doc = json.loads(json.dumps(g.export_state(6.0)))
        g2, _ = self.engine(lap_zone(min_lap_s=2))
        r = g2.import_state(doc, 8.0)
        self.assertEqual([x["ev"] for x in r], ["zones_lap_restored"])
        lap = g2.snapshot(8.0)["lap"]
        self.assertEqual((lap["count"], lap["running"], lap["last_s"], lap["current_s"]), (1, True, 2.5, 5.0))
        g3, _ = self.engine(lap_zone(min_lap_s=7))                       # different lap line: fresh
        self.assertEqual(g3.import_state(doc, 8.0), [])
        g4, _ = self.engine(lap_zone(min_lap_s=2))
        self.assertEqual(g4.import_state(doc, 6.0 + kc.GAME_STATE_MAX_AGE_S + 1), [])    # too old
        self.assertEqual(g4.import_state(dict(doc, boot_id="other"), 8.0), [])         # rebooted

    def test_lap_line_misses_past_its_end(self):
        g, _ = self.engine(lap_zone())
        r = g.on_self(fix(-2, 10), 0.0) + g.on_self(fix(2, 10), 0.5)
        self.assertEqual(evs(r, "lap_start"), [])

    def test_stopped_kart_sends_nothing(self):
        g, _ = self.engine(boost_zone(), mud_zone(x0=40, x1=60))
        r, t = self.drive(None, g, [(-20, 0), (0, 0), (5, 0), (30, 0), (45, 0), (50, 0), (55, 0)], stopped=True)
        self.assertEqual(lines(r), [])
        self.assertEqual(evs(r, "suppressed")[0]["why"], "kart STOPPED")
        # START while still inside the mud: the (safe) slowdown resumes; the boost was used up
        r = g.on_self(fix(56, 0, stopped=False), t) + g.tick(t + 0.25)
        self.assertEqual(lines(r), ["boost 3 0.50"])

    def test_disabled_sends_nothing(self):
        g, recs = self.engine(boost_zone(), mud_zone(x0=40, x1=60), lap_zone(), enabled=False)
        self.assertEqual(evs(recs, "game_on"), [])
        r, _ = self.drive(None, g, [(-20, 0), (0, 0), (5, 0), (30, 0), (45, 0), (50, 0)])
        self.assertEqual(r, [])
        s = g.snapshot(5.0)
        self.assertFalse(s["on"])
        self.assertEqual(s["off_reason"], "off at the pit")
        self.assertEqual(s["inside"], [])

    def test_no_file_and_local_off(self):
        g = kc.GameEngine()
        self.assertEqual(g.set_zones(None, 0.0), [])
        self.assertEqual(g.on_self(fix(0, 0), 1.0), [])
        self.assertEqual(g.snapshot(1.0)["off_reason"], "no zones from the pit")
        g = kc.GameEngine(local_enabled=False)
        g.set_zones(zones_doc(boost_zone()), 0.0)
        self.assertEqual(g.on_self(fix(0, 0), 1.0), [])
        self.assertEqual(g.snapshot(1.0)["off_reason"], "off in this kart's config")

    def test_switching_off_exits_and_resets(self):
        g, _ = self.engine(boost_zone(), lap_zone())
        g.on_self(fix(-1, 0), 1.0)
        g.on_self(fix(1, 0), 1.5)
        self.assertEqual(g.snapshot(2.0)["inside"], ["b1"])
        r = g.set_zones(zones_doc(boost_zone(), lap_zone(), enabled=False, version=2), 3.0)
        self.assertEqual([x["ev"] for x in r], ["zone_exit", "lap_reset", "game_off"])
        s = g.snapshot(3.0)
        self.assertEqual((s["inside"], s["lap"]["running"], s["on"]), ([], False, False))

    def test_no_fix_exits_after_5_s(self):
        g, _ = self.engine(mud_zone(), lap_zone())
        g.on_self(fix(-5, 0), 1.0)
        nofix = {"t": "self", "fix": False, "lat": None, "lon": None, "stopped": False, "vesc_link": True}
        r = g.on_self(nofix, 2.0) + g.tick(4.0)
        self.assertEqual(evs(r, "zone_exit"), [])
        self.assertEqual(len(lines(r)), 1)                 # the 2 s mud repeat still runs inside the 5 s
        r = g.tick(6.1)
        self.assertEqual(evs(r, "zone_exit")[0]["why"], "no GPS fix for 5 s")
        self.assertEqual(lines(g.tick(8.5)), [])
        # the next fix after the gap is not joined to the old one: no lap crossing across the gap
        r = g.on_self(fix(5, 30), 9.0) + g.on_self(fix(-5, 30), 9.5)
        self.assertEqual(evs(r, "lap_start"), [])

    def test_meter(self):
        g, _ = self.engine(boost_zone())
        g.on_self(fix(-15, 0, scale=0.75, mode_req=1), 1.0)
        self.assertEqual(g.meter(1.0)["kind"], "normal")
        g.on_self(fix(-15, 0, scale=1.0, mode_req=1), 1.5)
        self.assertEqual(g.meter(1.5)["kind"], "boost")
        g.on_self(fix(-15, 0, scale=0.5, mode_req=1), 2.0)
        self.assertEqual(g.meter(2.0)["kind"], "mud")
        g.on_self(fix(-15, 0, scale=0.0, mode_req=1, stopped=True), 2.5)
        self.assertEqual(g.meter(2.5)["kind"], "stopped")
        g.on_self(fix(0, 0, scale=1.0, mode_req=2), 3.0)   # SPORT + a 1.0 boost: scale unchanged, still a boost
        self.assertEqual(g.meter(3.0)["kind"], "boost")
        self.assertEqual(g.meter(9.0)["kind"], "normal")

    def test_boost_echo_result(self):
        g, _ = self.engine(boost_zone())
        self.assertIsNone(g.on_cmd({"t": "cmd", "src": 0, "cmd": 6, "arg": 100, "result": 1}, 1.0))
        res = g.on_cmd({"t": "cmd", "src": 254, "cmd": 6, "arg": 100, "result": 4}, 1.0)
        self.assertEqual(res["result_name"], "rejected")
        self.assertEqual(kc.CMD_NAMES[6], "boost")
        with self.assertRaises(kc.CommandError):
            kc.command_text("boost", 5)                     # never from the touchscreen / POST /cmd


def gps(e, n, kph=18.0, heading=90.0, hdop=1.0, sats=10):
    lat, lon = ll(e, n)
    return {"t": "gps", "kart": 2, "lat": lat, "lon": lon, "kph": kph, "heading": heading, "hdop": hdop,
            "sats": sats, "ms": 1000}


class GpsSamples(unittest.TestCase):
    def test_poor_samples_never_trigger(self):
        g = kc.GameEngine()
        g.set_zones(zones_doc(boost_zone(r=10), lap_zone()), 0.0)
        self.assertEqual(lines(g.on_gps(gps(-12, 0), 1.0)), [])
        r = g.on_gps(gps(-5, 0, hdop=4.5), 1.2) + g.on_gps(gps(0, 0, sats=4), 1.4) + g.on_gps(gps(5, 0, hdop=99.0), 1.6)
        self.assertEqual(r, [])                                     # inside the pad + across the line: nothing
        pos = g.snapshot(1.6)["pos"]
        self.assertEqual((pos["quality"], pos["usable"]), ("poor", False))
        self.assertIsNotNone(pos["lat"])                            # still drawn (greyed) on the HUD
        self.assertEqual(lines(g.on_gps(gps(2, 0), 1.8)), ["boost 5 1.00"])

    def test_outlier_rejected_then_accepted_when_repeated(self):
        f = kc.GpsFilter()
        self.assertEqual(f.feed(*ll(0, 0), 18.0, 90.0, 1.0, 10, 0.0, "gps")["quality"], "good")
        # 5 m/s * 0.2 s + 15 m = 16 m allowed; 40 m is a glitch
        self.assertEqual(f.feed(*ll(40, 0), 18.0, 90.0, 1.0, 10, 0.2, "gps")["quality"], "outlier")
        self.assertEqual(f.feed(*ll(1.5, 0), 18.0, 90.0, 1.0, 10, 0.4, "gps")["quality"], "good")    # back on track
        self.assertEqual(f.feed(*ll(80, 0), 18.0, 90.0, 1.0, 10, 0.6, "gps")["quality"], "outlier")
        r = f.feed(*ll(81, 0), 18.0, 90.0, 1.0, 10, 0.8, "gps")     # repeats near the jump: accept, new track
        self.assertEqual((r["quality"], r["reset"]), ("good", True))
        self.assertEqual(f.feed(*ll(82, 0), 18.0, 90.0, 1.0, 10, 1.0, "gps")["reset"], False)

    def test_outlier_does_not_trigger_or_cross(self):
        g = kc.GameEngine()
        g.set_zones(zones_doc(boost_zone(e=50, r=10), lap_zone()), 0.0)
        r = g.on_gps(gps(-3, 0), 1.0) + g.on_gps(gps(50, 0), 1.2)    # 53 m in 0.2 s: glitch into the pad, across the line
        self.assertEqual(lines(r) + [x["ev"] for x in r], [])
        self.assertEqual(g.snapshot(1.2)["pos"]["quality"], "outlier")

    def test_heading_held_below_1_5_mps(self):
        f = kc.GpsFilter()
        self.assertEqual(f.feed(*ll(0, 0), 10.0, 45.0, 1.0, 10, 0.0, "gps")["heading"], 45.0)
        self.assertEqual(f.feed(*ll(0.1, 0), 5.0, 270.0, 1.0, 10, 0.2, "gps")["heading"], 45.0)   # 1.39 m/s
        self.assertEqual(f.feed(*ll(0.2, 0), 6.0, 300.0, 1.0, 10, 0.4, "gps")["heading"], 300.0)  # 1.67 m/s

    def test_gps_lines_preferred_over_self(self):
        g = kc.GameEngine()
        g.set_zones(zones_doc(boost_zone(r=10)), 0.0)
        g.on_gps(gps(-20, 0), 1.0)
        # a self line saying "inside the pad" is ignored while gps lines are fresh
        self.assertEqual(lines(g.on_self(fix(0, 0), 1.1)), [])
        self.assertEqual(g.snapshot(1.1)["gps_source"], "gps")
        self.assertEqual(lines(g.on_gps(gps(-9, 0), 1.2)), ["boost 5 1.00"])
        # gps lines stop (gpsline off): the self line is the source again after 2 s
        g2 = kc.GameEngine()
        g2.set_zones(zones_doc(boost_zone(r=10)), 0.0)
        g2.on_gps(gps(-20, 0), 1.0)
        self.assertEqual(lines(g2.on_self(fix(-9, 0), 3.5)), ["boost 5 1.00"])
        self.assertEqual(g2.snapshot(3.5)["gps_source"], "self")

    def test_display_counts_gps_lines_not_bad(self):
        d = kc.KartDisplay(configured_id=2)
        self.assertEqual(d.ingest(gps(0, 0), 1.0, "w"), "gps")
        self.assertEqual((d.rx_bad, d.snapshot(1.0)["gps_lines"]), (0, 1))
        d.ingest(self_line(fix=True, lat=41.094, lon=-73.4167, hdop=1.2, gps_hz=5, fix_age_ms=180,
                           base_lat=41.0932, base_lon=-73.4187, base_fix_age_s=1), 1.2, "w")
        g = d.snapshot(1.2)["self"]["gps"]
        self.assertEqual((g["hdop_now"], g["gps_hz"], g["fix_age_ms"]), (1.2, 5, 180))
        self.assertEqual(g["base"], {"lat": 41.0932, "lon": -73.4187, "fix_age_s": 1})


class BaseCorrectionTests(unittest.TestCase):
    def base(self, de, dn, age=1):
        lat, lon = ll(de, dn)                  # the pit radio's live fix, de/dn metres off the surveyed point O
        return {"base_lat": lat, "base_lon": lon, "base_fix_age_s": age}

    def test_vector_exposed_but_off_by_default(self):
        c = kc.BaseCorrection()
        c.set_pit({"lat": O[0], "lon": O[1]})
        for t in range(10):
            c.feed(self.base(3, -4), float(t))
        snap = c.snapshot(9.0)
        self.assertFalse(snap["applied"])
        self.assertAlmostEqual(snap["vector"]["east_m"], -3.0, places=1)    # surveyed - live = -(3, -4)
        self.assertAlmostEqual(snap["vector"]["north_m"], 4.0, places=1)
        self.assertAlmostEqual(snap["vector"]["mag_m"], 5.0, places=1)
        self.assertEqual(c.apply(1.0, 2.0, 9.0), (1.0, 2.0, False))

    def test_applied_under_25_m_smoothed_and_stale_ignored(self):
        c = kc.BaseCorrection(enabled=True)
        c.set_pit({"lat": O[0], "lon": O[1]})
        c.feed(self.base(2, 0), 0.0)
        c.feed(self.base(4, 0), 1.0)
        c.feed(self.base(400, 0, age=9), 2.0)                  # base fix too old: not used
        lat, lon, ok = c.apply(*ll(10, 0), 2.0)
        self.assertTrue(ok)
        e, n = kc.local_en(lat, lon, O)
        self.assertAlmostEqual(e, 7.0, places=1)                # 10 - mean(2, 4)
        c.feed(self.base(2, 0), 40.0)                           # the window is 30 s: the old ones age out
        self.assertEqual(c.vector(40.0)["samples"], 1)
        far = kc.BaseCorrection(enabled=True)
        far.set_pit({"lat": O[0], "lon": O[1]})
        far.feed(self.base(30, 0), 0.0)
        self.assertFalse(far.apply(*ll(0, 0), 0.0)[2])
        self.assertIn("not applied", far.snapshot(0.0)["why_not"])

    def test_engine_uses_correction_when_enabled(self):
        g = kc.GameEngine(gps_correction=True)
        g.set_pit({"lat": O[0], "lon": O[1]})
        g.set_zones(zones_doc(boost_zone(e=0, r=10)), 0.0)
        g.on_self(fix(-30, 0, **self.base(-8, 0)), 0.5)       # live base fix 8 m west: shift east by 8
        self.assertTrue(g.snapshot(0.5)["correction"]["applied"])
        self.assertEqual(lines(g.on_gps(gps(-17, 0), 1.0)), ["boost 5 1.00"])    # -17 + 8 = -9: inside r 10


# --- race start, item boxes, heading slew, track, demo path ----------------------------
import glob  # noqa: E402
import random  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

sys.path.insert(0, os.path.join(KART, "sim"))
import sim_heltec  # noqa: E402


class FixedRng:
    def __init__(self, x):
        self.x = x

    def random(self):
        return self.x


def item_zone(e=0.0, n=0.0, r=5, **kw):
    lat, lon = ll(e, n)
    return {"id": "i1", "type": "item", "name": "Item", "shape": "circle", "lat": lat, "lon": lon, "r_m": r, **kw}


class Race(unittest.TestCase):
    def test_lights(self):
        self.assertEqual([kc.race_lights(x) for x in (10, 5.5, 5.0, 4.87, 3.2, 1.37, 0.37, 0.0, None)],
                         [0, 0, 1, 1, 2, 4, 5, 5, 0])

    def test_countdown_timing_and_go_by_clock(self):
        g = kc.GameEngine()
        g.set_zones(zones_doc(lap_zone()), 0.0)
        g.on_self(fix(-2, 0), 1.0)
        g.on_self(fix(2, 0), 1.5)                          # a lap running before the race
        self.assertTrue(g.snapshot(2.0)["lap"]["running"])
        r = g.on_race({"t": "race", "kart": 2, "event": "countdown", "go_in_ms": 4870, "secs": 5, "go_utc": 1762470000}, 10.0)
        self.assertEqual(r[0]["ev"], "race_countdown")
        rs = g.snapshot(10.0)["race"]
        self.assertEqual((rs["hold"], rs["go_in_s"], rs["lights"], rs["secs"]), (True, 4.87, 1, 5))
        self.assertEqual(g.snapshot(13.5)["race"]["lights"], 4)
        self.assertEqual(g.tick(14.86), [])                 # not yet
        self.assertTrue(g.grid_hold())
        r = g.tick(14.871)                                  # GO from arrival + go_in_ms, without the go line
        self.assertEqual([x["ev"] for x in r], ["lap_reset", "race_go"])
        self.assertEqual(r[-1]["how"], "clock")
        s = g.snapshot(15.0)
        self.assertFalse(s["race"]["hold"])
        self.assertEqual((s["lap"]["count"], s["lap"]["running"]), (0, False))
        self.assertFalse(s["race"]["go_confirmed"])
        r = g.on_race({"t": "race", "kart": 2, "event": "go"}, 14.95)          # confirms; no second GO
        self.assertEqual([x["ev"] for x in r], ["race_go_line"])
        self.assertTrue(g.snapshot(15.0)["race"]["go_confirmed"])

    def test_laps_count_from_first_crossing_after_go(self):
        g = kc.GameEngine()
        g.set_zones(zones_doc(lap_zone(min_lap_s=5)), 0.0)
        g.on_race({"t": "race", "event": "countdown", "go_in_ms": 3000, "secs": 3}, 0.0)
        r = g.on_self(fix(-2, 0), 1.0) + g.on_self(fix(2, 0), 1.5)          # creeping over the line in the hold
        self.assertEqual(evs(r, "lap_ignored")[0]["why"], "before GO")
        g.tick(3.0)
        r = g.on_self(fix(-2, 0), 3.2) + g.on_self(fix(2, 0), 3.4)          # first crossing after GO
        self.assertEqual(len(evs(r, "lap_start")), 1)
        self.assertEqual(g.snapshot(4.0)["lap"]["count"], 0)

    def test_go_line_first_and_stop_start_release(self):
        g = kc.GameEngine()
        g.on_race({"t": "race", "event": "countdown", "go_in_ms": 5000, "secs": 5}, 0.0)
        r = g.on_race({"t": "race", "event": "go"}, 4.9)                     # the line can beat the clock
        self.assertEqual([x["ev"] for x in r], ["race_go", "race_go_line"])
        self.assertEqual(r[0]["how"], "line")
        g.on_race({"t": "race", "event": "countdown", "go_in_ms": 5000, "secs": 5}, 10.0)
        g.on_cmd({"t": "cmd", "src": 0, "cmd": 2, "arg": 0, "result": 1}, 11.0)   # pit STOP ends the hold
        rs = g.snapshot(11.0)["race"]
        self.assertEqual((rs["hold"], rs["how"]), (False, "stop"))
        self.assertEqual(g.tick(20.0), [])                  # and no GO afterwards
        g.on_race({"t": "race", "event": "countdown", "go_in_ms": 5000, "secs": 5}, 30.0)
        g.on_cmd({"t": "cmd", "src": 0, "cmd": 3, "arg": 0, "result": 1}, 31.0)   # START releases early
        self.assertEqual(g.snapshot(31.0)["race"]["how"], "start")

    def test_finish_freezes_laps_until_next_countdown(self):
        g = kc.GameEngine()
        g.set_zones(zones_doc(lap_zone(min_lap_s=5)), 0.0)
        g.on_race({"t": "race", "event": "countdown", "go_in_ms": 1000, "secs": 1}, 0.0)
        g.tick(1.5)
        g.on_self(fix(-2, 0), 2.0); g.on_self(fix(2, 0), 2.2)              # lap 1 starts
        g.on_self(fix(8, 0), 5.0); g.on_self(fix(2, 0), 8.0)
        g.on_self(fix(-2, 0), 9.0)                                          # lap 1 done (~6.5 s)
        self.assertEqual(g.snapshot(9.0)["lap"]["count"], 1)
        r = g.on_race({"t": "race", "kart": 2, "event": "finish", "position": 3}, 10.0)
        self.assertEqual(evs(r, "race_finish")[0]["position"], 3)
        rs = g.snapshot(11.0)["race"]
        self.assertEqual((rs["finished"], rs["position"], rs["finish_age_s"], rs["hold"]), (True, 3, 1.0, False))
        self.assertEqual(rs["finish_lap"]["laps"], 1)
        self.assertIsNotNone(rs["finish_lap"]["best_s"])
        self.assertFalse(g.snapshot(11.0)["lap"]["running"])
        g.on_self(fix(-8, 0), 13.0)
        r = g.on_self(fix(2, 0), 16.0)                                      # crossing after the flag: ignored
        self.assertEqual(evs(r, "lap_ignored")[0]["why"], "finished")
        self.assertEqual(g.snapshot(17.0)["lap"]["count"], 1)
        g.on_race({"t": "race", "event": "finish", "position": 0}, 18.0)    # finish with no position
        self.assertEqual(g.snapshot(18.0)["race"]["position"], 0)
        g.on_race({"t": "race", "event": "countdown", "go_in_ms": 5000, "secs": 5}, 20.0)   # next race clears it
        rs = g.snapshot(20.0)["race"]
        self.assertEqual((rs["finished"], rs["position"], rs["hold"]), (False, None, True))

    def test_sim_finish_command(self):
        """The simulator (and demo mode) mirror the firmware: race 100+P -> a finish line with P."""
        out = []
        sim = sim_heltec.Sim(sim_heltec.sim_args(kart=2))
        sim.out.append(out.append)
        with sim.lock:
            sim.handle_line("race 5")
            sim.handle_line("finish 3")
            sim.handle_line("finish")
        msgs = [json.loads(x) for b in out for x in (b.decode() if isinstance(b, bytes) else b).splitlines() if x.startswith("{")]
        fins = [m for m in msgs if m.get("t") == "race" and m.get("event") == "finish"]
        self.assertEqual([m["position"] for m in fins], [3, 0])
        self.assertIsNone(sim.me.race_go)                     # finish releases the grid hold

    def test_no_zone_commands_in_grid_hold(self):
        g = kc.GameEngine()
        g.set_zones(zones_doc(boost_zone(r=10), mud_zone(x0=40, x1=60)), 0.0)
        g.on_race({"t": "race", "event": "countdown", "go_in_ms": 30000, "secs": 30}, 0.0)
        r, t = Game.drive(None, g, [(-20, 0), (0, 0), (5, 0), (30, 0), (45, 0), (50, 0)])
        self.assertEqual(lines(r), [])
        self.assertEqual(evs(r, "suppressed")[0]["why"], "grid hold")
        self.assertEqual(g.meter(3.0)["kind"], "grid")


class PageHelpers(unittest.TestCase):
    """Small pure functions of the driver page and the shared sound helper."""

    def test_sound_js_is_shared(self):
        with open(os.path.join(KART, "server", "static", "sound.js")) as f:
            kart = f.read()
        with open(os.path.join(os.path.dirname(KART), "pit", "server", "static", "sound.js")) as f:
            pit = f.read()
        self.assertEqual(kart, pit, "kart and pit sound.js must be identical copies")

    def test_js(self):
        node = find_node()
        if not node:
            self.skipTest("node not installed")
        js = "\n".join(js_function(n) for n in ("nul", "pitSignalClass", "lapTime", "finishText"))
        prog = js + """
console.log(JSON.stringify([[-60, -89.9, -90, -104, -105, -120, null].map(pitSignalClass),
  finishText({position: 3, finish_lap: {laps: 5, best_s: 27.61}}), finishText({position: 0, finish_lap: {laps: 1, best_s: null}})]));"""
        out = json.loads(subprocess.run([node, "-e", prog], capture_output=True, text=True, timeout=20).stdout)
        self.assertEqual(out[0], ["ok", "ok", "warn", "warn", "bad", "bad", ""])
        self.assertEqual(out[1]["pos"], "P3")
        self.assertIn("5 LAPS", out[1]["stats"]); self.assertIn("0:27.6", out[1]["stats"])
        self.assertEqual(out[2]["pos"], "")
        self.assertIn("1 LAP ", out[2]["stats"])

    def test_sound_kinds_cover_the_brief(self):
        node = find_node()
        if not node:
            self.skipTest("node not installed")
        with open(os.path.join(KART, "server", "static", "sound.js")) as f:
            src = f.read()
        prog = "const window = {addEventListener(){}}; const localStorage = {getItem(){return null}};\n" + src \
            + "\nconsole.log(JSON.stringify([GKSound.KINDS, GKSound.play('go')]));"
        kinds, played = json.loads(subprocess.run([node, "-e", prog], capture_output=True, text=True, timeout=20).stdout)
        for k in ("light", "go", "grid", "roulette", "item", "boost", "super", "mud", "lap", "best", "finish", "stop"):
            self.assertIn(k, kinds)
        self.assertFalse(played)                                  # no AudioContext in node: plays nothing, no throw


class Items(unittest.TestCase):
    def test_roll_distribution_seeded(self):
        rng = random.Random(20261106)
        n = 20000
        counts = {}
        for _ in range(n):
            name, secs, scale = kc.roll_item(rng)
            counts[name] = counts.get(name, 0) + 1
            self.assertTrue(scale is None or scale <= 1.0)
        for name, p, _, _ in kc.ITEM_TABLE:
            self.assertAlmostEqual(counts[name] / n, p, delta=0.012, msg=name)

    def test_each_outcome_command(self):
        for x, item, line in ((0.10, "boost", ["boost 5 1.00"]), (0.50, "super", ["boost 8 1.00"]),
                              (0.70, "mud", ["boost 3 0.50"]), (0.95, "empty", [])):
            g = kc.GameEngine(rng=FixedRng(x))
            g.set_zones(zones_doc(item_zone()), 0.0)
            g.on_self(fix(-20, 0), 1.0)
            r = g.on_self(fix(-4, 0), 1.5)
            self.assertEqual(evs(r, "item")[0]["item"], item)
            self.assertEqual(lines(r), line, item)

    def test_cooldown_15_and_gates(self):
        g = kc.GameEngine(rng=FixedRng(0.1))
        g.set_zones(zones_doc(item_zone()), 0.0)
        self.assertEqual(lines(g.on_self(fix(0, 0), 1.0)), ["boost 5 1.00"])
        g.on_self(fix(10, 0), 1.5)                         # out (> 3 m past the 5 m edge)
        r = g.on_self(fix(0, 0), 16.0)                     # 14.5 s after the exit
        self.assertEqual(lines(r), [])
        self.assertIn("cooldown", evs(r, "suppressed")[0]["why"])
        g.on_self(fix(10, 0), 16.5)
        self.assertEqual(lines(g.on_self(fix(0, 0), 32.0)), ["boost 5 1.00"])
        st = kc.GameEngine(rng=FixedRng(0.1))
        st.set_zones(zones_doc(item_zone()), 0.0)
        r = st.on_self(fix(0, 0, stopped=True), 1.0)
        self.assertEqual((lines(r), evs(r, "item")), ([], []))
        weak = kc.GameEngine(rng=FixedRng(0.1))
        weak.set_zones(zones_doc(item_zone()), 0.0)
        self.assertEqual(weak.on_gps(gps(0, 0, hdop=5.0), 1.0), [])
        off = kc.GameEngine(rng=FixedRng(0.1))
        off.set_zones(zones_doc(item_zone(), enabled=False), 0.0)
        self.assertEqual(off.on_self(fix(0, 0), 1.0), [])

    def test_small_zones_allowed(self):
        en, areas, _, ign = kc.parse_zones(zones_doc(item_zone(r=3), boost_zone(r=3) | {"id": "b3"}, boost_zone(r=2.9)))
        self.assertEqual(sorted(z.id for z in areas), ["b3", "i1"])
        self.assertEqual([i["id"] for i in ign], ["b1"])


def find_node():
    n = shutil.which("node")
    if n:
        return n
    c = sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/node")))
    return c[-1] if c else None


def js_function(name):
    with open(os.path.join(KART, "server", "static", "app.js")) as f:
        src = f.read()
    m = re.search(r"^function " + name + r"\(.*?^}", src, re.S | re.M)
    return m.group(0)


class HeadingSlew(unittest.TestCase):
    def test_python_slew(self):
        self.assertEqual(kc.slew_heading(None, 45.0, 0.1), 45.0)
        self.assertEqual(kc.slew_heading(30.0, None, 1.0), 30.0)       # no heading (slow): hold
        self.assertAlmostEqual(kc.slew_heading(350.0, 10.0, 0.1), 2.0)   # shortest way across north, 12 deg max
        self.assertAlmostEqual(kc.slew_heading(0.0, 90.0, 0.5), 60.0)
        self.assertAlmostEqual(kc.slew_heading(80.0, 90.0, 0.5), 90.0)
        h = 0.0
        for _ in range(10):                                            # 1 s at 10 Hz towards 180
            h = kc.slew_heading(h, 180.0, 0.1)
        self.assertAlmostEqual(h, 120.0)

    def test_js_matches_python(self):
        node = find_node()
        if not node:
            self.skipTest("node not installed")
        js = "\n".join(js_function(n) for n in ("nul", "angleDiffDeg", "slewHeading", "raceLights"))
        cases = [[350, 10, 0.1], [0, 90, 0.5], [80, 90, 0.5], [10, 350, 0.05], [None, 45, 0.1], [30, None, 1]]
        prog = js + "\nconst c=" + json.dumps(cases) + ";console.log(JSON.stringify([c.map(x=>slewHeading(x[0],x[1],x[2],120)),"\
            "[10,5.5,5,4.87,3.2,1.37,0.37,0].map(x=>raceLights(x))]));"
        out = json.loads(subprocess.run([node, "-e", prog], capture_output=True, text=True, timeout=20).stdout)
        py = [kc.slew_heading(a, b, c) for a, b, c in cases]
        for j, p in zip(out[0], py):
            self.assertAlmostEqual(j, p, places=6)
        self.assertEqual(out[1], [kc.race_lights(x) for x in (10, 5.5, 5, 4.87, 3.2, 1.37, 0.37, 0)])


def square_track(closed=True, n=None, width=6):
    pts = [ll(-30, -20), ll(30, -20), ll(30, 20), ll(-30, 20)] if n is None else [ll(i, 0) for i in range(n)]
    return {"points": pts, "closed": closed, "width_m": width}


class Track(unittest.TestCase):
    def test_parse_and_tolerance(self):
        t = kc.parse_track({"track": square_track()})
        self.assertEqual((len(t["points"]), t["closed"], t["width_m"]), (4, True, 6.0))
        self.assertAlmostEqual(t["length_m"], 200.0, delta=0.5)
        self.assertEqual(kc.parse_track({"track": dict(square_track(), width_m=-1)})["width_m"], 6.0)
        for bad in ({"points": [ll(0, 0)]}, square_track(n=201), {"points": [[1, "x"], [2, 3]]},
                    {"points": [[91, 0], [0, 0]]}, {"points": "nope"}, "track", {"points": [ll(0, 0), ll(1, 0)]}):
            self.assertIsNone(kc.parse_track({"track": bad}), bad)
        g = kc.GameEngine()
        g.set_zones(dict(zones_doc(boost_zone()), track={"points": [ll(0, 0)]}), 0.0)   # bad track, zones kept
        s = g.snapshot(0.0)
        self.assertTrue(s["on"])
        self.assertIsNone(s["track"])
        self.assertIn("track", [i["id"] for i in s["ignored"]])
        self.assertEqual(lines(g.on_self(fix(0, 0), 1.0)), ["boost 5 1.00"])

    def test_chevrons_follow_point_order(self):
        t = kc.parse_track({"track": square_track()})
        ch = kc.track_chevrons(t, 15.0)
        self.assertEqual(len(ch), 13)                                  # 200 m loop / 15 m
        self.assertAlmostEqual(ch[0][2], 90.0, places=3)                # first leg runs east
        self.assertAlmostEqual(ch[-1][2], 180.0, places=3)              # closing leg runs south
        open_ = kc.parse_track({"track": square_track(closed=False)})
        self.assertLess(len(kc.track_chevrons(open_, 15.0)), len(ch))


def drive_path(path, g, mph=15.0, laps=2.2, dt=0.2):
    """Drive the GameEngine along a demo path (sim PolyPath) with 5 Hz gps lines."""
    P = sim_heltec.PolyPath(path["points"])
    v = mph * 0.44704
    recs, t, s = [], 0.0, 0.0
    while s < P.length * laps:
        lat, lon, hd = P.at(s)
        recs += g.on_gps({"t": "gps", "lat": lat, "lon": lon, "kph": v * 3.6, "heading": hd, "hdop": 1.0, "sats": 10}, t)
        recs += g.tick(t)
        t += dt
        s += v * dt
    return recs


class DemoPath(unittest.TestCase):
    def course(self):
        with open(os.path.join(KART, "sim", "zones.course.json")) as f:
            doc = json.load(f)
        doc["zones"].append(item_zone(e=-35, n=0) | {"id": "i9"})
        return doc

    def check(self, doc, mph=15.0):
        path = kc.demo_path(doc, mph=mph)
        g = kc.GameEngine(rng=FixedRng(0.1))
        g.set_zones(dict(doc, enabled=True), 0.0)
        recs = drive_path(path, g, mph=mph)
        entered = {r["zone"] for r in recs if r["ev"] == "zone_enter"}
        areas = {z["id"] for z in doc["zones"] if z["type"] in ("boost", "mud", "item")}
        self.assertEqual(entered, areas, path)
        self.assertEqual(len(evs(recs, "lap_start")), 1)
        self.assertGreaterEqual(len(evs(recs, "lap")), 1, [r for r in recs if r["ev"].startswith("lap")])
        self.assertEqual(evs(recs, "lap_ignored"), [])                  # the line is crossed once per loop
        return path

    def test_course_zones(self):
        p = self.check(self.course())
        self.assertEqual(p["source"], "zones")

    def test_builtin_demo_course(self):
        self.check(kc.demo_zones((41.0932, -73.4187)))

    def test_short_course_gets_a_detour(self):
        doc = zones_doc(boost_zone(e=6, n=6, r=4), mud_zone(x0=-12, x1=-4, y0=2, y1=10), item_zone(e=0, n=14, r=4),
                        lap_zone(min_lap_s=20))
        p = self.check(doc)
        self.assertGreater(p["length_m"], 25 * 15 * 0.44704 - 1)

    def test_uses_the_track(self):
        doc = dict(zones_doc(boost_zone(e=30, n=0, r=5), lap_zone()), track=square_track())
        p = kc.demo_path(doc)
        self.assertEqual(p["source"], "track")
        self.assertEqual(p["points"], kc.parse_track(doc)["points"])


class RacePosition(unittest.TestCase):
    def test_self_fields_and_change_detection(self):
        g = kc.GameEngine()
        self.assertEqual(g.on_self(fix(0, 0, race_pos=0, race_state=0), 1.0, "w1"), [])     # nothing yet
        r = g.on_self(fix(0, 0, race_pos=3, race_state=2), 2.0, "w2")
        self.assertEqual([(x["ev"], x["state"]) for x in evs(r, "race_state")], [("race_state", 2)])
        self.assertEqual([(x["ev"], x["pos"], x["prev"], x["dir"]) for x in evs(r, "race_position")],
                         [("race_position", 3, 0, "new")])
        self.assertEqual(g.on_self(fix(0, 0, race_pos=3, race_state=2), 2.5, "w3"), [])     # unchanged: no event
        r = g.on_self(fix(0, 0, race_pos=2, race_state=2), 3.0, "w4")
        self.assertEqual(r[0]["dir"], "up")
        r = g.on_race({"t": "race", "kart": 2, "event": "position", "position": 4, "state": 2}, 4.0, "w5")
        self.assertEqual((r[0]["pos"], r[0]["dir"]), (4, "down"))
        rs = g.snapshot(5.0)["race"]
        self.assertEqual((rs["pos"], rs["state"], rs["state_name"], rs["pos_prev"], rs["pos_changed_wall"]),
                         (4, 2, "running", 2, "w5"))
        self.assertEqual(rs["pos_changed_age_s"], 1.0)
        r = g.on_self(fix(0, 0, race_pos=0, race_state=0), 6.0, "w6")
        self.assertEqual(evs(r, "race_position")[0]["dir"], "gone")
        self.assertEqual(g.snapshot(6.0)["race"]["state_name"], "idle")

    def test_bad_values_ignored(self):
        g = kc.GameEngine()
        for bad in (9, -1, "3", None, 2.5e9):
            self.assertEqual(g.race_position(bad, 7, 1.0), [], bad)
        rs = g.snapshot(1.0)["race"]
        self.assertEqual((rs["pos"], rs["state"]), (0, 0))
        d = kc.KartDisplay(configured_id=2)
        d.ingest(self_line(race_pos=3, race_state=2), 1.0, "w")
        self.assertEqual(d.snapshot(1.0)["self"]["status"]["race_pos"], 3)

    def test_finish_prefers_firmware_position_then_beacon(self):
        g = kc.GameEngine()
        g.on_self(fix(0, 0, race_pos=2, race_state=2), 1.0)
        g.on_race({"t": "race", "event": "finish", "position": 1}, 2.0)
        rs = g.snapshot(2.0)["race"]
        self.assertEqual((rs["position"], rs["position_source"]), (1, "finish"))
        g2 = kc.GameEngine()
        g2.on_self(fix(0, 0, race_pos=3, race_state=2), 1.0)
        g2.on_self(fix(0, 0, race_pos=0, race_state=3), 1.5)      # beacon may drop the position at the flag
        g2.on_race({"t": "race", "event": "finish", "position": 0}, 2.0)
        rs = g2.snapshot(2.0)["race"]
        # beacon state 3 already finished it with the last beacon position; the late finish keeps it
        self.assertEqual((rs["position"], rs["position_source"], rs["finish_age_s"]), (3, "beacon_state", 0.5))
        g3 = kc.GameEngine()
        g3.on_race({"t": "race", "event": "finish", "position": 0}, 2.0)
        self.assertEqual(g3.snapshot(2.0)["race"]["position"], 0)

    def test_beacon_state_3_finishes_within_one_self_line(self):
        """Pit STOP RACE: beacon state 3 (+ final position) must show FINISH at the next self line even
        when the kart's position did not change and no firmware finish command has arrived."""
        g = kc.GameEngine()
        g.on_race({"t": "race", "event": "countdown", "go_in_ms": 1000, "secs": 1}, 0.0)
        g.tick(1.0)
        g.on_self(fix(0, 0, race_pos=2, race_state=2), 2.0)
        self.assertEqual(g.snapshot(2.0)["race"]["state_name"], "running")
        r = g.on_self(fix(0, 0, race_pos=2, race_state=3), 2.5)       # same position, state 3
        self.assertEqual([x["ev"] for x in r], ["race_state", "race_finish"])
        rs = g.snapshot(2.5)["race"]
        self.assertEqual((rs["state"], rs["state_name"], rs["finished"], rs["position"], rs["position_source"],
                          rs["finish_age_s"]), (3, "finished", True, 2, "beacon_state", 0.0))
        g.on_self(fix(0, 0, race_pos=2, race_state=3), 3.0)            # repeats: no second finish
        r = g.on_race({"t": "race", "event": "finish", "position": 1}, 4.0)   # firmware finish later
        self.assertEqual([x["ev"] for x in r], ["race_finish_cmd"])
        rs = g.snapshot(4.0)["race"]
        self.assertEqual((rs["position"], rs["position_source"], rs["finish_age_s"]), (1, "finish", 1.5))
        g.on_self(fix(0, 0, race_pos=0, race_state=0), 6.0)            # NEW RACE: idle, chip stays
        rs = g.snapshot(6.0)["race"]
        self.assertEqual((rs["state_name"], rs["finished"]), ("idle", True))
        g.on_self(fix(0, 0, race_pos=0, race_state=1), 7.0)            # next countdown via the beacon
        self.assertFalse(g.snapshot(7.0)["race"]["finished"])

    def test_firmware_finish_reads_finished_while_beacon_still_running(self):
        """kart 2, 2026-11-06 01:51: firmware finish P1, beacon state still 2 for 5 s, then 0."""
        g = kc.GameEngine()
        g.on_self(fix(0, 0, race_pos=0, race_state=2), 1.0)
        g.on_race({"t": "race", "event": "finish", "position": 1}, 2.0)
        g.on_self(fix(0, 0, race_pos=0, race_state=2), 2.5)
        rs = g.snapshot(2.5)["race"]
        self.assertEqual((rs["state_name"], rs["beacon_state"], rs["position"]), ("finished", 2, 1))

    def test_js_change_direction_matches(self):
        node = find_node()
        if not node:
            self.skipTest("node not installed")
        cases = [[0, 3], [3, 2], [2, 4], [4, 0], [2, 2]]
        prog = js_function("posChange") + "\nconsole.log(JSON.stringify(" + json.dumps(cases) + ".map(c=>posChange(c[0],c[1]))));"
        out = json.loads(subprocess.run([node, "-e", prog], capture_output=True, text=True, timeout=20).stdout)
        self.assertEqual(out, ["new", "up", "down", "gone", "same"])


class SimRacePositions(unittest.TestCase):
    def sim(self, **kw):
        sim = sim_heltec.Sim(sim_heltec.sim_args(**kw))
        lines = []
        sim.out.append(lambda b: lines.append(json.loads(b)))
        return sim, lines

    def test_parse_sequence(self):
        self.assertEqual(sim_heltec.parse_pos_seq("3, 2,2,1"), [3, 2, 2, 1])
        self.assertEqual(sim_heltec.parse_pos_seq(""), [])
        with self.assertRaises(ValueError):
            sim_heltec.parse_pos_seq("3,9")

    def test_steps_every_10_s_and_holds_the_last(self):
        sim, lines = self.sim(race_pos="3,2,2,1")
        t0 = sim.pos_t0
        self.assertEqual([sim.position_at(t0 + x) for x in (0, 9.9, 10.1, 25, 31, 500)],
                         [(3, 2), (3, 2), (2, 2), (2, 2), (1, 2), (1, 2)])
        for x in (0, 10.1, 20.1, 30.1, 40):
            sim.update_position(t0 + x)
        ev = [(l["position"], l["state"]) for l in lines if l.get("event") == "position"]
        self.assertEqual(ev, [(3, 2), (2, 2), (1, 2)])          # one line per change, not per step

    def test_race_countdown_and_finish_states(self):
        sim, lines = self.sim(race_pos="2,1", race_pos_step=5)
        sim.handle_line("race 5")
        go = sim.me.race_go
        self.assertEqual(sim.position_at(go - 1), (2, 1))       # countdown: state 1, first position
        sim.me.race_go = None
        self.assertEqual(sim.position_at(go + 1), (2, 2))
        self.assertEqual(sim.position_at(go + 6), (1, 2))
        sim.handle_line("finish 1") if False else sim.command(0, 7, 101)
        self.assertEqual(sim.position_at(go + 7), (1, 3))
        self.assertEqual([l for l in lines if l.get("event") == "finish"][0]["position"], 1)

    def test_demo_cycles_p3_p2_p1_over_the_laps(self):
        sys.path.insert(0, os.path.join(KART, "server"))
        import demo
        self.assertEqual(demo.DEMO_POSITIONS, (3, 2, 1))
        lap = demo.demo_lap_s(198.5)
        self.assertAlmostEqual(lap, 198.5 / (15 * 0.44704), places=3)
        sim, _ = self.sim(race_pos="3,2,1", race_pos_step=lap)
        sim.handle_line("race 5")
        go = sim.me.race_go
        sim.me.race_go = None
        self.assertEqual([sim.position_at(go + x * lap + 1)[0] for x in range(4)], [3, 2, 1, 1])


class PiHealth(unittest.TestCase):
    """Pi power health (health.py is a byte-identical copy of the pit's) and the driver page chip."""

    def test_health_py_is_shared(self):
        with open(os.path.join(KART, "server", "health.py")) as f:
            kart = f.read()
        with open(os.path.join(os.path.dirname(KART), "pit", "server", "health.py")) as f:
            pit = f.read()
        self.assertEqual(kart, pit, "kart and pit health.py must be identical copies")

    def test_statuses(self):
        import health
        snap = health.Health(reader=lambda: "throttled=0x50005\n", temp_reader=lambda: 61.0).sample()
        self.assertEqual(snap["status"], "undervolt")
        self.assertEqual(snap["throttled"], "0x50005")
        f = snap["flags"]
        self.assertTrue(f["undervolt_now"] and f["throttled_now"])
        self.assertTrue(f["undervolt_since_boot"] and f["throttled_since_boot"])
        self.assertFalse(f["freq_capped_now"])
        self.assertEqual(health.Health(reader=lambda: "throttled=0x0", temp_reader=None).sample()["status"], "ok")
        real_which = health.shutil.which
        health.shutil.which = lambda name: None                 # no vcgencmd (Mac, CI)
        try:
            off = health.Health(temp_reader=lambda: None)
        finally:
            health.shutil.which = real_which
        self.assertFalse(off.available)
        self.assertEqual(off.sample()["status"], "unavailable")

    def test_chip_js(self):
        node = find_node()
        if not node:
            self.skipTest("node not installed")
        prog = js_function("healthChip") + """
console.log(JSON.stringify([{available:true,status:'undervolt'},{available:true,status:'throttled'},
  {available:true,status:'had_undervolt'},{available:true,status:'ok'},{available:false,status:'unavailable'},
  undefined].map(healthChip)));"""
        out = json.loads(subprocess.run([node, "-e", prog], capture_output=True, text=True, timeout=20).stdout)
        self.assertEqual(out[:3], [{"text": "UNDERVOLT", "cls": "bad"}, {"text": "THROTTLED", "cls": "warn"},
                                   {"text": "HAD UNDERVOLT", "cls": "stale"}])
        self.assertEqual(out[3:], [None, None, None])


if __name__ == "__main__":
    unittest.main(verbosity=1)
