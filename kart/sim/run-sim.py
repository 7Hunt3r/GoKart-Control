#!/usr/bin/env python3
"""Run the whole kart display on your laptop, with no kart and no radio.

    python3 kart/sim/run-sim.py            # shop aerial course, kart driving a lap
    python3 kart/sim/run-sim.py --map pit/server/map/map.manresa.json   # the real track
    python3 kart/sim/run-sim.py --port 8080 --no-open

It starts three local processes and opens the driver display in your browser:

    fake pit  ->  a stand-in for the pit base station (serves the map + imagery)
    sim kart  ->  a fake Heltec driving the course (so the map moves, speed changes)
    kart app  ->  the real display server (kart/server) you see in the browser

Edit anything under kart/server/static/ (index.html, app.js, style.css) and just
refresh the browser - there is no build step. Ctrl-C stops all three.

Python 3.9+ standard library only. Nothing here can reach a real kart.
"""
import argparse, atexit, os, re, signal, socket, subprocess, sys, tempfile, time, webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
FAKE_PIT = os.path.join(HERE, "fake_pit.py")
SIM = os.path.join(HERE, "sim_heltec.py")
SERVER = os.path.join(ROOT, "kart", "server", "kart_server.py")
PY = sys.executable
procs = []


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start(cmd, name, logdir):
    log = open(os.path.join(logdir, name + ".log"), "w")
    p = subprocess.Popen([PY, "-I"] + cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         cwd=ROOT, text=True, bufsize=1)
    procs.append((name, p, log))
    return p


def first_line_matching(p, pat, name, timeout=20):
    """Read the child's stdout until a line matches pat; tee everything to its log."""
    rx = re.compile(pat)
    t0 = time.time()
    _, _, log = next(x for x in procs if x[1] is p)
    while time.time() - t0 < timeout:
        line = p.stdout.readline()
        if not line:
            if p.poll() is not None:
                raise SystemExit(f"{name} exited early; see its log")
            continue
        log.write(line)
        log.flush()
        m = rx.search(line)
        if m:
            return m
    raise SystemExit(f"timed out waiting for {name} to start")


def pump(p):
    """After startup, keep draining a child's stdout into its log so it never blocks."""
    import threading
    _, _, log = next(x for x in procs if x[1] is p)

    def run():
        for line in p.stdout:
            log.write(line)
            log.flush()
    threading.Thread(target=run, daemon=True).start()


def shutdown(*_):
    for name, p, log in procs:
        if p.poll() is None:
            p.terminate()
    t0 = time.time()
    for name, p, log in procs:
        try:
            p.wait(timeout=max(0.1, 3 - (time.time() - t0)))
        except subprocess.TimeoutExpired:
            p.kill()
        log.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", default=os.path.join(ROOT, "pit", "server", "map", "map.shop.json"),
                    help="map package the fake pit serves (default: the shop aerial course)")
    ap.add_argument("--port", type=int, default=0, help="display HTTP port (default: a free one)")
    ap.add_argument("--seed", type=int, default=7, help="sim random seed")
    ap.add_argument("--no-course", action="store_true", help="kart sits still instead of driving the loop")
    ap.add_argument("--no-open", action="store_true", help="do not open a browser")
    a = ap.parse_args()
    if not os.path.exists(a.map):
        raise SystemExit(f"no such map file: {a.map}")

    atexit.register(shutdown)
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    tmp = tempfile.mkdtemp(prefix="gokart-sim-")
    cfg = os.path.join(tmp, "config.json")
    with open(cfg, "w") as f:
        f.write('{"map_poll_s": 1, "kart_id": 2, "sound": true, "freeplay": true}')
    http = a.port or free_port()

    print("starting the fake pit ...", flush=True)
    pit = start([FAKE_PIT, "--port", "0", "--map", a.map], "fake-pit", tmp)
    pit_url = first_line_matching(pit, r"http://127\.0\.0\.1:\d+", "fake pit").group(0)
    pump(pit)

    print("starting the sim kart ...", flush=True)
    sim_args = [SIM, "--tcp", "0", "--kart", "2", "--seed", str(a.seed)]
    if not a.no_course:
        sim_args += ["--course", "--warp", "2"]
    sim = start(sim_args, "sim-kart", tmp)
    sim_hostport = first_line_matching(sim, r"tcp (127\.0\.0\.1:\d+)", "sim kart").group(1)
    pump(sim)

    print("starting the display server ...", flush=True)
    srv = start([SERVER, "--tcp", sim_hostport, "--bind", "127.0.0.1", "--http-port", str(http),
                 "--config", cfg, "--log-dir", os.path.join(tmp, "logs"),
                 "--cache-dir", os.path.join(tmp, "cache", "map"), "--pit-url", pit_url], "display", tmp)
    first_line_matching(srv, r"on http://[\d.]+:\d+", "display server", timeout=25)
    pump(srv)

    url = f"http://127.0.0.1:{http}/"
    print(f"\n  Display is up:  {url}")
    print(f"  Map served:     {os.path.relpath(a.map, ROOT)}")
    print("  Edit kart/server/static/ and refresh the browser. Ctrl-C to stop.")
    print(f"  (logs in {tmp})\n", flush=True)
    if not a.no_open:
        time.sleep(1.0)
        webbrowser.open(url)
    while True:
        for name, p, log in procs:
            if p.poll() is not None:
                raise SystemExit(f"\n{name} stopped; see {tmp}/{name}.log")
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
