"""Fleet state, derived values and command delivery for the GoKart pit station.

Pure logic: no sockets, no threads, no serial. Every function that cares about
time takes `now` (a time.monotonic() value) so tests can drive the clock.

Contract: firmware/base-tbeam-s3/SERIAL_PROTOCOL.md (base-0.1.0).
Conventions: docs/requirements.md "Agreed telemetry fields" -- missing values
are None (JSON null), never a fabricated zero; freshness is tracked per data
group (link, VESC, GPS) with local monotonic time.
"""
from __future__ import annotations

import math
from collections import deque

KART_IDS = tuple(range(1, 9))
ALL_KARTS = 255

ONLINE_S = 5.0     # tele heard within this many seconds -> online (matches stat.karts_online)
STALE_S = 15.0     # within this -> stale; beyond -> offline

# Fixed per-kart colours shared by every client (proposed; no owner decision yet).
KART_COLORS = {
    1: "#e6194b", 2: "#3cb44b", 3: "#ffe119", 4: "#4363d8",
    5: "#f58231", 6: "#911eb4", 7: "#42d4f4", 8: "#f032e6",
}

MODE_NAMES = {0: "ECO", 1: "NORMAL", 2: "SPORT"}
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

    def ingest_tele(self, msg, now, wall):
        seq = msg.get("seq")
        prev = self.status.get("seq")
        relayed = msg.get("relayed") is True
        if relayed and seq == prev and self.last_tele_mono is not None and now - self.last_tele_mono < 0.4:
            self.relay_dupes += 1       # repeater copy of a packet the pit heard directly 25 ms earlier
            return
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
                self.trail.append((now, lat, lon))

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
            "trail": [[lat, lon] for (t, lat, lon) in self.trail if now - t <= 30.0],
        }


class Fleet:
    """Authoritative fleet + race state. Callers hold the app lock."""

    def __init__(self, fallback_origin=None):
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

    # -- ingest ------------------------------------------------------------
    def ingest(self, msg, now, wall):
        """Apply one parsed contract line. Returns the kart id for tele lines, else None."""
        t = msg.get("t")
        if t == "tele":
            k = msg.get("kart")
            if k not in self.karts:
                self.rx_bad += 1
                return None
            self.karts[k].ingest_tele(msg, now, wall)
            self._maybe_origin("kart", msg.get("lat"), msg.get("lon"), msg.get("fix"))
            return k
        if t == "base":
            self.base = dict(msg)
            self.base_mono = now
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

    def current_origin(self):
        if self.origin:
            return self.origin
        if self.fallback_origin:
            return dict(self.fallback_origin, source="config")
        return None

    def is_online(self, k, now):
        return link_status(self.karts[k].last_tele_mono, now) == "online"

    def snapshot(self, now):
        base_age = _age(self.base_mono, now)
        return {
            "karts": [self.karts[k].snapshot(now) for k in KART_IDS],
            "base": dict(self.base, age_s=base_age) if self.base else None,
            "stat": dict(self.stat, age_s=_age(self.stat_mono, now)) if self.stat else None,
            "repeater": dict(self.repeater, age_s=_age(self.repeater_mono, now)) if self.repeater else None,
            "simulation": self.simulation,
            "race": dict(self.race),
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
VALID_CMDS = {"mode", "stop", "start", "ping", "slow"}


class CommandError(ValueError):
    pass


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
        self.id = None                  # stable id independent of seq reuse

    def wire(self, kart=None):
        obj = {"cmd": self.cmd, "kart": self.kart if kart is None else kart, "seq": self.seq}
        if self.cmd == "mode":
            obj["arg"] = self.arg
        if self.cmd != "ping":
            obj["ttl"] = self.ttl
        return obj

    def to_dict(self):
        return {
            "id": self.id, "seq": self.seq, "cmd": self.cmd, "kart": self.kart, "arg": self.arg,
            "arg_name": (MODE_NAMES.get(self.arg) if self.cmd == "mode" else (f"{self.arg or 5} mph" if self.cmd == "slow" else None)),
            "ttl": self.ttl, "source": self.source, "state": self.state,
            "created_wall": self.created_wall, "first_tx_wall": self.first_tx_wall,
            "tx_count": self.tx_count, "radio_sent": self.radio_sent,
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

    # -- public API ----------------------------------------------------------
    def submit(self, cmd, kart, arg=None, source="?", now=0.0, wall=None):
        cmd = str(cmd).lower()
        if cmd not in VALID_CMDS:
            raise CommandError(f"unknown cmd {cmd!r}")
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
        else:
            arg = None
        c = Command(None, cmd, kart, arg, self.ttl, source, now, wall)
        self._ids += 1
        c.id = self._ids
        self.history.append(c)
        if cmd == "slow":
            arg = 5 if arg in (None, "") else int(arg)
            if not 1 <= arg <= 60:
                raise CommandError("slow: mph must be 1-60")
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
        cmds = [c.to_dict() for c in reversed(self.history)]
        last_fleet = next((c for c in cmds if c["kart"] == ALL_KARTS), None)
        per_kart = {}
        for c in reversed(self.history):
            for k in c.addressed:
                if str(k) not in per_kart and c.outcomes.get(k) not in ("cancelled",):
                    per_kart[str(k)] = {"id": c.id, "seq": c.seq, "cmd": c.cmd, "arg": c.arg,
                                        "outcome": c.outcomes[k]}
        return {"recent": cmds[:15], "last_fleet": last_fleet, "per_kart": per_kart,
                "next_seq": self.next_seq, "queued": len(self.queue)}

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
