#!/usr/bin/env bash
# speak_segments_test.sh — regression suite for tools/speak.sh line->speaker routing.
#
# speak.sh voices a verdict by attributing every line to a speaker (angel/devil/
# verifier/arbiter) and piping each contiguous segment to that speaker's TTS voice.
# The load-bearing invariant this suite pins: a speaker's block may span MANY lines
# (the readable multi-line verdict format) and must stay in ONE voice — an unmarked
# continuation line continues the current speaker, it is NOT re-attributed to the
# arbiter. It also pins the markdown-stripping and per-marker classification so a
# formatted verdict reads as clean prose.
#
# It uses the `--print-segments` dry-run mode, which classifies + prints
# "speaker: text" per segment WITHOUT invoking piper or any audio player, so the
# suite runs headlessly with no TTS deps. house pattern: bash + python3-free, no
# framework, per-run temp dir via trap, exit 0 iff every test passed.
#
# Run from anywhere:  bash tools/tests/speak_segments_test.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPEAK_SH="$(cd "$SCRIPT_DIR/../.." && pwd)/tools/speak.sh"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }

# segments <input> -> captures the dry-run "speaker: text" lines in OUT.
segments() { OUT="$(printf '%s' "$1" | bash "$SPEAK_SH" --print-segments 2>/dev/null)"; }

# assert that OUT contains a line exactly equal to $1
has_line() { printf '%s\n' "$OUT" | grep -qxF "$1"; }
# assert that OUT contains a line matching regex $1
has_re() { printf '%s\n' "$OUT" | grep -qE "$1"; }
# count segment lines attributed to speaker $1
count_speaker() { printf '%s\n' "$OUT" | grep -cE "^$1: " || true; }

# ---------------------------------------------------------------------------
# (1) each marker glyph routes to the right speaker
# ---------------------------------------------------------------------------
segments "😇 Angel says yes
😈 Devil says no
⚖️ Verdict is ship
✅ Verified conforms"
if has_re '^angel: Angel says yes' \
	&& has_re '^devil: Devil says no' \
	&& has_re '^arbiter: Verdict is ship' \
	&& has_re '^verifier: Verified conforms'; then
	pass "each marker glyph routes to its speaker"
else
	fail "each marker glyph routes to its speaker" "$OUT"
fi

# ---------------------------------------------------------------------------
# (2) THE core invariant: a multi-line Angel block stays entirely in angel's voice
#     (unmarked continuation lines are NOT re-attributed to the arbiter)
# ---------------------------------------------------------------------------
segments "😇 Angel — first line of the case
this is the second line with no marker
and a third continuation line

😈 Devil — the attack"
# angel segment must contain all three of its lines, folded into one segment
if has_re '^angel: .*first line of the case.*second line.*third continuation' \
	&& [ "$(count_speaker angel)" = "1" ] \
	&& [ "$(count_speaker arbiter)" = "0" ]; then
	pass "multi-line speaker block stays in one voice (no arbiter leak)"
else
	fail "multi-line speaker block stays in one voice (no arbiter leak)" "$OUT"
fi

# ---------------------------------------------------------------------------
# (3) markdown is stripped before speaking (bold/code/bullets)
# ---------------------------------------------------------------------------
segments "⚖️ **Verdict** — ship it

**Dealbreakers**
- crash risk → resolved by guard
- \`race\` condition → accepted"
if has_re '^arbiter: Verdict — ship it' \
	&& ! has_re '\*\*' \
	&& ! has_re '`' \
	&& has_re 'crash risk' \
	&& ! has_re '^arbiter: - '; then
	pass "markdown (**bold**, backticks, - bullets) stripped from spoken text"
else
	fail "markdown (**bold**, backticks, - bullets) stripped from spoken text" "$OUT"
fi

# ---------------------------------------------------------------------------
# (4) the leading glyph itself is stripped (never spoken aloud)
# ---------------------------------------------------------------------------
segments "😇 Angel — clean"
if has_re '^angel: Angel — clean' && ! has_re '😇'; then
	pass "leading marker glyph stripped from spoken text"
else
	fail "leading marker glyph stripped from spoken text" "$OUT"
fi

# ---------------------------------------------------------------------------
# (5) a full realistic verdict: one segment per speaker, in order
# ---------------------------------------------------------------------------
segments "🔎 **Rigor:** light self-check · **Gate:** assumption

😇 **Angel** — the change is small and reversible
it only touches one function

😈 **Devil** — but the edge case at zero is unhandled
that is a DEALBREAKER

⚖️ **Verdict** — proceed after guarding zero

**Dealbreakers**
- zero unhandled → resolved by early return"
# order of speakers as they first appear
order="$(printf '%s\n' "$OUT" | sed -E 's/:.*//' | tr '\n' ' ')"
if printf '%s' "$order" | grep -qE '^arbiter angel devil arbiter *$' \
	&& [ "$(count_speaker angel)" = "1" ] \
	&& [ "$(count_speaker devil)" = "1" ]; then
	pass "realistic multi-line verdict splits into one segment per speaker, in order"
else
	fail "realistic multi-line verdict splits into one segment per speaker, in order" "order=[$order]
$OUT"
fi

# ---------------------------------------------------------------------------
# (6) speak tag line alone produces no spoken segment (lang tag is not read aloud)
# ---------------------------------------------------------------------------
segments "<!-- speak:en -->
😇 Angel — hi"
if has_re '^angel: Angel — hi' && ! has_re 'speak:en'; then
	pass "language tag stripped, not spoken"
else
	fail "language tag stripped, not spoken" "$OUT"
fi

# ---------------------------------------------------------------------------
echo "-----"
printf 'total: %s   passed: %s   failed: %s\n' "$((PASS + FAIL))" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
