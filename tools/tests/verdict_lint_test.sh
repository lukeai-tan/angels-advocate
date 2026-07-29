#!/usr/bin/env bash
# verdict_lint_test.sh — calibration fixtures for tools/verdict-lint.py.
#
# A lint that always says CLEAN is worse than none (it launders theater — the same reasoning
# verifier-calibration.sh exists for). So this holds known-bad (verdict, devil-count) pairs the
# lint MUST fail, plus clean controls it MUST pass, and scores the exit codes. Add a fixture
# whenever a real miss slips through.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LINT="$DIR/verdict-lint.py"
PY="${PYTHON_BIN:-python3}"
pass=0 fail=0

# run <expected-exit> <name> <devil-count-args...> <<<verdict
check() {
  local want="$1" name="$2"; shift 2
  local out rc
  out="$("$PY" "$LINT" "$@" 2>&1)"; rc=$?
  if [ "$rc" -eq "$want" ]; then
    pass=$((pass+1)); # echo "PASS  $name"
  else
    fail=$((fail+1)); echo "FAIL  $name — wanted exit $want, got $rc"; echo "$out" | sed 's/^/      /'
  fi
}

# ---- KNOWN-BAD: must FAIL (exit 1) ----------------------------------------------------------

# A. Silently dropped dealbreaker: Devil raised 3, block disposes only 2.
check 1 "dropped-dealbreaker" --devil-count 3 <<'EOF'
**Dealbreakers**
- perf regression → **resolved by** caching the result
- race on shutdown → **accepting because** single-threaded in practice
EOF

# B. 'refuted' with no evidence cited.
check 1 "refuted-no-evidence" --devil-count 1 <<'EOF'
**Dealbreakers**
- the API is confusing → **refuted because** it reads fine to me
EOF

# C. 'none raised' but the Devil actually raised dealbreakers.
check 1 "none-raised-but-devil-spoke" --devil-count 2 <<'EOF'
**Dealbreakers**
- none raised
EOF

# D. Malformed bullet: no recognised disposition at all.
check 1 "no-disposition" --devil-count 1 <<'EOF'
**Dealbreakers**
- the migration is risky and we should think about it
EOF

# E. No Dealbreakers block whatsoever.
check 1 "missing-block" --devil-count 0 <<'EOF'
⚖️ Verdict — ship it, looks good.
EOF

# ---- CONTROLS: must PASS (exit 0) -----------------------------------------------------------

# F. Full coverage, refuted cites evidence (file:line + a measurement).
check 0 "clean-full" --devil-count 3 <<'EOF'
**Dealbreakers**
- perf regression → **refuted because** benchmarked at 1.2ms, unchanged (tools/bench.py:40)
- race on shutdown → **resolved by** a lock added at server.py:88
- scope creep → **accepting because** the user explicitly asked for the extra flag
EOF

# G. Genuinely none raised, and the Devil raised none.
check 0 "clean-none" --devil-count 0 <<'EOF'
**Dealbreakers**
- none raised
EOF

# H. More disposed than raised (merges/extra caution) is fine.
check 0 "clean-over-cover" --devil-count 1 <<'EOF'
**Dealbreakers**
- data loss → **resolved by** a dry-run flag, rehearsed on a copy (restore verified)
- ux confusion → **refuted because** reproduced the flow, 0/5 testers tripped
EOF

echo
echo "-------------------------------------------"
echo "verdict-lint calibration: $((pass+fail)) cases, passed: $pass, failed: $fail"
[ "$fail" -eq 0 ] || exit 1
