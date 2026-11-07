# Contributing

## Project boundaries

Use this project's own repository (canonical clone `~/Projects/50 Day/GoKart Control/`; Eamon's
is `~/GoKart-Control`). Before Git operations, confirm `git rev-parse --show-toplevel` points
there. Remotes use SSH (`git@github.com:colinfost3r/GoKart-Control.git`). Do not initialize
repositories in Projects or category folders.

The connected kart's measured settings define the hardware baseline; explicit owner decisions
define desired behaviour. The prior M5Stack app is historical. Keep verified measurements, owner
reports, assumptions and proposed changes distinct, and never claim a simulated feature is live.

## Branches and pull requests

The owner explicitly requests ongoing commits and pushes so Colin and Eamon share current work:
publish coherent, checked milestones without asking again for each routine push. This
supersedes the earlier per-commit request rule; it does **not** authorize hardware writes or
unreviewed deployments.

1. **Before starting:** `git fetch`, inspect local changes (`git status`) and branch divergence.
   Check existing branches and PRs before overlapping work.
2. **Work on a focused branch** (or worktree) per task. Keep documentation and tests in the same
   change as the code they describe. Update HANDOFF.md when milestone status, open decisions or
   next steps change.
3. **Before pushing:** run the checks below, `git fetch` again and reconcile intervening work.
   Preserve local work before integrating; never discard another collaborator's edits. Never
   force-push a shared branch: a non-fast-forward rejection means fetch, review, integrate and
   recheck. After committing, `git show --stat` to confirm what landed.
4. **Open a PR** for parallel implementation: state scope, touched components and acceptance
   checks, and fill in the [template](.github/pull_request_template.md) (implemented vs simulated
   vs planned, validation, controller impact, handoff). Mark it draft while incomplete. Small
   coordinated documentation updates may go directly to main after checking remote changes.

Current branches: `main` (Colin's baseline), `eamon/firmware-lora-starter` (firmware, pit, kart,
game layer; this is where current work lands), Colin's `firmware/heltec-bench-ota`,
`diagnostics/removable-bench-control` and `integration/command-config` (last merged
`eamon/firmware-lora-starter` at d98c41d, 2026-11-05). Integration back to main is an open item.

## Checks before every push

```sh
tools/run_tests.sh          # all offline suites; must end "ALL PASSED" (exit 0)
git diff --check            # whitespace errors
```

Firmware changes: compile every affected target (`cd firmware && ./build.sh kart`, `kart-uart`,
`base`, `repeater`); a protocol change touches all four, plus the pit and kart parsers and
`SERIAL_PROTOCOL.md`.

Changes that reach the radio chain (firmware, pit server, kart server): after deploying to the
bench, run the live bench on the pit Pi and paste its summary into the PR:

```sh
scp tools/bench/e2e_bench.py pit@gokart-pit.local:/tmp/
ssh pit@gokart-pit.local python3 /tmp/e2e_bench.py --kart 2
```

It sends a `race 3` command (3 s grid hold at zero current on a kart with a linked VESC) even
without `--allow-writes`; wheels off the ground. Details: [tools/README.md](tools/README.md).

VESC inspection-tool changes (`tools/vesc/`):

```sh
python3 -B tools/vesc/decode.py hardware/vesc/baseline-kart/2026-11-05/raw --out /tmp/gokart-baseline-decoded-$(date +%s)
```

The decoder refuses an existing output directory. Tests never open serial ports or contact a
controller.

## How the three kits deploy

| Kit | Target | Fresh install | Update | Kept on the device |
| --- | --- | --- | --- | --- |
| `firmware/` | Heltec V4.3 kart nodes, T-Beam S3 base and repeater | `./build.sh kart upload <port>` (or `kart-uart`, `base`, `repeater`), or `firmware/releases/` with esptool; then the USB console (`id`, `vescmac`, `wifi`) | `./build.sh kart ota gokart-N.local` or `espota.py -i <ip> -p 3232`; base from the pit Pi with esptool | Kart id, VESC pin, Wi-Fi/OTA credentials, GPS baud (board flash, never in source) |
| `pit/` | Raspberry Pi 4 `gokart-pit` | `pit/sdcard` (cloud-init, `flash.sh`) | `pit/deploy.sh [host] [--install]` (rsync to `/opt/gokart-pit`, restart) | `server/config.json` (web password), `logs/`, map imagery, edited `zones.json`, recordings |
| `kart/` | Raspberry Pi 3 B `gokart-kart-N` | `kart/sdcard` with `KART_ID` set | `kart/deploy.sh gokart-kart-N.local [--install]` | `server/config.json`, `logs/`, `cache/` (map package) |

Deploying to the shared pit or a kart is a hardware change: say so in the PR and the handoff.
First test of any write-capable firmware with the drive wheels off the ground; keep a rollback
image (Colin's: `pio run -t upload` from his branch).

## Who does what

No component owners have been formally assigned. As recorded in git history and the handoff:

| Area | Who has done the work | Hardware in their hands |
| --- | --- | --- |
| Requirements, architecture, VESC baseline capture, read-only VESC tools, hardware evidence | Colin | Baseline kart and its VESC; kart 1 Heltec |
| Bench Heltec firmware (`firmware/heltec` on `firmware/heltec-bench-ota`), integration branch | Colin | |
| Fleet firmware (kart, base, repeater), pit station, kart display, game layer, bench tool | Eamon (with Claude Code agents) | Kart 2 Heltec + Pi 3 B, pit Pi 4, both T-Beams |
| Owner decisions | The owner, as recorded in docs/requirements.md and HANDOFF.md | |

Integration responsibility must be stated explicitly in each PR.

## Shared information

| Information | Canonical source |
| --- | --- |
| Owner decisions and accepted behaviour | docs/requirements.md |
| Component ownership, links and failure boundaries | docs/architecture.md |
| Hardware facts and uncertainty | docs/hardware.md and immutable hardware evidence |
| Approved telemetry scope and proposed units/validity | docs/requirements.md, Agreed telemetry fields |
| Radio packet formats and the base serial contract | firmware/libraries/GoKartProtocol/GoKartProtocol.h, firmware/base-tbeam-s3/SERIAL_PROTOCOL.md |
| Game zones, race and track contract | pit/server/map/ZONES.md |
| Planning values mirroring these decisions | config/project.json |
| Current state, open decisions, how to run a session | HANDOFF.md |

Update affected references and HANDOFF in the same change when a decision moves. If two sources
disagree, inspect the decision and evidence rather than choosing whichever changed last; resolve
it in the PR. Do not rewrite immutable hardware captures to match a new design. A short shared
chat summary should name the pushed branch/commit and any remaining local work.

## Evidence and data

Keep baseline captures immutable. New captures go in new timestamped folders with identity,
firmware, source, transport, timestamp, hashes and capture scope. Never relabel a runtime read as
a verified saved-flash backup. Keep raw payloads alongside decoded JSON.

Never commit passwords, Wi-Fi keys, tokens, SSH keys or the pit web password: they live in
`pit/sdcard/secrets.env`, `kart/sdcard/secrets.env`, `*/server/config.json` and board flash, all
outside git. Delete `sdcard/build/` after flashing. SkyFi imagery (`course_skyfi.jpg`,
`course_shop.jpg`) is licensed and local-only; keep map source/date/CRS/attribution with any
imagery that is committed. Session logs and pace-car recordings are runtime data. The
`.gitignore` files enforce this; check `git status --ignored` if in doubt.

Hardware captures require the owner's connected test setup and an explicit port. The capture
tool implements reads only; do not extend its allowlist to writes while inspecting.
Configuration changes, firmware changes and motor tests are separate, documented work.

## Application design

- Keep VESC, GPS/LoRa, fleet state and presentation behind separate interfaces.
- The Heltec owns the VESC connection and supervisory requests; the kart Pi is display only.
  VESC motor control and wired inputs remain local.
- Maintain explicit stale/unknown states and requested vs confirmed commands; nothing missing is
  shown as 0.
- Loss/recovery of accessories or communications must not change driving state. Reconnect
  read-only; never replay profiles or commands; no heartbeat-driven restore.
- Identify the intended VESC per kart (pinned address, UUID); common BLE names are insufficient.
- The pit server arbitrates writes from multiple browsers; browsers never own serial ports.
- Scope control changes to verified hardware and a documented test plan.
- Reuse the pinned 6.00 schema only when the controller signature matches.
