#!/usr/bin/env bash
# token-report.sh — token usage across Angel's Advocate sessions.
#
# Walks every session transcript under this project (main <id>.jsonl + its subagents) and sums
# message.usage, split by type (input / output / cache-read / cache-create), per session and as
# a grand total. See tools/token_report.py for the logic and tools/debate_lib.py for the
# (unit-tested) parsing core.
#
# Usage:
#   tools/token-report.sh                      # per-session table + grand total
#   tools/token-report.sh --json               # same data as JSON
#   tools/token-report.sh --session <id> --subagents-only --since <ISO-UTC> --json
#                                              # one debate's subagent cost (used by the journal)
#
# Reads only local transcript files under ~/.claude/projects/<project>/. Nothing is sent
# anywhere. python3 stdlib only.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON_BIN:-python3}"

command -v "$PY" >/dev/null 2>&1 || { echo "token-report.sh: python3 not found (set PYTHON_BIN)." >&2; exit 1; }

exec "$PY" "$SCRIPT_DIR/token_report.py" "$@"
