#!/usr/bin/env python3
"""End-to-end bench test for the live fleet: pit server -> T-Beam -> LoRa -> kart Heltec -> kart Pi.

Run ON the pit Pi (the pit server trusts localhost, so no login is needed). deploy.sh does not
copy tools/, so copy the script over first:
    scp tools/bench/e2e_bench.py pit@gokart-pit.local:/tmp/
    ssh pit@gokart-pit.local python3 /tmp/e2e_bench.py --kart 2

Checks (each prints PASS/FAIL/SKIP; exit status 1 if any FAIL):
  pit link, radio stat fresh, beacon counter moving, kart online with low loss, repeater (if any),
  ping round trip, race countdown + GO on the kart Pi, zones/package served and synced to the
  kart, gps line rate and quality, and (only with --allow-writes, or when no VESC is linked, where
  they must come back no_vesc) slow/boost/stop/start.

VESC writes: slow/boost/stop/start are never sent to a kart whose VESC is linked unless
--allow-writes is given. The race check is NOT gated: it always sends `race 3`, which on a kart
with a linked VESC holds the current scale at 0 for about 3 s and then restores it. Run it with
the drive wheels off the ground or the VESC unlinked.
"""
import argparse, json, sys, time, urllib.request

def get(url, timeout=4):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())

def post(url, body, timeout=4):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

RESULTS = []
def rec(name, ok, detail="", skip=False):
    tag = "SKIP" if skip else ("PASS" if ok else "FAIL")
    RESULTS.append((tag, name, detail)); print(f"{tag:4} {name:44} {detail}")

def kart_state(pit_state, k):
    return next((x for x in pit_state["fleet"]["karts"] if x["id"] == k), None)

def wait_outcome(pit, cmd_id, timeout=12):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = get(pit + "/api/state")
        for c in st["commands"].get("recent") or []:
            if c["id"] == cmd_id and c["state"] == "done":
                return c
        time.sleep(0.5)
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pit", default="http://127.0.0.1:8080")
    ap.add_argument("--kart", type=int, default=2)
    ap.add_argument("--kart-url", default=None, help="kart Pi base URL (default http://gokart-kart-N.local:8081)")
    ap.add_argument("--allow-writes", action="store_true", help="send slow/boost/stop/start even if a VESC is linked")
    a = ap.parse_args()
    pit, k = a.pit, a.kart
    kurl = a.kart_url or f"http://gokart-kart-{k}.local:8081"
    if not a.kart_url:                      # .local can fail from the pit Pi; fall back to the last IP the pit saw for this kart
        try:
            urllib.request.urlopen(kurl + "/api/state", timeout=3)
        except Exception:
            import socket
            for cand in (f"gokart-kart-{k}", f"192.168.150.{110 + k - 1}", "192.168.150.111"):
                try:
                    socket.gethostbyname(cand); urllib.request.urlopen(f"http://{cand}:8081/api/state", timeout=3); kurl = f"http://{cand}:8081"; break
                except Exception:
                    continue

    # ---- pit + radio
    try:
        s0 = get(pit + "/api/state")
    except Exception as e:
        rec("pit api", False, str(e)); return finish()
    link = s0["server"]["link"]; rec("pit T-Beam link", link["connected"], link.get("desc", ""))
    stat = s0["fleet"].get("stat") or {}
    rec("radio stat fresh", bool(stat) and (stat.get("age_s") or 99) < 5, f"rx {stat.get('rx')} bad {stat.get('rx_bad')} sync {stat.get('sync')}")
    b0 = stat.get("beacon_tx"); time.sleep(3.2); s1 = get(pit + "/api/state"); b1 = (s1["fleet"].get("stat") or {}).get("beacon_tx")
    rec("beacon counter moving", b0 is not None and b1 is not None and b1 > b0, f"{b0} -> {b1} in 3 s")
    ks = kart_state(s1, k)
    rec(f"kart {k} online", bool(ks) and ks["link"] == "online", f"age {ks and ks.get('age_s')} s, packets {ks and ks.get('packets')}")
    loss = ks and ks.get("loss_pct_1m")
    rec(f"kart {k} loss < 5 %", loss is not None and loss < 5, f"{loss} %")
    rep = s1["fleet"].get("repeater")
    if rep: rec("repeater heard", (rep.get("age_s") or 99) < 20, f"rssi {rep.get('rssi')} karts {rep.get('karts')} age {rep.get('age_s')}")
    else: rec("repeater heard", True, "none plugged in", skip=True)
    vesc_linked = bool(ks and ks["status"].get("vesc_link"))
    rec(f"kart {k} VESC", True, "linked" if vesc_linked else "not linked (write commands expect no_vesc)", skip=True)

    # ---- ping round trip
    try:
        r = post(pit + "/cmd", {"cmd": "ping", "kart": k}); c = wait_outcome(pit, r["command"]["id"])
        rec("ping applied", bool(c) and c["outcomes"].get(str(k)) == "applied", f"{c and c['outcomes']}")
    except Exception as e:
        rec("ping applied", False, str(e))

    # ---- kart Pi
    try:
        kst = get(kurl + "/api/state")
        rec("kart Pi api", True, kurl)
        selfst = kst.get("self") or {}
        rec("kart Pi hears Heltec", selfst.get("link") == "online", f"uptime {selfst.get('status', {}).get('uptime_s')}")
        g = selfst.get("gps") or {}
        rec("gps 5 Hz line in use", (g.get("gps_hz") or 0) >= 4 or (kst.get("game", {}).get("gps", {}) or {}).get("hz", 0) >= 4, f"gps block: {json.dumps(g)[:120]}")
        rec("gps quality fields", "hdop_now" in g or "hdop" in g, "")
        mv = (kst.get("map") or {}).get("zones") or {}
        pz = get(pit + "/map/zones.json")
        rec("zones synced to kart", mv.get("version") == pz.get("version"), f"kart v{mv.get('version')} pit v{pz.get('version')} enabled {pz.get('enabled')}")
    except Exception as e:
        kst = None; rec("kart Pi api", False, str(e))

    # ---- race countdown through the whole chain (a race hold writes scale 0 to a linked VESC: gated like the writes)
    if vesc_linked and not a.allow_writes:
        rec("race command outcome", True, "VESC linked: skipped without --allow-writes", skip=True)
        rec("kart display saw countdown + GO", True, "skipped with the race command", skip=True)
    else:
      try:
        r = post(pit + "/cmd", {"cmd": "race", "kart": k, "arg": 3}); t_send = time.time()
        c = wait_outcome(pit, r["command"]["id"], timeout=10)
        want = {"applied", "accepted"} if (vesc_linked and a.allow_writes) else ({"no_vesc"} if not vesc_linked else {"rejected", "no_vesc", "applied", "accepted"})
        rec("race command outcome", bool(c) and c["outcomes"].get(str(k)) in want, f"{c and c['outcomes']} (want {sorted(want)})")
        if kst is not None:
            time.sleep(max(0, 5 - (time.time() - t_send)))
            kst2 = get(kurl + "/api/state")
            rs = (kst2.get("game") or {}).get("race") or kst2.get("race") or {}
            seen = bool(rs) and (rs.get("go_confirmed") is True or (rs.get("go_age_s") is not None and rs.get("go_age_s") < 30))
            rec("kart display saw countdown + GO", seen, f"race block: {json.dumps(rs)[:160]}" if rs else "no race block in kart /api/state")
      except Exception as e:
        rec("race command outcome", False, str(e))

    # ---- write commands
    if vesc_linked and not a.allow_writes:
        rec("slow/boost/stop/start", True, "VESC linked: skipped without --allow-writes", skip=True)
    else:
        want = {"no_vesc"} if not vesc_linked else {"applied", "accepted"}
        for cmd, arg in (("slow", 5), ("boost", 100), ("stop", None), ("start", None)):
            try:
                body = {"cmd": cmd, "kart": k}
                if arg is not None: body["arg"] = arg
                r = post(pit + "/cmd", body); c = wait_outcome(pit, r["command"]["id"])
                rec(f"{cmd} outcome", bool(c) and c["outcomes"].get(str(k)) in want, f"{c and c['outcomes']} (want {sorted(want)})")
            except Exception as e:
                rec(f"{cmd} outcome", False, str(e))
    return finish()

def finish():
    fails = [r for r in RESULTS if r[0] == "FAIL"]
    print(f"\n{len([r for r in RESULTS if r[0]=='PASS'])} pass, {len(fails)} fail, {len([r for r in RESULTS if r[0]=='SKIP'])} skip")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
