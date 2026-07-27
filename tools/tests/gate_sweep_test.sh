#!/usr/bin/env bash
# gate_sweep_test.sh — regression suite for tools/gate_sweep.py (the under-firing sweep).
#
# The sweep's value is a trustworthy heuristic: score a commit's gate-worthiness and decide
# whether a journal entry plausibly covers it. This pins the PURE logic (classify_commit,
# _risky_category, is_covered, score) so a future edit can't silently change what gets flagged.
# The git-gathering path is exercised by running the real tool in the repo, not unit-tested here.
#
# Same house pattern: bash + python3, no framework. Run from anywhere:
#   bash tools/tests/gate_sweep_test.sh    (exit 0 iff every test passed)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PY="${PYTHON_BIN:-python3}"
export PYTHONPATH="$TOOLS_DIR${PYTHONPATH:+:$PYTHONPATH}"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
pyt() {
	local name="$1" body="$2" out
	if out="$("$PY" -c "$body" 2>&1)"; then pass "$name"; else fail "$name" "$out"; fi
}

pyt "_risky_category classifies irreversible / dependency / infra / plain" "
import gate_sweep as g
assert g._risky_category('db/migrations/0007_add.py') == 'irreversible/schema'
assert g._risky_category('sql/schema.sql') == 'irreversible/schema'
assert g._risky_category('package-lock.json') == 'dependency change'
assert g._risky_category('go.mod') == 'dependency change'
assert g._risky_category('Dockerfile') == 'infra/CI'
assert g._risky_category('.github/workflows/ci.yml') == 'infra/CI'
assert g._risky_category('src/util.py') is None
"

pyt "classify_commit: strong signals, multi-file, churn, and trivial (empty)" "
import gate_sweep as g
# a lone dependency file -> strong, gate-worthy even though it's 1 file
r, strong = g.classify_commit(1, 5, 0, ['requirements.txt'])
assert strong and any('dependency' in x for x in r), (r, strong)
# 3+ files -> multi-file (soft signal)
r, strong = g.classify_commit(3, 10, 2, ['a.py','b.py','c.py'])
assert not strong and any('multi-file' in x for x in r), (r, strong)
# large churn
r, _ = g.classify_commit(1, 150, 120, ['big.py'])
assert any('large churn' in x for x in r), r
# trivial: 1 file, small churn, no risky path -> NOT gate-worthy
r, strong = g.classify_commit(1, 3, 1, ['README.md'])
assert r == [] and not strong, (r, strong)
"

pyt "classify_commit: adding a subagent role is gate-worthy despite being small" "
import gate_sweep as g
# The regression this pins: a new role is ~2 files / <100 lines, so the file-count and churn
# heuristics scored it NOT gate-worthy at all -- the sweep flagged a cosmetic 3-file tweak
# while three separate role-adding commits were invisible. In a repo whose architecture IS
# markdown, path identity has to carry what size cannot.
for path in ('.claude/agents/researcher.md', '.claude/workflows/sweep.js', '.claude/commands/x.md'):
    r, strong = g.classify_commit(2, 73, 0, [path, 'CLAUDE.md'])
    assert strong, (path, r, strong)
    assert any('workflow definition' in x for x in r), (path, r)
# CLAUDE.md alone counts too -- it IS the Arbiter's instructions
r, strong = g.classify_commit(1, 8, 0, ['CLAUDE.md'])
assert strong and any('workflow definition' in x for x in r), (r, strong)
# ...but ordinary prose/code must NOT be swept in by the new category
for path in ('README.md', 'tools/debate_lib.py', 'notes/claude-notes.txt'):
    r, strong = g.classify_commit(1, 8, 0, [path])
    assert not any('workflow definition' in x for x in r), (path, r)
# genuinely risky categories still outrank it in the reason ordering
r, _ = g.classify_commit(2, 10, 0, ['.claude/agents/x.md', 'db/migration_001.sql'])
assert 'irreversible' in r[0], r
"

pyt "is_covered: journal entry inside/outside the window" "
import gate_sweep as g
import debate_lib as dl
commit = dl._ts_epoch('2026-07-24T12:00:00Z')
near = dl._ts_epoch('2026-07-24T13:30:00Z')   # +1.5h
far  = dl._ts_epoch('2026-07-24T20:00:00Z')   # +8h
W = 3 * 3600
assert g.is_covered(commit, [near], W) is True
assert g.is_covered(commit, [far], W) is False
assert g.is_covered(commit, [], W) is False       # empty journal -> uncovered
assert g.is_covered(None, [near], W) is False      # unparseable commit date
"

pyt "score ranks strong signals above soft, then churn" "
import gate_sweep as g
strong = g.score(['dependency change: x','multi-file (3 files)'], True, 10, 5, 3)
soft   = g.score(['multi-file (3 files)'], False, 400, 300, 9)
assert strong > soft, (strong, soft)   # a risky-path commit outranks a big but ordinary one
"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
