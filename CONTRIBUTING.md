# Contributing

## Project boundaries

Use this project's independent repository in `~/Projects/50 Day/GoKart Control/`.
Before Git operations, confirm `git rev-parse --show-toplevel` points here. Remotes
use SSH. Do not initialize repositories in Projects or category folders.

The connected kart's measured settings define the hardware baseline; explicit
owner decisions define desired behavior. The prior M5Stack app is historical.
Keep verified measurements, owner reports, assumptions and proposed changes distinct.

## Workflow

Read README, HANDOFF and the baseline report first. Work on a focused branch;
keep documentation and tests with the change they explain. Update HANDOFF when
milestone status or next steps change. Never claim a simulated feature is live.

The owner explicitly requests ongoing commits and pushes so Colin and Eamon
share current work. Publish coherent, checked milestones without asking again
for each routine push. This supersedes the earlier per-commit request rule for
this project; it does not authorize hardware writes or unreviewed deployments.

Before starting, fetch the remote and inspect local changes and branch divergence.
Before pushing, fetch again and reconcile any intervening work. Preserve local
work before integrating; do not discard another collaborator's edits. Never
force-push shared branches. A non-fast-forward rejection means fetch, review,
integrate and recheck, not overwrite. Fetching reduces surprises but cannot
prevent concurrent edits; branch review remains necessary.

For parallel implementation, use one focused branch/worktree per task and a PR.
State task scope, touched components and acceptance checks in the PR; mark it
as a draft while incomplete. Check existing branches/PRs before overlapping work.
Small coordinated documentation updates may go directly to main after checking
remote changes. Integration responsibility must be explicit; no component owners
or active subagents have been assigned yet.

### Shared information and ownership

| Information | Canonical source |
| --- | --- |
| Owner decisions and accepted behavior | docs/requirements.md |
| Component ownership, links and failure boundaries | docs/architecture.md |
| Hardware facts and uncertainty | docs/hardware.md and immutable hardware evidence |
| Approved telemetry scope and proposed units/validity | docs/requirements.md, Agreed telemetry fields |
| Planning values mirroring these decisions | config/project.json |
| Completed work, open decisions and next steps | HANDOFF.md |

Update affected references and HANDOFF in the same change when a decision moves.
Keep owner-approved choices distinct from proposals and verified implementation.
If two sources disagree, inspect the decision and evidence rather than choosing
whichever file changed most recently; resolve the discrepancy in the PR. Do not
rewrite immutable hardware captures to match a new design. A brief shared chat
summary should identify the pushed branch/commit and any remaining local work.

## Offline verification

Python 3.9+ standard library is sufficient on macOS/Linux:

```sh
python3 -B -m unittest discover -s tests -v
python3 -B tools/vesc/decode.py hardware/vesc/baseline-kart/2026-11-05/raw --out /tmp/gokart-baseline-decoded
git diff --check
```

The decoder refuses an existing output directory. Choose a new scratch path on
subsequent runs. Tests do not open serial ports or contact any controller.

Hardware captures require the owner's connected test setup and the correct
explicit port. Only read operations are implemented by the capture tool. Do not
extend the allowlist to write/actuate while doing an inspection. Configuration
changes, firmware changes and motor tests are separate work, not diagnostics
silently bundled into a read.

## Evidence and data

Keep baseline captures immutable. New captures go into new timestamped folders
with identity, firmware, source, transport, timestamp, hashes and capture scope.
Never relabel a runtime read as a verified saved-flash backup. Keep raw payloads
alongside decoded JSON so interpretations can be checked later.

Do not store passwords, tokens or local network credentials. If future device
formats include secrets, sanitize exported configuration and note the omission;
do not publish raw secret-bearing packets. Keep map source/date/CRS/attribution
with imagery; use a suitable asset store or Git LFS for large imagery once chosen.

## Application design

- Keep VESC, GPS/LoRa, fleet state and presentation behind separate interfaces.
- Heltec owns the VESC connection and supervisory requests; the kart Pi owns only
  display/interface behavior. VESC motor control and wired inputs remain local.
- Maintain explicit stale/unknown states and requested vs confirmed commands.
- Identify the intended VESC per kart; common BLE advertisement names are insufficient.
- Pit server arbitrates writes from multiple browsers; browsers never own serial ports.
- Scope any future control changes to verified hardware and a documented test plan.
- Reuse the pinned 6.00 schema only when the controller signature matches.
