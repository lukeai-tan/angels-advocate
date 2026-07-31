#!/usr/bin/env bash
# transcript-sweep.sh — the denominator gate-sweep.sh has never had.
#
# gate-sweep.sh asks "which COMMITS look gate-worthy but were never journaled?" — a population
# that structurally excludes every decision that never became a commit. This scans the session
# TRANSCRIPTS instead, clusters file-mutating tool calls into decision *episodes*, and reports
# how many were gate-worthy, how many were journaled, and how many never became a commit at all
# (the slice gate-sweep cannot see). See tools/transcript_sweep.py for the logic and limits.
#
# It prints RAW COUNTS against an explicitly named population and deliberately never prints a
# rate: the denominator is a heuristic over a proxy, so a percentage would look like a
# measurement of the gate's miss rate without being one.
#
# Reads transcripts but emits only paths, line counts, and timestamps — never file content.
#
# Usage:
#   tools/transcript-sweep.sh              # raw counts
#   tools/transcript-sweep.sh --list       # + the un-journaled gate-worthy episodes
#   tools/transcript-sweep.sh --gap 45     # idle minutes that end an episode (default 20)
#   tools/transcript-sweep.sh --window 4   # journal/commit coverage window in hours (default 3)
#
# Read-only. Uses ~/.claude/projects/<slug>/, git, and the local journal.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON_BIN:-python3}"

command -v "$PY" >/dev/null 2>&1 || { echo "transcript-sweep.sh: python3 not found (set PYTHON_BIN)." >&2; exit 1; }

exec "$PY" "$SCRIPT_DIR/transcript_sweep.py" "$@"
