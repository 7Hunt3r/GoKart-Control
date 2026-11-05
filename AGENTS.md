# Gokart project conventions

- Canonical project: ~/Projects/50 Day/gokart-dashboard/. Reuse this repository.
- Read README.md and HANDOFF.md before implementation; inspect local git status.
- The connected kart's measured baseline takes precedence over the old M5Stack
  repository for existing hardware/settings. User requirements govern desired behavior.
- Preserve evidence in hardware/vesc/. Never mutate an existing baseline capture.
- Read-only controller inspection is authorized; do not silently include motor
  commands, configuration writes, firmware updates, resets or motor detection.
- Maintain separate verified/reported/proposed status. Runtime reads are not
  automatically saved-flash backups. Motor-temperature sensors are not wired.
- BLE is the preferred VESC transport. Driver inputs remain hardwired to the ESC.
- Driver messages, gradual remote stop and geofence enforcement are deferred.
- Commit/push only when requested, per the user's shared Git conventions.
- No sub-agent delegation is required by this file.
- Run offline checks described in CONTRIBUTING.md for inspection-tool changes.
