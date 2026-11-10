#!/usr/bin/env python3
"""GoKart Control pit station server.

    python3 pit_server.py                      # auto-find the T-Beam (/dev/tbeam, ttyACM*, cu.usbmodem*)
    python3 pit_server.py --port /dev/ttys004  # explicit serial path (e.g. simulator pty)
    python3 pit_server.py --tcp 127.0.0.1:7878 # simulator over TCP

Standard library + pyserial only. Serves the operator UI (/), the touch kiosk
(/kiosk), the phone page (/m, also /mobile), a 4 Hz Server-Sent Events state stream (/events) and the JSON API.
Contract with the T-Beam: firmware/base-tbeam-s3/SERIAL_PROTOCOL.md.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import random
import secrets
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import health  # noqa: E402
import pitcore  # noqa: E402
from link import Link  # noqa: E402

VERSION = "pit-0.1.0"
PIT_ROOT = os.path.dirname(HERE)
STATIC_DIR = os.path.join(HERE, "static")
DEFAULTS = {
    "password": "",              # shared operator password; empty = only localhost allowed
    "gps_correction": False,     # apply the pit radio's measured GPS error (vs map.json "pit") to kart positions
    "bind": "0.0.0.0",
    "http_port": 8080,
    "trust_localhost": True,     # the kiosk browser on the Pi itself skips the login
    "serial_port": None,         # null = auto-find
    "tcp": None,                 # "host:port" for the simulator
    "cmd_ttl_s": pitcore.DEFAULT_TTL_S,
    "origin": None,              # {"lat":..,"lon":..} last-resort map origin
    "log_dir": None,             # default <pit>/logs
    "health_period_s": health.PERIOD_S,   # vcgencmd get_throttled + CPU temp sample period (Pi only)
    "health_file": None,         # tests/dev: read the get_throttled text from this file instead of vcgencmd
    "log_max_file_mb": 64,       # a session log longer than this continues in a new -partN file
    "log_max_total_mb": 1024,    # oldest session logs are deleted beyond this total (the current one never)
    "sse_max_clients": 24,       # live pages at once; more get HTTP 503 and retry
}
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".svg": "image/svg+xml", ".ico": "image/x-icon",
        ".webmanifest": "application/manifest+json"}
# Pages a login may return to (?next=...); anything else goes to "/".
PAGES = {"/": "index.html", "/index.html": "index.html", "/kiosk": "kiosk.html",
         "/m": "mobile.html", "/mobile": "mobile.html"}
COOKIE = "gkpit"
SSE_PERIOD_S = 0.25
SSE_WRITE_TIMEOUT_S = 10.0   # a page that stops reading for this long is dropped (its thread ends)
SSE_ZONES_EVERY_S = 30.0     # frames carry the zones document when it changes and at least this often
RACE_POS_PERIOD_S = 2.0      # {"t":"race"} repeat while a race runs (the base keeps it 30 s)
RACE_FINISH_REPEATS = (0.0, 3.0, 6.0)   # finished: sent 3 times over 6 s, then left to the base
RACE_IDLE_REPEATS = (0.0, 2.0)
# Fleet report rate (firmware GK_CMD_RATE): a 50 ms slot cannot hold a 23 ms packet plus its 23 ms
# repeater copy, so while a repeater is on the air every kart reports at 1 Hz.
RATE_REPEAT_S = 30.0         # "rate 1" to all karts this often while a repeater is heard
REPEATER_FRESH_S = 60.0      # repeater heartbeat heard within this -> repeater in use
REPEATER_GONE_S = 120.0      # no repeater for this long -> "rate 2" once          # NEW RACE / abort: state 0 twice (one lost line must not leave "3")


def wall_iso(ts=None):
    return datetime.fromtimestamp(time.time() if ts is None else ts, timezone.utc) \
        .isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _read_boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()[:36] or None
    except OSError:
        return None


def _file_reader(path):
    def read():
        with open(path) as f:
            return f.read()
    return read


# ---------------------------------------------------------------------------
class SessionLog:
    """JSONL log, one new file per server start. Every serial line in both
    directions plus every operator action and event, each timestamped."""

    def __init__(self, log_dir, max_file_mb=64, max_total_mb=1024):
        os.makedirs(log_dir, exist_ok=True)
        self.dir = log_dir
        self.base = "session-" + time.strftime("%Y%m%d-%H%M%S")
        self.max_file = int(max(1, float(max_file_mb)) * 1024 * 1024)
        self.max_total = int(max(8, float(max_total_mb)) * 1024 * 1024)
        self.part = 1
        self.path = os.path.join(log_dir, self.base + ".jsonl")
        self._f = open(self.path, "a", buffering=1, encoding="utf-8")
        self._size = self._f.tell()
        self._lock = threading.Lock()
        self.recent_all = deque(maxlen=500)
        self.recent_events = deque(maxlen=300)   # everything except raw rx/tx lines
        self.pruned = self._prune()

    def write(self, kind, **fields):
        rec = {"ts": wall_iso(), "mono": round(time.monotonic(), 3), "kind": kind}
        rec.update(fields)
        line = json.dumps(rec, separators=(",", ":"), default=str)
        with self._lock:
            try:
                self._f.write(line + "\n")
                self._size += len(line) + 1
                if self._size >= self.max_file:
                    self._rotate()
            except (OSError, ValueError):      # full card / closed file: keep running, keep the memory copy
                pass
            self.recent_all.append(rec)
            if kind not in ("rx", "tx"):
                self.recent_events.append(rec)

    def _rotate(self):
        """Continue in <base>-partN.jsonl and drop the oldest logs beyond max_total (lock held)."""
        self.part += 1
        old, self.path = self.path, os.path.join(self.dir, f"{self.base}-part{self.part}.jsonl")
        self._f.write(json.dumps({"ts": wall_iso(), "kind": "event", "what": "log_continues",
                                  "file": os.path.basename(self.path)}) + "\n")
        self._f.close()
        self._f = open(self.path, "a", buffering=1, encoding="utf-8")
        self._size = 0
        self._f.write(json.dumps({"ts": wall_iso(), "kind": "event", "what": "log_continued",
                                  "from": os.path.basename(old)}) + "\n")
        self._prune()

    def _prune(self):
        """Delete the oldest session-*.jsonl files until the folder is under max_total. Never the current file."""
        try:
            files = []
            for n in os.listdir(self.dir):
                if n.startswith("session-") and n.endswith(".jsonl"):
                    p = os.path.join(self.dir, n)
                    st = os.stat(p)
                    files.append((st.st_mtime, p, st.st_size))
        except OSError:
            return []
        files.sort()
        total = sum(f[2] for f in files)
        gone = []
        for _, p, size in files:
            if total <= self.max_total:
                break
            if p == self.path:
                continue
            try:
                os.remove(p)
                total -= size
                gone.append(os.path.basename(p))
            except OSError:
                pass
        return gone

    def recent(self, n=100, include_lines=False):
        with self._lock:
            src = self.recent_all if include_lines else self.recent_events
            return list(src)[-n:]


# ---------------------------------------------------------------------------
class App:
    def __init__(self, cfg, map_dir):
        self.cfg = cfg
        self.map_dir = map_dir
        self._map_cache = (None, {})            # (mtime_ns, map.json dict)
        self.zones_error = None                 # why zones.json was ignored at the last load, or None
        self.lock = threading.RLock()
        log_dir = cfg.get("log_dir") or os.path.join(PIT_ROOT, "logs")
        self.log = SessionLog(log_dir, cfg.get("log_max_file_mb", 64), cfg.get("log_max_total_mb", 1024))
        self.seq_file = os.path.join(log_dir, "cmdseq.json")
        self.sessions = {}                       # cookie token -> operator name
        self.map_cfg = self._load_map()
        fallback = self.map_cfg.get("origin") or cfg.get("origin")
        self.fleet = pitcore.Fleet(fallback_origin=fallback, pit_marker=self.map_cfg.get("pit"),
                                   gps_correction=bool(cfg.get("gps_correction", False)))
        self.zones_path = os.path.join(map_dir, "zones.json")
        # pace car / track builder: recordings in map/recordings/, newest reloaded
        self.fleet.recorder = pitcore.TrackRecorder(os.path.join(map_dir, "recordings"))
        self.zones_comment = None
        self._zones_mtime = None
        self._load_zones()
        self.link = Link(self._on_line, self._on_link_state, port=cfg.get("serial_port"),
                         tcp=cfg.get("tcp"), log=self._console)
        self.cmds = pitcore.CommandManager(self.fleet, self._send, log=self._cmd_log,
                                           start_seq=self._load_seq(), ttl=int(cfg["cmd_ttl_s"]))
        self.link_since = None
        self._saved_seq = self.cmds.next_seq
        self._state_cache = None                # (mono, full_json, light_json, zones_version)
        self._sse_clients = 0
        self._sse_lock = threading.Lock()
        self.stopping = threading.Event()
        self._race_tx = None                    # (state, pos) last sent to the T-Beam
        self._race_tx_mono = 0.0
        self._race_logged = None                # (state, pos) last written to the session log
        self._race_finish_at = []               # pending repeat times (mono) for state 3
        self._race_idle_pending = False         # NEW RACE / reset / abort: state-0 lines due
        self._race_idle_at = []                 # pending repeat times (mono) for state 0
        self._rate = None                       # fleet report rate last broadcast (1 / 2 Hz) or None
        self._rate_tx_mono = 0.0
        self._rate_wall = None
        self._started_mono = time.monotonic()
        self._scale_check_mono = 0.0
        # start_id changes on every server start: the pages reload themselves when it does
        self.start_id = secrets.token_hex(6)
        self.boot_id = _read_boot_id()
        self.health = health.Health(reader=_file_reader(cfg["health_file"]) if cfg.get("health_file") else "auto",
                                    on_change=self._on_health)
        self.log.write("event", what="server_start", version=VERSION, pid=os.getpid(),
                       start_id=self.start_id, boot_id=self.boot_id, health=self.health.available,
                       source=cfg.get("tcp") or cfg.get("serial_port") or "auto",
                       start_seq=self.cmds.next_seq)
        if self.log.pruned:
            self.log.write("event", what="logs_pruned", files=self.log.pruned)

    # -- plumbing ------------------------------------------------------------
    def _console(self, msg):
        print(f"{wall_iso()} {msg}", flush=True)
        self.log.write("event", what="link", msg=msg)

    def _cmd_log(self, kind, **fields):
        self.log.write(kind, **fields)

    def _send(self, obj):
        ok = self.link.write_line(obj)
        self.log.write("tx", line=json.dumps(obj, separators=(",", ":")), ok=ok)
        return ok

    def _load_seq(self):
        try:
            with open(self.seq_file) as f:
                s = int(json.load(f)["next_seq"])
                if 1 <= s <= 255:
                    return s
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return random.randint(1, 255)            # avoid colliding with a kart's remembered seq

    def _save_seq(self):
        if self.cmds.next_seq != self._saved_seq:
            self._saved_seq = self.cmds.next_seq
            try:
                with open(self.seq_file, "w") as f:
                    json.dump({"next_seq": self._saved_seq}, f)
            except OSError:
                pass

    def _load_map(self):
        """map.json, re-read only when its mtime changes (state() asks for it 4-5 times a second)."""
        path = os.path.join(self.map_dir, "map.json")
        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError:
            return {}
        if self._map_cache[0] == mtime:
            return self._map_cache[1]
        try:
            with open(path) as f:
                m = json.load(f)
            m = m if isinstance(m, dict) else {}
        except (OSError, ValueError):
            m = {}
        self._map_cache = (mtime, m)
        return m

    # -- game zones (map/ZONES.md) ------------------------------------------
    def _load_zones(self):
        """(Re)load zones.json if its mtime changed. A missing file = game off.
        An invalid file is logged and ignored (karts are served the last good document)."""
        try:
            mtime = os.stat(self.zones_path).st_mtime_ns
        except OSError:
            mtime = None
        if mtime == self._zones_mtime:
            return
        self._zones_mtime = mtime
        if mtime is None:
            self.zones_error = None
            with self.lock:
                self.fleet.game.set_doc(pitcore.EMPTY_ZONES)
            return
        try:
            with open(self.zones_path) as f:
                raw = json.load(f)
            doc = pitcore.validate_zones(raw)
            ver = raw.get("version", 0)
            doc["version"] = ver if isinstance(ver, int) and not isinstance(ver, bool) and ver >= 0 else 0
        except (OSError, ValueError, AttributeError) as e:
            self.zones_error = str(e) or type(e).__name__
            self.log.write("event", what="zones_invalid", path=self.zones_path, error=self.zones_error)
            print(f"{wall_iso()} zones.json ignored: {e}", flush=True)
            return
        self.zones_error = None
        self.zones_comment = raw.get("_comment") if isinstance(raw.get("_comment"), str) else self.zones_comment
        with self.lock:
            self.fleet.game.set_doc(doc)

    def zones_doc(self):
        self._load_zones()
        with self.lock:
            d = self.fleet.game.doc
            out = {"_comment": self.zones_comment} if self.zones_comment else {}
            out.update(version=d["version"], enabled=d["enabled"], zones=[dict(z) for z in d["zones"]])
            if d.get("track"):
                out["track"] = d["track"]
        return out

    def _save_zones(self, doc):
        """Write atomically (tmp + rename), keeping the file's _comment."""
        out = {"_comment": self.zones_comment} if self.zones_comment else {}
        out.update(version=doc["version"], enabled=doc["enabled"], zones=doc["zones"])
        if doc.get("track"):
            out["track"] = doc["track"]
        if self.zones_error and os.path.exists(self.zones_path):
            # the file on disk was unreadable: keep it for a post-mortem instead of silently overwriting it
            bad = f"{self.zones_path}.bad-{time.strftime('%Y%m%d-%H%M%S')}"
            try:
                os.replace(self.zones_path, bad)
                self.log.write("event", what="zones_invalid_kept", path=bad)
            except OSError:
                pass
            self.zones_error = None
        tmp = self.zones_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(out, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())                # power cuts happen at the pit: never a 0-byte zones.json
        os.replace(tmp, self.zones_path)
        self._zones_mtime = os.stat(self.zones_path).st_mtime_ns

    def save_zones(self, body, who):
        self._load_zones()
        with self.lock:
            cur = self.fleet.game.doc
            body = dict(body)
            body.setdefault("enabled", cur["enabled"])      # the editor does not flip the master switch
            if "track" not in body and cur.get("track"):    # absent = keep; null = remove
                body["track"] = cur["track"]
            doc = pitcore.validate_zones(body)
            doc["version"] = cur["version"] + 1
            try:
                self._save_zones(doc)
            except OSError as e:
                raise pitcore.ZoneError(f"could not write zones.json: {e}")
            self.fleet.game.set_doc(doc)
        self._state_cache = None        # next /api/state and SSE frame show the change
        self.log.write("op", who=who, action="zones_save", version=doc["version"], enabled=doc["enabled"],
                       zones=[{"id": z["id"], "type": z["type"]} for z in doc["zones"]],
                       track_points=len(doc["track"]["points"]) if doc.get("track") else 0)
        return self.zones_doc()

    def enable_zones(self, body, who):
        en = body.get("enabled")
        if not isinstance(en, bool):
            raise pitcore.ZoneError("enabled must be true or false")
        self._load_zones()
        with self.lock:
            cur = self.fleet.game.doc
            doc = {"version": cur["version"] + 1, "enabled": en, "zones": cur["zones"]}
            if cur.get("track"):
                doc["track"] = cur["track"]
            try:
                self._save_zones(doc)
            except OSError as e:
                raise pitcore.ZoneError(f"could not write zones.json: {e}")
            self.fleet.game.set_doc(doc)
        self._state_cache = None        # next /api/state and SSE frame show the change
        self.log.write("op", who=who, action="zones_enable", enabled=en, version=doc["version"])
        return {"enabled": en, "version": doc["version"]}

    def auto_boxes(self, body, who):
        """POST /zones/auto_boxes {"n": 3}: replace every item box with n boxes spread evenly along
        the saved track (pitcore.boxes_along_track); a normal zones save (version bump, log)."""
        n = body.get("n", 3)
        r_m = body.get("r_m", pitcore.AUTO_BOX_R_M)
        self._load_zones()
        with self.lock:                                     # RLock: save_zones takes it again
            cur = self.fleet.game.doc
            if not cur.get("track"):
                raise pitcore.ZoneError("no track yet: record one with PACE CAR or draw it with TRACK, then SAVE")
            zbody, boxes = pitcore.auto_boxes_body(cur, n, r_m)
            doc = self.save_zones(zbody, who)
        self.log.write("op", who=who, action="zones_auto_boxes", version=doc["version"], n=len(boxes),
                       r_m=boxes[0]["r_m"], ids=[b["id"] for b in boxes])
        return {"zones": doc, "boxes": [b["id"] for b in boxes]}

    def set_gps_correction(self, body, who):
        enabled = bool((body or {}).get("enabled"))
        with self.lock:
            self.fleet.set_correction(enabled)
            st = self.fleet.correction_status(time.monotonic())
        self.log.write("op", who=who, action="gps_correction", enabled=enabled)
        return {"gps_correction": st}

    def race_start(self, body, who):
        """Reset lap timing and broadcast one `race` command (grid hold, GO in `secs`). ZONES.md "Race start"."""
        secs = body.get("secs", 5)
        if isinstance(secs, bool) or not isinstance(secs, int) or not 1 <= secs <= 30:
            raise pitcore.CommandError("secs must be a whole number 1-30")
        laps = body.get("laps", 0) or 0
        if isinstance(laps, bool) or not isinstance(laps, int) or not 0 <= laps <= 99:
            raise pitcore.CommandError("laps must be a whole number 0-99 (0 = untimed)")
        if not self.link.connected:
            raise pitcore.CommandError("T-Beam link is down; race start not sent")
        now, t = time.monotonic(), time.time()
        wall = wall_iso(t)
        with self.lock:
            c = self.cmds.submit("race", pitcore.ALL_KARTS, secs, source=f"{who} (race start)", now=now, wall=wall)
            self.fleet.game.race_begin(laps, now + secs, wall_iso(t + secs))   # also resets lap timing
            race = self.fleet.race
            race.update(started_wall=wall, go_wall=wall_iso(t + secs), go_epoch=t + secs, go_secs=secs, laps=laps,
                        start_cmd_id=c.id, by=who, changed_wall=wall)
            d = c.to_dict()
        self._state_cache = None        # next /api/state and SSE frame show the change
        self.log.write("op", who=who, action="race_start", secs=secs, laps=laps, id=d["id"], seq=d["seq"],
                       go_wall=wall_iso(t + secs))
        self._race_tick(now, force=True)        # state 1, positions all 0, right away
        return {"command": d, "go_wall": wall_iso(t + secs), "secs": secs, "laps": laps}

    # -- pace car / track builder ------------------------------------------------
    def track_record(self, body, who):
        action = body.get("action")
        now, wall = time.monotonic(), wall_iso()
        with self.lock:
            rec = self.fleet.recorder
            if action == "start":
                kart = body.get("kart")
                if isinstance(kart, bool) or kart not in pitcore.KART_IDS:
                    raise pitcore.CommandError("kart must be 1-8")
                out = rec.start(kart, now, wall)
            elif action == "stop":
                out = rec.stop(wall)
            elif action == "clear":
                out = rec.clear()
            else:
                raise pitcore.CommandError('action must be "start", "stop" or "clear"')
        self._state_cache = None
        self.log.write("op", who=who, action="track_record", what=action, kart=out["kart"],
                       points=out["n_points"], laps=[(lp["n"], lp["length_m"], lp["time_s"]) for lp in out["laps"]],
                       file=out["file"])
        return out

    def track_recording(self):
        with self.lock:
            return self.fleet.recorder.summary(with_points=True)

    def track_use(self, body, who):
        lap = body.get("lap")
        self._load_zones()
        with self.lock:
            rec = self.fleet.recorder
            lt = None
            if isinstance(lap, int) and not isinstance(lap, bool) and 1 <= lap <= len(rec.laps):
                lt = rec.laps[lap - 1]["time_s"]
            zbody, info = pitcore.track_from_recording(
                rec, lap, self.fleet.game.doc, width_m=body.get("width_m", 6), lapline=body.get("lapline", "auto"),
                simplify_m=body.get("simplify_m", 1.0), lap_time_s=lt)
            info["kart"] = rec.kart
        doc = self.save_zones(zbody, who)
        self.log.write("op", who=who, action="track_use", version=doc["version"], **info)
        return {"zones": doc, "info": info}

    def _online(self, now):
        return [k for k in pitcore.KART_IDS if self.fleet.is_online(k, now)]

    def _send_finishes(self, finishes, who, now, wall):
        """One `race` command per kart: arg 100 + position, or 0 (finish, no position). Lock held."""
        out = []
        for kart, arg in finishes:
            c = self.cmds.submit("race", kart, arg, source=f"{who} (race finish)", now=now, wall=wall)
            out.append({"kart": kart, "arg": arg, "id": c.id})
        return out

    def _reachable(self, k, now):
        """A finish command is worth airtime only for a kart heard in the last STALE_S."""
        return pitcore.link_status(self.fleet.karts[k].last_tele_mono, now) in ("online", "stale")

    def race_stop(self, who):
        """STOP RACE: final results, every kart gets its finish command (ZONES.md "Race finish").
        Before GO it is ABORT START instead: a race finish would release the grid hold at once
        (kart firmware), so the karts get STOP ALL and stay at zero power until START."""
        if not self.link.connected:
            raise pitcore.CommandError("T-Beam link is down; race stop not sent")
        now, wall = time.monotonic(), wall_iso()
        with self.lock:
            ses = self.fleet.game.session
            if ses is not None and ses.running and ses.go_mono is not None and now < ses.go_mono:
                c = self.cmds.submit("stop", pitcore.ALL_KARTS, None, source=f"{who} (abort start)", now=now, wall=wall)
                self.fleet.game.reset_laps()
                for key in ("go_epoch", "go_wall", "go_secs"):
                    self.fleet.race.pop(key, None)
                self.fleet.race.update(aborted_wall=wall, by=who, changed_wall=wall)
                self._race_idle_pending = True
                d = c.to_dict()
                aborted = True
            else:
                aborted = False
                fin = self.fleet.game.race_stop(self._online(now), wall)
                res = self.fleet.game.session.snapshot()
        self._state_cache = None
        if aborted:
            self.log.write("op", who=who, action="race_abort", stop_id=d["id"], seq=d["seq"])
            self._race_tick(now)                # state 0 (idle) after the STOP
            return {"aborted": True, "sent": [], "stop": d, "session": None}
        # The race line first: the base carries it in its next beacon. Then one finish command per kart
        # that can hear us (online or stale); karts long gone only cost airtime, and every command skips a
        # beacon at the base, which is what kept kart 2 on "running" for 4 s on 2026-11-06.
        self._race_tick(now)                    # state 3 with the final positions, right away
        with self.lock:
            skipped = [k for k, _ in fin if not self._reachable(k, now)]
            sent = self._send_finishes([(k, a) for k, a in fin if self._reachable(k, now)], who, now, wall)
        self.log.write("op", who=who, action="race_stop", sent=sent, skipped=skipped, results=res["results"])
        return {"aborted": False, "sent": sent, "skipped": skipped, "session": res}

    def reset_laps(self, who):
        with self.lock:
            self.fleet.game.reset_laps()
            self._race_idle_pending = True
        self._state_cache = None        # next /api/state and SSE frame show the change
        self.log.write("op", who=who, action="laps_reset")
        self._race_tick(time.monotonic())       # state 0 now and again in 2 s
        return {"reset": True}

    # -- link callbacks (reader thread) --------------------------------------
    def _on_line(self, text):
        now, wall = time.monotonic(), wall_iso()
        self.log.write("rx", line=text)
        try:
            msg = json.loads(text)
        except ValueError:
            msg = None
        with self.lock:
            self.fleet.rx_lines += 1
            if not isinstance(msg, dict):
                self.fleet.rx_bad += 1
                return
            k = self.fleet.ingest(msg, now, wall)
            fin, ended = [], False
            if k is not None:
                self.cmds.on_tele(k, now, wall)
                fin = self.fleet.game.take_finishes()        # a kart completed the race length
                ended = self.fleet.game.check_end(self._online(now), wall)
            elif msg.get("t") == "sent":
                self.cmds.on_sent(msg)
            if fin and self.link.connected:
                sent = self._send_finishes(fin, "pit", now, wall)
            else:
                sent = []
            res = self.fleet.game.session.snapshot() if (fin or ended) and self.fleet.game.session else None
        if fin or ended:
            self.log.write("event", what="race_finish", sent=sent, ended=ended,
                           results=res["results"] if res and ended else None)
            self._state_cache = None
            if ended:
                self._race_tick(now)

    def _on_link_state(self, connected, desc):
        with self.lock:
            self.link_since = wall_iso()
            if connected:
                self.fleet.simulation = False   # re-detected from this source's own markers
            else:
                self.cmds.on_link_down(time.monotonic(), wall_iso())
        self.log.write("event", what="link_up" if connected else "link_down", desc=desc)
        print(f"{wall_iso()} link {'UP' if connected else 'DOWN'}: {desc}", flush=True)

    # -- background ----------------------------------------------------------
    def start(self):
        self.link.start()
        threading.Thread(target=self._ticker, name="cmd-ticker", daemon=True).start()
        threading.Thread(target=self._health_loop, name="health", daemon=True).start()

    def _health_loop(self):
        period = max(0.2, float(self.cfg.get("health_period_s") or health.PERIOD_S))
        while not self.stopping.is_set():
            try:
                self.health.sample()
            except Exception as e:          # never let a sensor problem kill the thread
                print(f"{wall_iso()} health sample failed: {e!r}", flush=True)
            if self.stopping.wait(period):
                break

    def _on_health(self, prev, snap):
        """Status transitions go to the session log and the journal (persistent: see why_rebooted.sh)."""
        self.log.write("event", what="health", status=snap["status"], prev=prev,
                       throttled=snap["throttled"], temp_c=snap["temp_c"])
        print(f"{wall_iso()} health {prev} -> {snap['status']} throttled={snap['throttled']} "
              f"temp={snap['temp_c']}C", flush=True)

    def _ticker(self):
        while not self.stopping.wait(0.1):
            events = []
            with self.lock:
                now = time.monotonic()
                self.cmds.tick(now, wall_iso())
                self._rate_policy(now)
                if now - self._scale_check_mono >= 1.0:
                    self._scale_check_mono = now
                    events = self._scale_watch(now)
                self._save_seq()
            for k, what, info in events:
                self.log.write("event", what="scale_mismatch" if what == "start" else "scale_mismatch_end",
                               kart=k, **(info or {}))
            self._race_tick(time.monotonic())

    def _rate_policy(self, now):
        """Lock held. Repeater heard in the last 60 s: "rate 1" to all karts now and every 30 s.
        No repeater for 120 s (or since start): "rate 2" once. Auto commands never hide an
        operator's last command on the cards (pitcore Command.auto)."""
        if not self.link.connected:
            return
        rm = self.fleet.repeater_mono
        want = None
        if rm is not None and now - rm <= REPEATER_FRESH_S:
            if self._rate != 1 or now - self._rate_tx_mono >= RATE_REPEAT_S:
                want = 1
        elif now - (rm if rm is not None else self._started_mono) >= REPEATER_GONE_S and self._rate != 2:
            want = 2
        if want is None:
            return
        wall = wall_iso()
        c = self.cmds.submit("rate", pitcore.ALL_KARTS, want, source="pit (fleet rate)", now=now, wall=wall, auto=True)
        if want != self._rate:
            self.log.write("event", what="fleet_rate", hz=want, id=c.id,
                           reason="repeater in use" if want == 1 else "no repeater for 120 s")
            self._rate_wall = wall
        self._rate, self._rate_tx_mono = want, now

    def _scale_watch(self, now):
        """Lock held. SCALE != MODE transitions (pitcore Fleet.watch_scales)."""
        ses = self.fleet.game.session
        grid = bool(ses is not None and ses.running and ses.go_mono is not None and now < ses.go_mono + 1.5)
        return self.fleet.watch_scales(now, self.cmds.boosted_recently(now), grid)

    def _race_tick(self, now, force=False):
        """Race positions to the T-Beam (SERIAL_PROTOCOL.md "Race positions"): on START RACE (state 1),
        every RACE_POS_PERIOD_S while running (1/2), 3 times over 6 s when finished (3), and state 0
        once on NEW RACE / reset. The session log gets one event per change, not every repeat."""
        with self.lock:
            state, pos = self.fleet.game.race_positions(now)
            cur = (state, tuple(pos))
            send = False
            if state in (1, 2):
                self._race_finish_at, self._race_idle_at, self._race_idle_pending = [], [], False
                send = force or cur != self._race_tx or now - self._race_tx_mono >= RACE_POS_PERIOD_S
            elif state == 3:
                self._race_idle_at, self._race_idle_pending = [], False
                if self._race_tx is None or self._race_tx[0] != 3 or (cur != self._race_tx and not self._race_finish_at):
                    self._race_finish_at = [now + d for d in RACE_FINISH_REPEATS]
                send = self._due(self._race_finish_at, now)
            else:
                self._race_finish_at = []
                if self._race_idle_pending or force:
                    self._race_idle_pending = False
                    self._race_idle_at = [now + d for d in RACE_IDLE_REPEATS]
                send = self._due(self._race_idle_at, now)
            if not send:
                return
            ok = self._send({"t": "race", "state": state, "pos": list(pos)})
            self._race_tx, self._race_tx_mono = cur, now
            log = cur != self._race_logged
            if log:
                self._race_logged = cur
        if log:
            self.log.write("event", what="race_pos", state=state, pos=list(pos), ok=ok)

    @staticmethod
    def _due(times, now):
        """Pop every repeat time that has passed; True if at least one did."""
        hit = False
        while times and now >= times[0]:
            times.pop(0)
            hit = True
        return hit

    # -- state ---------------------------------------------------------------
    def state(self):
        now = time.monotonic()
        with self.lock:
            fleet = self.fleet.snapshot(now)
            fleet["rate"] = {"hz": self._rate, "since": self._rate_wall,
                             "policy": "1 Hz while a repeater is heard, 2 Hz after 120 s without one"}
            ge = fleet["race"].get("go_epoch")
            # seconds until GO (negative after) from the server clock, so every page counts down together
            fleet["race"]["go_in_s"] = None if ge is None else round(ge - time.time(), 2)
            cmds = self.cmds.snapshot()
            rx, bad = self.fleet.rx_lines, self.fleet.rx_bad
            track_rec = self.fleet.recorder.summary()
        zones = self.zones_doc()
        return {
            "server": {"version": VERSION, "time": wall_iso(), "log_file": os.path.basename(self.log.path),
                       "start_id": self.start_id, "boot_id": self.boot_id, "health": self.health.snapshot(),
                       "link": {"connected": self.link.connected, "desc": self.link.description,
                                "since": self.link_since},
                       "rx_lines": rx, "rx_bad": bad, "zones_error": self.zones_error,
                       "tx_lines": self.link.lines_written if hasattr(self.link, "lines_written") else None},
            "fleet": fleet,
            "commands": cmds,
            "map": self.map_package(fleet["origin"]),
            "zones": zones,
            "zones_version": zones["version"],
            "track_rec": track_rec,
        }

    def state_frames(self):
        """(full, light, zones_version) JSON bytes, cached 0.2 s and shared by every client.
        light = without the zones document (pages keep the last one they got)."""
        c = self._state_cache
        if c is not None and time.monotonic() - c[0] < 0.2:
            return c[1], c[2], c[3]
        st = self.state()
        full = json.dumps(st, separators=(",", ":")).encode()
        st.pop("zones")
        light = json.dumps(st, separators=(",", ":")).encode()
        self._state_cache = (time.monotonic(), full, light, st["zones_version"])
        return full, light, st["zones_version"]

    def state_json(self):
        return self.state_frames()[0]

    def sse_enter(self):
        with self._sse_lock:
            if self._sse_clients >= int(self.cfg.get("sse_max_clients") or 24):
                return False
            self._sse_clients += 1
            return True

    def sse_leave(self):
        with self._sse_lock:
            self._sse_clients -= 1

    def map_package(self, origin=None):
        m = self._load_map()
        self._load_zones()
        img = m.get("image")
        if origin is None:
            with self.lock:
                origin = self.fleet.current_origin()
        base = self.fleet.base
        pit = m.get("pit") or ({"lat": base["lat"], "lon": base["lon"]}
                               if base.get("fix") and base.get("lat") is not None else None)
        return {"version": m.get("version", 0),
                "image": f"/map/{os.path.basename(img)}" if img else None,
                "bounds": m.get("bounds"),
                "origin": {"lat": origin["lat"], "lon": origin["lon"]} if origin else None,
                "origin_source": origin.get("source") if origin else None,
                "course": m.get("course") or [],
                "pit": pit,
                "source": m.get("source"), "attribution": m.get("attribution"),
                "zones": "/map/zones.json", "zones_version": self.fleet.game.doc["version"]}

    # -- actions -------------------------------------------------------------
    def command(self, body, who):
        if not self.link.connected:
            raise pitcore.CommandError("T-Beam link is down; command not sent")
        now, wall = time.monotonic(), wall_iso()
        with self.lock:
            c = self.cmds.submit(body.get("cmd"), body.get("kart"), body.get("arg"),
                                 source=who, now=now, wall=wall)
            d = c.to_dict()
        self.log.write("op", who=who, action="cmd", request=body, id=d["id"], seq=d["seq"])
        return d

    def race(self, body, who):
        now, wall = time.monotonic(), wall_iso()
        issued = []
        with self.lock:
            race = self.fleet.race
            if "entered" in body:
                ent = body["entered"]
                if not isinstance(ent, list) or any(k not in pitcore.KART_IDS for k in ent):
                    raise pitcore.CommandError("entered must be a list of kart ids 1-8")
                race["entered"] = sorted(set(ent))
            if "mode" in body:
                if body["mode"] not in pitcore.MODE_NAMES:
                    raise pitcore.CommandError("mode must be 0, 1 or 2")
                race["mode"] = body["mode"]
            if "active" in body:
                active = bool(body["active"])
                if active:
                    if not self.link.connected:
                        raise pitcore.CommandError("T-Beam link is down; race not started")
                    entered = set(race["entered"])
                    if not entered:
                        raise pitcore.CommandError("no karts entered")
                    mode = race["mode"]
                    if entered == set(pitcore.KART_IDS):
                        plan = [("mode", 255, mode), ("start", 255, None)]
                    else:
                        plan = [("stop", k, None) for k in pitcore.KART_IDS if k not in entered]
                        for k in sorted(entered):
                            plan += [("mode", k, mode), ("start", k, None)]
                    for cmd, kart, arg in plan:
                        c = self.cmds.submit(cmd, kart, arg, source=f"{who} (race)", now=now, wall=wall)
                        issued.append(c.id)
                race["active"] = active
            race["changed_wall"], race["by"] = wall, who
            result = dict(race, issued=issued)
        self.log.write("op", who=who, action="race", request=body, issued=issued)
        return result


# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "GoKartPit/" + VERSION
    app: App = None   # set in main()

    def log_message(self, fmt, *args):     # keep the journal quiet; ops go to the session log
        pass

    # -- helpers ---------------------------------------------------------------
    def _client_is_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _who(self):
        """Operator identity for this request, or None if not authenticated."""
        app = self.app
        if app.cfg.get("trust_localhost", True) and self._client_is_local():
            return "kiosk@localhost"
        c = cookies.SimpleCookie()
        try:
            c.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return None
        tok = c.get(COOKIE)
        if tok and tok.value in app.sessions:
            return f"{app.sessions[tok.value]}@{self.client_address[0]}"
        return None

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, separators=(",", ":")), "application/json")

    def _file(self, directory, name, download=None):
        name = os.path.basename(name)
        path = os.path.join(directory, name)
        ext = os.path.splitext(name)[1].lower()
        if not name or ext not in MIME or not os.path.isfile(path):
            return self._send(404, "not found")
        with open(path, "rb") as f:
            data = f.read()
        hdr = {"Content-Disposition": f'attachment; filename="{download}"'} if download else None
        self._send(200, data, MIME[ext], hdr)

    def _read_body(self, limit=8192):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("body too large")
        return self.rfile.read(n) if n else b""

    def _need_auth(self):
        if not self.app.cfg.get("password"):
            self._send(403, "Pit password not configured: set \"password\" in pit/server/config.json "
                            "and restart. Only the local kiosk can connect until then.")
        elif self.path.startswith(("/api/", "/events", "/cmd", "/zones", "/laps", "/race")):
            self._json(401, {"ok": False, "error": "login required"})
        else:
            p = urlparse(self.path).path
            nxt = f"?next={p}" if p in PAGES and p not in ("/", "/index.html") else ""
            self._send(303, "", headers={"Location": "/login" + nxt})

    # -- routes ------------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        p = url.path
        app = self.app
        # Public: health, login page, static assets, map package (kart Pis fetch it on pit Wi-Fi).
        if p == "/healthz":
            return self._send(200, "ok")
        if p == "/login":
            return self._file(STATIC_DIR, "login.html")
        if p.startswith("/static/"):
            return self._file(STATIC_DIR, p[len("/static/"):])
        if p == "/map/package.json":
            return self._json(200, app.map_package())
        if p == "/map/zones.json":
            return self._json(200, app.zones_doc())
        if p.startswith("/map/"):
            return self._file(app.map_dir, p[len("/map/"):])
        if p == "/logout":
            nxt = (parse_qs(url.query).get("next") or [""])[0]
            loc = f"/login?next={nxt}" if nxt in PAGES and nxt != "/" else "/login"
            return self._send(303, "", headers={"Location": loc,
                                                "Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"})

        who = self._who()
        if who is None:
            return self._need_auth()
        if p in PAGES:
            return self._file(STATIC_DIR, PAGES[p])
        if p == "/api/state":
            return self._send(200, app.state_json(), "application/json")
        if p == "/api/log/recent":
            q = parse_qs(url.query)
            n = max(1, min(500, int((q.get("n") or ["100"])[0])))
            return self._json(200, app.log.recent(n, include_lines=(q.get("lines") or ["0"])[0] == "1"))
        if p == "/api/log/download":
            return self._download_log()
        if p == "/track/recording":
            return self._json(200, app.track_recording())
        if p == "/api/whoami":
            return self._json(200, {"who": who})
        if p == "/events":
            return self._events()
        return self._send(404, "not found")

    def _download_log(self):
        path = self.app.log.path
        with open(path, "rb") as f:
            data = f.read()
        self._send(200, data, "application/x-ndjson",
                   {"Content-Disposition": f'attachment; filename="{os.path.basename(path)}"'})

    def _events(self):
        """~4 Hz state stream. Capped at sse_max_clients; a client that stops reading for
        SSE_WRITE_TIMEOUT_S is dropped. Frames carry the zones document only when its version
        changes (and every SSE_ZONES_EVERY_S); the pages keep the last one they received."""
        app = self.app
        if not app.sse_enter():
            return self._send(503, "too many live pages", headers={"Retry-After": "5"})
        try:
            self.connection.settimeout(SSE_WRITE_TIMEOUT_S)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b"retry: 2000\n\n")
            sent_zv, sent_at = None, 0.0
            while not app.stopping.is_set():
                full, light, zv = app.state_frames()
                now = time.monotonic()
                if zv != sent_zv or now - sent_at >= SSE_ZONES_EVERY_S:
                    data, sent_zv, sent_at = full, zv, now
                else:
                    data = light
                self.wfile.write(b"data: " + data + b"\n\n")
                self.wfile.flush()
                time.sleep(SSE_PERIOD_S)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass                                # includes socket.timeout: a stalled client
        finally:
            app.sse_leave()

    def do_POST(self):
        p = urlparse(self.path).path
        app = self.app
        try:
            raw = self._read_body(262144 if p == "/zones" else 8192)
        except ValueError:
            return self._json(413, {"ok": False, "error": "body too large"})
        if p == "/login":
            form = parse_qs(raw.decode("utf-8", "replace"))
            pw = (form.get("password") or [""])[0]
            name = ((form.get("name") or ["operator"])[0].strip() or "operator")[:24]
            nxt = (form.get("next") or [""])[0]
            nxt = nxt if nxt in PAGES else "/"
            want = app.cfg.get("password") or ""
            if want and hmac.compare_digest(pw.encode(), want.encode()):
                tok = secrets.token_urlsafe(24)
                app.sessions[tok] = name
                app.log.write("op", who=f"{name}@{self.client_address[0]}", action="login")
                return self._send(303, "", headers={
                    "Location": nxt,
                    "Set-Cookie": f"{COOKIE}={tok}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800"})
            app.log.write("event", what="login_failed", ip=self.client_address[0])
            time.sleep(1.0)
            bad = "/login?bad=1" + (f"&next={nxt}" if nxt != "/" else "")
            return self._send(303, "", headers={"Location": bad})

        who = self._who()
        if who is None:
            return self._need_auth()
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._json(415, {"ok": False, "error": "send application/json"})
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
        except ValueError as e:
            return self._json(400, {"ok": False, "error": f"bad json: {e}"})
        try:
            if p == "/cmd":
                return self._json(200, {"ok": True, "command": app.command(body, who)})
            if p == "/api/race":
                return self._json(200, {"ok": True, "race": app.race(body, who)})
            if p == "/zones":
                return self._json(200, {"ok": True, "zones": app.save_zones(body, who)})
            if p == "/zones/auto_boxes":
                return self._json(200, {"ok": True, **app.auto_boxes(body, who)})
            if p == "/zones/enable":
                return self._json(200, {"ok": True, **app.enable_zones(body, who)})
            if p == "/race/start":
                return self._json(200, {"ok": True, **app.race_start(body, who)})
            if p == "/race/stop":
                return self._json(200, {"ok": True, **app.race_stop(who)})
            if p == "/laps/reset":
                return self._json(200, {"ok": True, **app.reset_laps(who)})
            if p == "/track/record":
                return self._json(200, {"ok": True, "recording": app.track_record(body, who)})
            if p == "/track/use":
                return self._json(200, {"ok": True, **app.track_use(body, who)})
            if p == "/gps_correction":
                return self._json(200, {"ok": True, **app.set_gps_correction(body, who)})
        except pitcore.CommandError as e:
            app.log.write("op", who=who, action=p, request=body, refused=str(e))
            return self._json(409, {"ok": False, "error": str(e)})
        return self._json(404, {"ok": False, "error": "not found"})


# ---------------------------------------------------------------------------
def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path) as f:
            user = json.load(f)
        cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--port", help="serial device (default: auto-find the T-Beam)")
    ap.add_argument("--tcp", help="host:port of the simulator's TCP server")
    ap.add_argument("--http-port", type=int)
    ap.add_argument("--bind")
    ap.add_argument("--log-dir")
    ap.add_argument("--map-dir", default=os.path.join(HERE, "map"))
    ap.add_argument("--password", help="override config password (tests/dev only)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    for key, val in (("serial_port", args.port), ("tcp", args.tcp), ("http_port", args.http_port),
                     ("bind", args.bind), ("log_dir", args.log_dir), ("password", args.password)):
        if val is not None:
            cfg[key] = val

    app = App(cfg, args.map_dir)
    Handler.app = app
    httpd = ThreadingHTTPServer((cfg["bind"], int(cfg["http_port"])), Handler)
    httpd.daemon_threads = True
    app.start()
    print(f"{wall_iso()} pit server {VERSION} on http://{cfg['bind']}:{cfg['http_port']} "
          f"source={cfg.get('tcp') or cfg.get('serial_port') or 'auto'} log={app.log.path}"
          + ("" if cfg.get("password") else "  (no password set: remote browsers are refused)"),
          flush=True)
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.stopping.set()
        app.link.stop()
        app.log.write("event", what="server_stop")


if __name__ == "__main__":
    main()
