#!/usr/bin/env bash
# verdict_timing_test.sh — regression suite for the ordering probe (tools/verdict_timing.py).
#
# WHY THIS EXISTS. This probe exists to catch the workflow wearing a costume — a decision labelled
# "light self-check" whose 😈 block was actually composed after the fix. A probe like that fails in
# two directions, and both are worse than not having it:
#
#   (1) STRICT DETECTION — the marker glyphs appear in every message that merely *discusses* the
#       format, including the conversation this probe was designed in. On the real corpus 108 of
#       138 glyph-bearing messages are rejected. A looser filter would quietly measure chatter.
#   (2) NORMALISED LABELS, NO TEXT — the Rigor line is authored prose and can contain anything.
#       It is classified into a fixed vocabulary at parse time and dropped, so the probe cannot
#       print transcript content. Proven here with a canary planted inside a Rigor line.
#   (3) TIMING CLASSIFICATION — edits after the verdict = pre-written, before = retrospective,
#       both = mixed, none = no edits.
#   (4) MIXED IS NEVER A FAILURE — the first version folded MIXED into the costume count while its
#       own LIMITS block promised it never would. Since the MIXED share runs from 7% to 63% purely
#       as a function of the clustering gap, that bug let a free constant inflate the headline
#       finding several-fold. Pinned here so it cannot come back.
#   (5) ERA SPLIT — verdicts predating the anti-retrospective rule were written under no such
#       obligation and must not be scored against it (the same trap transcript_sweep.py fell into).
#   (6) SIDECHAIN EXCLUDED — a subagent's own output is not the Arbiter's verdict.
#
# House pattern from transcript_sweep_test.sh / gui_test.sh: bash + python3, no framework, every
# fixture under one mktemp dir removed on exit. The repo is never touched.
#
# Run from anywhere:  bash tools/tests/verdict_timing_test.sh
# Exit 0 iff every test passed.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PY="${PYTHON_BIN:-python3}"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }

SECRET="CANARY-rigor-line-must-never-be-printed-7b21"
ERA="2026-01-08T00:00:00Z"

REPO="$WORKDIR/repo"
HOME_DIR="$WORKDIR/home"
mkdir -p "$REPO/.angel-advoc"
git init -q "$REPO" 2>/dev/null
git -C "$REPO" config user.email t@t.invalid
git -C "$REPO" config user.name t

"$PY" - "$REPO" "$HOME_DIR" "$SECRET" <<'PY'
import io, json, os, sys
repo, home, secret = sys.argv[1], sys.argv[2], sys.argv[3]
tdir = os.path.join(home, ".claude", "projects", repo.replace(os.sep, "-"))
os.makedirs(tdir)
n = [0]

def edit(ts, path):
    n[0] += 1
    return {"type": "assistant", "timestamp": ts, "isSidechain": False, "uuid": "e%d" % n[0],
            "message": {"content": [{"type": "tool_use", "name": "Edit",
                                     "input": {"file_path": os.path.join(repo, path),
                                               "old_string": "x\n", "new_string": "y\n"}}]}}

def verdict(ts, rigor, sidechain=False, markers="\U0001F50E\U0001F608⚖"):
    n[0] += 1
    body = ""
    if "\U0001F50E" in markers:
        body += "\U0001F50E **Rigor:** %s · **Gate:** fixture\n\n" % rigor if rigor else "\U0001F50E no rigor line here\n\n"
    if "\U0001F608" in markers:
        body += "\U0001F608 **Devil** — fixture attack\n\n"
    if "⚖" in markers:
        body += "⚖️ **Verdict** — fixture ruling\n"
    return {"type": "assistant", "timestamp": ts, "isSidechain": sidechain, "uuid": "v%d" % n[0],
            "message": {"content": [{"type": "text", "text": body}]}}

rows = []
# V1 pre-written, undeclared. The canary rides in the Rigor line: normalise_rigor must classify
# it as "light (undeclared)" and the raw text must never surface.
rows.append(verdict("2026-01-10T13:00:00.000Z", "light self-check %s" % secret))
rows += [edit("2026-01-10T13:01:00.000Z", "a.py"), edit("2026-01-10T13:02:00.000Z", "b.py")]
# V2 retrospective, undeclared -> THE COSTUME. The one finding that should be counted.
rows += [edit("2026-01-10T15:00:00.000Z", "c.py"), edit("2026-01-10T15:01:00.000Z", "d.py")]
rows.append(verdict("2026-01-10T15:05:00.000Z", "light self-check"))
# V3 mixed, undeclared -> must NOT be counted as the costume.
rows.append(edit("2026-01-10T17:00:00.000Z", "e.py"))
rows.append(verdict("2026-01-10T17:02:00.000Z", "light self-check"))
rows.append(edit("2026-01-10T17:04:00.000Z", "f.py"))
# V4 self-declared retrospective, measuring retrospective -> COMPLIANCE, not a failure.
rows += [edit("2026-01-10T19:00:00.000Z", "g.py"), edit("2026-01-10T19:01:00.000Z", "h.py")]
rows.append(verdict("2026-01-10T19:02:00.000Z", "light self-check (retrospective)"))
# V5 retrospective + undeclared, but BEFORE the rule -> excluded from FINDINGS.
rows += [edit("2026-01-05T10:00:00.000Z", "i.py"), edit("2026-01-05T10:01:00.000Z", "j.py")]
rows.append(verdict("2026-01-05T10:03:00.000Z", "light self-check"))
# V6/V7 rejected by the strict filter: one glyph only, and all glyphs but no Rigor line.
rows.append(verdict("2026-01-10T21:00:00.000Z", "light self-check", markers="\U0001F608"))
rows.append(verdict("2026-01-10T21:10:00.000Z", None))
# V9 the real false positive, and the ONLY fixture that isolates the glyph-COUNT branch: a message
# that quotes a perfectly parseable Rigor line while merely *discussing* the format. V6 can't test
# this — it has no Rigor line, so it is rejected with or without the count check.
rows.append(verdict("2026-01-10T23:00:00.000Z", "light self-check", markers="\U0001F50E"))
# V8 a subagent's own output -> ignored entirely, not even counted as rejected.
rows.append(verdict("2026-01-10T22:00:00.000Z", "structural debate", sidechain=True))

with io.open(os.path.join(tdir, "sess1.jsonl"), "w", encoding="utf-8") as fh:
    for r in rows:
        fh.write(json.dumps(r) + "\n")
PY

OUT="$WORKDIR/out.txt"
"$PY" "$TOOLS_DIR/verdict_timing.py" --root "$REPO" --home "$HOME_DIR" \
	--era "$ERA" --gap 5 >"$OUT" 2>&1
if ! grep -q "DETECTION" "$OUT"; then
	fail "verdict_timing.py runs on the fixture" "$(tail -3 "$OUT")"
	printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
	exit 1
fi
pass "verdict_timing.py runs on the fixture"

field() {
	"$PY" -c "
import re, sys, io
m = re.search(sys.argv[1], io.open(sys.argv[2], encoding='utf-8').read(), re.M)
print(m.group(1) if m else 'NONE')
" "$1" "$OUT"
}
expect() { if [ "$2" = "$3" ]; then pass "$1 == $3"; else fail "$1" "expected $3, got $2"; fi; }

# --- (1) strict detection + (6) sidechain excluded ---------------------------------------------
# V1-V5 pass. V6 (one glyph, no Rigor), V7 (all glyphs, no Rigor) and V9 (Rigor line but only one
# glyph — format chatter) are rejected. V8 (sidechain) is invisible: not found, not even rejected.
expect "verdict blocks found" "$(field '^  verdict blocks found +(\d+)')" 5
expect "messages rejected by the filter (sidechain not among them)" \
	"$(field '^  messages rejected by the filter +(\d+)')" 3
# Isolate the glyph-count branch. Dropping it is the loosening that would make the probe measure
# chatter, and it is invisible to every other assertion here.
chatter="$("$PY" -c "
import sys; sys.path.insert(0, '$TOOLS_DIR')
import verdict_timing as vt
e = lambda t: {'type': 'assistant', 'isSidechain': False,
               'message': {'content': [{'type': 'text', 'text': t}]}}
rigor = u'\U0001F50E **Rigor:** light self-check · **Gate:** x\n'
print(vt.verdict_from_entry(e(rigor))[0],
      vt.verdict_from_entry(e(rigor + u'\U0001F608 d\n' + u'⚖️ v\n'))[0])
")"
expect "a Rigor line alone is chatter; all three glyphs make a verdict" "$chatter" \
	"None light (undeclared)"

# --- (2) no raw Rigor text in the output -------------------------------------------------------
if grep -qF "$SECRET" "$OUT"; then
	fail "Rigor line text never reaches the output" "the planted canary was printed"
else
	pass "Rigor line text never reaches the output (canary absent)"
fi
leak="$("$PY" -c "
import sys; sys.path.insert(0, '$TOOLS_DIR')
import verdict_timing as vt
lab = vt.normalise_rigor('light self-check $SECRET')
print('LEAK' if '$SECRET' in lab else 'CLEAN', repr(lab))
")"
case "$leak" in
CLEAN*) pass "normalise_rigor maps free text to a fixed vocabulary ($leak)" ;;
*) fail "normalise_rigor maps free text to a fixed vocabulary" "$leak" ;;
esac

# --- (3) timing classification -----------------------------------------------------------------
cls="$("$PY" -c "
import sys; sys.path.insert(0, '$TOOLS_DIR')
import verdict_timing as vt
print(vt.classify_timing(0, 2), vt.classify_timing(2, 0), vt.classify_timing(1, 1), vt.classify_timing(0, 0))
")"
expect "classify_timing(after / before / both / neither)" "$cls" \
	"pre-written retrospective mixed no edits"

# --- (4) MIXED is never folded into the failure count ------------------------------------------
# V2 is the only real costume. V3 is mixed and must be reported separately, not added to it.
expect "costume count (undeclared light, measured retrospective)" \
	"$(field '^  undeclared light self-checks measuring retrospective +(\d+)')" 1
expect "the same, measured MIXED (reported apart)" \
	"$(field '^    the same, measuring MIXED[^0-9]+(\d+)')" 1
compliance_re='^  self-declared .\(retrospective\)., measured retrospective +(\d+)'
expect "self-declared retrospective, measured retrospective (compliance)" \
	"$(field "$compliance_re")" 1

# --- (5) the era split really excludes pre-rule verdicts ---------------------------------------
# V5 is undeclared + retrospective. Were the era ignored, the costume count above would be 2.
if grep -q "before the rule" "$OUT"; then
	pass "pre-rule verdicts are reported in their own era block"
else
	fail "pre-rule verdicts are reported in their own era block"
fi
moved="$("$PY" "$TOOLS_DIR/verdict_timing.py" --root "$REPO" --home "$HOME_DIR" \
	--era "2026-01-01T00:00:00Z" --gap 5 2>&1 | "$PY" -c "
import re, sys
m = re.search(r'^  undeclared light self-checks measuring retrospective +(\d+)', sys.stdin.read(), re.M)
print(m.group(1) if m else 'NONE')
")"
expect "moving the era boundary pulls V5 into scope" "$moved" 2

# --- the sensitivity block is present and really varies ----------------------------------------
if grep -q "^SENSITIVITY" "$OUT"; then
	pass "report carries its SENSITIVITY block"
else
	fail "report carries its SENSITIVITY block"
fi

# ---------------------------------------------------------------------------
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
