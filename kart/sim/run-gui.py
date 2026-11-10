#!/usr/bin/env python3
"""Local graphics preview: one SoNo sample course for the map and every fake kart.

    python3 -I kart/sim/run-gui.py --port 64802 --no-open

Uses the existing fake pit, simulator and driver server unchanged. No serial,
radio or hardware connection is opened. The route is an illustrative street-map
fixture, not a designated driving course. Ctrl-C stops all preview processes.
"""
import argparse
import atexit
import importlib.util
import json
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import webbrowser

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ZONES = HERE / "zones.gui.json"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def kart_feed():
    sh = load("gui_sim_heltec", HERE / "sim_heltec.py")
    points = json.loads(ZONES.read_text())["track"]["points"]
    # PolyPath is the simulator's existing route input, also used by demo mode.
    sh.ORIGIN = tuple(points[0])
    args = sh.sim_args(kart=2, seed=7)
    sim = sh.Sim(args, path=sh.PolyPath(points), full_mph=24, start_mph=18)
    threading.Thread(target=sim.run, daemon=True).start()
    try:
        sim.serve_tcp(0)
    finally:
        sim.stopping.set()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--kart-feed", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.kart_feed:
        return kart_feed()

    runner = load("gui_sim_runner", HERE / "run-sim.py")
    atexit.register(runner.shutdown)
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    tmp = Path(tempfile.mkdtemp(prefix="gokart-gui-"))
    cfg = tmp / "config.json"
    cfg.write_text(json.dumps({"map_poll_s": 1, "kart_id": 2, "sound": True, "freeplay": True}))
    port = a.port or runner.free_port()
    pit = runner.start([runner.FAKE_PIT, "--port", "0", "--map", str(ROOT / "pit/server/map/map.sono.json"),
                        "--zones", str(ZONES)], "fake-pit", str(tmp))
    pit_url = runner.first_line_matching(pit, r"http://127\.0\.0\.1:\d+", "fake pit").group(0)
    runner.pump(pit)
    sim = runner.start([str(Path(__file__).resolve()), "--kart-feed"], "sim-kart", str(tmp))
    host = runner.first_line_matching(sim, r"tcp (127\.0\.0\.1:\d+)", "sim kart").group(1)
    runner.pump(sim)
    server = runner.start([runner.SERVER, "--tcp", host, "--bind", "127.0.0.1", "--http-port", str(port),
                           "--config", str(cfg), "--log-dir", str(tmp / "logs"),
                           "--cache-dir", str(tmp / "cache/map"), "--pit-url", pit_url], "display", str(tmp))
    runner.first_line_matching(server, r"on http://[\d.]+:\d+", "display server", timeout=25)
    runner.pump(server)
    url = f"http://127.0.0.1:{port}/"
    print(f"\nGraphics preview: {url}\nShared SoNo sample course; no real kart.\nLogs: {tmp}\n", flush=True)
    if not a.no_open:
        webbrowser.open(url)
    while True:
        for name, process, _ in runner.procs:
            if process.poll() is not None:
                raise SystemExit(f"{name} stopped; see {tmp}/{name}.log")
        time.sleep(.5)


if __name__ == "__main__":
    main()
