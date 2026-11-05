/* GoKart pit UI: shared by the operator page (/) and the touch kiosk (/kiosk).
   Vanilla JS, no external assets. State arrives over Server-Sent Events (/events)
   about 4 times a second; every browser shows the same server-side state. */
"use strict";

const KIOSK = document.body.dataset.page === "kiosk";
const MODES = ["ECO", "NORMAL", "SPORT"];
const LINK_TEXT = { online: "ONLINE", stale: "STALE", offline: "OFFLINE", never: "NEVER HEARD" };
const LINK_CLASS = { online: "ok", stale: "warn", offline: "stale", never: "stale" };
let S = null;
let lastMsg = 0;
const $ = (id) => document.getElementById(id);

/* per-viewer conveniences only; the page works without storage */
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ignore */ } },
};
const filters = { online: store.get("gk_f_online", false), entered: store.get("gk_f_entered", false) };

function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function nul(v) { return v === null || v === undefined || (typeof v === "number" && Number.isNaN(v)); }
function fmt(v, d = 0, unit = "") { return nul(v) ? "--" : Number(v).toFixed(d) + unit; }
function ageText(a) {
  if (nul(a)) return "never";
  if (a < 60) return a.toFixed(a < 10 ? 1 : 0) + "s";
  if (a < 3600) return Math.floor(a / 60) + "m";
  return Math.floor(a / 3600) + "h";
}
function hhmmss(iso) { if (!iso) return "--"; const d = new Date(iso); return d.toLocaleTimeString([], { hour12: false }); }

let toastTimer = null;
function toast(msg, err) {
  let t = $("toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.appendChild(t); }
  t.className = "toast" + (err ? " err" : "");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), err ? 5000 : 2500);
}

async function post(url, body) {
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.status === 401) { location.href = "/login"; return null; }
    const j = await r.json().catch(() => ({ ok: false, error: "HTTP " + r.status }));
    if (!j.ok) toast(j.error || "refused", true);
    return j;
  } catch (e) {
    toast("Pit server unreachable", true);
    return null;
  }
}

function cmdLabel(cmd, kart, arg) {
  const who = kart === 255 ? "ALL" : "K" + kart;
  return cmd === "mode" ? `${who} ${MODES[arg]}` : `${who} ${cmd.toUpperCase()}`;
}
function sendCmd(cmd, kart, arg) {
  const body = { cmd, kart };
  if (cmd === "mode") body.arg = arg;
  post("/cmd", body).then((j) => { if (j && j.ok) toast(`${cmdLabel(cmd, kart, arg)} sent (seq ${j.command.seq ?? "queued"})`); });
}

/* press-and-hold buttons (STOP ALL, START ALL, RACE ON) */
function holdButton(btn, ms, fire) {
  let timer = null, t0 = 0, raf = 0;
  const reset = () => { clearTimeout(timer); timer = null; cancelAnimationFrame(raf); btn.style.setProperty("--p", 0); };
  const step = () => { btn.style.setProperty("--p", Math.min(1, (performance.now() - t0) / ms)); if (timer) raf = requestAnimationFrame(step); };
  btn.addEventListener("pointerdown", (e) => {
    if (btn.disabled) return;
    e.preventDefault();
    try { btn.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    t0 = performance.now();
    timer = setTimeout(() => { reset(); fire(); }, ms);
    raf = requestAnimationFrame(step);
  });
  ["pointerup", "pointercancel", "lostpointercapture"].forEach((ev) => btn.addEventListener(ev, () => { if (timer) reset(); }));
  btn.addEventListener("contextmenu", (e) => e.preventDefault());
}

/* ------------------------------------------------------------------ cards */
const cards = {};
function kartButtons(k) {
  return `<div class="kbtns">
    <button data-c="mode" data-k="${k}" data-a="0">ECO</button>
    <button data-c="mode" data-k="${k}" data-a="1">NORMAL</button>
    <button data-c="mode" data-k="${k}" data-a="2">SPORT</button>
    <button class="b-stop" data-c="stop" data-k="${k}">STOP</button>
    <button class="b-start" data-c="start" data-k="${k}">START</button></div>`;
}
function buildCards() {
  const box = $("cards");
  box.innerHTML = "";
  for (let k = 1; k <= 8; k++) {
    const el = document.createElement("div");
    el.className = "card never";
    el.innerHTML = `
      <div class="hdr"><div class="swatch" data-f="sw">${k}</div><span class="chip" data-f="link">--</span>
        <span class="sm" data-f="age"></span><div class="badges" data-f="badges"></div></div>
      <div class="row">
        <span><span class="big val" data-f="batt">--</span> <span class="sm val" data-f="volts"></span></span>
        <span><span class="big val" data-f="wheel">--</span><span class="lbl"> mph wheel*</span></span></div>
      <div class="row sm"><span class="faultline" data-f="fault"></span><span class="val" data-f="esc"></span></div>
      <div class="row sm"><span data-f="mode"></span></div>
      <div class="row sm"><span class="val" data-f="gpsmph"></span><span class="val" data-f="gps"></span>
        <span class="val" data-f="rssi"></span><span class="outcome" data-f="out"></span></div>
      ${KIOSK ? "" : kartButtons(k)}`;
    const f = {};
    el.querySelectorAll("[data-f]").forEach((n) => { f[n.dataset.f] = n; });
    if (KIOSK) el.addEventListener("click", () => openSheet(k));
    box.appendChild(el);
    cards[k] = { el, f };
  }
}

function faultInfo(k) {
  const v = k.vesc || {};
  if (nul(v.fault)) return { text: "fault unknown", cls: "faultline unk", bad: false };
  if (v.fault === 0) return k.vesc_fresh ? { text: "no fault", cls: "faultline okf", bad: false }
    : { text: "no fault (stale)", cls: "faultline unk", bad: false };
  return { text: `FAULT ${v.fault} ${k.fault_name || ""}`, cls: "faultline", bad: true };
}
function modeText(k) {
  const st = k.status || {};
  const req = k.mode_req_name || "--", act = k.mode_actual_name || "?";
  const conf = st.mode_confirmed === true ? "✓ confirmed" : (st.mode_confirmed === false ? "unconfirmed" : "");
  return `<b>${esc(req)}</b> → ${esc(act)} <span class="${st.mode_confirmed ? "o-applied" : "o-unknown"}">${conf}</span>`;
}
function outcomeText(k) {
  const pk = S.commands.per_kart[String(k)];
  if (!pk) return "";
  const what = pk.cmd === "mode" ? MODES[pk.arg] : pk.cmd.toUpperCase();
  return `<span class="o-${pk.outcome}">${esc(what)}: ${esc(pk.outcome)}</span>`;
}

function visible(k) {
  const entered = S.fleet.race.entered || [];
  if (filters.online && k.link !== "online") return false;
  if (filters.entered && !entered.includes(k.id)) return false;
  return true;
}

function updateCards() {
  const entered = S.fleet.race.entered || [];
  const linkUp = S.server.link.connected;
  let hidden = 0;
  for (const k of S.fleet.karts) {
    const c = cards[k.id];
    const st = k.status || {}, v = k.vesc || {}, d = k.derived || {}, g = k.gps || {};
    const show = visible(k);
    if (!show) hidden++;
    c.el.className = "card " + k.link + (show ? "" : " hidden");
    c.f.sw.style.background = k.color;
    c.f.link.textContent = LINK_TEXT[k.link];
    c.f.link.className = "chip " + LINK_CLASS[k.link];
    c.f.age.textContent = nul(k.age_s) ? "" : ageText(k.age_s) + " ago";
    const old = !k.vesc_fresh && !nul(k.vesc_age_s) ? ` (${ageText(k.vesc_age_s)} old)` : "";
    c.f.batt.textContent = nul(d.battery_pct) ? "--" : Math.round(d.battery_pct) + "%";
    c.f.volts.textContent = fmt(v.volts, 1, "V") + (d.under_load ? (KIOSK ? " load" : " under load") : "") + old;
    c.f.wheel.textContent = nul(d.wheel_mph) ? "--" : Math.abs(d.wheel_mph).toFixed(0);
    c.f.batt.classList.toggle("old", !k.vesc_fresh);
    c.f.wheel.classList.toggle("old", !k.vesc_fresh);
    c.f.gpsmph.textContent = g.fix_fresh && !nul(d.gps_mph) ? `GPS ${d.gps_mph.toFixed(0)} mph` : "GPS -- mph";
    c.f.mode.innerHTML = modeText(k);
    c.f.esc.textContent = "ESC " + fmt(v.esc_c, 0, "°C");
    const fi = faultInfo(k);
    c.f.fault.textContent = fi.text;
    c.f.fault.className = fi.cls;
    c.f.gps.textContent = (g.fix_now === true ? `${fmt(g.sats_now)} sats` : (g.fix_now === false ? "NO FIX" : "fix --"))
      + (g.last_fix && !g.fix_fresh ? ` (last ${ageText(g.fix_age_s)})` : "");
    c.f.rssi.textContent = `${fmt(st.rssi, 0, " dBm")} / ${fmt(st.snr, 1, " dB")}`;
    c.f.out.innerHTML = outcomeText(k.id);
    const b = [];
    if (st.stopped === true) b.push('<span class="badge stopped">STOPPED</span>');
    if (fi.bad) b.push('<span class="badge fault">FAULT</span>');
    if (st.vesc_link === false) b.push('<span class="badge unknown">VESC LINK DOWN</span>');
    if (st.writes === false) b.push('<span class="badge grey">WRITES OFF</span>');
    if (entered.includes(k.id)) b.push('<span class="badge entered">ENTERED</span>');
    c.f.badges.innerHTML = b.join("");
    c.el.querySelectorAll("[data-c]").forEach((btn) => {
      btn.disabled = !linkUp;
      if (btn.dataset.c === "mode") btn.classList.toggle("sel", st.mode_req === Number(btn.dataset.a));
    });
  }
  $("hiddenNote").textContent = hidden ? `${hidden} hidden` : "";
}

/* ------------------------------------------------------------ top + fleet */
function chip(id, text, cls) { const e = $(id); if (e) { e.textContent = text; e.className = "chip " + (cls || ""); } }
function updateTop() {
  const L = S.server.link, st = S.fleet.stat, base = S.fleet.base, race = S.fleet.race;
  $("simBanner").classList.toggle("hidden", !S.fleet.simulation);
  chip("linkChip", L.connected ? (KIOSK ? "T-Beam OK" : "T-Beam " + L.desc) : "T-BEAM DOWN", L.connected ? "ok" : "bad");
  if (st) {
    const staleStat = (st.age_s || 0) > 5;
    chip("radioChip", `radio ${st.radio} rx ${st.rx} tx ${st.tx}` + (st.rx_bad ? ` bad ${st.rx_bad}` : ""),
      st.radio === "none" ? "bad" : (staleStat ? "stale" : "ok"));
  } else chip("radioChip", "radio: no stat", "stale");
  if (base) chip("baseChip", base.fix ? `base GPS ${fmt(base.sats)} sats` : "base GPS no fix", base.fix ? "ok" : "warn");
  chip("raceChip", race.active ? `RACE ${MODES[race.mode]} (${race.entered.length})` : "race off", race.active ? "info" : "");
  $("clock").textContent = new Date().toLocaleTimeString([], { hour12: false });
  const cb = $("connBanner");
  if (!L.connected) { cb.textContent = "T-BEAM LINK DOWN: telemetry is frozen and commands cannot be sent"; cb.classList.remove("hidden"); }
  else cb.classList.add("hidden");
  ["stopAll", "startAll"].forEach((id) => { $(id).disabled = !L.connected; });
  document.querySelectorAll("[data-allmode]").forEach((b) => { b.disabled = !L.connected; });
  $("fOnline").classList.toggle("sel", filters.online);
  $("fEntered").classList.toggle("sel", filters.entered);
}

/* --------------------------------------------------------------- outcomes */
let lastOutcomeKey = "";
function summarize(c) {
  const counts = {};
  Object.values(c.outcomes).forEach((o) => { counts[o] = (counts[o] || 0) + 1; });
  return Object.entries(counts).map(([o, n]) => `<span class="o-${o}">${n} ${esc(o)}</span>`).join(", ");
}
function updateOutcomes() {
  const C = S.commands;
  const key = JSON.stringify([C.recent, C.last_fleet]);
  if (key === lastOutcomeKey) return;
  lastOutcomeKey = key;
  const lf = C.last_fleet;
  let h = "";
  if (lf) {
    h += `<div class="sm"><b>Last fleet command:</b> ${esc(cmdLabel(lf.cmd, 255, lf.arg))} &middot; seq ${lf.seq ?? "-"} &middot;
      ${esc(lf.source)} at ${hhmmss(lf.created_wall)} &middot; sent ${lf.tx_count}x (radio ${lf.radio_sent}) &middot; ${esc(lf.state)}</div>
      <div class="ocells">` + [1, 2, 3, 4, 5, 6, 7, 8].map((k) => {
      const o = lf.outcomes[String(k)] || "-";
      return `<div class="ocell o-${o}"><span class="k">K${k}</span>${esc(o)}</div>`;
    }).join("") + "</div>";
  } else h += '<div class="sm">No fleet command yet this session.</div>';
  h += `<table class="cmds"><tr><th>#</th><th>seq</th><th>command</th><th>by</th><th>outcome</th></tr>` +
    C.recent.slice(0, KIOSK ? 8 : 12).map((c) => `<tr><td>${c.id}</td><td>${c.seq ?? "-"}</td>
      <td>${esc(cmdLabel(c.cmd, c.kart, c.arg))}</td><td>${esc(c.source)}<br>${hhmmss(c.created_wall)}</td><td>${summarize(c)}</td></tr>`).join("") +
    "</table>";
  h += `<p class="note">offline = kart not online when sent; no_ack = no echo after retries (outcome unknown);
    unknown = kart says VESC did not confirm; superseded = a newer command went to that kart; link_lost = T-Beam link dropped (never replayed).</p>`;
  $("outcomes").innerHTML = h;
}

/* ------------------------------------------------------------------- race */
let raceBuilt = false;
function buildRace() {
  $("race").innerHTML = `
    <div class="lbl">Entered karts (tap to toggle; shared by all operators)</div>
    <div class="racegrid">${[1, 2, 3, 4, 5, 6, 7, 8].map((k) => `<button data-enter="${k}">K${k}</button>`).join("")}</div>
    <div class="lbl" style="margin-top:8px">Race mode</div>
    <div class="racegrid" style="grid-template-columns:repeat(3,1fr)">${MODES.map((m, i) => `<button data-racemode="${i}">${m}</button>`).join("")}</div>
    <div style="display:flex;gap:6px;margin-top:8px">
      <button id="raceOn" class="hold startall" style="flex:1">RACE ON<small>hold 1 s</small></button>
      <button id="raceOff" style="flex:1">RACE OFF</button></div>
    <p class="note">RACE ON sends the selected mode + START to entered karts and STOP to karts not entered,
      with per-kart outcomes in CMDS. RACE OFF only clears the race flag; it sends nothing.
      Entry and filters never remove a kart from telemetry or fleet commands.</p>`;
  holdButton($("raceOn"), 1000, () => post("/api/race", { active: true }).then((j) => { if (j && j.ok) toast(`Race on: ${j.race.issued.length} commands queued`); }));
  raceBuilt = true;
}
function updateRace() {
  if (!raceBuilt) buildRace();
  const race = S.fleet.race;
  document.querySelectorAll("[data-enter]").forEach((b) => {
    const k = Number(b.dataset.enter);
    const on = race.entered.includes(k);
    b.classList.toggle("sel", on);
    b.style.background = on ? S.fleet.karts[k - 1].color : "";
    b.style.color = on ? "#000" : "";
  });
  document.querySelectorAll("[data-racemode]").forEach((b) => b.classList.toggle("sel", Number(b.dataset.racemode) === race.mode));
  $("raceOn").disabled = !S.server.link.connected;
}

/* -------------------------------------------------------------------- log */
let logBusy = false;
function refreshLog() {
  const box = $("log");
  if (!box || box.offsetParent === null || logBusy) return;
  logBusy = true;
  fetch("/api/log/recent?n=80").then((r) => r.json()).then((rows) => {
    box.textContent = rows.slice().reverse().map((r) => {
      const rest = Object.assign({}, r); delete rest.ts; delete rest.mono; delete rest.kind;
      return `${hhmmss(r.ts)} ${r.kind} ${JSON.stringify(rest)}`;
    }).join("\n");
  }).catch(() => {}).finally(() => { logBusy = false; });
}

/* -------------------------------------------------------------------- map */
const view = { fit: true, scale: 1, cx: 0, cy: 0 };
let mapImg = null, mapImgSrc = null;
const R_EARTH = 6371000;
function niceStep(m) { const steps = [5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000]; return steps.find((s) => s >= m) || 5000; }

function drawMap() {
  const cv = $("map");
  if (!cv || cv.offsetParent === null) return;
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const o = S.fleet.origin;
  const note = $("mapNote");
  if (!o) {
    ctx.fillStyle = "#333"; ctx.font = "bold 16px sans-serif";
    ctx.fillText("No position yet: waiting for a base or kart GPS fix", 12, 30);
    if (note) note.textContent = "";
    return;
  }
  const cl = Math.cos(o.lat * Math.PI / 180);
  const en = (lat, lon) => [(lon - o.lon) * Math.PI / 180 * R_EARTH * cl, (lat - o.lat) * Math.PI / 180 * R_EARTH];
  const M = S.map || {};
  const base = S.fleet.base;
  const shown = S.fleet.karts.filter((k) => visible(k) && k.gps && k.gps.last_fix);

  // auto-fit: karts + base (+ imagery bounds only if no kart has a position)
  if (view.fit) {
    const pts = [];
    shown.forEach((k) => { pts.push(en(k.gps.last_fix.lat, k.gps.last_fix.lon)); k.trail.forEach((p) => pts.push(en(p[0], p[1]))); });
    if (base && base.fix && !nul(base.lat)) pts.push(en(base.lat, base.lon));
    (M.course || []).forEach((p) => pts.push(en(p[0], p[1])));
    if (!pts.length && M.bounds) { pts.push(en(M.bounds.north, M.bounds.west), en(M.bounds.south, M.bounds.east)); }
    if (!pts.length) pts.push([0, 0]);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const span = Math.max(80, Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
    const q = Math.pow(1.25, Math.ceil(Math.log(span * 1.25) / Math.log(1.25)));  // quantized: no jitter
    view.scale = Math.min(W, H) / q;
    view.cx = Math.round((Math.max(...xs) + Math.min(...xs)) / 2 / 10) * 10;
    view.cy = Math.round((Math.max(...ys) + Math.min(...ys)) / 2 / 10) * 10;
  }
  const px = (p) => [W / 2 + (p[0] - view.cx) * view.scale, H / 2 - (p[1] - view.cy) * view.scale];

  // georeferenced background (optional; see pit/server/map/README.md)
  if (M.image && M.bounds) {
    if (mapImgSrc !== M.image) { mapImgSrc = M.image; mapImg = new Image(); mapImg.src = M.image; }
    if (mapImg && mapImg.complete && mapImg.naturalWidth) {
      const a = px(en(M.bounds.north, M.bounds.west)), b = px(en(M.bounds.south, M.bounds.east));
      ctx.globalAlpha = 0.9; ctx.drawImage(mapImg, a[0], a[1], b[0] - a[0], b[1] - a[1]); ctx.globalAlpha = 1;
    }
  }
  if (M.course && M.course.length > 1) {
    ctx.strokeStyle = "#111"; ctx.lineWidth = 3; ctx.setLineDash([8, 6]); ctx.beginPath();
    M.course.forEach((p, i) => { const q = px(en(p[0], p[1])); i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); });
    ctx.stroke(); ctx.setLineDash([]);
  }
  if (base && base.fix && !nul(base.lat)) {
    const b = px(en(base.lat, base.lon));
    ctx.fillStyle = "#000"; ctx.fillRect(b[0] - 7, b[1] - 7, 14, 14);
    ctx.font = "bold 12px sans-serif"; ctx.fillText("PIT", b[0] + 10, b[1] + 4);
  }
  // karts: fresh = online + fresh fix; otherwise frozen at last fix and greyed
  shown.forEach((k) => {
    const fresh = k.link === "online" && k.gps.fix_fresh;
    const gone = k.link === "offline" || k.link === "never";
    if (k.trail.length > 1) {
      ctx.strokeStyle = fresh ? k.color : "#999"; ctx.globalAlpha = 0.55; ctx.lineWidth = 3; ctx.beginPath();
      k.trail.forEach((p, i) => { const q = px(en(p[0], p[1])); i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); });
      ctx.stroke(); ctx.globalAlpha = 1;
    }
    const f = k.gps.last_fix, p = px(en(f.lat, f.lon));
    if (fresh && !nul(f.heading)) {
      const r = f.heading * Math.PI / 180;
      ctx.strokeStyle = "#000"; ctx.lineWidth = 3; ctx.beginPath();
      ctx.moveTo(p[0], p[1]); ctx.lineTo(p[0] + Math.sin(r) * 20, p[1] - Math.cos(r) * 20); ctx.stroke();
    }
    ctx.beginPath(); ctx.arc(p[0], p[1], 10, 0, 2 * Math.PI);
    ctx.fillStyle = fresh ? k.color : (gone ? "rgba(200,200,200,0.6)" : "#bbb"); ctx.fill();
    ctx.lineWidth = fresh ? 2 : 3; ctx.strokeStyle = fresh ? "#000" : k.color;
    if (!fresh) ctx.setLineDash([3, 3]);
    ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = "#000"; ctx.font = "bold 13px sans-serif"; ctx.textAlign = "center"; ctx.fillText(String(k.id), p[0], p[1] + 5);
    ctx.textAlign = "left";
    if (!fresh) { ctx.font = "bold 11px sans-serif"; ctx.fillStyle = "#444"; ctx.fillText(ageText(k.gps.fix_age_s), p[0] + 13, p[1] - 8); }
  });
  // scale bar + north
  const step = niceStep(80 / view.scale);
  const len = step * view.scale;
  ctx.fillStyle = "rgba(255,255,255,.85)"; ctx.fillRect(6, H - 30, len + 70, 24);
  ctx.strokeStyle = "#000"; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(12, H - 12); ctx.lineTo(12 + len, H - 12); ctx.stroke();
  ctx.fillStyle = "#000"; ctx.font = "bold 12px sans-serif"; ctx.fillText(`${step} m`, 18 + len, H - 8);
  ctx.font = "bold 14px sans-serif"; ctx.fillText("N ↑", W - 34, H - 10);
  if (note) note.textContent = `origin: ${o.source === "base" ? "base GPS" : o.source === "kart" ? "first kart fix" : "configured"}`
    + (M.image ? (M.attribution ? ` | imagery: ${M.attribution}` : " | imagery loaded") : " | no imagery");
}

function setupMapInput() {
  const cv = $("map");
  if (!cv) return;
  let drag = null;
  cv.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, cx: view.cx, cy: view.cy }; cv.setPointerCapture(e.pointerId); });
  cv.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) view.fit = false;
    if (!view.fit) { view.cx = drag.cx - dx / view.scale; view.cy = drag.cy + dy / view.scale; if (S) drawMap(); }
  });
  ["pointerup", "pointercancel"].forEach((ev) => cv.addEventListener(ev, () => { drag = null; }));
}

/* ------------------------------------------------------------ kiosk sheet */
let sheetKart = null;
function openSheet(k) {
  sheetKart = k;
  $("sheetInner").innerHTML = `<div class="hdr" style="display:flex;gap:8px;align-items:center">
      <div class="swatch" id="shSw">${k}</div><b style="font-size:22px">Kart ${k}</b><span id="shLink" class="chip"></span>
      <span class="spacer" style="flex:1"></span><button data-close="1">CLOSE</button></div>
    <div class="grid2 sm" id="shBody"></div>${kartButtons(k)}
    <p class="note">Wheel mph is uncalibrated. Motor temperature is unavailable (no sensors wired).</p>`;
  $("sheet").classList.remove("hidden");
  updateSheet();
}
function closeSheet() { sheetKart = null; $("sheet").classList.add("hidden"); }
function updateSheet() {
  if (!sheetKart || !S) return;
  const k = S.fleet.karts[sheetKart - 1];
  const st = k.status || {}, v = k.vesc || {}, d = k.derived || {}, g = k.gps || {}, f = g.last_fix || {};
  $("shSw").style.background = k.color;
  const l = $("shLink"); l.textContent = `${LINK_TEXT[k.link]} ${nul(k.age_s) ? "" : ageText(k.age_s) + " ago"}`; l.className = "chip " + LINK_CLASS[k.link];
  const fi = faultInfo(k);
  const rows = [
    ["Battery", `${fmt(d.battery_pct, 0, "%")} ${fmt(v.volts, 1, " V")}${d.under_load ? " (under load)" : ""}`],
    ["VESC data age", k.vesc_age_s === null ? "never" : ageText(k.vesc_age_s)],
    ["Batt / motor A", `${fmt(v.batt_a, 1)} / ${fmt(v.motor_a, 1)}`],
    ["ERPM / duty", `${fmt(v.erpm)} / ${fmt(v.duty, 2)}`],
    ["Wheel mph*", fmt(nul(d.wheel_mph) ? null : Math.abs(d.wheel_mph), 1)],
    ["GPS mph", g.fix_fresh ? fmt(d.gps_mph, 1) : "--"],
    ["ESC temp", fmt(v.esc_c, 1, " °C")],
    ["Motor temp", "unavailable"],
    ["Fault", `<span class="${fi.cls}">${esc(fi.text)}</span>`],
    ["Mode", modeText(k)],
    ["Current scale", fmt(v.scale, 2)],
    ["Stopped", nul(st.stopped) ? "--" : (st.stopped ? "<b class='faultline'>YES</b>" : "no")],
    ["VESC link", nul(st.vesc_link) ? "--" : (st.vesc_link ? "up" : "<b class='faultline'>DOWN</b>")],
    ["Writes allowed", nul(st.writes) ? "--" : (st.writes ? "yes" : "no")],
    ["GPS", g.fix_now ? `fix, ${fmt(g.sats_now)} sats` : (g.fix_now === false ? "NO FIX" : "--")],
    ["Last fix", f.lat === undefined ? "none" : `${f.lat.toFixed(6)}, ${f.lon.toFixed(6)} (${ageText(g.fix_age_s)})`],
    ["RSSI / SNR", `${fmt(st.rssi, 0, " dBm")} / ${fmt(st.snr, 1, " dB")}`],
    ["Kart hears pit", fmt(st.base_rssi, 0, " dBm")],
    ["Packets / missed", `${k.packets} / ${k.seq_gaps}`],
    ["Uptime / Ah", `${nul(st.uptime_s) ? "--" : ageText(st.uptime_s)} / ${fmt(v.ah, 2)}`],
    ["Last command", outcomeText(k.id) || "none"],
  ];
  $("shBody").innerHTML = rows.map(([a, b]) => `<div><div class="lbl">${a}</div><div>${b}</div></div>`).join("");
  $("sheetInner").querySelectorAll("[data-c]").forEach((btn) => {
    btn.disabled = !S.server.link.connected;
    if (btn.dataset.c === "mode") btn.classList.toggle("sel", st.mode_req === Number(btn.dataset.a));
  });
}

/* ---------------------------------------------------------------- wiring */
function render() {
  updateTop();
  updateCards();
  updateOutcomes();
  updateRace();
  drawMap();
  updateSheet();
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) { if (e.target.id === "sheet") closeSheet(); return; }
  const ds = b.dataset;
  if (ds.c) sendCmd(ds.c, Number(ds.k), ds.a === undefined ? undefined : Number(ds.a));
  else if (ds.allmode !== undefined) sendCmd("mode", 255, Number(ds.allmode));
  else if (ds.close) closeSheet();
  else if (ds.zoom) {
    if (ds.zoom === "fit") view.fit = true;
    else { view.fit = false; view.scale *= ds.zoom === "in" ? 1.5 : 1 / 1.5; }
    if (S) drawMap();
  } else if (ds.tab) {
    document.querySelectorAll("[data-tab]").forEach((t) => t.classList.toggle("sel", t === b));
    ["map", "cmds", "race", "log"].forEach((t) => $("tab-" + t).classList.toggle("hidden", t !== ds.tab));
    store.set("gk_tab", ds.tab);
    if (S) render();
    refreshLog();
  } else if (ds.enter) {
    if (!S) return;
    const k = Number(ds.enter);
    const cur = S.fleet.race.entered;
    post("/api/race", { entered: cur.includes(k) ? cur.filter((x) => x !== k) : cur.concat([k]) });
  } else if (ds.racemode !== undefined) post("/api/race", { mode: Number(ds.racemode) });
  else if (b.id === "raceOff") post("/api/race", { active: false }).then((j) => { if (j && j.ok) toast("Race flag off (no commands sent)"); });
  else if (b.id === "fOnline" || b.id === "fEntered") {
    const key = b.id === "fOnline" ? "online" : "entered";
    filters[key] = !filters[key];
    store.set("gk_f_" + key, filters[key]);
    if (S) render();
  }
});

function connect() {
  const es = new EventSource("/events");
  es.onmessage = (ev) => {
    lastMsg = Date.now();
    try { S = JSON.parse(ev.data); } catch (e) { return; }
    $("connBanner").dataset.server = "ok";
    render();
  };
  es.onerror = () => {
    fetch("/api/whoami").then((r) => { if (r.status === 401) location.href = "/login"; }).catch(() => {});
  };
}

function watchdog() {
  if (Date.now() - lastMsg > 3000) {
    const cb = $("connBanner");
    cb.textContent = lastMsg ? "PIT SERVER NOT RESPONDING: everything on screen is frozen" : "Connecting to pit server...";
    cb.classList.remove("hidden");
  }
}

buildCards();
setupMapInput();
holdButton($("stopAll"), 1000, () => sendCmd("stop", 255));
holdButton($("startAll"), 1000, () => sendCmd("start", 255));
if (KIOSK) {
  const tab = store.get("gk_tab", "map");
  const btn = document.querySelector(`[data-tab="${tab}"]`);
  if (btn && tab !== "map") btn.click();
}
window.addEventListener("resize", () => { if (S) drawMap(); });
setInterval(watchdog, 1000);
setInterval(refreshLog, 2000);
connect();
