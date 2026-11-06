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


def wall_iso(ts=None):
    return datetime.fromtimestamp(time.time() if ts is None else ts, timezone.utc) \
        .isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
class SessionLog:
    """JSONL log, one new file per server start. Every serial line in both
    directions plus every operator action and event, each timestamped."""

    def __init__(self, log_dir):
        os.makedirs(log_dir, exist_ok=True)
        self.path = os.path.join(log_dir, "session-" + time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
        self._f = open(self.path, "a", buffering=1, encoding="utf-8")
        self._lock = threading.Lock()
        self.recent_all = deque(maxlen=500)
        self.recent_events = deque(maxlen=300)   # everything except raw rx/tx lines

    def write(self, kind, **fields):
        rec = {"ts": wall_iso(), "mono": round(time.monotonic(), 3), "kind": kind}
        rec.update(fields)
        line = json.dumps(rec, separators=(",", ":"), default=str)
        with self._lock:
            self._f.write(line + "\n")
            self.recent_all.append(rec)
            if kind not in ("rx", "tx"):
                self.recent_events.append(rec)

    def recent(self, n=100, include_lines=False):
        with self._lock:
            src = self.recent_all if include_lines else self.recent_events
            return list(src)[-n:]


# ---------------------------------------------------------------------------
class App:
    def __init__(self, cfg, map_dir):
        self.cfg = cfg
        self.map_dir = map_dir
        self.lock = threading.RLock()
        log_dir = cfg.get("log_dir") or os.path.join(PIT_ROOT, "logs")
        self.log = SessionLog(log_dir)
        self.seq_file = os.path.join(log_dir, "cmdseq.json")
        self.sessions = {}                       # cookie token -> operator name
        self.map_cfg = self._load_map()
        fallback = self.map_cfg.get("origin") or cfg.get("origin")
        self.fleet = pitcore.Fleet(fallback_origin=fallback, pit_marker=self.map_cfg.get("pit"),
                                   gps_correction=bool(cfg.get("gps_correction", False)))
        self.zones_path = os.path.join(map_dir, "zones.json")
        self.zones_comment = None
        self._zones_mtime = None
        self._load_zones()
        self.link = Link(self._on_line, self._on_link_state, port=cfg.get("serial_port"),
                         tcp=cfg.get("tcp"), log=self._console)
        self.cmds = pitcore.CommandManager(self.fleet, self._send, log=self._cmd_log,
                                           start_seq=self._load_seq(), ttl=int(cfg["cmd_ttl_s"]))
        self.link_since = None
        self._saved_seq = self.cmds.next_seq
        self._state_cache = (0.0, b"{}")
        self.stopping = threading.Event()
        self.log.write("event", what="server_start", version=VERSION, pid=os.getpid(),
                       source=cfg.get("tcp") or cfg.get("serial_port") or "auto",
                       start_seq=self.cmds.next_seq)

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
        path = os.path.join(self.map_dir, "map.json")
        try:
            with open(path) as f:
                m = json.load(f)
            return m if isinstance(m, dict) else {}
        except (OSError, ValueError):
            return {}

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
            with self.lock:
                self.fleet.game.set_doc(pitcore.EMPTY_ZONES)
            return
        try:
            with open(self.zones_path) as f:
                raw = json.load(f)
            doc = pitcore.validate_zones(raw)
            ver = raw.get("version", 0)
            doc["version"] = ver if isinstance(ver, int) and not isinstance(ver, bool) and ver >= 0 else 0
        except (OSError, ValueError) as e:
            self.log.write("event", what="zones_invalid", path=self.zones_path, error=str(e))
            print(f"{wall_iso()} zones.json ignored: {e}", flush=True)
            return
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
        tmp = self.zones_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(out, f, indent=2)
            f.write("\n")
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
        self._state_cache = (0.0, b"{}")        # next /api/state and SSE frame show the change
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
        self._state_cache = (0.0, b"{}")        # next /api/state and SSE frame show the change
        self.log.write("op", who=who, action="zones_enable", enabled=en, version=doc["version"])
        return {"enabled": en, "version": doc["version"]}

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
        self._state_cache = (0.0, b"{}")        # next /api/state and SSE frame show the change
        self.log.write("op", who=who, action="race_start", secs=secs, laps=laps, id=d["id"], seq=d["seq"],
                       go_wall=wall_iso(t + secs))
        return {"command": d, "go_wall": wall_iso(t + secs), "secs": secs, "laps": laps}

    def _online(self, now):
        return [k for k in pitcore.KART_IDS if self.fleet.is_online(k, now)]

    def _send_finishes(self, finishes, who, now, wall):
        """One `race` command per kart: arg 100 + position, or 0 (finish, no position). Lock held."""
        out = []
        for kart, arg in finishes:
            c = self.cmds.submit("race", kart, arg, source=f"{who} (race finish)", now=now, wall=wall)
            out.append({"kart": kart, "arg": arg, "id": c.id})
        return out

    def race_stop(self, who):
        """STOP RACE: final results, every kart gets its finish command (ZONES.md "Race finish")."""
        if not self.link.connected:
            raise pitcore.CommandError("T-Beam link is down; race stop not sent")
        now, wall = time.monotonic(), wall_iso()
        with self.lock:
            fin = self.fleet.game.race_stop(self._online(now), wall)
            sent = self._send_finishes(fin, who, now, wall)
            res = self.fleet.game.session.snapshot()
        self._state_cache = (0.0, b"{}")
        self.log.write("op", who=who, action="race_stop", sent=sent, results=res["results"])
        return {"sent": sent, "session": res}

    def reset_laps(self, who):
        with self.lock:
            self.fleet.game.reset_laps()
        self._state_cache = (0.0, b"{}")        # next /api/state and SSE frame show the change
        self.log.write("op", who=who, action="laps_reset")
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
            self._state_cache = (0.0, b"{}")

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

    def _ticker(self):
        while not self.stopping.wait(0.1):
            with self.lock:
                self.cmds.tick(time.monotonic(), wall_iso())
                self._save_seq()

    # -- state ---------------------------------------------------------------
    def state(self):
        now = time.monotonic()
        with self.lock:
            fleet = self.fleet.snapshot(now)
            ge = fleet["race"].get("go_epoch")
            # seconds until GO (negative after) from the server clock, so every page counts down together
            fleet["race"]["go_in_s"] = None if ge is None else round(ge - time.time(), 2)
            cmds = self.cmds.snapshot()
            rx, bad = self.fleet.rx_lines, self.fleet.rx_bad
        return {
            "server": {"version": VERSION, "time": wall_iso(), "log_file": os.path.basename(self.log.path),
                       "link": {"connected": self.link.connected, "desc": self.link.description,
                                "since": self.link_since},
                       "rx_lines": rx, "rx_bad": bad},
            "fleet": fleet,
            "commands": cmds,
            "map": self.map_package(fleet["origin"]),
            "zones": self.zones_doc(),
        }

    def state_json(self):
        t, data = self._state_cache
        if time.monotonic() - t < 0.2:
            return data
        data = json.dumps(self.state(), separators=(",", ":")).encode()
        self._state_cache = (time.monotonic(), data)
        return data

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
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 2000\n\n")
            while not self.app.stopping.is_set():
                self.wfile.write(b"data: " + self.app.state_json() + b"\n\n")
                self.wfile.flush()
                time.sleep(SSE_PERIOD_S)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

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
            if p == "/zones/enable":
                return self._json(200, {"ok": True, **app.enable_zones(body, who)})
            if p == "/race/start":
                return self._json(200, {"ok": True, **app.race_start(body, who)})
            if p == "/race/stop":
                return self._json(200, {"ok": True, **app.race_stop(who)})
            if p == "/laps/reset":
                return self._json(200, {"ok": True, **app.reset_laps(who)})
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
