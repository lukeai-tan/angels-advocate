#!/usr/bin/env bash
# self-check.sh — run every Angel's Advocate integrity check in one shot.
#
# The workflow's honesty guarantees are only as good as the checks that enforce them — but those
# checks are scattered (a preflight guard, several calibration harnesses, a gate-under-fire sweep,
# the unit suites) and only run when someone remembers. This consolidates them behind one command
# so "is the workflow's own machinery still honest?" has a single green/red answer.
#
# Usage:  tools/self-check.sh [arbiter-model]
#   arbiter-model defaults to $ANTHROPIC_MODEL, else claude-opus-5 (only used by the preflight guard).
#
# Exit: 0 iff every FAILING check passed (the gate-sweep is informational and never fails the run).
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

bold "Gate-sweep — under-fire audit (informational; never fails this run)"
if [ -x "$HERE/gate-sweep.sh" ] || [ -f "$HERE/gate-sweep.sh" ]; then
  bash "$HERE/gate-sweep.sh" 2>&1 | tail -12 | sed 's/^/  /' || echo "  (gate-sweep.sh errored — inspect manually)"
else
  echo "  (gate-sweep.sh not found)"
fi

bold "Summary"
total=$((pass + fail))
if [ "$fail" -eq 0 ]; then
  printf '  \033[32m✅ ALL GREEN\033[0m — %d/%d checks passed (+ gate-sweep, informational)\n' "$pass" "$total"
else
  printf '  \033[31m❌ %d of %d checks FAILED\033[0m — the workflow'\''s own integrity machinery is not clean\n' "$fail" "$total"
fi
exit "$fail"
