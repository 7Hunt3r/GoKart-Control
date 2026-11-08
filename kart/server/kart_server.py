#!/usr/bin/env python3
"""GoKart Control kart display server (Raspberry Pi 3 B on each kart).

    python3 kart_server.py                       # auto-find the Heltec (/dev/heltec, ttyACM*, cu.usbmodem*)
    python3 kart_server.py --port /dev/ttys004   # explicit serial path (simulator pty)
    python3 kart_server.py --tcp 127.0.0.1:7979  # simulator over TCP

Standard library + pyserial only. The Pi is DISPLAY ONLY (docs/requirements.md):
it reads the Heltec's USB lines, shows them to the driver, and forwards the
driver's mode taps to the Heltec as text requests ("mode 1"). The Heltec owns
the VESC, GPS and LoRa and decides what to apply.

HTTP (default 0.0.0.0:8081; the kiosk uses 127.0.0.1):
  /             driver page (1024x600 touch)
  /events       Server-Sent Events, full state ~5 Hz
  /api/state    the same state once
  POST /cmd     {"cmd":"mode","arg":1} | slow [arg mph] | stop | start | ping | status
                (from 127.0.0.1 only unless config "remote_cmd": true)
  POST /api/map/refresh   poll the pit for a new map now
  POST /api/demo  {"on": true|false} or {"toggle": true}: demo mode (localhost only, config "demo")
  /map/<file>   cached course imagery;  /map/package.json  the cached package
  /map/zones.json  the cached game zones (pit/server/map/ZONES.md), 404 when none
  /healthz      "ok"
Pi power health (health.py, a byte-identical copy of pit/server/health.py): vcgencmd
get_throttled + CPU temperature every health_period_s, in state server.health; status
transitions go to the session log and the journal. Off a Pi it reports "unavailable".
Session logs: <kart>/logs/session-*.jsonl (every serial line both ways, every request,
every game event and every game command).

Game layer (pit/server/map/ZONES.md): the zones file arrives with the map package;
kartcore.GameEngine evaluates it against this kart's own fixes and the server sends
the resulting `boost SECS SCALE` lines to the Heltec. Nothing else is ever sent by
the game (no STOP/START/mode), and nothing above scale 1.0.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import health  # noqa: E402
import kartcore  # noqa: E402
from demo import Demo  # noqa: E402
from link import Link  # noqa: E402
from mapsync import MapSync  # noqa: E402

VERSION = "kart-display-0.2.0"
KART_ROOT = os.path.dirname(HERE)
STATIC_DIR = os.path.join(HERE, "static")
DEFAULTS = {
    "kart_id": 2,                       # display label only; the Heltec's own id wins when they differ
    "pit_urls": ["http://gokart-pit.local:8080", "http://192.168.151.240:8080"],
    "map_poll_s": 30,
    "beginner_mode": False,             # hides driver-facing fault alerts only (docs/requirements.md)
    "map_orientation": "heading_up",    # "heading_up" | "north_up" (heading-up only while following)
    "follow_span_m": 70,                # metres shown across the map height while following
    "follow_resume_s": 10,              # auto-follow resumes this long after the last touch
    "mode_hold_ms": 500,                # press-and-hold to request a mode (0 = plain tap)
    "map_fps": 10,                      # max redraw rate while something moves
    "bind": "0.0.0.0",
    "http_port": 8081,
    "remote_cmd": False,                # allow POST /cmd from other machines (debug only)
    "serial_port": None,
    "tcp": None,
    "status_poll_s": 60,                # send "status" to the Heltec this often (read-only reply)
    "log_dir": None,                    # default <kart>/logs
    "cache_dir": None,                  # default <kart>/cache/map
    "max_log_mb": 800,                  # oldest session logs are deleted beyond this
    "game": True,                       # false = this kart ignores the pit's game zones entirely
    "gps_correction": False,            # game positions += (pit surveyed - pit radio live fix), if < 25 m
    "demo": True,                       # allow demo mode (hold the KART badge 3 s): simulated kart, real Heltec untouched
    "demo_max_s": 180,                  # demo ends by itself after this
    "sound": True,                      # driver page: synthesized game sounds (WebAudio, static/sound.js)
    "sound_volume": 0.8,                # master volume of those sounds, 0-1
    "rotate": 0,                        # install.sh only: 180 = panel mounted ports-down (kernel + touch rotation)
    "health_period_s": health.PERIOD_S, # vcgencmd get_throttled + CPU temp sample period (Pi only)
    "health_file": None,                # tests/dev: read the get_throttled text from this file instead of vcgencmd
}
# config sanity: (type, min, max) for numbers; a bad value is replaced by the default and reported
CONFIG_RULES = {
    "kart_id": (int, 1, 8), "map_poll_s": (float, 5, 3600), "follow_span_m": (float, 10, 2000),
    "follow_resume_s": (float, 1, 600), "mode_hold_ms": (int, 0, 5000), "map_fps": (int, 1, 30),
    "http_port": (int, 1, 65535), "status_poll_s": (float, 5, 3600), "max_log_mb": (float, 10, 100000),
    "demo_max_s": (float, 10, 3600), "sound_volume": (float, 0, 1), "rotate": (int, 0, 180),
    "health_period_s": (float, 0.2, 3600),
}
CONFIG_BOOLS = ("beginner_mode", "remote_cmd", "game", "gps_correction", "demo", "sound")
CONFIG_CHOICES = {"map_orientation": ("heading_up", "north_up"), "rotate": (0, 180)}
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".svg": "image/svg+xml"}
SSE_PERIOD_S = 0.2
SSE_SKIP = ("heltec", "rx_lines", "rx_bad", "gps_lines", "kiosk")   # /api/state only (the page does not use them)
DEMO_FINISH_LAPS = 3                    # demo mode: FINISH (P1) after this many laps
PUBLIC_CFG = ("kart_id", "beginner_mode", "map_orientation", "follow_span_m", "follow_resume_s",
              "mode_hold_ms", "map_fps", "game", "sound", "sound_volume", "demo")


def _file_reader(path):
    def read():
        with open(path) as f:
            return f.read()
    return read


def wall_iso(ts=None):
    return datetime.fromtimestamp(time.time() if ts is None else ts, timezone.utc) \
        .isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SessionLog:
    """JSONL session log: every serial line (rx/tx) and every event.

    One file per server start, rotated to a new part (`session-<start>-pNN.jsonl`) when it reaches
    `part_mb`. Writes go to a 64 KB buffer flushed at least once a second (`flush()` from the ticker)
    instead of one write() per line: at 7 lines/s that is a lot of small SD card writes. A power cut
    loses at most ~1 s. `max_mb` (config max_log_mb) caps all session logs together: the oldest files
    are deleted at start, at every rotation and every 5 minutes; less than MIN_FREE_MB free on the
    card also deletes old files. A full card never stops the display (write errors are counted)."""

    PART_MB = 64
    MIN_FREE_MB = 300
    PRUNE_EVERY_S = 300.0

    def __init__(self, log_dir, max_mb, part_mb=None):
        try:
            os.makedirs(log_dir, exist_ok=True)
            if not os.access(log_dir, os.W_OK):
                raise PermissionError(f"{log_dir} not writable")
        except OSError as e:               # a bad log dir must not crash-loop the display
            fallback = os.path.join(tempfile.gettempdir(), "gokart-kart-logs")
            print(f"{wall_iso()} WARNING: log dir {log_dir}: {e}; logging to {fallback}", flush=True)
            log_dir = fallback
            os.makedirs(log_dir, exist_ok=True)
        self.dir = log_dir
        self.max_bytes = float(max_mb) * 1024 * 1024
        self.part_bytes = float(part_mb or self.PART_MB) * 1024 * 1024
        self.base = "session-" + time.strftime("%Y%m%d-%H%M%S")
        self.part = 0
        self.errors = 0
        self._lock = threading.Lock()
        self._pruned_at = time.monotonic()
        self._open()
        self.prune()

    def _open(self):
        name = self.base + (f"-p{self.part:02d}" if self.part else "") + ".jsonl"
        self.path = os.path.join(self.dir, name)
        self._f = open(self.path, "a", buffering=65536, encoding="utf-8")
        self._size = os.path.getsize(self.path)

    def prune(self):
        """Delete the oldest session files (never the open one) beyond max_mb or when the card is nearly full."""
        try:
            names = [f for f in os.listdir(self.dir) if f.startswith("session-") and f.endswith(".jsonl")]
            st = {f: os.stat(os.path.join(self.dir, f)) for f in names}
            files = sorted(names, key=lambda f: (st[f].st_mtime, f))      # oldest first (parts too)
            sizes = {f: st[f].st_size for f in files}
        except OSError:
            return
        cur = os.path.basename(self.path)
        total = sum(sizes.values())

        def low_disk():
            try:
                st = os.statvfs(self.dir)
                return st.f_bavail * st.f_frsize < self.MIN_FREE_MB * 1024 * 1024
            except (OSError, AttributeError):
                return False
        for f in files:
            if f == cur or not (total > self.max_bytes or low_disk()):
                continue
            try:
                os.unlink(os.path.join(self.dir, f))
                total -= sizes[f]
            except OSError:
                pass
        self._pruned_at = time.monotonic()

    def write(self, kind, **fields):
        rec = {"ts": wall_iso(), "mono": round(time.monotonic(), 3), "kind": kind}
        rec.update(fields)
        line = json.dumps(rec, separators=(",", ":"), default=str) + "\n"
        rotate = False
        with self._lock:
            try:
                self._f.write(line)
                self._size += len(line)
                if kind != "rx":         # serial lines are the bulk; events, commands, game records go out now
                    self._f.flush()
            except (OSError, ValueError):
                self.errors += 1         # a full SD card must not stop the display
            if self._size >= self.part_bytes:
                rotate = True
                try:
                    self._f.close()
                except OSError:
                    self.errors += 1
                self.part += 1
                try:
                    self._open()
                except OSError:
                    self.errors += 1
        if rotate:
            self.prune()

    def flush(self):
        with self._lock:
            try:
                self._f.flush()
            except (OSError, ValueError):
                self.errors += 1
        if time.monotonic() - self._pruned_at > self.PRUNE_EVERY_S:
            self.prune()

    def close(self):
        with self._lock:
            try:
                self._f.close()
            except OSError:
                pass


def play_test_tone(freq=660.0, secs=1.0, rate=48000):
    """Generate a short stereo sine WAV and play it with aplay on the default ALSA device."""
    import math
    import struct
    import subprocess
    import tempfile
    import wave
    path = os.path.join(tempfile.gettempdir(), "gokart-sound-test.wav")
    n = int(rate * secs)
    frames = bytearray()
    for i in range(n):
        env = min(1.0, i / 2400, (n - i) / 2400)
        v = int(12000 * env * math.sin(2 * math.pi * freq * i / rate))
        frames += struct.pack("<hh", v, v)
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(rate); w.writeframes(bytes(frames))
    try:
        r = subprocess.run(["aplay", "-q", path], capture_output=True, text=True, timeout=secs + 5)
        return {"rc": r.returncode, "stderr": r.stderr.strip()[-300:]}
    except (OSError, subprocess.SubprocessError) as e:
        return {"rc": None, "stderr": str(e)}


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.log = SessionLog(cfg.get("log_dir") or os.path.join(KART_ROOT, "logs"), float(cfg["max_log_mb"]))
        cid = cfg.get("kart_id")
        self.configured_id = int(cid) if cid else None
        cache_dir = cfg.get("cache_dir") or os.path.join(KART_ROOT, "cache", "map")
        # lap/race timing survives a server restart within the same boot (kartcore.GameEngine.export_state)
        self.game_state_path = os.path.join(os.path.dirname(os.path.normpath(cache_dir)), "game-state.json")
        self._game_saved = None
        self._game_pending = self._load_game_state()
        self._real_game = None             # the real kart's game timing, parked while demoing
        self._mismatch_logged = False
        self._reboots_seen = 0
        self._fresh_state()
        self.demo = None                 # Demo while demoing: it replaces the Heltec for reads AND writes
        self.sound_test_seq = 0          # GET /api/sound-test bumps it; the page plays a test jingle
        self.sound_test_mono = None
        self.link = Link(self._on_line, self._on_link_state, port=cfg.get("serial_port"),
                         tcp=cfg.get("tcp"), log=self._console)
        self.maps = MapSync(cfg["pit_urls"], cache_dir,
                            interval=float(cfg["map_poll_s"]), log=self._map_log)
        self.link_since = None
        self.stopping = threading.Event()
        self._state_cache = (0.0, b"{}")
        self._parts_cache = (0.0, {})
        self._status_due = 0.0
        self.started_mono = time.monotonic()
        self.config_warnings = []
        self.kiosk = None                  # last heartbeat from the driver page on this Pi (frame stats)
        self.kiosk_mono = None
        self.health = health.Health(reader=_file_reader(cfg["health_file"]) if cfg.get("health_file") else "auto",
                                    on_change=self._on_health)
        self.log.write("event", what="server_start", version=VERSION, pid=os.getpid(), health=self.health.available,
                       source=cfg.get("tcp") or cfg.get("serial_port") or "auto",
                       config={k: cfg.get(k) for k in PUBLIC_CFG + ("pit_urls",)})

    def _fresh_state(self):
        """New display/command/game state (start, and on entering/leaving demo mode)."""
        self.disp = kartcore.KartDisplay(configured_id=self.configured_id)
        self.cmds = kartcore.CommandTracker()
        self.game = kartcore.GameEngine(local_enabled=bool(self.cfg.get("game", True)),
                                        gps_correction=bool(self.cfg.get("gps_correction", False)))

    # -- demo mode -------------------------------------------------------------------
    def _demo_zones(self):
        """The pit's current zones (forced on: demo is a simulation) or, with none, a built-in
        course around the kart's last fix / the map origin / the pit."""
        doc = self.maps.zones()
        usable = isinstance(doc, dict) and (kartcore.parse_track(doc) is not None or any(
            isinstance(z, dict) and z.get("type") in kartcore.ZONE_TYPES for z in doc.get("zones") or []))
        if usable:
            return dict(doc, enabled=True, version=f"demo-{doc.get('version')}"), "pit"
        pkg = self.maps.package or {}
        fx = self.disp.me.fix if self.disp.me.fix else None
        c = (fx["lat"], fx["lon"]) if fx else None
        for k in ("origin", "pit"):
            o = pkg.get(k)
            if c is None and isinstance(o, dict) and o.get("lat") is not None:
                c = (o["lat"], o["lon"])
        return kartcore.demo_zones(c or (41.09399, -73.41637), version="demo-builtin"), "builtin"

    def demo_start(self, who):
        if not self.cfg.get("demo", True):
            raise kartcore.CommandError("demo mode is off in config.json")
        if self.demo:
            return self.demo_info()
        doc, source = self._demo_zones()
        with self.lock:
            if self.demo:                  # two holds at once: one demo
                return self.demo_info()
            kid = self.disp.heltec_id or self.configured_id or 2
            self._real_game = self.game.export_state(time.monotonic())   # parked; restored on demo stop
            self._fresh_state()
            self.demo_source = source
            self.demo = Demo(doc, kid, self._on_demo_line, max_s=float(self.cfg.get("demo_max_s", 180)))
        self._state_cache = (0.0, b"{}")
        self.log.write("event", what="demo_start", who=who, zones=source, path_m=self.demo.path["length_m"],
                       path_from=self.demo.path.get("source"))
        print(f"{wall_iso()} DEMO start ({source} zones, loop {self.demo.path['length_m']} m) by {who}", flush=True)
        return self.demo_info()

    def demo_stop(self, who, why="stopped"):
        with self.lock:
            d = self.demo
            if not d:
                return self.demo_info()
            self.demo = None
            d.stop()
            self._fresh_state()           # the real Heltec's next lines repopulate it
            # the real kart's lap/race timing comes back once the real zones are loaded (next tick);
            # nothing from the simulated kart (position, trail, laps, race, P badge) is kept
            self._game_pending, self._real_game = self._real_game, None
            self._status_due = time.monotonic() + 1.0
        self._state_cache = (0.0, b"{}")
        self.log.write("event", what="demo_stop", who=who, why=why, sent_to_sim=len(d.writes))
        print(f"{wall_iso()} DEMO stop ({why}) by {who}", flush=True)
        return self.demo_info()

    def sound_test(self, alsa=False):
        """Ask the driver page to play its test sounds (checks Chromium -> ALSA -> HDMI panel).
        alsa=True instead plays a 1 s 660 Hz tone straight through ALSA with aplay (no browser); the
        HDMI device is exclusive, so that only works while Chromium is not holding it (~20 s after
        the page's last sound)."""
        if alsa:
            out = {"ok": True, "alsa": play_test_tone()}
            self.log.write("op", action="sound_test", alsa=out["alsa"])
            return out
        with self.lock:
            self.sound_test_seq += 1
            self.sound_test_mono = time.monotonic()
            self._state_cache = (0.0, b"{}")
        out = {"ok": True, "seq": self.sound_test_seq}
        self.log.write("op", action="sound_test", seq=self.sound_test_seq, alsa=out.get("alsa"))
        return out

    def demo_info(self):
        d = self.demo
        if not d:
            return {"active": False, "allowed": bool(self.cfg.get("demo", True))}
        return {"active": True, "allowed": True, "left_s": round(d.left_s(), 1), "zones": self.demo_source,
                "path_m": d.path["length_m"], "path_from": d.path.get("source")}

    def _on_demo_line(self, text, source=None):
        self._ingest(text, demo=True, source=source)     # _ingest drops it unless `source` is the current demo

    def _console(self, msg):
        print(f"{wall_iso()} {msg}", flush=True)
        self.log.write("event", what="link", msg=msg)

    def _map_log(self, msg):
        print(f"{wall_iso()} {msg}", flush=True)
        self.log.write("event", what="map", msg=msg)

    # -- link (reader thread) --------------------------------------------------
    def _on_line(self, text):
        if self.demo is not None:          # demoing: the real Heltec's lines are logged, not shown
            self.log.write("rx", line=text, ignored="demo")
            return
        self._ingest(text)

    def _ingest(self, text, demo=False, source=None):
        now, wall = time.monotonic(), wall_iso()
        try:
            msg = json.loads(text) if text[:1] == "{" else None
        except ValueError:
            msg = None
        with self.lock:
            # demo started/stopped between the reader's check and here: this line belongs to the other
            # side and must not land in the fresh state (stale simulated position after a demo, or a
            # real line in the demo)
            cur = self.demo
            if (cur is not None) != demo or (demo and source is not None and source is not cur):
                self.log.write("rx", line=text, ignored="demo switch" if demo else "demo")
                return
            if demo:
                self.log.write("rx", line=text, demo=True)
            else:
                self.log.write("rx", line=text)
            self.disp.rx_lines += 1
            if not isinstance(msg, dict):
                self.disp.rx_bad += 1        # boot chatter, partial lines: logged, not shown
                return
            t = self.disp.ingest(msg, now, wall)
            if self.disp.heltec_reboots != self._reboots_seen:     # Heltec rebooted mid-session
                self._reboots_seen = self.disp.heltec_reboots
                self._status_due = now + 1.0                        # re-read fw/radio/gps details
                self.log.write("event", what="heltec_reboot", uptime_s=msg.get("uptime_s"))
            if not self._mismatch_logged and self.configured_id and self.disp.heltec_id \
                    and self.disp.heltec_id != self.configured_id:
                self._mismatch_logged = True
                self.log.write("event", what="kart_id_mismatch", config=self.configured_id, heltec=self.disp.heltec_id)
                print(f"{wall_iso()} WARNING: config kart_id {self.configured_id} but the Heltec says kart "
                      f"{self.disp.heltec_id}; showing the Heltec's id (fix config.json or the Heltec's `id`)", flush=True)
            game = []
            if t == "cmd":
                rec = self.cmds.on_cmd_line(msg, wall)
                if rec:
                    self.log.write("cmd_echo", id=rec["id"], seq=rec["seq"], result=rec["result_name"])
                res = self.game.on_cmd(msg, now)
                if res:
                    self.log.write("game", ev="cmd_echo", seq=msg.get("seq"), arg=msg.get("arg"),
                                   result=res["result_name"])
            elif t == "self":
                self.cmds.on_self(msg)
                game = self.game.on_self(msg, now, wall)
            elif t == "gps":
                game = self.game.on_gps(msg, now)
            elif t == "race":
                game = self.game.on_race(msg, now, wall)
            d = self.demo
            demo_finish = (demo and d is not None and not self.game.race["finished"]
                           and any(r.get("ev") == "lap" and r.get("lap", 0) >= DEMO_FINISH_LAPS for r in game))
        self._game_out(game)
        if demo_finish:                    # demo: after 3 laps the simulated pit flags the kart home in P1
            d.write_text("finish 1")
            self.log.write("event", what="demo_finish", laps=DEMO_FINISH_LAPS, position=1)

    def _on_link_state(self, connected, desc):
        with self.lock:
            self.link_since = wall_iso()
            if self.demo is not None:
                self.log.write("event", what="link_up" if connected else "link_down", desc=desc, during="demo")
                return
            self.disp.on_link_change()
            if connected:
                self.disp.simulation = False
                self._status_due = time.monotonic() + 1.0     # ask for fw/radio/gps details soon
            else:
                self.cmds.on_link_down()
        self.log.write("event", what="link_up" if connected else "link_down", desc=desc)
        print(f"{wall_iso()} heltec link {'UP' if connected else 'DOWN'}: {desc}", flush=True)

    def _send_text(self, text, wait=True):
        """wait=False only queues the line (game commands, from the reader thread: nothing may block it)."""
        d = self.demo
        if d is not None:                  # demoing: NEVER the real Heltec
            ok = d.write_text(text)
            self.log.write("tx", line=text, ok=ok, demo=True)
            return ok
        ok = self.link.write_text(text) if wait else self.link.write_text(text, wait=False)
        self.log.write("tx", line=text, ok=ok, **({} if wait else {"queued": True}))
        return ok

    def _connected(self):
        return True if self.demo is not None else self.link.connected

    def _game_out(self, recs):
        """Log every game record; send the command lines (outside the app lock)."""
        for r in recs:
            if r["ev"] == "cmd":
                sent = self._connected() and self._send_text(r["line"], wait=False)
                self.log.write("game", **r, sent=bool(sent))
            else:
                self.log.write("game", **r)
                if r["ev"] in ("game_on", "game_off", "lap", "boost", "zones_updated", "lap_reset"):
                    print(f"{wall_iso()} game: {r}", flush=True)
        if recs:
            self._state_cache = (0.0, b"{}")
            self._parts_cache = (0.0, {})
            if self.demo is None and any(r["ev"] in kartcore.GAME_TIMING_EVENTS for r in recs):
                self._save_game_state()

    # -- background ------------------------------------------------------------
    def start(self):
        self.link.start()
        self.maps.start()
        threading.Thread(target=self._ticker, name="ticker", daemon=True).start()
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
        """Status transitions go to the session log and the journal (same as the pit server)."""
        self.log.write("event", what="health", status=snap["status"], prev=prev,
                       throttled=snap["throttled"], temp_c=snap["temp_c"])
        print(f"{wall_iso()} health {prev} -> {snap['status']} throttled={snap['throttled']} "
              f"temp={snap['temp_c']}C", flush=True)

    def _ticker(self):
        while not self.stopping.wait(0.25):
            now = time.monotonic()
            d = self.demo
            if d is not None and d.expired(now):
                self.demo_stop("timer", why=f"{self.cfg.get('demo_max_s', 180)} s limit")
                d = None
            zones = d.zones if d is not None else self.maps.zones()
            pkg = self.maps.package
            with self.lock:
                self.cmds.tick(now)
                self.game.set_pit(pkg.get("pit") if pkg else None)
                game = self.game.set_zones(zones, now)
                if self._game_pending is not None and d is None:
                    game += self.game.import_state(self._game_pending, now)
                    self._game_pending = None
                game += self.game.tick(now)
                due = self._connected() and self._status_due and now >= self._status_due
                if due:
                    self._status_due = now + float(self.cfg["status_poll_s"])
            self._game_out(game)
            if due:
                self._send_text("status", wait=False)
            self.log.flush()

    # -- game timing across restarts -------------------------------------------
    def _load_game_state(self):
        try:
            with open(self.game_state_path) as f:
                doc = json.load(f)
            return doc if isinstance(doc, dict) else None
        except (OSError, ValueError):
            return None

    def _save_game_state(self):
        with self.lock:
            doc = self.game.export_state(time.monotonic())
        if doc == self._game_saved:
            return
        self._game_saved = doc
        tmp = self.game_state_path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(doc, f, separators=(",", ":"))
            os.replace(tmp, self.game_state_path)
        except OSError:
            pass

    # -- state -----------------------------------------------------------------
    def state(self):
        now = time.monotonic()
        with self.lock:
            d = self.disp.snapshot(now)
            cmds = self.cmds.snapshot(now)
            game = self.game.snapshot(now)
        dm = self.demo
        mp = self.maps.snapshot()
        if dm is not None:
            mp["zones"] = dm.zones
        if game.get("track") and isinstance(mp.get("zones"), dict):
            mp["zones"] = {k: v for k, v in mp["zones"].items() if k != "track"}    # sent once, as game.track
        lsnap = self.link.snapshot() if hasattr(self.link, "snapshot") else {}
        d.update({
            "server": {"version": VERSION, "time": wall_iso(), "log_file": os.path.basename(self.log.path),
                       "log_errors": self.log.errors, "config_warnings": self.config_warnings,
                       "health": self.health.snapshot(),
                       "link": {"connected": self._connected(),
                                # "up" | "reconnecting" (was up, retrying) | "connecting" (never up yet) | "demo"
                                "state": "demo" if dm is not None else lsnap.get("state",
                                         "up" if self.link.connected else "reconnecting"),
                                "attempts": lsnap.get("attempts"), "error": lsnap.get("last_error"),
                                "down_s": round(time.time() - lsnap["down_since"], 1)
                                          if lsnap.get("down_since") and dm is None else None,
                                "desc": dm.description if dm is not None else self.link.description,
                                "since": self.link_since}},
            "demo": self.demo_info(),
            "sound_test": {"seq": self.sound_test_seq,
                           "age_s": None if self.sound_test_mono is None else round(now - self.sound_test_mono, 1)},
            "config": {k: self.cfg.get(k) for k in PUBLIC_CFG},
            "kiosk": self.kiosk_info(now),
            "commands": cmds,
            "map": mp,
            "game": game,
        })
        return d

    def heartbeat(self, body):
        """POST /api/heartbeat from the kiosk page every 5 s: proves the page is alive (the kiosk
        watchdog in start-chromium.sh restarts Chromium when it stops) and carries frame stats."""
        keep = ("draws", "draw_ms_avg", "draw_ms_max", "renders", "render_ms_avg", "layer_builds", "secs", "hidden")
        self.kiosk = {k: body.get(k) for k in keep if isinstance(body.get(k), (int, float, bool))}
        self.kiosk_mono = time.monotonic()

    def kiosk_info(self, now=None):
        now = time.monotonic() if now is None else now
        return {"age_s": None if self.kiosk_mono is None else round(now - self.kiosk_mono, 1),
                "server_up_s": round(now - self.started_mono, 1), "stats": self.kiosk}

    def state_json(self):
        t, data = self._state_cache
        if time.monotonic() - t < 0.15:
            return data
        data = json.dumps(self.state(), separators=(",", ":")).encode()
        self._state_cache = (time.monotonic(), data)
        return data

    def sse_parts(self):
        """The state as {top-level key: JSON bytes} for /events, built at most every 0.15 s and shared
        by all clients. Each client is sent only the keys that changed since its previous frame (the
        page merges them); keys the driver page never reads are left out (they stay in /api/state)."""
        t, parts = self._parts_cache
        if time.monotonic() - t < 0.15 and parts:
            return parts
        st = self.state()
        parts = {k: json.dumps(v, separators=(",", ":")).encode() for k, v in st.items() if k not in SSE_SKIP}
        self._parts_cache = (time.monotonic(), parts)
        return parts

    def command(self, body, who):
        cmd, arg = body.get("cmd"), body.get("arg")
        line = kartcore.command_text(cmd, arg)          # raises CommandError
        if not self._connected():
            self.log.write("op", who=who, action="cmd", request=body, refused="heltec link down")
            raise kartcore.CommandError("Heltec not connected; nothing sent")
        ok = self._send_text(line)
        now, wall = time.monotonic(), wall_iso()
        with self.lock:
            rec = self.cmds.sent(cmd, arg, line, ok, now, wall, who)
            out = {k: v for k, v in rec.items() if k != "sent_mono"}
        self._state_cache = (0.0, b"{}")                 # next SSE frame shows the request at once
        self._parts_cache = (0.0, {})
        self.log.write("op", who=who, action="cmd", request=body, line=line, sent=ok, id=rec["id"])
        if not ok:
            raise kartcore.CommandError("write to the Heltec failed; link down")
        return out


class Handler(BaseHTTPRequestHandler):
    server_version = "GoKartKart/" + VERSION
    app: App = None

    def log_message(self, fmt, *args):
        pass

    def _local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", cache=False):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, separators=(",", ":")), "application/json")

    def _file(self, path, cache=False):
        ext = os.path.splitext(path or "")[1].lower()
        if not path or ext not in MIME or not os.path.isfile(path):
            return self._send(404, "not found")
        with open(path, "rb") as f:
            data = f.read()
        self._send(200, data, MIME[ext], cache=cache)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        p = urlparse(self.path).path
        app = self.app
        if p == "/healthz":
            return self._send(200, "ok")
        if p in ("/", "/index.html"):
            return self._file(os.path.join(STATIC_DIR, "index.html"))
        if p.startswith("/static/"):
            return self._file(os.path.join(STATIC_DIR, os.path.basename(p)))
        if p == "/map/package.json":
            return self._json(200, app.maps.snapshot())
        if p == "/map/zones.json":
            z = app.maps.zones()
            return self._json(200, z) if z is not None else self._send(404, "no zones cached")
        if p.startswith("/map/"):
            # imagery names change with each package version, so the browser may cache them
            return self._file(app.maps.file_path(p[len("/map/"):]), cache=True)
        if p == "/api/state":
            return self._send(200, app.state_json(), "application/json")
        if p == "/api/kiosk":                 # for the kiosk watchdog (start-chromium.sh)
            return self._json(200, app.kiosk_info())
        if p == "/events":
            return self._events()
        if p == "/api/sound-test":
            if not self._local() and not app.cfg.get("remote_cmd"):
                return self._json(403, {"ok": False, "error": "sound test only from the kart itself"})
            q = parse_qs(urlparse(self.path).query)
            return self._json(200, app.sound_test(alsa=q.get("alsa", ["0"])[0] == "1"))
        return self._send(404, "not found")

    def _events(self):
        """~5 Hz frames. The first is the full state; after that only the top-level keys whose JSON
        changed, as {"_d":1, key: value, ...} (the page merges them into its copy)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last = {}
        try:
            self.wfile.write(b"retry: 1000\n\n")
            while not self.app.stopping.is_set():
                parts = self.app.sse_parts()
                changed = [k for k, v in parts.items() if last.get(k) != v]
                body = b",".join(b'"' + k.encode() + b'":' + parts[k] for k in changed)
                if last:
                    body = b'{"_d":1' + (b"," + body if body else b"") + b"}"
                else:
                    body = b"{" + body + b"}"
                last = parts
                self.wfile.write(b"data: " + body + b"\n\n")
                self.wfile.flush()
                time.sleep(SSE_PERIOD_S)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_POST(self):
        p = urlparse(self.path).path
        app = self.app
        who = "kiosk@localhost" if self._local() else f"remote@{self.client_address[0]}"
        n = int(self.headers.get("Content-Length") or 0)
        if n > 4096:
            return self._json(413, {"ok": False, "error": "body too large"})
        raw = self.rfile.read(n) if n else b""
        if p == "/api/map/refresh":
            app.maps.poke()
            return self._json(200, {"ok": True})
        if p == "/api/heartbeat":
            if self._local():                 # only the kiosk on this Pi counts
                try:
                    body = json.loads(raw or b"{}")
                    app.heartbeat(body if isinstance(body, dict) else {})
                except ValueError:
                    pass
            return self._json(200, {"ok": True})
        if p == "/api/demo":
            if not self._local() and not app.cfg.get("remote_cmd"):
                return self._json(403, {"ok": False, "error": "demo only from the kart's own screen"})
            try:
                body = json.loads(raw or b"{}")
                on = (not app.demo) if body.get("toggle") else bool(body.get("on"))
                info = app.demo_start(who) if on else app.demo_stop(who)
            except (ValueError, AttributeError) as e:
                return self._json(400, {"ok": False, "error": f"bad request: {e}"})
            except kartcore.CommandError as e:
                return self._json(409, {"ok": False, "error": str(e)})
            return self._json(200, {"ok": True, "demo": info})
        if p != "/cmd":
            return self._json(404, {"ok": False, "error": "not found"})
        if not self._local() and not app.cfg.get("remote_cmd"):
            app.log.write("op", who=who, action="cmd", refused="remote commands disabled")
            return self._json(403, {"ok": False, "error": "commands only from the kart's own screen "
                                                          "(set remote_cmd in config.json to allow)"})
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
        except ValueError as e:
            return self._json(400, {"ok": False, "error": f"bad json: {e}"})
        try:
            return self._json(200, {"ok": True, "command": app.command(body, who)})
        except kartcore.CommandError as e:
            return self._json(409, {"ok": False, "error": str(e)})


def load_config(path):
    """DEFAULTS + config.json, validated. Returns (cfg, warnings). A broken config.json never stops
    the display: it is reported and the defaults are used."""
    return validate_config(*read_config(path))


def read_config(path):
    cfg, warnings = dict(DEFAULTS), []
    if path and os.path.exists(path):
        try:
            with open(path) as f:
                user = json.load(f)
            if not isinstance(user, dict):
                raise ValueError("not a JSON object")
            cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
        except (OSError, ValueError) as e:
            warnings.append(f"{path}: {e}; using the defaults")
    return cfg, warnings


def validate_config(cfg, warnings=None):
    warnings = [] if warnings is None else warnings
    for k in sorted(set(cfg) - set(DEFAULTS)):
        warnings.append(f"unknown config key {k!r} ignored (typo?)")
    for k, (typ, lo, hi) in CONFIG_RULES.items():
        v = cfg.get(k)
        if v is None and k == "kart_id":
            continue
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi
        if ok and typ is int and v != int(v):
            ok = False
        if not ok:
            warnings.append(f"config {k}={v!r} invalid ({lo}-{hi}); using {DEFAULTS[k]!r}")
            cfg[k] = DEFAULTS[k]
        else:
            cfg[k] = typ(v)
    for k in CONFIG_BOOLS:
        if not isinstance(cfg.get(k), bool):
            warnings.append(f"config {k}={cfg.get(k)!r} is not true/false; using {DEFAULTS[k]!r}")
            cfg[k] = DEFAULTS[k]
    for k, choices in CONFIG_CHOICES.items():
        if cfg.get(k) not in choices:
            warnings.append(f"config {k}={cfg.get(k)!r} not one of {choices}; using {DEFAULTS[k]!r}")
            cfg[k] = DEFAULTS[k]
    urls = cfg.get("pit_urls")
    if isinstance(urls, str):
        cfg["pit_urls"] = [urls]
    elif not (isinstance(urls, list) and all(isinstance(u, str) for u in urls)):
        warnings.append(f"config pit_urls={urls!r} is not a list of URLs; using the defaults")
        cfg["pit_urls"] = list(DEFAULTS["pit_urls"])
    return cfg, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--port", help="serial device (default: auto-find the Heltec)")
    ap.add_argument("--tcp", help="host:port of the simulator's TCP server")
    ap.add_argument("--http-port", type=int)
    ap.add_argument("--bind")
    ap.add_argument("--log-dir")
    ap.add_argument("--cache-dir")
    ap.add_argument("--pit-url", action="append", help="pit base URL (repeatable; overrides config pit_urls)")
    ap.add_argument("--kart-id", type=int)
    ap.add_argument("--beginner", action="store_true", help="beginner mode (hide driver fault alerts)")
    args = ap.parse_args(argv)

    cfg, warnings = read_config(args.config)
    for key, val in (("serial_port", args.port), ("tcp", args.tcp), ("http_port", args.http_port),
                     ("bind", args.bind), ("log_dir", args.log_dir), ("cache_dir", args.cache_dir),
                     ("pit_urls", args.pit_url), ("kart_id", args.kart_id)):
        if val is not None:
            cfg[key] = val
    if args.beginner:
        cfg["beginner_mode"] = True
    cfg, warnings = validate_config(cfg, warnings)

    app = App(cfg)
    app.config_warnings = warnings
    for w in warnings:
        print(f"{wall_iso()} CONFIG: {w}", flush=True)
        app.log.write("event", what="config_warning", msg=w)
    Handler.app = app
    httpd = ThreadingHTTPServer((cfg["bind"], int(cfg["http_port"])), Handler)
    httpd.daemon_threads = True
    app.start()
    print(f"{wall_iso()} kart display {VERSION} kart {cfg.get('kart_id')} on http://{cfg['bind']}:{cfg['http_port']} "
          f"source={cfg.get('tcp') or cfg.get('serial_port') or 'auto'} pit={cfg['pit_urls']} log={app.log.path}",
          flush=True)
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.stopping.set()
        if app.demo:
            app.demo_stop("shutdown")
        app.link.stop()
        app.maps.stop()
        app.log.write("event", what="server_stop")
        app.log.close()


if __name__ == "__main__":
    main()
