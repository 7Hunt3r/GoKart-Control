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
const MODE_TOP_MPH = [15, 25, 0];   // firmware speed cap per mode (config.h MODES[]), 0 = none; checked by firmware/tests/test_protocol.py
const RESULT_TEXT = { none: "no result", accepted: "accepted", applied: "applied", no_vesc: "NO VESC", rejected: "REJECTED", unknown: "not confirmed" };
const CMD_TEXT = { mode: "mode", stop: "STOP", start: "START", ping: "ping", slow: "SLOW", status: "status" };
const $ = (id) => document.getElementById(id);
let S = null, lastMsg = 0, wasStopped = null;

function nul(v) { return v === null || v === undefined || (typeof v === "number" && Number.isNaN(v)); }
function ageText(a) {
  if (nul(a)) return "never";
  if (a < 60) return a.toFixed(0) + " s";
  if (a < 3600) return Math.floor(a / 60) + " min";
  return Math.floor(a / 3600) + " h";
}
/* DOM writes only when the value changes: the page re-renders 5x a second on a Pi 3 B, and every
   textContent/className/style write invalidates style + layout even when the value is the same */
const domCache = new Map();
function txt(id, v) { v = String(v); const k = "t:" + id; if (domCache.get(k) !== v) { domCache.set(k, v); $(id).textContent = v; } }
function cls(el, v) { if (el.className !== v) el.className = v; }
function sty(el, prop, v) { v = String(v); const k = el.id + ":" + prop; if (domCache.get(k) !== v) { domCache.set(k, v); el.style[prop] = v; } }
function svgCls(el, v) { if (el.getAttribute("class") !== v) el.setAttribute("class", v); }
function setChip(id, text, c) { txt(id, text); cls($(id), "chip " + (c || "")); }
function show(id, on) { $(id).classList.toggle("hidden", !on); }
function cfg(k, d) { return S && S.config && !nul(S.config[k]) ? S.config[k] : d; }
/* pit chip colour from how well the kart hears the pit (base_rssi, dBm): > -90 green, > -105 amber, else red */
/* server.health -> null (nothing to show) or {text, cls}: same rule and words as the pit pages
   (pit/server/static/watch.js healthChip), in the driver page's capitals. Shown only when wrong. */
function healthChip(h) {
  if (!h || !h.available) return null;
  if (h.status === "undervolt") return { text: "UNDERVOLT", cls: "bad" };
  if (h.status === "throttled") return { text: "THROTTLED", cls: "warn" };
  if (h.status === "had_undervolt") return { text: "HAD UNDERVOLT", cls: "stale" };
  return null;
}
function pitSignalClass(rssi) {
  if (nul(rssi)) return "";
  return rssi > -90 ? "ok" : rssi > -105 ? "warn" : "bad";
}

/* ------------------------------------------------------------ dashboard */
function render() {
  const me = S.self, link = S.server.link;
  const fresh = me.link === "online";          // self line within 3 s
  const st = me.status || {};

  show("simBanner", !!S.simulation);
  const dm = S.demo || {};
  $("simBanner").classList.toggle("demo", !!dm.active);
  txt("simText", dm.active
    ? `DEMO (SIMULATION): not the real kart - hold KART 3 s to exit - ${Math.floor(dm.left_s / 60)}:${String(Math.floor(dm.left_s % 60)).padStart(2, "0")} left`
    : "SIMULATION: not a real kart");
  if (!link.connected) {
    txt("connBanner", link.state === "connecting" ? "KART COMPUTER (Heltec USB) NOT FOUND YET - check the USB cable"
      : `KART COMPUTER (Heltec USB) LOST ${nul(link.down_s) ? "" : ageText(link.down_s) + " "}- reconnecting... values below are old`);
    show("connBanner", true);
  } else if (!fresh) { txt("connBanner", me.age_s === null ? "Waiting for the kart computer..." : `KART COMPUTER SILENT ${ageText(me.age_s)} - values below are old`); show("connBanner", true); }
  else show("connBanner", false);

  // STOPPED (authoritative telemetry flag) and SLOW (inferred from command echoes)
  show("stopBanner", st.stopped === true);
  if (st.stopped === true && fresh && wasStopped === false) Snd.play("stop");   // pit STOP: firm low double tone
  wasStopped = fresh ? st.stopped === true : wasStopped;
  txt("stopBanner", fresh ? "STOPPED" : "STOPPED (last known)");
  const slow = S.slow || {};
  show("slowBanner", slow.active === true);
  txt("slowBanner", slow.active ? `SLOW ${slow.mph} MPH` : "SLOW");
  // GRID hold (race start): where STOPPED goes; it is not STOPPED (stopped stays false)
  const race = (S.game && S.game.race) || {};
  show("gridBanner", race.hold === true && st.stopped !== true);
  document.body.classList.toggle("alerting", st.stopped === true || slow.active === true || race.hold === true || !$("connBanner").classList.contains("hidden"));

  // chips
  const id = me.heltec_id !== null ? me.heltec_id : me.configured_id;
  setChip("kartChip", `KART ${nul(id) ? "?" : id}${me.id_mismatch ? "?" : ""}`, "id" + (me.id_mismatch ? " bad" : ""));
  // Heltec USB link down: one chip says so instead of three "?" chips
  const linkUp = link.connected;
  if (!linkUp) setChip("linkChip", link.state === "connecting" ? "USB: CONNECTING" : `USB: RECONNECTING${link.attempts ? " #" + link.attempts : ""}`, "link warn");
  const pl = S.pit_link || {};
  if (pl.state === "ok") setChip("pitChip", `PIT ${pl.rssi}`, pitSignalClass(pl.rssi));
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
  show("linkChip", !linkUp); show("pitChip", linkUp); show("vescChip", linkUp); show("gpsChip", linkUp);
  // Pi power health (vcgencmd get_throttled): only when something is wrong; the row compacts to fit 380 px
  const hc = healthChip(S.server.health);
  if (hc) setChip("healthChip", hc.text, "health " + hc.cls);
  show("healthChip", !!hc);
  cls($("chips"), hc ? "chips crowded" : "chips");

  // speed: GPS ground speed from a fresh fix only; the arc falls back to wheel mph when there is no fix
  const d = me.derived || {};
  const mphEl = $("mph");
  const wheel = fresh && me.vesc_fresh && !nul(d.wheel_mph) ? Math.abs(d.wheel_mph) : null;
  let arcMph = null;
  if (fresh && !nul(d.gps_mph)) { txt("mph", Math.round(d.gps_mph)); svgCls(mphEl, "mphnum"); txt("mphSrc", "GPS"); arcMph = d.gps_mph; }
  else if (wheel !== null) { txt("mph", Math.round(wheel)); svgCls(mphEl, "mphnum"); txt("mphSrc", "WHEEL"); arcMph = wheel; }
  else { txt("mph", !fresh ? "NO DATA" : "NO FIX"); svgCls(mphEl, "mphnum na"); txt("mphSrc", "GPS"); }
  txt("mphNote", fresh && !g.fix_fresh
    ? (wheel !== null ? "NO GPS FIX: wheel speed (uncalibrated)" : !nul(g.fix_age_s) ? `last fix ${ageText(g.fix_age_s)} ago` : "")
    : (wheel !== null ? `wheel ${wheel.toFixed(0)} mph (uncalibrated)` : ""));
  const game = S.game || {}, meter = (game.meter || {});
  drawSpeedo(arcMph, fresh ? meter.kind : "unknown");

  // battery: only from samples with vesc_link true, with its own age
  const v = me.vesc || {};
  const battFresh = me.vesc_fresh && fresh;
  document.querySelector(".batt").classList.toggle("stale", !battFresh);
  txt("battPct", nul(d.battery_pct) ? "--" : Math.round(d.battery_pct) + "%");
  txt("battV", nul(v.volts) ? "-- V" : Number(v.volts).toFixed(1) + " V");
  const bar = $("battBar");
  sty(bar, "width", nul(d.battery_pct) ? "0" : d.battery_pct + "%");
  sty(bar, "background", !battFresh ? "var(--stale)" : d.battery_pct < 20 ? "var(--bad)" : d.battery_pct < 40 ? "var(--warn)" : "var(--ok)");
  txt("battNote", nul(v.volts) ? "no VESC data yet" : !battFresh ? `VESC data ${ageText(me.vesc_age_s)} old` : (d.under_load ? "under load: % reads low" : "20S pack, 60-84 V"));

  // mode requested -> actual, shown as the gear badge
  const mr = st.mode_req, ma = st.mode_actual;
  const gear = $("gear");
  cls(gear, "gear" + (!fresh ? " stale" : "") + (fresh && !nul(mr) && mr !== ma ? " req" : "") + (fresh && ma === 2 ? " g2" : ""));
  txt("gearLetter", nul(ma) ? "?" : MODES[ma][0]);
  txt("modeNow", nul(ma) ? "unknown" : MODES[ma] + (st.mode_confirmed ? " ✓" : ""));
  let mn = "";
  if (!nul(mr) && mr !== ma) mn = `requested ${MODES[mr]}: not applied yet`;
  else if (!nul(ma) && !st.mode_confirmed) mn = st.stopped ? "held by STOP" : "not confirmed by VESC read-back";
  else if (!nul(ma)) mn = (MODE_TOP_MPH[ma] ? `${MODE_TOP_MPH[ma]} mph cap · ` : "no speed cap · ") + "confirmed by VESC read-back";
  else mn = "VESC has not reported a mode";
  txt("modeNote", fresh ? mn : nul(me.age_s) ? "no data from the kart computer yet" : `last known (${ageText(me.age_s)} ago)`);
  document.querySelectorAll(".mbtn").forEach((b) => {
    const m = Number(b.dataset.mode);
    const cap = b.querySelector(".cap");
    if (cap) cap.textContent = MODE_TOP_MPH[m] ? `${MODE_TOP_MPH[m]} mph` : "no cap";
    b.classList.toggle("actual", m === ma && fresh);
    b.classList.toggle("req", m === mr && fresh);
    b.disabled = !link.connected;
  });

  // fault: hidden in beginner mode (driver presentation only; pit + logs keep it)
  const fault = v.fault;
  const showFault = !cfg("beginner_mode", false) && battFresh && !nul(fault) && fault !== 0;
  show("faultBox", showFault);
  if (showFault) txt("faultBox", `VESC FAULT ${fault} ${me.fault_name || ""}`);

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
  txt("cmdNote", cn);

  renderGame(game, fresh, st);
  renderRace((game && game.race) || {});
  sndConfig(); sndUi(); soundTest(S.sound_test);
  show("sndTest", !!dm.active);
  // pulses (BOOST label, USB link chip) flip with each render (5 Hz) instead of a 60 fps CSS animation:
  // on a Pi 3 B every animated frame is a full GPU composite
  const pulsing = $("boostMeter").classList.contains("boost") || !link.connected;
  pulse = pulsing && !pulse;
  document.body.classList.toggle("pulse", pulse);     // no-op (no style recalc) while nothing pulses
}
let pulse = false;

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
let arcD = null;
function drawSpeedo(mph, kind) {
  const f = nul(mph) ? 0 : Math.max(0, Math.min(1, mph / SPEEDO.max));
  const dd = f > 0.004 ? arcPath(SPEEDO.a0, SPEEDO.a0 + (SPEEDO.a1 - SPEEDO.a0) * f, SPEEDO.r) : "";
  if (dd !== arcD) { arcD = dd; $("arcFg").setAttribute("d", dd); }
  cls($("speedo"), "speedo" + (kind === "boost" || kind === "mud" || kind === "stopped" ? " " + kind : ""));
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
  cls(bm, "meter " + kind);
  const sc = fresh && !nul(m.scale) ? Math.max(0, Math.min(1, m.scale)) : null, ms = nul(m.mode_scale) ? null : m.mode_scale;
  const pc = (x) => (x * 100).toFixed(1) + "%";
  sty($("bMark"), "left", ms === null ? "100%" : pc(ms));
  sty($("bMark"), "display", ms === null ? "none" : "");
  const base = sc === null ? 0 : ms === null ? sc : Math.min(sc, ms);
  sty($("bBase"), "width", pc(base));
  const fill = $("bFill");
  if (sc !== null && ms !== null && kind === "boost") { sty(fill, "left", pc(ms)); sty(fill, "width", pc(Math.max(0.03, sc - ms))); }
  else if (sc !== null && ms !== null && kind === "mud") { sty(fill, "left", pc(sc)); sty(fill, "width", pc(ms - sc)); }
  else sty(fill, "width", "0");
  const ov = m.override;
  const left = ov && ov.left_s > 0 ? ` ${Math.ceil(ov.left_s)}s` : "";
  txt("boostLabel", kind === "boost" ? "BOOST!" + left : kind === "mud" ? "MUD" + left
    : kind === "stopped" ? "STOP" : kind === "grid" ? "GRID" : kind === "normal" ? `POWER ${Math.round(sc * 100)}%` : "POWER --");

  // lap panel or the GAME OFF chip
  const on = game.on === true;
  show("lapPanel", on);
  show("gameChip", !on);
  show("lapStrip", on);
  if (!on) txt("gameChip", "GAME OFF");
  else {
    const lap = game.lap || {};
    const finished = !!(game.race && game.race.finished);
    $("lapPanel").classList.toggle("idle", !lap.running);
    txt("lapNum", lap.running ? String(lap.count + 1) : finished ? String(lap.count) : "-");
    txt("lapCur", lap.running ? lapTime(lap.current_s) : finished ? "FINISHED" : "CROSS THE LINE");
    txt("lapLast", lapTime(lap.last_s));
    txt("lapBest", lapTime(lap.best_s));
    txt("lsLast", lapTime(lap.last_s));
    txt("lsBest", lapTime(lap.best_s));
    $("lsBest").classList.toggle("best", !nul(lap.best_s));
  }
  renderPosition((game && game.race) || {});
  gameEvents(game);
}

/* running position (pit beacon): big "P3" badge next to the lap panel, hidden at 0 */
const posUi = { shown: null };
function textOn(hex) {                       // black or white text on the kart colour
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || "");
  if (!m) return "#000";
  const [r, g, b] = m.slice(1).map((x) => parseInt(x, 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.5 ? "#000" : "#fff";
}
function posChange(prev, pos) {              // mirrors kartcore race_position dir
  return !prev ? "new" : !pos ? "gone" : pos < prev ? "up" : pos > prev ? "down" : "same";
}
/* ascending two-note when the position improves, descending when it drops. Kept here, not in
   sound.js, because sound.js is shared byte-for-byte with the pit page. Respects mute/sound config. */
function posSound(d) {
  if (!Snd.enabled || Snd.muted) return;
  const ctx = Snd.ensure();
  if (!ctx || ctx.state !== "running") return;
  try {
    const t = ctx.currentTime + 0.01;
    [[0, 0.11, 784], [0.12, 0.25, d === "up" ? 1175 : 523]].forEach(([dt, dur, f]) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "square"; o.frequency.setValueAtTime(f, t + dt);
      g.gain.setValueAtTime(0.0001, t + dt);
      g.gain.exponentialRampToValueAtTime(0.18, t + dt + 0.015);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dt + dur);
      o.connect(g); g.connect(ctx.destination); o.start(t + dt); o.stop(t + dt + dur + 0.05);
    });
  } catch (e) { /* sound is a nicety */ }
}
function renderPosition(race) {
  // after the flag the finishing place (firmware finish / beacon state 3) wins over the live beacon position
  const badge = $("posBadge"), pos = (race.finished && race.position > 0 ? race.position : Number(race.pos)) || 0;
  if (!pos) { show("posBadge", false); posUi.shown = 0; return; }
  const color = (S.self && S.self.color) || "#3cb44b";
  if (posUi.color !== color) { posUi.color = color; badge.style.setProperty("--kc", color); badge.style.setProperty("--kfg", textOn(color)); }
  show("posBadge", true);                     // sits right of the lap panel / GAME OFF chip (flex row .tl)
  if (posUi.shown !== pos) {
    const d = posChange(posUi.shown, pos);
    txt("posNum", String(pos));
    if (posUi.shown !== null && posUi.shown !== 0 && (d === "up" || d === "down")) {   // not on page load
      badge.classList.remove("pop", "up", "down"); void badge.offsetWidth;
      badge.classList.add("pop", d);
      posSound(d);
    }
    posUi.shown = pos;
  }
}

let lastEvSeq = null;
function gameEvents(game) {
  const evs = game.events || [];
  const top = evs.reduce((a, e) => Math.max(a, e.seq), 0);
  if (lastEvSeq === null || top < lastEvSeq) { lastEvSeq = top; return; }   // first frame / server restarted: no replay
  const fresh = evs.filter((e) => e.seq > lastEvSeq && e.age_s < 3);
  const itemZones = new Set(fresh.filter((e) => e.ev === "item").map((e) => e.zone));
  fresh.forEach((e) => {
    if (e.ev === "item") itemRoulette(e.item);
    else if (e.ev === "boost" && itemZones.has(e.zone)) { /* shown by the roulette */ }
    else if (e.ev === "boost") { flash("BOOST!", "boost"); Snd.play("boost"); }
    else if (e.ev === "mud") { flash("MUD", "mud"); Snd.play("mud"); }
    else if (e.ev === "lap") { flash(`LAP ${e.lap}  ${lapTime(e.lap_s)}${e.best ? "  BEST" : ""}`, "lap", e.best ? "fbest" : ""); Snd.play(e.best ? "best" : "lap"); }
    else if (e.ev === "lap_start") Snd.play("lap");
  });
  lastEvSeq = top;
}
/* item box roulette: ~1.2 s of cycling icons (each pops in with a tick), then the result
   (the command already went out at entry) */
const ITEM_SVG = {
  boost: '<svg viewBox="0 0 100 100"><g fill="none" stroke="#000" stroke-width="20" stroke-linejoin="miter"><path d="M18 50 L50 22 L82 50"/><path d="M18 76 L50 48 L82 76"/></g>'
    + '<g fill="none" stroke="currentColor" stroke-width="11" stroke-linejoin="miter"><path d="M18 50 L50 22 L82 50"/><path d="M18 76 L50 48 L82 76"/></g></svg>',
  super: '<svg viewBox="0 0 100 100"><path d="M50 4 L62 37 L97 38 L69 59 L79 93 L50 73 L21 93 L31 59 L3 38 L38 37 Z" fill="currentColor" stroke="#000" stroke-width="6" stroke-linejoin="round"/></svg>',
  mud: '<svg viewBox="0 0 100 100"><g fill="currentColor" stroke="#000" stroke-width="5" stroke-linejoin="round">'
    + '<path d="M50 28 C64 24 68 40 80 42 C93 45 92 62 80 65 C72 67 76 82 61 79 C51 77 47 88 35 81 C23 74 31 64 19 60 C7 55 12 41 25 42 C35 43 37 31 50 28 Z"/>'
    + '<circle cx="84" cy="22" r="7"/><circle cx="16" cy="84" r="6"/><circle cx="20" cy="24" r="4.5"/></g></svg>',
  empty: '<svg viewBox="0 0 100 100"><g stroke-linecap="square"><path d="M22 22 L78 78 M78 22 L22 78" stroke="#000" stroke-width="24"/>'
    + '<path d="M22 22 L78 78 M78 22 L22 78" stroke="currentColor" stroke-width="13"/></g></svg>',
};
const ITEM_COLOR = { boost: "#ffb000", super: "#ffd400", mud: "#c58a4e", empty: "#d0d0d0" };
const ITEM_NAME = { boost: "BOOST", super: "SUPER!", mud: "MUD", empty: "EMPTY" };
let itemTimer = null;
function itemGlyph(k, color) {
  const g = $("itemGlyph");
  g.innerHTML = ITEM_SVG[k] || "?";
  g.style.color = color || "";
  g.classList.remove("pop"); void g.offsetWidth; g.classList.add("pop");
}
function itemRoulette(result) {
  const box = $("itemBox"), order = ["boost", "mud", "super", "empty"];
  clearInterval(itemTimer); clearTimeout(itemTimer);
  let i = 0;
  const t0 = performance.now();
  box.className = "itembox"; txt("itemName", "");
  Snd.play("roulette");                        // accelerating ticks for the whole spin
  itemTimer = setInterval(() => {
    if (performance.now() - t0 < 1200) {
      const k = order[i++ % order.length];
      itemGlyph(k, ITEM_COLOR[k]);
      return;
    }
    clearInterval(itemTimer);
    box.className = "itembox done r-" + result;
    itemGlyph(result, { mud: "#e2b07a", empty: "#bbb", super: "#ff2a1a", boost: "#fff" }[result] || "#fff");
    txt("itemName", ITEM_NAME[result] || result);
    if (result === "empty") Snd.play("empty");
    else {
      Snd.play("item");                        // bright two-note chime, then the item's own sound
      setTimeout(() => Snd.play(result), 260);
      if (result === "super") flash("\u2605", "star");
      else if (result === "boost") flash("BOOST!", "boost");
      else if (result === "mud") flash("MUD", "mud");
    }
    itemTimer = setTimeout(() => { box.className = "itembox hidden"; }, 1500);
  }, 90);
}

/* race start: lights from the countdown line's GO instant (server: arrival + go_in_ms) */
function raceLights(goInS, n = 5) {         // mirrors kartcore.race_lights
  if (nul(goInS)) return 0;
  if (goInS <= 0) return n;
  return Math.max(0, Math.min(n, n + 1 - Math.ceil(goInS - 1e-9)));
}
const RACE_GO_HOLD_MS = 1000, RACE_FADE_MS = 500;     // GO! full screen, then a 0.5 s fade back to the HUD
const raceUi = { goAt: null, lit: 0, goShownAt: null, active: false };
function renderRace(race) {
  const now = performance.now();
  if (race.hold && !nul(race.go_in_s)) {
    const at = now + race.go_in_s * 1000;
    raceUi.goAt = raceUi.goAt === null || Math.abs(raceUi.goAt - at) > 250 ? at : raceUi.goAt;   // keep it steady
    if (!raceUi.active) { raceUi.active = true; raceUi.lit = 0; raceUi.goShownAt = null; requestAnimationFrame(raceTick); }
  } else if (!race.hold && raceUi.active && raceUi.goShownAt === null) {
    raceUi.goAt = now;                         // released early (START) or GO came first: go now
  } else if (!raceUi.active && !nul(race.go_age_s) && race.go_age_s < 1.0 && race.how !== "stop") {
    raceUi.active = true; raceUi.goAt = now - race.go_age_s * 1000; raceUi.lit = 5; raceUi.goShownAt = null;
    requestAnimationFrame(raceTick);           // page loaded right at GO
  }
  if (race.hold === false && race.how === "stop" && raceUi.active) raceEnd();
  renderFinish(race);
}

/* race finish (pit STOP RACE or the race length reached): checkered takeover for 6 s, then a
   0.5 s fade to the HUD and a small FINISHED chip until the next countdown */
const FINISH_SHOW_MS = 6000;
const finUi = { shown: false, t0: 0, timer: null };
function finishText(race) {
  const p = race.position || 0, fl = race.finish_lap || {};
  return { pos: p > 0 ? "P" + p : "", stats: `${nul(fl.laps) ? "-" : fl.laps} LAP${fl.laps === 1 ? "" : "S"}  ·  BEST <b>${lapTime(fl.best_s)}</b>` };
}
function renderFinish(race) {
  const fin = race.finished === true;
  show("finChip", fin && !race.hold && !(S.game && S.game.on));     // with the game on the lap panel says it
  if (fin) txt("finChip", race.position > 0 ? `FINISHED P${race.position}` : "FINISHED");
  if (!fin) { finUi.shown = false; if (!$("finishOv").classList.contains("hidden")) $("finishOv").className = "finishov hidden"; return; }
  if (finUi.shown || nul(race.finish_age_s) || race.finish_age_s * 1000 > FINISH_SHOW_MS) return;
  finUi.shown = true;                               // once per finish (a reload within 6 s shows the rest)
  const ov = $("finishOv"), t = finishText(race);
  txt("finPos", t.pos);
  $("finStats").innerHTML = t.stats;
  ov.className = "finishov" + (t.pos ? "" : " nopos");
  if (race.finish_age_s < 1) Snd.play("finish");
  const left = FINISH_SHOW_MS - race.finish_age_s * 1000;
  clearTimeout(finUi.timer);
  finUi.timer = setTimeout(() => { ov.classList.add("fade"); finUi.timer = setTimeout(() => { ov.className = "finishov hidden"; }, 500); }, left);
}
function raceEnd() {
  raceUi.active = false; raceUi.goAt = null;
  const ov = $("raceOv");
  ov.className = "raceov hidden";
  document.body.classList.remove("racing");
}
function raceTick() {
  if (!raceUi.active) return;
  const ov = $("raceOv"), now = performance.now(), rem = (raceUi.goAt - now) / 1000;
  ov.classList.remove("hidden");
  document.body.classList.add("racing");
  if (rem > 0) {
    const lit = raceLights(rem);
    // three beeps on the last three lights, a soft idle tick each second before that (grid hold)
    if (lit > raceUi.lit) Snd.play(lit >= 3 ? "light" : "grid");
    else if (lit === 0 && Math.ceil(rem) !== raceUi.sec) Snd.play("grid");
    raceUi.lit = lit; raceUi.sec = Math.ceil(rem);
    ov.querySelectorAll(".lights i").forEach((el, i) => el.classList.toggle("on", i < lit));
    ov.classList.remove("go", "fade"); show("raceGo", false);
    txt("raceMsg", `GO in ${Math.ceil(rem)}`);
  } else {
    if (raceUi.goShownAt === null) { raceUi.goShownAt = now; Snd.play("go"); show("raceGo", true); }
    ov.classList.add("go"); txt("raceMsg", "");
    if (now - raceUi.goShownAt > RACE_GO_HOLD_MS) ov.classList.add("fade");
    if (now - raceUi.goShownAt > RACE_GO_HOLD_MS + RACE_FADE_MS) { raceEnd(); return; }
  }
  requestAnimationFrame(raceTick);
}

let flashTimer = null;
function flash(text, cls, extra) {
  const f = $("flash");
  f.className = "flash hidden"; void f.offsetWidth;     // restart the animation
  txt("flashText", text);
  f.className = "flash f" + cls + (extra ? " " + extra : "");
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { f.className = "flash hidden"; }, cls === "lap" || cls === "star" ? 1300 : 850);
}

/* ---------------------------------------------------------------- sound
   All sounds come from the shared GKSound helper (static/sound.js, same file on the pit). The kart
   kiosk runs Chromium with --autoplay-policy=no-user-gesture-required so audio runs at once; anywhere
   else the first touch unlocks it and "TAP TO ENABLE SOUND" shows until then. Mute is remembered per
   display; "sound": false in config.json silences it; "sound_volume" 0-1 (default 0.8) is the master. */
const Snd = GKSound;
let sndCfgKey = null;
function sndConfig() {
  const key = [cfg("sound", true), cfg("sound_volume", 0.8)].join();
  if (key === sndCfgKey) return;
  sndCfgKey = key;
  GKSound.init({ enabled: cfg("sound", true) !== false, volume: cfg("sound_volume", 0.8), profile: "kart", onchange: () => sndUi() });
}
const SND_ICON = (muted) => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round">'
  + '<path d="M3 9h4l5-4v14l-5-4H3z" fill="currentColor"/>'
  + (muted ? '<path d="M16 9l5 6M21 9l-5 6" stroke-linecap="round"/>' : '<path d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12" stroke-linecap="round"/>')
  + "</svg>";
let sndShown = null;
function sndUi() {
  const on = Snd.enabled;
  const key = [on, Snd.muted, Snd.unlocked()].join();
  if (key === sndShown) return;
  sndShown = key;
  const b = $("sndBtn");
  b.classList.toggle("hidden", !on);
  b.classList.toggle("muted", Snd.muted);
  b.innerHTML = SND_ICON(Snd.muted);
  show("sndHint", on && !Snd.muted && !Snd.unlocked());
}
window.addEventListener("pointerdown", () => setTimeout(sndUi, 50), { capture: true });
$("sndBtn").addEventListener("click", () => { Snd.ensure(); Snd.setMuted(!Snd.muted); });
/* sound test: GET /api/sound-test (TEST SOUND shows in demo mode) -> every open driver page plays
   the three start beeps, GO, then a boost, even when muted (it is an explicit test) */
let soundTestSeq = null;
function soundTest(st) {
  if (!st) return;
  if (soundTestSeq === null || st.seq < soundTestSeq) { soundTestSeq = st.seq; return; }
  if (st.seq === soundTestSeq) return;
  soundTestSeq = st.seq;
  if (nul(st.age_s) || st.age_s > 5) return;
  const was = Snd.muted;
  if (was) Snd.setMuted(false);
  ["light", "light", "light", "go"].forEach((k, i) => setTimeout(() => Snd.play(k), i * 600));
  setTimeout(() => { Snd.play("boost"); if (was) setTimeout(() => Snd.setMuted(true), 1200); }, 3200);
  txt("cmdNote", Snd.unlocked() ? "sound test: playing" : "sound test: audio is locked (tap the screen once)");
}
$("sndTest").addEventListener("click", () => fetch("/api/sound-test").catch(() => {}));

/* ---------------------------------------------- game: zones on the map */
/* zone shapes (true scale) go into the cached map layer; their markers (chevrons, mud dots, "?" box)
   are collected into `marks` and drawn upright on every frame by drawZoneMarks() */
function drawZones(ctx, en, px, marks) {
  const doc = S.map && S.map.zones;
  if (!doc || !Array.isArray(doc.zones)) return;
  const on = !!(S.game && S.game.on), inside = new Set((S.game && S.game.inside) || []);
  ctx.save();
  if (!on) { ctx.globalAlpha = 0.35; ctx.setLineDash([8, 6]); }
  doc.zones.forEach((z) => {
    try {
      if (z.type === "lapline" && z.a && z.b) return lapLine(ctx, px(en(z.a[0], z.a[1])), px(en(z.b[0], z.b[1])), on);
      if (z.type !== "boost" && z.type !== "mud" && z.type !== "item") return;
      const boost = z.type === "boost", item = z.type === "item";
      let c, cw, rad;
      ctx.beginPath();
      if (z.shape === "circle" && !nul(z.lat)) {
        cw = en(z.lat, z.lon); c = px(cw); rad = Math.max(4, z.r_m * view.scale);
        ctx.arc(c[0], c[1], rad, 0, 2 * Math.PI);
      } else if (z.shape === "polygon" && Array.isArray(z.points) && z.points.length >= 3) {
        const ws = z.points.map((p) => en(p[0], p[1])), pts = ws.map(px);
        pts.forEach((q, i) => (i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1])));
        ctx.closePath();
        cw = [ws.reduce((a, q) => a + q[0], 0) / ws.length, ws.reduce((a, q) => a + q[1], 0) / ws.length];
        c = px(cw);
        rad = Math.min(...pts.map((q) => Math.hypot(q[0] - c[0], q[1] - c[1])));
      } else return;
      // true-scale ring/shape, then a marker at least MIN_MARK px across (small 3-5 m pads stay visible)
      ctx.fillStyle = !on ? "rgba(140,140,140,.4)" : item ? "rgba(30,91,216,.3)" : boost ? "rgba(255,176,0,.45)" : "rgba(110,70,30,.55)";
      ctx.fill();
      ctx.lineWidth = inside.has(z.id) ? 6 : 3;
      ctx.strokeStyle = !on ? "#666" : item ? "#1e5bd8" : boost ? "#e08a00" : "#4a2e12";
      ctx.stroke();
      marks.push({ cw, rad, boost, item, on });
    } catch (e) { /* a malformed zone never breaks the map */ }
  });
  ctx.restore();
}
function drawZoneMarks(ctx, marks, px) {
  marks.forEach(({ cw, rad, boost, item, on }) => {
    const c = px(cw), mr = Math.max(MIN_MARK / 2, rad);
    ctx.save();
    if (!on) ctx.globalAlpha = 0.35;
    if (item) itemMark(ctx, c, mr, on);
    else {
      if (rad < MIN_MARK / 2) {
        ctx.beginPath(); ctx.arc(c[0], c[1], mr, 0, 2 * Math.PI);
        ctx.fillStyle = !on ? "#999" : boost ? "#ffb000" : "#6e461e"; ctx.fill();
        ctx.lineWidth = 2; ctx.strokeStyle = "#000"; ctx.stroke();
      }
      if (boost) chevrons(ctx, c, mr, on);
      else mudDots(ctx, c, mr, on);
    }
    ctx.restore();
  });
}
const MIN_MARK = 24;          // px: smallest on-screen marker for a zone (owner: pads are ~5 m)
function itemMark(ctx, c, r, on) {
  const h = Math.max(MIN_MARK / 2, Math.min(26, r * 0.8));
  ctx.save(); ctx.setLineDash([]);
  ctx.translate(c[0], c[1]);
  ctx.fillStyle = on ? "#1e5bd8" : "#888"; ctx.strokeStyle = "#fff"; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.rect(-h, -h, 2 * h, 2 * h); ctx.fill(); ctx.stroke();
  ctx.strokeStyle = "#000"; ctx.lineWidth = 1.5; ctx.strokeRect(-h - 2, -h - 2, 2 * h + 4, 2 * h + 4);
  ctx.fillStyle = "#fff"; ctx.font = `900 ${Math.round(h * 1.5)}px sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText("?", 0, h * 0.08);
  ctx.restore();
}

/* track route band (ZONES.md "Track route"): width_m wide, chevrons every ~15 m in point order,
   the stretch ahead of the kart brighter in heading-up mode */
function trackChevrons(pts, closed, en, spacing) {      // mirrors kartcore.track_chevrons (in local metres)
  const P = pts.map((p) => en(p[0], p[1]));
  if (closed) P.push(P[0]);
  const out = []; let carry = spacing / 2;
  for (let i = 1; i < P.length; i++) {
    const a = P[i - 1], b = P[i], L = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (L <= 0) continue;
    const hd = Math.atan2(b[0] - a[0], b[1] - a[1]);
    let d = carry;
    for (; d <= L; d += spacing) out.push([a[0] + (b[0] - a[0]) * d / L, a[1] + (b[1] - a[1]) * d / L, hd]);
    carry = d - L;
  }
  return out;
}
function trackWidthPx(t) { return Math.max(6, t.width_m * view.scale); }
function drawTrack(ctx, en, px, dim) {          // cached layer: band, edge, direction chevrons
  const t = S.game && S.game.track;
  if (!t || !t.points || t.points.length < 2) return;
  const P = t.points.map((p) => en(p[0], p[1]));
  const w = trackWidthPx(t);
  ctx.save(); ctx.lineJoin = "round"; ctx.lineCap = "round";
  trackPath(ctx, P, t.closed, px); ctx.strokeStyle = "rgba(0,70,90,.55)"; ctx.lineWidth = w + 5; ctx.stroke();
  trackPath(ctx, P, t.closed, px); ctx.strokeStyle = dim ? "rgba(0,229,255,.28)" : "rgba(0,229,255,.5)"; ctx.lineWidth = w; ctx.stroke();
  const cs = Math.max(5, Math.min(15, w * 0.42));     // direction chevrons, a little larger than the band edge
  trackChevrons(t.points, t.closed, en, 15).forEach(([e, n, hd]) => {
    const q = px([e, n]);
    ctx.save(); ctx.translate(q[0], q[1]); ctx.rotate(hd);      // world-aligned: the layer turns with the map
    ctx.beginPath(); ctx.moveTo(-cs, cs * 0.6); ctx.lineTo(0, -cs * 0.5); ctx.lineTo(cs, cs * 0.6);
    ctx.strokeStyle = "rgba(0,40,60,.9)"; ctx.lineWidth = 6; ctx.stroke();
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 3; ctx.stroke();
    ctx.restore();
  });
  ctx.restore();
}
function trackPath(ctx, pts, close, px) {
  ctx.beginPath();
  pts.forEach((p, i) => { const q = px(p); i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); });
  if (close) ctx.closePath();
}
/* heading-up: the ~80 m of route ahead of the kart (nearest point onwards, in point order) brighter */
function drawTrackAhead(ctx, en, px, mePos) {
  const t = S.game && S.game.track;
  if (!t || !t.points || t.points.length < 2 || !mePos) return;
  const P = t.points.map((p) => en(p[0], p[1]));
  const seq = t.closed ? P.concat(P) : P.slice();
  let k = 0, best = Infinity;
  P.forEach((p, i) => { const d = Math.hypot(p[0] - mePos[0], p[1] - mePos[1]); if (d < best) { best = d; k = i; } });
  const ahead = [mePos]; let acc = 0;
  for (let i = k + 1; i < seq.length && acc < 80; i++) { acc += Math.hypot(seq[i][0] - seq[i - 1][0], seq[i][1] - seq[i - 1][1]); ahead.push(seq[i]); }
  if (ahead.length < 2) return;
  ctx.save(); ctx.lineJoin = "round"; ctx.lineCap = "round";
  trackPath(ctx, ahead, false, px); ctx.strokeStyle = "rgba(0,240,255,.75)"; ctx.lineWidth = trackWidthPx(t); ctx.stroke();
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
  txt("cmdNote", `${MODES[m]}: sending...`);
  post("/cmd", { cmd: "mode", arg: m }).then((j) => { if (!j.ok) txt("cmdNote", `${MODES[m]}: ${j.error}`); });
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

/* hidden demo trigger: press-and-hold the KART badge 3 s (config "demo") */
(function setupDemoHold() {
  const chip = $("kartChip");
  let raf = null, t0 = 0;
  const reset = () => { if (raf) cancelAnimationFrame(raf); raf = null; chip.classList.remove("holding"); chip.style.setProperty("--hp", 0); };
  chip.addEventListener("pointerdown", (e) => {
    if (!S || !cfg("demo", true)) return;
    try { chip.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    t0 = performance.now(); chip.classList.add("holding");
    const tick = () => {
      const p = Math.min(1, (performance.now() - t0) / 3000);
      chip.style.setProperty("--hp", p);
      if (p >= 1) { reset(); post("/api/demo", { toggle: true }).then((j) => { if (!j.ok) txt("cmdNote", `demo: ${j.error}`); }); }
      else raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  });
  ["pointerup", "pointercancel", "lostpointercapture"].forEach((ev) => chip.addEventListener(ev, () => { if (raf) reset(); }));
})();

/* ------------------------------------------------------------------ map */
const R_EARTH = 6371000;
const view = { follow: true, pausedUntil: 0, all: false, scale: null, cx: 0, cy: 0, rot: 0 };
let userFollow = true;               // the FOLLOW button; touching the map pauses it
let mapImg = null, mapImgSrc = null, mapImgReady = null;
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
  if (!mapSize.w) mapSize.w = cv.clientWidth, mapSize.h = cv.clientHeight;   // then kept by a ResizeObserver
  const W = mapSize.w, H = mapSize.h;
  if (!W || !H) return;
  if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#33363a"; ctx.fillRect(0, 0, W, H);
  const en = projection();
  const pkg = (S.map && S.map.package) || null;
  if (!en) { txt("mapMsg", "No map yet and no GPS fix"); show("mapMsg", true); txt("mapNote", mapNoteText()); return; }

  const now = performance.now();
  const me = S.self, mf = selfFix(me);
  const meFresh = me.link === "online" && me.gps.fix_fresh;
  const meGood = meFresh && (!mf || !mf.quality || mf.quality === "good");    // poor / outlier: drawn greyed
  const mePos = mf ? drPosition("self", mf, now, en, meFresh) : null;

  // follow resumes follow_resume_s after the last touch
  if (userFollow && !view.follow && Date.now() >= view.pausedUntil) { view.follow = true; view.all = false; view.scale = null; }
  const resumeIn = userFollow && !view.follow ? Math.ceil((view.pausedUntil - Date.now()) / 1000) : 0;
  show("resumeNote", resumeIn > 0);
  if (resumeIn > 0) txt("resumeNote", `follow in ${resumeIn} s`);
  $("followBtn").classList.toggle("sel", userFollow);

  if (view.scale === null || view.all || (view.follow && !mePos)) fitAll(en, pkg, W, H);
  if (view.follow && mePos) {
    view.cx = mePos[0]; view.cy = mePos[1];
    view.scale = view.all ? view.scale : (view.followScale || H / cfg("follow_span_m", 70));
  }
  // heading-up: slew at most 120 deg/s; below 1.5 m/s hold the last heading (Doppler heading is noise)
  const headingUp = view.follow && !view.all && cfg("map_orientation", "heading_up") === "heading_up" && !!mePos;
  const tNow = performance.now(), dtS = view.lastT ? Math.min(0.5, (tNow - view.lastT) / 1000) : 0;
  view.lastT = tNow;
  if (headingUp) {
    const tgt = meFresh && !nul(mf.heading) && (mf.gps_kph || 0) / 3.6 >= 1.5 ? mf.heading : null;
    const prev = view.hdg;
    view.hdg = slewHeading(view.hdg, tgt, dtS, 120);
    view.slewing = !nul(tgt) && !nul(view.hdg) && Math.abs(angleDiffDeg(tgt, view.hdg)) > 0.5;
    view.rot = nul(view.hdg) ? 0 : view.hdg * Math.PI / 180;
    if (prev === undefined) view.slewing = true;
  } else { view.rot = 0; view.hdg = null; view.slewing = false; }
  const cyS = headingUp && view.rot !== 0 ? H * 0.6 : H / 2;     // own kart ~60 % down: more track ahead
  view.cyS = cyS;
  const cr = Math.cos(view.rot), sr = Math.sin(view.rot);
  const px = (p) => {
    const dx = (p[0] - view.cx) * view.scale, dy = (p[1] - view.cy) * view.scale;
    return [W / 2 + dx * cr - dy * sr, cyS - (dx * sr + dy * cr)];
  };

  // imagery, course, track band, zone shapes, lap line: one cached north-up layer, turned with the map
  if (pkg && pkg.image && pkg.bounds && mapImgSrc !== pkg.image) {
    mapImgSrc = pkg.image; mapImgReady = null;
    const img = new Image();
    // into the layer only once decoded (a 3000 x 3044 JPEG takes a while on a Pi 3 B)
    img.onload = () => { (img.decode ? img.decode() : Promise.resolve()).catch(() => {}).then(() => {
      if (mapImg === img) { mapImgReady = img.src; requestDraw(); } }); };
    mapImg = img; img.src = pkg.image;
  }
  const L = ensureLayer(en, pkg, W, H, cyS, dpr, headingUp);
  if (L.cv) {
    const k = view.scale / L.s;                 // != 1 only for a moment while zooming (rebuild pending)
    ctx.save();
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "low";
    ctx.translate(W / 2, cyS); ctx.rotate(-view.rot);
    ctx.drawImage(L.cv, (L.cx - view.cx) * view.scale - L.half * k, -(L.cy - view.cy) * view.scale - L.half * k, 2 * L.half * k, 2 * L.half * k);
    ctx.restore();
  }
  if (headingUp && mePos) { drawTrackAhead(ctx, en, px, mePos); drawChevronsNear(ctx, L, px, mePos); }
  drawZoneMarks(ctx, L.marks, px);
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
    if (hd !== null && (mf.gps_kph || 0) > 1) {        // clear arrow: white outline, kart colour, drop shadow
      ctx.rotate(hd);
      ctx.beginPath(); ctx.moveTo(0, -34); ctx.lineTo(22, 22); ctx.lineTo(0, 11); ctx.lineTo(-22, 22); ctx.closePath();
    } else { ctx.beginPath(); ctx.arc(0, 0, 18, 0, 2 * Math.PI); }
    ctx.save();
    ctx.shadowColor = "rgba(0,0,0,.65)"; ctx.shadowBlur = 10; ctx.shadowOffsetX = 0; ctx.shadowOffsetY = 4;
    ctx.lineJoin = "round"; ctx.lineWidth = 9; ctx.strokeStyle = "#f2f2f2"; ctx.stroke();
    ctx.restore();
    ctx.fillStyle = meGood ? me.color : "#999"; ctx.fill();
    ctx.lineJoin = "round"; ctx.lineWidth = 3; ctx.strokeStyle = "#000"; if (!meGood) ctx.setLineDash([5, 4]); ctx.stroke(); ctx.setLineDash([]);
    ctx.restore();
    if (!meFresh) label(ctx, `last fix ${ageText(me.gps.fix_age_s)}`, p[0] + 24, p[1] - 14);
    else if (!meGood) label(ctx, mf.quality === "outlier" ? "GPS jump ignored" : "weak GPS: zones paused", p[0] + 24, p[1] - 14);
  }
  if (!mf) { txt("mapMsg", me.link === "online" ? "NO GPS FIX YET" : "NO POSITION"); show("mapMsg", true); }
  else if (!meFresh) { txt("mapMsg", me.link === "online" ? "NO GPS FIX" : "POSITION FROZEN"); show("mapMsg", true); }
  else show("mapMsg", false);

  // scale bar + north arrow
  const step = niceStep(90 / view.scale), len = step * view.scale;
  ctx.fillStyle = "rgba(0,0,0,.55)"; ctx.fillRect(6, H - 46, len + 70, 26);
  ctx.strokeStyle = "#e6e6e6"; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(12, H - 28); ctx.lineTo(12 + len, H - 28); ctx.stroke();
  ctx.fillStyle = "#e6e6e6"; ctx.font = "bold 14px sans-serif"; ctx.fillText(`${step} m`, 18 + len, H - 24);
  // north compass: top-right, under the map buttons, 1.5x the old size (r 30)
  const [ccx, ccy] = compassPos(W);
  ctx.save(); ctx.translate(ccx, ccy); ctx.rotate(-view.rot);
  ctx.fillStyle = "rgba(0,0,0,.5)"; ctx.strokeStyle = "rgba(255,255,255,.7)"; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(0, 0, 30, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
  ctx.fillStyle = "#ff3030"; ctx.beginPath(); ctx.moveTo(0, -25); ctx.lineTo(10, 2); ctx.lineTo(-10, 2); ctx.closePath(); ctx.fill();
  ctx.fillStyle = "#e6e6e6"; ctx.beginPath(); ctx.moveTo(0, 13); ctx.lineTo(7, 2); ctx.lineTo(-7, 2); ctx.closePath(); ctx.fill();
  ctx.font = "900 15px sans-serif"; ctx.textAlign = "center"; ctx.fillStyle = "#fff"; ctx.fillText("N", 0, 27); ctx.textAlign = "left";
  ctx.restore();
  txt("mapNote", mapNoteText());
}

/* The static map content is drawn once into an offscreen canvas ("layer"): north-up, at the current
   zoom, centred on the view, big enough (<= 2048 px, the Pi 3 B GPU texture limit) to cover the
   screen at any rotation plus a margin. Each frame only blits it (rotated in heading-up). It is
   rebuilt when its content changes (map image, zones, track, game on/off, zone highlight), when the
   zoom settles on a new level, or when the view centre drifts past the margin (~40 m at the follow
   zoom). Drawing the 3000 x 3044 photo every frame was the Pi's biggest cost. */
const layer = { key: "", cv: null, s: 0, cx: 0, cy: 0, half: 0, marks: [], chev: [], zoomAt: 0 };
let mapGen = 0, lastMapJson = "";                                 // bumped when S.map arrives (zones / package changed)
function layerKey(pkg, headingUp, dpr) {
  const g = S.game || {}, t = g.track;
  return [mapGen, mapImgSrc, mapImgReady, !!g.on, (g.inside || []).join("|"),
    t ? t.points.length + ":" + t.width_m + ":" + t.closed + ":" + t.points[0] + ":" + t.points[t.points.length - 1] : "",
    headingUp, dpr, pkg && pkg.origin ? pkg.origin.lat + "," + pkg.origin.lon : ""].join(";");
}
function ensureLayer(en, pkg, W, H, cyS, dpr, headingUp) {
  const reach = Math.hypot(W / 2, Math.max(cyS, H - cyS));        // view centre -> farthest screen corner
  const half = Math.max(reach + 64, Math.min(1024 / dpr, reach + 420));
  const key = layerKey(pkg, headingUp, dpr), now = performance.now();
  const drift = Math.hypot(view.cx - layer.cx, view.cy - layer.cy) * view.scale;
  const zoomed = Math.abs(view.scale / (layer.s || 1) - 1) > 0.001;
  if (view.scale !== layer.zoomScale) { layer.zoomScale = view.scale; layer.zoomAt = now; }
  if (zoomed && layer.cv && key === layer.key && now - layer.zoomAt < 150) {
    requestDraw();                              // zooming: the old layer, scaled, until the zoom is still 150 ms
    return layer;
  }
  if (layer.cv && key === layer.key && !zoomed && drift <= half - reach && layer.half === half) return layer;
  perf.layers++;
  const size = Math.ceil(2 * half * dpr);
  const cv = layer.cv || document.createElement("canvas");
  if (cv.width !== size || cv.height !== size) { cv.width = size; cv.height = size; }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, 2 * half, 2 * half);
  Object.assign(layer, { cv, key, s: view.scale, cx: view.cx, cy: view.cy, half, marks: [], chev: [] });
  const lpx = (p) => [half + (p[0] - layer.cx) * layer.s, half - (p[1] - layer.cy) * layer.s];
  if (pkg && pkg.image && pkg.bounds && mapImgReady && mapImg && mapImg.naturalWidth) {
    const b = pkg.bounds, p0 = lpx(en(b.north, b.west)), p1 = lpx(en(b.south, b.east));
    const iw = mapImg.naturalWidth, ih = mapImg.naturalHeight, dw = p1[0] - p0[0], dh = p1[1] - p0[1];
    const x0 = Math.max(0, p0[0]), y0 = Math.max(0, p0[1]), x1 = Math.min(2 * half, p1[0]), y1 = Math.min(2 * half, p1[1]);
    if (x1 > x0 && y1 > y0 && dw > 0 && dh > 0) {            // only the part of the photo inside the layer
      ctx.imageSmoothingQuality = "medium";
      ctx.drawImage(mapImg, (x0 - p0[0]) / dw * iw, (y0 - p0[1]) / dh * ih, (x1 - x0) / dw * iw, (y1 - y0) / dh * ih, x0, y0, x1 - x0, y1 - y0);
    }
  }
  if (pkg && pkg.course && pkg.course.length > 1) {
    ctx.strokeStyle = "#000"; ctx.lineWidth = 4; ctx.setLineDash([10, 8]); ctx.beginPath();
    pkg.course.forEach((p, i) => { const q = lpx(en(p[0], p[1])); i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); });
    ctx.stroke(); ctx.setLineDash([]);
  }
  drawTrack(ctx, en, lpx, headingUp);
  const t = S.game && S.game.track;
  if (t && t.points && t.points.length >= 2) layer.chev = trackChevrons(t.points, t.closed, en, 15);
  drawZones(ctx, en, lpx, layer.marks);
  return layer;
}
/* heading-up: the chevrons under the bright "ahead" band are drawn again on top of it */
function drawChevronsNear(ctx, L, px, mePos) {
  const t = S.game && S.game.track;
  if (!t || !L.chev.length) return;
  const cs = Math.max(5, Math.min(15, trackWidthPx(t) * 0.42));
  L.chev.forEach(([e, n, hd]) => {
    if (Math.hypot(e - mePos[0], n - mePos[1]) > 90) return;
    const q = px([e, n]);
    ctx.save(); ctx.translate(q[0], q[1]); ctx.rotate(hd - view.rot);
    ctx.beginPath(); ctx.moveTo(-cs, cs * 0.6); ctx.lineTo(0, -cs * 0.5); ctx.lineTo(cs, cs * 0.6);
    ctx.strokeStyle = "rgba(0,40,60,.9)"; ctx.lineWidth = 6; ctx.stroke();
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 3; ctx.stroke();
    ctx.restore();
  });
}
const mapSize = { w: 0, h: 0 };
try {
  new ResizeObserver((es) => { const r = es[0].contentRect; mapSize.w = r.width; mapSize.h = r.height; ctlPos = null; requestDraw(); }).observe($("map"));
} catch (e) { window.addEventListener("resize", () => { mapSize.w = 0; ctlPos = null; requestDraw(); }); }
let ctlPos = null;                               // compass centre under the map buttons (layout read once)
function compassPos(W) {
  if (!ctlPos) {
    const ctl = document.querySelector(".mapctl");
    ctlPos = ctl ? [ctl.offsetLeft + ctl.offsetWidth / 2, ctl.offsetTop + ctl.offsetHeight + 40] : [W - 41, 240];
  }
  return ctlPos;
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
function angleDiffDeg(a, b) { let d = (a - b) % 360; if (d < 0) d += 360; return d > 180 ? d - 360 : d; }
function slewHeading(cur, target, dt, maxDps) {       // mirrors kartcore.slew_heading
  if (nul(target)) return cur === undefined ? null : cur;
  if (nul(cur)) return ((target % 360) + 360) % 360;
  const d = angleDiffDeg(target, cur), step = maxDps * Math.max(0, dt);
  if (Math.abs(d) <= step) return ((target % 360) + 360) % 360;
  return (((cur + Math.sign(d) * step) % 360) + 360) % 360;
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
  const zd = S.map && S.map.zones, trk = S.game && S.game.track;           // the course: zones + track route
  if (zd && Array.isArray(zd.zones)) zd.zones.forEach((z) => {
    if (z.shape === "circle" && !nul(z.lat)) pts.push(en(z.lat, z.lon));
    else if (z.shape === "polygon" && Array.isArray(z.points)) z.points.forEach((p) => pts.push(en(p[0], p[1])));
    else if (z.shape === "line" && z.a && z.b) pts.push(en(z.a[0], z.a[1]), en(z.b[0], z.b[1]));
  });
  if (trk && Array.isArray(trk.points)) trk.points.forEach((p) => pts.push(en(p[0], p[1])));
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
/* One draw scheduler: SSE frames, touches and dead reckoning only mark the map dirty; it is drawn at
   most map_fps times a second (30 while a finger is on it). Draws run in requestAnimationFrame, which
   the browser pauses by itself while the page is really hidden (no explicit document.hidden check:
   a kiosk that wrongly reported "hidden" would freeze the HUD). */
let drawDirty = false, drawTimer = null, lastDraw = 0, fingers = 0, renderDirty = false;
function requestDraw() { drawDirty = true; kickDraw(); }
function kickDraw() {
  if (drawTimer !== null || !S) return;
  const fps = fingers ? 30 : Math.max(1, Math.min(30, cfg("map_fps", 10)));
  const wait = Math.max(0, 1000 / fps - (performance.now() - lastDraw));
  drawTimer = setTimeout(() => { drawTimer = null; requestAnimationFrame(drawFrame); }, wait);
}
function animating() {
  const moving = (k) => k.link === "online" && k.gps && k.gps.fix_fresh && k.gps.last_fix && (k.gps.last_fix.gps_kph || 0) > 2;
  return moving(S.self) || S.fleet.some(moving) || (userFollow && !view.follow) || view.slewing;
}
function drawFrame() {
  if (!S || !drawDirty) return;
  drawDirty = false;
  lastDraw = performance.now();
  if (renderDirty) { renderDirty = false; const t0 = performance.now(); render(); perf.renders++; perf.renderMs += performance.now() - t0; }
  const t1 = performance.now();
  drawMap();                                      // DOM + map in one frame, not two
  const dt = performance.now() - t1;
  perf.draws++; perf.drawMs += dt; perf.drawMax = Math.max(perf.drawMax, dt);
  if (animating()) requestDraw();                 // dead reckoning / heading slew / resume countdown
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) requestDraw(); });
/* heartbeat to the server every 5 s with frame stats (/api/state "kiosk"); the kiosk watchdog
   (kiosk/start-chromium.sh) restarts Chromium when the page stops sending it */
const perf = { t0: performance.now(), draws: 0, drawMs: 0, drawMax: 0, renders: 0, renderMs: 0, layers: 0 };
setInterval(() => {
  const secs = (performance.now() - perf.t0) / 1000, r = (x) => Math.round(x * 100) / 100;
  post("/api/heartbeat", { secs: r(secs), draws: perf.draws, draw_ms_avg: perf.draws ? r(perf.drawMs / perf.draws) : 0,
    draw_ms_max: r(perf.drawMax), renders: perf.renders, render_ms_avg: perf.renders ? r(perf.renderMs / perf.renders) : 0,
    layer_builds: perf.layers, hidden: document.hidden });
  Object.assign(perf, { t0: performance.now(), draws: 0, drawMs: 0, drawMax: 0, renders: 0, renderMs: 0, layers: 0 });
}, 5000);
$("map").addEventListener("pointerdown", () => { fingers++; });
["pointerup", "pointercancel"].forEach((ev) => $("map").addEventListener(ev, () => { fingers = Math.max(0, fingers - 1); }));

function connect() {
  const es = new EventSource("/events");
  es.onmessage = (ev) => {
    lastMsg = Date.now();
    let m;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    // the server sends the full state first, then only the top-level keys that changed ("_d": 1)
    if (m._d) { if (!S) return; delete m._d; S = Object.assign({}, S, m); } else S = m;
    renderDirty = true;
    requestDraw();
    if ("map" in m) { const mj = JSON.stringify(m.map.zones) + JSON.stringify(m.map.package); if (mj !== lastMapJson) { lastMapJson = mj; mapGen++; } }
  };
}
setInterval(() => {
  if (Date.now() - lastMsg > 3000) {
    const cb = $("connBanner");
    cb.textContent = lastMsg ? "DISPLAY SERVER NOT RESPONDING - screen is frozen" : "Starting display...";
    domCache.delete("t:connBanner");
    cb.classList.remove("hidden");
  }
}, 1000);
sndConfig(); Snd.ensure(); sndUi();
connect();
