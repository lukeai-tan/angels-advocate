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
#        😇 or "Angel"                        -> angel
#        😈 or "Devil"                        -> devil
#        ✅ / "Verified" / "CONFORMANCE CHECK" -> verifier
#        ⚖️ / 🔎 / everything else            -> arbiter (narration)
#      A marker line starts a new segment; unmarked lines continue the current one.
#   3. Each segment is piped to Piper with that speaker+language's model, and
#      played in order so the conversation is voiced back in sequence.
#
# Requires: piper (in PATH or set PIPER_BIN), aplay OR paplay, voices.conf.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="${VOICES_CONF:-$SCRIPT_DIR/voices.conf}"
PIPER_BIN="${PIPER_BIN:-piper}"
# Python that has sherpa_onnx installed (for the Supertonic/ja daemon). The
# miniforge python is where `pip install sherpa-onnx` landed; override if yours
# differs. Only consulted when a voice uses the "supertonic:" engine.
SHERPA_PY="${SHERPA_PY:-python3}"
SHERPA_SOCK="${SHERPA_SOCK:-${XDG_RUNTIME_DIR:-/tmp}/sherpa_ttsd.sock}"
SHERPA_DAEMON="$SCRIPT_DIR/sherpa_ttsd.py"

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
# Extract the two-letter code from the first `<!-- speak:xx -->` tag. sed with a
# capture group is portable POSIX (GNU + BSD/macOS); no PCRE lookahead needed.
LANG_CODE="$FORCED_LANG"
if [ -z "$LANG_CODE" ]; then
  LANG_CODE="$(printf '%s' "$INPUT" | sed -nE 's/.*<!--[[:space:]]*speak:([a-zA-Z]{2})[[:space:]]*-->.*/\1/p' | head -n1)"
fi
LANG_CODE="$(printf '%s' "${LANG_CODE:-en}" | tr '[:upper:]' '[:lower:]')"
case "$LANG_CODE" in en|de|zh|ja) ;; *) echo "speak.sh: unknown lang '$LANG_CODE', using en" >&2; LANG_CODE="en";; esac

model_for() { # $1 = speaker -> echoes model path from "<lang>_<speaker>"
  local var="${LANG_CODE}_$1"
  printf '%s' "${!var:-}"
}

speaker_of_line() { # classify a single line -> angel|devil|verifier|arbiter|""(blank)
  local line="$1"
  [ -z "${line//[[:space:]]/}" ] && { printf ''; return; }
  case "$line" in
    *"😇"*|"Angel"*|"CASE FOR"*)                    printf 'angel' ;;
    *"😈"*|"Devil"*|"CASE AGAINST"*)                printf 'devil' ;;
    *"✅"*|"Verified"*|"CONFORMANCE CHECK"*)         printf 'verifier' ;;
    *) printf 'arbiter' ;;
  esac
}

# --- Supertonic (sherpa) daemon: render <text,sid> -> WAV over a unix socket ---
# Auto-start the daemon on first use and reuse it thereafter. It self-exits after
# an idle timeout (see sherpa_ttsd.py), so nothing lingers forever.
ensure_sherpa_daemon() {
  # Already listening?  A quick connect test.
  if "$SHERPA_PY" - "$SHERPA_SOCK" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    s.connect(sys.argv[1]); s.close()
except OSError:
    sys.exit(1)
PY
  then return 0; fi
  # Not up — launch it detached and wait for the socket to appear.
  [ -f "$SHERPA_DAEMON" ] || { echo "speak.sh: daemon missing: $SHERPA_DAEMON" >&2; return 1; }
  SUPERTONIC_DIR="${SUPERTONIC_DIR:-}" nohup "$SHERPA_PY" "$SHERPA_DAEMON" \
    --socket "$SHERPA_SOCK" >/dev/null 2>&1 &
  disown || true
  for _ in $(seq 1 60); do        # up to ~30s for the model to load
    [ -S "$SHERPA_SOCK" ] && "$SHERPA_PY" - "$SHERPA_SOCK" <<'PY' 2>/dev/null && return 0
import socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    s.connect(sys.argv[1]); s.close()
except OSError:
    sys.exit(1)
PY
    sleep 0.5
  done
  echo "speak.sh: sherpa daemon failed to start" >&2
  return 1
}

# Send one render request; returns 0 and writes $TMPWAV on success.
sherpa_render() { # $1 = sid, $2 = text
  ensure_sherpa_daemon || return 1
  SID="$1" TEXT="$2" OUT="$TMPWAV" SOCK="$SHERPA_SOCK" "$SHERPA_PY" - <<'PY'
import json, os, socket, sys
req = {"sid": int(os.environ["SID"]), "out": os.environ["OUT"],
       "text": os.environ["TEXT"], "speed": 1.0}
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    s.connect(os.environ["SOCK"])
    s.sendall((json.dumps(req) + "\n").encode("utf-8"))
    resp = b""
    while b"\n" not in resp:
        c = s.recv(4096)
        if not c: break
        resp += c
finally:
    s.close()
ok = resp.startswith(b"OK")
if not ok:
    sys.stderr.write("speak.sh: " + resp.decode("utf-8", "replace"))
sys.exit(0 if ok else 1)
PY
}

# --- 2 & 3. walk lines, accumulate per-speaker segments, speak on speaker change ---
TMPWAV="$(mktemp --suffix=.wav)"
trap 'rm -f "$TMPWAV"' EXIT

cur_speaker=""
buf=""

flush() {
  [ -z "${buf//[[:space:]]/}" ] && { buf=""; return; }
  # strip the speak tag and leading marker glyphs before speaking
  local text
  text="$(printf '%s' "$buf" \
    | sed -E 's/<!--[[:space:]]*speak:[a-zA-Z]{2}[[:space:]]*-->//g' \
    | sed -E 's/^[[:space:]]*(😇|😈|⚖️|🔎|✅)[[:space:]]*//')"
  # nothing left to say once the tag/markers are stripped (e.g. a lone tag line):
  # piper would emit an empty WAV that the player can't open, so skip it
  [ -z "${text//[[:space:]]/}" ] && { buf=""; return; }
  local model; model="$(model_for "$cur_speaker")"
  if [ -z "$model" ]; then
    echo "speak.sh: no voice for ${LANG_CODE}_${cur_speaker} (skipping segment)" >&2
    buf=""; return
  fi
  case "$model" in
    supertonic:*)   # sherpa daemon engine — model is "supertonic:<sid>"
      if ! sherpa_render "${model#supertonic:}" "$text"; then
        echo "speak.sh: supertonic render failed for ${LANG_CODE}_${cur_speaker} (skipping)" >&2
        buf=""; return
      fi
      ;;
    *)              # piper engine — model is a .onnx file path
      if [ ! -f "$model" ]; then
        echo "speak.sh: no model file for ${LANG_CODE}_${cur_speaker} ($model) (skipping)" >&2
        buf=""; return
      fi
      printf '%s' "$text" | "$PIPER_BIN" --model "$model" --output_file "$TMPWAV" >/dev/null 2>&1
      ;;
  esac
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
