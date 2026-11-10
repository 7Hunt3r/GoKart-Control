# Freeplay multiplayer: sessions, items and kart-to-kart hits (contract, 2026-11-09)

Owner's ask: "single-player and multiplayer freeplay, where you can invite other online karts; in the
mystery boxes you get cached shells etc. that, when you're close enough to another kart, you press on
the display and shell them, put them into slow mode for a few seconds, other stuff like that."

Everything here works WITHOUT the pit: karts already hear each other's LoRa telemetry directly (and via
the repeater), so sessions, invites, proximity and hits ride on the existing 1-2 Hz telemetry. The pit,
when present, only observes (and its STOP / SLOW / race rules still win, as for every game effect).

## 1. Sessions (who plays with whom)

- One byte in every kart's telemetry: `session` = 0 (none) or the HOST kart's id (1-8).
- A kart HOSTS by setting `session` to its own id. It is then "open": every other kart that hears it
  sees an invite. A kart JOINS by setting its `session` to the host's id (allowed while the host has been
  heard in the last 10 s). LEAVE = 0. The host leaving ends the game for everyone (their dashes show it
  and drop to single-player freeplay).
- "In my game" = both karts have the same non-zero `session`. Peers are listed from the fleet table
  (karts heard in the last 10 s with my session).
- Courses stay per kart in v1 (each kart keeps its own freeplay loop and boxes); only the items and hits
  are shared. (Sharing the host's course needs the pit or a bulk channel: later.)

## 2. Items (what a mystery box gives you in multiplayer)

Single-player keeps today's instant rolls (BOOST 45 % / ROCKET 15 % / MUD 25 % / empty 15 %). In a
session the roll is: BOOST 35 % (instant, as now) / SHELL 30 % (held) / LIGHTNING 15 % (held) /
ROCKET 10 % (instant) / empty 10 %.

Owner 2026-11-09 (applies to single-player too): freeplay starts in ECO; a BOOST puts the kart in NORMAL
for 5 s (`boost 5 0.75`: scale 0.75 and, through the firmware's cap lift, NORMAL's 25 mph); a ROCKET (the
item formerly called SUPER, rocket icon) puts it in SPORT for 8 s (`boost 8 1.0`: scale 1.0, uncapped).
Firmware 0.2.5 lifts the cap to the highest mode whose scale the boost reaches (0.2.4 lifted exactly one
mode, so a 1.0 boost in ECO was 140 A at 25 mph). A kart holds ONE item; a new held item replaces it. Held items are
kept on the kart Pi (survive a dash restart within the session, cleared on leave).

| Item | Target | Effect on the target (its own Heltec applies it) |
|---|---|---|
| SHELL | one peer within 25 m (dead-reckoned fleet position), chosen automatically = nearest | timed SLOW 5 mph for 4 s (`slow 5 4`, firmware 0.2.4 timed slow) |
| LIGHTNING | every peer in the session | timed SLOW 5 mph for 3 s |

The effect is the same safety-checked path as mud: never while the target is STOPPED or in a race grid
hold (the hit is still acknowledged so the attacker stops repeating it), never above a pit cap, released
by the firmware timer, START and pit mode commands still clear everything.

## 3. Hits over the air (firmware: GkTelemetry 47 -> 54 bytes; 46/47-byte frames still accepted)

Seven uint8 fields appended after `slow_mph` (offsets 47..53). Airtime at SF7/BW500 goes from 23.1 ms
to 25.7 ms (54 bytes is the end of a LoRa symbol group, so the 7th byte is free); every slot-plan
static_assert in GoKartProtocol.h still holds with margin (2 Hz slot: packet ends 8.3 ms before the next
kart's slot opens, was 10.9). Base, repeater and every Python decoder accept any of the three lengths and
read missing fields as 0; the repeater relays bytes unchanged.

| offset | field | meaning |
|---|---|---|
| 47 | `session` | 0 = not in a freeplay game, else the HOST kart's id (1-8); a host carries its own id |
| 48 | `atk_to` | 0 = no attack pending; 1-8 = target kart; 255 (GK_ADDR_ALL) = everyone in my session |
| 49 | `atk_item` | 1 = shell, 2 = lightning (0 with atk_to 0) |
| 50 | `atk_seq` | 1-255, +1 per attack this kart launches (never 0; wraps 255 -> 1) |
| 51 | `ack_from` | kart id whose hit I last accepted, held for 5 s (0 = none) |
| 52 | `ack_seq` | that hit's `atk_seq` |
| 53 | `score` | hits I have landed in this session (acked attacks; lightning counts each ack), reset on session change, saturates at 255 |

Attacker: `attack` sets atk_to/atk_item/atk_seq and repeats them in every telemetry frame until (a) a
frame from the target shows `ack_from == me && ack_seq == atk_seq` -> result `acked` (score +1), or (b)
5 s pass -> `timeout`. For atk_to 255 it always runs the full 5 s and reports which peers acked (score +1
each). One attack in flight at a time: a new `attack` replaces it (`replaced`). Attacks need a session and,
for a single target, that the target was heard in the last 10 s with my session, else `rejected`.

Target: a frame from kart K with `session == mine (non-zero)`, `atk_to == me or 255`, `atk_seq != 0`
and `atk_seq` different from the last one accepted from K (or the same one older than 60 s) is a new
hit: remember (K, seq), set `ack_from = K, ack_seq = seq` for 5 s, apply the effect and print a `hit`
line. The effect is a timed SLOW through the same code as the USB `slow 5 SECS` (firmware 0.2.4): shell
4 s, lightning 3 s. It is skipped (hit still acknowledged, `applied:false`) while STOPPED, in a grid hold,
with writes disabled or without a verified VESC. Repeater copies are the same frames (deduped by K+seq).
Two hits inside 5 s: the later one takes the ack slot; the earlier attacker times out (its hit still
landed). Frames from karts outside my session are ignored for hits.

Sessions on the kart: `session host` -> session = my id. `session join N` needs kart N heard in the
last 10 s with `session == N` (it is hosting). `session leave` -> 0. A joined kart leaves by itself when
the host is not heard for 10 s (`ended: "host lost"`) or the host's frames no longer carry its own id
(`ended: "host left"`); a host never auto-ends. Peers = karts heard in the last 10 s with my session.
Nothing persists across a Heltec reboot (the kart Pi re-sends host/join from its own state).

## 4. USB lines (kart Pi <-> Heltec, firmware 0.2.5)

Pi -> Heltec (console):
- `session host` | `session join N` | `session leave`
- `attack N shell` (N = 1-8) | `attack all lightning` (also `attack N lightning`, `attack all shell` accepted)
- `help` lists them; `status` gains `"session":H,"hosting":bool,"score":n`

Heltec -> Pi (JSON lines):
- `self` and `fleet` lines (gkTelemetryToJson, also the base's `tele`) gain `"session","atk_to","atk_item",
  "atk_seq","ack_from","ack_seq","score"` (plain integers, 0 when absent from an old frame)
- `{"t":"game","kart":me,"session":H,"hosting":true|false,"peers":[3,5],"hosts":[2],"score":n,
  "attack":{"to":3,"item":"shell","seq":n,"age_ms":1200}|null,"ended":"host lost"|"host left"|null}`
  once per second while session != 0 or any host is heard, plus once immediately on every change
  (join/leave/ended/attack start or end) and once when it all goes back to nothing
- `{"t":"hit","kart":me,"from":K,"item":"shell"|"lightning","seq":n,"secs":4,"applied":true|false,
  "why":"stopped"|"grid hold"|"writes off"|"no vesc"|null}` for every accepted hit
- `{"t":"attack","kart":me,"to":3|255,"item":"shell","seq":n,"result":"sent"|"acked"|"timeout"|
  "replaced"|"rejected"|"done","acked":[3,5],"why":"no session"|"not in your game"|"not heard"|null}`:
  `sent` when launched, then exactly one terminal line (`acked`/`timeout` for one target, `done` with the
  `acked` list for 255, `replaced` when a newer attack superseded it, `rejected` immediately)
- `{"t":"log"}` lines narrate the same events for humans

## 5. Kart Pi and dash (kart/server + static)

- Freeplay must be on (single-player) to host or join; leaving freeplay leaves the session. `/api/state.freeplay`
  gains `multi: {"session": 0|H, "hosting": bool, "peers": [{"id", "dist_m"|null, "in_range": bool, "score",
  "age_s"}], "hosts": [ids], "held": null|{"item": "shell"|"lightning", "since_s"}, "score": n,
  "attack": null|{"to", "item", "seq", "result", "age_s", "acked": [...]}, "last_hit": null|{"from", "item",
  "secs", "applied", "why", "age_s"}, "fw_ok": bool}` (fw_ok = Heltec reports kart-0.2.5+; older firmware
  ignores the session/attack console lines, so the buttons are hidden) and `POST /api/freeplay {"host": true}
  | {"join": N} | {"leave": true} | {"use": true}` (use = fire the held item: `attack N shell` at the nearest
  in-range peer, or `attack all lightning`; 409 when nothing is held / no one in range).
- `multi.events`: the last 12 multiplayer happenings as `{"seq", "age_s", "ev", ...}` (seq increases
  forever like `game.events`, so the page acts on each once): `{"ev":"hit","from":2,"item":"shell","secs":4,
  "applied":true}`, `{"ev":"attack","to":3,"item":"shell","result":"sent"|"acked"|"timeout"|"done"|"rejected",
  "acked":[...]}`, `{"ev":"held","item":"shell"}` (a box gave us an item), `{"ev":"session","session":H,
  "hosting":bool,"ended":...}` (joined/left/ended), `{"ev":"peer","id":3,"joined":true|false}`.
- Distances: each peer's last fix (fleet line) dead-reckoned along its heading at its gps_kph for up to 3 s
  of age against my own latest filtered position; `in_range` = within 25 m with a fix younger than 10 s.
- Items: in a session (`multi.session != 0`) the item-box roll is the multiplayer table (section 2) and a
  held item replaces the previous one; outside a session the single-player table is unchanged. Held item
  and session wish (host/joined host id) are kept in freeplay.json and re-sent to the Heltec after a link
  up or Heltec reboot (the firmware forgets them).
- Session log records: `freeplay_host`, `freeplay_join`, `freeplay_leave`, `freeplay_session` (firmware
  game line changes), `freeplay_item_held`, `freeplay_use`, `freeplay_attack` (terminal result),
  `freeplay_hit` (we were hit), all with `"source": "freeplay"`.
- Dash: with freeplay on, the FREEPLAY chip is tappable: HOST / LEAVE. When a host is heard and I am not
  in a session, an INVITE card: "K2 is hosting freeplay" with a 64 px JOIN button (auto-hides after the host
  stops). In a session the chip reads "FREEPLAY · K2's game · with K3 K5".
- Held item = one big button above the mode strip ("SHELL  ▶ K3  18 m" enabled when a peer is within
  25 m, "SHELL  (no one in range)" disabled otherwise; "LIGHTNING ▶ ALL"). Pressing sends `attack ...`
  and shows SENT / HIT! (on hitack) / NO ANSWER. Being hit: full-screen "HIT by K2!" flash + sound, the
  SLOW banner comes from the firmware's slow_mph as for mud.
- Sounds: reuse the existing sound.js profile (a new "hit" and "shell" cue).

## 6. Pit

Decodes the new fields into tele JSON (`session`, `atk_to/item/seq`, `ack_from/seq`); logs
`game_hit` events ("K2 shelled K3"); kart cards show a small session badge "G2" (host id). No controls.

## 7. Safety and precedence (unchanged rules, restated)

- All effects are timed slows/boosts the firmware already supports; nothing here writes modes, STOP or
  START. The pit's STOP, SLOW and race countdown still override everything.
- A pit game with zones pauses freeplay on that kart, including multiplayer actions.
- Attacks are ignored across sessions and from karts not in my session.
