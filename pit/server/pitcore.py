"""Fleet state, derived values and command delivery for the GoKart pit station.

Pure logic: no sockets, no threads, no serial. Every function that cares about
time takes `now` (a time.monotonic() value) so tests can drive the clock.

Contract: firmware/base-tbeam-s3/SERIAL_PROTOCOL.md (base-0.1.0).
Conventions: docs/requirements.md "Agreed telemetry fields" -- missing values
are None (JSON null), never a fabricated zero; freshness is tracked per data
group (link, VESC, GPS) with local monotonic time.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import deque

KART_IDS = tuple(range(1, 9))
ALL_KARTS = 255

ONLINE_S = 5.0     # tele heard within this many seconds -> online (matches stat.karts_online)
M_PER_DEG_LAT = 111320.0
CORR_MAX_M = 25.0      # position-domain GPS correction is applied only below this magnitude
CORR_MIN_SATS = 6
CORR_MIN_SAMPLES = 5
CORR_MAX_AGE_S = 10.0
STALE_S = 15.0     # within this -> stale; beyond -> offline

# Fixed per-kart colours shared by every client (proposed; no owner decision yet).
KART_COLORS = {
    1: "#e6194b", 2: "#3cb44b", 3: "#ffe119", 4: "#4363d8",
    5: "#f58231", 6: "#911eb4", 7: "#42d4f4", 8: "#f032e6",
}

MODE_NAMES = {0: "ECO", 1: "NORMAL", 2: "SPORT"}
# What each mode writes (firmware/kart-heltec-v4/config.h MODES[]; firmware/tests/test_protocol.py checks
# these copies against it): motor-current scale of the controller's 140 A, and a top-speed cap in mph
# (0 = none, the controller's own ERPM bound). ECO 70 A / 15 mph, NORMAL 105 A / 25 mph, SPORT 140 A / uncapped.
MODE_SCALES = {0: 0.50, 1: 0.75, 2: 1.0}
MODE_TOP_MPH = {0: 15, 1: 25, 2: 0}
SCALE_MISMATCH_TOL = 0.05     # live scale this far from the requested mode's scale ...
SCALE_MISMATCH_S = 10.0       # ... for this long (not stopped, not on the grid, no boost) -> SCALE != MODE
CMD_RESULT_NAMES = {0: "received", 1: "accepted", 2: "applied", 3: "no_vesc",
                    4: "rejected", 5: "unknown"}

# VESC mc_fault_code names as enumerated in vedderb/bldc datatypes.h (6.x).
# Not checked against this fleet's controllers; the numeric code is authoritative.
VESC_FAULT_NAMES = {
    0: "NONE", 1: "OVER_VOLTAGE", 2: "UNDER_VOLTAGE", 3: "DRV", 4: "ABS_OVER_CURRENT",
    5: "OVER_TEMP_FET", 6: "OVER_TEMP_MOTOR", 7: "GATE_DRIVER_OVER_VOLTAGE",
    8: "GATE_DRIVER_UNDER_VOLTAGE", 9: "MCU_UNDER_VOLTAGE",
    10: "BOOTING_FROM_WATCHDOG_RESET", 11: "ENCODER_SPI",
    12: "ENCODER_SINCOS_BELOW_MIN_AMPLITUDE", 13: "ENCODER_SINCOS_ABOVE_MAX_AMPLITUDE",
    14: "FLASH_CORRUPTION", 15: "HIGH_OFFSET_CURRENT_SENSOR_1",
    16: "HIGH_OFFSET_CURRENT_SENSOR_2", 17: "HIGH_OFFSET_CURRENT_SENSOR_3",
    18: "UNBALANCED_CURRENTS", 19: "BRK", 20: "RESOLVER_LOT", 21: "RESOLVER_DOS",
    22: "RESOLVER_LOS", 23: "FLASH_CORRUPTION_APP_CFG", 24: "FLASH_CORRUPTION_MC_CFG",
    25: "ENCODER_NO_MAGNET", 26: "ENCODER_MAGNET_TOO_STRONG", 27: "PHASE_FILTER",
}

# ---------------------------------------------------------------------------
# Derived values (host side, per the contract)
# ---------------------------------------------------------------------------
PACK_EMPTY_V = 60.0      # 20S Li-ion, 3.00 V/cell
PACK_FULL_V = 84.0       # 20S Li-ion, 4.20 V/cell
UNDER_LOAD_A = 20.0      # |batt_a| above this: voltage sag makes % unreliable

POLE_PAIRS = 7           # 14 poles (2026-11-05 runtime capture, not physically verified)
GEAR_RATIO = 2.769
WHEEL_DIAMETER_M = 0.406
METERS_PER_MILE = 1609.34


def battery_percent(volts):
    """Linear 60.0 V = 0 % .. 84.0 V = 100 %, clamped. None in -> None out."""
    if volts is None:
        return None
    pct = (float(volts) - PACK_EMPTY_V) / (PACK_FULL_V - PACK_EMPTY_V) * 100.0
    return round(max(0.0, min(100.0, pct)), 1)


def under_load(batt_a):
    """True when battery current is high enough that voltage-based % is unreliable.
    None when the current is unknown (unknown is not 'light load')."""
    if batt_a is None:
        return None
    return abs(float(batt_a)) > UNDER_LOAD_A


def wheel_mph(erpm):
    """Contract formula. UNCALIBRATED: drivetrain numbers are configured values only.
    Sign is kept (negative = reverse rotation)."""
    if erpm is None:
        return None
    mph = float(erpm) / POLE_PAIRS / GEAR_RATIO * (WHEEL_DIAMETER_M * math.pi) * 60.0 / METERS_PER_MILE
    return round(mph, 2)


def erpm_for_mph(mph):
    """Inverse of wheel_mph (used by the simulator)."""
    return mph * METERS_PER_MILE / 60.0 / (WHEEL_DIAMETER_M * math.pi) * GEAR_RATIO * POLE_PAIRS


def kph_to_mph(kph):
    return None if kph is None else round(float(kph) * 0.621371, 2)


def link_status(last_mono, now):
    """'never' | 'online' | 'stale' | 'offline' from a last-heard monotonic time."""
    if last_mono is None:
        return "never"
    age = now - last_mono
    if age <= ONLINE_S:
        return "online"
    if age <= STALE_S:
        return "stale"
    return "offline"


def _age(last_mono, now):
    return None if last_mono is None else round(max(0.0, now - last_mono), 1)


# ---------------------------------------------------------------------------
# Fleet state
# ---------------------------------------------------------------------------
VESC_FIELDS = ("volts", "motor_a", "batt_a", "erpm", "duty", "esc_c", "fault", "ah", "scale")
STATUS_FIELDS = ("seq", "mode_req", "mode_actual", "mode_confirmed", "stopped", "slow_mph", "vesc_link",
                 "pps", "wifi", "writes", "last_cmd_seq", "cmd_result", "uptime_s",
                 "base_rssi", "rssi", "snr", "rx_ms")
TRAIL_POINTS = 30
LATE_WINDOW_S = 3.0      # a packet up to LATE_SEQS behind the last one, within this time, is a late copy
LATE_SEQS = 16


class KartState:
    def __init__(self, kart_id):
        self.id = kart_id
        self.color = KART_COLORS[kart_id]
        self.last_tele_mono = None      # any tele line (radio link evidence)
        self.last_tele_wall = None
        self.status = {}                # STATUS_FIELDS from the latest tele
        self.last_vesc_mono = None      # latest tele with vesc_link true
        self.vesc = {}                  # VESC_FIELDS from that sample (nulls preserved)
        self.last_fix_mono = None       # latest tele with a valid fix
        self.gps_age_at_rx = None       # kart-reported fix age at that receipt
        self.fix = {}                   # lat, lon, heading, gps_kph, sats
        self.gps_now = {}               # fix/sats/gps_age_s from the latest tele, valid or not
        self.trail = deque(maxlen=TRAIL_POINTS)
        self.packets = 0
        self.seq_gaps = 0               # tele packets missed, from the kart's own seq counter
        self.rx_count = 0               # tele packets received
        self.recent = deque(maxlen=600)   # (mono, received, gap) for a rolling loss figure
        self.via_repeater = False       # last counted packet came through the LoRa repeater
        self.relay_only = 0             # packets that only arrived via the repeater (direct copy lost)
        self.relay_dupes = 0            # repeater copies dropped because the direct copy was already counted
        self.late_dupes = 0             # other repeats / out-of-order packets dropped (would move the kart back)
        self.reboots = 0                # kart uptime went backwards (kart firmware restarted)
        self.mismatch_since = None      # mono when the live scale started to differ from the mode's scale
        self.mismatch_reported = False  # the "start" event has been logged

    def is_late_copy(self, msg, now):
        """True for a packet the pit already has or that is older than the newest one: the same seq
        again or up to LATE_SEQS behind it, within LATE_WINDOW_S of the last packet, from the same
        boot (uptime not going backwards). Relayed copies of a packet heard directly arrive ~25 ms
        later; a repeater queue can deliver one after the next direct packet. Applying either would
        move the kart back along its path (and, for laps, across the line twice)."""
        seq, prev = msg.get("seq"), self.status.get("seq")
        if not isinstance(seq, int) or not isinstance(prev, int) or self.last_tele_mono is None:
            return False
        if now - self.last_tele_mono >= LATE_WINDOW_S:
            return False
        up, prev_up = msg.get("uptime_s"), self.status.get("uptime_s")
        if isinstance(up, (int, float)) and isinstance(prev_up, (int, float)) and up + 2 < prev_up:
            return False                # rebooted: a fresh seq counter, not a late copy
        return seq == prev or 1 <= (prev - seq) % 256 <= LATE_SEQS

    def ingest_tele(self, msg, now, wall):
        seq = msg.get("seq")
        prev = self.status.get("seq")
        relayed = msg.get("relayed") is True
        if self.is_late_copy(msg, now):
            if relayed:
                self.relay_dupes += 1   # repeater copy of a packet the pit heard directly
            else:
                self.late_dupes += 1
            return
        up, prev_up = msg.get("uptime_s"), self.status.get("uptime_s")
        if isinstance(up, (int, float)) and isinstance(prev_up, (int, float)) and up + 2 < prev_up:
            self.reboots += 1
        if relayed:
            self.relay_only += 1
        self.via_repeater = relayed
        self.rx_count += 1
        gap_now = 0
        if isinstance(seq, int) and isinstance(prev, int):
            gap = (seq - prev - 1) % 256
            if 0 < gap < 128:
                gap_now = gap
            if 0 < gap < 128:           # ignore restarts/reorders
                self.seq_gaps += gap
        self.recent.append((now, 1, gap_now))
        self.packets += 1
        self.last_tele_mono = now
        self.last_tele_wall = wall
        self.status = {f: msg.get(f) for f in STATUS_FIELDS}
        if msg.get("vesc_link") is True:
            self.vesc = {f: msg.get(f) for f in VESC_FIELDS}
            self.last_vesc_mono = now
        self.gps_now = {"fix": msg.get("fix"), "sats": msg.get("sats"),
                        "gps_age_s": msg.get("gps_age_s")}
        lat, lon = msg.get("lat"), msg.get("lon")
        if msg.get("fix") is True and lat is not None and lon is not None:
            self.fix = {"lat": lat, "lon": lon, "heading": msg.get("heading"),
                        "gps_kph": msg.get("gps_kph"), "sats": msg.get("sats")}
            age = msg.get("gps_age_s")
            self.gps_age_at_rx = None if age in (None, 255) else float(age)
            self.last_fix_mono = now
            if not self.trail or (self.trail[-1][1], self.trail[-1][2]) != (lat, lon):
                self.trail.append((now, round(lat, 6), round(lon, 6)))     # 0.1 m: plenty for a trail

    def _mismatch(self, now):
        if self.mismatch_since is None:
            return None
        if now - self.mismatch_since < SCALE_MISMATCH_S:
            return None                 # (the kart's own revert_missed flag is USB-console only: the
                                        #  47-byte telemetry has no spare flag bit for it)
        mr = self.status.get("mode_req")
        return {"since_s": round(now - self.mismatch_since, 1), "scale": self.vesc.get("scale"),
                "want": round(MODE_SCALES[mr], 3) if mr in MODE_SCALES else None, "mode": mr}

    def scale_off(self, now, boosted, on_grid):
        """True when this kart's live scale disagrees with its requested mode right now (and nothing
        explains it: not stopped, not on the race grid, no boost from the pit in the last few seconds)."""
        st, mr, sc = self.status, self.status.get("mode_req"), self.vesc.get("scale")
        if link_status(self.last_tele_mono, now) != "online" or self.last_vesc_mono is None \
                or now - self.last_vesc_mono > ONLINE_S:
            return False
        if mr not in MODE_SCALES or not isinstance(sc, (int, float)) or st.get("stopped") is not False:
            return False
        if boosted or on_grid:
            return False
        return abs(sc - MODE_SCALES[mr]) > SCALE_MISMATCH_TOL

    def _trail(self, now, max_age_s=30.0, max_gap_s=3.0):
        """Recent path for the map: the last max_age_s, starting after the newest gap longer than
        max_gap_s (a kart that dropped out for 8 s would otherwise get a straight line across the course)."""
        out, last_t = [], None
        for t, lat, lon in self.trail:
            if now - t > max_age_s:
                continue
            if last_t is not None and t - last_t > max_gap_s:
                out = []
            out.append([lat, lon])
            last_t = t
        return out

    def _loss_pct(self, now, window_s):
        rx = lost = 0
        for t, r, g in self.recent:
            if now - t <= window_s: rx += r; lost += g
        return round(100.0 * lost / (rx + lost), 1) if (rx + lost) else None

    def snapshot(self, now):
        link = link_status(self.last_tele_mono, now)
        vesc_age = _age(self.last_vesc_mono, now)
        fix_age = None
        if self.last_fix_mono is not None:
            fix_age = round(now - self.last_fix_mono + (self.gps_age_at_rx or 0.0), 1)
        volts = self.vesc.get("volts")
        batt_a = self.vesc.get("batt_a")
        fault = self.vesc.get("fault")
        mr, ma = self.status.get("mode_req"), self.status.get("mode_actual")
        return {
            "id": self.id,
            "color": self.color,
            "link": link,                                   # radio link from tele arrival
            "age_s": _age(self.last_tele_mono, now),
            "last_seen_wall": self.last_tele_wall,
            "packets": self.packets,
            "seq_gaps": self.seq_gaps,
            "rx_count": self.rx_count,
            "loss_pct_1m": self._loss_pct(now, 60.0),
            "via_repeater": self.via_repeater,
            "relay_only": self.relay_only,
            "relay_dupes": self.relay_dupes,
            "late_dupes": self.late_dupes,
            "reboots": self.reboots,
            "scale_mismatch": self._mismatch(now),
            "status": dict(self.status),
            "mode_req_name": MODE_NAMES.get(mr) if mr is not None else None,
            "mode_actual_name": MODE_NAMES.get(ma) if ma is not None else None,
            # VESC group: values are from the last sample with vesc_link true, with its own age.
            "vesc": dict(self.vesc),
            "vesc_age_s": vesc_age,
            "vesc_fresh": vesc_age is not None and vesc_age <= ONLINE_S,
            "fault_name": VESC_FAULT_NAMES.get(fault, "UNKNOWN_CODE") if fault is not None else None,
            "derived": {
                "battery_pct": battery_percent(volts),
                "under_load": under_load(batt_a),
                "wheel_mph": wheel_mph(self.vesc.get("erpm")),
                "wheel_mph_calibrated": False,
                "gps_mph": kph_to_mph(self.fix.get("gps_kph")),
            },
            "gps": {
                "fix_now": self.gps_now.get("fix"),
                "sats_now": self.gps_now.get("sats"),
                "last_fix": dict(self.fix) if self.fix else None,
                "fix_age_s": fix_age,
                "fix_fresh": fix_age is not None and fix_age <= ONLINE_S,
            },
            "trail": self._trail(now),
        }


class Fleet:
    """Authoritative fleet + race state. Callers hold the app lock."""

    def __init__(self, fallback_origin=None, pit_marker=None, gps_correction=False):
        # Position-domain differential GPS: the pit radio sits at a surveyed spot (map.json "pit"); its live
        # fix minus that spot is mostly the common-mode error the karts see too (same sky, same minute).
        # Off unless configured; the measured vector is always exposed so it can be judged first.
        self.pit_marker = pit_marker if pit_marker and pit_marker.get("lat") is not None else None
        self.corr_enabled = bool(gps_correction)
        self.corr = {"n": 0.0, "e": 0.0, "samples": 0}
        self.corr_mono = None
        self.karts = {k: KartState(k) for k in KART_IDS}
        self.base = {}
        self.base_mono = None
        self.repeater = {}
        self.repeater_mono = None
        self.stat = {}
        self.stat_mono = None
        self.simulation = False
        self.race = {"active": False, "mode": 1, "entered": [], "changed_wall": None, "by": None}
        self.origin = None                      # {"lat","lon","source"}; locked per session
        self.fallback_origin = fallback_origin  # from map.json / config
        self.rx_lines = 0
        self.rx_bad = 0
        self.game = Game()
        self.recorder = None                    # TrackRecorder (pace car), set by the server

    # -- ingest ------------------------------------------------------------
    def ingest(self, msg, now, wall):
        """Apply one parsed contract line. Returns the kart id for tele lines, else None."""
        t = msg.get("t")
        if t == "tele":
            k = msg.get("kart")
            if k not in self.karts:
                self.rx_bad += 1
                return None
            ks = self.karts[k]
            prev = (ks.fix.get("lat"), ks.fix.get("lon"), ks.last_fix_mono) if ks.fix else None
            ks.ingest_tele(msg, now, wall)
            if self.game.session is not None and self.game.session.running:
                self.game.session.seen.add(k)
            if self.recorder is not None and ks.fix and ks.last_fix_mono == now:
                self.recorder.on_fix(k, ks.fix.get("lat"), ks.fix.get("lon"), msg.get("gps_kph"),
                                     msg.get("sats"), msg.get("fix"), now)
            if prev is not None and ks.last_fix_mono == now:
                cur = (ks.fix["lat"], ks.fix["lon"])
                if cur != prev[:2]:
                    self.game.on_move(k, prev, cur, now, wall)
            self._maybe_origin("kart", msg.get("lat"), msg.get("lon"), msg.get("fix"))
            return k
        if t == "base":
            self.base = dict(msg)
            self.base_mono = now
            self._update_correction(msg, now)
            self._maybe_origin("base", msg.get("lat"), msg.get("lon"), msg.get("fix"))
        elif t == "repeater":
            self.repeater = dict(msg)
            self.repeater_mono = now
        elif t == "stat":
            self.stat = dict(msg)
            self.stat_mono = now
            if str(msg.get("fw") or "").startswith("sim-"):
                self.simulation = True
        elif t == "log":
            if msg.get("msg") == "SIMULATION":
                self.simulation = True
        return None

    _RANK = {"config": 0, "kart": 1, "base": 2}

    def _maybe_origin(self, source, lat, lon, fix):
        """Origin priority: base GPS > first kart fix > configured. Upgrades once per
        source; never moves within a source, so the map does not jitter."""
        if fix is not True or lat is None or lon is None:
            return
        cur = self.origin
        if cur is None or self._RANK[source] > self._RANK[cur["source"]]:
            self.origin = {"lat": lat, "lon": lon, "source": source}

    def _update_correction(self, msg, now):
        if not self.pit_marker or msg.get("fix") is not True:
            return
        lat, lon = msg.get("lat"), msg.get("lon")
        if lat is None or lon is None or (msg.get("sats") or 0) < CORR_MIN_SATS:
            return
        mlat, mlon = self.pit_marker["lat"], self.pit_marker["lon"]
        err_n = (lat - mlat) * M_PER_DEG_LAT
        err_e = (lon - mlon) * M_PER_DEG_LAT * math.cos(math.radians(mlat))
        if math.hypot(err_n, err_e) > 60.0:          # not a GPS error, that is a wrong marker or an indoor fix
            return
        if self.corr_mono is None or now - self.corr_mono > 60.0:
            self.corr = {"n": err_n, "e": err_e, "samples": 1}
        else:
            a = 0.05                                  # ~20 s window at the 1 Hz base line
            self.corr = {"n": self.corr["n"] * (1 - a) + err_n * a,
                         "e": self.corr["e"] * (1 - a) + err_e * a,
                         "samples": self.corr["samples"] + 1}
        self.corr_mono = now

    def set_correction(self, enabled):
        self.corr_enabled = bool(enabled)

    def correction_status(self, now):
        age = _age(self.corr_mono, now)
        mag = math.hypot(self.corr["n"], self.corr["e"])
        fresh = age is not None and age <= CORR_MAX_AGE_S
        applied = self.corr_enabled and fresh and mag < CORR_MAX_M and self.corr["samples"] >= CORR_MIN_SAMPLES
        return {"enabled": self.corr_enabled, "applied": applied, "north_m": round(self.corr["n"], 1),
                "east_m": round(self.corr["e"], 1), "magnitude_m": round(mag, 1), "age_s": age,
                "samples": self.corr["samples"], "marker": bool(self.pit_marker)}

    def _apply_correction(self, snap):
        n, e = self.corr["n"], self.corr["e"]
        def shift(lat, lon):
            return (lat - n / M_PER_DEG_LAT, lon - e / (M_PER_DEG_LAT * math.cos(math.radians(lat))))
        lf = snap["gps"].get("last_fix")
        if lf and lf.get("lat") is not None:
            lf["lat_raw"], lf["lon_raw"] = lf["lat"], lf["lon"]
            lf["lat"], lf["lon"] = shift(lf["lat"], lf["lon"])
        snap["trail"] = [list(shift(p[0], p[1])) for p in snap["trail"]]

    def current_origin(self):
        if self.origin:
            return self.origin
        if self.fallback_origin:
            return dict(self.fallback_origin, source="config")
        return None

    def is_online(self, k, now):
        return link_status(self.karts[k].last_tele_mono, now) == "online"

    def watch_scales(self, now, boosted=(), grid=False):
        """Update each kart's SCALE != MODE timer. boosted: karts the pit boosted in the last few
        seconds; grid: a race start is holding karts. Returns [(kart, "start"|"end", info)] transitions
        (the start is reported once the mismatch has lasted SCALE_MISMATCH_S)."""
        out = []
        for k, ks in self.karts.items():
            off = ks.scale_off(now, k in boosted, grid)
            if off and ks.mismatch_since is None:
                ks.mismatch_since = now
            elif not off and ks.mismatch_since is not None:
                ks.mismatch_since = None
                if ks.mismatch_reported:
                    ks.mismatch_reported = False
                    out.append((k, "end", None))
                continue
            if off and not ks.mismatch_reported and ks._mismatch(now) is not None:
                ks.mismatch_reported = True
                out.append((k, "start", ks._mismatch(now)))
        return out

    def snapshot(self, now):
        base_age = _age(self.base_mono, now)
        laps = self.game.board(now)
        by_kart = {r["kart"]: r for r in laps}
        karts = []
        corr = self.correction_status(now)
        for k in KART_IDS:
            snap = self.karts[k].snapshot(now)
            snap["lap"] = by_kart.get(k)
            if corr["applied"]:
                self._apply_correction(snap)
            karts.append(snap)
        return {
            "karts": karts,
            "gps_correction": corr,
            "base": dict(self.base, age_s=base_age) if self.base else None,
            "stat": dict(self.stat, age_s=_age(self.stat_mono, now)) if self.stat else None,
            "repeater": dict(self.repeater, age_s=_age(self.repeater_mono, now)) if self.repeater else None,
            "simulation": self.simulation,
            "race": dict(self.race, session=None if self.game.session is None else self.game.session.snapshot()),
            "game": self.game.status(),
            "laps": laps,
            "origin": self.current_origin(),
            "thresholds": {"online_s": ONLINE_S, "stale_s": STALE_S},
        }


# ---------------------------------------------------------------------------
# Command delivery
# ---------------------------------------------------------------------------
RETRY_INTERVAL_S = 1.5
MAX_RETRANSMITS = 5          # contract: "retransmit every 1.5 s, up to 5 times" -> 6 sends max
DEFAULT_TTL_S = 10
ACCEPT_HOLD_S = 4.0       # queued commands wait this long for an "accepted" kart to report final
HISTORY = 40

# Per-kart outcome states.
#   queued      waiting behind another in-flight command for this kart
#   pending     transmitted, kart was online, no echo yet
#   received/accepted   kart echoed the seq; result not final yet (keeps updating)
#   applied/no_vesc/rejected/unknown   kart's cmd_result for this seq
#   no_ack      retries exhausted (or kart went silent) without an echo: outcome unknown
#   offline     kart was not online when the command went out (it may still echo later)
#   superseded  a newer command to this kart went out before this one finished
#   cancelled   dropped from the queue by a STOP before it was ever sent
#   link_lost   serial link to the T-Beam dropped; NOT retried after reconnect
ECHOED = {"received", "accepted", "applied", "no_vesc", "rejected", "unknown"}
FINAL = {"applied", "no_vesc", "rejected", "unknown", "no_ack", "offline",
         "superseded", "cancelled", "link_lost"}
VALID_CMDS = {"mode", "stop", "start", "ping", "slow", "boost", "race", "rate"}
BOOST_MIN_PCT, BOOST_MAX_PCT = 10, 100   # firmware GK_CMD_BOOST: scale percent, lasts 5 s


class CommandError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Game zones (Mario Kart layer) and lap timing. Contract: map/ZONES.md
# ---------------------------------------------------------------------------
R_EARTH = 6371000.0
ZONE_TYPES = ("boost", "mud", "item", "lapline")
ZONE_SHAPES = {"boost": ("circle", "polygon"), "mud": ("circle", "polygon"), "item": ("circle", "polygon"),
               "lapline": ("line",)}
ZONE_DEFAULT_NAMES = {"boost": "Boost pad", "mud": "Mud", "item": "Item box", "lapline": "Start / finish"}
MIN_R_M, MAX_R_M = 3.0, 500.0     # karts fix at 5 Hz; 5 m is a good pad outdoors
MAX_ZONES = 64
MAX_POLY_POINTS = 64
MIN_LINE_M = 4.0
DEFAULT_MIN_LAP_S = 20.0
MAX_MOVE_MPS = 40.0       # a fix-to-fix move faster than this (90 mph) is a GPS jump: it never counts a lap
EMPTY_ZONES = {"version": 0, "enabled": False, "zones": []}


class ZoneError(CommandError):
    pass


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _num(z, key, default=None, what=None):
    v = z.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ZoneError(f"zone {z.get('id')!r}: {what or key} must be a number")
    return float(v)


def _point(p, zid, what):
    if not isinstance(p, (list, tuple)) or len(p) != 2 or any(
            isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) for c in p):
        raise ZoneError(f"zone {zid!r}: {what} must be [lat, lon]")
    lat, lon = float(p[0]), float(p[1])
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ZoneError(f"zone {zid!r}: {what} is not a valid lat/lon")
    return [round(lat, 7), round(lon, 7)]


def local_en(lat0, lon0):
    """Tangent-plane projection (metres east, north) around lat0/lon0; cos(lat) scaling for lon."""
    cl = math.cos(math.radians(lat0))
    return lambda lat, lon: (math.radians(lon - lon0) * R_EARTH * cl, math.radians(lat - lat0) * R_EARTH)


def validate_zones(doc):
    """Check a zones document against ZONES.md and return the normalized
    {"enabled", "zones"} (version is assigned by the caller). Raises ZoneError.
    Out-of-range scale/secs/cooldown/min_lap_s are clamped; geometry that is too
    small, missing or malformed is refused."""
    if not isinstance(doc, dict):
        raise ZoneError("zones document must be a JSON object")
    zones = doc.get("zones")
    if not isinstance(zones, list):
        raise ZoneError("zones must be a list")
    if len(zones) > MAX_ZONES:
        raise ZoneError(f"at most {MAX_ZONES} zones")
    out, ids, laplines = [], set(), 0
    for i, z in enumerate(zones):
        if not isinstance(z, dict):
            raise ZoneError(f"zone #{i + 1} must be an object")
        zid = z.get("id")
        if not isinstance(zid, str) or not zid.strip() or len(zid) > 16 \
                or not all(c.isalnum() or c in "-_" for c in zid):
            raise ZoneError(f"zone #{i + 1}: id must be 1-16 letters, digits, - or _")
        if zid in ids:
            raise ZoneError(f"duplicate zone id {zid!r}")
        ids.add(zid)
        typ = z.get("type")
        if isinstance(typ, str) and typ and typ not in ZONE_TYPES and len(json.dumps(z)) <= 4096:
            out.append(dict(z))      # newer zone kind: kept as-is, ignored by timing, drawn greyed (ZONES.md)
            continue
        if typ not in ZONE_TYPES:
            raise ZoneError(f"zone {zid!r}: type must be boost, mud, item or lapline")
        shape = z.get("shape", ZONE_SHAPES[typ][0])
        if shape not in ZONE_SHAPES[typ]:
            raise ZoneError(f"zone {zid!r}: a {typ} zone must be a {' or '.join(ZONE_SHAPES[typ])}")
        name = z.get("name")
        name = (name.strip()[:40] if isinstance(name, str) else "") or ZONE_DEFAULT_NAMES[typ]
        n = {"id": zid, "type": typ, "name": name, "shape": shape}
        if shape == "circle":
            lat, lon = _point([z.get("lat"), z.get("lon")], zid, "centre")
            r = _num(z, "r_m", what="r_m")
            if r < MIN_R_M:
                raise ZoneError(f"zone {zid!r}: radius {r:g} m is below the {MIN_R_M:g} m minimum")
            if r > MAX_R_M:
                raise ZoneError(f"zone {zid!r}: radius {r:g} m is above {MAX_R_M:g} m")
            n.update(lat=lat, lon=lon, r_m=round(r, 1))
        elif shape == "polygon":
            pts = z.get("points")
            if not isinstance(pts, list) or len(pts) < 3:
                raise ZoneError(f"zone {zid!r}: a polygon needs at least 3 points")
            if len(pts) > MAX_POLY_POINTS:
                raise ZoneError(f"zone {zid!r}: at most {MAX_POLY_POINTS} polygon points")
            n["points"] = [_point(p, zid, f"point {j + 1}") for j, p in enumerate(pts)]
        else:
            a, b = _point(z.get("a"), zid, "a"), _point(z.get("b"), zid, "b")
            en = local_en(a[0], a[1])
            if math.hypot(*en(*b)) < MIN_LINE_M:
                raise ZoneError(f"zone {zid!r}: lap line must be at least {MIN_LINE_M:g} m long")
            n.update(a=a, b=b)
        if typ == "boost":
            n["scale"] = round(_clamp(_num(z, "scale", 1.0), 0.1, 1.0), 2)
            n["secs"] = int(round(_clamp(_num(z, "secs", 5), 1, 30)))
            n["cooldown_s"] = int(round(_clamp(_num(z, "cooldown_s", 20), 0, 600)))
        elif typ == "item":                  # outcome is rolled on the kart; the pit only stores/draws it
            n["cooldown_s"] = int(round(_clamp(_num(z, "cooldown_s", 15), 0, 600)))
        elif typ == "mud":
            n["scale"] = round(_clamp(_num(z, "scale", 0.5), 0.1, 1.0), 2)
        else:
            laplines += 1
            n["min_lap_s"] = int(round(_clamp(_num(z, "min_lap_s", DEFAULT_MIN_LAP_S), 5, 600)))
        out.append(n)
    if laplines > 1:
        raise ZoneError("only one lap line is allowed")
    enabled = doc.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ZoneError("enabled must be true or false")
    res = {"enabled": enabled, "zones": out}
    track = validate_track(doc.get("track"))
    if track is not None:
        res["track"] = track
    return res


TRACK_MIN_POINTS, TRACK_MAX_POINTS = 2, 200
TRACK_WIDTH_M = (2.0, 20.0)


def validate_track(t):
    """Optional course centreline (ZONES.md "Track route"). None/absent = no track. No game effect."""
    if t is None:
        return None
    if not isinstance(t, dict):
        raise ZoneError("track must be an object")
    pts = t.get("points")
    if not isinstance(pts, list) or not TRACK_MIN_POINTS <= len(pts) <= TRACK_MAX_POINTS:
        raise ZoneError(f"track needs {TRACK_MIN_POINTS}-{TRACK_MAX_POINTS} points")
    pts = [_point(p, "track", f"point {i + 1}") for i, p in enumerate(pts)]
    closed = t.get("closed", False)
    if not isinstance(closed, bool):
        raise ZoneError("track closed must be true or false")
    w = t.get("width_m", 6)
    if isinstance(w, bool) or not isinstance(w, (int, float)) or not math.isfinite(w) \
            or not TRACK_WIDTH_M[0] <= w <= TRACK_WIDTH_M[1]:
        raise ZoneError("track width_m must be 2-20")
    if closed and len(pts) < 3:
        raise ZoneError("a closed track needs at least 3 points")
    return {"points": pts, "closed": closed, "width_m": round(float(w), 1)}


def segment_cross(p1, p2, q1, q2):
    """Fraction t along p1->p2 where it crosses segment q1-q2, or None. 2D points."""
    rx, ry = p2[0] - p1[0], p2[1] - p1[1]
    sx, sy = q2[0] - q1[0], q2[1] - q1[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-12:
        return None                                  # parallel / degenerate
    qpx, qpy = q1[0] - p1[0], q1[1] - p1[1]
    t = (qpx * sy - qpy * sx) / den
    u = (qpx * ry - qpy * rx) / den
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return t
    return None


class LapState:
    def __init__(self):
        self.laps = 0               # completed laps
        self.started_mono = None    # last crossing (start of the running lap)
        self.started_wall = None
        self.last_s = None
        self.best_s = None
        self.crossings = 0

    def to_dict(self, kart, now):
        return {"kart": kart, "laps": self.laps,
                "last_s": None if self.last_s is None else round(self.last_s, 2),
                "best_s": None if self.best_s is None else round(self.best_s, 2),
                "current_s": None if self.started_mono is None else round(max(0.0, now - self.started_mono), 1),
                "last_cross_wall": self.started_wall, "running": self.started_mono is not None}


RACE_FINISH_BASE = 100        # race command arg 100 + position = finish with that position; arg 0 = finish, no position


def race_arg_name(arg):
    if arg == 0:
        return "FINISH"
    if arg is not None and arg > RACE_FINISH_BASE:
        return f"FINISH P{arg - RACE_FINISH_BASE}"
    return f"GO in {arg or 5} s"


class RaceSession:
    """One race from START RACE to its end (ZONES.md "Race finish"): optional length in laps,
    finishing order, final results. Ends when every online kart has finished the set laps, or on STOP RACE."""

    def __init__(self, laps_target, go_mono, go_wall):
        self.laps_target = int(laps_target or 0)     # 0 = untimed (ends only on STOP RACE)
        self.go_mono, self.go_wall = go_mono, go_wall
        self.finished = {}                           # kart -> {"pos", "total_s", "laps"}
        self.seen = set()                            # karts heard during the race
        self.pending = []                            # (kart, arg) finish commands to send
        self.ended_by = None                         # None while running, "laps" | "stop"
        self.finished_wall = None
        self.results = None

    @property
    def running(self):
        return self.ended_by is None

    def finish(self, kart, at, laps):
        pos = len(self.finished) + 1
        self.finished[kart] = {"pos": pos, "total_s": round(at - self.go_mono, 2), "laps": laps}
        self.pending.append((kart, RACE_FINISH_BASE + pos))
        return pos

    def snapshot(self):
        return {"state": "running" if self.running else "finished", "laps_target": self.laps_target,
                "go_wall": self.go_wall, "finished": {str(k): v["pos"] for k, v in self.finished.items()},
                "ended_by": self.ended_by, "finished_wall": self.finished_wall, "results": self.results}


class Game:
    """Zones document + per-kart lap timing from telemetry fixes."""

    def __init__(self):
        self.doc = dict(EMPTY_ZONES)
        self.laps = {}                       # kart -> LapState
        self.session = None                  # RaceSession from START RACE until NEW RACE / reset
        self._line = None                    # (a_en, b_en, project, min_lap_s) or None
        self.line_moves_in_race = 0          # lap line edited while a race session existed (laps kept)

    def set_doc(self, doc):
        """Install a validated document ({"version","enabled","zones"}). Lap state is
        cleared when the lap line geometry or min_lap_s changes."""
        old = self._lapline()
        self.doc = {"version": int(doc.get("version", 0)), "enabled": bool(doc.get("enabled")),
                    "zones": list(doc.get("zones") or [])}
        if doc.get("track"):
            self.doc["track"] = doc["track"]
        new = self._lapline()
        key = lambda z: None if z is None else (tuple(z["a"]), tuple(z["b"]), z.get("min_lap_s"))  # noqa: E731
        if key(old) != key(new):
            if self.session is None:
                self.laps = {}               # practice timing restarts against the new line
            else:
                # A race is on (or its results are showing): completed laps stay. The next crossing of
                # the new line counts against the previous crossing of the old one (min_lap_s applies).
                self.line_moves_in_race += 1
        if new is None:
            self._line = None
        else:
            proj = local_en(new["a"][0], new["a"][1])
            self._line = (proj(*new["a"]), proj(*new["b"]), proj,
                          float(new.get("min_lap_s", DEFAULT_MIN_LAP_S)))

    def _lapline(self):
        return next((z for z in self.doc["zones"] if z.get("type") == "lapline" and z.get("shape") == "line"), None)

    @property
    def active(self):
        return self.doc["enabled"] and self._line is not None

    def reset_laps(self):
        """Clear lap timing and any race session (RESET LAPS / NEW RACE)."""
        self.laps = {}
        self.session = None

    # -- race session ---------------------------------------------------------
    def race_begin(self, laps_target, go_mono, go_wall=None):
        self.reset_laps()
        self.session = RaceSession(laps_target, go_mono, go_wall)
        return self.session

    @property
    def frozen(self):
        """Final results are showing: no more lap counting until the next start / reset."""
        return self.session is not None and not self.session.running

    def take_finishes(self):
        """Finish commands waiting to be sent: [(kart, arg)]."""
        if self.session is None:
            return []
        out, self.session.pending = self.session.pending, []
        return out

    def _end(self, by, wall):
        ses = self.session
        ses.ended_by, ses.finished_wall = by, wall
        rows = []
        for k, f in sorted(ses.finished.items(), key=lambda kv: kv[1]["pos"]):
            st = self.laps.get(k)
            rows.append({"pos": f["pos"], "kart": k, "laps": f["laps"], "total_s": f["total_s"],
                         "best_s": None if st is None or st.best_s is None else round(st.best_s, 2)})
        ses.results = rows

    def check_end(self, online, wall=None):
        """Laps race: ends once every online kart has finished. Returns True when it ended now."""
        ses = self.session
        if ses is None or not ses.running or not ses.laps_target or not ses.finished:
            return False
        if all(k in ses.finished for k in online):
            self._end("laps", wall)
            return True
        return False

    def race_stop(self, online, wall=None, all_karts=range(1, 9)):
        """STOP RACE: freeze laps, place every kart not finished yet (laps desc, then earliest last
        crossing; karts with no completed lap last, by id) and return the finish commands
        [(kart, arg)]: 100 + position for karts in the race, 0 for the rest."""
        ses = self.session
        if ses is None:
            ses = self.session = RaceSession(0, None, None)
        if not ses.running:
            return []
        field = (set(online) | ses.seen | set(self.laps)) - set(ses.finished)
        done = [k for k in field if k in self.laps and self.laps[k].laps > 0]
        done.sort(key=lambda k: (-self.laps[k].laps, self.laps[k].started_mono, k))
        rest = sorted(k for k in field if k not in done)
        for k in done + rest:
            st = self.laps.get(k)
            at = st.started_mono if st is not None and st.laps > 0 else None
            pos = len(ses.finished) + 1
            ses.finished[k] = {"pos": pos, "laps": st.laps if st else 0,
                               "total_s": None if at is None or ses.go_mono is None else round(at - ses.go_mono, 2)}
            ses.pending.append((k, RACE_FINISH_BASE + pos))
        for k in all_karts:
            if k not in ses.finished:
                ses.pending.append((k, 0))
        self._end("stop", wall)
        return self.take_finishes()

    def on_move(self, kart, prev, cur, now, wall=None):
        """prev = (lat, lon, mono_of_that_fix), cur = (lat, lon) received at `now`.
        Returns True when the move crossed the lap line and counted."""
        if not self.active or self.frozen:
            return False
        ses = self.session
        if ses is not None and kart in ses.finished:
            return False                     # this kart has finished: its laps stop counting
        if ses is not None and ses.go_mono is not None and now < ses.go_mono:
            return False                     # grid hold: nothing counts before GO (karts may still roll over the line)
        a, b, proj, min_lap = self._line
        p0, p1 = proj(prev[0], prev[1]), proj(cur[0], cur[1])
        t = segment_cross(p0, p1, a, b)
        if t is None:
            return False
        pm = prev[2]
        if pm is not None and math.hypot(p1[0] - p0[0], p1[1] - p0[1]) > MAX_MOVE_MPS * max(0.2, now - pm):
            return False                     # GPS jump (cold start after a reboot, multipath): not a crossing
        at = pm + t * (now - pm) if pm is not None and 0 <= now - pm <= 5.0 else now
        st = self.laps.setdefault(kart, LapState())
        if st.started_mono is not None:
            lap = at - st.started_mono
            if lap < min_lap:
                return False
            st.laps += 1
            st.last_s = lap
            st.best_s = lap if st.best_s is None else min(st.best_s, lap)
        st.started_mono, st.started_wall = at, wall
        st.crossings += 1
        if ses is not None and ses.laps_target and st.laps >= ses.laps_target:
            ses.finish(kart, at, st.laps)    # reached the race length: finished with its position now
        return True

    def race_positions(self, now, n_karts=8):
        """(state, [p1..p8]) for the T-Beam `{"t":"race"}` line (SERIAL_PROTOCOL.md "Race positions").
        state: 0 idle (no race session), 1 countdown (before GO), 2 running, 3 finished.
        Positions: finished karts keep their finishing position; the rest follow by completed laps
        desc, then earliest last crossing (as board()); karts with no completed lap get 0."""
        ses = self.session
        pos = [0] * n_karts
        if ses is None:
            return 0, pos
        if not ses.running:
            for k, f in ses.finished.items():
                if 1 <= k <= n_karts:
                    pos[k - 1] = f["pos"]
            return 3, pos
        if ses.go_mono is not None and now < ses.go_mono:
            return 1, pos
        order = sorted(ses.finished, key=lambda k: ses.finished[k]["pos"])
        rest = [k for k, st in self.laps.items()
                if k not in ses.finished and st.laps > 0 and st.started_mono is not None]
        rest.sort(key=lambda k: (-self.laps[k].laps, self.laps[k].started_mono, k))
        for i, k in enumerate(order + rest, 1):
            if 1 <= k <= n_karts:
                pos[k - 1] = i
        return 2, pos

    def board(self, now):
        """Ranked laps: completed laps desc, then earlier last crossing first."""
        if not self.active:
            return []
        rows = [(k, st) for k, st in self.laps.items() if st.started_mono is not None]
        rows.sort(key=lambda r: (-r[1].laps, r[1].started_mono, r[0]))
        out = []
        lead = rows[0][1] if rows else None
        for pos, (k, st) in enumerate(rows, 1):
            d = st.to_dict(k, now)
            d["pos"] = pos
            # gap to the leader: whole laps behind, or (same laps) seconds behind at the line
            d["gap_laps"] = lead.laps - st.laps
            d["gap_s"] = (round(st.started_mono - lead.started_mono, 2)
                          if pos > 1 and st.laps == lead.laps else None)
            out.append(d)
        return out

    def status(self):
        line = self._lapline()
        return {"enabled": self.doc["enabled"], "version": self.doc["version"],
                "zones": len(self.doc["zones"]), "lapline": line is not None,
                "min_lap_s": line.get("min_lap_s") if line else None, "timing": self.active}



class Command:
    def __init__(self, seq, cmd, kart, arg, ttl, source, now, wall):
        self.seq = seq                  # assigned at first transmit
        self.cmd = cmd
        self.kart = kart                # 1-8 or 255
        self.arg = arg
        self.ttl = ttl
        self.source = source
        self.created_mono = now
        self.created_wall = wall
        self.addressed = list(KART_IDS) if kart == ALL_KARTS else [kart]
        self.outcomes = {k: "queued" for k in self.addressed}
        self.echo_wall = {}
        self.echo_mono = {}
        self.superseded = set()
        self.state = "queued"           # queued -> active -> done
        self.first_tx_mono = None
        self.first_tx_wall = None
        self.last_tx_mono = None
        self.tx_count = 0
        self.radio_sent = 0             # `sent` lines from the T-Beam for this seq
        self.wait_ms = None             # base queue wait of the last `sent` line (firmware 2026-11-07+)
        self.issued = None              # unix seconds at first transmit; the base uses it without GPS time
        self.auto = False               # sent by a pit policy (fleet rate), not an operator
        self.id = None                  # stable id independent of seq reuse

    def wire(self, kart=None):
        obj = {"cmd": self.cmd, "kart": self.kart if kart is None else kart, "seq": self.seq}
        if self.arg is not None:            # mode/slow/boost/race/rate: the base sends arg 0 when it is missing
            obj["arg"] = self.arg
        if self.cmd != "ping":
            obj["ttl"] = self.ttl
        if self.issued is not None:         # same value on every retry: TTL and race GO count from the first send
            obj["issued"] = self.issued
        return obj

    def arg_name(self):
        if self.cmd == "mode":
            return MODE_NAMES.get(self.arg)
        if self.cmd == "slow":
            return f"{self.arg or 5} mph"
        if self.cmd == "boost":
            return f"{self.arg}%"
        if self.cmd == "race":
            return race_arg_name(self.arg)
        if self.cmd == "rate":
            return f"{self.arg} Hz"
        return None

    def to_dict(self):
        return {
            "id": self.id, "seq": self.seq, "cmd": self.cmd, "kart": self.kart, "arg": self.arg,
            "arg_name": self.arg_name(),
            "ttl": self.ttl, "source": self.source, "state": self.state,
            "created_wall": self.created_wall, "first_tx_wall": self.first_tx_wall,
            "tx_count": self.tx_count, "radio_sent": self.radio_sent, "wait_ms": self.wait_ms, "auto": self.auto,
            "outcomes": {str(k): v for k, v in self.outcomes.items()},
            "echo_wall": {str(k): v for k, v in self.echo_wall.items()},
        }


class CommandManager:
    """Host-side delivery policy from the contract.

    send(obj) -> bool writes one JSON line to the T-Beam; False if the link is down.
    log(kind, **fields) records events. All methods expect the caller's lock.
    """

    def __init__(self, fleet, send, log=lambda *a, **k: None, start_seq=1,
                 ttl=DEFAULT_TTL_S, retry_interval=RETRY_INTERVAL_S, max_retransmits=MAX_RETRANSMITS):
        self.fleet = fleet
        self.send = send
        self.log = log
        self.ttl = ttl
        self.retry_interval = retry_interval
        self.max_retransmits = max_retransmits
        self.next_seq = start_seq if 1 <= start_seq <= 255 else 1
        self.history = deque(maxlen=HISTORY)    # all commands, newest last
        self.queue = []                         # queued (not yet transmitted), FIFO
        self._ids = 0
        self.last_wait_ms = None                # base queue wait of the latest `sent` line
        self.clock = time.time                  # unix seconds for "issued" (tests may replace it)

    # -- public API ----------------------------------------------------------
    def submit(self, cmd, kart, arg=None, source="?", now=0.0, wall=None, auto=False):
        cmd = str(cmd).lower()
        if cmd not in VALID_CMDS:
            raise CommandError(f"unknown cmd {cmd!r}")
        if isinstance(kart, bool) or isinstance(arg, bool):
            raise CommandError("kart and arg must be numbers, not true/false")
        if isinstance(kart, float) and not kart.is_integer():
            raise CommandError("kart must be 1-8 or 255")
        try:
            kart = int(kart)
        except (TypeError, ValueError):
            raise CommandError("kart must be 1-8 or 255")
        if kart not in KART_IDS and kart != ALL_KARTS:
            raise CommandError("kart must be 1-8 or 255")
        if cmd == "mode":
            try:
                arg = int(arg)
            except (TypeError, ValueError):
                raise CommandError("mode arg must be 0 ECO, 1 NORMAL or 2 SPORT")
            if arg not in MODE_NAMES:
                raise CommandError("mode arg must be 0 ECO, 1 NORMAL or 2 SPORT")
        elif cmd == "slow":
            try:
                arg = 5 if arg in (None, "") else int(arg)
            except (TypeError, ValueError):
                raise CommandError("slow: mph must be 1-60")
            if not 1 <= arg <= 60:
                raise CommandError("slow: mph must be 1-60")
        elif cmd == "boost":
            try:
                arg = BOOST_MAX_PCT if arg in (None, "") else int(arg)
            except (TypeError, ValueError):
                raise CommandError("boost: scale percent must be 10-100")
            if not BOOST_MIN_PCT <= arg <= BOOST_MAX_PCT:
                raise CommandError("boost: scale percent must be 10-100")
        elif cmd == "rate":
            try:
                arg = int(arg)
            except (TypeError, ValueError):
                raise CommandError("rate: arg must be 1 or 2 (Hz)")
            if arg not in (1, 2):
                raise CommandError("rate: arg must be 1 or 2 (Hz)")
        elif cmd == "race":
            # 1-30 = grid hold, GO in arg s; 0 = finish (no position); 101-108 = finish in position arg-100
            try:
                arg = 5 if arg in (None, "") else int(arg)
            except (TypeError, ValueError):
                raise CommandError("race: arg must be 1-30 (GO in s), 0 or 101-108 (finish)")
            if not (1 <= arg <= 30 or arg == 0 or RACE_FINISH_BASE + 1 <= arg <= RACE_FINISH_BASE + 8):
                raise CommandError("race: arg must be 1-30 (GO in s), 0 or 101-108 (finish)")
        else:
            arg = None
        c = Command(None, cmd, kart, arg, self.ttl, source, now, wall)
        c.auto = bool(auto)
        self._ids += 1
        c.id = self._ids
        self.history.append(c)
        if cmd == "stop":
            self._preempt(c, now)
            self._transmit_first(c, now, wall)      # STOP never waits in the queue
        else:
            self.queue.append(c)
            self.log("cmd_queued", id=c.id, cmd=cmd, kart=kart, arg=arg, source=source)
            self._pump(now, wall)
        return c

    def on_tele(self, kart, now, wall=None):
        """Match the kart's echoed last_cmd_seq/cmd_result to the command that used it."""
        st = self.fleet.karts[kart].status
        seq, res = st.get("last_cmd_seq"), st.get("cmd_result")
        if not isinstance(seq, int) or seq == 0:
            return
        c = self._latest_with_seq(seq)
        if c is None or kart not in c.outcomes or c.first_tx_mono is None:
            return
        if c.outcomes[kart] in ("superseded", "cancelled"):
            return
        new = CMD_RESULT_NAMES.get(res, "unknown")
        if c.outcomes[kart] != new:
            c.outcomes[kart] = new
            c.echo_wall[kart] = wall
            c.echo_mono[kart] = now
            self.log("cmd_outcome", id=c.id, seq=c.seq, kart=kart, outcome=new)

    def on_sent(self, msg):
        """T-Beam confirmation that it put a command on the air."""
        c = self._latest_with_seq(msg.get("seq"))
        if c is not None:
            c.radio_sent += 1
            if isinstance(msg.get("wait_ms"), (int, float)):
                c.wait_ms = msg["wait_ms"]
                self.last_wait_ms = msg["wait_ms"]

    def on_link_down(self, now, wall=None):
        """Serial link lost: stop retrying, never replay after reconnect."""
        for c in list(self.history):
            if c.state == "active":
                self._finish(c, "link_lost")
        for c in self.queue:
            for k in c.addressed:
                c.outcomes[k] = "link_lost"
            c.state = "done"
            self.log("cmd_done", id=c.id, outcomes=c.outcomes, reason="link_lost")
        self.queue = []

    def tick(self, now, wall=None):
        for c in list(self.history):
            if c.state != "active":
                continue
            need = self._need(c, now)
            if not need:
                self._finish(c, "no_ack")
                continue
            if now - c.last_tx_mono >= self.retry_interval:
                if c.tx_count > self.max_retransmits:
                    self._finish(c, "no_ack")
                    continue
                if not self._transmit(c, need, now):
                    self._finish(c, "link_lost")
        self._pump(now, wall)

    def snapshot(self):
        cmds, seen_auto = [], False
        for c in reversed(self.history):     # policy commands (fleet rate every 30 s): only the newest is listed
            if c.auto:
                if seen_auto:
                    continue
                seen_auto = True
            cmds.append(c.to_dict())
        last_fleet = next((c for c in cmds if c["kart"] == ALL_KARTS and not c["auto"]), None)
        per_kart = {}
        for c in reversed(self.history):
            if c.auto:
                continue                        # the fleet-rate policy never hides an operator's last command
            for k in c.addressed:
                if str(k) not in per_kart and c.outcomes.get(k) not in ("cancelled",):
                    per_kart[str(k)] = {"id": c.id, "seq": c.seq, "cmd": c.cmd, "arg": c.arg,
                                        "arg_name": c.arg_name(), "outcome": c.outcomes[k]}
        return {"recent": cmds[:15], "last_fleet": last_fleet, "per_kart": per_kart,
                "next_seq": self.next_seq, "queued": len(self.queue), "last_wait_ms": self.last_wait_ms}

    def boosted_recently(self, now, window_s=7.0):
        """Karts a pit BOOST may still be acting on (5 s override + margin)."""
        out = set()
        for c in self.history:
            if c.cmd == "boost" and c.first_tx_mono is not None and now - c.first_tx_mono <= window_s:
                out |= set(c.addressed)
        return out

    # -- internals -----------------------------------------------------------
    def _latest_with_seq(self, seq):
        for c in reversed(self.history):
            if c.seq == seq:
                return c
        return None

    def _alloc_seq(self):
        """Rolling 1-255, skipping any seq a kart currently reports as last handled
        (a kart ignores a repeat of its last seq, so reusing it would fake an ack)
        and any seq still in flight."""
        blocked = {self.fleet.karts[k].status.get("last_cmd_seq") for k in KART_IDS}
        blocked |= {c.seq for c in self.history if c.state == "active"}
        s = self.next_seq
        for _ in range(255):
            if s not in blocked:
                break
            s = s % 255 + 1
        self.next_seq = s % 255 + 1
        return s

    def _busy_karts(self, now):
        """Karts still owed an echo, or that echoed received/accepted within
        ACCEPT_HOLD_S (so e.g. a race START does not overwrite a MODE that the
        kart has accepted but not yet confirmed)."""
        busy = set()
        for c in self.history:
            if c.auto:
                continue                     # a policy command (fleet rate) never delays an operator's command
            for k in c.addressed:
                o = c.outcomes.get(k)
                if c.state == "active" and o == "pending":
                    busy.add(k)
                elif o in ("received", "accepted") and now - c.echo_mono.get(k, -1e9) < ACCEPT_HOLD_S:
                    busy.add(k)
        return busy

    def _pump(self, now, wall):
        """Send queued commands whose karts have nothing in flight (per-kart serialization)."""
        busy = self._busy_karts(now)
        still = []
        for c in self.queue:
            if busy & set(c.addressed):
                still.append(c)
                busy |= set(c.addressed)     # keep FIFO order per kart
                continue
            self._transmit_first(c, now, wall)
            busy |= {k for k in c.addressed if c.outcomes[k] == "pending"}
        self.queue = still

    def _preempt(self, stop_cmd, now):
        targets = set(stop_cmd.addressed)
        keep = []
        for c in self.queue:
            hit = targets & set(c.addressed)
            if hit:
                for k in hit:
                    c.outcomes[k] = "cancelled"
                c.addressed = [k for k in c.addressed if k not in hit]
                if not c.addressed:
                    c.state = "done"
                    self.log("cmd_done", id=c.id, outcomes=c.outcomes, reason="cancelled_by_stop")
                    continue
                if c.kart == ALL_KARTS:
                    c.superseded |= hit      # forces per-kart retransmits later
            keep.append(c)
        self.queue = keep
        for c in self.history:
            if c.state == "active" and c is not stop_cmd:
                hit = targets & set(c.addressed)
                for k in hit:
                    c.superseded.add(k)
                    if c.outcomes[k] not in FINAL:
                        c.outcomes[k] = "superseded"

    def _transmit_first(self, c, now, wall):
        if not c.auto:                       # an operator command wins over a policy command still in flight
            for h in self.history:
                if h.auto and h.state == "active":
                    for k in set(h.addressed) & set(c.addressed):
                        h.superseded.add(k)
                        if h.outcomes[k] not in FINAL and h.outcomes[k] not in ECHOED:
                            h.outcomes[k] = "superseded"
        c.issued = int(self.clock())
        c.seq = self._alloc_seq()
        c.state = "active"
        c.first_tx_mono = now
        c.first_tx_wall = wall
        for k in c.addressed:
            c.outcomes[k] = "pending" if self.fleet.is_online(k, now) else "offline"
        if not self._transmit(c, None, now):
            self._finish(c, "link_lost")

    def _transmit(self, c, need, now):
        """First send uses the command's own address. Retransmits use 255 only when
        it cannot re-trigger a kart that has since handled a newer command; otherwise
        one addressed line per kart still owed an echo (same seq)."""
        targets = list(c.addressed) if need is None else need
        intact_fleet = c.kart == ALL_KARTS and not c.superseded and len(c.addressed) == len(KART_IDS)
        if c.kart != ALL_KARTS or (intact_fleet and (need is None or len(need) > 1)):
            lines = [c.wire()]
        else:
            lines = [c.wire(kart=k) for k in targets]
        ok = all(self.send(obj) for obj in lines)
        c.tx_count += 1
        c.last_tx_mono = now
        self.log("cmd_tx", id=c.id, seq=c.seq, attempt=c.tx_count, lines=lines, ok=ok)
        return ok

    def _need(self, c, now):
        return [k for k in c.addressed
                if c.outcomes[k] == "pending" and k not in c.superseded and self.fleet.is_online(k, now)]

    def _finish(self, c, unresolved):
        for k in c.addressed:
            if c.outcomes[k] == "pending":
                c.outcomes[k] = unresolved
        c.state = "done"
        self.log("cmd_done", id=c.id, seq=c.seq, outcomes=c.outcomes, tx=c.tx_count)


# ---------------------------------------------------------------------------
# Pace car / track builder (README "Pace car: record a track by driving it").
# The pit records one kart's accepted fixes (2 Hz tele, no radio change), detects laps
# when the path comes back to its start, and turns a lap into the zones.json "track"
# (+ an optional lap line across the start).
REC_MIN_SATS = 5
REC_MIN_SPACING_M = 1.5
REC_JUMP_SLACK_M = 15.0
REC_START_RADIUS_M = 12.0
REC_MIN_LAP_M = 60.0
REC_MAX_POINTS = 20000
LAPLINE_LEN_M = 10.0


def dist_m(a, b):
    """Metres between two [lat, lon] (tangent plane, fine at track scale)."""
    cl = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(math.radians(b[1] - a[1]) * R_EARTH * cl, math.radians(b[0] - a[0]) * R_EARTH)


def path_length(pts):
    return sum(dist_m(pts[i - 1], pts[i]) for i in range(1, len(pts)))


def douglas_peucker(pts, tol_m):
    """Simplify [[lat, lon], ...] keeping both ends; perpendicular tolerance in metres. Iterative."""
    if len(pts) < 3:
        return [list(p[:2]) for p in pts]
    proj = local_en(pts[0][0], pts[0][1])
    xy = [proj(p[0], p[1]) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (ax, ay), (bx, by) = xy[i], xy[j]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        best, bi = -1.0, None
        for k in range(i + 1, j):
            px, py = xy[k]
            d = abs(dy * (px - ax) - dx * (py - ay)) / L if L > 1e-9 else math.hypot(px - ax, py - ay)
            if d > best:
                best, bi = d, k
        if bi is not None and best > tol_m:
            keep[bi] = True
            stack += [(i, bi), (bi, j)]
    return [list(pts[k][:2]) for k in range(len(pts)) if keep[k]]


def simplify_track(pts, tol_m=1.0, max_points=TRACK_MAX_POINTS):
    """Douglas-Peucker with `tol_m`, raised x1.5 until the result fits in max_points."""
    tol = max(0.05, float(tol_m))
    out = douglas_peucker(pts, tol)
    while len(out) > max_points:
        tol *= 1.5
        out = douglas_peucker(pts, tol)
    return out


def lapline_at(pts, length_m=LAPLINE_LEN_M):
    """A segment `length_m` long through pts[0], perpendicular to the heading there
    (towards the first point at least 4 m away). Returns (a, b) as [lat, lon]."""
    p0 = pts[0]
    ahead = next((p for p in pts[1:] if dist_m(p0, p) >= 4.0), pts[-1] if len(pts) > 1 else None)
    if ahead is None or dist_m(p0, ahead) < 0.5:
        raise ZoneError("lap is too short to place a lap line")
    proj = local_en(p0[0], p0[1])
    ex, ny = proj(ahead[0], ahead[1])
    h = math.hypot(ex, ny)
    lx, ly = -ny / h, ex / h                       # left normal
    half = length_m / 2
    cl = math.cos(math.radians(p0[0]))

    def back(e, n):
        return [round(p0[0] + math.degrees(n / R_EARTH), 7), round(p0[1] + math.degrees(e / (R_EARTH * cl)), 7)]
    return back(lx * half, ly * half), back(-lx * half, -ly * half)


class TrackRecorder:
    """Records one kart's path. Points are [lat, lon, t_s (since start), kph].
    Laps close when the path returns within REC_START_RADIUS_M of the lap's start point after
    at least REC_MIN_LAP_M travelled (closed at the nearest approach). Persisted as JSON in
    save_dir on every lap and on stop; the newest file is reloaded at start-up."""

    def __init__(self, save_dir=None):
        self.save_dir = save_dir
        self.file = None
        self._reset(None)
        if save_dir:
            self.load_latest()

    def _reset(self, kart):
        self.kart = kart
        self.recording = False
        self.since_wall = None
        self.stopped_wall = None
        self.t0 = None
        self.points = []
        self.laps = []
        self._lap_start = 0          # index of the current lap's first point
        self._travel = 0.0           # metres since the current lap's start
        self._close = None           # (index, distance) best approach to the start while inside the radius
        self.rejected = 0

    # -- control -------------------------------------------------------------
    def start(self, kart, now, wall):
        if kart not in KART_IDS:
            raise CommandError("kart must be 1-8")
        self._reset(kart)
        self.recording, self.since_wall, self.t0 = True, wall, now
        self.file = None
        if self.save_dir:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.file = os.path.join(self.save_dir, f"{stamp}-k{kart}.json")
        return self.summary()

    def stop(self, wall):
        if self.recording:
            if self._close is not None:
                self._close_lap(self._close[0])
            self.recording = False
            self.stopped_wall = wall
            self.save()
        return self.summary()

    def clear(self):
        f = self.file
        self._reset(None)
        self.file = None
        if f:
            try:
                os.remove(f)
            except OSError:
                pass
        return self.summary()

    # -- fixes ---------------------------------------------------------------
    def on_fix(self, kart, lat, lon, kph, sats, fix, now):
        """Offer one kart fix. Returns True when it was appended."""
        if not self.recording or kart != self.kart:
            return False
        if fix is not True or lat is None or lon is None or not isinstance(sats, (int, float)) or sats < REC_MIN_SATS:
            return False
        p = [round(float(lat), 7), round(float(lon), 7), round(now - self.t0, 2),
             None if kph is None else round(float(kph), 1)]
        if self.points:
            last = self.points[-1]
            d = dist_m(last, p)
            dt = max(0.0, p[2] - last[2])
            v = max(float(kph or 0.0), float(last[3] or 0.0)) / 3.6
            if d > v * dt + REC_JUMP_SLACK_M:
                self.rejected += 1
                return False                         # GPS jump
            if d < REC_MIN_SPACING_M:
                return False
            self._travel += d
        if len(self.points) >= REC_MAX_POINTS:
            self.stop(None)
            return False
        self.points.append(p)
        self._check_lap()
        return True

    def _check_lap(self):
        i = len(self.points) - 1
        start = self.points[self._lap_start]
        if self._travel < REC_MIN_LAP_M:
            return
        d = dist_m(start, self.points[i])
        if self._close is None:
            if d <= REC_START_RADIUS_M:
                self._close = (i, d)
            return
        if d < self._close[1] and d <= REC_START_RADIUS_M:
            self._close = (i, d)
            return
        self._close_lap(self._close[0])

    def _close_lap(self, end):
        start = self._lap_start
        pts = self.points[start:end + 1]
        self.laps.append({"n": len(self.laps) + 1, "start": start, "end": end,
                          "length_m": round(path_length(pts), 1),
                          "time_s": round(pts[-1][2] - pts[0][2], 1)})
        self._lap_start = end
        self._close = None
        self._travel = path_length(self.points[end:])
        self.save()

    # -- output --------------------------------------------------------------
    def lap_points(self, which):
        """[[lat, lon], ...] of lap n (1-based) or "all" (the whole recording)."""
        if which == "all":
            pts = self.points
        else:
            if isinstance(which, bool) or not isinstance(which, int) or not 1 <= which <= len(self.laps):
                raise CommandError(f"lap must be 1-{len(self.laps)} or \"all\"" if self.laps else "no laps recorded yet")
            lp = self.laps[which - 1]
            pts = self.points[lp["start"]:lp["end"] + 1]
        return [p[:2] for p in pts]

    def summary(self, with_points=False):
        out = {"kart": self.kart, "recording": self.recording, "since_wall": self.since_wall,
               "stopped_wall": self.stopped_wall, "n_points": len(self.points), "rejected": self.rejected,
               "travelled_m": round(path_length(self.points), 1) if with_points else None,
               "laps": [{"n": lp["n"], "length_m": lp["length_m"], "time_s": lp["time_s"],
                         "points": lp["end"] - lp["start"] + 1} for lp in self.laps],
               "file": os.path.basename(self.file) if self.file else None}
        if with_points:
            out["points"] = [p[:2] for p in self.points]
        return out

    def save(self):
        if not self.file or not self.points:
            return
        doc = {"kart": self.kart, "since_wall": self.since_wall, "stopped_wall": self.stopped_wall,
               "points": self.points, "laps": self.laps}
        try:
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            tmp = self.file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(doc, f, separators=(",", ":"))
            os.replace(tmp, self.file)
        except OSError:
            pass

    def load_latest(self):
        try:
            names = sorted(n for n in os.listdir(self.save_dir) if n.endswith(".json"))
        except OSError:
            return False
        for name in reversed(names):
            path = os.path.join(self.save_dir, name)
            try:
                with open(path) as f:
                    doc = json.load(f)
                pts = [[float(p[0]), float(p[1]), float(p[2]), p[3]] for p in doc["points"]]
                laps = [dict(lp) for lp in doc.get("laps") or []]
                for lp in laps:
                    if not 0 <= lp["start"] <= lp["end"] < len(pts):
                        raise ValueError("bad lap indices")
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                continue
            self._reset(doc.get("kart"))
            self.points, self.laps, self.file = pts, laps, path
            self.since_wall, self.stopped_wall = doc.get("since_wall"), doc.get("stopped_wall")
            self._lap_start = laps[-1]["end"] if laps else 0
            return True
        return False


def track_from_recording(rec, lap, cur_doc, width_m=6, lapline="auto", simplify_m=1.0, lap_time_s=None):
    """Build the zones body for POST-like saving: the recorded lap as "track" (closed), plus a lap
    line across the lap's start when lapline == "auto" (replacing any existing lap line).
    Returns (body, info). Raises CommandError/ZoneError."""
    if lapline not in ("auto", "none"):
        raise CommandError('lapline must be "auto" or "none"')
    if isinstance(width_m, bool) or not isinstance(width_m, (int, float)) or not TRACK_WIDTH_M[0] <= width_m <= TRACK_WIDTH_M[1]:
        raise CommandError("width_m must be 2-20")
    if isinstance(simplify_m, bool) or not isinstance(simplify_m, (int, float)) or not 0.1 <= simplify_m <= 20:
        raise CommandError("simplify_m must be 0.1-20")
    raw = rec.lap_points(lap)
    if len(raw) < 3 or path_length(raw) < 20:
        raise CommandError("the recording is too short for a track")
    pts = simplify_track(raw, simplify_m, TRACK_MAX_POINTS)
    loop = lap != "all" or dist_m(raw[0], raw[-1]) <= 2 * REC_START_RADIUS_M
    if loop and len(pts) > 3 and dist_m(pts[0], pts[-1]) <= max(3.0, 2 * simplify_m):
        pts = pts[:-1]                     # closed: the renderer joins last -> first
    track = {"points": pts, "closed": bool(loop and len(pts) >= 3), "width_m": round(float(width_m), 1)}
    zones = [dict(z) for z in cur_doc.get("zones") or []]
    info = {"lap": lap, "raw_points": len(raw), "points": len(pts), "length_m": round(path_length(raw), 1),
            "closed": track["closed"], "lapline": None}
    if lapline == "auto":
        old = next((z for z in zones if z.get("type") == "lapline"), None)
        zones = [z for z in zones if z.get("type") != "lapline" and z.get("id") != "lap"]
        a, b = lapline_at(raw)
        min_lap = int((old or {}).get("min_lap_s", DEFAULT_MIN_LAP_S))
        if lap_time_s:
            min_lap = max(5, min(min_lap, int(lap_time_s * 0.6)))     # never longer than 60 % of the driven lap
        zones.append({"id": "lap", "type": "lapline", "shape": "line", "a": a, "b": b, "min_lap_s": min_lap})
        info["lapline"] = {"a": a, "b": b, "min_lap_s": min_lap}
    body = {"enabled": cur_doc.get("enabled", False), "zones": zones, "track": track}
    validate_zones(body)                   # raises ZoneError with a clear message
    return body, info
