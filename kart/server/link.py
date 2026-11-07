"""Heltec link: newline-delimited JSON over USB CDC serial (pyserial) or TCP.

Adapted from pit/server/link.py (proven against the T-Beam S3 on the pit Pi 4,
2026-11-05). The kart Heltec V4.3 uses the same ESP32-S3 USB-Serial/JTAG
(303a:1001), so the port handling is unchanged; only the names and the
plain-text write differ (the kart firmware takes text commands like "mode 1").

One reader thread owns the connection, reconnects forever, and hands every
line to `on_line(text)`. Writes go through a separate writer thread so a stuck
USB endpoint can never stall the reader: `write_text(text)` waits (<= 1.5 s)
for the result, `write_text(text, wait=False)` only queues it (used from the
reader thread itself, e.g. game commands). Either returns False when the link
is down. A queued line older than 1 s is dropped, and the queue is emptied
whenever the link drops: commands are never replayed after a reconnect
(AGENTS.md reliability rule). A failed write closes the port so the reader
reconnects at once.

`state`: "connecting" (never connected yet), "up", "reconnecting" (was up,
lost; retrying every 2 s). `attempts`, `last_error`, `up_since`/`down_since`
(time.time()) describe it for the page's link chip.
"""
from __future__ import annotations

import glob
import json
import os
import queue
import socket
import sys
import threading
import time

try:
    import serial                      # pyserial
    from serial.tools import list_ports
except ImportError:                    # TCP/simulator mode still works without it
    serial = None
    list_ports = None

HELTEC_VID, HELTEC_PID = 0x303A, 0x1001
MAX_LINE = 4096
RECONNECT_S = 2.0
WRITE_TIMEOUT_S = 0.5      # pyserial write timeout (a stalled CDC endpoint raises instead of blocking)
WRITE_WAIT_S = 1.5         # write_text(wait=True) gives up after this
QUEUE_MAX_AGE_S = 1.0      # a queued line older than this is dropped, never sent late


def find_heltec_port():
    """Preferred order: /dev/heltec (udev symlink), a VID/PID match, /dev/serial/by-id/*,
    /dev/ttyACM*, then macOS /dev/cu.usbmodem*. Returns a path or None."""
    if os.path.exists("/dev/heltec"):
        return "/dev/heltec"
    if list_ports is not None:
        for p in list_ports.comports():
            if p.vid == HELTEC_VID and p.pid == HELTEC_PID:
                return p.device
    by_id = sorted(glob.glob("/dev/serial/by-id/*"))
    for p in by_id:
        if "Espressif" in p or "303a" in p.lower() or "JTAG_serial" in p:
            return p
    for pattern in ("/dev/serial/by-id/*", "/dev/ttyACM*", "/dev/cu.usbmodem*"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    return None


class Link:
    def __init__(self, on_line, on_state, port=None, tcp=None, log=print):
        """port: explicit serial path, or None to auto-find. tcp: "host:port" (simulator)."""
        self.on_line = on_line
        self.on_state = on_state            # on_state(connected: bool, description: str)
        self.port = port
        self.tcp = tcp
        self.log = log
        self.connected = False
        self.description = "not connected"
        self.state = "connecting"
        self.attempts = 0                   # failed opens since the last good connection
        self.last_error = None
        self.up_since = None
        self.down_since = time.time()
        self.lines = 0
        self.bad_writes = 0
        self._wlock = threading.Lock()
        self._writer = None                 # callable(bytes)
        self._closer = None
        self._gen = 0                       # connection generation: a queued line only goes to its own port
        self._q = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="heltec-link", daemon=True)
        self._wthread = threading.Thread(target=self._write_loop, name="heltec-writer", daemon=True)

    def start(self):
        self._thread.start()
        self._wthread.start()

    def stop(self):
        self._stop.set()
        self._q.put(None)
        self._close()

    def write_line(self, obj, wait=True):
        return self.write_text(json.dumps(obj, separators=(",", ":")), wait=wait)

    def write_text(self, text, wait=True):
        """Send one plain-text command line (e.g. "mode 1"). False if the link is down (or, with
        wait, if the write failed or took longer than WRITE_WAIT_S)."""
        data = (text.strip() + "\n").encode()
        if not self.connected or self._writer is None:
            return False
        done = threading.Event() if wait else None
        item = {"data": data, "gen": self._gen, "t": time.monotonic(), "done": done, "ok": False}
        try:
            self._q.put_nowait(item)
        except queue.Full:
            self.log("link write queue full; line dropped")
            return False
        if not wait:
            return True
        done.wait(WRITE_WAIT_S)
        return item["ok"]

    # -- internals -----------------------------------------------------------
    def _write_loop(self):
        while not self._stop.is_set():
            item = self._q.get()
            if item is None:
                break
            ok = False
            with self._wlock:
                writer, gen = self._writer, self._gen
            if writer is not None and item["gen"] == gen and time.monotonic() - item["t"] <= QUEUE_MAX_AGE_S:
                try:
                    writer(item["data"])
                    ok = True
                except Exception as e:      # noqa: BLE001 - any write error means the port is gone
                    self.bad_writes += 1
                    self.log(f"link write failed: {e}; reconnecting")
                    self._close(gen)       # the reader sees the closed port and reconnects
            item["ok"] = ok
            if item["done"] is not None:
                item["done"].set()

    def _drain(self):
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                return
            if item is None:
                self._q.put(None)
                return
            if item["done"] is not None:
                item["done"].set()

    def _set_state(self, connected, desc):
        self.connected = connected
        self.description = desc
        if connected:
            self.state, self.attempts, self.last_error, self.up_since = "up", 0, None, time.time()
        else:
            self.state, self.down_since = "reconnecting", time.time()
        try:
            self.on_state(connected, desc)
        except Exception as e:              # noqa: BLE001
            self.log(f"on_state error: {e}")

    def snapshot(self):
        return {"state": self.state, "attempts": self.attempts, "last_error": self.last_error,
                "up_since": self.up_since, "down_since": None if self.connected else self.down_since,
                "lines": self.lines, "bad_writes": self.bad_writes}

    def _close(self, gen=None):
        with self._wlock:
            if gen is not None and gen != self._gen:
                return                      # that connection is already gone
            self._writer = None
            closer, self._closer = self._closer, None
        if closer:
            try:
                closer()
            except Exception:               # noqa: BLE001
                pass

    def _run(self):
        last_msg = None
        while not self._stop.is_set():
            try:
                reader = self._open()
            except Exception as e:          # noqa: BLE001
                self.attempts += 1
                self.last_error = str(e)[:200]
                msg = f"link unavailable: {e}"
                if msg != last_msg:         # don't spam the journal every 2 s
                    self.log(msg)
                    last_msg = msg
                self._stop.wait(RECONNECT_S)
                continue
            last_msg = None
            self._set_state(True, self.description)
            try:
                buf = b""
                while not self._stop.is_set():
                    chunk = reader()
                    if chunk is None:       # read timeout, still connected
                        continue
                    if chunk == b"":
                        raise ConnectionError("closed by peer")
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        self._deliver(line)
                    if len(buf) > MAX_LINE:  # garbage without newlines: drop it
                        self._deliver(buf[:200] + b"...<overlong line dropped>")
                        buf = b""
            except Exception as e:          # noqa: BLE001
                self.last_error = str(e)[:200]
                if not self._stop.is_set():
                    self.log(f"link lost: {e}")
            self._close()
            self._drain()                   # never send a stale command to the next connection
            self._set_state(False, f"lost: {self.description}")
            self._stop.wait(RECONNECT_S)

    def _deliver(self, raw):
        text = raw.decode("utf-8", errors="replace").strip("\r\n\x00 ")
        if text:
            self.lines += 1
            try:
                self.on_line(text)
            except Exception as e:          # noqa: BLE001
                self.log(f"on_line error: {e}")

    def _install(self, writer, closer):
        with self._wlock:
            self._gen += 1
            self._writer = writer
            self._closer = closer

    def _open(self):
        if self.tcp:
            host, _, port = self.tcp.rpartition(":")
            s = socket.create_connection((host or "127.0.0.1", int(port)), timeout=5)
            s.settimeout(0.5)

            def read():
                try:
                    return s.recv(4096)
                except socket.timeout:
                    return None

            def write(b):
                s.settimeout(WRITE_TIMEOUT_S)   # shared with reads: both are short anyway
                s.sendall(b)
            self._install(write, s.close)
            self.description = f"tcp {self.tcp}"
            return read
        if serial is None:
            raise RuntimeError("pyserial not installed (apt install python3-serial)")
        path = self.port or find_heltec_port()
        if not path:
            raise FileNotFoundError("no Heltec serial port found (/dev/heltec, /dev/ttyACM*, /dev/cu.usbmodem*)")
        ser = serial.Serial(path, 115200, timeout=0.5, write_timeout=WRITE_TIMEOUT_S)
        try:
            ser.dtr = True                  # some ESP32-S3 CDC stacks only send with DTR set
        except Exception:                   # noqa: BLE001 - ptys may not support it
            pass
        try:
            ser.reset_input_buffer()        # whatever was buffered before we opened is stale
        except Exception:                   # noqa: BLE001
            pass

        def read():
            data = ser.read(ser.in_waiting or 1)
            return data if data else None
        # no ser.flush(): tcdrain() can block forever on a wedged CDC endpoint; write_timeout bounds write()
        self._install(ser.write, ser.close)
        self.description = f"serial {os.path.realpath(path) if path == '/dev/heltec' else path}"
        return read


if __name__ == "__main__":                  # quick manual probe: python3 link.py
    print(find_heltec_port() or "no port found", file=sys.stderr)
