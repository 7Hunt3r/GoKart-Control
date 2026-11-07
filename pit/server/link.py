"""T-Beam link: newline-delimited JSON over USB CDC serial (pyserial) or TCP.

One reader thread owns the connection, reconnects forever, and hands every
line to `on_line(text)`. `write_line(obj)` is safe from any thread and returns
False when the link is down (the caller decides what that means; commands are
never queued for replay after a reconnect).

Writes are paced by a writer thread: one line every WRITE_GAP_S, each preceded by
a bare newline. Measured 2026-11-06 on the pit Pi: a burst of nine lines (STOP RACE
= eight finish commands + the race line, ~470 bytes in 7 ms) lost its tail inside
the T-Beam's USB CDC receive buffer while the base was busy transmitting, and the
leftover fragment then turned the next line into "bad json". Pacing keeps the
buffer from overflowing; the leading newline terminates any fragment so the next
line always parses (the base ignores empty lines).
"""
from __future__ import annotations

import glob
import json
import os
import socket
import sys
import queue
import threading
import time

try:
    import serial                      # pyserial
    from serial.tools import list_ports
except ImportError:                    # TCP/simulator mode still works without it
    serial = None
    list_ports = None

TBEAM_VID, TBEAM_PID = 0x303A, 0x1001
MAX_LINE = 4096
RECONNECT_S = 2.0
WRITE_GAP_S = 0.02                     # >= 20 ms between lines (~55 bytes each) to the T-Beam
WRITE_QUEUE_MAX = 200                  # lines; beyond this the link is treated as stuck


def find_tbeam_port():
    """Preferred order: /dev/tbeam (udev symlink), a VID/PID match, /dev/serial/by-id/*,
    /dev/ttyACM*, then macOS /dev/cu.usbmodem*. Returns a path or None."""
    if os.path.exists("/dev/tbeam"):
        return "/dev/tbeam"
    if list_ports is not None:
        for p in list_ports.comports():
            if p.vid == TBEAM_VID and p.pid == TBEAM_PID:
                return p.device
    by_id = sorted(glob.glob("/dev/serial/by-id/*"))
    for p in by_id:
        if "TBeam" in p or "303a" in p.lower() or "JTAG_serial" in p:
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
        self._wlock = threading.Lock()
        self._writer = None                 # callable(bytes)
        self._closer = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="tbeam-link", daemon=True)
        self._outq = queue.Queue(maxsize=WRITE_QUEUE_MAX)
        self._gen = 0                       # connection generation: lines queued for an old one are dropped
        self._wthread = threading.Thread(target=self._write_loop, name="tbeam-write", daemon=True)
        self.write_gap_s = WRITE_GAP_S
        self.lines_written = 0
        self.write_errors = 0

    def start(self):
        self._thread.start()
        self._wthread.start()

    def stop(self):
        self._stop.set()
        self._close()

    def write_line(self, obj):
        """Queue one JSON line for the T-Beam. True = queued on a live link (written within
        a few tens of ms, in order); False = link down (nothing queued, nothing replayed later)."""
        data = b"\n" + (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        # no lock here: the writer thread may hold _wlock for up to write_timeout, and callers hold the app lock
        gen = self._gen
        if not self.connected or self._writer is None:
            return False
        try:
            self._outq.put_nowait((gen, data))
            return True
        except queue.Full:
            self.write_errors += 1
            self.log("link write queue full: line dropped")
            return False

    def _write_loop(self):
        last = 0.0
        while not self._stop.is_set():
            try:
                gen, data = self._outq.get(timeout=0.5)
            except queue.Empty:
                continue
            wait = last + self.write_gap_s - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            with self._wlock:
                if gen != self._gen or self._writer is None:
                    continue                # queued for a connection that has since dropped
                try:
                    self._writer(data)
                    self.lines_written += 1
                except Exception as e:      # noqa: BLE001 - the reader thread notices the dead port
                    self.write_errors += 1
                    self.log(f"link write failed: {e}")
            last = time.monotonic()

    # -- internals -----------------------------------------------------------
    def _set_state(self, connected, desc):
        self.connected = connected
        self.description = desc
        try:
            self.on_state(connected, desc)
        except Exception as e:              # noqa: BLE001
            self.log(f"on_state error: {e}")

    def _close(self):
        with self._wlock:
            self._writer = None
            self._gen += 1                  # anything still queued is for the dead connection
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
                self.log(f"link lost: {e}")
            self._close()
            self._set_state(False, f"lost: {self.description}")
            self._stop.wait(RECONNECT_S)

    def _deliver(self, raw):
        text = raw.decode("utf-8", errors="replace").strip("\r\n\x00 ")
        if text:
            try:
                self.on_line(text)
            except Exception as e:          # noqa: BLE001
                self.log(f"on_line error: {e}")

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
            with self._wlock:
                self._writer = s.sendall
                self._closer = s.close
            self.description = f"tcp {self.tcp}"
            return read
        if serial is None:
            raise RuntimeError("pyserial not installed (apt install python3-serial)")
        path = self.port or find_tbeam_port()
        if not path:
            raise FileNotFoundError("no T-Beam serial port found (/dev/tbeam, /dev/ttyACM*, /dev/cu.usbmodem*)")
        ser = serial.Serial(path, 115200, timeout=0.5, write_timeout=1.0)
        try:
            ser.dtr = True                  # some ESP32-S3 CDC stacks only send with DTR set
        except Exception:                   # noqa: BLE001 - ptys may not support it
            pass

        def read():
            data = ser.read(ser.in_waiting or 1)
            return data if data else None
        with self._wlock:
            self._writer = lambda b: (ser.write(b), ser.flush())
            self._closer = ser.close
        self.description = f"serial {path}"
        return read


if __name__ == "__main__":                  # quick manual probe: python3 link.py
    print(find_tbeam_port() or "no port found", file=sys.stderr)
