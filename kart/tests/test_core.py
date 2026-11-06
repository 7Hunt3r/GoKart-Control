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


if __name__ == "__main__":
    unittest.main(verbosity=1)
