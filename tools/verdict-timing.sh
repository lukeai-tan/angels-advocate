#!/usr/bin/env bash
# verdict-timing.sh — was the 😈 block written BEFORE the work, or after it?
#
# The Arbiter spec's anti-retrospective rule rests on an ordering claim: the Devil's block tends to be
# composed after the fix already landed. That is a timestamp, not a statistic — this reads it
# straight out of the session transcripts and reports declared timing against measured timing.
#
# It replaces a proxy that was demoted on 2026-07-31 (the light-vs-structural dealbreaker
# acceptance rate: reproducible, but p = 0.175, resting on two events, decaying toward 1.0x as n
# grows — and confounded, since structural fires on harder decisions). Ordering has no such
# confound and does not depend on self-assigned dispositions.
#
# Emits normalised rigor labels, counts, and timestamps only — never message text.
#
# Usage:
#   tools/verdict-timing.sh              # the cross-tab
#   tools/verdict-timing.sh --gap 45     # idle minutes that end an episode (default 20)
#   tools/verdict-timing.sh --era <iso>  # when the anti-retrospective rule landed
#
# Read-only. Companion to transcript-sweep.sh (which counts episodes) and gate-sweep.sh (commits).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON_BIN:-python3}"

command -v "$PY" >/dev/null 2>&1 || { echo "verdict-timing.sh: python3 not found (set PYTHON_BIN)." >&2; exit 1; }

exec "$PY" "$SCRIPT_DIR/verdict_timing.py" "$@"
