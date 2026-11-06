/* GoKart kart driver display (Pi 3 B, LESOWN L7 1024x600 touch).
   Vanilla JS, no external assets. State arrives from kart_server.py over
   Server-Sent Events (/events, ~5 Hz). The page never invents values: anything
   missing or stale is shown as such ("--", "NO GPS FIX", greyed + age).
   Map projection, imagery placement, dead reckoning and pinch/drag are adapted
   from pit/server/static/app.js so the pit and the karts draw the same way.
   Game layer (pit/server/map/ZONES.md): speedometer arc, gear, boost meter from the
   live current scale, lap panel, zone overlays on the map, and a short flash +
   synthesized sound (WebAudio, no files) on boost / mud / lap events. The page only
   SHOWS the game; the server decides and sends the boost commands. */
"use strict";

const MODES = ["ECO", "NORMAL", "SPORT"];
const RESULT_TEXT = { none: "no result", accepted: "accepted", applied: "applied", no_vesc: "NO VESC", rejected: "REJECTED", unknown: "not confirmed" };
const CMD_TEXT = { mode: "mode", stop: "STOP", start: "START", ping: "ping", slow: "SLOW", status: "status" };
const $ = (id) => document.getElementById(id);
let S = null, lastMsg = 0;

function nul(v) { return v === null || v === undefined || (typeof v === "number" && Number.isNaN(v)); }
function ageText(a) {
  if (nul(a)) return "never";
  if (a < 60) return a.toFixed(0) + " s";
  if (a < 3600) return Math.floor(a / 60) + " min";
  return Math.floor(a / 3600) + " h";
}
function setChip(id, text, cls) { const e = $(id); e.textContent = text; e.className = "chip " + (cls || ""); }
function show(id, on) { $(id).classList.toggle("hidden", !on); }
function cfg(k, d) { return S && S.config && !nul(S.config[k]) ? S.config[k] : d; }

/* ------------------------------------------------------------ dashboard */
function render() {
  const me = S.self, link = S.server.link;
  const fresh = me.link === "online";          // self line within 3 s
  const st = me.status || {};

  show("simBanner", !!S.simulation);
  const cb = $("connBanner");
  if (!link.connected) { cb.textContent = "NO CONNECTION TO KART COMPUTER (Heltec USB) - values below are old"; show("connBanner", true); }
  else if (!fresh) { cb.textContent = me.age_s === null ? "Waiting for the kart computer..." : `KART COMPUTER SILENT ${ageText(me.age_s)} - values below are old`; show("connBanner", true); }
  else show("connBanner", false);

  // STOPPED (authoritative telemetry flag) and SLOW (inferred from command echoes)
  show("stopBanner", st.stopped === true);
  $("stopBanner").textContent = fresh ? "STOPPED" : "STOPPED (last known)";
  const slow = S.slow || {};
  show("slowBanner", slow.active === true);
  $("slowBanner").textContent = slow.active ? `SLOW ${slow.mph} MPH` : "SLOW";
  document.body.classList.toggle("alerting", st.stopped === true || slow.active === true || !$("connBanner").classList.contains("hidden"));

  // chips
  const id = me.heltec_id !== null ? me.heltec_id : me.configured_id;
  setChip("kartChip", `KART ${nul(id) ? "?" : id}${me.id_mismatch ? "?" : ""}`, "id" + (me.id_mismatch ? " bad" : ""));
  const pl = S.pit_link || {};
  if (pl.state === "ok") setChip("pitChip", `PIT ${pl.rssi}`, "ok");
  else if (pl.state === "lost") setChip("pitChip", "NO PIT", "warn");
  else setChip("pitChip", "PIT ?", "");
  if (!fresh) setChip("vescChip", "VESC ?", "");
  else if (st.vesc_link === true) setChip("vescChip", "VESC", "ok");
  else setChip("vescChip", "NO VESC", "bad");
  const g = me.gps || {};
  if (!fresh) setChip("gpsChip", "GPS ?", "");
  else if (g.fix_fresh && gpsWeak()) setChip("gpsChip", `GPS WEAK${nul(g.hdop_now) || g.hdop_now >= 99 ? "" : " " + Number(g.hdop_now).toFixed(1)}`, "warn");
  else if (g.fix_fresh) setChip("gpsChip", `GPS ${nul(g.sats_now) ? "" : g.sats_now}${g.gps_hz === 5 ? " 5Hz" : ""}`, "ok");
  else setChip("gpsChip", "NO FIX", "warn");

  // speed: GPS ground speed from a fresh fix only; the arc falls back to wheel mph when there is no fix
  const d = me.derived || {};
  const mphEl = $("mph");
  const wheel = fresh && me.vesc_fresh && !nul(d.wheel_mph) ? Math.abs(d.wheel_mph) : null;
  let arcMph = null;
  if (fresh && !nul(d.gps_mph)) { mphEl.textContent = Math.round(d.gps_mph); mphEl.setAttribute("class", "mphnum"); $("mphSrc").textContent = "GPS"; arcMph = d.gps_mph; }
  else if (wheel !== null) { mphEl.textContent = Math.round(wheel); mphEl.setAttribute("class", "mphnum"); $("mphSrc").textContent = "WHEEL"; arcMph = wheel; }
  else { mphEl.textContent = !fresh ? "NO DATA" : "NO FIX"; mphEl.setAttribute("class", "mphnum na"); $("mphSrc").textContent = "GPS"; }
  $("mphNote").textContent = fresh && !g.fix_fresh
    ? (wheel !== null ? "NO GPS FIX: wheel speed (uncalibrated)" : !nul(g.fix_age_s) ? `last fix ${ageText(g.fix_age_s)} ago` : "")
    : (wheel !== null ? `wheel ${wheel.toFixed(0)} mph (uncalibrated)` : "");
  const game = S.game || {}, meter = (game.meter || {});
  drawSpeedo(arcMph, fresh ? meter.kind : "unknown");

  // battery: only from samples with vesc_link true, with its own age
  const v = me.vesc || {};
  const battFresh = me.vesc_fresh && fresh;
  document.querySelector(".batt").classList.toggle("stale", !battFresh);
  $("battPct").textContent = nul(d.battery_pct) ? "--" : Math.round(d.battery_pct) + "%";
  $("battV").textContent = nul(v.volts) ? "-- V" : Number(v.volts).toFixed(1) + " V";
  const bar = $("battBar");
  bar.style.width = nul(d.battery_pct) ? "0" : d.battery_pct + "%";
  bar.style.background = !battFresh ? "var(--stale)" : d.battery_pct < 20 ? "var(--bad)" : d.battery_pct < 40 ? "var(--warn)" : "var(--ok)";
  $("battNote").textContent = nul(v.volts) ? "no VESC data yet" : !battFresh ? `VESC data ${ageText(me.vesc_age_s)} old` : (d.under_load ? "under load: % reads low" : "20S pack, 60-84 V");

  // mode requested -> actual, shown as the gear badge
  const mr = st.mode_req, ma = st.mode_actual;
  const gear = $("gear");
  gear.className = "gear" + (!fresh ? " stale" : "") + (fresh && !nul(mr) && mr !== ma ? " req" : "") + (fresh && ma === 2 ? " g2" : "");
  $("gearLetter").textContent = nul(ma) ? "?" : MODES[ma][0];
  $("modeNow").textContent = nul(ma) ? "unknown" : MODES[ma] + (st.mode_confirmed ? " ✓" : "");
  let mn = "";
  if (!nul(mr) && mr !== ma) mn = `requested ${MODES[mr]}: not applied yet`;
  else if (!nul(ma) && !st.mode_confirmed) mn = st.stopped ? "held by STOP" : "not confirmed by VESC read-back";
  else if (!nul(ma)) mn = "confirmed by VESC read-back";
  else mn = "VESC has not reported a mode";
  $("modeNote").textContent = fresh ? mn : `last known (${ageText(me.age_s)} ago)`;
  document.querySelectorAll(".mbtn").forEach((b) => {
    const m = Number(b.dataset.mode);
    b.classList.toggle("actual", m === ma && fresh);
    b.classList.toggle("req", m === mr && fresh);
    b.disabled = !link.connected;
  });

  // fault: hidden in beginner mode (driver presentation only; pit + logs keep it)
  const fault = v.fault;
  const showFault = !cfg("beginner_mode", false) && battFresh && !nul(fault) && fault !== 0;
  show("faultBox", showFault);
  if (showFault) $("faultBox").textContent = `VESC FAULT ${fault} ${me.fault_name || ""}`;

  // last command we sent + last pit command seen
  const c = (S.commands || [])[0];
  let cn = "";
  if (c) {
    const what = c.cmd === "mode" ? MODES[c.arg] : CMD_TEXT[c.cmd] || c.cmd;
    if (c.state === "sent") cn = `${what}: sent, waiting for the kart...`;
    else if (c.state === "echoed") cn = `${what}: ${RESULT_TEXT[c.result_name] || c.result_name}`;
    else if (c.state === "no_echo") cn = `${what}: NO REPLY from the kart computer`;
    else if (c.state === "link_down" || c.state === "link_lost") cn = `${what}: NOT SENT (no link)`;
    if (c.age_s > 30) cn = "";
  }
  const pit = (S.cmds_seen || []).filter((x) => x.src === 0).slice(-1)[0];
  if (!cn && pit && pit.age_s < 60) cn = `pit ${CMD_TEXT[pit.cmd_name] || pit.cmd_name}: ${RESULT_TEXT[pit.result_name] || pit.result_name}`;
  if (!cn && me.id_mismatch) cn = `Heltec says kart ${me.heltec_id}; display config says ${me.configured_id}`;
  $("cmdNote").textContent = cn;

  renderGame(game, fresh, st);
  drawMap();
}

/* ------------------------------------------------------- game: speedo */
const SPEEDO = { cx: 180, cy: 150, r: 128, a0: 160, a1: 380, max: 30 };
function polar(a, r) { const t = a * Math.PI / 180; return [SPEEDO.cx + r * Math.cos(t), SPEEDO.cy + r * Math.sin(t)]; }
function arcPath(a0, a1, r) {
  const p0 = polar(a0, r), p1 = polar(a1, r);
  return `M${p0[0].toFixed(1)} ${p0[1].toFixed(1)} A${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${p1[0].toFixed(1)} ${p1[1].toFixed(1)}`;
}
(function buildSpeedo() {
  $("arcBg").setAttribute("d", arcPath(SPEEDO.a0, SPEEDO.a1, SPEEDO.r));
  let svg = "";
  for (let v = 0; v <= SPEEDO.max; v += 5) {
    const a = SPEEDO.a0 + (SPEEDO.a1 - SPEEDO.a0) * v / SPEEDO.max, major = v % 15 === 0;   // labels clear of the number
    const p = polar(a, SPEEDO.r - 14), q = polar(a, SPEEDO.r - (major ? 30 : 24));
    svg += `<line class="tick${major ? "" : " minor"}" x1="${p[0].toFixed(1)}" y1="${p[1].toFixed(1)}" x2="${q[0].toFixed(1)}" y2="${q[1].toFixed(1)}"/>`;
    if (major) { const l = polar(a, SPEEDO.r - 44); svg += `<text class="ticklbl" x="${l[0].toFixed(1)}" y="${(l[1] + 5).toFixed(1)}" text-anchor="middle">${v}</text>`; }
  }
  $("ticks").innerHTML = svg;
})();
function drawSpeedo(mph, kind) {
  const f = nul(mph) ? 0 : Math.max(0, Math.min(1, mph / SPEEDO.max));
  $("arcFg").setAttribute("d", f > 0.004 ? arcPath(SPEEDO.a0, SPEEDO.a0 + (SPEEDO.a1 - SPEEDO.a0) * f, SPEEDO.r) : "");
  $("speedo").className = "speedo" + (kind === "boost" || kind === "mud" || kind === "stopped" ? " " + kind : "");
}

/* ------------------------------------------- game: meter, laps, events */
function lapTime(s) {
  if (nul(s)) return "--";
  const m = Math.floor(s / 60), r = s - m * 60;
  return `${m}:${r < 10 ? "0" : ""}${r.toFixed(1)}`;
}
function renderGame(game, fresh, st) {
  // boost meter: live scale vs the requested mode's scale
  const m = game.meter || {}, kind = fresh ? (m.kind || "unknown") : "unknown";
  const bm = $("boostMeter");
  bm.className = "meter " + kind;
  const sc = fresh && !nul(m.scale) ? Math.max(0, Math.min(1, m.scale)) : null, ms = nul(m.mode_scale) ? null : m.mode_scale;
  $("bMark").style.left = ms === null ? "100%" : ms * 100 + "%";
  $("bMark").style.display = ms === null ? "none" : "";
  const base = sc === null ? 0 : ms === null ? sc : Math.min(sc, ms);
  $("bBase").style.width = base * 100 + "%";
  const fill = $("bFill");
  if (sc !== null && ms !== null && kind === "boost") { fill.style.left = ms * 100 + "%"; fill.style.width = Math.max(0.03, sc - ms) * 100 + "%"; }
  else if (sc !== null && ms !== null && kind === "mud") { fill.style.left = sc * 100 + "%"; fill.style.width = (ms - sc) * 100 + "%"; }
  else fill.style.width = "0";
  const ov = m.override;
  const left = ov && ov.left_s > 0 ? ` ${Math.ceil(ov.left_s)}s` : "";
  $("boostLabel").textContent = kind === "boost" ? "BOOST!" + left : kind === "mud" ? "MUD" + left
    : kind === "stopped" ? "STOP" : kind === "normal" ? `POWER ${Math.round(sc * 100)}%` : "POWER --";

  // lap panel or the GAME OFF chip
  const on = game.on === true;
  show("lapPanel", on);
  show("gameChip", !on);
  if (!on) $("gameChip").textContent = "GAME OFF";
  else {
    const lap = game.lap || {};
    $("lapPanel").classList.toggle("idle", !lap.running);
    $("lapNum").textContent = lap.running ? String(lap.count + 1) : "-";
    $("lapCur").textContent = lap.running ? lapTime(lap.current_s) : "CROSS THE LINE";
    $("lapLast").textContent = lapTime(lap.last_s);
    $("lapBest").textContent = lapTime(lap.best_s);
  }
  gameEvents(game);
}

let lastEvSeq = null;
function gameEvents(game) {
  const evs = game.events || [];
  const top = evs.reduce((a, e) => Math.max(a, e.seq), 0);
  if (lastEvSeq === null || top < lastEvSeq) { lastEvSeq = top; return; }   // first frame / server restarted: no replay
  evs.filter((e) => e.seq > lastEvSeq && e.age_s < 3).forEach((e) => {
    if (e.ev === "boost") { flash("BOOST!", "boost"); sound("boost"); }
    else if (e.ev === "mud") { flash("MUD", "mud"); sound("mud"); }
    else if (e.ev === "lap") { flash(`LAP ${e.lap}  ${lapTime(e.lap_s)}${e.best ? "  BEST" : ""}`, "lap"); sound(e.best ? "best" : "lap"); }
    else if (e.ev === "lap_start") sound("go");
  });
  lastEvSeq = top;
}
let flashTimer = null;
function flash(text, cls) {
  const f = $("flash");
  f.className = "flash hidden"; void f.offsetWidth;     // restart the animation
  $("flashText").textContent = text;
  f.className = "flash f" + cls;
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { f.className = "flash hidden"; }, cls === "lap" ? 1200 : 800);
}

/* synthesized sounds (no audio files). The kiosk runs Chromium with
   --autoplay-policy=no-user-gesture-required; elsewhere audio starts after the first touch. */
let actx = null;
function audio() {
  if (!cfg("sound", true)) return null;
  try {
    if (!actx) actx = new (window.AudioContext || window.webkitAudioContext)();
    if (actx.state === "suspended") actx.resume();
  } catch (e) { return null; }
  return actx;
}
window.addEventListener("pointerdown", () => { if (cfg("sound", true)) audio(); }, { capture: true });
function tone(ctx, t0, dur, type, f0, f1, vol) {
  const o = ctx.createOscillator(), g = ctx.createGain();
  o.type = type;
  o.frequency.setValueAtTime(f0, t0);
  if (f1) o.frequency.exponentialRampToValueAtTime(f1, t0 + dur);
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(vol, t0 + 0.02);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  o.connect(g); g.connect(ctx.destination);
  o.start(t0); o.stop(t0 + dur + 0.05);
}
function sound(kind) {
  const ctx = audio(); if (!ctx || ctx.state !== "running") return;
  const t = ctx.currentTime + 0.01;
  if (kind === "boost") { tone(ctx, t, 0.55, "sawtooth", 180, 900, 0.22); tone(ctx, t + 0.05, 0.5, "square", 360, 1500, 0.08); tone(ctx, t + 0.55, 0.25, "square", 1320, 0, 0.12); }
  else if (kind === "mud") { tone(ctx, t, 0.45, "square", 140, 60, 0.2); tone(ctx, t + 0.12, 0.35, "triangle", 90, 50, 0.25); }
  else if (kind === "lap") { tone(ctx, t, 0.14, "square", 880, 0, 0.14); tone(ctx, t + 0.18, 0.3, "square", 1320, 0, 0.14); }
  else if (kind === "best") { [880, 1109, 1319, 1760].forEach((f, i) => tone(ctx, t + i * 0.13, 0.2, "square", f, 0, 0.13)); }
  else if (kind === "go") tone(ctx, t, 0.25, "square", 1046, 0, 0.12);
}

/* ---------------------------------------------- game: zones on the map */
function drawZones(ctx, en, px) {
  const doc = S.map && S.map.zones;
  if (!doc || !Array.isArray(doc.zones)) return;
  const on = !!(S.game && S.game.on), inside = new Set((S.game && S.game.inside) || []);
  ctx.save();
  if (!on) { ctx.globalAlpha = 0.35; ctx.setLineDash([8, 6]); }
  doc.zones.forEach((z) => {
    try {
      if (z.type === "lapline" && z.a && z.b) return lapLine(ctx, px(en(z.a[0], z.a[1])), px(en(z.b[0], z.b[1])), on);
      if (z.type !== "boost" && z.type !== "mud") return;
      const boost = z.type === "boost";
      let c, rad;
      ctx.beginPath();
      if (z.shape === "circle" && !nul(z.lat)) {
        c = px(en(z.lat, z.lon)); rad = Math.max(4, z.r_m * view.scale);
        ctx.arc(c[0], c[1], rad, 0, 2 * Math.PI);
      } else if (z.shape === "polygon" && Array.isArray(z.points) && z.points.length >= 3) {
        const pts = z.points.map((p) => px(en(p[0], p[1])));
        pts.forEach((q, i) => (i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1])));
        ctx.closePath();
        c = [pts.reduce((a, q) => a + q[0], 0) / pts.length, pts.reduce((a, q) => a + q[1], 0) / pts.length];
        rad = Math.min(...pts.map((q) => Math.hypot(q[0] - c[0], q[1] - c[1])));
      } else return;
      ctx.fillStyle = !on ? "rgba(140,140,140,.4)" : boost ? "rgba(255,176,0,.45)" : "rgba(110,70,30,.55)";
      ctx.fill();
      ctx.lineWidth = inside.has(z.id) ? 7 : 4;
      ctx.strokeStyle = !on ? "#666" : boost ? "#e08a00" : "#4a2e12";
      ctx.stroke();
      if (boost) chevrons(ctx, c, rad, on);
      else mudDots(ctx, c, rad, on);
    } catch (e) { /* a malformed zone never breaks the map */ }
  });
  ctx.restore();
}
function chevrons(ctx, c, rad, on) {
  const s = Math.max(5, Math.min(18, rad * 0.28));
  ctx.save(); ctx.setLineDash([]);
  ctx.lineWidth = Math.max(3, s * 0.45); ctx.lineJoin = "miter"; ctx.lineCap = "butt";
  for (let i = -1; i <= 1; i++) {
    const y = c[1] - i * s * 1.1;
    ctx.beginPath(); ctx.moveTo(c[0] - s, y + s * 0.6); ctx.lineTo(c[0], y - s * 0.4); ctx.lineTo(c[0] + s, y + s * 0.6);
    ctx.strokeStyle = "#000"; ctx.lineWidth = Math.max(5, s * 0.6); ctx.stroke();
    ctx.strokeStyle = on ? "#fff" : "#ddd"; ctx.lineWidth = Math.max(3, s * 0.35); ctx.stroke();
  }
  ctx.restore();
}
function mudDots(ctx, c, rad, on) {
  const s = Math.max(2, Math.min(7, rad * 0.12));
  ctx.save(); ctx.setLineDash([]);
  ctx.fillStyle = on ? "#2b1a0a" : "#555";
  [[-1.6, -0.6], [0.4, -1.2], [1.5, 0.3], [-0.5, 1], [0.9, 1.5], [-1.8, 1.4]].forEach(([dx, dy]) => {
    ctx.beginPath(); ctx.arc(c[0] + dx * s * 2.2, c[1] + dy * s * 2.2, s * (0.7 + 0.25 * Math.abs(dx)), 0, 2 * Math.PI); ctx.fill();
  });
  ctx.restore();
}
function lapLine(ctx, a, b, on) {
  const L = Math.hypot(b[0] - a[0], b[1] - a[1]); if (L < 2) return;
  const w = Math.max(8, Math.min(16, 2.5 * view.scale)), n = Math.max(2, Math.round(L / w));
  ctx.save(); ctx.setLineDash([]);
  ctx.translate(a[0], a[1]); ctx.rotate(Math.atan2(b[1] - a[1], b[0] - a[0]));
  ctx.fillStyle = "#000"; ctx.fillRect(-2, -w - 2, L + 4, 2 * w + 4);
  const cw = L / n;
  for (let i = 0; i < n; i++) for (let j = 0; j < 2; j++) {
    ctx.fillStyle = (i + j) % 2 ? "#000" : on ? "#fff" : "#bbb";
    ctx.fillRect(i * cw, (j - 1) * w, cw, w);
  }
  ctx.restore();
}

/* --------------------------------------------------- mode buttons (hold) */
function post(url, body) {
  return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    .then((r) => r.json().catch(() => ({ ok: false, error: "HTTP " + r.status })))
    .catch((e) => ({ ok: false, error: String(e) }));
}
function requestMode(m) {
  $("cmdNote").textContent = `${MODES[m]}: sending...`;
  post("/cmd", { cmd: "mode", arg: m }).then((j) => { if (!j.ok) $("cmdNote").textContent = `${MODES[m]}: ${j.error}`; });
}
document.querySelectorAll(".mbtn").forEach((btn) => {
  let timer = null, t0 = 0;
  const reset = () => { if (timer) cancelAnimationFrame(timer); timer = null; btn.style.setProperty("--p", 0); };
  btn.addEventListener("pointerdown", (e) => {
    if (btn.disabled) return;
    try { btn.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    const hold = cfg("mode_hold_ms", 500);
    if (hold <= 0) { requestMode(Number(btn.dataset.mode)); return; }
    t0 = performance.now();
    const tick = () => {
      const p = Math.min(1, (performance.now() - t0) / hold);
      btn.style.setProperty("--p", p);
      if (p >= 1) { reset(); requestMode(Number(btn.dataset.mode)); } else timer = requestAnimationFrame(tick);
    };
    timer = requestAnimationFrame(tick);
  });
  ["pointerup", "pointercancel", "lostpointercapture"].forEach((ev) => btn.addEventListener(ev, () => { if (timer) reset(); }));
});

/* ------------------------------------------------------------------ map */
const R_EARTH = 6371000;
const view = { follow: true, pausedUntil: 0, all: false, scale: null, cx: 0, cy: 0, rot: 0 };
let userFollow = true;               // the FOLLOW button; touching the map pauses it
let mapImg = null, mapImgSrc = null;
const DR_MAX_S = 1.2;
const dr = new Map();

function projection() {
  const pkg = S.map && S.map.package;
  let o = pkg && pkg.origin;
  if (!o && pkg && pkg.bounds) { const b = pkg.bounds; o = { lat: (b.north + b.south) / 2, lon: (b.east + b.west) / 2 }; }
  if (!o && S.self.gps && S.self.gps.last_fix) o = S.self.gps.last_fix;
  if (!o) { const k = S.fleet.find((x) => x.gps && x.gps.last_fix); if (k) o = k.gps.last_fix; }
  if (!o) return null;
  const cl = Math.cos(o.lat * Math.PI / 180);
  return (lat, lon) => [(lon - o.lon) * Math.PI / 180 * R_EARTH * cl, (lat - o.lat) * Math.PI / 180 * R_EARTH];
}
/* own kart: the game engine's filtered sample (5 Hz gps lines, else the self line) when fresh */
function gamePos() {
  const p = S.game && S.game.pos;
  return p && !nul(p.lat) && p.age_s !== null && p.age_s < 3 ? p : null;
}
function gpsWeak() { const p = gamePos(); return !!p && p.quality === "poor"; }
function selfFix(me) {
  const p = gamePos();
  if (p) return { lat: p.lat, lon: p.lon, heading: p.heading, gps_kph: p.kph || 0, quality: p.quality };
  return me.gps && me.gps.last_fix;
}
function drPosition(key, f, now, en, fresh) {
  if (!f) return null;
  let d = dr.get(key);
  if (!d || d.fixLat !== f.lat || d.fixLon !== f.lon) {
    const e = en(f.lat, f.lon);
    d = { fixLat: f.lat, fixLon: f.lon, atMs: now, kph: f.gps_kph || 0, heading: f.heading, dispE: d ? d.dispE : e[0], dispN: d ? d.dispN : e[1] };
    dr.set(key, d);
  }
  const base = en(f.lat, f.lon);
  let target = base;
  if (fresh && d.kph > 2 && !nul(d.heading)) {
    const dt = Math.min(DR_MAX_S, (now - d.atMs) / 1000), sp = d.kph / 3.6, r = d.heading * Math.PI / 180;
    target = [base[0] + Math.sin(r) * sp * dt, base[1] + Math.cos(r) * sp * dt];
  }
  if (Math.hypot(target[0] - d.dispE, target[1] - d.dispN) > 50) { d.dispE = target[0]; d.dispN = target[1]; }
  d.dispE += (target[0] - d.dispE) * 0.35; d.dispN += (target[1] - d.dispN) * 0.35;
  return [d.dispE, d.dispN];
}
function niceStep(m) { const s = [5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000]; return s.find((x) => x >= m) || 5000; }

function drawMap() {
  const cv = $("map");
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  if (!W || !H) return;
  if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#d9d9d4"; ctx.fillRect(0, 0, W, H);
  const msg = $("mapMsg");
  const en = projection();
  const pkg = (S.map && S.map.package) || null;
  if (!en) { msg.textContent = "No map yet and no GPS fix"; show("mapMsg", true); $("mapNote").textContent = mapNoteText(); return; }

  const now = performance.now();
  const me = S.self, mf = selfFix(me);
  const meFresh = me.link === "online" && me.gps.fix_fresh;
  const meGood = meFresh && (!mf || !mf.quality || mf.quality === "good");    // poor / outlier: drawn greyed
  const mePos = mf ? drPosition("self", mf, now, en, meFresh) : null;

  // follow resumes follow_resume_s after the last touch
  if (userFollow && !view.follow && Date.now() >= view.pausedUntil) { view.follow = true; view.all = false; view.scale = null; }
  const resumeIn = userFollow && !view.follow ? Math.ceil((view.pausedUntil - Date.now()) / 1000) : 0;
  show("resumeNote", resumeIn > 0);
  if (resumeIn > 0) $("resumeNote").textContent = `follow in ${resumeIn} s`;
  $("followBtn").classList.toggle("sel", userFollow);

  if (view.scale === null || view.all || (view.follow && !mePos)) fitAll(en, pkg, W, H);
  if (view.follow && mePos) {
    view.cx = mePos[0]; view.cy = mePos[1];
    view.scale = view.all ? view.scale : (view.followScale || H / cfg("follow_span_m", 160));
  }
  const headingUp = view.follow && cfg("map_orientation", "north_up") === "heading_up" && meFresh && !nul(mf.heading) && (mf.gps_kph || 0) > 3;
  if (headingUp) view.rot += angleDiff(mf.heading * Math.PI / 180, view.rot) * 0.3;
  else view.rot = 0;
  const cr = Math.cos(view.rot), sr = Math.sin(view.rot);
  const px = (p) => {
    const dx = (p[0] - view.cx) * view.scale, dy = (p[1] - view.cy) * view.scale;
    return [W / 2 + dx * cr - dy * sr, H / 2 - (dx * sr + dy * cr)];
  };

  // imagery (north-up WGS84, placed by its bounds like the pit)
  if (pkg && pkg.image && pkg.bounds) {
    if (mapImgSrc !== pkg.image) { mapImgSrc = pkg.image; mapImg = new Image(); mapImg.onload = () => { if (S) drawMap(); }; mapImg.src = pkg.image; }
    if (mapImg && mapImg.complete && mapImg.naturalWidth) drawImagery(ctx, en, pkg.bounds, W, H, px);
  }
  if (pkg && pkg.course && pkg.course.length > 1) {
    ctx.strokeStyle = "#000"; ctx.lineWidth = 4; ctx.setLineDash([10, 8]); ctx.beginPath();
    pkg.course.forEach((p, i) => { const q = px(en(p[0], p[1])); i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); });
    ctx.stroke(); ctx.setLineDash([]);
  }
  drawZones(ctx, en, px);
  if (pkg && pkg.pit && !nul(pkg.pit.lat)) {
    const b = px(en(pkg.pit.lat, pkg.pit.lon));
    ctx.fillStyle = "#000"; ctx.fillRect(b[0] - 9, b[1] - 9, 18, 18);
    ctx.fillStyle = "#fff"; ctx.font = "bold 10px sans-serif"; ctx.textAlign = "center"; ctx.fillText("PIT", b[0], b[1] + 4); ctx.textAlign = "left";
  }

  // other karts: dots with numbers; stale = grey dashed + age; offline = faded
  S.fleet.forEach((k) => {
    if (!k.gps || !k.gps.last_fix) return;
    const fresh = k.link === "online" && k.gps.fix_fresh;
    const gone = k.link === "offline";
    if (k.trail.length > 1) trail(ctx, k.trail, en, px, fresh ? k.color : "#888", 4, 0.6);
    const p = px(drPosition(k.id, k.gps.last_fix, now, en, fresh) || en(k.gps.last_fix.lat, k.gps.last_fix.lon));
    ctx.beginPath(); ctx.arc(p[0], p[1], 15, 0, 2 * Math.PI);
    ctx.fillStyle = fresh ? k.color : gone ? "rgba(210,210,210,.6)" : "#bbb"; ctx.fill();
    ctx.lineWidth = 3; ctx.strokeStyle = fresh ? "#000" : "#555";
    if (!fresh) ctx.setLineDash([4, 3]);
    ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = "#000"; ctx.font = "900 18px sans-serif"; ctx.textAlign = "center"; ctx.fillText(String(k.id), p[0], p[1] + 6); ctx.textAlign = "left";
    if (!fresh) label(ctx, ageText(k.link === "online" ? k.gps.fix_age_s : k.age_s), p[0] + 18, p[1] - 10);
  });

  // own kart: big arrow along the GPS heading (grey + age when not fresh)
  if (mePos) {
    if (me.trail.length > 1) trail(ctx, me.trail, en, px, "#0b4fb3", 5, 0.75);
    const p = px(mePos);
    const hd = nul(mf.heading) ? null : mf.heading * Math.PI / 180 - view.rot;
    ctx.save(); ctx.translate(p[0], p[1]);
    if (hd !== null && (mf.gps_kph || 0) > 1) {
      ctx.rotate(hd);
      ctx.beginPath(); ctx.moveTo(0, -30); ctx.lineTo(19, 20); ctx.lineTo(0, 10); ctx.lineTo(-19, 20); ctx.closePath();
    } else { ctx.beginPath(); ctx.arc(0, 0, 18, 0, 2 * Math.PI); }
    ctx.lineWidth = 8; ctx.strokeStyle = "#fff"; ctx.stroke();
    ctx.fillStyle = meGood ? me.color : "#999"; ctx.fill();
    ctx.lineWidth = 3; ctx.strokeStyle = "#000"; if (!meGood) ctx.setLineDash([5, 4]); ctx.stroke(); ctx.setLineDash([]);
    ctx.restore();
    if (!meFresh) label(ctx, `last fix ${ageText(me.gps.fix_age_s)}`, p[0] + 24, p[1] - 14);
    else if (!meGood) label(ctx, mf.quality === "outlier" ? "GPS jump ignored" : "weak GPS: zones paused", p[0] + 24, p[1] - 14);
  }
  if (!mf) { msg.textContent = me.link === "online" ? "NO GPS FIX YET" : "NO POSITION"; show("mapMsg", true); }
  else if (!meFresh) { msg.textContent = me.link === "online" ? "NO GPS FIX" : "POSITION FROZEN"; show("mapMsg", true); }
  else show("mapMsg", false);

  // scale bar + north arrow
  const step = niceStep(90 / view.scale), len = step * view.scale;
  ctx.fillStyle = "rgba(255,255,255,.9)"; ctx.fillRect(6, H - 46, len + 70, 26);
  ctx.strokeStyle = "#000"; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(12, H - 28); ctx.lineTo(12 + len, H - 28); ctx.stroke();
  ctx.fillStyle = "#000"; ctx.font = "bold 14px sans-serif"; ctx.fillText(`${step} m`, 18 + len, H - 24);
  ctx.save(); ctx.translate(W - 120, H - 50); ctx.rotate(-view.rot);
  ctx.fillStyle = "rgba(255,255,255,.9)"; ctx.beginPath(); ctx.arc(0, 0, 20, 0, 2 * Math.PI); ctx.fill();
  ctx.fillStyle = "#000"; ctx.beginPath(); ctx.moveTo(0, -16); ctx.lineTo(8, 6); ctx.lineTo(-8, 6); ctx.closePath(); ctx.fill();
  ctx.font = "bold 12px sans-serif"; ctx.textAlign = "center"; ctx.fillText("N", 0, 17); ctx.textAlign = "left";
  ctx.restore();
  $("mapNote").textContent = mapNoteText();
}

function drawImagery(ctx, en, b, W, H, px) {
  const a = en(b.north, b.west), c = en(b.south, b.east);
  if (view.rot === 0) {
    // north-up: draw only the visible part of the (large) image; cheaper on a Pi 3 B
    const p0 = px(a), p1 = px(c);
    const iw = mapImg.naturalWidth, ih = mapImg.naturalHeight, dw = p1[0] - p0[0], dh = p1[1] - p0[1];
    const x0 = Math.max(0, p0[0]), y0 = Math.max(0, p0[1]), x1 = Math.min(W, p1[0]), y1 = Math.min(H, p1[1]);
    if (x1 <= x0 || y1 <= y0) return;
    const sx = (x0 - p0[0]) / dw * iw, sy = (y0 - p0[1]) / dh * ih, sw = (x1 - x0) / dw * iw, sh = (y1 - y0) / dh * ih;
    ctx.drawImage(mapImg, sx, sy, sw, sh, x0, y0, x1 - x0, y1 - y0);
    return;
  }
  ctx.save();
  ctx.translate(W / 2, H / 2); ctx.rotate(-view.rot);
  const s = view.scale;
  ctx.drawImage(mapImg, (a[0] - view.cx) * s, -(a[1] - view.cy) * s, (c[0] - a[0]) * s, -(c[1] - a[1]) * s);
  ctx.restore();
}
function trail(ctx, pts, en, px, color, w, alpha) {
  ctx.strokeStyle = color; ctx.globalAlpha = alpha; ctx.lineWidth = w; ctx.beginPath();
  pts.forEach((p, i) => { const q = px(en(p[0], p[1])); i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); });
  ctx.stroke(); ctx.globalAlpha = 1;
}
function label(ctx, text, x, y) {
  ctx.font = "bold 14px sans-serif";
  const w = ctx.measureText(text).width;
  ctx.fillStyle = "rgba(255,255,255,.9)"; ctx.fillRect(x - 3, y - 14, w + 6, 19);
  ctx.fillStyle = "#000"; ctx.fillText(text, x, y);
}
function angleDiff(a, b) { let d = a - b; while (d > Math.PI) d -= 2 * Math.PI; while (d < -Math.PI) d += 2 * Math.PI; return d; }
function fitAll(en, pkg, W, H) {
  const pts = [];
  if (S.self.gps && S.self.gps.last_fix) pts.push(en(S.self.gps.last_fix.lat, S.self.gps.last_fix.lon));
  S.fleet.forEach((k) => {
    if (!k.gps || !k.gps.last_fix || k.link === "offline") return;
    pts.push(en(k.gps.last_fix.lat, k.gps.last_fix.lon));
    k.trail.forEach((p) => pts.push(en(p[0], p[1])));
  });
  if (pkg && pkg.course) pkg.course.forEach((p) => pts.push(en(p[0], p[1])));
  if (pts.length < 2 && pkg && pkg.bounds) pts.push(en(pkg.bounds.north, pkg.bounds.west), en(pkg.bounds.south, pkg.bounds.east));
  if (!pts.length) pts.push([0, 0]);
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const span = Math.max(80, Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
  view.scale = Math.min(W, H) / (span * 1.2);
  view.cx = (Math.max(...xs) + Math.min(...xs)) / 2;
  view.cy = (Math.max(...ys) + Math.min(...ys)) / 2;
}
function mapNoteText() {
  const m = S.map || {}, pkg = m.package, sy = m.sync || {};
  const pit = sy.reachable === true ? "pit reachable" : sy.reachable === false ? "pit not reachable" : "pit not checked yet";
  if (!pkg) return `no course map cached (${pit}; it downloads on the pit Wi-Fi)`;
  return `map v${pkg.version} cached ${pkg.fetched_wall ? pkg.fetched_wall.slice(0, 16).replace("T", " ") : ""} | ${pit}`
    + (pkg.attribution ? ` | imagery: ${pkg.attribution}` : "");
}

/* map input: one finger pans, two fingers pinch-zoom, both pause auto-follow */
function touched() {
  view.follow = false; view.all = false;
  view.pausedUntil = Date.now() + cfg("follow_resume_s", 10) * 1000;
}
(function setupMapInput() {
  const cv = $("map");
  const ptrs = new Map();
  let drag = null, pinch = null;
  const MIN_SCALE = 0.02, MAX_SCALE = 40;
  const local = (e) => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  const clampScale = (v) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, v));
  const world = (x, y) => [view.cx + (x - cv.clientWidth / 2) / view.scale, view.cy - (y - cv.clientHeight / 2) / view.scale];
  const unrotate = () => { view.rot = 0; };     // free pan is always north-up
  cv.addEventListener("pointerdown", (e) => {
    if (!S || view.scale === null) return;
    cv.setPointerCapture(e.pointerId);
    ptrs.set(e.pointerId, local(e));
    touched(); unrotate();
    if (ptrs.size === 1) { const [x, y] = local(e); drag = { x, y, cx: view.cx, cy: view.cy, moved: false }; pinch = null; }
    else if (ptrs.size === 2) {
      drag = null;
      const [a, b] = [...ptrs.values()];
      pinch = { d0: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1, scale0: view.scale, anchor: world((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) };
    }
  });
  cv.addEventListener("pointermove", (e) => {
    if (!ptrs.has(e.pointerId)) return;
    ptrs.set(e.pointerId, local(e));
    touched();
    if (pinch && ptrs.size >= 2) {
      const [a, b] = [...ptrs.values()];
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]) || 1;
      const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
      view.scale = clampScale(pinch.scale0 * d / pinch.d0);
      view.followScale = view.scale;
      view.cx = pinch.anchor[0] - (mx - cv.clientWidth / 2) / view.scale;
      view.cy = pinch.anchor[1] + (my - cv.clientHeight / 2) / view.scale;
      requestDraw();
    } else if (drag && ptrs.size === 1) {
      const [x, y] = local(e);
      const dx = x - drag.x, dy = y - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 6) drag.moved = true;
      if (drag.moved) { view.cx = drag.cx - dx / view.scale; view.cy = drag.cy + dy / view.scale; requestDraw(); }
    }
  });
  const release = (e) => {
    ptrs.delete(e.pointerId);
    if (ptrs.size < 2) pinch = null;
    if (ptrs.size === 1) { const [x, y] = [...ptrs.values()][0]; drag = { x, y, cx: view.cx, cy: view.cy, moved: true }; }
    if (ptrs.size === 0) drag = null;
  };
  ["pointerup", "pointercancel", "lostpointercapture"].forEach((ev) => cv.addEventListener(ev, release));
  cv.addEventListener("wheel", (e) => {
    e.preventDefault(); touched(); unrotate();
    view.scale = clampScale(view.scale * Math.pow(1.0015, -e.deltaY)); view.followScale = view.scale; requestDraw();
  }, { passive: false });
})();

document.querySelector(".mapctl").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b || !S) return;
  if (b.id === "followBtn") {
    userFollow = !userFollow;
    try { localStorage.setItem("gk_follow", userFollow ? "1" : "0"); } catch (err) { /* ignore */ }
    if (userFollow) { view.follow = true; view.all = false; } else { view.follow = false; }
  } else if (b.dataset.zoom === "all") { touched(); view.all = true; view.rot = 0; }
  else {
    if (view.scale === null) return;
    const f = b.dataset.zoom === "in" ? 1.5 : 1 / 1.5;
    view.scale = Math.min(40, Math.max(0.02, view.scale * f)); view.followScale = view.scale; view.all = false;
    if (!view.follow) touched();
  }
  requestDraw();
});
try { if (localStorage.getItem("gk_follow") === "0") { userFollow = false; view.follow = false; } } catch (err) { /* ignore */ }

/* -------------------------------------------------------------- wiring */
let drawQueued = false;
function requestDraw() { if (drawQueued || !S) return; drawQueued = true; requestAnimationFrame(() => { drawQueued = false; drawMap(); }); }

function connect() {
  const es = new EventSource("/events");
  es.onmessage = (ev) => {
    lastMsg = Date.now();
    try { S = JSON.parse(ev.data); } catch (e) { return; }
    render();
  };
}
setInterval(() => {
  if (Date.now() - lastMsg > 3000) {
    const cb = $("connBanner");
    cb.textContent = lastMsg ? "DISPLAY SERVER NOT RESPONDING - screen is frozen" : "Starting display...";
    cb.classList.remove("hidden");
  }
}, 1000);
// Between SSE frames, redraw the map only while something moves (dead reckoning), at most map_fps.
let lastAnim = 0;
(function anim(t) {
  requestAnimationFrame(anim);
  if (!S || t - lastAnim < 1000 / cfg("map_fps", 10)) return;
  lastAnim = t;
  const moving = (k) => k.link === "online" && k.gps && k.gps.fix_fresh && k.gps.last_fix && (k.gps.last_fix.gps_kph || 0) > 2;
  if (moving(S.self) || S.fleet.some(moving) || (userFollow && !view.follow)) drawMap();
})(0);
window.addEventListener("resize", requestDraw);
connect();
