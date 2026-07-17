#!/usr/bin/env bash
# shush.sh — stop in-flight speech, and/or mute voicing entirely.
#
# The Stop hook fires speak.sh in the BACKGROUND (so the session isn't blocked),
# which means a long verdict keeps playing after the turn ends and there's no
# obvious way to interrupt it.
#
#   ./shush.sh              stop any audio playing RIGHT NOW (one-shot)
#   ./shush.sh --mute       stop current audio AND disable all future voicing
#   ./shush.sh --unmute     re-enable voicing (future verdicts speak again)
#   ./shush.sh --status     report whether voicing is currently muted
#
# The one-shot kill hits: the audio players (paplay/aplay), the piper renderer,
# and any running speak.sh pipelines. It deliberately LEAVES the sherpa TTS daemon
# (sherpa_ttsd.py) running — that's the warm model, killing it just makes the
# next Japanese verdict slow to start again.
#
# --mute drops a sentinel file the Stop hook checks; --unmute removes it. The
# sentinel lives in ~/.claude (per-machine), so muting never edits the shared,
# committed hook config.
#
# Safe to run anytime; if nothing is playing the one-shot kill is a no-op.
set -u

MUTE_FLAG="${ANGEL_ADVOC_MUTE_FLAG:-$HOME/.claude/angel-advoc-muted}"

case "${1:-}" in
  --status)
    if [ -f "$MUTE_FLAG" ]; then echo "shush: voicing is MUTED ($MUTE_FLAG)"; else echo "shush: voicing is ON"; fi
    exit 0 ;;
  --unmute)
    rm -f "$MUTE_FLAG" && echo "shush: voicing re-enabled — verdicts will speak again."
    exit 0 ;;
  --mute)
    mkdir -p "$(dirname "$MUTE_FLAG")"
    : > "$MUTE_FLAG"
    echo "shush: voicing MUTED — future verdicts stay silent (undo: shush.sh --unmute)."
    # fall through to also stop anything playing right now
    ;;
  "" ) : ;;  # default: one-shot kill only
  * ) echo "shush: unknown option '$1' (use --mute | --unmute | --status | no arg)" >&2; exit 2 ;;
esac

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
