#!/usr/bin/env bash
# shush.sh — stop any in-flight speech started by speak.sh, immediately.
#
# The Stop hook fires speak.sh in the BACKGROUND (so the session isn't blocked),
# which means a long verdict keeps playing after the turn ends and there's no
# obvious way to interrupt it. Run this to cut it off:
#
#   ./shush.sh
#   tools/shush.sh
#
# It kills, in order: the audio players (paplay/aplay), the piper renderer, and
# any running speak.sh pipelines. It deliberately LEAVES the sherpa TTS daemon
# (sherpa_ttsd.py) running — that's the warm model, killing it just makes the
# next Japanese verdict slow to start again.
#
# Safe to run anytime; if nothing is playing it's a no-op.
set -u

killed=0

# 1. Audio players — exact-name match so we never hit an unrelated process.
for p in paplay aplay; do
  if pkill -x "$p" 2>/dev/null; then echo "shush: stopped $p"; killed=1; fi
done

# 2. Piper renderer (standalone binary; basename is 'piper').
if pkill -x piper 2>/dev/null; then echo "shush: stopped piper"; killed=1; fi

# 3. Running speak.sh pipelines. Match the script path, but exclude THIS process
#    (and our own parent shell) so shush.sh never kills itself. From a script file
#    our own cmdline is the interpreter + shush.sh, so it won't match 'speak.sh'
#    anyway — the --older/self-exclude is belt-and-suspenders.
for pid in $(pgrep -f '/speak\.sh|[^/]speak\.sh' 2>/dev/null); do
  [ "$pid" = "$$" ] && continue
  [ "$pid" = "$PPID" ] && continue
  kill "$pid" 2>/dev/null && { echo "shush: stopped speak.sh ($pid)"; killed=1; }
done

# Note: we intentionally do NOT kill sherpa_ttsd.py — leaving the model warm.
[ "$killed" = 0 ] && echo "shush: nothing playing."
exit 0
