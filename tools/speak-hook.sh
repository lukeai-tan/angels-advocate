#!/usr/bin/env bash
# speak-hook.sh — Claude Code Stop hook. Voices the Arbiter's last reply.
#
# Wired into .claude/settings.json under hooks.Stop. Claude Code pipes the hook a
# JSON object on stdin that includes `transcript_path` (the session JSONL). We
# pull the last assistant text message out of it and, IF it contains a speaker
# marker (😇 😈 ⚖️ 🔎 ✅ — i.e. it's an actual Arbiter verdict, not idle chatter),
# pipe it to speak.sh. Plain replies are left silent on purpose.
#
# Speaking runs in the background so the hook returns immediately and never
# blocks the session while audio plays.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPEAK="$SCRIPT_DIR/speak.sh"
PY="${PYTHON_BIN:-python3}"

# Mute switch. If this sentinel file exists, voicing is disabled — the hook exits
# quietly and speaks nothing. Toggle it with `shush.sh --mute` / `--unmute`. Kept
# outside the repo (in ~/.claude) so muting is a per-machine choice, never a
# committed change to the shared hook config.
MUTE_FLAG="${ANGEL_ADVOC_MUTE_FLAG:-$HOME/.claude/angel-advoc-muted}"
[ -f "$MUTE_FLAG" ] && exit 0

# Hooks can run with a reduced PATH that omits piper. If it's not already
# resolvable, point speak.sh at the known install via PIPER_BIN so it still works.
if [ -z "${PIPER_BIN:-}" ] && ! command -v piper >/dev/null 2>&1; then
  for cand in "$HOME/piper/piper/piper" "$HOME/piper/piper"; do
    [ -x "$cand" ] && { export PIPER_BIN="$cand"; break; }
  done
fi

# The hook JSON arrives on stdin. We pass it to Python via an env var rather than
# a pipe: `python3 - <<'PY'` already consumes stdin for the script source, so a
# pipe would be silently discarded and sys.stdin would read the script itself.
HOOK_INPUT="$(cat)"
export HOOK_INPUT

# Extract the last assistant text block from the transcript. Emits nothing (and
# the hook exits quietly) if there's no transcript path or no speakable text.
TEXT="$("$PY" - <<'PY'
import json, os, sys

try:
    hook = json.loads(os.environ.get("HOOK_INPUT", ""))
except Exception:
    sys.exit(0)

path = hook.get("transcript_path")
if not path:
    sys.exit(0)

try:
    lines = open(path, encoding="utf-8").read().splitlines()
except OSError:
    sys.exit(0)

# Walk backward to the most recent assistant message that carries text blocks.
for ln in reversed(lines):
    try:
        obj = json.loads(ln)
    except ValueError:
        continue
    if obj.get("type") != "assistant":
        continue
    content = obj.get("message", {}).get("content")
    if not isinstance(content, list):
        continue
    parts = [b.get("text", "") for b in content if b.get("type") == "text"]
    text = "\n".join(p for p in parts if p).strip()
    if text:
        print(text)
        break
PY
)"

# Only voice actual Arbiter output — a response carrying a speaker marker. Plain
# replies (no marker) stay silent so the hook isn't reading everything aloud.
case "$TEXT" in
  *"😇"*|*"😈"*|*"⚖️"*|*"🔎"*|*"✅"*)
    printf '%s' "$TEXT" | "$SPEAK" >/dev/null 2>&1 &
    disown
    # Surface a visible confirmation line in the UI. A Stop hook's plain stdout
    # only shows in transcript mode, so we emit the documented `systemMessage`
    # JSON field instead. We deliberately omit `decision`/`continue` so this only
    # displays text and never alters control flow. Built in Python to JSON-encode
    # the emoji/quotes/newlines safely.
    TEXT="$TEXT" "$PY" - <<'PY'
import json, os
text = os.environ.get("TEXT", "").strip()
# One-line preview: first non-empty line, minus the speak tag, capped in length.
line = next((l.strip() for l in text.splitlines()
             if l.strip() and "<!-- speak:" not in l), "")
if len(line) > 90:
    line = line[:89].rstrip() + "…"
print(json.dumps({"systemMessage": f"🔊 Voicing verdict — {line}"}))
PY
    ;;
esac

exit 0
