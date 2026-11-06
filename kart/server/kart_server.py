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
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

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
    "sound": True,                      # driver page: synthesized boost/lap sounds (WebAudio)
}
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".svg": "image/svg+xml"}
SSE_PERIOD_S = 0.2
PUBLIC_CFG = ("kart_id", "beginner_mode", "map_orientation", "follow_span_m", "follow_resume_s",
              "mode_hold_ms", "map_fps", "game", "sound", "demo")


def wall_iso(ts=None):
    return datetime.fromtimestamp(time.time() if ts is None else ts, timezone.utc) \
        .isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SessionLog:
    """JSONL, one file per server start: every serial line (rx/tx) and every event."""

    def __init__(self, log_dir, max_mb):
        os.makedirs(log_dir, exist_ok=True)
        self.dir = log_dir
        self.path = os.path.join(log_dir, "session-" + time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
        self._prune(max_mb)
        self._f = open(self.path, "a", buffering=1, encoding="utf-8")
        self._lock = threading.Lock()

    def _prune(self, max_mb):
        files = sorted(f for f in os.listdir(self.dir) if f.startswith("session-") and f.endswith(".jsonl"))
        sizes = {f: os.path.getsize(os.path.join(self.dir, f)) for f in files}
        total = sum(sizes.values())
        while files and total > max_mb * 1024 * 1024:
            f = files.pop(0)
            total -= sizes[f]
            try:
                os.unlink(os.path.join(self.dir, f))
            except OSError:
                pass

    def write(self, kind, **fields):
        rec = {"ts": wall_iso(), "mono": round(time.monotonic(), 3), "kind": kind}
        rec.update(fields)
        line = json.dumps(rec, separators=(",", ":"), default=str)
        with self._lock:
            try:
                self._f.write(line + "\n")
            except OSError:
                pass                    # a full SD card must not stop the display


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.log = SessionLog(cfg.get("log_dir") or os.path.join(KART_ROOT, "logs"), float(cfg["max_log_mb"]))
        cid = cfg.get("kart_id")
        self.configured_id = int(cid) if cid else None
        self._fresh_state()
        self.demo = None                 # Demo while demoing: it replaces the Heltec for reads AND writes
        self.link = Link(self._on_line, self._on_link_state, port=cfg.get("serial_port"),
                         tcp=cfg.get("tcp"), log=self._console)
        self.maps = MapSync(cfg["pit_urls"], cfg.get("cache_dir") or os.path.join(KART_ROOT, "cache", "map"),
                            interval=float(cfg["map_poll_s"]), log=self._map_log)
        self.link_since = None
        self.stopping = threading.Event()
        self._state_cache = (0.0, b"{}")
        self._status_due = 0.0
        self.log.write("event", what="server_start", version=VERSION, pid=os.getpid(),
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
            kid = self.disp.heltec_id or self.configured_id or 2
            self._fresh_state()
            self.demo_source = source
            self.demo = Demo(doc, kid, self._on_demo_line, max_s=float(self.cfg.get("demo_max_s", 180)))
        self._state_cache = (0.0, b"{}")
        self.log.write("event", what="demo_start", who=who, zones=source, path_m=self.demo.path["length_m"],
                       path_from=self.demo.path.get("source"))
        print(f"{wall_iso()} DEMO start ({source} zones, loop {self.demo.path['length_m']} m) by {who}", flush=True)
        return self.demo_info()

    def demo_stop(self, who, why="stopped"):
        d = self.demo
        if not d:
            return self.demo_info()
        with self.lock:
            self.demo = None
            d.stop()
            self._fresh_state()           # the real Heltec's next lines repopulate it
            self._status_due = time.monotonic() + 1.0
        self._state_cache = (0.0, b"{}")
        self.log.write("event", what="demo_stop", who=who, why=why, sent_to_sim=len(d.writes))
        print(f"{wall_iso()} DEMO stop ({why}) by {who}", flush=True)
        return self.demo_info()

    def demo_info(self):
        d = self.demo
        if not d:
            return {"active": False, "allowed": bool(self.cfg.get("demo", True))}
        return {"active": True, "allowed": True, "left_s": round(d.left_s(), 1), "zones": self.demo_source,
                "path_m": d.path["length_m"], "path_from": d.path.get("source")}

    def _on_demo_line(self, text):
        if self.demo is None:
            return
        self._ingest(text, demo=True)

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

    def _ingest(self, text, demo=False):
        now, wall = time.monotonic(), wall_iso()
        if demo:
            self.log.write("rx", line=text, demo=True)
        else:
            self.log.write("rx", line=text)
        try:
            msg = json.loads(text)
        except ValueError:
            msg = None
        with self.lock:
            self.disp.rx_lines += 1
            if not isinstance(msg, dict):
                self.disp.rx_bad += 1        # boot chatter, partial lines: logged, not shown
                return
            t = self.disp.ingest(msg, now, wall)
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
                game = self.game.on_self(msg, now)
            elif t == "gps":
                game = self.game.on_gps(msg, now)
            elif t == "race":
                game = self.game.on_race(msg, now)
        self._game_out(game)

    def _on_link_state(self, connected, desc):
        if self.demo is not None:
            self.log.write("event", what="link_up" if connected else "link_down", desc=desc, during="demo")
            return
        with self.lock:
            self.link_since = wall_iso()
            self.disp.on_link_change()
            if connected:
                self.disp.simulation = False
                self._status_due = time.monotonic() + 1.0     # ask for fw/radio/gps details soon
            else:
                self.cmds.on_link_down()
        self.log.write("event", what="link_up" if connected else "link_down", desc=desc)
        print(f"{wall_iso()} heltec link {'UP' if connected else 'DOWN'}: {desc}", flush=True)

    def _send_text(self, text):
        d = self.demo
        if d is not None:                  # demoing: NEVER the real Heltec
            ok = d.write_text(text)
            self.log.write("tx", line=text, ok=ok, demo=True)
            return ok
        ok = self.link.write_text(text)
        self.log.write("tx", line=text, ok=ok)
        return ok

    def _connected(self):
        return True if self.demo is not None else self.link.connected

    def _game_out(self, recs):
        """Log every game record; send the command lines (outside the app lock)."""
        for r in recs:
            if r["ev"] == "cmd":
                sent = self._connected() and self._send_text(r["line"])
                self.log.write("game", **r, sent=bool(sent))
            else:
                self.log.write("game", **r)
                if r["ev"] in ("game_on", "game_off", "lap", "boost", "zones_updated", "lap_reset"):
                    print(f"{wall_iso()} game: {r}", flush=True)
        if recs:
            self._state_cache = (0.0, b"{}")

    # -- background ------------------------------------------------------------
    def start(self):
        self.link.start()
        self.maps.start()
        threading.Thread(target=self._ticker, name="ticker", daemon=True).start()

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
                game = self.game.set_zones(zones, now) + self.game.tick(now)
                due = self._connected() and self._status_due and now >= self._status_due
                if due:
                    self._status_due = now + float(self.cfg["status_poll_s"])
            self._game_out(game)
            if due:
                self._send_text("status")

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
        d.update({
            "server": {"version": VERSION, "time": wall_iso(), "log_file": os.path.basename(self.log.path),
                       "link": {"connected": self._connected(),
                                "desc": dm.description if dm is not None else self.link.description,
                                "since": self.link_since}},
            "demo": self.demo_info(),
            "config": {k: self.cfg.get(k) for k in PUBLIC_CFG},
            "commands": cmds,
            "map": mp,
            "game": game,
        })
        return d

    def state_json(self):
        t, data = self._state_cache
        if time.monotonic() - t < 0.15:
            return data
        data = json.dumps(self.state(), separators=(",", ":")).encode()
        self._state_cache = (time.monotonic(), data)
        return data

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
        if p == "/events":
            return self._events()
        return self._send(404, "not found")

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 1000\n\n")
            while not self.app.stopping.is_set():
                self.wfile.write(b"data: " + self.app.state_json() + b"\n\n")
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
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path) as f:
            user = json.load(f)
        cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
    return cfg


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

    cfg = load_config(args.config)
    for key, val in (("serial_port", args.port), ("tcp", args.tcp), ("http_port", args.http_port),
                     ("bind", args.bind), ("log_dir", args.log_dir), ("cache_dir", args.cache_dir),
                     ("pit_urls", args.pit_url), ("kart_id", args.kart_id)):
        if val is not None:
            cfg[key] = val
    if args.beginner:
        cfg["beginner_mode"] = True

    app = App(cfg)
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


if __name__ == "__main__":
    main()
