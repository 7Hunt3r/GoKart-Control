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

Commit and push subsequent work only when the owner asks. The initial scaffold
commit/push was the explicit newproject exception. Do not overwrite another
collaborator's working changes or force-push shared branches.

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
- Maintain explicit stale/unknown states and requested vs confirmed commands.
- Identify the intended VESC per kart; common BLE advertisement names are insufficient.
- Pit server arbitrates writes from multiple browsers; browsers never own serial ports.
- Scope any future control changes to verified hardware and a documented test plan.
- Reuse the pinned 6.00 schema only when the controller signature matches.
