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
CMD_CODES = {"mode": 1, "stop": 2, "start": 3, "ping": 4, "slow": 5}
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
                 "base_rssi", "rssi", "snr", "rx_ms")


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
        self.gps_now = {"fix": msg.get("fix"), "sats": msg.get("sats"), "gps_age_s": msg.get("gps_age_s")}
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
