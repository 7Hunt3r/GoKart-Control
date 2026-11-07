#!/usr/bin/env bash
# Run every offline test suite in the repo and print a summary table.
#
#   tools/run_tests.sh              # all suites
#   tools/run_tests.sh pit kart     # only suites whose name starts with one of these words
#   tools/run_tests.sh -v           # also print each suite's full output
#
# No hardware, no network. Python 3.9+ standard library plus pyserial (pit/kart servers).
# Every suite runs with `python3 -I` from the repo root. Exit status 1 if any suite fails,
# times out or cannot be found when asked for by name.
set -u
set -f   # suite commands are word-split below; never glob-expand their patterns

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 2
PY="${PYTHON:-python3}"
SUITE_TIMEOUT="${SUITE_TIMEOUT:-600}"   # seconds per suite (macOS has no `timeout`; perl does it)

VERBOSE=0
FILTERS=()
for a in "$@"; do
  case "$a" in
    -v|--verbose) VERBOSE=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) FILTERS+=("$a") ;;
  esac
done

# name | command (run from the repo root)
SUITES=(
  "vesc-tools|$PY -I -m unittest discover -s tests -p test_*.py"
  "pit-core|$PY -I pit/tests/test_core.py"
  "pit-e2e|$PY -I pit/tests/test_e2e.py"
  "kart-core|$PY -I kart/tests/test_core.py"
  "kart-mapsync|$PY -I kart/tests/test_mapsync.py"
  "kart-e2e|$PY -I kart/tests/test_e2e.py"
)
if [ -d firmware/tests ]; then
  SUITES+=("firmware|$PY -I -m unittest discover -s firmware/tests -p test_*.py")
fi

wanted() {
  [ ${#FILTERS[@]} -eq 0 ] && return 0
  local f; for f in "${FILTERS[@]}"; do case "$1" in "$f"*) return 0 ;; esac; done
  return 1
}

if ! "$PY" -c 'import serial' 2>/dev/null; then
  echo "note: pyserial is not importable by $PY; the pit/kart suites need it (pip3 install pyserial)" >&2
fi

TMPBASE="${TMPDIR:-/tmp}"
LOGDIR="$(mktemp -d "${TMPBASE%/}/gokart-tests.XXXXXX")"
ROWS=()
FAILED=0
RAN_ANY=0

for entry in "${SUITES[@]}"; do
  name="${entry%%|*}"; cmd="${entry#*|}"
  wanted "$name" || continue
  RAN_ANY=1
  log="$LOGDIR/$name.log"
  printf '%-14s running... ' "$name"
  t0=$(date +%s)
  # shellcheck disable=SC2086
  # Own process group so a timeout also kills the simulators/servers an e2e suite started.
  perl -e '$t = shift; $pid = fork; die "fork: $!" unless defined $pid;
    if (!$pid) { setpgrp(0, 0); exec @ARGV or die "exec: $!" }
    $SIG{ALRM} = sub { kill "TERM", -$pid; sleep 2; kill "KILL", -$pid; exit 124 };
    alarm $t; waitpid($pid, 0); exit($? & 127 ? 128 + ($? & 127) : $? >> 8)' \
    "$SUITE_TIMEOUT" $cmd >"$log" 2>&1
  rc=$?
  secs=$(( $(date +%s) - t0 ))
  ran=$(grep -Eo '^Ran [0-9]+ tests?' "$log" | tail -1 | grep -Eo '[0-9]+')
  ran=${ran:-?}
  skipped=$(grep -Eo 'skipped=[0-9]+' "$log" | tail -1 | grep -Eo '[0-9]+')
  if [ $rc -eq 0 ] && grep -qE '^OK' "$log"; then
    result="PASS"
  elif [ $rc -eq 124 ]; then
    result="TIMEOUT"; FAILED=1
  else
    result="FAIL"; FAILED=1
  fi
  echo "$result (${secs}s)"
  ROWS+=("$(printf '%-14s %-8s %6s %8s %6ss  %s' "$name" "$result" "$ran" "${skipped:-0}" "$secs" "$cmd")")
  if [ $VERBOSE -eq 1 ] || [ "$result" != "PASS" ]; then
    echo "----- $name output (last 40 lines; full log $log) -----"
    tail -40 "$log"
    echo "-----"
  fi
done

if [ $RAN_ANY -eq 0 ]; then
  echo "no suite matches: ${FILTERS[*]}" >&2
  exit 1
fi

echo
printf '%-14s %-8s %6s %8s %7s  %s\n' SUITE RESULT TESTS SKIPPED TIME COMMAND
for r in "${ROWS[@]}"; do echo "$r"; done
echo
echo "logs: $LOGDIR"
if [ $FAILED -ne 0 ]; then
  echo "FAILED"
  exit 1
fi
echo "ALL PASSED"
exit 0
