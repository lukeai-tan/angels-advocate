#!/usr/bin/env bash
# speak.sh — read a Claude response on stdin, speak it with per-speaker,
# per-language Piper voices.
#
#   echo "<!-- speak:de -->\n😇 Angel — ...\n😈 Devil — ...\n⚖️ Verdict — ..." | ./speak.sh
#   ./speak.sh < response.txt
#   ./speak.sh --lang ja < response.txt      # force language, ignore any tag
#
# How it works:
#   1. Language: from a `<!-- speak:xx -->` tag in the text, or --lang, else en.
#   2. Splitting: every line is attributed to a speaker by its leading marker:
#        😇 or "Angel"   -> angel
#        😈 or "Devil"   -> devil
#        ⚖️ / 🔎 / everything else -> arbiter (narration)
#      A marker line starts a new segment; unmarked lines continue the current one.
#   3. Each segment is piped to Piper with that speaker+language's model, and
#      played in order so the conversation is voiced back in sequence.
#
# Requires: piper (in PATH or set PIPER_BIN), aplay OR paplay, voices.conf.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="${VOICES_CONF:-$SCRIPT_DIR/voices.conf}"
PIPER_BIN="${PIPER_BIN:-piper}"

# --- pick a player available on this system (WSLg ships pulseaudio -> paplay) ---
if command -v paplay >/dev/null 2>&1; then
  PLAY() { paplay "$1"; }
elif command -v aplay >/dev/null 2>&1; then
  PLAY() { aplay -q "$1"; }
else
  echo "speak.sh: no audio player found (need paplay or aplay)." >&2
  exit 1
fi

command -v "$PIPER_BIN" >/dev/null 2>&1 || { echo "speak.sh: piper not found (set PIPER_BIN)." >&2; exit 1; }
[ -f "$CONF" ] || { echo "speak.sh: config not found: $CONF" >&2; exit 1; }
# shellcheck disable=SC1090
source "$CONF"

# --- args ---
FORCED_LANG=""
if [ "${1:-}" = "--lang" ]; then FORCED_LANG="${2:-}"; shift 2 || true; fi

INPUT="$(cat)"

# --- 1. language ---
LANG_CODE="$FORCED_LANG"
if [ -z "$LANG_CODE" ]; then
  LANG_CODE="$(printf '%s' "$INPUT" | grep -oiE '<!--[[:space:]]*speak:[a-z]{2}[[:space:]]*-->' | head -n1 | grep -oiE '[a-z]{2}(?=[[:space:]]*-->)' || true)"
  # portable fallback if the lookahead grep isn't supported:
  if [ -z "$LANG_CODE" ]; then
    LANG_CODE="$(printf '%s' "$INPUT" | sed -nE 's/.*<!--[[:space:]]*speak:([a-zA-Z]{2})[[:space:]]*-->.*/\1/p' | head -n1)"
  fi
fi
LANG_CODE="$(printf '%s' "${LANG_CODE:-en}" | tr '[:upper:]' '[:lower:]')"
case "$LANG_CODE" in en|de|zh|ja) ;; *) echo "speak.sh: unknown lang '$LANG_CODE', using en" >&2; LANG_CODE="en";; esac

model_for() { # $1 = speaker -> echoes model path from "<lang>_<speaker>"
  local var="${LANG_CODE}_$1"
  printf '%s' "${!var:-}"
}

speaker_of_line() { # classify a single line -> angel|devil|arbiter|""(blank)
  local line="$1"
  [ -z "${line//[[:space:]]/}" ] && { printf ''; return; }
  case "$line" in
    *"😇"*|"Angel"*|"CASE FOR"*)        printf 'angel' ;;
    *"😈"*|"Devil"*|"CASE AGAINST"*)    printf 'devil' ;;
    *) printf 'arbiter' ;;
  esac
}

# --- 2 & 3. walk lines, accumulate per-speaker segments, speak on speaker change ---
TMPWAV="$(mktemp --suffix=.wav)"
trap 'rm -f "$TMPWAV"' EXIT

cur_speaker=""
buf=""

flush() {
  [ -z "${buf//[[:space:]]/}" ] && { buf=""; return; }
  local model; model="$(model_for "$cur_speaker")"
  if [ -z "$model" ] || [ ! -f "$model" ]; then
    echo "speak.sh: no model for ${LANG_CODE}_${cur_speaker} (skipping segment)" >&2
    buf=""; return
  fi
  # strip the speak tag and leading marker glyphs before speaking
  local text
  text="$(printf '%s' "$buf" \
    | sed -E 's/<!--[[:space:]]*speak:[a-zA-Z]{2}[[:space:]]*-->//g' \
    | sed -E 's/^[[:space:]]*(😇|😈|⚖️|🔎)[[:space:]]*//')"
  printf '%s' "$text" | "$PIPER_BIN" --model "$model" --output_file "$TMPWAV" 2>/dev/null
  PLAY "$TMPWAV"
  buf=""
}

while IFS= read -r line || [ -n "$line" ]; do
  who="$(speaker_of_line "$line")"
  [ -z "$who" ] && { buf+="$line"$'\n'; continue; }   # blank line: keep in current segment
  if [ -n "$cur_speaker" ] && [ "$who" != "$cur_speaker" ]; then
    flush
  fi
  cur_speaker="$who"
  buf+="$line"$'\n'
done <<< "$INPUT"
flush
