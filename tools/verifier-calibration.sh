#!/usr/bin/env bash
# verifier-calibration.sh — check the verifier catches problems instead of rubber-stamping.
#
# The verifier tends to return CONFORMS; this proves it can say FAILS when it should. It manages
# checked-in fixtures (known-bad verdict/diff pairs it MUST fail + a clean control it MUST pass)
# and scores the verifier's verdicts. The verifier is a Claude Code subagent, so the Arbiter runs
# it on each fixture's prompt in between the `prompt` and `score` steps. See tools/verifier_calibration.py.
#
# Procedure:
#   tools/verifier-calibration.sh list                 # what will be tested
#   for each fixture id: spawn the `verifier` subagent on `verifier-calibration.sh prompt <id>`
#   collect each verdict into results.jsonl as {"id":"<id>","overall":"<verifier OVERALL line>"}
#   tools/verifier-calibration.sh score results.jsonl  # calibrated? (exit 1 if it rubber-stamped)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON_BIN:-python3}"

command -v "$PY" >/dev/null 2>&1 || { echo "verifier-calibration.sh: python3 not found (set PYTHON_BIN)." >&2; exit 1; }

exec "$PY" "$SCRIPT_DIR/verifier_calibration.py" "$@"
