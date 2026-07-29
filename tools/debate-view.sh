#!/usr/bin/env bash
# debate-view.sh — launch the live terminal viewer for Angel's Advocate agents.
#
# Run this in a SECOND terminal while a debate runs in Claude Code. It tails the current
# session's subagent transcripts and shows each agent's thinking + tool calls + output,
# live, grouped by role (angel/devil/verifier/…). See tools/debate_view.py for the UI
# and tools/debate_lib.py for the (unit-tested) parsing core.
#
# Usage:
#   tools/debate-view.sh                 # live view of the most-recently-active session
#   tools/debate-view.sh <session-id>    # a specific session under this project
#   tools/debate-view.sh --once          # one-shot dump (replay / pipe to less)
#   tools/debate-view.sh --gui           # open a local browser view (loopback-only http server)
#   tools/debate-view.sh --check-independence   # verify cross-model roles ran on a model !=
#                                        # the Arbiter's, from actual runtime models (exit 1 collapse)
#
# Reads only local transcript files under ~/.claude/projects/<project>/<session>/subagents/.
# Nothing is sent anywhere. python3 stdlib only (curses ships with it on Linux/macOS).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON_BIN:-python3}"

command -v "$PY" >/dev/null 2>&1 || { echo "debate-view.sh: python3 not found (set PYTHON_BIN)." >&2; exit 1; }

exec "$PY" "$SCRIPT_DIR/debate_view.py" "$@"
