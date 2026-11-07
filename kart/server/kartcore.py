"""Kart display state: parsing the Heltec's USB lines, staleness, derived values.

Pure logic: no sockets, no threads, no serial. Every function that cares about
time takes `now` (a time.monotonic() value) so tests can drive the clock.

Input contract: firmware/libraries/GoKartProtocol/GoKartProtocol.h
`gkTelemetryToJson` (field table in firmware/base-tbeam-s3/SERIAL_PROTOCOL.md,
`tele`). The kart Heltec prints `{"t":"self",...}` for itself (2 Hz) and
`{"t":"fleet","kart":N,...}` for every other kart it hears, plus `log`, `cmd`,
`ble` and `status` lines.

Conventions (docs/requirements.md "Agreed telemetry fields"): a missing value is
None (JSON null), never a fabricated zero. The firmware prints VESC numbers as
0 when its VESC link is down, and gps_kph/heading as 0 without a fix, so those
groups are only taken from samples whose `vesc_link` / `fix` flag is true, and
each group keeps its own age. The derivations and fault names are copied from
pit/server/pitcore.py so the pit and the kart show the same numbers.

The Pi is display only (docs/requirements.md): nothing here talks to a VESC.
Mode taps become text requests to the Heltec, which decides.
"""
from __future__ import annotations

import math
import random
from collections import deque

KART_IDS = tuple(range(1, 9))
ONLINE_S = 5.0     # same thresholds as the pit (pitcore.ONLINE_S / STALE_S)
STALE_S = 15.0
SELF_FRESH_S = 3.0  # self lines arrive at 2 Hz over USB; older than this is stale for the driver

KART_COLORS = {     # identical to pitcore.KART_COLORS so a kart has one colour everywhere
    1: "#e6194b", 2: "#3cb44b", 3: "#ffe119", 4: "#4363d8",
    5: "#f58231", 6: "#911eb4", 7: "#42d4f4", 8: "#f032e6",
}
MODE_NAMES = {0: "ECO", 1: "NORMAL", 2: "SPORT"}
# Motor-current scale per mode: firmware/kart-heltec-v4/config.h MODES[] (80/140, 100/140, 1.0).
MODE_SCALES = {0: 80.0 / 140.0, 1: 100.0 / 140.0, 2: 1.0}
CMD_CODES = {"mode": 1, "stop": 2, "start": 3, "ping": 4, "slow": 5, "boost": 6, "race": 7}
CMD_NAMES = {v: k for k, v in CMD_CODES.items()}
CMD_RESULT_NAMES = {0: "none", 1: "accepted", 2: "applied", 3: "no_vesc", 4: "rejected", 5: "unknown"}
SRC_USB = 0xFE      # the firmware's source id for commands typed on its USB port (us)
SRC_PIT = 0         # GK_ADDR_BASE

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

# --- derived values (same as pitcore) ---------------------------------------
PACK_EMPTY_V = 60.0      # 20S Li-ion, 3.00 V/cell
PACK_FULL_V = 84.0       # 20S Li-ion, 4.20 V/cell
UNDER_LOAD_A = 20.0
POLE_PAIRS = 7
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
    if batt_a is None:
        return None
    return abs(float(batt_a)) > UNDER_LOAD_A


def wheel_mph(erpm):
    """Contract formula; UNCALIBRATED (drivetrain numbers not physically verified)."""
    if erpm is None:
        return None
    return round(float(erpm) / POLE_PAIRS / GEAR_RATIO * (WHEEL_DIAMETER_M * math.pi) * 60.0 / METERS_PER_MILE, 2)


def erpm_for_mph(mph):
    return mph * METERS_PER_MILE / 60.0 / (WHEEL_DIAMETER_M * math.pi) * GEAR_RATIO * POLE_PAIRS


def kph_to_mph(kph):
    return None if kph is None else round(float(kph) * 0.621371, 2)


def link_status(last_mono, now, online_s=ONLINE_S, stale_s=STALE_S):
    if last_mono is None:
        return "never"
    age = now - last_mono
    if age <= online_s:
        return "online"
    if age <= stale_s:
        return "stale"
    return "offline"


def _age(last_mono, now):
    return None if last_mono is None else round(max(0.0, now - last_mono), 1)


# --- one kart (self or fleet) -------------------------------------------------
VESC_FIELDS = ("volts", "motor_a", "batt_a", "erpm", "duty", "esc_c", "fault", "ah", "scale")
STATUS_FIELDS = ("seq", "mode_req", "mode_actual", "mode_confirmed", "stopped", "vesc_link",
                 "pps", "wifi", "writes", "last_cmd_seq", "cmd_result", "uptime_s",
                 "base_rssi", "rssi", "snr", "rx_ms", "race_pos", "race_state")


class KartState:
    def __init__(self, kart_id, trail_points=30, trail_s=30.0):
        self.id = kart_id
        self.color = KART_COLORS.get(kart_id, "#888888")
        self.last_mono = None
        self.last_wall = None
        self.status = {}
        self.last_vesc_mono = None
        self.vesc = {}
        self.last_fix_mono = None
        self.gps_age_at_rx = None
        self.fix = {}
        self.gps_now = {}
        self.trail = deque(maxlen=trail_points)
        self.trail_s = trail_s
        self.packets = 0
        self.last_pit_mono = None       # self only: latest sample with base_rssi != 0
        self.pit_rssi = None

    def ingest(self, msg, now, wall):
        self.packets += 1
        self.last_mono = now
        self.last_wall = wall
        self.status = {f: msg.get(f) for f in STATUS_FIELDS}
        if msg.get("vesc_link") is True:
            self.vesc = {f: msg.get(f) for f in VESC_FIELDS}
            self.last_vesc_mono = now
        self.gps_now = {"fix": msg.get("fix"), "sats": msg.get("sats"), "gps_age_s": msg.get("gps_age_s"),
                        # USB-only self fields (firmware 2026-11-06+); absent = None
                        "hdop": msg.get("hdop"), "fix_age_ms": msg.get("fix_age_ms"), "gps_hz": msg.get("gps_hz"),
                        "base_lat": msg.get("base_lat"), "base_lon": msg.get("base_lon"),
                        "base_fix_age_s": msg.get("base_fix_age_s")}
        lat, lon = msg.get("lat"), msg.get("lon")
        if msg.get("fix") is True and lat is not None and lon is not None:
            self.fix = {"lat": lat, "lon": lon, "heading": msg.get("heading"),
                        "gps_kph": msg.get("gps_kph"), "sats": msg.get("sats")}
            age = msg.get("gps_age_s")
            self.gps_age_at_rx = None if age in (None, 255) else float(age)
            self.last_fix_mono = now
            if not self.trail or (self.trail[-1][1], self.trail[-1][2]) != (lat, lon):
                self.trail.append((now, lat, lon))
        br = msg.get("base_rssi")
        if isinstance(br, (int, float)) and br != 0:     # 0 = the Heltec has not heard the pit for 30 s
            self.last_pit_mono = now
            self.pit_rssi = br

    def snapshot(self, now, online_s=ONLINE_S):
        link = link_status(self.last_mono, now, online_s=online_s)
        vesc_age = _age(self.last_vesc_mono, now)
        fix_age = None
        if self.last_fix_mono is not None:
            fix_age = round(now - self.last_fix_mono + (self.gps_age_at_rx or 0.0), 1)
        volts, fault = self.vesc.get("volts"), self.vesc.get("fault")
        mr, ma = self.status.get("mode_req"), self.status.get("mode_actual")
        vesc_fresh = vesc_age is not None and vesc_age <= online_s
        fix_fresh = fix_age is not None and fix_age <= online_s
        return {
            "id": self.id,
            "color": self.color,
            "link": link,
            "age_s": _age(self.last_mono, now),
            "last_seen_wall": self.last_wall,
            "packets": self.packets,
            "status": dict(self.status),
            "mode_req_name": MODE_NAMES.get(mr) if mr is not None else None,
            "mode_actual_name": MODE_NAMES.get(ma) if ma is not None else None,
            "vesc": dict(self.vesc),
            "vesc_age_s": vesc_age,
            "vesc_fresh": vesc_fresh,
            "fault_name": VESC_FAULT_NAMES.get(fault, "UNKNOWN_CODE") if fault is not None else None,
            "derived": {
                "battery_pct": battery_percent(volts),
                "under_load": under_load(self.vesc.get("batt_a")),
                "wheel_mph": wheel_mph(self.vesc.get("erpm")),
                "wheel_mph_calibrated": False,
                # GPS ground speed only from a fresh fix: no fix is "unknown", never 0 mph.
                "gps_mph": kph_to_mph(self.fix.get("gps_kph")) if fix_fresh else None,
            },
            "gps": {
                "fix_now": self.gps_now.get("fix"),
                "sats_now": self.gps_now.get("sats"),
                "hdop_now": self.gps_now.get("hdop"),
                "gps_hz": self.gps_now.get("gps_hz"),
                "fix_age_ms": self.gps_now.get("fix_age_ms"),
                "base": ({"lat": self.gps_now.get("base_lat"), "lon": self.gps_now.get("base_lon"),
                          "fix_age_s": self.gps_now.get("base_fix_age_s")}
                         if self.gps_now.get("base_lat") is not None else None),
                "last_fix": dict(self.fix) if self.fix else None,
                "fix_age_s": fix_age,
                "fix_fresh": fix_fresh,
            },
            "trail": [[lat, lon] for (t, lat, lon) in self.trail if now - t <= self.trail_s],
        }


# --- the whole display state --------------------------------------------------
class KartDisplay:
    """Everything the driver page shows. Callers hold the app lock."""

    def __init__(self, configured_id=None):
        self.configured_id = configured_id
        self.me = KartState(configured_id or 0, trail_points=240, trail_s=120.0)
        self.fleet = {}                      # kart id -> KartState
        self.heltec_id = None                # from self lines (authoritative over config)
        self.status = {}                     # latest {"t":"status"} reply
        self.status_mono = None
        self.ble = {}
        self.ble_mono = None
        self.logs = deque(maxlen=40)         # (wall, kart, msg)
        self.cmds_seen = deque(maxlen=20)    # every {"t":"cmd"} line (any source)
        self.simulation = False
        self.rx_lines = 0
        self.rx_bad = 0
        self.gps_lines = 0
        self.last_uptime = None
        self.heltec_reboots = 0
        # SLOW has no telemetry flag (all 8 flag bits are used), so it is inferred
        # from command echoes: a SLOW the firmware took (accepted/applied/unknown)
        # stays on until a START is echoed or the Heltec reboots.
        self.slow = {"active": None, "mph": None, "since_wall": None, "basis": "unknown since display start"}
        self.slow_first_self = True

    # -- ingest ----------------------------------------------------------------
    def ingest(self, msg, now, wall):
        """Apply one parsed line. Returns the line type."""
        t = msg.get("t")
        if t == "self":
            k = msg.get("kart")
            if isinstance(k, int):
                self.heltec_id = k
                self.me.id = k
                self.me.color = KART_COLORS.get(k, "#888888")
            up = msg.get("uptime_s")
            if isinstance(up, int):
                # uptime_s is u16: a drop of more than a few seconds that is not a wrap means a reboot
                if self.last_uptime is not None and up + 5 < self.last_uptime and self.last_uptime < 65000:
                    self.heltec_reboots += 1
                    self._slow_set(False, None, wall, "Heltec rebooted (SLOW is not kept across a reboot)")
                if self.slow_first_self and up < 120:
                    self._slow_set(False, None, wall, "Heltec booted recently, no SLOW since")
                self.last_uptime = up
            self.slow_first_self = False
            sm = msg.get("slow_mph")
            if isinstance(sm, int):          # firmware from 2026-11-06 reports SLOW in telemetry: no inference needed
                if (sm > 0) != (self.slow.get("active") is True) or (sm > 0 and sm != self.slow.get("mph")):
                    self._slow_set(sm > 0, sm or None, wall, "from Heltec telemetry (slow_mph)")
                elif self.slow.get("basis") != "from Heltec telemetry (slow_mph)":
                    self.slow["basis"] = "from Heltec telemetry (slow_mph)"
            self.me.ingest(msg, now, wall)
        elif t == "fleet":
            k = msg.get("kart")
            if k not in KART_IDS:
                self.rx_bad += 1
                return t
            if k not in self.fleet:
                self.fleet[k] = KartState(k)
            self.fleet[k].ingest(msg, now, wall)
        elif t == "cmd":
            self.cmds_seen.append({"wall": wall, "mono": now, **{f: msg.get(f) for f in
                                   ("kart", "src", "seq", "cmd", "arg", "result")}})
            self._slow_from_cmd(msg, wall)
        elif t == "log":
            text = str(msg.get("msg"))
            if text == "SIMULATION":
                self.simulation = True
            self.logs.append({"wall": wall, "kart": msg.get("kart"), "msg": text})
        elif t == "status":
            self.status = dict(msg)
            self.status_mono = now
            if str(msg.get("fw") or "").startswith("sim-"):
                self.simulation = True
        elif t == "gps":
            self.gps_lines += 1             # 5 Hz position line; the game engine consumes it
        elif t == "ble":
            self.ble = dict(msg)
            self.ble_mono = now
        return t

    def _slow_set(self, active, mph, wall, basis):
        self.slow = {"active": active, "mph": mph, "since_wall": wall, "basis": basis}

    def _slow_from_cmd(self, msg, wall):
        cmd, res = msg.get("cmd"), msg.get("result")
        who = "pit" if msg.get("src") == SRC_PIT else "display" if msg.get("src") == SRC_USB else "src %s" % msg.get("src")
        if cmd == CMD_CODES["slow"] and res in (1, 2, 5):
            mph = msg.get("arg") or 5               # firmware: arg 0 = SLOW_DEFAULT_MPH (5)
            self._slow_set(True, mph, wall, f"SLOW {mph} mph from {who} ({CMD_RESULT_NAMES.get(res)})")
        elif cmd == CMD_CODES["start"] and res in (1, 2, 3, 5):
            # firmware clears slowActive before it checks the VESC, so any START it took ends SLOW
            self._slow_set(False, None, wall, f"START from {who}")

    def on_link_change(self):
        """Serial link (re)connected: the Heltec may have rebooted while we were away."""
        self.slow_first_self = True
        self.last_uptime = None

    # -- snapshot --------------------------------------------------------------
    def pit_link(self, now):
        me = self.me
        if me.last_mono is None or now - me.last_mono > SELF_FRESH_S:
            return {"state": "unknown", "rssi": None, "age_s": _age(me.last_pit_mono, now)}
        br = me.status.get("base_rssi")
        if isinstance(br, (int, float)) and br != 0:
            return {"state": "ok", "rssi": br, "age_s": 0.0}
        return {"state": "lost", "rssi": None, "age_s": _age(me.last_pit_mono, now)}

    def snapshot(self, now):
        me = self.me.snapshot(now, online_s=SELF_FRESH_S)
        me["configured_id"] = self.configured_id
        me["heltec_id"] = self.heltec_id
        me["id_mismatch"] = (self.configured_id is not None and self.heltec_id is not None
                             and self.configured_id != self.heltec_id)
        fleet = [k.snapshot(now) for _, k in sorted(self.fleet.items())]
        return {
            "self": me,
            "fleet": fleet,
            "pit_link": self.pit_link(now),
            "slow": dict(self.slow),
            "simulation": self.simulation,
            "heltec": {"status": dict(self.status), "status_age_s": _age(self.status_mono, now),
                       "ble": dict(self.ble), "ble_age_s": _age(self.ble_mono, now),
                       "reboots_seen": self.heltec_reboots,
                       "logs": list(self.logs)[-12:]},
            "cmds_seen": [{k: v for k, v in c.items() if k != "mono"} | {"age_s": _age(c["mono"], now),
                          "cmd_name": CMD_NAMES.get(c["cmd"]), "result_name": CMD_RESULT_NAMES.get(c["result"])}
                          for c in list(self.cmds_seen)[-6:]],
            "rx_lines": self.rx_lines,
            "rx_bad": self.rx_bad,
            "gps_lines": self.gps_lines,
        }


# --- commands from the touchscreen ---------------------------------------------
class CommandError(Exception):
    pass


def command_text(cmd, arg=None):
    """Validate a driver/debug request and return the Heltec text line.
    Allowed: mode 0|1|2, slow [mph], stop, start, ping, status."""
    if cmd == "mode":
        if arg not in (0, 1, 2):
            raise CommandError("mode needs arg 0 (ECO), 1 (NORMAL) or 2 (SPORT)")
        return f"mode {arg}"
    if cmd == "slow":
        if arg is None or arg == 0:
            return "slow"
        if not isinstance(arg, int) or isinstance(arg, bool) or not 1 <= arg <= 40:
            raise CommandError("slow takes an integer mph 1-40 (or none for the firmware default)")
        return f"slow {arg}"
    if cmd in ("stop", "start", "ping", "status"):
        return cmd
    raise CommandError(f"unknown command {cmd!r} (mode, slow, stop, start, ping, status)")


class CommandTracker:
    """Outcome of each request we typed into the Heltec's USB port.

    The firmware numbers USB commands itself (++usbSeq) and echoes
    {"t":"cmd","src":254,...}, so a request is matched to the oldest unanswered
    request with the same command code. Later self lines whose last_cmd_seq is
    that seq refine the result (accepted -> applied). No echo within
    ECHO_TIMEOUT_S = "no_echo" (outcome unknown). Nothing is ever resent."""

    ECHO_TIMEOUT_S = 4.0

    def __init__(self, keep=12):
        self.items = deque(maxlen=keep)
        self.next_id = 1

    def sent(self, cmd, arg, line, ok, now, wall, who):
        rec = {"id": self.next_id, "cmd": cmd, "arg": arg, "line": line, "who": who,
               "sent_wall": wall, "sent_mono": now, "state": "sent" if ok else "link_down",
               "seq": None, "result": None, "result_name": None, "echo_wall": None}
        self.next_id += 1
        self.items.append(rec)
        return rec

    def on_cmd_line(self, msg, wall):
        if msg.get("src") != SRC_USB:
            return None
        code = msg.get("cmd")
        for rec in self.items:
            if rec["state"] == "sent" and CMD_CODES.get(rec["cmd"]) == code:
                rec.update(state="echoed", seq=msg.get("seq"), result=msg.get("result"),
                           result_name=CMD_RESULT_NAMES.get(msg.get("result")), echo_wall=wall)
                return rec
        return None

    def on_self(self, msg):
        seq, res = msg.get("last_cmd_seq"), msg.get("cmd_result")
        if not self.items or seq is None:
            return
        rec = self.items[-1]
        if rec["state"] == "echoed" and rec["seq"] == seq and res not in (None, 0) and res != rec["result"]:
            rec["result"], rec["result_name"] = res, CMD_RESULT_NAMES.get(res)

    def tick(self, now):
        for rec in self.items:
            if rec["state"] == "sent" and now - rec["sent_mono"] > self.ECHO_TIMEOUT_S:
                rec["state"] = "no_echo"

    def on_link_down(self):
        for rec in self.items:
            if rec["state"] == "sent":
                rec["state"] = "link_lost"

    def snapshot(self, now):
        return [{k: v for k, v in r.items() if k != "sent_mono"} | {"age_s": _age(r["sent_mono"], now)}
                for r in reversed(self.items)]


# --- game layer (Mario Kart zones) ----------------------------------------------
# Contract: pit/server/map/ZONES.md. The kart evaluates the zone file against its own
# GPS (self lines, 2 Hz) so the game works with no radio and no Wi-Fi. The only thing
# it ever sends is `boost SECS SCALE` (timed current-scale override in the Heltec,
# never above 1.0, auto-reverting, cancelled by STOP/START). Pure logic: the server
# sends the returned command lines and logs every returned record.
R_EARTH = 6371000.0
ZONE_EXIT_HYST_M = 3.0       # "outside" again only > 3 m outside the edge
LAP_REARM_M = 6.0            # lap line: after a crossing, the next one counts only once the kart has been
                             # this far from the line (GPS jitter on the line, a kart parked on the grid)
SWEEP_MAX_GAP_S = 1.5        # zone entry also tests the path from the previous fix if it is this recent
                             # (2 Hz self-line fallback at speed: a 3 m pad fits between two fixes)
GO_LINE_STANDALONE_S = 5.0   # a go line with no countdown seen (display restarted mid-hold) starts a race
GAME_STATE_MAX_AGE_S = 1800.0  # lap/race timing restored after a server restart if saved this recently
GAME_TIMING_EVENTS = ("lap_start", "lap", "lap_reset", "race_countdown", "race_go", "race_finish",
                      "race_released", "zones_lap_restored")
NO_FIX_EXIT_S = 5.0          # no fix for this long: leave every zone, forget the previous fix
MUD_REPEAT_S = 2.0           # mud: re-send while inside
MUD_SECS = 3                 # each mud command lasts 3 s (so the cap ends <= 3 s after leaving)
BOOST_DEFAULTS = {"secs": 5, "scale": 1.0, "cooldown_s": 20.0}
ITEM_COOLDOWN_S = 15.0
# Item box roll (ZONES.md "Item boxes"): (name, probability, secs, scale); empty = nothing sent
ITEM_TABLE = (("boost", 0.45, 5, 1.0), ("super", 0.15, 8, 1.0), ("mud", 0.25, 3, 0.5), ("empty", 0.15, None, None))
RACE_GO_GRACE_S = 0.0        # the overlay/laps follow the computed GO instant; the go line only confirms it
RACE_STATES = {0: "idle", 1: "countdown", 2: "running", 3: "finished"}
HEADING_SLEW_DPS = 120.0     # heading-up map: max rotation rate
ZONE_TYPES = ("boost", "mud", "item", "lapline")
MUD_DEFAULT_SCALE = 0.5
MIN_LAP_DEFAULT_S = 20.0
MIN_CIRCLE_R_M = 3.0         # ZONES.md: >= 3 (pads are typically 5 m)
SCALE_MIN, SCALE_MAX = 0.10, 1.00
SCALE_TOL = 0.015            # firmware MODE_MATCH_TOL


def boot_id():
    """Linux boot id (a restarted process in the same boot shares CLOCK_MONOTONIC), else "nonlinux"."""
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return "nonlinux"


def local_en(lat, lon, ref):
    """(east, north) metres of lat/lon on the tangent plane at ref = (lat, lon)."""
    return ((lon - ref[1]) * math.pi / 180.0 * R_EARTH * math.cos(math.radians(ref[0])),
            (lat - ref[0]) * math.pi / 180.0 * R_EARTH)


def point_in_polygon(p, poly):
    """Even-odd ray cast; p and poly in the same planar coordinates."""
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xc:
                inside = not inside
    return inside


def dist_point_segment(p, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def segment_crossing(p1, p2, q1, q2):
    """Fraction t in [0, 1] along p1->p2 where it crosses segment q1-q2, else None.
    Touching an end point counts; collinear overlap does not (no direction to cross)."""
    rx, ry = p2[0] - p1[0], p2[1] - p1[1]
    sx, sy = q2[0] - q1[0], q2[1] - q1[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-12:
        return None
    qpx, qpy = q1[0] - p1[0], q1[1] - p1[1]
    t = (qpx * sy - qpy * sx) / den
    u = (qpx * ry - qpy * rx) / den
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return t
    return None


def clamp_scale(x):
    return max(SCALE_MIN, min(SCALE_MAX, float(x)))


def boost_line(secs, scale):
    """The Heltec USB command. secs 1-30, scale 0.10-1.00, never above 1.0."""
    secs = int(max(1, min(30, round(float(secs)))))
    return f"boost {secs} {clamp_scale(scale):.2f}"


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pt(v):
    return isinstance(v, (list, tuple)) and len(v) >= 2 and _num(v[0]) and _num(v[1])


class Zone:
    """One boost/mud area or a lap line, projected around its own reference point."""

    def __init__(self, z):
        self.raw = z
        self.id = str(z.get("id"))
        self.type = z.get("type")
        self.name = z.get("name") or self.id
        self.shape = z.get("shape")
        if self.type == "lapline":
            if self.shape not in (None, "line") or not (_pt(z.get("a")) and _pt(z.get("b"))):
                raise ValueError("lapline needs shape line with points a and b")
            self.ref = (float(z["a"][0]), float(z["a"][1]))
            self.a = (0.0, 0.0)
            self.b = local_en(float(z["b"][0]), float(z["b"][1]), self.ref)
            self.min_lap_s = float(z["min_lap_s"]) if _num(z.get("min_lap_s")) and z["min_lap_s"] > 0 else MIN_LAP_DEFAULT_S
            return
        if self.shape == "circle":
            if not (_num(z.get("lat")) and _num(z.get("lon")) and _num(z.get("r_m"))):
                raise ValueError("circle needs lat, lon, r_m")
            if z["r_m"] < MIN_CIRCLE_R_M:
                raise ValueError(f"circle r_m {z['r_m']} < {MIN_CIRCLE_R_M:g} m")
            self.ref = (float(z["lat"]), float(z["lon"]))
            self.r = float(z["r_m"])
        elif self.shape == "polygon":
            pts = z.get("points")
            if not isinstance(pts, list) or len(pts) < 3 or not all(_pt(p) for p in pts):
                raise ValueError("polygon needs >= 3 [lat, lon] points")
            self.ref = (float(pts[0][0]), float(pts[0][1]))
            self.poly = [local_en(float(p[0]), float(p[1]), self.ref) for p in pts]
        else:
            raise ValueError(f"unknown shape {self.shape!r}")
        if self.type == "item":
            self.cooldown_s = float(z["cooldown_s"]) if _num(z.get("cooldown_s")) and z["cooldown_s"] >= 0 \
                else ITEM_COOLDOWN_S
            if self.shape == "circle" and self.r < MIN_CIRCLE_R_M:
                raise ValueError("item box too small")
            return
        if self.type == "boost":
            self.scale = clamp_scale(z["scale"]) if _num(z.get("scale")) else BOOST_DEFAULTS["scale"]
            self.secs = int(max(1, min(30, z["secs"]))) if _num(z.get("secs")) else BOOST_DEFAULTS["secs"]
            self.cooldown_s = float(z["cooldown_s"]) if _num(z.get("cooldown_s")) and z["cooldown_s"] >= 0 \
                else BOOST_DEFAULTS["cooldown_s"]
        else:   # mud
            self.scale = clamp_scale(z["scale"]) if _num(z.get("scale")) else MUD_DEFAULT_SCALE

    def outside_m(self, lat, lon):
        """<= 0 inside the shape, else metres outside its edge."""
        p = local_en(lat, lon, self.ref)
        if self.shape == "circle":
            return math.hypot(p[0], p[1]) - self.r
        if point_in_polygon(p, self.poly):
            return 0.0
        n = len(self.poly)
        return min(dist_point_segment(p, self.poly[i], self.poly[(i + 1) % n]) for i in range(n))

    def swept(self, lat1, lon1, lat2, lon2):
        """True if the straight path between two fixes passes through the shape (entry between samples)."""
        p, q = local_en(lat1, lon1, self.ref), local_en(lat2, lon2, self.ref)
        if self.shape == "circle":
            return dist_point_segment((0.0, 0.0), p, q) <= self.r
        n = len(self.poly)
        return point_in_polygon(p, self.poly) or any(
            segment_crossing(p, q, self.poly[i], self.poly[(i + 1) % n]) is not None for i in range(n))

    def crossing(self, lat1, lon1, lat2, lon2):
        return segment_crossing(local_en(lat1, lon1, self.ref), local_en(lat2, lon2, self.ref), self.a, self.b)

    def line_dist_m(self, lat, lon):
        return dist_point_segment(local_en(lat, lon, self.ref), self.a, self.b)


def parse_zones(doc):
    """-> (enabled, [Zone boost/mud], lapline Zone or None, [ignored {id, reason}])."""
    if not isinstance(doc, dict) or not isinstance(doc.get("zones"), list):
        return False, [], None, []
    areas, lapline, ignored = [], None, []
    seen = set()
    for z in doc["zones"]:
        if not isinstance(z, dict):
            continue
        zid = str(z.get("id"))
        if z.get("type") not in ZONE_TYPES:
            ignored.append({"id": zid, "reason": f"unknown type {z.get('type')!r}"})
            continue
        if zid in seen:
            ignored.append({"id": zid, "reason": "duplicate id"})
            continue
        try:
            zone = Zone(z)
        except (ValueError, TypeError, KeyError) as e:
            ignored.append({"id": zid, "reason": str(e)})
            continue
        seen.add(zid)
        if zone.type == "lapline":
            if lapline is None:
                lapline = zone
            else:
                ignored.append({"id": zid, "reason": "only the first lap line is timed"})
        else:
            areas.append(zone)
    return doc.get("enabled") is True, areas, lapline, ignored


# --- GPS samples for the game: quality filter, outliers, heading, base correction ---
GPS_LINE_FRESH_S = 2.0       # {"t":"gps"} lines seen this recently: the self line's position is not used
GPS_MAX_HDOP = 4.0           # worse = drawn greyed, never used for triggers or laps
GPS_MIN_SATS = 5
GPS_JUMP_SLACK_M = 15.0      # outlier: further than speed * dt + 15 m from the last accepted sample
GPS_HEADING_MIN_MPS = 1.5    # Doppler heading is noise below this; keep the last good one
CORR_WINDOW_S = 30.0         # base correction: mean over this window
CORR_MAX_BASE_AGE_S = 5.0
CORR_MAX_M = 25.0


def _dist_m(a, b):
    e, n = local_en(b[0], b[1], a)
    return math.hypot(e, n)


class GpsFilter:
    """One position sample in -> {lat, lon, kph, heading, hdop, sats, quality, usable, reset}.
    quality: "good" | "poor" (hdop > 4 or sats < 5; drawn greyed, never triggers) |
    "outlier" (jump > speed*dt + 15 m from the last accepted sample). A second sample
    that agrees with a rejected one is accepted ("reset": the old track is not joined
    to it, so no lap crossing is drawn across the jump). hdop/sats missing (older
    firmware) are not filtered."""

    def __init__(self):
        self.acc = None          # (lat, lon, mono, mps) last accepted good sample
        self.cand = None         # (lat, lon, mono) last rejected outlier
        self.heading = None      # last good Doppler heading

    def feed(self, lat, lon, kph, heading, hdop, sats, now, source):
        mps = (float(kph) / 3.6) if _num(kph) else 0.0
        if _num(heading) and mps >= GPS_HEADING_MIN_MPS:
            self.heading = float(heading)
        out = {"lat": lat, "lon": lon, "kph": kph if _num(kph) else None, "heading": self.heading,
               "hdop": hdop if _num(hdop) else None, "sats": sats if _num(sats) else None,
               "source": source, "mono": now, "usable": False, "reset": False}
        if (_num(hdop) and hdop > GPS_MAX_HDOP) or (_num(sats) and sats < GPS_MIN_SATS):
            out["quality"] = "poor"
            return out
        if self.acc is not None:
            dt = max(0.0, now - self.acc[2])
            limit = max(mps, self.acc[3]) * dt + GPS_JUMP_SLACK_M
            if _dist_m(self.acc[:2], (lat, lon)) > limit:
                c = self.cand
                if c is not None and _dist_m(c[:2], (lat, lon)) <= mps * max(0.0, now - c[2]) + GPS_JUMP_SLACK_M:
                    out["reset"] = True          # repeated: the kart really is over there
                else:
                    self.cand = (lat, lon, now)
                    out["quality"] = "outlier"
                    return out
        self.cand = None
        self.acc = (lat, lon, now, mps)
        out.update(quality="good", usable=True)
        return out


class BaseCorrection:
    """Optional position-domain correction (config "gps_correction", default off):
    pit surveyed position (map package "pit") minus the pit radio's live fix
    (self base_lat/base_lon, used while base_fix_age_s < 5), averaged over 30 s,
    applied only when under 25 m. The vector is always computed and exposed."""

    def __init__(self, enabled=False):
        self.enabled = bool(enabled)
        self.pit = None
        self.samples = deque()   # (mono, dlat, dlon)

    def set_pit(self, pit):
        p = (float(pit["lat"]), float(pit["lon"])) if isinstance(pit, dict) and _num(pit.get("lat")) and _num(pit.get("lon")) else None
        if p != self.pit:
            self.pit = p
            self.samples.clear()

    def _trim(self, now):
        while self.samples and now - self.samples[0][0] > CORR_WINDOW_S:
            self.samples.popleft()

    def feed(self, msg, now):
        bl, bo, age = msg.get("base_lat"), msg.get("base_lon"), msg.get("base_fix_age_s")
        if self.pit and _num(bl) and _num(bo) and _num(age) and age < CORR_MAX_BASE_AGE_S:
            self.samples.append((now, self.pit[0] - bl, self.pit[1] - bo))
        self._trim(now)

    def vector(self, now):
        self._trim(now)
        if not self.samples or self.pit is None:
            return None
        n = len(self.samples)
        dlat = sum(x[1] for x in self.samples) / n
        dlon = sum(x[2] for x in self.samples) / n
        e = dlon * math.pi / 180 * R_EARTH * math.cos(math.radians(self.pit[0]))
        nn = dlat * math.pi / 180 * R_EARTH
        return {"dlat": dlat, "dlon": dlon, "east_m": round(e, 2), "north_m": round(nn, 2),
                "mag_m": round(math.hypot(e, nn), 2), "samples": n}

    def apply(self, lat, lon, now):
        v = self.vector(now) if self.enabled else None
        if v and v["mag_m"] < CORR_MAX_M:
            return lat + v["dlat"], lon + v["dlon"], True
        return lat, lon, False

    def snapshot(self, now):
        v = self.vector(now)
        why = None
        if not self.enabled:
            why = "off (config gps_correction)"
        elif self.pit is None:
            why = "no surveyed pit position in the map package"
        elif v is None:
            why = "no fresh pit radio fix (base_fix_age_s < 5) in 30 s"
        elif v["mag_m"] >= CORR_MAX_M:
            why = f"{v['mag_m']:.0f} m >= {CORR_MAX_M:.0f} m: not applied"
        return {"enabled": self.enabled, "applied": bool(self.enabled and v and v["mag_m"] < CORR_MAX_M),
                "vector": v, "pit": list(self.pit) if self.pit else None, "why_not": why}


def race_lights(go_in_s, n=5):
    """Red lights lit during the countdown: one more each second, all n in the last second.
    Mirrors raceLights() in static/app.js."""
    if go_in_s is None:
        return 0
    if go_in_s <= 0:
        return n
    return max(0, min(n, n + 1 - math.ceil(go_in_s - 1e-9)))


TRACK_MAX_POINTS = 200
TRACK_DEFAULT_WIDTH_M = 6.0


def parse_track(doc):
    """zones.json "track" (ZONES.md "Track route") -> {"points", "closed", "width_m", "length_m"} or
    None when absent or invalid (a bad track is ignored; the zones still work)."""
    t = doc.get("track") if isinstance(doc, dict) else None
    if not isinstance(t, dict):
        return None
    pts = t.get("points")
    if not isinstance(pts, list) or not 2 <= len(pts) <= TRACK_MAX_POINTS or not all(_pt(p) for p in pts):
        return None
    pts = [[float(p[0]), float(p[1])] for p in pts]
    if any(abs(p[0]) > 90 or abs(p[1]) > 180 for p in pts):
        return None
    w = t.get("width_m")
    w = float(w) if _num(w) and 1.0 <= w <= 50.0 else TRACK_DEFAULT_WIDTH_M
    closed = t.get("closed") is True and len(pts) >= 3
    ref = pts[0]
    en = [local_en(p[0], p[1], ref) for p in pts]
    segs = list(zip(en, en[1:] + ([en[0]] if closed else [])))
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in segs)
    if length < 5.0:
        return None
    return {"points": pts, "closed": closed, "width_m": w, "length_m": round(length, 1)}


def track_chevrons(track, spacing_m=15.0):
    """Direction marks along a track: [(lat, lon, heading_deg)] every spacing_m, following point
    order (the HUD draws the same thing; this is the testable reference)."""
    pts = track["points"]
    ref = pts[0]
    en = [local_en(p[0], p[1], ref) for p in pts]
    if track["closed"]:
        en = en + [en[0]]
    out, carry = [], spacing_m / 2.0
    for a, b in zip(en, en[1:]):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L <= 0:
            continue
        hd = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360.0
        d = carry
        while d <= L:
            f = d / L
            lat, lon = en_to_ll(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, ref)
            out.append((lat, lon, hd))
            d += spacing_m
        carry = d - L
    return out


def roll_item(rng):
    """One item-box roll: (name, secs, scale); secs None = empty box."""
    x = rng.random()
    acc = 0.0
    for name, p, secs, scale in ITEM_TABLE:
        acc += p
        if x < acc:
            return name, secs, scale
    return ITEM_TABLE[-1][0], None, None


def angle_diff_deg(a, b):
    """Shortest signed difference a - b in degrees, in (-180, 180]."""
    d = (a - b) % 360.0
    return d - 360.0 if d > 180.0 else d


def slew_heading(cur, target, dt, max_dps=HEADING_SLEW_DPS):
    """Heading-up map rotation: move cur towards target by at most max_dps * dt (shortest way).
    cur None = jump to target; target None = hold cur. Mirrors slewHeading() in static/app.js."""
    if target is None:
        return cur
    if cur is None:
        return target % 360.0
    d = angle_diff_deg(target, cur)
    step = max_dps * max(0.0, dt)
    if abs(d) <= step:
        return target % 360.0
    return (cur + math.copysign(step, d)) % 360.0


def _centre(z):
    """(lat, lon) centre of a boost/mud/item zone dict, or None."""
    if z.get("shape") == "circle" and _num(z.get("lat")) and _num(z.get("lon")):
        return float(z["lat"]), float(z["lon"])
    pts = z.get("points")
    if z.get("shape") == "polygon" and isinstance(pts, list) and len(pts) >= 3 and all(_pt(p) for p in pts):
        ref = (float(pts[0][0]), float(pts[0][1]))
        en = [local_en(float(p[0]), float(p[1]), ref) for p in pts]
        a = cx = cy = 0.0                          # area centroid (shoelace)
        for i in range(len(en)):
            x1, y1 = en[i]
            x2, y2 = en[(i + 1) % len(en)]
            c = x1 * y2 - x2 * y1
            a += c
            cx += (x1 + x2) * c
            cy += (y1 + y2) * c
        if abs(a) < 1e-9:
            e, n = sum(p[0] for p in en) / len(en), sum(p[1] for p in en) / len(en)
        else:
            e, n = cx / (3 * a), cy / (3 * a)
        return en_to_ll(e, n, ref)
    return None


def en_to_ll(e, n, ref):
    return (ref[0] + math.degrees(n / R_EARTH),
            ref[1] + math.degrees(e / (R_EARTH * math.cos(math.radians(ref[0])))))


def demo_zones(center, version=1):
    """Built-in demo course (SIMULATION) around center = (lat, lon): the simulator's 70 x 36 m loop
    with a start/finish line, a boost pad, mud and an item box. Used when the pit has no zones."""
    ll = lambda e, n: [round(v, 7) for v in en_to_ll(e, n, center)]     # noqa: E731
    b, it = ll(35, 0), ll(-35, 0)
    return {"_comment": "DEMO (simulation) course generated by the kart display", "version": version, "enabled": True,
            "zones": [
                {"id": "demo-b", "type": "boost", "name": "Boost pad", "shape": "circle", "lat": b[0], "lon": b[1],
                 "r_m": 9, "scale": 1.0, "secs": 5, "cooldown_s": 20},
                {"id": "demo-m", "type": "mud", "name": "Mud", "shape": "polygon",
                 "points": [ll(-12, -10), ll(12, -10), ll(12, -26), ll(-12, -26)], "scale": 0.5},
                {"id": "demo-i", "type": "item", "name": "Item box", "shape": "circle", "lat": it[0], "lon": it[1],
                 "r_m": 9, "cooldown_s": 15},
                {"id": "demo-lap", "type": "lapline", "name": "Start / finish", "shape": "line",
                 "a": ll(0, 10), "b": ll(0, 26), "min_lap_s": 20}]}


def demo_path(doc, center=None, mph=15.0, approach_m=12.0):
    """Closed driving loop for demo mode through the zones of doc: from just before the lap line,
    across it, then through the centre of every boost/mud/item zone in angular order around their
    centroid (travelling the same way round as the lap-line crossing), and back. If the loop would be
    shorter than min_lap_s at `mph` (+5 s) a detour point is pushed outwards so laps still count.
    Returns {"origin": (lat, lon), "points": [[lat, lon], ...] (closed: last joins first), "length_m"}."""
    track = parse_track(doc)
    if track is not None:                           # ZONES.md: demo drives along the track (closed loop)
        pts = [list(p) for p in track["points"]]
        ref = pts[0]
        en = [local_en(p[0], p[1], ref) for p in pts] + [local_en(pts[0][0], pts[0][1], ref)]
        L = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(en, en[1:]))
        return {"origin": tuple(ref), "points": pts, "length_m": round(L, 1), "source": "track"}
    zones = [z for z in (doc or {}).get("zones", []) if isinstance(z, dict)]
    centres = [c for c in (_centre(z) for z in zones if z.get("type") in ("boost", "mud", "item")) if c]
    lap = next((z for z in zones if z.get("type") == "lapline" and _pt(z.get("a")) and _pt(z.get("b"))), None)
    if lap is not None:
        origin = ((lap["a"][0] + lap["b"][0]) / 2.0, (lap["a"][1] + lap["b"][1]) / 2.0)
    elif centres:
        origin = centres[0]
    elif center is not None:
        origin = (float(center[0]), float(center[1]))
    else:
        raise ValueError("no zones and no centre for the demo loop")
    P = [local_en(c[0], c[1], origin) for c in centres]
    pts = []
    if lap is not None:
        a = local_en(lap["a"][0], lap["a"][1], origin)
        b = local_en(lap["b"][0], lap["b"][1], origin)
        ux, uy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(ux, uy) or 1.0
        nx, ny = -uy / L, ux / L                    # normal to the line
        if P:                                       # cross towards the side the zones are mostly on? no: away from
            mx = sum(p[0] for p in P) / len(P)       # their centroid, so the loop comes back round to the line
            my = sum(p[1] for p in P) / len(P)
            if mx * nx + my * ny > 0:
                nx, ny = -nx, -ny
        before = (-nx * approach_m, -ny * approach_m)
        after = (nx * approach_m, ny * approach_m)
    else:
        before = after = None
    if len(P) < 2:                                  # too few zones for a loop: add corners around them
        base = P[0] if P else (0.0, 0.0)
        extra = [(base[0] + 30, base[1] + 25), (base[0] - 30, base[1] + 25), (base[0] - 30, base[1] - 25),
                 (base[0] + 30, base[1] - 25)]
        if before is not None:                      # keep the corners off the lap line's side
            extra = [(after[0] * 3 + ex[0] * 0.0 + (ex[0] - base[0]), after[1] * 3 + (ex[1] - base[1]))
                     for ex in extra]
        P = P + extra
    cx = sum(p[0] for p in P) / len(P)
    cy = sum(p[1] for p in P) / len(P)
    ang = lambda p: math.atan2(p[1] - cy, p[0] - cx)     # noqa: E731
    if before is not None:
        # travel direction round the centroid implied by crossing before -> after
        cross = (before[0] - cx) * (after[1] - before[1]) - (before[1] - cy) * (after[0] - before[0])
        sign = 1.0 if cross > 0 else -1.0          # +1 = counter-clockwise
        a0 = ang(after)
        order = sorted(P, key=lambda p: ((ang(p) - a0) * sign) % (2 * math.pi))
        loop = [before, after] + order
    else:
        loop = sorted(P, key=ang)
    length = lambda pts_: sum(math.hypot(pts_[i][0] - pts_[i - 1][0], pts_[i][1] - pts_[i - 1][1])   # noqa: E731
                              for i in range(len(pts_)))
    if lap is not None:
        min_lap = float(lap["min_lap_s"]) if _num(lap.get("min_lap_s")) else MIN_LAP_DEFAULT_S
        need = (min_lap + 5.0) * mph * 0.44704
        L0 = length(loop)
        if L0 < need:                               # detour outwards between the last zone and the line
            p, q = loop[-1], loop[0]
            mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
            dx, dy = mx - cx, my - cy
            d = math.hypot(dx, dy) or 1.0
            push = (need - L0) / 2.0 + 5.0
            loop.append((mx + dx / d * push, my + dy / d * push))
    pts = [list(en_to_ll(e, n, origin)) for e, n in loop]
    return {"origin": origin, "points": pts, "length_m": round(length(loop), 1), "source": "zones"}


class GameEngine:
    """Zone triggers + lap timing for this kart. Callers hold the app lock.

    on_self(msg, now) / tick(now) / set_zones(doc, now) return a list of records;
    every record is logged by the caller, and records with "ev": "cmd" carry the
    "line" to send to the Heltec. Rules: ZONES.md. Extra kart-side rules:
    no zone commands while the kart reports STOPPED (a boost entered while stopped
    is used up, not saved for later); mud wins over boost when they overlap."""

    def __init__(self, local_enabled=True, gps_correction=False, rng=None):
        self.local_enabled = bool(local_enabled)
        self.rng = rng or random.Random()
        self.race = {"hold": False, "go_at": None, "secs": None, "rx": None, "go_mono": None,
                     "go_confirmed": False, "how": None, "go_utc": None,
                     "finished": False, "position": None, "finish_mono": None, "finish_lap": None,
                     # running position from the pit beacon (race firmware 2026-11-06 20:30+)
                     "pos": 0, "state": 0, "pos_changed_wall": None, "pos_changed_mono": None,
                     "pos_prev": 0, "last_pos": 0, "position_source": None}
        self.filter = GpsFilter()
        self.corr = BaseCorrection(gps_correction)
        self.gps_line_mono = None   # last {"t":"gps"} line
        self.gps_lines = 0
        self.pos = None             # last filtered sample (for the HUD)
        self.gps_hz = None
        self.track = None
        self.doc = None
        self.version = None
        self.enabled = False
        self.areas, self.lapline, self.ignored = [], None, []
        self.inside = {}            # zone id -> entered mono
        self.zst = {}               # zone id -> {"fired": bool, "cool_until": mono}
        self.next_mud = None
        self.prev_fix = None        # (lat, lon, mono)
        self.last_fix_mono = None
        self.stopped = None
        self.scale = None
        self.mode_req = None
        self.override = None        # {"kind", "scale", "until"} as last sent
        self.events = deque(maxlen=12)
        self.seq = 0
        self.cmds_sent = 0
        self.last_result = None     # {"result", "result_name", "mono"} of the last boost echo
        self._reset_lap()

    # -- state ------------------------------------------------------------------
    def _reset_lap(self):
        self.lap = {"count": 0, "running": False, "start": None, "last_cross": None,
                    "last_s": None, "best_s": None, "times": []}
        self.lap_armed = True       # False after a crossing until the kart is LAP_REARM_M from the line

    def active(self):
        return self.local_enabled and self.enabled and (bool(self.areas) or self.lapline is not None)

    def off_reason(self):
        if not self.local_enabled:
            return "off in this kart's config"
        if self.doc is None:
            return "no zones from the pit"
        if not self.enabled:
            return "off at the pit"
        if not self.areas and self.lapline is None:
            return "no zones"
        return None

    def _rec(self, recs, ev, now, **kw):
        self.seq += 1
        r = {"ev": ev, "seq": self.seq, **kw}
        recs.append(r)
        if ev != "cmd":
            self.events.append(dict(r, mono=now))
        return r

    def _cmd(self, recs, now, zone, kind, secs, scale):
        line = boost_line(secs, scale)
        self.cmds_sent += 1
        self.override = {"kind": kind, "scale": clamp_scale(scale), "until": now + int(secs), "zone": zone.id}
        self._rec(recs, "cmd", now, line=line, zone=zone.id, effect=kind)
        if kind == "boost":
            self._rec(recs, "boost", now, zone=zone.id, name=zone.name, scale=clamp_scale(scale), secs=int(secs))

    # -- zones file ---------------------------------------------------------------
    def set_zones(self, doc, now):
        """New zones document (or None = no file). Same document again = no-op."""
        recs = []
        if doc == self.doc:
            return recs
        old_lap = self.lapline.raw if self.lapline else None
        was_active = self.active()
        self.doc = doc
        self.version = doc.get("version") if isinstance(doc, dict) else None
        self.enabled, self.areas, self.lapline, self.ignored = parse_zones(doc)
        self.track = parse_track(doc)
        if isinstance(doc, dict) and doc.get("track") is not None and self.track is None:
            self.ignored.append({"id": "track", "reason": "invalid track (2-200 [lat, lon] points)"})
        ids = {z.id: z for z in self.areas}
        for zid in list(self.inside):
            if zid not in ids or not self.active():
                del self.inside[zid]
                self._rec(recs, "zone_exit", now, zone=zid, why="zones changed")
        self.zst = {k: v for k, v in self.zst.items() if k in ids}
        if not self.active() or (self.lapline.raw if self.lapline else None) != old_lap:
            if self.lap["running"] or self.lap["count"]:
                self._rec(recs, "lap_reset", now, why="game off" if not self.active() else "lap line changed")
            self._reset_lap()
        if not self.active():
            self.prev_fix = None
            self.next_mud = None
        if self.active() != was_active:
            self._rec(recs, "game_on" if self.active() else "game_off", now, version=self.version,
                      why=self.off_reason())
        else:
            self._rec(recs, "zones_updated", now, version=self.version)
        return recs

    # -- telemetry ----------------------------------------------------------------
    def on_self(self, msg, now, wall=None):
        pos_recs = self.race_position(msg.get("race_pos"), msg.get("race_state"), now, wall)
        return pos_recs + self._on_self(msg, now)

    def _on_self(self, msg, now):
        self.stopped = msg.get("stopped")
        self.mode_req = msg.get("mode_req")
        self.scale = msg.get("scale") if msg.get("vesc_link") is True else None
        if _num(msg.get("gps_hz")):
            self.gps_hz = msg.get("gps_hz")
        self.corr.feed(msg, now)
        recs = []
        if self.gps_line_mono is not None and now - self.gps_line_mono <= GPS_LINE_FRESH_S:
            return self._timeouts(now, recs) if self.active() else recs     # 5 Hz gps lines carry the position
        lat, lon = msg.get("lat"), msg.get("lon")
        if msg.get("fix") is not True or not _num(lat) or not _num(lon):
            return self._timeouts(now, recs) if self.active() else recs
        return self._sample(float(lat), float(lon), msg.get("gps_kph"), msg.get("heading"),
                            msg.get("hdop"), msg.get("sats"), now, "self", recs)

    def on_gps(self, msg, now):
        """{"t":"gps"} line (one per fix, up to 5 Hz): the preferred position source."""
        recs = []
        lat, lon = msg.get("lat"), msg.get("lon")
        if not _num(lat) or not _num(lon):
            return recs
        self.gps_line_mono = now
        self.gps_lines += 1
        return self._sample(float(lat), float(lon), msg.get("kph"), msg.get("heading"),
                            msg.get("hdop"), msg.get("sats"), now, "gps", recs)

    def set_pit(self, pit):
        self.corr.set_pit(pit)

    def _sample(self, lat, lon, kph, heading, hdop, sats, now, source, recs):
        lat, lon, corrected = self.corr.apply(lat, lon, now)
        self.pos = self.filter.feed(lat, lon, kph, heading, hdop, sats, now, source)
        self.pos["corrected"] = corrected
        if not self.active():
            return recs
        if not self.pos["usable"]:
            return self._timeouts(now, recs)
        if self.pos["reset"]:
            self.prev_fix = None
        self._evaluate(lat, lon, now, recs)
        return recs

    def tick(self, now):
        recs = []
        r = self.race
        if r["hold"] and r["go_at"] is not None and now >= r["go_at"] + RACE_GO_GRACE_S:
            self._go(r["go_at"], now, "clock", recs)
        if not self.active():
            return recs
        self._timeouts(now, recs)
        self._mud(now, recs, entered=False)
        return recs

    def _timeouts(self, now, recs):
        if self.last_fix_mono is not None and now - self.last_fix_mono > NO_FIX_EXIT_S and (self.inside or self.prev_fix):
            for zid in list(self.inside):
                del self.inside[zid]
                self._exit_state(zid, now)
                self._rec(recs, "zone_exit", now, zone=zid, why="no GPS fix for 5 s")
            self.prev_fix = None
            self.next_mud = None
        return recs

    def _exit_state(self, zid, now):
        st = self.zst.setdefault(zid, {"fired": False, "cool_until": None})
        z = next((a for a in self.areas if a.id == zid), None)
        if st["fired"] and z is not None and z.type in ("boost", "item"):
            st["fired"] = False
            st["cool_until"] = now + z.cooldown_s

    def _evaluate(self, lat, lon, now, recs):
        entered = []
        prev = self.prev_fix if self.prev_fix is not None and now - self.prev_fix[2] <= SWEEP_MAX_GAP_S else None
        for z in self.areas:
            d = z.outside_m(lat, lon)
            if z.id not in self.inside:
                # inside now, or the path since the previous fix went through it (fast kart, 2 Hz fallback,
                # small pad): an entry either way
                if d <= 0 or (prev is not None and z.swept(prev[0], prev[1], lat, lon)):
                    self.inside[z.id] = now
                    entered.append(z)
                    self._rec(recs, "zone_enter", now, zone=z.id, type=z.type, name=z.name,
                              **({} if d <= 0 else {"swept": True}))
            elif d > ZONE_EXIT_HYST_M:
                del self.inside[z.id]
                self._exit_state(z.id, now)
                self._rec(recs, "zone_exit", now, zone=z.id, type=z.type, name=z.name)
        in_mud = any(z.type == "mud" for z in self.areas if z.id in self.inside)
        fire = []                   # (zone, kind, secs, scale): at most ONE goes to the Heltec per sample
        for z in entered:
            if z.type not in ("boost", "item"):
                continue
            st = self.zst.setdefault(z.id, {"fired": False, "cool_until": None})
            if self.stopped is True:
                self._rec(recs, "suppressed", now, zone=z.id, why="kart STOPPED")
            elif self.race["hold"]:
                self._rec(recs, "suppressed", now, zone=z.id, why="grid hold")
            elif in_mud:
                self._rec(recs, "suppressed", now, zone=z.id, why="inside mud")
            elif st["cool_until"] is not None and now < st["cool_until"]:
                self._rec(recs, "suppressed", now, zone=z.id, why=f"cooldown {st['cool_until'] - now:.0f} s")
            elif z.type == "item":
                st["fired"] = True
                name, secs, scale = roll_item(self.rng)
                self._rec(recs, "item", now, zone=z.id, name=z.name, item=name)
                if secs is not None:
                    fire.append((z, "mud" if name == "mud" else "boost", secs, scale))
            else:
                st["fired"] = True
                fire.append((z, "boost", z.secs, z.scale))
        if fire:
            # overlapping boost pad + item box entered on the same fix: both are used up (cooldowns, item
            # roulette), but only the strongest effect is sent (highest scale, then longest), so two
            # commands never race each other in the Heltec
            best = max(fire, key=lambda f: (clamp_scale(f[3]), int(f[2])))
            for f in fire:
                if f is not best:
                    self._rec(recs, "suppressed", now, zone=f[0].id, why=f"overlaps {best[0].id}")
            self._cmd(recs, now, *best)
        self._mud(now, recs, entered=any(z.type == "mud" for z in entered))
        if self.lapline is not None:
            if not self.lap_armed and self.lapline.line_dist_m(lat, lon) > LAP_REARM_M:
                self.lap_armed = True
            if self.prev_fix is not None and now - self.prev_fix[2] <= NO_FIX_EXIT_S:
                t = self.lapline.crossing(self.prev_fix[0], self.prev_fix[1], lat, lon)
                if t is not None and t > 0.0:       # a fix exactly ON the line counts once (t == 1), not twice
                    self._cross(self.prev_fix[2] + t * (now - self.prev_fix[2]), now, recs)
        self.prev_fix = (lat, lon, now)
        self.last_fix_mono = now

    def _mud(self, now, recs, entered):
        muds = [z for z in self.areas if z.type == "mud" and z.id in self.inside]
        if not muds:
            self.next_mud = None
            return
        if not entered and self.next_mud is not None and now < self.next_mud:
            return
        if self.stopped is True or self.race["hold"]:
            return          # stays due: resumes on the first tick after START / GO if still inside
        z = min(muds, key=lambda m: m.scale)
        self._cmd(recs, now, z, "mud", MUD_SECS, z.scale)
        self.next_mud = now + MUD_REPEAT_S
        if entered:
            self._rec(recs, "mud", now, zone=z.id, name=z.name, scale=z.scale)

    def _cross(self, t_cross, now, recs):
        L = self.lapline
        lap = self.lap
        go = self.race["go_mono"]
        if self.race["finished"]:
            self._rec(recs, "lap_ignored", now, zone=L.id, why="finished")    # laps stop counting after the flag
            return
        if self.race["hold"] or (go is not None and t_cross < go):
            self._rec(recs, "lap_ignored", now, zone=L.id, why="before GO")
            return
        if not self.lap_armed:
            # still near the line since the last crossing: GPS jitter (or a kart sitting on the line).
            # Before the first full lap the latest crossing is the real start (the kart leaves forward).
            if lap["count"] == 0 and lap["running"]:
                lap.update(start=t_cross, last_cross=t_cross)
                self._rec(recs, "lap_start", now, zone=L.id, restart=True)
            else:
                self._rec(recs, "lap_ignored", now, zone=L.id, why="near the line (not re-armed)")
            return
        self.lap_armed = False
        if lap["last_cross"] is None:
            lap.update(running=True, start=t_cross, last_cross=t_cross)
            self._rec(recs, "lap_start", now, zone=L.id)
            return
        since = t_cross - lap["last_cross"]
        if since < L.min_lap_s:
            self._rec(recs, "lap_ignored", now, zone=L.id, since_s=round(since, 2), min_lap_s=L.min_lap_s)
            return
        lap_s = round(t_cross - lap["start"], 2)
        best = lap["best_s"] is None or lap_s < lap["best_s"]
        lap["count"] += 1
        lap["last_s"] = lap_s
        if best:
            lap["best_s"] = lap_s
        lap["times"] = (lap["times"] + [lap_s])[-10:]
        lap.update(start=t_cross, last_cross=t_cross, running=True)
        self._rec(recs, "lap", now, zone=L.id, lap=lap["count"], lap_s=lap_s, best=best)

    # -- race start (ZONES.md "Race start") -------------------------------------------
    def race_position(self, pos, state, now, wall=None, recs=None):
        """Running position P (0 = none, 1-8) and race state from the self line / position event.
        A change records a "race_position" event: dir "up" (better), "down" (worse), "new", "gone"."""
        recs = [] if recs is None else recs
        r = self.race
        prev_state = r["state"]
        if _num(state) and 0 <= int(state) <= 3:
            r["state"] = int(state)
            if r["state"] != prev_state:
                self._rec(recs, "race_state", now, state=r["state"], prev=prev_state)
        p = int(pos) if _num(pos) and 0 <= int(pos) <= 8 else None
        if p is not None and p != r["pos"]:
            prev = r["pos"]
            d = "new" if prev == 0 else "gone" if p == 0 else "up" if p < prev else "down"
            r.update(pos=p, pos_prev=prev, pos_changed_mono=now, pos_changed_wall=wall)
            if p:
                r["last_pos"] = p
            self._rec(recs, "race_position", now, pos=p, prev=prev, dir=d, state=r["state"])
        # The pit beacon's race state is a second route to the flag (STOP RACE sends state 3 with the
        # final positions; the per-kart `race 100+P` finish command may arrive later or be lost):
        # state 3 = finished at once with the beacon's position; a new countdown (1) clears it.
        if r["state"] == 3 and prev_state != 3:
            if not r["finished"]:
                self._finish(r["pos"] or r["last_pos"], "beacon_state", now, recs)
            elif not r["position"] and (r["pos"] or r["last_pos"]):
                r.update(position=r["pos"] or r["last_pos"], position_source="beacon_state")
        elif r["state"] == 1 and prev_state != 1 and r["finished"]:
            r.update(finished=False, position=None, finish_mono=None, finish_lap=None, position_source=None)
        return recs

    def _finish(self, pos, src, now, recs):
        r, lap = self.race, self.lap
        r.update(hold=False, finished=True, position=pos, finish_mono=now, position_source=src if pos else None,
                 finish_lap={"laps": lap["count"], "best_s": lap["best_s"], "last_s": lap["last_s"]})
        lap.update(running=False)
        self._rec(recs, "race_finish", now, position=pos, laps=lap["count"], best_s=lap["best_s"], source=src)

    def on_race(self, msg, now, wall=None):
        """{"t":"race","event":"countdown","go_in_ms":N,"secs":S,"go_utc":U} / {"event":"go"}.
        GO = arrival + go_in_ms (the overlay and the lap reset do not wait for the go line,
        which only confirms it). Laps count from the first crossing after GO."""
        recs = []
        r = self.race
        ev = msg.get("event")
        if ev == "countdown":
            go_in = msg.get("go_in_ms")
            go_in = max(0.0, float(go_in) / 1000.0) if _num(go_in) else float(msg.get("secs") or 5)
            r.update(hold=True, go_at=now + go_in, secs=msg.get("secs"), rx=now, go_confirmed=False, how=None,
                     go_utc=msg.get("go_utc"), finished=False, position=None, finish_mono=None, finish_lap=None,
                     position_source=None)
            self._rec(recs, "race_countdown", now, go_in_s=round(go_in, 2), secs=msg.get("secs"))
            if go_in <= 0:
                self._go(now, now, "clock", recs)
        elif ev == "go":
            if r["hold"]:
                self._go(now, now, "line", recs)
            elif r["go_mono"] is None or now - r["go_mono"] > GO_LINE_STANDALONE_S:
                # no countdown seen (display restarted during the hold, or the line was lost): this IS the
                # start; a finish from the previous race is cleared like a countdown would
                r.update(finished=False, position=None, finish_mono=None, finish_lap=None, position_source=None)
                self._go(now, now, "line", recs)
            r["go_confirmed"] = True
            self._rec(recs, "race_go_line", now)
        elif ev == "position":
            self.race_position(msg.get("position"), msg.get("state"), now, wall, recs)
        elif ev == "finish":
            # the pit's race finish (arg 0 / 100 + position): the firmware releases any hold; the HUD
            # shows the FINISH takeover and the laps freeze until the next countdown
            pos = msg.get("position")
            pos = int(pos) if _num(pos) and 0 < int(pos) <= 8 else 0
            if r["finished"] and r["position_source"] == "beacon_state":
                # the beacon's state 3 got here first: the takeover is already showing; only refine
                if pos and pos != r["position"]:
                    r.update(position=pos, position_source="finish")
                self._rec(recs, "race_finish_cmd", now, position=pos)
                return recs
            src = "finish" if pos else None
            if not pos and r["last_pos"]:                # firmware sent no position: last beacon position
                pos, src = r["last_pos"], "beacon"
            self._finish(pos, src, now, recs)
            r["position_source"] = src
        return recs

    def _go(self, t_go, now, how, recs):
        r = self.race
        r.update(hold=False, go_mono=t_go, how=how)
        if self.lap["running"] or self.lap["count"]:
            self._rec(recs, "lap_reset", now, why="race GO")
        self._reset_lap()
        self._rec(recs, "race_go", now, how=how)

    # -- survive a server restart (same boot) / a demo ------------------------------------
    def export_state(self, now):
        """Lap + race timing as plain JSON. Monotonic times are kept as they are: CLOCK_MONOTONIC is
        system-wide, so they stay valid for another process in the same boot (checked by boot_id).
        Not exported (rebuilt from live data): zone inside/cooldowns, overrides, positions, SLOW."""
        r = self.race
        return {"v": 1, "boot_id": boot_id(), "saved_mono": now,
                "lapline": self.lapline.raw if self.lapline else None,
                "lap": dict(self.lap, times=list(self.lap["times"])),
                "race": {k: r[k] for k in ("hold", "go_at", "secs", "go_mono", "go_confirmed", "how", "finished",
                                           "position", "finish_mono", "finish_lap", "position_source", "last_pos")}}

    def import_state(self, doc, now):
        """Restore export_state() after the zones are loaded. Only within the same boot, within
        GAME_STATE_MAX_AGE_S, and with the very same lap line; otherwise timing starts fresh."""
        recs = []
        try:
            ok = (isinstance(doc, dict) and doc.get("v") == 1 and doc.get("boot_id") == boot_id()
                  and _num(doc.get("saved_mono")) and 0 <= now - doc["saved_mono"] <= GAME_STATE_MAX_AGE_S
                  and self.lapline is not None and doc.get("lapline") == self.lapline.raw and self.active())
            if not ok:
                return recs
            lap, race = doc["lap"], doc["race"]
            if race.get("hold") and not (_num(race.get("go_at")) and race["go_at"] > now):
                race["hold"] = False             # the countdown ended while we were down: GO happened
                race["go_mono"] = race.get("go_at")
            self.lap = {k: lap.get(k) for k in ("count", "running", "start", "last_cross", "last_s", "best_s")}
            self.lap["count"] = int(self.lap["count"] or 0)
            self.lap["times"] = list(lap.get("times") or [])[-10:]
            self.lap_armed = False                # the kart may be sitting on the line right now
            self.race.update({k: v for k, v in race.items() if k in self.race})
            self.prev_fix = None
            self._rec(recs, "zones_lap_restored", now, laps=self.lap["count"], best_s=self.lap["best_s"],
                      running=self.lap["running"], finished=self.race["finished"],
                      age_s=round(now - doc["saved_mono"], 1))
        except (KeyError, TypeError, ValueError, AttributeError):
            return []
        return recs

    def grid_hold(self):
        return bool(self.race["hold"])

    def on_cmd(self, msg, now):
        """A {"t":"cmd"} line: STOP/START end a grid hold (the firmware clears it);
        remember the result of our last boost (src 254, cmd 6)."""
        if msg.get("cmd") in (CMD_CODES["stop"], CMD_CODES["start"]) and msg.get("result") in (1, 2, 5) \
                and self.race["hold"]:
            self.race.update(hold=False, how="stop" if msg.get("cmd") == CMD_CODES["stop"] else "start")
            self._rec([], "race_released", now, by=CMD_NAMES.get(msg.get("cmd")))
            if msg.get("cmd") == CMD_CODES["start"]:
                self.race["go_mono"] = now
                self._reset_lap()
        if msg.get("src") != SRC_USB or msg.get("cmd") != CMD_CODES["boost"]:
            return None
        res = msg.get("result")
        self.last_result = {"result": res, "result_name": CMD_RESULT_NAMES.get(res), "arg": msg.get("arg"), "mono": now}
        return self.last_result

    # -- display ------------------------------------------------------------------
    def meter(self, now):
        """Live current scale vs the requested mode's scale: boost above, mud below."""
        ms = MODE_SCALES.get(self.mode_req)
        sc = self.scale
        ov = self.override if self.override and now < self.override["until"] else None
        if self.stopped is True:
            kind = "stopped"
        elif self.race["hold"]:
            kind = "grid"                  # held at zero power for the start: not MUD, not STOPPED
        elif sc is None or ms is None:
            kind = ov["kind"] if ov else "unknown"
        elif sc > ms + SCALE_TOL or (ov and ov["kind"] == "boost" and sc >= ms - SCALE_TOL):
            kind = "boost"
        elif sc < ms - SCALE_TOL:
            kind = "mud"
        else:
            kind = "normal"
        return {"scale": sc, "mode_scale": round(ms, 3) if ms is not None else None, "kind": kind,
                "override": {"kind": ov["kind"], "scale": ov["scale"], "left_s": round(ov["until"] - now, 1)} if ov else None}

    def race_snapshot(self, now):
        r = self.race
        out = {"hold": r["hold"], "secs": r["secs"], "go_confirmed": r["go_confirmed"], "how": r["how"],
               "go_in_s": round(r["go_at"] - now, 2) if r["hold"] and r["go_at"] is not None else None,
               "go_age_s": round(now - r["go_mono"], 2) if r["go_mono"] is not None else None}
        out["lights"] = race_lights(out["go_in_s"]) if r["hold"] else (5 if out["go_age_s"] is not None and out["go_age_s"] < 0 else 0)
        out["finished"] = r["finished"]
        out["position"] = r["position"]
        out["finish_age_s"] = round(now - r["finish_mono"], 2) if r["finish_mono"] is not None else None
        out["finish_lap"] = r["finish_lap"]
        out["position_source"] = r["position_source"]
        out["pos"] = r["pos"]
        # state: the beacon's race state, except that a finish this kart already has (firmware finish
        # command or beacon state 3) reads "finished" until the beacon says idle or a new countdown
        st = 3 if r["finished"] and r["state"] in (2, 3) else r["state"]
        out["state"] = st
        out["state_name"] = RACE_STATES.get(st)
        out["beacon_state"] = r["state"]
        out["pos_changed_wall"] = r["pos_changed_wall"]
        out["pos_prev"] = r["pos_prev"]
        out["pos_changed_age_s"] = round(now - r["pos_changed_mono"], 2) if r["pos_changed_mono"] is not None else None
        return out

    def snapshot(self, now):
        lap = self.lap
        return {
            "on": self.active(),
            "off_reason": self.off_reason(),
            "version": self.version,
            "inside": sorted(self.inside),
            "ignored": list(self.ignored),
            "lap": {"count": lap["count"], "running": lap["running"],
                    "current_s": round(now - lap["start"], 1) if lap["running"] and lap["start"] is not None else None,
                    "last_s": lap["last_s"], "best_s": lap["best_s"], "times": list(lap["times"])},
            "meter": self.meter(now),
            "events": [{k: v for k, v in e.items() if k != "mono"} | {"age_s": _age(e["mono"], now)}
                       for e in list(self.events)[-8:]],
            "cmds_sent": self.cmds_sent,
            "race": self.race_snapshot(now),
            "track": self.track,
            "pos": ({k: v for k, v in self.pos.items() if k != "mono"} | {"age_s": _age(self.pos["mono"], now)})
                   if self.pos else None,
            "gps_source": "gps" if self.gps_line_mono is not None and now - self.gps_line_mono <= GPS_LINE_FRESH_S
                          else "self",
            "gps_lines": self.gps_lines,
            "gps_hz": self.gps_hz,
            "correction": self.corr.snapshot(now),
            "last_result": ({k: v for k, v in self.last_result.items() if k != "mono"}
                            | {"age_s": _age(self.last_result["mono"], now)}) if self.last_result else None,
        }
