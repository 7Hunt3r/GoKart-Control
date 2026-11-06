"""Course map sync: pit /map/package.json -> local cache -> served at /map/<file>.

The pit (pit/server/pit_server.py) serves its course map package without a
login at GET /map/package.json and the image at /map/<file> (see
pit/server/map/README.md). On course day there is no internet and the pit
Wi-Fi may be out of range on track, so the display ALWAYS draws from the
local cache; the pit is only polled to refresh it:

- every `interval` seconds try each pit URL in order (mDNS name, then the IP);
- if the package `version` differs from the cached one (or the cached image is
  missing) download the image to a temp file, check it, rename it into place,
  then write the package (image first, package last, so a cut-off download
  never leaves a package pointing at a half image);
- same version: only the metadata is refreshed (the pit's origin can move with
  its GPS fix without a version bump); the image is not re-downloaded;
- unreachable pit: nothing changes, the cached copy keeps being served.

Standard library only.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urljoin

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_PACKAGE_BYTES = 256 * 1024
PACKAGE = "package.json"


def _wall():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class MapSync:
    def __init__(self, pit_urls, cache_dir, interval=30.0, timeout=5.0, log=print):
        if isinstance(pit_urls, str):
            pit_urls = [pit_urls]
        self.pit_urls = [u.rstrip("/") for u in pit_urls if u]
        self.cache_dir = cache_dir
        self.interval = float(interval)
        self.timeout = float(timeout)
        self.log = log
        os.makedirs(cache_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.package = self._load_cached()
        self.status = {"reachable": None, "pit_url": None, "last_try_wall": None, "last_ok_wall": None,
                       "last_error": None, "last_update_wall": None, "downloads": 0}

    # -- public ----------------------------------------------------------------
    def start(self):
        threading.Thread(target=self._run, name="map-sync", daemon=True).start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def poke(self):
        """Poll now (e.g. from /api/map/refresh)."""
        self._wake.set()

    def snapshot(self):
        with self._lock:
            pkg = dict(self.package) if self.package else None
            st = dict(self.status)
        return {"package": pkg, "sync": st, "cached": pkg is not None}

    def file_path(self, name):
        """Absolute path of a cached file that may be served, or None."""
        name = os.path.basename(name)
        if not name or name.startswith(".") or name == PACKAGE:
            return None
        if os.path.splitext(name)[1].lower() not in IMAGE_EXT:
            return None
        p = os.path.join(self.cache_dir, name)
        return p if os.path.isfile(p) else None

    # -- loop ------------------------------------------------------------------
    def _run(self):
        while not self._stop.is_set():
            self.sync_once()
            self._wake.wait(self.interval)
            self._wake.clear()

    def sync_once(self):
        """One poll of the pit. Returns True if the cache changed."""
        errors = []
        with self._lock:
            self.status["last_try_wall"] = _wall()
        for base in self.pit_urls:
            try:
                pkg = self._get_json(base + "/map/package.json")
            except Exception as e:          # noqa: BLE001 - any failure: try the next URL
                errors.append(f"{base}: {e}")
                continue
            try:
                changed = self._apply(base, pkg)
            except Exception as e:          # noqa: BLE001 - reachable pit, bad package/image
                with self._lock:
                    self.status.update(reachable=True, pit_url=base, last_error=f"{base}: {e}")
                self.log(f"map sync: {base}: {e} (keeping cached map)")
                return False
            with self._lock:
                self.status.update(reachable=True, pit_url=base, last_ok_wall=_wall(), last_error=None)
            return changed
        with self._lock:
            was = self.status["reachable"]
            self.status.update(reachable=False, last_error="; ".join(errors)[:400])
        if was is not False:
            self.log("map sync: pit unreachable, using the cached map" if self.package
                     else "map sync: pit unreachable and no cached map yet")
        return False

    # -- internals -------------------------------------------------------------
    def _get(self, url, limit):
        req = urllib.request.Request(url, headers={"User-Agent": "gokart-kart-display"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            if r.status != 200:
                raise urllib.error.HTTPError(url, r.status, "bad status", r.headers, None)
            data = r.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"{url} larger than {limit} bytes")
        return data

    def _get_json(self, url):
        obj = json.loads(self._get(url, MAX_PACKAGE_BYTES))
        if not isinstance(obj, dict):
            raise ValueError("package is not a JSON object")
        return obj

    def _apply(self, base, pkg):
        version = pkg.get("version")
        if not isinstance(version, (int, float, str)):
            raise ValueError("package has no version")
        remote_img = pkg.get("image")
        name = os.path.basename(remote_img) if remote_img else None
        if name and os.path.splitext(name)[1].lower() not in IMAGE_EXT:
            raise ValueError(f"image type not allowed: {name}")
        cur = self.package
        need_image = bool(name) and (cur is None or cur.get("version") != version
                                     or cur.get("image_file") != name
                                     or not os.path.isfile(os.path.join(self.cache_dir, name)))
        if need_image:
            data = self._get(urljoin(base + "/", remote_img.lstrip("/")), MAX_IMAGE_BYTES)
            if not _looks_like_image(data):
                raise ValueError(f"{name}: download is not a JPEG/PNG/WebP")
            self._atomic_write(name, data)
            with self._lock:
                self.status["downloads"] += 1
            self.log(f"map sync: downloaded {name} ({len(data)} bytes) for package version {version}")
        local = {k: pkg.get(k) for k in ("version", "bounds", "origin", "origin_source", "course",
                                          "pit", "source", "attribution")}
        local["image"] = f"/map/{name}" if name else None
        local["image_file"] = name
        local["fetched_from"] = base
        changed = cur is None or {k: v for k, v in cur.items() if k not in ("fetched_wall",)} \
            != {k: v for k, v in local.items()}
        if not changed:
            return False
        local["fetched_wall"] = _wall()
        self._atomic_write(PACKAGE, json.dumps(local, indent=1).encode())
        with self._lock:
            self.package = local
            self.status["last_update_wall"] = local["fetched_wall"]
        if cur is None or cur.get("version") != version:
            self.log(f"map sync: package version {cur.get('version') if cur else None} -> {version}")
        self._prune(keep={PACKAGE, name})
        return True

    def _atomic_write(self, name, data):
        fd, tmp = tempfile.mkstemp(prefix=".dl-", dir=self.cache_dir)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, os.path.join(self.cache_dir, name))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _prune(self, keep):
        for n in os.listdir(self.cache_dir):
            if n in keep:
                continue
            if n.startswith(".dl-") or os.path.splitext(n)[1].lower() in IMAGE_EXT:
                try:
                    os.unlink(os.path.join(self.cache_dir, n))
                except OSError:
                    pass

    def _load_cached(self):
        try:
            with open(os.path.join(self.cache_dir, PACKAGE)) as f:
                pkg = json.load(f)
            if isinstance(pkg, dict):
                return pkg
        except (OSError, ValueError):
            pass
        return None


def _looks_like_image(b):
    return b[:3] == b"\xff\xd8\xff" or b[:8] == b"\x89PNG\r\n\x1a\n" or (b[:4] == b"RIFF" and b[8:12] == b"WEBP")
