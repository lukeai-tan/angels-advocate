#!/usr/bin/env bash
# gate-sweep.sh — find decisions that should have fired review but were never journaled.
#
# The gate's real risk is UNDER-firing: shipping something gate-worthy with no review or record.
# This scans recent git commits, scores each for gate-worthiness (multi-file, irreversible/schema,
# dependency, infra, large churn), and flags the ones with NO decision-journal entry nearby as
# candidate under-fires. Companion to journal-report.sh (which audits what WAS logged); this looks
# for what SHOULD have been. See tools/gate_sweep.py for the logic.
#
# Usage:
#   tools/gate-sweep.sh                 # last 30 commits
#   tools/gate-sweep.sh 60              # last 60
#   tools/gate-sweep.sh --since 2.weeks # by git date range
#   tools/gate-sweep.sh --all           # coverage for every commit, not just misses
#   tools/gate-sweep.sh --window 4      # journal-coverage window in hours (default 3)
#
# Read-only. Uses git + the local journal ($ANGEL_ADVOC_JOURNAL, else <repo>/.angel-advoc/).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON_BIN:-python3}"

command -v "$PY" >/dev/null 2>&1 || { echo "gate-sweep.sh: python3 not found (set PYTHON_BIN)." >&2; exit 1; }

exec "$PY" "$SCRIPT_DIR/gate_sweep.py" "$@"
