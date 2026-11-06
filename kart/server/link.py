"""Heltec link: newline-delimited JSON over USB CDC serial (pyserial) or TCP.

Adapted from pit/server/link.py (proven against the T-Beam S3 on the pit Pi 4,
2026-11-05). The kart Heltec V4.3 uses the same ESP32-S3 USB-Serial/JTAG
(303a:1001), so the port handling is unchanged; only the names and the
plain-text write differ (the kart firmware takes text commands like "mode 1").

One reader thread owns the connection, reconnects forever, and hands every
line to `on_line(text)`. `write_line(obj)` is safe from any thread and returns
False when the link is down (the caller decides what that means; commands are
never queued for replay after a reconnect).
"""
from __future__ import annotations

import glob
import json
import os
import socket
import sys
import threading

try:
    import serial                      # pyserial
    from serial.tools import list_ports
except ImportError:                    # TCP/simulator mode still works without it
    serial = None
    list_ports = None

HELTEC_VID, HELTEC_PID = 0x303A, 0x1001
MAX_LINE = 4096
RECONNECT_S = 2.0


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
        self._wlock = threading.Lock()
        self._writer = None                 # callable(bytes)
        self._closer = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="heltec-link", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._close()

    def write_line(self, obj):
        return self.write_text(json.dumps(obj, separators=(",", ":")))

    def write_text(self, text):
        """Send one plain-text command line (e.g. "mode 1"). False if the link is down."""
        data = (text.strip() + "\n").encode()
        with self._wlock:
            if not self.connected or self._writer is None:
                return False
            try:
                self._writer(data)
                return True
            except Exception as e:          # noqa: BLE001 - any write error means link down
                self.log(f"link write failed: {e}")
                return False

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
        path = self.port or find_heltec_port()
        if not path:
            raise FileNotFoundError("no Heltec serial port found (/dev/heltec, /dev/ttyACM*, /dev/cu.usbmodem*)")
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
    print(find_heltec_port() or "no port found", file=sys.stderr)
