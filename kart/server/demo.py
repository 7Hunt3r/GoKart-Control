"""Demo mode: an in-process simulated kart Heltec (kart/sim/sim_heltec.py) that replaces the
real Heltec link for the driver page, so the real screen can show the game without a kart
moving. SIMULATION ONLY.

The simulated kart drives a loop built from the zones (kartcore.demo_path: the track when
there is one, else through every boost/mud/item centre and across the lap line) at ~15 mph,
with a `race 5` countdown at the start, and the pit beacon's running position cycling
P3 -> P2 -> P1, one step per lap after GO. Its lines go through a queue and a reader thread
(like a serial link), so no lock is ever held across the simulator and the server.
Everything the server "sends" while demoing goes to the simulator: the caller (kart_server)
routes writes here instead of to the real Heltec, which is never written to.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(os.path.dirname(HERE), "sim")

import kartcore  # noqa: E402

DEMO_MPH = 15.0
DEMO_MAX_S = 180.0
DEMO_POSITIONS = (3, 2, 1)


def demo_lap_s(path_m, mph=DEMO_MPH):
    """Seconds per lap of the demo loop at the cruise speed (position steps once per lap)."""
    return max(5.0, float(path_m) / (mph * 0.44704))


def _sim_module():
    if SIM_DIR not in sys.path:
        sys.path.insert(0, SIM_DIR)
    import sim_heltec          # noqa: E402  (kart/sim, deployed alongside server/)
    return sim_heltec


class Demo:
    def __init__(self, zones_doc, kart_id, on_line, max_s=DEMO_MAX_S, mph=DEMO_MPH, race_secs=5):
        sh = _sim_module()
        self.zones = zones_doc
        self.path = kartcore.demo_path(zones_doc, mph=mph)
        self.started = time.monotonic()
        self.until = self.started + float(max_s)
        self.on_line = on_line
        self._q = queue.Queue()
        self._stop = threading.Event()
        # running position from the "pit beacon": P3 -> P2 -> P1, one step per lap (from GO)
        self.lap_s = demo_lap_s(self.path["length_m"], mph)
        args = sh.sim_args(kart=int(kart_id or 2), uptime0=600, race_pos=",".join(map(str, DEMO_POSITIONS)),
                           race_pos_step=self.lap_s)
        self.sim = sh.Sim(args, path=sh.PolyPath(self.path["points"]),
                          full_mph=mph / sh.MODE_SCALE[1], start_mph=0.0)
        self.sim.out.append(self._q.put)
        self.writes = []                     # every line the server sent to the simulator
        threading.Thread(target=self._reader, name="demo-reader", daemon=True).start()
        threading.Thread(target=self.sim.run, name="demo-sim", daemon=True).start()
        with self.sim.lock:
            self.sim.log("SIMULATION")
            self.sim.log("DEMO: simulated kart on a loop through the zones")
            if race_secs:
                self.sim.handle_line(f"race {int(race_secs)}")

    # -- the "link" the server writes to while demoing -------------------------------
    connected = True
    description = "DEMO: simulated kart (in-process), real Heltec not used"

    def write_text(self, text):
        if self._stop.is_set():
            return False
        self.writes.append(text)
        with self.sim.lock:
            self.sim.handle_line(text)
        return True

    def left_s(self, now=None):
        return max(0.0, self.until - (now if now is not None else time.monotonic()))

    def expired(self, now=None):
        return (now if now is not None else time.monotonic()) >= self.until

    def stop(self):
        self._stop.set()
        self.sim.stopping.set()
        self._q.put(None)

    def _reader(self):
        buf = b""
        while not self._stop.is_set():
            data = self._q.get()
            if data is None:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if self._stop.is_set():
                    return
                text = line.decode("utf-8", "replace").strip()
                if text:
                    self.on_line(text, self)     # the server checks it is still the current demo
