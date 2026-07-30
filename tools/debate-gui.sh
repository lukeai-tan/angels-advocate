#!/usr/bin/env bash
# debate-gui.sh — launch the BROWSER view for Angel's Advocate agents, detached.
#
# The GUI server (debate_view.py --gui) blocks on serve_forever(), so a slash command — which
# runs inline in the Claude Code turn — can't host it directly without hanging. This launcher
# starts the server in its own session (detached), waits for it to bind and print its
# loopback URL, relays that URL, and returns — leaving the server running so the browser view
# keeps polling. It's the browser counterpart to debate-window.sh (which opens the terminal
# viewer in a tmux/Windows-Terminal pane).
#
# Usage:
#   tools/debate-gui.sh                 # newest session under this project
#   tools/debate-gui.sh <session-id>    # a specific session
#
# Stop the server later with:  pkill -f 'debate_view.py --gui'
# It only ever READS the session's subagent transcripts and serves them on 127.0.0.1 only —
# nothing is sent anywhere and nothing is modified.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON_BIN:-python3}"

command -v "$PY" >/dev/null 2>&1 || { echo "debate-gui.sh: python3 not found (set PYTHON_BIN)." >&2; exit 1; }

LOG="$(mktemp "${TMPDIR:-/tmp}/debate-gui.XXXXXX.log")"
PORT="${ANGEL_ADVOC_GUI_PORT:-8770}"

# Reuse one stable port instead of leaking a new ephemeral server per launch. Free it by
# killing the PREVIOUS viewer first — but only ours, and only the one actually holding this
# port: a blanket `pkill -f 'debate_view.py --gui'` would also kill a GUI you have open for a
# DIFFERENT project, which is not what "restart mine" should mean.
for pid in $(ss -ltnpH "sport = :$PORT" 2>/dev/null \
             | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u); do
  if tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -q 'debate_view.py .*--gui'; then
    kill "$pid" 2>/dev/null && echo "debate-gui: stopped previous viewer (pid $pid) on port $PORT"
  else
    echo "debate-gui: port $PORT is held by pid $pid, which is NOT a debate viewer — leaving it alone." >&2
  fi
done
# give the socket a moment to be released before we rebind it
for _ in 1 2 3 4 5 6 7 8 9 10; do
  ss -ltnH "sport = :$PORT" 2>/dev/null | grep -q . || break
  sleep 0.1
done

# Detach fully so the server outlives this launcher (and the slash-command shell that spawned it).
if command -v setsid >/dev/null 2>&1; then
  setsid "$PY" "$SCRIPT_DIR/debate_view.py" --gui --port "$PORT" "$@" >"$LOG" 2>&1 &
else
  nohup "$PY" "$SCRIPT_DIR/debate_view.py" --gui --port "$PORT" "$@" >"$LOG" 2>&1 &
fi
disown 2>/dev/null || true

# The port is ephemeral, so the URL isn't known until the server binds. Poll its log for it.
url=""
for _ in $(seq 1 30); do
  url="$(grep -oE 'http://127\.0\.0\.1:[0-9]+/' "$LOG" 2>/dev/null | head -n1 || true)"
  [ -n "$url" ] && break
  sleep 0.1
done

if [ -n "$url" ]; then
  echo "debate-gui: opened the browser view at $url"
  echo "debate-gui: loopback only, running in the background — stop it with: pkill -f 'debate_view.py --gui'"
else
  echo "debate-gui: launched but no URL appeared within 3s. Server output:"
  sed 's/^/  /' "$LOG" 2>/dev/null || true
fi
