#!/usr/bin/env python3
"""Local pit graphics preview on the same illustrative SoNo route as the driver.

The existing eight-kart simulator supplies all statuses and command responses.
Only this preview's position fixture changes. No hardware connection is opened.
"""
import argparse
import atexit
import importlib.util
import json
import math
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import time
import webbrowser

ROOT = Path(__file__).resolve().parents[2]
ZONES = ROOT / "kart/sim/zones.gui.json"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pit_feed():
    sim = load("pit_gui_sim", ROOT / "pit/sim/sim_tbeam.py")
    driver = load("pit_gui_path", ROOT / "kart/sim/sim_heltec.py")
    path = driver.PolyPath(json.loads(ZONES.read_text())["track"]["points"])
    package = json.loads((ROOT / "pit/server/map/map.sono.json").read_text())
    sim.ORIGIN = (package["pit"]["lat"], package["pit"]["lon"])

    def position(distance):
        lat, lon, heading = path.at(distance)
        east = math.radians(lon - sim.ORIGIN[1]) * sim.R_EARTH * math.cos(math.radians(sim.ORIGIN[0]))
        north = math.radians(lat - sim.ORIGIN[0]) * sim.R_EARTH
        return east, north, heading

    sim.oval = position  # Process-local fixture; original simulator source is unchanged.
    sys.argv = [str(ROOT / "pit/sim/sim_tbeam.py"), "--tcp", "0", "--seed", "7"]
    sim.main()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--pit-feed", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.pit_feed:
        return pit_feed()
    runner = load("pit_gui_runner", ROOT / "kart/sim/run-sim.py")
    atexit.register(runner.shutdown)
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    tmp = Path(tempfile.mkdtemp(prefix="gokart-pit-gui-"))
    maps = tmp / "map"
    maps.mkdir()
    shutil.copyfile(ZONES, maps / "zones.json")
    shutil.copyfile(ROOT / "pit/server/map/map.sono.json", maps / "map.json")
    shutil.copyfile(ROOT / "pit/server/map/sono-light.png", maps / "sono-light.png")
    cfg = tmp / "config.json"
    cfg.write_text("{}")
    port = args.port or runner.free_port()
    feed = runner.start([str(Path(__file__).resolve()), "--pit-feed"], "pit-sim", str(tmp))
    host = runner.first_line_matching(feed, r"tcp (127\.0\.0\.1:\d+)", "pit simulator").group(1)
    runner.pump(feed)
    server = runner.start([str(ROOT / "pit/server/pit_server.py"), "--tcp", host, "--bind", "127.0.0.1",
                           "--http-port", str(port), "--config", str(cfg), "--log-dir", str(tmp / "logs"),
                           "--map-dir", str(maps)], "pit-server", str(tmp))
    runner.pump(server)
    url = f"http://127.0.0.1:{port}/kiosk"
    print(f"Pit graphics preview: {url}\nEight simulated karts on the SoNo sample route; no hardware.\nLogs: {tmp}", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    while True:
        for name, process, _ in runner.procs:
            if process.poll() is not None:
                raise SystemExit(f"{name} stopped; see {tmp}/{name}.log")
        time.sleep(.5)


if __name__ == "__main__":
    main()
