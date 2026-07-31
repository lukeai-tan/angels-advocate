#!/usr/bin/env bash
# transcript_sweep_test.sh — regression suite for the decision-episode denominator
# (tools/transcript_sweep.py).
#
# WHY THIS EXISTS. This tool exists to answer "is the gate under-firing?", and the failure mode of
# a tool like that is not crashing — it is producing a confident-looking wrong number that then
# gets quoted. Its first run did exactly that: it reported 6 un-journaled gate-worthy episodes,
# all of which predated the journal's own first entry and therefore *could not* have been
# journaled. So the properties worth pinning are the ones that keep the number honest:
#
#   (1) POPULATION BOUNDARY — sidechain (subagent) entries and non-mutating tools are excluded,
#       and episodes never merge across sessions. Two conversations interleaved in wall-clock
#       time would otherwise fuse into an episode no single conversation ever had.
#   (2) IDLE GAP SPLITS — a gap wider than --gap starts a new episode; a narrower one does not.
#   (3) PRE-JOURNAL IS NOT A MISS — the bug above, pinned. Work done before the journal existed
#       is reported in its own bucket, never as under-firing.
#   (4) NO CONTENT LEAKS — transcripts hold file contents and whatever the user typed. A secret
#       planted in an edit's payload must not survive into the output. mutations_from_entry()
#       drops the text at parse time; this proves it end-to-end through the CLI.
#   (5) NO RATE — the verdict that authorised this tool said raw counts scoped to a named
#       population, never a headline rate. Enforced mechanically: no '%' in the output.
#
# House pattern from gui_test.sh / workflow_test.sh: bash + python3, no framework, every fixture
# under one mktemp dir removed on exit. The repo is never touched.
#
# Run from anywhere:  bash tools/tests/transcript_sweep_test.sh
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

SECRET="sk-live-CANARY-must-never-be-printed-9f3a"

# --- build the fixture repo + fake ~/.claude transcripts ---------------------------------------
REPO="$WORKDIR/repo"
HOME_DIR="$WORKDIR/home"
mkdir -p "$REPO/.angel-advoc"
git init -q "$REPO" 2>/dev/null
git -C "$REPO" config user.email t@t.invalid
git -C "$REPO" config user.name t
: >"$REPO/seed.txt"
git -C "$REPO" add seed.txt >/dev/null 2>&1
git -C "$REPO" commit -qm seed >/dev/null 2>&1

"$PY" - "$REPO" "$HOME_DIR" "$SECRET" <<'PY'
import io, json, os, sys
repo, home, secret = sys.argv[1], sys.argv[2], sys.argv[3]
slug = repo.replace(os.sep, "-")
tdir = os.path.join(home, ".claude", "projects", slug)
os.makedirs(tdir)

def edit(ts, path, sidechain=False, tool="Edit", uuid=None, payload="a\nb\nc\n"):
    return {"type": "assistant", "timestamp": ts, "isSidechain": sidechain,
            "uuid": uuid or (ts + path + str(sidechain)),
            "message": {"content": [{"type": "tool_use", "name": tool,
                                     "input": {"file_path": os.path.join(repo, path),
                                               "old_string": "x\n", "new_string": payload}}]}}

rows = []
# A_pre — 4 files, five days BEFORE the journal's first entry. Gate-worthy, but unscoreable.
for i, f in enumerate(["a1.py", "a2.py", "a3.py", "a4.py"]):
    rows.append(edit("2026-01-05T10:0%d:00.000Z" % i, f))
# A1 — 4 files, right after a journal entry. Gate-worthy and covered.
for i, f in enumerate(["b1.py", "b2.py", "b3.py", "b4.py"]):
    rows.append(edit("2026-01-10T13:0%d:00.000Z" % i, f))
# A2 — 5 files, hours from any journal entry and from any commit. The real MISS.
for i, f in enumerate(["c1.py", "c2.py", "c3.py", "c4.py", "c5.py"]):
    rows.append(edit("2026-01-10T20:0%d:00.000Z" % i, f))
# Secrets in edit payloads — one on c1.py, whose path IS printed by --list, and one on c6.py,
# whose path is not (only the first three are shown). Both positions matter: a leak planted only
# in the truncated tail passes a canary check that a real leak would have failed.
rows.append(edit("2026-01-10T20:00:30.000Z", "c1.py", uuid="secret-printed", payload=secret))
rows.append(edit("2026-01-10T20:05:00.000Z", "c6.py", payload=secret))
# Excluded by the population boundary: a subagent's edit, and a read.
rows.append(edit("2026-01-10T13:00:30.000Z", "sidechain-only.py", sidechain=True))
rows.append(edit("2026-01-10T13:00:40.000Z", "read-only.py", tool="Read"))
# A duplicate uuid (resumed/forked session replay) — must be counted once.
rows.append(edit("2026-01-10T13:00:00.000Z", "b1.py", uuid="dupe"))
rows.append(edit("2026-01-10T13:00:00.000Z", "b1.py", uuid="dupe"))
with io.open(os.path.join(tdir, "sessA.jsonl"), "w", encoding="utf-8") as fh:
    for r in rows:
        fh.write(json.dumps(r) + "\n")

# Session B — interleaved with A1 in wall-clock time. Must NOT merge into A1's episode.
with io.open(os.path.join(tdir, "sessB.jsonl"), "w", encoding="utf-8") as fh:
    fh.write(json.dumps(edit("2026-01-10T13:00:30.000Z", "solo.py")) + "\n")

with io.open(os.path.join(repo, ".angel-advoc", "journal.jsonl"), "w", encoding="utf-8") as fh:
    for ts in ("2026-01-10T12:00:00Z", "2026-01-10T13:05:00Z"):
        fh.write(json.dumps({"ts": ts, "gate": "light", "target": "fixture"}) + "\n")
PY

run_sweep() { "$PY" "$TOOLS_DIR/transcript_sweep.py" --root "$REPO" --home "$HOME_DIR" "$@" 2>&1; }

OUT="$WORKDIR/out.txt"
run_sweep --list >"$OUT"
if ! grep -q "RAW COUNTS" "$OUT"; then
	fail "transcript_sweep.py runs on the fixture" "$(tail -3 "$OUT")"
	printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
	exit 1
fi
pass "transcript_sweep.py runs on the fixture"

# Pull the counts out of the report text rather than re-implementing the logic here.
field() { # <regex with one capture group>
	"$PY" -c "
import re, sys, io
m = re.search(sys.argv[1], io.open(sys.argv[2], encoding='utf-8').read(), re.M)
print(m.group(1) if m else 'NONE')
" "$1" "$OUT"
}

expect() { # <label> <actual> <want>
	if [ "$2" = "$3" ]; then pass "$1 == $3"; else fail "$1" "expected $3, got $2"; fi
}

# --- (1) population boundary + (2) idle gap ----------------------------------------------------
# Four episodes: A_pre, A1, A2 (session A, split on the >20min gaps) and B's lone call. The
# sidechain edit, the Read, and the duplicate uuid must all be absent from the call count.
expect "episodes" "$(field '^  episodes +(\d+)')" 4
# 15 in session A + 1 kept from the duplicated pair + 1 in session B. The sidechain edit and the
# Read never count; the replayed duplicate counts once (18 here would mean dedup regressed).
expect "mutating tool calls (sidechain/Read excluded, dupe counted once)" "$(field '^  main-thread mutating tool calls +(\d+)')" 17
expect "gate-worthy episodes (B's single file is not)" "$(field '^  gate-worthy episodes +(\d+)')" 3

# --- (3) pre-journal is bucketed, not scored as a miss -----------------------------------------
expect "episodes predating the journal" "$(field '^    predate the journal \([\d-]+\) +(\d+)')" 1
expect "journaled" "$(field '^    journaled within [^ ]+ +(\d+)')" 1
expect "NOT journaled" "$(field '^    NOT journaled +(\d+)')" 1
expect "never committed" "$(field '^      of those, never committed +(\d+)')" 1

# The pre-journal episode must not also be listed among the misses.
if grep -qE '^  2026-01-05' "$OUT"; then
	fail "pre-journal episode is excluded from the --list of misses" \
		"a 2026-01-05 episode was listed as un-journaled"
else
	pass "pre-journal episode is excluded from the --list of misses"
fi

# --- (4) no content leaks ----------------------------------------------------------------------
if grep -qF "$SECRET" "$OUT"; then
	fail "no transcript content in the output" "the planted canary reached the report"
else
	pass "no transcript content in the output (canary absent)"
fi
# Same property one level down, at the parse boundary where it is actually enforced.
leak="$("$PY" -c "
import sys; sys.path.insert(0, '$TOOLS_DIR')
import transcript_sweep as ts
e = {'type':'assistant','timestamp':'2026-01-10T13:00:00Z','isSidechain':False,
     'message':{'content':[{'type':'tool_use','name':'Edit',
     'input':{'file_path':'/x/y.py','old_string':'$SECRET','new_string':'$SECRET'}}]}}
out = ts.mutations_from_entry(e)
print('LEAK' if '$SECRET' in repr(out) else 'CLEAN', out)
")"
case "$leak" in
CLEAN*) pass "mutations_from_entry drops payload text at parse time ($leak)" ;;
*) fail "mutations_from_entry drops payload text at parse time" "$leak" ;;
esac

# --- (5) raw counts, never a rate --------------------------------------------------------------
if grep -qF '%' "$OUT"; then
	fail "output contains no percentage (verdict: raw counts, named population)" \
		"$(grep -nF '%' "$OUT" | head -2 | tr '\n' ' ')"
else
	pass "output contains no percentage (verdict: raw counts, named population)"
fi
for must in "POPULATION" "LIMITS" "SENSITIVITY"; do
	if grep -q "^$must" "$OUT"; then
		pass "report carries its $must block"
	else
		fail "report carries its $must block"
	fi
done

# --- sensitivity really varies with the window (the block isn't decorative) ---------------------
tight="$(run_sweep --window 0.01 | "$PY" -c "
import re, sys
m = re.search(r'^    NOT journaled +(\d+)', sys.stdin.read(), re.M)
print(m.group(1) if m else 'NONE')
")"
if [ "$tight" = "2" ]; then
	pass "a tighter window uncovers the previously-covered episode (misses 1 -> $tight)"
else
	fail "a tighter window uncovers the previously-covered episode" "expected 2, got $tight"
fi

# ---------------------------------------------------------------------------
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
