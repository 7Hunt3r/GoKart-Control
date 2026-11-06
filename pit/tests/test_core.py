"""Unit tests for pit/server/pitcore.py: derivations, staleness, command delivery.

Run:  python3 -I pit/tests/test_core.py      (or python3 -m unittest from pit/tests)
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import pitcore as pc  # noqa: E402


def tele(kart, **kw):
    msg = {"t": "tele", "kart": kart, "seq": 1, "lat": 41.07, "lon": -73.40, "fix": True,
           "gps_age_s": 0, "sats": 9, "gps_kph": 30.0, "heading": 90.0, "volts": 78.0,
           "motor_a": 10.0, "batt_a": 5.0, "erpm": 10000, "duty": 0.3, "esc_c": 35.0, "fault": 0,
           "mode_req": 1, "mode_actual": 1, "scale": 0.75, "stopped": False, "vesc_link": True,
           "mode_confirmed": True, "pps": True, "wifi": False, "writes": True,
           "last_cmd_seq": 0, "cmd_result": 0, "ah": 0.1, "uptime_s": 100,
           "base_rssi": -80, "rssi": -82, "snr": 7.0, "rx_ms": 1}
    msg.update(kw)
    return msg


class Derivations(unittest.TestCase):
    def test_battery_percent(self):
        self.assertEqual(pc.battery_percent(60.0), 0.0)
        self.assertEqual(pc.battery_percent(84.0), 100.0)
        self.assertEqual(pc.battery_percent(72.0), 50.0)
        self.assertEqual(pc.battery_percent(50.0), 0.0)      # clamped
        self.assertEqual(pc.battery_percent(90.0), 100.0)    # clamped
        self.assertIsNone(pc.battery_percent(None))          # never a fabricated zero

    def test_under_load(self):
        self.assertTrue(pc.under_load(25.0))
        self.assertTrue(pc.under_load(-25.0))
        self.assertFalse(pc.under_load(20.0))
        self.assertIsNone(pc.under_load(None))

    def test_wheel_mph_contract_formula(self):
        erpm = 12345
        want = erpm / 7 / 2.769 * (0.406 * math.pi) * 60 / 1609.34
        self.assertAlmostEqual(pc.wheel_mph(erpm), want, places=2)
        self.assertEqual(pc.wheel_mph(0), 0.0)                # valid zero stays zero
        self.assertIsNone(pc.wheel_mph(None))
        self.assertLess(pc.wheel_mph(-1000), 0)              # sign kept
        self.assertAlmostEqual(pc.wheel_mph(pc.erpm_for_mph(20.0)), 20.0, places=1)

    def test_kph_to_mph(self):
        self.assertAlmostEqual(pc.kph_to_mph(100), 62.14, places=2)
        self.assertIsNone(pc.kph_to_mph(None))


class Staleness(unittest.TestCase):
    def test_link_status_thresholds(self):
        self.assertEqual(pc.link_status(None, 100), "never")
        self.assertEqual(pc.link_status(100, 105.0), "online")
        self.assertEqual(pc.link_status(100, 105.1), "stale")
        self.assertEqual(pc.link_status(100, 115.0), "stale")
        self.assertEqual(pc.link_status(100, 115.1), "offline")

    def test_vesc_group_kept_separately_from_link(self):
        f = pc.Fleet()
        f.ingest(tele(1, volts=80.0), now=0.0, wall="t0")
        # radio keeps arriving but the VESC link is down: VESC values age, link stays online
        f.ingest(tele(1, vesc_link=False, volts=None, fault=None), now=8.0, wall="t8")
        s = f.karts[1].snapshot(8.5)
        self.assertEqual(s["link"], "online")
        self.assertEqual(s["vesc"]["volts"], 80.0)             # last valid, not overwritten by null
        self.assertAlmostEqual(s["vesc_age_s"], 8.5)
        self.assertFalse(s["vesc_fresh"])
        self.assertFalse(s["status"]["vesc_link"])

    def test_missing_values_are_null_not_zero(self):
        f = pc.Fleet()
        f.ingest(tele(2, volts=None, erpm=None, fault=None), now=0.0, wall="t0")
        s = f.karts[2].snapshot(0.1)
        self.assertIsNone(s["derived"]["battery_pct"])
        self.assertIsNone(s["derived"]["wheel_mph"])
        self.assertIsNone(s["fault_name"])                     # unknown is not "no fault"

    def test_no_fix_never_moves_position(self):
        f = pc.Fleet()
        f.ingest(tele(3, lat=41.1, lon=-73.5), now=0.0, wall="t0")
        f.ingest(tele(3, fix=False, lat=None, lon=None, gps_age_s=255), now=2.0, wall="t2")
        s = f.karts[3].snapshot(3.0)
        self.assertEqual(s["gps"]["last_fix"]["lat"], 41.1)
        self.assertFalse(s["gps"]["fix_now"])
        self.assertAlmostEqual(s["gps"]["fix_age_s"], 3.0)
        f2 = pc.Fleet()
        f2.ingest(tele(4, fix=False, lat=None, lon=None), now=0.0, wall="t0")
        self.assertIsNone(f2.karts[4].snapshot(0)["gps"]["last_fix"])
        self.assertIsNone(f2.current_origin())                 # never 0,0

    def test_never_heard_and_offline(self):
        f = pc.Fleet()
        self.assertEqual(f.karts[8].snapshot(50)["link"], "never")
        f.ingest(tele(8), now=0.0, wall="t0")
        self.assertEqual(f.karts[8].snapshot(20)["link"], "offline")
        self.assertEqual(f.karts[8].snapshot(20)["vesc"]["volts"], 78.0)  # kept, visibly aged

    def test_seq_gaps(self):
        f = pc.Fleet()
        for i, seq in enumerate([10, 11, 14, 15]):
            f.ingest(tele(1, seq=seq), now=float(i), wall="t")
        self.assertEqual(f.karts[1].seq_gaps, 2)

    def test_origin_priority(self):
        f = pc.Fleet(fallback_origin={"lat": 1.0, "lon": 2.0})
        self.assertEqual(f.current_origin()["source"], "config")
        f.ingest(tele(1, lat=41.0, lon=-73.0), 0.0, "t")
        self.assertEqual(f.current_origin()["source"], "kart")
        f.ingest(tele(2, lat=42.0, lon=-74.0), 0.0, "t")
        self.assertEqual(f.current_origin()["lat"], 41.0)      # first kart fix, does not jitter
        f.ingest({"t": "base", "lat": 41.5, "lon": -73.5, "fix": True}, 0.0, "t")
        self.assertEqual(f.current_origin()["source"], "base")

    def test_simulation_markers(self):
        f = pc.Fleet()
        f.ingest({"t": "stat", "fw": "base-0.1.0"}, 0, "t")
        self.assertFalse(f.simulation)
        f.ingest({"t": "stat", "fw": "sim-0.1.0"}, 0, "t")
        self.assertTrue(f.simulation)
        f2 = pc.Fleet()
        f2.ingest({"t": "log", "msg": "SIMULATION"}, 0, "t")
        self.assertTrue(f2.simulation)


class Harness:
    """Fleet + CommandManager with a captured send() and a manual clock."""

    def __init__(self, online=pc.KART_IDS, link_up=True):
        self.fleet = pc.Fleet()
        self.sent = []
        self.link_up = link_up
        self.now = 0.0
        self.last_seq = {k: 0 for k in pc.KART_IDS}
        self.cm = pc.CommandManager(self.fleet, self._send, start_seq=1)
        for k in online:
            self.hear(k)

    def _send(self, obj):
        if not self.link_up:
            return False
        self.sent.append(dict(obj))
        return True

    def hear(self, k, last_cmd_seq=None, cmd_result=None):
        if last_cmd_seq is not None:
            self.last_seq[k] = (last_cmd_seq, cmd_result)
        seq, res = self.last_seq[k] if isinstance(self.last_seq[k], tuple) else (0, 0)
        self.fleet.ingest(tele(k, last_cmd_seq=seq, cmd_result=res), self.now, "w")
        self.cm.on_tele(k, self.now, "w")

    def advance(self, dt, heard=()):
        """Move the clock, deliver tele from `heard` karts each 0.5 s, tick at 10 Hz."""
        end = self.now + dt
        while self.now < end - 1e-9:
            self.now = round(self.now + 0.1, 6)
            if abs((self.now * 2) - round(self.now * 2)) < 1e-6:
                for k in heard:
                    self.hear(k)
            self.cm.tick(self.now, "w")


class CommandDelivery(unittest.TestCase):
    def test_fleet_stop_eight_outcomes(self):
        h = Harness()
        c = h.cm.submit("stop", 255, source="t", now=h.now)
        self.assertEqual(h.sent, [{"cmd": "stop", "kart": 255, "seq": 1, "ttl": 10}])
        self.assertEqual(set(c.outcomes.values()), {"pending"})
        for k in range(1, 9):
            h.hear(k, c.seq, 2)
        self.assertEqual(set(c.outcomes.values()), {"applied"})
        h.advance(3.0, heard=pc.KART_IDS)
        self.assertEqual(c.state, "done")
        self.assertEqual(len(h.sent), 1)                       # no retransmit once all echoed

    def test_retransmit_every_1_5s_up_to_5_times(self):
        h = Harness()
        c = h.cm.submit("stop", 255, now=h.now)
        for k in range(1, 8):
            h.hear(k, c.seq, 2)                                # kart 8 never echoes
        h.advance(12.0, heard=pc.KART_IDS)
        self.assertEqual(c.tx_count, 6)                        # initial + 5 retransmits
        self.assertEqual(len(h.sent), 6)
        # only kart 8 still owed an echo -> retransmits are addressed to it, same seq
        self.assertTrue(all(s == {"cmd": "stop", "kart": 8, "seq": c.seq, "ttl": 10} for s in h.sent[1:]))
        self.assertEqual(c.outcomes[8], "no_ack")
        self.assertEqual(c.state, "done")

    def test_retransmit_uses_broadcast_while_several_missing(self):
        h = Harness()
        c = h.cm.submit("start", 255, now=h.now)
        h.hear(1, c.seq, 2)
        h.advance(1.6, heard=pc.KART_IDS)
        self.assertEqual(h.sent[1]["kart"], 255)

    def test_offline_kart_marked_and_late_echo_upgrades(self):
        h = Harness(online=range(1, 8))                         # kart 8 never heard
        c = h.cm.submit("stop", 255, now=h.now)
        self.assertEqual(c.outcomes[8], "offline")
        for k in range(1, 8):
            h.hear(k, c.seq, 2)
        h.advance(2.0, heard=range(1, 8))
        self.assertEqual(c.state, "done")
        self.assertEqual(len(h.sent), 1)                       # offline kart does not drive retries
        h.hear(8, c.seq, 2)                                    # it heard the broadcast after all
        self.assertEqual(c.outcomes[8], "applied")

    def test_kart_going_silent_stops_retries(self):
        h = Harness()
        c = h.cm.submit("mode", 3, arg=2, now=h.now)
        self.assertEqual(h.sent[0], {"cmd": "mode", "kart": 3, "seq": c.seq, "arg": 2, "ttl": 10})
        h.advance(7.0, heard=[k for k in pc.KART_IDS if k != 3])   # kart 3 silent -> stale after 5 s
        self.assertEqual(c.state, "done")
        self.assertEqual(c.outcomes[3], "no_ack")
        self.assertLessEqual(c.tx_count, 5)

    def test_result_codes_mapped(self):
        h = Harness()
        c = h.cm.submit("mode", 255, arg=0, now=h.now)
        for k, res in zip(range(1, 9), [0, 1, 2, 3, 4, 5, 2, 2]):
            h.hear(k, c.seq, res)
        self.assertEqual([c.outcomes[k] for k in range(1, 7)],
                         ["received", "accepted", "applied", "no_vesc", "rejected", "unknown"])

    def test_seq_rolls_1_255_and_skips_seqs_karts_report(self):
        h = Harness()
        h.cm.next_seq = 254
        h.last_seq[2] = (255, 2)
        h.hear(2)                                              # kart 2 last handled seq 255
        a = h.cm.submit("ping", 1, now=h.now)
        self.assertEqual(a.seq, 254)
        h.hear(1, a.seq, 2)
        b = h.cm.submit("ping", 1, now=h.now)
        self.assertEqual(b.seq, 1)                             # 255 skipped (kart 2 would ignore it), 0 never used
        self.assertEqual(h.sent[0], {"cmd": "ping", "kart": 1, "seq": 254})

    def test_per_kart_serialization_and_accept_hold(self):
        h = Harness()
        m = h.cm.submit("mode", 4, arg=2, now=h.now)
        s = h.cm.submit("start", 4, now=h.now)
        self.assertEqual(s.state, "queued")                    # waits for the mode command
        h.hear(4, m.seq, 1)                                    # accepted, not yet applied
        h.advance(1.0, heard=pc.KART_IDS)
        self.assertEqual(s.state, "queued")                    # accept-hold
        h.hear(4, m.seq, 2)                                    # applied
        h.advance(0.2, heard=pc.KART_IDS)
        self.assertEqual(s.state, "active")
        self.assertEqual(m.outcomes[4], "applied")

    def test_stop_preempts_queue_and_supersedes(self):
        h = Harness()
        fleet_mode = h.cm.submit("mode", 255, arg=2, now=h.now)
        queued = h.cm.submit("start", 5, now=h.now)
        self.assertEqual(queued.state, "queued")
        stop = h.cm.submit("stop", 5, now=h.now)
        self.assertEqual(stop.state, "active")                 # STOP never waits
        self.assertEqual(queued.outcomes[5], "cancelled")
        self.assertEqual(fleet_mode.outcomes[5], "superseded")
        h.hear(5, stop.seq, 2)
        h.advance(1.6, heard=pc.KART_IDS)
        # fleet mode retransmit must not re-trigger kart 5: addressed lines, kart 5 excluded
        retx = [s for s in h.sent if s.get("seq") == fleet_mode.seq][1:]
        self.assertTrue(retx)
        self.assertNotIn(255, [s["kart"] for s in retx])
        self.assertNotIn(5, [s["kart"] for s in retx])

    def test_link_down_is_not_replayed(self):
        h = Harness()
        c = h.cm.submit("stop", 255, now=h.now)
        q = h.cm.submit("start", 255, now=h.now)
        h.link_up = False
        h.cm.on_link_down(h.now)
        self.assertEqual(set(c.outcomes.values()), {"link_lost"})
        self.assertEqual(set(q.outcomes.values()), {"link_lost"})
        h.link_up = True
        n = len(h.sent)
        h.advance(5.0, heard=pc.KART_IDS)
        self.assertEqual(len(h.sent), n)                       # nothing resent after reconnect

    def test_validation(self):
        h = Harness()
        for args in [("fly", 1, None), ("stop", 9, None), ("stop", "x", None), ("mode", 1, 3), ("mode", 1, None)]:
            with self.assertRaises(pc.CommandError):
                h.cm.submit(*args, now=h.now)

    def test_snapshot_outcome_table(self):
        h = Harness()
        c = h.cm.submit("stop", 255, now=h.now)
        snap = h.cm.snapshot()
        self.assertEqual(snap["last_fleet"]["id"], c.id)
        self.assertEqual(len(snap["last_fleet"]["outcomes"]), 8)
        self.assertEqual(snap["per_kart"]["3"]["cmd"], "stop")


# ---------------------------------------------------------------------------
# Game zones + laps (map/ZONES.md)
# ---------------------------------------------------------------------------
LAT0, LON0 = 41.0940, -73.4165
M_LAT = 1 / 111194.93                       # degrees per metre north
M_LON = M_LAT / math.cos(math.radians(LAT0))


def at(e, n):
    """lat/lon of a point e metres east, n metres north of LAT0/LON0."""
    return [round(LAT0 + n * M_LAT, 7), round(LON0 + e * M_LON, 7)]


def zdoc(*zones, enabled=True):
    return {"enabled": enabled, "zones": list(zones)}


LAPLINE = {"id": "lap", "type": "lapline", "shape": "line", "a": at(0, -10), "b": at(0, 10)}


class ZoneValidation(unittest.TestCase):
    def test_good_document_normalized(self):
        d = pc.validate_zones(zdoc(
            {"id": "b1", "type": "boost", "shape": "circle", "lat": LAT0, "lon": LON0, "r_m": 12, "scale": 3, "secs": 99},
            {"id": "m1", "type": "mud", "shape": "polygon", "points": [at(0, 0), at(20, 0), at(10, 15)], "scale": 0.01},
            dict(LAPLINE)))
        b, m, lap = d["zones"]
        self.assertEqual((b["scale"], b["secs"], b["cooldown_s"], b["name"]), (1.0, 30, 20, "Boost pad"))  # clamped
        self.assertEqual(m["scale"], 0.1)
        self.assertEqual((lap["min_lap_s"], lap["name"]), (20, "Start / finish"))
        self.assertTrue(d["enabled"])

    def test_refused(self):
        circle = {"id": "b1", "type": "boost", "shape": "circle", "lat": LAT0, "lon": LON0, "r_m": 12}
        bad = [
            zdoc(dict(circle, r_m=7.9)),                                      # below 8 m
            zdoc({"id": "m", "type": "mud", "shape": "polygon", "points": [at(0, 0), at(9, 9)]}),
            zdoc(circle, dict(circle)),                                       # duplicate id
            zdoc(dict(circle, id="")),
            zdoc(dict(circle, id="has space")),
            zdoc(dict(circle, shape="line")),
            zdoc(dict(circle, lat=95)),
            zdoc(dict(circle, r_m="12")),
            zdoc(dict(LAPLINE, b=at(0, -9))),                                 # 1 m line
            zdoc(dict(LAPLINE, shape="circle")),
            zdoc(LAPLINE, dict(LAPLINE, id="lap2")),                          # one lap line only
            {"zones": "nope"}, [], {"zones": [], "enabled": "yes"},
            zdoc({"id": "x", "type": 7}),
        ]
        for doc in bad:
            with self.assertRaises(pc.ZoneError, msg=doc):
                pc.validate_zones(doc)
        self.assertTrue(issubclass(pc.ZoneError, pc.CommandError))         # -> HTTP 409 like commands

    def test_unknown_type_passthrough(self):
        d = pc.validate_zones(zdoc({"id": "o1", "type": "oil", "shape": "circle", "lat": LAT0, "lon": LON0, "r_m": 9}))
        self.assertEqual(d["zones"][0]["type"], "oil")


class Laps(unittest.TestCase):
    def setUp(self):
        self.f = pc.Fleet()
        self.set(zdoc(dict(LAPLINE, min_lap_s=20)))
        self.t = 1000.0

    def set(self, doc, version=1):
        d = pc.validate_zones(doc)
        d["version"] = version
        self.f.game.set_doc(d)

    def fix(self, k, e, n, dt=1.0):
        self.t += dt
        lat, lon = at(e, n)
        self.f.ingest(tele(k, lat=lat, lon=lon), self.t, "w%.1f" % self.t)

    def lap(self, k):
        return self.f.snapshot(self.t)["karts"][k - 1]["lap"]

    def test_crossing_starts_then_counts_laps(self):
        self.fix(1, -5, 0)
        self.assertIsNone(self.lap(1))
        self.fix(1, 5, 0)                                   # first crossing: clock starts, lap 0
        self.assertEqual((self.lap(1)["laps"], self.lap(1)["running"]), (0, True))
        self.fix(1, 20, 0, dt=10)
        self.fix(1, -5, 0, dt=15)                            # back across westbound (either direction)
        self.assertEqual(self.lap(1)["laps"], 1)
        self.assertAlmostEqual(self.lap(1)["last_s"], 25.0, delta=1.5)
        self.fix(1, 5, 0, dt=30)
        lp = self.lap(1)
        self.assertEqual(lp["laps"], 2)
        self.assertAlmostEqual(lp["best_s"], min(lp["last_s"], 25.0), delta=1.5)

    def test_interpolated_crossing_time(self):
        self.fix(1, -10, 0)
        self.fix(1, 10, 0, dt=2.0)                           # crossed halfway -> 1 s before receipt
        self.fix(1, 30, 0, dt=20.0)
        self.fix(1, -10, 0, dt=4.0)                          # crossed 3/4 along (30 -> -10 m)
        # start at t0+2-1, end at t0+2+20+3 -> 24 s
        self.assertAlmostEqual(self.lap(1)["last_s"], 24.0, places=1)

    def test_min_lap_s(self):
        self.fix(1, -5, 0)
        self.fix(1, 5, 0)
        self.fix(1, -5, 0, dt=5)                             # 5 s later: too soon, ignored
        self.assertEqual(self.lap(1)["laps"], 0)
        self.fix(1, 5, 0, dt=20)
        self.assertEqual(self.lap(1)["laps"], 1)

    def test_miss_line_and_outside_segment(self):
        self.fix(1, -5, 30)
        self.fix(1, 5, 30)                                   # passes north of the 20 m line
        self.assertIsNone(self.lap(1))

    def test_disabled_or_no_line(self):
        self.set(zdoc(LAPLINE, enabled=False))
        self.fix(1, -5, 0); self.fix(1, 5, 0)
        self.assertIsNone(self.lap(1))
        self.assertEqual(self.f.snapshot(self.t)["laps"], [])
        self.set(zdoc(enabled=True))
        self.fix(1, -5, 0); self.fix(1, 5, 0)
        self.assertEqual(self.f.snapshot(self.t)["game"]["timing"], False)

    def test_ranking_and_reset(self):
        for k in (1, 2, 3):
            self.fix(k, -5, 0)
        self.fix(2, 5, 0); self.fix(1, 5, 0); self.fix(3, 5, 0)     # start order 2, 1, 3
        self.fix(1, -5, 0, dt=25); self.fix(3, -5, 0)               # 1 and 3 complete lap 1; 2 does not
        board = self.f.snapshot(self.t)["laps"]
        self.assertEqual([r["kart"] for r in board], [1, 3, 2])
        self.assertEqual([r["pos"] for r in board], [1, 2, 3])
        self.f.game.reset_laps()
        self.assertEqual(self.f.snapshot(self.t)["laps"], [])

    def test_line_change_clears_laps(self):
        self.fix(1, -5, 0); self.fix(1, 5, 0)
        self.set(zdoc(LAPLINE, {"id": "b1", "type": "boost", "shape": "circle", "lat": LAT0, "lon": LON0, "r_m": 9}), 2)
        self.assertIsNotNone(self.lap(1))                    # unrelated edit keeps timing
        self.set(zdoc(dict(LAPLINE, b=at(0, 12))), 3)
        self.assertIsNone(self.lap(1))

    def test_relay_duplicate_does_not_double_count(self):
        self.fix(1, -5, 0)
        self.t += 1
        lat, lon = at(5, 0)
        self.f.ingest(tele(1, seq=9, lat=lat, lon=lon), self.t, "w")
        self.f.ingest(tele(1, seq=9, lat=lat, lon=lon, relayed=True), self.t + 0.02, "w")
        self.assertEqual(self.lap(1)["laps"], 0)


class BoostCommand(unittest.TestCase):
    def test_boost_arg_on_wire(self):
        h = Harness()
        h.cm.submit("boost", 2, 100, now=h.now)
        self.assertEqual(h.sent[-1]["cmd"], "boost")
        self.assertEqual(h.sent[-1]["arg"], 100)
        h.cm.submit("boost", 3, None, now=h.now)
        self.assertEqual(h.sent[-1]["arg"], 100)                # default full scale
        h.cm.submit("slow", 4, 7, now=h.now)
        self.assertEqual(h.sent[-1]["arg"], 7)
        for bad in (9, 101, "x"):
            with self.assertRaises(pc.CommandError):
                h.cm.submit("boost", 1, bad, now=h.now)


class GpsCorrectionTest(unittest.TestCase):
    def _base(self, f, now, lat, lon, sats=9):
        f.ingest({"t": "base", "lat": lat, "lon": lon, "fix": True, "sats": sats, "utc": None, "pps": False}, now, "w")

    def test_measured_applied_and_gated(self):
        marker = {"lat": 41.0940, "lon": -73.4165}
        f = pc.Fleet(pit_marker=marker, gps_correction=True)
        # base reads 3 m north and 4 m east of the surveyed spot, steadily
        dlat, dlon = 3.0 / pc.M_PER_DEG_LAT, 4.0 / (pc.M_PER_DEG_LAT * math.cos(math.radians(41.094)))
        f.ingest({"t": "tele", "kart": 1, "seq": 1, "lat": 41.0950, "lon": -73.4160, "fix": True, "gps_age_s": 0, "sats": 9}, 0.0, "w")
        for i in range(3):
            self._base(f, float(i), marker["lat"] + dlat, marker["lon"] + dlon)
        st = f.snapshot(3.0)
        self.assertFalse(st["gps_correction"]["applied"])          # fewer than 5 samples
        for i in range(3, 12):
            self._base(f, float(i), marker["lat"] + dlat, marker["lon"] + dlon)
        st = f.snapshot(12.0)
        gc = st["gps_correction"]
        self.assertTrue(gc["applied"]); self.assertAlmostEqual(gc["north_m"], 3.0, delta=0.2); self.assertAlmostEqual(gc["east_m"], 4.0, delta=0.2)
        k1 = st["karts"][0]["gps"]["last_fix"]
        self.assertAlmostEqual((k1["lat_raw"] - k1["lat"]) * pc.M_PER_DEG_LAT, 3.0, delta=0.2)
        self.assertAlmostEqual(k1["lon_raw"], -73.4160)
        # stale base -> not applied, raw positions again
        st = f.snapshot(40.0)
        self.assertFalse(st["gps_correction"]["applied"])
        self.assertNotIn("lat_raw", st["karts"][0]["gps"]["last_fix"])
        # few satellites or a 200 m indoor fix never move the estimate
        self._base(f, 41.0, marker["lat"] + 0.002, marker["lon"], sats=9)
        self._base(f, 42.0, marker["lat"] + dlat, marker["lon"] + dlon, sats=4)
        self.assertEqual(f.correction_status(42.0)["samples"], 12)
        f.set_correction(False)
        self.assertFalse(f.correction_status(42.0)["enabled"])

    def test_off_by_default_and_no_marker(self):
        f = pc.Fleet()
        self._base(f, 1.0, 41.0, -73.0)
        gc = f.snapshot(1.0)["gps_correction"]
        self.assertFalse(gc["enabled"]); self.assertFalse(gc["marker"]); self.assertEqual(gc["samples"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
