# GoKart Control project conventions

- Canonical project: ~/Projects/50 Day/GoKart Control/ (Eamon's clone: ~/GoKart-Control).
  Reuse this repository.
- Read README.md and HANDOFF.md before implementation; inspect local git status.
- The connected kart's measured baseline takes precedence over the old M5Stack
  repository for existing hardware/settings. User requirements govern desired behavior.
- Preserve evidence in hardware/vesc/. Never mutate an existing baseline capture.
- Read-only controller inspection is authorized; do not silently include motor
  commands, configuration writes, firmware updates, resets or motor detection.
- Maintain separate verified/reported/proposed status. Runtime reads are not
  automatically saved-flash backups. Motor-temperature sensors are not wired.
- The Heltec owns the VESC connection, GPS, LoRa and supervisory profile logic.
  The kart Pi is display/interface only. BLE remains the preferred VESC transport.
  Driver inputs and motor control remain with the ESC; profile writes are disabled.
- Reliability first: no driving-state changes on display/Heltec/link loss or
  reconnect. Keep current active VESC settings; no saved-profile restore,
  heartbeat-dependent driving lease, stop/reset or cached-command replay.
  Reconnect read-only and reconcile actual state. Existing ESC protections remain
  intact. This supersedes the earlier saved-configuration fallback requirement.
- Driver messages, gradual remote stop and geofence enforcement are deferred.
  Status note (2026-11-06, facts only, not a rule change): branch eamon/firmware-lora-starter
  builds the kart with pit-commanded temporary (store=0) writes enabled for STOP/START/mode/
  SLOW/BOOST/RACE and an optional game layer; this is flagged for owner review against the
  rules above (HANDOFF.md, Decisions for Colin). Until the owner decides, the rules stand.
- The owner now explicitly authorizes ongoing commits/pushes at coherent, checked
  milestones so Colin and Eamon share current work. This supersedes the earlier
  per-commit request rule for this project. Fetch before work and again before push;
  inspect and integrate remote changes without overwriting others or force-pushing.
  Use focused branches/worktrees and PRs for parallel implementation; see CONTRIBUTING.md.
- Keep requirements, architecture, config and handoff consistent. Preserve evidence;
  distinguish owner decisions from proposed details and verified implementation.
- No sub-agent delegation is required by this file.
- Run offline checks described in CONTRIBUTING.md for inspection-tool changes, and
  tools/run_tests.sh (all suites) before every push.
