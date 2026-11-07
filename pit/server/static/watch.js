/* GoKart pit pages: self-healing + Pi health chip. Pure functions only (no DOM), so
   pit/tests/test_core.py can run them outside a browser (node, or JavaScriptCore on a Mac).

   GKWatch.decide(st, now) -> "ok" | "wait" | "probe" | "reload"
     st.pageStart  ms the page loaded           st.lastMsg  ms of the last SSE frame (0 = none yet)
     st.startId0   server start_id first seen   st.startId  latest start_id
     st.probeOkAt  ms of the last successful /healthz probe (0 = none)
   Reload when the pit server restarted (start_id changed: new code, fresh state), or when the
   SSE stream has been dead for DEAD_MS but /healthz answers (the server is back, the stream is not).
   Never reload into an error page (probe first) and never within MIN_AGE_MS of loading, so a
   bad server cannot cause a reload loop faster than one reload per MIN_AGE_MS. */
"use strict";
const GKWatch = (() => {
  const DEAD_MS = 20000, MIN_AGE_MS = 30000, PROBE_FRESH_MS = 5000;

  function decide(st, now) {
    if (now - st.pageStart < MIN_AGE_MS) return "wait";
    if (st.startId0 && st.startId && st.startId !== st.startId0) return "reload";
    const dead = now - (st.lastMsg || st.pageStart);
    if (dead <= DEAD_MS) return "ok";
    return st.probeOkAt && now - st.probeOkAt < PROBE_FRESH_MS && st.probeOkAt > (st.lastMsg || 0) ? "reload" : "probe";
  }

  /* server.health -> null (nothing to show) or {text, cls}. Shown only when something is wrong. */
  function healthChip(h) {
    if (!h || !h.available) return null;
    if (h.status === "undervolt") return { text: "UNDERVOLT", cls: "bad" };
    if (h.status === "throttled") return { text: "throttled", cls: "warn" };
    if (h.status === "had_undervolt") return { text: "had undervolt", cls: "stale" };
    return null;
  }

  return { decide, healthChip, DEAD_MS, MIN_AGE_MS, PROBE_FRESH_MS };
})();
if (typeof module !== "undefined") module.exports = GKWatch;
