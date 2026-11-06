/* GoKart game sounds: ONE shared helper for the kart driver page and the pit pages.
   Identical copies live in kart/server/static/sound.js and pit/server/static/sound.js
   (kart/tests/test_core.py checks they match). WebAudio synthesis only: no audio files, no samples.

   GKSound.init({ volume: 0-1, enabled: bool, profile: "kart" | "pit", onchange: fn })
   GKSound.play(kind)   kinds: light, go, grid, roulette, item, empty, boost, super, mud, lap, best, finish, stop, tick
   GKSound.setMuted(b) / .muted (remembered in localStorage "gk_mute"), .unlocked(), .ensure()
   The first touch on the page unlocks audio (browsers block it before that, unless Chromium runs with
   --autoplay-policy=no-user-gesture-required, as both kiosks do). The kart profile is louder and fuller
   (an octave-down layer and a compressor; the kart is outdoors), the pit profile is quieter. */
"use strict";
const GKSound = (() => {
  const S = {
    ctx: null, out: null, noiseBuf: null, volume: 0.8, enabled: true, profile: "pit", onchange: null,
    muted: (() => { try { return localStorage.getItem("gk_mute") === "1"; } catch (e) { return false; } })(),
  };
  function changed() { if (S.onchange) try { S.onchange(); } catch (e) { /* ui only */ } }
  function master() {
    const kart = S.profile === "kart";
    return Math.max(0, Math.min(1, S.volume)) * (kart ? 1.0 : 0.55);
  }
  function ensure() {
    if (!S.enabled) return null;
    try {
      if (!S.ctx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        S.ctx = new AC();
        S.ctx.onstatechange = changed;
        const g = S.ctx.createGain();
        g.gain.value = master();
        if (S.profile === "kart") {            // fuller and louder outdoors: compress so peaks do not clip
          const c = S.ctx.createDynamicsCompressor();
          c.threshold.value = -18; c.knee.value = 8; c.ratio.value = 4; c.attack.value = 0.003; c.release.value = 0.15;
          g.connect(c); c.connect(S.ctx.destination);
        } else g.connect(S.ctx.destination);
        S.out = g;
      }
      if (S.ctx.state === "suspended") S.ctx.resume().catch(() => {});
    } catch (e) { S.ctx = null; }
    return S.ctx;
  }
  function noise() {
    if (!S.noiseBuf) {
      const n = S.ctx.sampleRate, b = S.ctx.createBuffer(1, n, n), d = b.getChannelData(0);
      for (let i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;
      S.noiseBuf = b;
    }
    return S.noiseBuf;
  }
  // one oscillator note: t0 start, dur s, type, f0 -> f1 (exponential), peak vol
  function tone(t0, dur, type, f0, f1, vol, sub) {
    const ctx = S.ctx, o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type;
    o.frequency.setValueAtTime(f0, t0);
    if (f1) o.frequency.exponentialRampToValueAtTime(f1, t0 + dur);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(vol, t0 + Math.min(0.02, dur / 4));
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g); g.connect(S.out);
    o.start(t0); o.stop(t0 + dur + 0.05);
    if (sub !== false && S.profile === "kart" && f0 > 160) tone(t0, dur, "triangle", f0 / 2, f1 ? f1 / 2 : 0, vol * 0.6, false);
  }
  // band-passed noise sweep (whoosh / splash)
  function swish(t0, dur, f0, f1, q, vol) {
    const ctx = S.ctx, src = ctx.createBufferSource(), bp = ctx.createBiquadFilter(), g = ctx.createGain();
    src.buffer = noise(); src.loop = true;
    bp.type = "bandpass"; bp.Q.value = q;
    bp.frequency.setValueAtTime(f0, t0); bp.frequency.exponentialRampToValueAtTime(f1, t0 + dur);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(vol, t0 + dur * 0.6);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    src.connect(bp); bp.connect(g); g.connect(S.out);
    src.start(t0); src.stop(t0 + dur + 0.05);
  }
  const KINDS = {
    // race start: three short mid beeps on the lights, one long higher tone on GO (every kind < 1.5 s)
    light: (t) => tone(t, 0.2, "square", 587, 0, 0.32),
    go: (t) => { tone(t, 1.1, "square", 1175, 0, 0.34); tone(t, 1.1, "sawtooth", 1180, 0, 0.06, false); },
    grid: (t) => tone(t, 0.035, "triangle", 900, 0, 0.07, false),                  // soft idle tick
    tick: (t) => tone(t, 0.03, "square", 1600, 0, 0.06, false),
    // item box: ticks that speed up over ~1.2 s, then a bright two-note chime for the result
    roulette: (t) => { let at = 0, gap = 0.05; while (at < 1.15) { tone(t + at, 0.03, "square", 1500 + at * 500, 0, 0.09, false); at += gap; gap *= 1.11; } },
    item: (t) => { tone(t, 0.13, "square", 1319, 0, 0.22); tone(t + 0.12, 0.38, "square", 1976, 0, 0.22); },
    empty: (t) => { tone(t, 0.18, "triangle", 330, 220, 0.24); tone(t + 0.16, 0.3, "triangle", 247, 165, 0.24); },
    // boost: rising filtered-noise whoosh + sawtooth sweep up
    boost: (t) => { swish(t, 0.75, 350, 4200, 1.4, 0.5); tone(t, 0.7, "sawtooth", 160, 1100, 0.2); tone(t + 0.68, 0.22, "square", 1320, 0, 0.14); },
    // super: short ORIGINAL 6-note jingle (not any game's theme) + sparkle
    super: (t) => {
      [659, 784, 988, 880, 1175, 1568].forEach((f, i) => tone(t + i * 0.09, i === 5 ? 0.4 : 0.11, "square", f, 0, 0.2));
      for (let i = 0; i < 7; i++) tone(t + 0.5 + i * 0.07, 0.08, "sine", 2400 + ((i * 457) % 1300), 0, 0.12, false);
      swish(t + 0.45, 0.6, 3000, 9000, 3, 0.18);
    },
    // mud: low wet "blub" with a pitch drop (two bubbles + a short splash)
    mud: (t) => { tone(t, 0.22, "sine", 220, 70, 0.55); tone(t + 0.2, 0.3, "sine", 180, 55, 0.5); swish(t, 0.35, 900, 250, 2.5, 0.22); },
    lap: (t) => { tone(t, 0.14, "square", 784, 0, 0.22); tone(t + 0.16, 0.42, "square", 1175, 0, 0.22); },
    best: (t) => { [880, 1109, 1319].forEach((f, i) => tone(t + i * 0.14, i === 2 ? 0.5 : 0.16, "square", f, 0, 0.22)); },
    // race finish: an original short fanfare (rising arpeggio, held chord)
    finish: (t) => {
      [523, 659, 784, 1046].forEach((f, i) => tone(t + i * 0.12, 0.13, "square", f, 0, 0.2));
      [784, 1046, 1319].forEach((f) => tone(t + 0.5, 0.85, "square", f, 0, 0.13));
      swish(t + 0.5, 0.7, 2500, 7000, 2, 0.12);
    },
    // STOP from the pit: firm low double tone
    stop: (t) => { tone(t, 0.26, "square", 196, 0, 0.4); tone(t + 0.34, 0.4, "square", 147, 0, 0.42); },
  };
  return {
    KINDS: Object.keys(KINDS),
    init(o) {
      if (o.volume !== undefined && o.volume !== null && !Number.isNaN(Number(o.volume))) S.volume = Number(o.volume);
      if (o.enabled !== undefined) S.enabled = o.enabled !== false;
      if (o.profile) S.profile = o.profile;
      if (o.onchange) S.onchange = o.onchange;
      if (S.out) S.out.gain.value = master();
      changed();
    },
    ensure,
    get muted() { return S.muted; },
    get enabled() { return S.enabled; },
    unlocked() { return !!S.ctx && S.ctx.state === "running"; },
    setMuted(m) {
      S.muted = !!m;
      try { localStorage.setItem("gk_mute", S.muted ? "1" : "0"); } catch (e) { /* per-viewer convenience only */ }
      changed();
    },
    play(kind) {
      if (S.muted || !S.enabled || !KINDS[kind]) return false;
      const ctx = ensure();
      if (!ctx || ctx.state !== "running") return false;
      try { KINDS[kind](ctx.currentTime + 0.01); } catch (e) { return false; }   // sound is a nicety
      return true;
    },
  };
})();
/* the first touch anywhere unlocks audio; every tapped button shrinks to 97 % for 80 ms */
window.addEventListener("pointerdown", (e) => {
  GKSound.ensure();
  const b = e.target.closest && e.target.closest("button, .chip.id");
  if (b) { b.classList.add("tapfx"); setTimeout(() => b.classList.remove("tapfx"), 80); }
}, { capture: true });
