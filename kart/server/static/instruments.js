/* Read-only presentation of the same state used by app.js. No requests or commands. */
"use strict";
(() => {
  const text = (id, value) => {
    const node = document.getElementById(id);
    if (node.textContent !== value) node.textContent = value;
  };
  const finite = value => typeof value === "number" && Number.isFinite(value);
  window.addEventListener("gokart-state-render", ({ detail: state }) => {
    const me = state.self || {}, v = me.vesc || {}, status = me.status || {};
    const online = me.link === "online", fresh = online && me.vesc_fresh;
    text("inputPower", fresh && finite(v.volts) && finite(v.batt_a) ? (v.volts * v.batt_a / 1000).toFixed(2) : "--");
    text("motorErpm", fresh && finite(v.erpm) ? String(Math.round(v.erpm)) : "--");
    text("escTemp", fresh && finite(v.esc_c) ? v.esc_c.toFixed(0) : "--");
    document.querySelector(".telemetry-grid").classList.toggle("stale", !fresh);
    const pct = me.derived && me.derived.battery_pct;
    document.querySelector(".driver-sidebar").dataset.energyLevel = !fresh ? "stale" : finite(pct) && pct < 20 ? "low" : finite(pct) && pct < 40 ? "warning" : "normal";
    const face = document.getElementById("linkFace");
    const connected = online && state.server && state.server.link.connected;
    const attention = connected && (status.stopped || (fresh && finite(v.fault) && v.fault !== 0));
    const mood = !connected ? "offline" : attention ? "attention" : "online";
    if (face.dataset.mood !== mood) {
      face.dataset.mood = mood;
      face.setAttribute("aria-label", !connected ? "Kart link unavailable" : attention ? "Kart connected; see status alert" : "Kart link connected");
      face.querySelector(".face-mouth").setAttribute("d", mood === "online" ? "M10 20v3h12v-3" : mood === "attention" ? "M10 24v-3h12v3" : "M10 22h12");
    }
    const rssi = status.base_rssi, heard = connected && state.pit_link && state.pit_link.state === "ok";
    const count = heard && finite(rssi) && rssi < 0 ? Math.max(1, Math.min(5, Math.ceil((rssi + 120) / 12))) : 0;
    const signal = document.getElementById("pitSignal");
    signal.setAttribute("aria-label", count ? `Pit signal ${rssi} dBm` : "Pit signal unavailable");
    signal.querySelectorAll("i").forEach((bar, i) => bar.classList.toggle("lit", i < count));
  });
})();
