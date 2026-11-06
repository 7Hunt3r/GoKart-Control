/* GoKart pit UI: shared by the operator page (/), the touch kiosk (/kiosk) and the
   phone page (/m, portrait, tap a kart card to expand its commands).
   Vanilla JS, no external assets. State arrives over Server-Sent Events (/events)
   about 4 times a second; every browser shows the same server-side state. */
"use strict";

const KIOSK = document.body.dataset.page === "kiosk";
const MOBILE = document.body.dataset.page === "mobile";
const COMPACT = KIOSK || MOBILE;                    // short chip/volt texts
const LOGIN_URL = MOBILE ? "/login?next=/m" : "/login";
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
    if (r.status === 401) { location.href = LOGIN_URL; return null; }
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
  if (cmd === "mode" || ((cmd === "slow" || cmd === "boost") && !nul(arg))) body.arg = arg;
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
const gridSeen = {}, goFlash = {};   // per kart: was holding on the grid; GO badge until (ms)
const expanded = new Set();   // mobile: kart ids whose card is expanded (per viewer)
function kartButtons(k) {
  return `<div class="kbtns">
    <button data-c="mode" data-k="${k}" data-a="0">ECO</button>
    <button data-c="mode" data-k="${k}" data-a="1">NORMAL</button>
    <button data-c="mode" data-k="${k}" data-a="2">SPORT</button>
    <button class="b-slow" data-c="slow" data-k="${k}" data-a="5">SLOW</button>
    <button class="b-boost" data-c="boost" data-k="${k}" data-a="100">BOOST</button>
    <button class="b-stop" data-c="stop" data-k="${k}">STOP</button>
    <button class="b-start" data-c="start" data-k="${k}">START</button></div>`;
}
function mobileCardHtml(k) {
  return `
      <div class="hdr"><div class="swatch" data-f="sw">${k}</div><span class="chip" data-f="link">--</span>
        <span class="sm" data-f="age"></span><div class="badges" data-f="badges"></div><span class="chev" aria-hidden="true"></span></div>
      <div class="row mrow">
        <span><span class="big val" data-f="batt">--</span> <span class="val mvolts" data-f="volts"></span></span>
        <span class="mmode" data-f="mode"></span></div>
      <div class="row sm"><span class="faultline" data-f="fault"></span><span class="val" data-f="esc"></span><span class="val" data-f="gps"></span></div>
      <div class="row sm"><span class="val" data-f="rssi"></span></div>
      <div class="kexp">
        <div class="row sm"><span class="val" data-f="gpsmph"></span></div>
        <div class="row sm"><span><span class="val" data-f="wheel">--</span><span class="lbl"> mph wheel (uncalibrated)</span></span></div>
        ${kartButtons(k)}
        <div class="lastcmd"><span class="lbl">Last command</span> <span class="outcome" data-f="out"></span></div>
      </div>`;
}
function buildCards() {
  const box = $("cards");
  box.innerHTML = "";
  for (let k = 1; k <= 8; k++) {
    const el = document.createElement("div");
    el.className = "card never";
    if (MOBILE) {
      el.innerHTML = mobileCardHtml(k);
      el.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;      // command buttons are handled globally
        if (expanded.has(k)) expanded.delete(k); else expanded.add(k);
        el.classList.toggle("open", expanded.has(k));
      });
      box.appendChild(el);
      const f = {};
      el.querySelectorAll("[data-f]").forEach((n) => { f[n.dataset.f] = n; });
      cards[k] = { el, f };
      continue;
    }
    el.innerHTML = `
      <div class="hdr"><div class="swatch" data-f="sw">${k}</div><span class="chip" data-f="link">--</span>
        <span class="sm" data-f="age"></span><div class="badges" data-f="badges"></div></div>
      <div class="row bigrow">
        <span><span class="big val" data-f="batt">--</span> <span class="sm val" data-f="volts"></span></span>
        <span class="spd"><span class="big val" data-f="wheel">--</span><span class="unit">mph</span><span class="wtag">wheel</span></span></div>
      <div class="stat"><span class="faultline" data-f="fault"></span><span class="val r" data-f="esc"></span></div>
      <div class="stat"><span data-f="mode"></span><span class="outcome r" data-f="out"></span></div>
      <div class="stat"><span><span class="val" data-f="gpsmph"></span> &middot; <span class="val" data-f="gps"></span></span><span class="val r" data-f="rssi"></span></div>
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
    c.el.className = "card " + k.link + (show ? "" : " hidden") + (expanded.has(k.id) ? " open" : "");
    c.f.sw.style.background = k.color;
    c.f.link.textContent = LINK_TEXT[k.link];
    c.f.link.className = "chip " + LINK_CLASS[k.link];
    c.f.age.textContent = nul(k.age_s) ? "" : ageText(k.age_s) + " ago";
    const old = !k.vesc_fresh && !nul(k.vesc_age_s) ? ` (${ageText(k.vesc_age_s)} old)` : "";
    c.f.batt.textContent = nul(d.battery_pct) ? "--" : Math.round(d.battery_pct) + "%";
    c.f.volts.textContent = fmt(v.volts, 1, "V") + (d.under_load ? (COMPACT ? " load" : " under load") : "") + old;
    c.f.wheel.textContent = nul(d.wheel_mph) ? "--" : Math.abs(d.wheel_mph).toFixed(0);
    c.f.batt.classList.toggle("old", !k.vesc_fresh);
    c.f.wheel.classList.toggle("old", !k.vesc_fresh);
    c.f.gpsmph.textContent = g.fix_fresh && !nul(d.gps_mph) ? `GPS ${d.gps_mph.toFixed(0)} mph` : "GPS --";
    c.f.mode.innerHTML = modeText(k);
    c.f.esc.textContent = "ESC " + fmt(v.esc_c, 0, "°C");
    const fi = faultInfo(k);
    c.f.fault.textContent = fi.text;
    c.f.fault.className = fi.cls;
    c.f.gps.textContent = (g.fix_now === true ? `${fmt(g.sats_now)} sats` : (g.fix_now === false ? "NO FIX" : "fix --"))
      + (g.last_fix && !g.fix_fresh ? ` (last ${ageText(g.fix_age_s)})` : "");
    const loss = (k.loss_pct_1m === null || k.loss_pct_1m === undefined ? "" : ` · loss ${k.loss_pct_1m}%`)
      + (k.via_repeater ? " · via repeater" : "");
    if (MOBILE) {   // rssi/snr = how the pit hears the kart; base_rssi = how the kart hears the pit
      c.f.rssi.textContent = `pit hears ${fmt(st.rssi, 0)}/${fmt(st.snr, 1, " dB")}${loss}`
        + (nul(st.base_rssi) ? "" : ` · kart hears ${fmt(st.base_rssi, 0)}`);
    } else if (KIOSK) {     // short: the detail sheet has SNR and the rest
      c.f.rssi.textContent = `${fmt(st.rssi, 0, " dBm")}` + (nul(k.loss_pct_1m) ? "" : ` · ${k.loss_pct_1m}% loss`) + (k.via_repeater ? " · rep" : "");
    } else {
      c.f.rssi.textContent = `${fmt(st.rssi, 0, " dBm")} / ${fmt(st.snr, 1, " dB")}` + loss
        + (st.base_rssi ? ` · pit↑${fmt(st.base_rssi, 0, "")}` : "");
    }
    c.f.out.innerHTML = outcomeText(k.id) || (MOBILE ? '<span class="note">none this session</span>' : "");
    const b = [];
    if (st.stopped === true) b.push('<span class="badge stopped">STOPPED</span>');
    if (fi.bad) b.push('<span class="badge fault">FAULT</span>');
    const onGrid = k.link === "online" && k.vesc_fresh && v.scale === 0 && st.stopped === false;   // race grid hold
    if (gridSeen[k.id] && !onGrid && k.link === "online") goFlash[k.id] = Date.now() + 2500;
    gridSeen[k.id] = onGrid;
    const ses = session(), fp = ses && ses.finished && ses.finished[String(k.id)];
    if (fp) b.push(`<span class="badge finished">${KIOSK ? "FIN" : "FINISHED"} P${fp}</span>`);   // the flag beats GRID/GO
    else if (onGrid) b.push('<span class="badge grid">GRID</span>');
    else if ((goFlash[k.id] || 0) > Date.now()) b.push('<span class="badge go">GO</span>');
    if (st.slow_mph > 0) b.push(`<span class="badge slow">SLOW ${st.slow_mph} MPH</span>`);
    if (st.vesc_link === false) b.push('<span class="badge unknown">VESC LINK DOWN</span>');
    if (st.writes === false) b.push('<span class="badge grey">WRITES OFF</span>');
    if (entered.includes(k.id)) b.push('<span class="badge entered">ENTERED</span>');
    if (gameTiming() && k.lap && !fp) b.push(`<span class="badge lap">P${k.lap.pos} L${k.lap.laps}${nul(k.lap.best_s) || KIOSK ? "" : " · " + fmtLap(k.lap.best_s)}</span>`);
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
  chip("linkChip", L.connected ? (COMPACT ? "T-Beam OK" : "T-Beam " + L.desc) : "T-BEAM DOWN", L.connected ? "ok" : "bad");
  if (st) {
    const staleStat = (st.age_s || 0) > 5;
    chip("radioChip", (MOBILE ? `radio ${st.radio}` : KIOSK ? `${st.radio} rx ${st.rx}` : `radio ${st.radio} rx ${st.rx} tx ${st.tx}` + (st.rx_bad ? ` bad ${st.rx_bad}` : "")),
      st.radio === "none" ? "bad" : (staleStat ? "stale" : "ok"));
  } else chip("radioChip", "radio: no stat", "stale");
  if (base) chip("baseChip", base.fix ? `base GPS ${fmt(base.sats)} sats` : "base GPS no fix", base.fix ? "ok" : "warn");
  const gcorr = S.fleet.gps_correction;
  if ($("corrChip")) {
    if (!gcorr || !gcorr.marker) chip("corrChip", "GPS corr: no pit marker", "");
    else if (!gcorr.samples || gcorr.age_s === null || gcorr.age_s > 10) chip("corrChip", gcorr.enabled ? (KIOSK ? "corr: wait" : "GPS corr: waiting for base fix") : (KIOSK ? "corr off" : "GPS corr off"), "");
    else chip("corrChip", `GPS corr ${gcorr.magnitude_m.toFixed(1)} m` + (gcorr.applied ? " ON" : (gcorr.enabled ? " (hold)" : " off")), gcorr.applied ? "ok" : (gcorr.magnitude_m >= 25 ? "warn" : ""));
    $("corrChip").onclick = () => { if (gcorr) post("/gps_correction", { enabled: !gcorr.enabled }); };
  }
  const rp = S.fleet.repeater;
  if (!rp) chip("repChip", KIOSK ? "no rep" : "no repeater", "");
  else if (rp.age_s > 20) chip("repChip", `REPEATER LOST ${ageText(rp.age_s)}`, "bad");
  else {
    const batt = rp.batt_v > 0.5 ? ` ${rp.batt_v.toFixed(2)} V` : " USB";
    const ks = rp.karts && rp.karts.length ? ` · K${rp.karts.join(" K")}` : "";
    chip("repChip", (COMPACT ? "rep" : "repeater") + ` ${fmt(rp.rssi, 0)} dBm${batt}${ks}`, rp.batt_v > 0.5 && rp.batt_v < 3.5 ? "warn" : "ok");
  }
  if ($("kartsChip")) {
    const on = S.fleet.karts.filter((k) => k.link === "online").length;
    chip("kartsChip", `karts ${on}/${S.fleet.karts.length}`, on ? (on === S.fleet.karts.length ? "ok" : "warn") : "bad");
  }
  chip("raceChip", race.active ? `RACE ${MODES[race.mode]} (${race.entered.length})` : "race off", race.active ? "info" : "");
  $("clock").textContent = new Date().toLocaleTimeString([], { hour12: false });
  const cb = $("connBanner");
  if (!L.connected) { cb.textContent = "T-BEAM LINK DOWN: telemetry is frozen and commands cannot be sent"; cb.classList.remove("hidden"); }
  else cb.classList.add("hidden");
  ["stopAll", "startAll", "slowAll", "boostAll"].forEach((id) => { if ($(id)) $(id).disabled = !L.connected; });
  const Z = zonesDoc();
  const gc = $("gameChip");
  if (gc) {
    gc.textContent = Z.enabled ? (COMPACT ? "GAME ON" : `GAME ON · ${Z.zones.length} zone${Z.zones.length === 1 ? "" : "s"}`) : "GAME OFF";
    gc.className = "chip gamechip " + (Z.enabled ? "on" : "off");     // a toggle: ON = amber with a dot, OFF = outlined
  }
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
    C.recent.slice(0, KIOSK ? 8 : (MOBILE ? 20 : 12)).map((c) => `<tr><td>${c.id}</td><td>${c.seq ?? "-"}</td>
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
    <div class="lbl" style="margin-top:10px">Race start: grid hold, then GO on the shared clock</div>
    <div class="racestart">
      <div class="racesecs">${[3, 5, 10].map((n) => `<button data-racesecs="${n}">${n} s</button>`).join("")}</div>
      <button id="raceStart" class="hold racestartbtn">START RACE<small>hold 1 s · resets laps</small></button></div>
    <div class="racestart">
      <div class="racesecs racelaps">${[0, 3, 5, 10].map((n) => `<button data-racelaps="${n}">${n ? n : "OPEN"}<small>${n ? "laps" : "untimed"}</small></button>`).join("")}</div>
      <button id="raceStop" class="hold racestopbtn">STOP RACE<small>hold 1 s · final results</small></button></div>
    <p class="note">START RACE sends one "race" command to all karts: each holds at zero power, then all launch
      together when the lights go out. STOP still wins during the hold; START releases a kart early.</p>
    <p class="note">RACE ON sends the selected mode + START to entered karts and STOP to karts not entered,
      with per-kart outcomes in CMDS. RACE OFF only clears the race flag; it sends nothing.
      Entry and filters never remove a kart from telemetry or fleet commands.</p>`;
  const startRace = () => {
    const secs = store.get("gk_racesecs", 5), laps = store.get("gk_racelaps", 0);
    post("/race/start", { secs, laps }).then((j) => { if (j && j.ok) toast(`Race start sent: GO in ${j.secs} s${j.laps ? ", " + j.laps + " laps" : ""} (laps reset)`); });
  };
  holdButton($("raceStop"), 1000, () => post("/race/stop", {}).then((j) => {
    if (j && j.ok) toast(`Race stopped: ${j.sent.filter((x) => x.arg > 100).length} karts placed, finish sent`);
  }));
  // phone: hold 1 s, then a confirm sheet with a big GO button (a pocket tap must never start a race)
  holdButton($("raceStart"), 1000, () => { GKSound.ensure(); if (MOBILE) confirmRace(startRace); else startRace(); });
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
  $("raceStart").disabled = !S.server.link.connected;
  const rs = store.get("gk_racesecs", 5), rl = store.get("gk_racelaps", 0);
  document.querySelectorAll("[data-racesecs]").forEach((b) => b.classList.toggle("sel", Number(b.dataset.racesecs) === rs));
  document.querySelectorAll("[data-racelaps]").forEach((b) => b.classList.toggle("sel", Number(b.dataset.racelaps) === rl));
  const ses = race.session;
  $("raceStop").disabled = !S.server.link.connected || !ses || ses.state !== "running";
}

function confirmRace(go) {
  let c = $("raceConfirm");
  if (!c) {
    c = document.createElement("div"); c.id = "raceConfirm"; c.className = "confirm hidden";
    c.innerHTML = `<div class="cbox"><div class="ctitle" id="rcTitle"></div>
      <p class="note">Every kart holds at zero power, then all launch together on GO. Laps reset.</p>
      <button id="rcGo" class="cgo">GO</button><button id="rcCancel" class="ccancel">CANCEL</button></div>`;
    document.body.appendChild(c);
    c.addEventListener("click", (e) => { if (e.target === c || e.target.id === "rcCancel") c.classList.add("hidden"); });
  }
  const nl = store.get("gk_racelaps", 0);
  $("rcTitle").textContent = `Start race in ${store.get("gk_racesecs", 5)} s?` + (nl ? ` (${nl} laps)` : "");
  $("rcGo").onclick = () => { c.classList.add("hidden"); go(); };
  c.classList.remove("hidden");
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
// Dead reckoning between 1 Hz fixes: advance along the reported heading at the reported
// speed for up to DR_MAX_S after the last fix, and ease the drawn point toward each new fix.
const DR_MAX_S = 2.0;
const dr = new Map();   // kart id -> { fixLat, fixLon, atMs, kph, heading, dispE, dispN }
function drPosition(k, now, en) {
  const f = k.gps && k.gps.last_fix; if (!f) return null;
  let d = dr.get(k.id);
  if (!d || d.fixLat !== f.lat || d.fixLon !== f.lon) {
    const e = en(f.lat, f.lon);
    d = { fixLat: f.lat, fixLon: f.lon, atMs: now, kph: f.gps_kph || 0, heading: f.heading, dispE: d ? d.dispE : e[0], dispN: d ? d.dispN : e[1] };
    dr.set(k.id, d);
  }
  const base = en(f.lat, f.lon);
  const fresh = k.link === "online" && k.gps.fix_fresh;
  let target = base;
  if (fresh && d.kph > 2 && d.heading !== null && d.heading !== undefined) {
    const dt = Math.min(DR_MAX_S, (now - d.atMs) / 1000), v = d.kph / 3.6, r = d.heading * Math.PI / 180;
    target = [base[0] + Math.sin(r) * v * dt, base[1] + Math.cos(r) * v * dt];
  }
  d.dispE += (target[0] - d.dispE) * 0.3; d.dispN += (target[1] - d.dispN) * 0.3;
  return [d.dispE, d.dispN];
}
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
  const en = geo().en;
  const M = S.map || {};
  const zones = zed.on ? zed.draft : zonesDoc().zones;
  const base = S.fleet.base;
  const shown = S.fleet.karts.filter((k) => visible(k) && k.gps && k.gps.last_fix);

  // auto-fit: karts + base (+ imagery bounds only if no kart has a position)
  if (view.fit) {
    const pts = [];
    shown.forEach((k) => { pts.push(en(k.gps.last_fix.lat, k.gps.last_fix.lon)); k.trail.forEach((p) => pts.push(en(p[0], p[1]))); });
    if (base && base.fix && !nul(base.lat)) pts.push(en(base.lat, base.lon));
    (M.course || []).forEach((p) => pts.push(en(p[0], p[1])));
    zonePoints(zones).forEach((p) => pts.push(en(p[0], p[1])));
    const trk = zed.on ? zed.track : zonesDoc().track;
    if (trk && trk.points) trk.points.forEach((p) => pts.push(en(p[0], p[1])));
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
  drawTrack(ctx, zed.on ? zed.track : zonesDoc().track, px, en, !zed.on && !zonesDoc().enabled);
  drawZones(ctx, zones, px, en, !zed.on && !zonesDoc().enabled);
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
    const f = k.gps.last_fix, p = px(drPosition(k, performance.now(), en) || en(f.lat, f.lon));
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
  if (zed.on && zed.lapA) {
    const a = px(en(zed.lapA[0], zed.lapA[1]));
    ctx.fillStyle = "#000"; ctx.beginPath(); ctx.arc(a[0], a[1], 6, 0, 2 * Math.PI); ctx.fill();
    ctx.font = "bold 12px sans-serif"; ctx.fillText("tap the other end", a[0] + 10, a[1] - 8);
  }
  if (note) note.textContent = `origin: ${o.source === "base" ? "base GPS" : o.source === "kart" ? "first kart fix" : "configured"}`
    + (M.image ? (M.attribution ? ` | imagery: ${M.attribution}` : " | imagery loaded") : " | no imagery");
}

function setupMapInput() {
  const cv = $("map");
  if (!cv) return;
  // One finger / mouse: pan. Two fingers: pinch zoom anchored on the midpoint. Wheel: zoom at cursor.
  const ptrs = new Map();
  let drag = null, pinch = null;
  const MIN_SCALE = 0.02, MAX_SCALE = 60;   // px per metre
  const local = (e) => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  const clampScale = (v) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, v));
  // world metres under a canvas point
  const world = (x, y) => [view.cx + (x - cv.clientWidth / 2) / view.scale, view.cy - (y - cv.clientHeight / 2) / view.scale];
  const zoomAt = (x, y, factor) => {
    const [wx, wy] = world(x, y);
    view.scale = clampScale(view.scale * factor);
    view.cx = wx - (x - cv.clientWidth / 2) / view.scale;
    view.cy = wy + (y - cv.clientHeight / 2) / view.scale;
    view.fit = false;
  };
  cv.addEventListener("pointerdown", (e) => {
    cv.setPointerCapture(e.pointerId);
    ptrs.set(e.pointerId, local(e));
    if (ptrs.size === 1) {
      const [x, y] = local(e);
      drag = { x, y, cx: view.cx, cy: view.cy, moved: false }; pinch = null;
      if (zed.on && zed.tool === "select") {          // zone editor: grab a zone to move it
        const hit = zoneAt(world(x, y));
        if (hit) { zed.sel = hit.id; drag.zone = hit; drag.last = world(x, y); if (S) drawMap(); }
      }
    }
    else if (ptrs.size === 2) {
      drag = null;
      const [a, b] = [...ptrs.values()];
      pinch = { d0: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1, scale0: view.scale, anchor: world((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) };
      view.fit = false;
    }
  });
  cv.addEventListener("pointermove", (e) => {
    if (!ptrs.has(e.pointerId)) return;
    ptrs.set(e.pointerId, local(e));
    if (pinch && ptrs.size >= 2) {
      const [a, b] = [...ptrs.values()];
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]) || 1;
      const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
      view.scale = clampScale(pinch.scale0 * d / pinch.d0);
      view.cx = pinch.anchor[0] - (mx - cv.clientWidth / 2) / view.scale;
      view.cy = pinch.anchor[1] + (my - cv.clientHeight / 2) / view.scale;
      if (S) drawMap();
    } else if (drag && ptrs.size === 1) {
      const [x, y] = local(e);
      const dx = x - drag.x, dy = y - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) { drag.moved = true; view.fit = false; }
      if (drag.moved && drag.zone) {
        const w = world(x, y);
        moveZone(drag.zone, w[0] - drag.last[0], w[1] - drag.last[1]);
        drag.last = w; zed.dirty = true; if (S) drawMap();
      } else if (drag.moved) { view.cx = drag.cx - dx / view.scale; view.cy = drag.cy + dy / view.scale; if (S) drawMap(); }
    }
  });
  const release = (e) => {
    if (zed.on && e.type === "pointerup" && drag && !drag.moved && ptrs.size === 1 && ptrs.has(e.pointerId)) {
      const [x, y] = local(e);
      zoneTap(world(x, y));
    }
    ptrs.delete(e.pointerId);
    if (ptrs.size < 2) pinch = null;
    if (ptrs.size === 1) { const [x, y] = [...ptrs.values()][0]; drag = { x, y, cx: view.cx, cy: view.cy, moved: true }; }
    if (ptrs.size === 0) drag = null;
  };
  ["pointerup", "pointercancel", "lostpointercapture"].forEach((ev) => cv.addEventListener(ev, release));
  cv.addEventListener("wheel", (e) => { e.preventDefault(); const [x, y] = local(e); zoomAt(x, y, Math.pow(1.0015, -e.deltaY)); if (S) drawMap(); }, { passive: false });
  cv.addEventListener("dblclick", (e) => { const [x, y] = local(e); zoomAt(x, y, 1.6); if (S) drawMap(); });
}

/* -------------------------------------------------- game zones + laps (map/ZONES.md) */
const ZONE_STYLE = {
  boost: { fill: "rgba(255,140,0,0.08)", stroke: "rgba(184,90,0,0.55)" },   // faint trigger radius; the panel carries the look
  mud: { fill: "rgba(115,72,30,0.42)", stroke: "#4a2c10" },
  item: { fill: "rgba(30,110,230,0.22)", stroke: "#0b4fb3" },
};
const ZONE_DEFAULT_R = { boost: 5, mud: 6, item: 4 };
// zone editor state (operator page only): draft is edited locally until SAVE
const zed = { track: null, on: false, tool: "select", draft: [], sel: null, lapA: null, dirty: false, baseVersion: null, shownSel: undefined };

function zonesDoc() { return (S && S.zones) || { version: 0, enabled: false, zones: [] }; }
function gameTiming() { return !!(S && S.fleet.game && S.fleet.game.timing); }
function fmtLap(s) {
  if (nul(s)) return "--";
  if (s < 60) return s.toFixed(1);
  const m = Math.floor(s / 60), r = s - m * 60;
  return `${m}:${r.toFixed(1).padStart(4, "0")}`;
}
/* local tangent plane around the map origin: en(lat,lon) -> [east m, north m], ll(e,n) -> [lat, lon] */
function geo() {
  const o = S.fleet.origin;
  const cl = Math.cos(o.lat * Math.PI / 180), k = 180 / Math.PI / R_EARTH;
  return {
    en: (lat, lon) => [(lon - o.lon) * Math.PI / 180 * R_EARTH * cl, (lat - o.lat) * Math.PI / 180 * R_EARTH],
    ll: (e, n) => [+(o.lat + n * k).toFixed(7), +(o.lon + e * k / cl).toFixed(7)],
  };
}
function zonePoints(zones) {
  const pts = [];
  (zones || []).forEach((z) => {
    if (z.shape === "circle" && !nul(z.lat)) pts.push([z.lat, z.lon]);
    else if (z.shape === "polygon" && Array.isArray(z.points)) z.points.forEach((p) => pts.push(p));
    else if (z.shape === "line" && z.a && z.b) pts.push(z.a, z.b);
  });
  return pts;
}

/* Mario Kart style boost panel: an amber strip with three chevrons, a fixed minimum size so a
   5 m pad still reads on the kiosk map; the faint circle around it is the real trigger radius. */
function drawBoostPanel(ctx, c, rpx) {
  const w = Math.max(14, Math.min(28, rpx * 1.1)), h = w * 1.5;
  ctx.save();
  ctx.translate(c[0], c[1]);
  ctx.fillStyle = "#ff8c00"; ctx.strokeStyle = "#111"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.rect(-w / 2, -h / 2, w, h); ctx.fill(); ctx.stroke();
  ctx.strokeStyle = "#fff8e0"; ctx.lineWidth = Math.max(2, w / 7); ctx.lineJoin = "miter";
  for (let i = 0; i < 3; i++) {
    const y = -h / 2 + h * (0.32 + i * 0.25);
    ctx.beginPath(); ctx.moveTo(-w * 0.32, y); ctx.lineTo(0, y - w * 0.3); ctx.lineTo(w * 0.32, y); ctx.stroke();
  }
  ctx.restore();
}
/* course centreline (ZONES.md "Track route"): thin dashed line + direction chevrons every ~15 m */
function drawTrack(ctx, trk, px, en, off) {
  if (!trk || !Array.isArray(trk.points) || !trk.points.length) return;
  const ps = trk.points.map((p) => px(en(p[0], p[1])));
  ctx.save();
  if (off) ctx.globalAlpha = 0.4;
  const path = () => { ctx.beginPath(); ps.forEach((q, i) => (i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]))); if (trk.closed && ps.length > 2) ctx.closePath(); };
  path(); ctx.strokeStyle = "rgba(255,255,255,0.85)"; ctx.lineWidth = 4; ctx.stroke();
  path(); ctx.strokeStyle = "#0b4fb3"; ctx.lineWidth = 2; ctx.setLineDash([7, 5]); ctx.stroke(); ctx.setLineDash([]);
  const segs = ps.map((q, i) => [q, ps[i + 1] || (trk.closed && ps.length > 2 ? ps[0] : null)]).filter((sg) => sg[1]);
  const every = Math.max(24, 15 * view.scale);           // px between chevrons (~15 m, never cluttered)
  let next = every / 2, acc = 0;                          // distance along the route of the next chevron
  ctx.strokeStyle = "#0b4fb3"; ctx.lineWidth = 2.5; ctx.lineJoin = "miter";
  for (const [a, b] of segs) {
    const L = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const ux = L ? (b[0] - a[0]) / L : 0, uy = L ? (b[1] - a[1]) / L : 0;
    for (; next <= acc + L; next += every) {
      const d = next - acc, x = a[0] + ux * d, y = a[1] + uy * d, s = 5;
      ctx.beginPath();
      ctx.moveTo(x - ux * s - uy * s, y - uy * s + ux * s); ctx.lineTo(x, y); ctx.lineTo(x - ux * s + uy * s, y - uy * s - ux * s);
      ctx.stroke();
    }
    acc += L;
  }
  if (zed.on) {                                            // editing: show the points, first one ringed
    ps.forEach((q, i) => { ctx.fillStyle = "#0b4fb3"; ctx.beginPath(); ctx.arc(q[0], q[1], i ? 3.5 : 6, 0, 2 * Math.PI); ctx.fill(); });
  }
  ctx.restore();
}
function drawItemBox(ctx, c, g) {
  const h = Math.max(7, g * 0.8);
  ctx.save();
  ctx.translate(c[0], c[1]); ctx.rotate(Math.PI / 4 * 0.25);
  ctx.fillStyle = "#1e6ee6"; ctx.fillRect(-h, -h, 2 * h, 2 * h);
  ctx.lineWidth = Math.max(2, h / 4); ctx.strokeStyle = "#fff"; ctx.strokeRect(-h * 0.8, -h * 0.8, 1.6 * h, 1.6 * h);
  ctx.lineWidth = 1.5; ctx.strokeStyle = "#08306b"; ctx.strokeRect(-h, -h, 2 * h, 2 * h);
  ctx.fillStyle = "#fff"; ctx.font = `900 ${Math.round(h * 1.4)}px sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText("?", 0, h * 0.08);
  ctx.restore();
}
function drawLapline(ctx, a, b, sel) {
  const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
  if (len < 1) return;
  const n = Math.max(4, Math.round(len / 7)), w = len / n, h = 4;
  ctx.save();
  ctx.translate(a[0], a[1]); ctx.rotate(Math.atan2(b[1] - a[1], b[0] - a[0]));
  if (sel) { ctx.strokeStyle = "#0b4fb3"; ctx.lineWidth = 3; ctx.setLineDash([5, 4]); ctx.strokeRect(-4, -h - 4, len + 8, 2 * h + 8); ctx.setLineDash([]); }
  for (let i = 0; i < n; i++) {
    ctx.fillStyle = i % 2 ? "#fff" : "#000"; ctx.fillRect(i * w, -h, w, h);
    ctx.fillStyle = i % 2 ? "#000" : "#fff"; ctx.fillRect(i * w, 0, w, h);
  }
  ctx.strokeStyle = "#000"; ctx.lineWidth = 1; ctx.strokeRect(0, -h, len, 2 * h);
  ctx.restore();
  // flag on end a: pole + 3x2 checkered flag
  ctx.strokeStyle = "#000"; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(a[0], a[1] - 26); ctx.stroke();
  for (let i = 0; i < 3; i++) for (let j = 0; j < 2; j++) {
    ctx.fillStyle = (i + j) % 2 ? "#fff" : "#000"; ctx.fillRect(a[0] + 1 + i * 5, a[1] - 26 + j * 5, 5, 5);
  }
  ctx.strokeRect(a[0] + 1, a[1] - 26, 15, 10);
}
function drawZones(ctx, zones, px, en, off) {
  if (!zones || !zones.length) return;
  ctx.save();
  if (off) ctx.globalAlpha = 0.4;               // game switched off: zones drawn faded
  for (const z of zones) {
    const sel = zed.on && zed.sel === z.id;
    if (z.type === "lapline" && z.shape === "line" && z.a && z.b) {
      drawLapline(ctx, px(en(z.a[0], z.a[1])), px(en(z.b[0], z.b[1])), sel);
      continue;
    }
    const st = ZONE_STYLE[z.type] || { fill: "rgba(150,150,150,0.22)", stroke: "#888" };   // unknown kind: greyed
    let c, rpx;
    ctx.beginPath();
    if (z.shape === "circle" && !nul(z.lat)) {
      c = px(en(z.lat, z.lon)); rpx = Math.max(4, (z.r_m || 0) * view.scale);
      ctx.arc(c[0], c[1], rpx, 0, 2 * Math.PI);
    } else if (z.shape === "polygon" && Array.isArray(z.points) && z.points.length > 2) {
      const ps = z.points.map((p) => px(en(p[0], p[1])));
      ps.forEach((q, i) => (i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1])));
      ctx.closePath();
      c = [ps.reduce((a, q) => a + q[0], 0) / ps.length, ps.reduce((a, q) => a + q[1], 0) / ps.length];
      rpx = Math.min(...ps.map((q) => Math.hypot(q[0] - c[0], q[1] - c[1])));
    } else continue;
    ctx.fillStyle = st.fill; ctx.fill();
    ctx.lineWidth = sel ? 4 : 2; ctx.strokeStyle = sel ? "#0b4fb3" : st.stroke;
    if (!ZONE_STYLE[z.type] || sel) ctx.setLineDash([6, 4]);
    ctx.stroke(); ctx.setLineDash([]);
    const g = Math.min(16, Math.max(6, rpx * 0.5));
    if (z.type === "boost") drawBoostPanel(ctx, c, rpx);
    else if (z.type === "item") drawItemBox(ctx, c, g);
    else if (z.type === "mud") {
      ctx.fillStyle = "#2b1a08";
      [[-0.5, 0.2], [0.1, -0.35], [0.45, 0.3]].forEach(([dx, dy]) => { ctx.beginPath(); ctx.arc(c[0] + dx * g, c[1] + dy * g, Math.max(2, g / 5), 0, 2 * Math.PI); ctx.fill(); });
    }
    if (rpx > 22 || sel) {
      ctx.font = "bold 11px sans-serif"; ctx.textAlign = "center"; ctx.fillStyle = "#000";
      ctx.fillText(z.name || z.type, c[0], c[1] + rpx + 12); ctx.textAlign = "left";
    }
  }
  ctx.restore();
}

/* ---- editor (operator page) ---- */
function zoneAt(w) {
  if (!S || !S.fleet.origin) return null;
  const en = geo().en, tol = 14 / view.scale;
  for (let i = zed.draft.length - 1; i >= 0; i--) {
    const z = zed.draft[i];
    if (z.shape === "circle" && !nul(z.lat)) {
      const c = en(z.lat, z.lon);
      if (Math.hypot(w[0] - c[0], w[1] - c[1]) <= Math.max(z.r_m, tol)) return z;
    } else if (z.shape === "line" && z.a && z.b) {
      const a = en(z.a[0], z.a[1]), b = en(z.b[0], z.b[1]);
      const dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy || 1;
      const t = Math.max(0, Math.min(1, ((w[0] - a[0]) * dx + (w[1] - a[1]) * dy) / L2));
      if (Math.hypot(w[0] - a[0] - t * dx, w[1] - a[1] - t * dy) <= tol) return z;
    } else if (z.shape === "polygon" && Array.isArray(z.points)) {
      const ps = z.points.map((p) => en(p[0], p[1]));
      let inside = false;
      for (let a = 0, b = ps.length - 1; a < ps.length; b = a++) {
        if ((ps[a][1] > w[1]) !== (ps[b][1] > w[1]) && w[0] < (ps[b][0] - ps[a][0]) * (w[1] - ps[a][1]) / (ps[b][1] - ps[a][1]) + ps[a][0]) inside = !inside;
      }
      if (inside) return z;
    }
  }
  return null;
}
function moveZone(z, de, dn) {
  const g = geo();
  const sh = (p) => { const q = g.en(p[0], p[1]); return g.ll(q[0] + de, q[1] + dn); };
  if (z.shape === "circle") { const q = sh([z.lat, z.lon]); z.lat = q[0]; z.lon = q[1]; }
  else if (z.shape === "polygon") z.points = z.points.map(sh);
  else if (z.shape === "line") { z.a = sh(z.a); z.b = sh(z.b); }
}
function newZoneId(prefix) {
  const ids = new Set(zed.draft.map((z) => z.id));
  if (prefix === "lap" && !ids.has("lap")) return "lap";
  for (let i = 1; ; i++) if (!ids.has(prefix + i)) return prefix + i;
}
function zoneTap(w) {
  if (!S || !S.fleet.origin) { toast("No map origin yet: wait for a GPS fix", true); return; }
  const g = geo(), here = g.ll(w[0], w[1]);
  const t = zed.tool;
  if (t === "track") {
    if (!zed.track) zed.track = { points: [], closed: false, width_m: 6 };
    if (zed.track.points.length >= 200) { toast("Track is limited to 200 points", true); return; }
    zed.track.points.push(here); zed.dirty = true;
    if (S) { drawMap(); updateZoneBar(); }
    return;
  }
  if (t === "boost" || t === "mud" || t === "item") {
    const z = t === "boost"
      ? { id: newZoneId("b"), type: "boost", name: "Boost pad", shape: "circle", lat: here[0], lon: here[1], r_m: ZONE_DEFAULT_R.boost, scale: 1.0, secs: 5, cooldown_s: 20 }
      : t === "item"
        ? { id: newZoneId("i"), type: "item", name: "Item box", shape: "circle", lat: here[0], lon: here[1], r_m: ZONE_DEFAULT_R.item, cooldown_s: 15 }
        : { id: newZoneId("m"), type: "mud", name: "Mud", shape: "circle", lat: here[0], lon: here[1], r_m: ZONE_DEFAULT_R.mud, scale: 0.5 };
    zed.draft.push(z); zed.sel = z.id; zed.dirty = true;
    setZoneTool("select");
  } else if (t === "lapline") {
    if (!zed.lapA) { zed.lapA = here; }
    else {
      const a = g.en(zed.lapA[0], zed.lapA[1]);
      if (Math.hypot(w[0] - a[0], w[1] - a[1]) < 4) { toast("Lap line must be at least 4 m long", true); return; }
      const old = zed.draft.find((z) => z.type === "lapline");
      const z = { id: old ? old.id : newZoneId("lap"), type: "lapline", name: old ? old.name : "Start / finish", shape: "line",
        a: zed.lapA, b: here, min_lap_s: old && old.min_lap_s ? old.min_lap_s : 20 };
      zed.draft = zed.draft.filter((q) => q.type !== "lapline").concat([z]);   // one lap line per course
      zed.lapA = null; zed.sel = z.id; zed.dirty = true;
      setZoneTool("select");
    }
  } else if (t === "delete") {
    const hit = zoneAt(w);
    if (hit) { zed.draft = zed.draft.filter((z) => z !== hit); if (zed.sel === hit.id) zed.sel = null; zed.dirty = true; }
  } else {
    const hit = zoneAt(w);
    zed.sel = hit ? hit.id : null;
  }
  if (S) { drawMap(); updateZoneBar(); }
}
function setZoneTool(t) { zed.tool = t; zed.lapA = null; updateZoneBar(); }
function zoneEdit(on) {
  if (on) {
    zed.draft = JSON.parse(JSON.stringify(zonesDoc().zones));
    zed.track = zonesDoc().track ? JSON.parse(JSON.stringify(zonesDoc().track)) : null;
    zed.baseVersion = zonesDoc().version;
    zed.sel = null; zed.lapA = null; zed.dirty = false; zed.tool = "select";
    view.fit = false;                        // keep the view still while placing zones
  }
  zed.on = on;
  zed.shownSel = undefined;
  if ($("zoneBar")) $("zoneBar").classList.toggle("hidden", !on);
  if ($("zoneEdit")) $("zoneEdit").classList.toggle("sel", on);
  if (S) { drawMap(); updateZoneBar(); }
}
const ZONE_HINT = {
  boost: "Tap the map to place a boost pad (5 m)", mud: "Tap the map to place a mud patch (6 m)",
  track: "Tap points along the centreline in driving order, then CLOSE LOOP",
  item: "Tap the map to place an item box (4 m)",
  lapline: "Tap one end of the start/finish line, then the other end", delete: "Tap a zone to delete it",
  select: "Tap a zone to select it, drag it to move; drag empty map to pan",
};
function updateZoneBar() {
  if (!zed.on || !$("zoneBar")) return;
  document.querySelectorAll("[data-ztool]").forEach((b) => b.classList.toggle("sel", b.dataset.ztool === zed.tool));
  const z = zed.draft.find((q) => q.id === zed.sel);
  const nm = $("zName"), rr = $("zRadius");
  if (zed.shownSel !== zed.sel) {           // only refresh inputs on selection change, never while typing
    zed.shownSel = zed.sel;
    nm.value = z ? (z.name || "") : "";
    if (z && z.shape === "circle") rr.value = z.r_m;
  }
  nm.disabled = !z;
  rr.disabled = !(z && z.shape === "circle");
  $("zRadiusVal").textContent = z && z.shape === "circle" ? `${Math.round(z.r_m)} m` : "--";
  $("zSel").textContent = z ? `${z.type.toUpperCase()} ${z.id}` : "nothing selected";
  const counts = ["boost", "mud", "lapline", "item"].map((t) => zed.draft.filter((q) => q.type === t).length);
  $("zHint").textContent = zed.tool === "lapline" && zed.lapA ? "Now tap the other end of the line" : ZONE_HINT[zed.tool];
  $("zHint").dataset.tool = zed.tool;
  $("zCounts").textContent = `${counts[0]} boost · ${counts[1]} mud · ${counts[3]} item · ${counts[2] ? "lap line" : "no lap line"} · `
    + (zed.track && zed.track.points.length ? `track ${zed.track.points.length} pts${zed.track.closed ? " closed" : ""}` : "no track")
    + (zed.dirty ? " · UNSAVED" : "");
  $("zoneBar").classList.toggle("tooltrack", zed.tool === "track");
  $("zoneBar").classList.toggle("hassel", !!z);
}
function saveZones() {
  if (zonesDoc().version !== zed.baseVersion && !confirm("Zones were changed elsewhere since you started editing. Overwrite them?")) return;
  const trk = zed.track && zed.track.points.length >= 2 ? zed.track : null;
  if (trk && trk.closed && trk.points.length < 3) { toast("A closed track needs at least 3 points", true); return; }
  post("/zones", { enabled: zonesDoc().enabled, zones: zed.draft, track: trk }).then((j) => {
    if (j && j.ok) { toast(`Zones saved (version ${j.zones.version}, ${j.zones.zones.length} zones)`); zoneEdit(false); }
  });
}
function setupZoneEditor() {
  if (!$("zoneBar")) return;
  $("zName").addEventListener("input", (e) => {
    const z = zed.draft.find((q) => q.id === zed.sel);
    if (z) { z.name = e.target.value.slice(0, 40); zed.dirty = true; if (S) drawMap(); }
  });
  $("zRadius").addEventListener("input", (e) => {
    const z = zed.draft.find((q) => q.id === zed.sel);
    if (z && z.shape === "circle") { z.r_m = Number(e.target.value); zed.dirty = true; if (S) { drawMap(); updateZoneBar(); } }
  });
}

/* ---- leaderboard ---- */
let lapsBuilt = false;
function gapText(r) {                      // gap to the leader (pitcore Game.board: gap_laps / gap_s)
  if (r.pos === 1) return "LEADER";
  if (r.gap_laps > 0) return `+${r.gap_laps} lap${r.gap_laps > 1 ? "s" : ""}`;
  return nul(r.gap_s) ? "--" : `+${r.gap_s.toFixed(1)}`;
}
function session() { return (S && S.fleet.race && S.fleet.race.session) || null; }
function resultsTable(rows) {
  return `<div class="results"><div class="reshead"><span>FINAL RESULTS</span></div><table class="laps res"><tr><th>P</th><th>Kart</th><th>Laps</th><th>Best</th><th>Time</th></tr>` +
    rows.map((r) => {
      const k = S.fleet.karts[r.kart - 1];
      return `<tr${r.pos === 1 ? ' class="lead"' : ""}><td class="pos">${r.pos}</td><td><span class="lsw" style="background:${k.color}">${r.kart}</span></td>
        <td class="n">${r.laps}</td><td class="n">${fmtLap(r.best_s)}</td><td class="n">${nul(r.total_s) ? "--" : fmtLap(r.total_s)}</td></tr>`;
    }).join("") + `</table><button id="newRace" class="newrace">NEW RACE</button></div>`;
}
function lapsTable(board, compact) {
  const ses = session(), target = ses && ses.state === "running" ? ses.laps_target : 0;
  const best = board.reduce((a, r) => (nul(r.best_s) ? a : Math.min(a, r.best_s)), Infinity);
  return `<table class="laps"><tr><th>P</th><th>Kart</th><th>Laps</th><th>Last</th><th>Best</th><th>Gap</th>${target ? "<th>Left</th>" : ""}${compact ? "" : "<th>Now</th>"}</tr>` +
    board.map((r) => {
      const k = S.fleet.karts[r.kart - 1];
      return `<tr${r.pos === 1 ? ' class="lead"' : ""}><td class="pos">${r.pos}</td><td><span class="lsw" style="background:${k.color}">${r.kart}</span></td>
        <td class="n">${r.laps}</td><td class="n">${fmtLap(r.last_s)}</td><td class="n${r.best_s === best ? " fast" : ""}">${fmtLap(r.best_s)}</td>
        <td class="n gap">${gapText(r)}</td>${target ? `<td class="n left">${ses.finished[String(r.kart)] ? "P" + ses.finished[String(r.kart)] : Math.max(0, target - r.laps)}</td>` : ""}${compact ? "" : `<td class="n">${fmtLap(r.current_s)}</td>`}</tr>`;
    }).join("") + "</table>";
}
function lapsStatus() {
  const G = S.fleet.game || {};
  if (!G.enabled) return "Game is OFF: no lap timing. Zones are ignored by the karts.";
  if (!G.lapline) return "No lap line yet: draw one with ZONES on the operator page.";
  if (!(S.fleet.laps || []).length) return `Waiting for the first crossing (min lap ${G.min_lap_s} s).`;
  return "";
}
function updateLaps() {
  const box = $("laps");
  if (box) {
    if (!lapsBuilt) {
      box.innerHTML = `<div id="lapsBody"></div><div class="lapsfoot">
        <button id="lapsReset" class="hold resetlaps">RESET LAPS<small>hold 1 s</small></button>
        <span class="note">Laps are counted by the pit from telemetry (1-2 fixes/s, about ±0.5 s) and reset when the server restarts.</span></div>`;
      holdButton($("lapsReset"), 1000, () => post("/laps/reset", {}).then((j) => { if (j && j.ok) toast("Laps reset"); }));
      lapsBuilt = true;
    }
    const msg = lapsStatus(), board = S.fleet.laps || [], ses = session();
    const fin = ses && ses.state === "finished" ? ses.results || [] : null;
    const key = JSON.stringify([msg, fin, ses && ses.finished, ses && ses.laps_target, board.map((r) => [r.kart, r.laps, r.last_s, r.best_s, r.gap_laps, r.gap_s, MOBILE ? 0 : Math.floor(r.current_s || 0)])]);
    if (box.dataset.key !== key) {
      box.dataset.key = key;
      $("lapsBody").innerHTML = fin ? resultsTable(fin)       // final results replace the live board until NEW RACE
        : (msg ? `<p class="note lapmsg">${esc(msg)}</p>` : "") + (ses && ses.laps_target ? `<p class="note lapmsg"><b>${ses.laps_target}-lap race</b> running</p>` : "")
          + (board.length ? lapsTable(board, MOBILE) : "");
    }
  }
  // compact overlay on every map: top positions, collapsible per viewer
  const wrap = $("map") && $("map").parentElement;
  if (!wrap) return;
  let ov = $("mapLaps");
  if (!ov) {
    ov = document.createElement("div"); ov.id = "mapLaps"; ov.className = "maplaps hidden";
    ov.innerHTML = '<button id="mapLapsHead" class="mlhead">LAPS</button><div id="mapLapsBody"></div>';
    wrap.appendChild(ov);
  }
  const board = S.fleet.laps || [];
  ov.classList.toggle("hidden", !gameTiming() || !board.length);
  const open = store.get("gk_maplaps", true);
  $("mapLapsHead").textContent = open ? "LAPS ▾" : `LAPS ▸ ${board.length ? "K" + board[0].kart + " leads" : ""}`;
  $("mapLapsBody").classList.toggle("hidden", !open);
  if (open) {
    $("mapLapsBody").innerHTML = board.slice(0, 8).map((r) => {
      const k = S.fleet.karts[r.kart - 1];
      return `<div class="mlrow"><b>${r.pos}</b><span class="lsw" style="background:${k.color}">${r.kart}</span><span>L${r.laps}</span><span>${fmtLap(r.best_s)}</span></div>`;
    }).join("");
  }
}

/* ------------------------------------------------- sound (static/sound.js, shared with the karts) */
// Pit profile: quieter than the karts. Mute is per browser (localStorage); the first touch unlocks
// audio and "TAP FOR SOUND" shows until then (the pit kiosk Chromium has no autoplay flag).
let sndShown = null;
function sndUi() {
  const b = $("sndBtn"); if (!b) return;
  const key = [GKSound.muted, GKSound.unlocked()].join();
  if (key === sndShown) return;
  sndShown = key;
  // one chip: TAP FOR SOUND (amber) until the first touch unlocks audio, then SOUND / MUTED
  const locked = !GKSound.muted && !GKSound.unlocked();
  b.textContent = GKSound.muted ? "MUTED" : locked ? "TAP FOR SOUND" : "SOUND";
  b.className = "chip sndchip " + (GKSound.muted ? "bad" : locked ? "warn" : "");
}
GKSound.init({ profile: "pit", volume: 0.8, onchange: sndUi });
document.addEventListener("pointerdown", () => setTimeout(sndUi, 50), { capture: true });
if ($("sndBtn")) $("sndBtn").addEventListener("click", () => { GKSound.ensure(); GKSound.setMuted(!GKSound.muted); });

/* ------------------------------------------------- race start countdown (ZONES.md "Race start") */
// GO time comes from the server as race.go_in_s (server clock), turned into a local target once per
// race start so every pit page shows the same lights: a full-width strip at the top of the card area
// (kiosk), above the cards (operator) or at the top of the scroll area (phone). One red light comes on
// each second (same as the karts), a beep on the last three; at GO the lights turn green and the whole
// page flashes green with GO! once.
function raceLights(goInS, n = 5) {         // same as kart/server/static/app.js raceLights
  if (nul(goInS)) return 0;
  if (goInS <= 0) return n;
  return Math.max(0, Math.min(n, n + 1 - Math.ceil(goInS - 1e-9)));
}
const cdn = { key: null, goAt: 0, lit: -1, went: false, sec: null };
function cdBuild() {
  let ov = $("countdown");
  if (ov) return ov;
  ov = document.createElement("div"); ov.id = "countdown"; ov.className = "countdown hidden";
  ov.innerHTML = '<div class="cdlabel">RACE<br>START</div><div class="cdlights">' + [0, 1, 2, 3, 4].map(() => '<span class="cdl"></span>').join("")
    + '</div><div class="cdtext"></div>';
  const host = KIOSK ? document.querySelector(".main") : MOBILE ? document.querySelector(".mmain") : null;
  if (host) host.prepend(ov); else $("cards").before(ov);
  const go = document.createElement("div"); go.id = "goFlash"; go.className = "goflash hidden"; go.innerHTML = "<span>GO!</span>";
  document.body.appendChild(go);
  return ov;
}
function updateCountdown() {
  const race = S.fleet.race || {};
  if (nul(race.go_in_s) || !race.go_wall) { if ($("countdown")) $("countdown").classList.add("hidden"); cdn.key = null; return; }
  if (cdn.key !== race.go_wall) {           // new race start: fix the local target once
    cdn.key = race.go_wall; cdn.goAt = Date.now() + race.go_in_s * 1000; cdn.lit = -1; cdn.sec = null; cdn.went = race.go_in_s <= -2;
  } else {                                   // drift correction only if way off (tab slept etc.)
    const t = Date.now() + race.go_in_s * 1000;
    if (Math.abs(t - cdn.goAt) > 400) cdn.goAt = t;
  }
  tickCountdown();
}
function tickCountdown() {
  if (!cdn.key) return;
  const ov = cdBuild();
  const r = (cdn.goAt - Date.now()) / 1000;
  if (r <= -2) { ov.classList.add("hidden"); return; }
  ov.classList.remove("hidden");
  const lights = ov.querySelectorAll(".cdl"), txt = ov.querySelector(".cdtext");
  if (r > 0) {
    const lit = raceLights(r), sec = Math.ceil(r);
    ov.classList.remove("go");
    lights.forEach((l, i) => l.classList.toggle("on", i < lit));
    txt.textContent = `GO in ${sec}`;
    if (cdn.lit !== -1 && lit > cdn.lit) GKSound.play(lit >= 3 ? "light" : "grid");
    else if (cdn.sec !== null && sec !== cdn.sec && lit === 0) GKSound.play("grid");
    cdn.lit = lit; cdn.sec = sec;
  } else {
    ov.classList.add("go");
    lights.forEach((l) => l.classList.remove("on"));
    txt.textContent = "GO!";
    if (!cdn.went) {
      cdn.went = true; GKSound.play("go");
      const g = $("goFlash"); g.classList.remove("hidden", "run"); void g.offsetWidth; g.classList.add("run");
      setTimeout(() => g.classList.add("hidden"), 1300);
    }
  }
}
setInterval(tickCountdown, 50);

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
  if (gameTiming()) rows.push(["Laps / last / best", k.lap ? `P${k.lap.pos} · ${k.lap.laps} · ${fmtLap(k.lap.last_s)} · ${fmtLap(k.lap.best_s)}` : "no crossing yet"]);
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
  updateLaps();
  drawMap();
  updateSheet();
  updateZoneBar();
  updateCountdown();
  sndUi();
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
    document.querySelectorAll(".tabpane").forEach((p) => p.classList.toggle("hidden", p.id !== "tab-" + ds.tab));
    store.set(TAB_KEY, ds.tab);
    if (S) render();
    refreshLog();
  } else if (ds.enter) {
    if (!S) return;
    const k = Number(ds.enter);
    const cur = S.fleet.race.entered;
    post("/api/race", { entered: cur.includes(k) ? cur.filter((x) => x !== k) : cur.concat([k]) });
  } else if (ds.racemode !== undefined) post("/api/race", { mode: Number(ds.racemode) });
  else if (ds.ztool) setZoneTool(ds.ztool);
  else if (ds.racesecs) { store.set("gk_racesecs", Number(ds.racesecs)); if (S) updateRace(); }
  else if (ds.racelaps !== undefined) { store.set("gk_racelaps", Number(ds.racelaps)); if (S) updateRace(); }
  else if (b.id === "newRace") post("/laps/reset", {}).then((j) => { if (j && j.ok) toast("Results cleared: ready for a new race"); });
  else if (b.id === "zoneEdit") { if (zed.on && zed.dirty && !confirm("Discard unsaved zone changes?")) return; zoneEdit(!zed.on); }
  else if (b.id === "zSave") saveZones();
  else if (b.id === "tUndo" || b.id === "tClose" || b.id === "tClear") {
    if (b.id === "tClear") { if (zed.track && zed.track.points.length && !confirm("Clear the whole track?")) return; zed.track = null; }
    else if (zed.track && b.id === "tUndo") { zed.track.points.pop(); if (zed.track.points.length < 3) zed.track.closed = false; }
    else if (zed.track && b.id === "tClose") {
      if (zed.track.points.length < 3) { toast("Add at least 3 points before closing the loop", true); return; }
      zed.track.closed = !zed.track.closed;
    }
    zed.dirty = true; setZoneTool("track"); if (S) drawMap();
  }
  else if (b.id === "zCancel") { if (zed.dirty && !confirm("Discard unsaved zone changes?")) return; zoneEdit(false); }
  else if (b.id === "gameChip") {
    if (!S) return;
    const on = !zonesDoc().enabled;
    post("/zones/enable", { enabled: on }).then((j) => { if (j && j.ok) toast(on ? "Game ON: karts react to zones" : "Game OFF: karts ignore all zones"); });
  } else if (b.id === "mapLapsHead") { store.set("gk_maplaps", !store.get("gk_maplaps", true)); if (S) updateLaps(); }
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
    fetch("/api/whoami").then((r) => { if (r.status === 401) location.href = LOGIN_URL; }).catch(() => {});
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
holdButton($("stopAll"), 1000, () => { GKSound.play("stop"); sendCmd("stop", 255); });
holdButton($("slowAll"), 1000, () => sendCmd("slow", 255, 5));
holdButton($("startAll"), 1000, () => sendCmd("start", 255));
if ($("boostAll")) holdButton($("boostAll"), 1000, () => sendCmd("boost", 255, 100));
setupZoneEditor();
const TAB_KEY = MOBILE ? "gk_mtab" : "gk_tab";
if (KIOSK || MOBILE) {
  const first = KIOSK ? "map" : "karts";
  const tab = store.get(TAB_KEY, first);
  const btn = document.querySelector(`[data-tab="${tab}"]`);
  if (btn && tab !== first) btn.click();
}
window.addEventListener("resize", () => { if (S) drawMap(); });
setInterval(watchdog, 1000);
setInterval(() => {
  if (!S) return;
  const moving = S.fleet.karts.some((k) => k.link === "online" && k.gps && k.gps.fix_fresh && k.gps.last_fix && (k.gps.last_fix.gps_kph || 0) > 2);
  if (moving) drawMap();
}, 50);
setInterval(refreshLog, 2000);
connect();
