#!/usr/bin/env bash
# self-check.sh — run every Angel's Advocate integrity check in one shot.
#
# The workflow's honesty guarantees are only as good as the checks that enforce them — but those
# checks are scattered (a preflight guard, several calibration harnesses, three under-firing and
# ordering probes, the unit suites) and only run when someone remembers. This consolidates them
# behind one command so "is the workflow's own machinery still honest?" has a single green/red
# answer — plus the probe excerpts, which have no such answer to give and say so.
#
# Usage:  tools/self-check.sh [arbiter-model]
#   arbiter-model defaults to $ANTHROPIC_MODEL, else claude-opus-5 (only used by the preflight guard).
#
# Exit: 0 iff every FAILING check passed (the three probes are informational and never fail the run).
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # tools/
ROOT="$(cd "$HERE/.." && pwd)"                          # repo root
cd "$ROOT"                                              # tests reference tools/… relative to root
ARBITER="${1:-${ANTHROPIC_MODEL:-claude-opus-5}}"

bold(){ printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
fail=0 pass=0

bold "Test + calibration suites (tools/tests/*_test.sh)"
shopt -s nullglob
for t in "$HERE"/tests/*_test.sh; do
  name="$(basename "$t")"
  if out="$(bash "$t" 2>&1)"; then
    printf '  \033[32m✔\033[0m %s\n' "$name"; pass=$((pass + 1))
  else
    printf '  \033[31m✗\033[0m %s\n' "$name"; fail=$((fail + 1))
    printf '%s\n' "$out" | tail -4 | sed 's/^/        /'
  fi
done
shopt -u nullglob

bold "Preflight — cross-model config guard (arbiter: $ARBITER)"
if bash "$HERE/preflight.sh" "$ARBITER" >/dev/null 2>&1; then
  printf '  \033[32m✔\033[0m preflight: every cross-model role declares a model != the arbiter\n'; pass=$((pass + 1))
else
  printf '  \033[31m✗\033[0m preflight: a cross-model role would collapse to a same-model self-check (run: tools/preflight.sh %s)\n' "$ARBITER"; fail=$((fail + 1))
fi

# The three probes below MEASURE the workflow rather than testing it, so none of them can pass or
# fail: their outputs are counts over heuristic populations, and each ships a LIMITS block spelling
# out what its numbers do not support. Turning any of them into a pass/fail gate would be exactly
# the costume this repo forbids — a threshold on a number whose denominator is a judgement call.
# So they print an excerpt and never touch $fail. Read the full report when a number moves.
#
# They are complementary, not redundant: gate-sweep's population is *commits* (blind to decisions
# that never became one), transcript-sweep's is *edit episodes* mined from transcripts (the
# denominator gate-sweep lacks), and verdict-timing measures ORDER — was the 😈 block composed
# before the edits or after them — which is the anti-retrospective rule's actual claim.
probe() { # probe <script> <awk-range> <what it answers>
  local script="$HERE/$1" range="$2" out
  printf '\n  \033[1m%s\033[0m — %s\n' "$1" "$3"
  if [ ! -f "$script" ]; then printf '    (not found)\n'; return; fi
  if out="$(bash "$script" 2>&1)"; then
    printf '%s\n' "$out" | awk "$range" | sed 's/^/    /'
    printf '    \033[2m(excerpt — full report and its LIMITS block: tools/%s)\033[0m\n' "$1"
  else
    printf '    (errored — inspect manually: tools/%s)\n' "$1"
  fi
}

bold "Probes — informational; measured, not pass/fail, so they never fail this run"
probe gate-sweep.sh       'NR<=5 || /^  [0-9a-f]{9} /'  'did a gate-worthy COMMIT go unjournaled?'
probe transcript-sweep.sh '/^RAW COUNTS/,/^$/'          'did a gate-worthy EDIT EPISODE go unjournaled?'
probe verdict-timing.sh   '/^FINDINGS/,/^$/'            'was the 😈 block written before the work, or after?'

bold "Summary"
total=$((pass + fail))
if [ "$fail" -eq 0 ]; then
  printf '  \033[32m✅ ALL GREEN\033[0m — %d/%d checks passed (+ 3 probes, informational)\n' "$pass" "$total"
else
  printf '  \033[31m❌ %d of %d checks FAILED\033[0m — the workflow'\''s own integrity machinery is not clean\n' "$fail" "$total"
fi
exit "$fail"
