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
    return {"t": "self", "kart": 2, "fix": True, "lat": lat, "lon": lon, "stopped": stopped,
            "vesc_link": True, "mode_req": 1, "scale": 0.714, **kw}


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
        doc = zones_doc(boost_zone(r=5), {"id": "x", "type": "oil", "shape": "circle"},
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

    def drive(self, g, pts, t0=1.0, dt=0.5, **kw):
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
        r = g.on_self(fix(2, 0), 9.5) + g.on_self(fix(-2, 0), 10.0)      # back across at 9.75: too soon
        self.assertEqual(evs(r, "lap_ignored")[0]["since_s"], 9.25)
        self.assertEqual(g.snapshot(10.0)["lap"]["count"], 0)
        r = g.on_self(fix(-2, 1), 29.5) + g.on_self(fix(2, 1), 30.0)     # eastbound, t = 29.75
        self.assertEqual(evs(r, "lap")[0]["lap_s"], 29.25)
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

    def test_lap_line_misses_past_its_end(self):
        g, _ = self.engine(lap_zone())
        r = g.on_self(fix(-2, 10), 0.0) + g.on_self(fix(2, 10), 0.5)
        self.assertEqual(evs(r, "lap_start"), [])

    def test_stopped_kart_sends_nothing(self):
        g, _ = self.engine(boost_zone(), mud_zone(x0=40, x1=60))
        r, t = self.drive(g, [(-20, 0), (0, 0), (5, 0), (30, 0), (45, 0), (50, 0), (55, 0)], stopped=True)
        self.assertEqual(lines(r), [])
        self.assertEqual(evs(r, "suppressed")[0]["why"], "kart STOPPED")
        # START while still inside the mud: the (safe) slowdown resumes; the boost was used up
        r = g.on_self(fix(56, 0, stopped=False), t) + g.tick(t + 0.25)
        self.assertEqual(lines(r), ["boost 3 0.50"])

    def test_disabled_sends_nothing(self):
        g, recs = self.engine(boost_zone(), mud_zone(x0=40, x1=60), lap_zone(), enabled=False)
        self.assertEqual(evs(recs, "game_on"), [])
        r, _ = self.drive(g, [(-20, 0), (0, 0), (5, 0), (30, 0), (45, 0), (50, 0)])
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
        g.on_self(fix(-30, 0, scale=0.714, mode_req=1), 1.0)
        self.assertEqual(g.meter(1.0)["kind"], "normal")
        g.on_self(fix(-30, 0, scale=1.0, mode_req=1), 1.5)
        self.assertEqual(g.meter(1.5)["kind"], "boost")
        g.on_self(fix(-30, 0, scale=0.5, mode_req=1), 2.0)
        self.assertEqual(g.meter(2.0)["kind"], "mud")
        g.on_self(fix(-30, 0, scale=0.0, mode_req=1, stopped=True), 2.5)
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
