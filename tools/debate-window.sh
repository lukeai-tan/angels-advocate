#!/usr/bin/env bash
# debate-window.sh — auto-open the Angel's Advocate debate viewer in a separate
# Windows Terminal window when a subagent spawns.
#
# Wired via a PreToolUse hook on the subagent-spawning tool (see .claude/settings.json).
# The hook fires once per spawn (angel/devil/verifier/…); the guard below ensures only
# ONE window opens per debate. Safe to run manually too — running it with no args opens
# the window immediately (subject to the guard).
#
# WSL-only: it shells out to wt.exe (Windows Terminal). On non-WSL / no-wt hosts it is a
# no-op, so it is harmless to leave wired everywhere. Deliberately NOT installed by
# install.sh (environment-specific); it lives project-scoped in this repo.
#
# Modes:
#   debate-window.sh          guard, then launch a new wt.exe window (what the hook calls)
#   debate-window.sh --force  skip the debounce lock; open on demand (what /debate-window calls)
#   debate-window.sh --run    run the viewer + pause-on-error (invoked INSIDE the window)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---- inner mode: runs inside the freshly-opened terminal window ----------------------
if [ "${1:-}" = "--run" ]; then
  if ! "$SCRIPT_DIR/debate-view.sh"; then
    st=$?
    echo
    echo "debate-view exited with status $st"
    read -n1 -rsp "press any key to close this window..."
  fi
  exit 0
fi

# ---- guard + launch: runs from the hook (no arg) or /debate-window (--force) ----------

# --force (manual/on-demand) skips the debounce lock; the hook path keeps it.
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

# Only meaningful under WSL with Windows Terminal. Elsewhere, quietly do nothing.
if ! command -v wt.exe >/dev/null 2>&1; then
  [ "$FORCE" -eq 1 ] && echo "debate-window: wt.exe not found — this feature needs WSL + Windows Terminal."
  exit 0
fi

# TTL matches the debate-clustering gap in debate_lib.py (DEBATE_GAP_SECONDS): spawns
# within one debate share a single window; a new debate (>TTL later) gets a fresh one.
LOCK="${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}/angel-advoc-debate-window.lock"
TTL=300

now=$(date +%s)
if [ "$FORCE" -eq 0 ]; then
  # Atomic claim: mkdir succeeds for exactly ONE racing process. This — not the pgrep
  # check below — is what prevents 3 simultaneous spawns from opening 3 windows.
  if mkdir "$LOCK" 2>/dev/null; then
    :  # won the claim — proceed to launch
  else
    age=$(( now - $(stat -c %Y "$LOCK" 2>/dev/null || echo "$now") ))
    if [ "$age" -lt "$TTL" ]; then exit 0; fi  # a recent launch already covers this debate
    touch "$LOCK" 2>/dev/null || true          # stale — refresh and re-evaluate
  fi
else
  # On-demand: ignore the debounce, but keep the lock fresh so a following hook spawn
  # doesn't immediately open a second window on top of this one.
  mkdir "$LOCK" 2>/dev/null || touch "$LOCK" 2>/dev/null || true
fi

# If a viewer is already open, reuse it (it auto-refreshes and can page debates with [ ]).
if pgrep -f 'debate_view\.py' >/dev/null 2>&1; then
  [ "$FORCE" -eq 1 ] && echo "debate-window: a viewer window is already open — reusing it (page debates with [ / ])."
  exit 0
fi

touch "$LOCK" 2>/dev/null || true

# Open a NEW Windows Terminal window, cd'd into the repo inside WSL, running the viewer.
# Invoking our own --run mode keeps the wt command line free of ';' — which wt.exe would
# otherwise parse as a pane separator. Backgrounded + exit 0 so the hook never delays the
# subagent spawn.
wt.exe --window new wsl.exe -d "${WSL_DISTRO_NAME:-Ubuntu}" --cd "$REPO" \
  -- bash -lc "'$SCRIPT_DIR/debate-window.sh' --run" \
  >/dev/null 2>&1 &

[ "$FORCE" -eq 1 ] && echo "debate-window: opened the debate viewer in a new Windows Terminal window."
exit 0
