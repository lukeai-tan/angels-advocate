#!/usr/bin/env bash
# self-check.sh — run every Angel's Advocate integrity check in one shot.
#
# The workflow's honesty guarantees are only as good as the checks that enforce them — but those
# checks are scattered (a preflight guard, several calibration harnesses, three under-firing and
# ordering probes, the unit suites) and only run when someone remembers. This consolidates them
# behind one command so "is the workflow's own machinery still honest?" has a single green/red
# answer — plus the probe excerpts, which have no such answer to give and say so.
#
# A check that SKIPS is not a check that passed. Two suites (gui_test, workflow_test) skip their
# `node --check` parse when no JS parser is installed, and they say so on stdout — but this script
# captured that stdout and discarded it on the success branch, so a box without node printed
# "✅ ALL GREEN" over JavaScript that no longer parsed. That is verbatim the failure bb1ae1e was
# written to remove ("deleting one brace left self-check.sh reporting ALL GREEN 9/9"). So skips are
# now surfaced, counted, and given their own summary state — green is reserved for verified.
#
# Usage:  tools/self-check.sh [--strict] [arbiter-model]
#   arbiter-model defaults to $ANTHROPIC_MODEL, else claude-opus-5 (only used by the preflight guard).
#   --strict  exit non-zero when any check was SKIPPED. Default is exit 0 (a node-less box can still
#             get a useful run out of the other suites); use --strict in CI, where "could not verify"
#             should be as loud as "failed". The flag exists because the exit-code call belongs to
#             the caller, not to this script.
#
# Exit: 0 iff every FAILING check passed — and, under --strict, nothing was skipped. The three probes
# are informational and never fail the run.
#
# $ANGEL_ADVOC_TESTS_DIR (test-only) runs the suite loop against a different directory and suppresses
# the preflight + probes, since a scoped run is not the repo-wide integrity sweep. It is also what
# makes tests/self_check_test.sh possible: the glob below auto-enrols every *_test.sh, so a test that
# re-invoked this script would recurse forever — pointing the child at a temp dir breaks that by
# construction rather than by a guard someone can forget to add.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # tools/
ROOT="$(cd "$HERE/.." && pwd)"                          # repo root
cd "$ROOT"                                              # tests reference tools/… relative to root

STRICT=0
args=()
for a in "$@"; do
  case "$a" in
    --strict) STRICT=1 ;;
    *) args+=("$a") ;;
  esac
done
ARBITER="${args[0]:-${ANTHROPIC_MODEL:-claude-opus-5}}"

TESTS_DIR="${ANGEL_ADVOC_TESTS_DIR:-$HERE/tests}"
SCOPED=0; [ -n "${ANGEL_ADVOC_TESTS_DIR:-}" ] && SCOPED=1

bold(){ printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
fail=0 pass=0 skipped=0

bold "Test + calibration suites ($TESTS_DIR/*_test.sh)"
shopt -s nullglob
for t in "$TESTS_DIR"/*_test.sh; do
  name="$(basename "$t")"
  out="$(bash "$t" 2>&1)"; rc=$?
  # Anchored to the start of the line ON PURPOSE. Several suites legitimately print the word "skip"
  # mid-sentence (journal-report_test asserts on `skip-noted` gates; debate_lib_test on skipped
  # malformed lines) — an unanchored, case-insensitive match reports 3 phantom skips across a clean
  # run. `^SKIP` reports 0 there and 2 on a node-less box, which is the whole point.
  sk="$(printf '%s\n' "$out" | grep -c '^SKIP')"
  skipped=$((skipped + sk))
  if [ "$rc" -eq 0 ]; then
    if [ "$sk" -gt 0 ]; then
      # Not a ✔. The suite did not fail, but it did not verify what it claims to verify either.
      printf '  \033[33m⚠\033[0m %s \033[33m(%d skipped)\033[0m\n' "$name" "$sk"
    else
      printf '  \033[32m✔\033[0m %s\n' "$name"
    fi
    pass=$((pass + 1))
  else
    printf '  \033[31m✗\033[0m %s\n' "$name"; fail=$((fail + 1))
    printf '%s\n' "$out" | tail -4 | sed 's/^/        /'
  fi
  # Always echo the skip lines themselves — the reason a check was skipped is the actionable part
  # (usually "install node"), and burying it was the original defect.
  [ "$sk" -gt 0 ] && printf '%s\n' "$out" | grep '^SKIP' | sed 's/^/        /'
done
shopt -u nullglob

if [ "$SCOPED" -eq 0 ]; then
bold "Preflight — cross-model config guard (arbiter: $ARBITER)"
if bash "$HERE/preflight.sh" "$ARBITER" >/dev/null 2>&1; then
  printf '  \033[32m✔\033[0m preflight: every cross-model role declares a model != the arbiter\n'; pass=$((pass + 1))
else
  printf '  \033[31m✗\033[0m preflight: a cross-model role would collapse to a same-model self-check (run: tools/preflight.sh %s)\n' "$ARBITER"; fail=$((fail + 1))
fi
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

if [ "$SCOPED" -eq 0 ]; then
bold "Probes — informational; measured, not pass/fail, so they never fail this run"
probe gate-sweep.sh       'NR<=5 || /^  [0-9a-f]{9} /'  'did a gate-worthy COMMIT go unjournaled?'
probe transcript-sweep.sh '/^RAW COUNTS/,/^$/'          'did a gate-worthy EDIT EPISODE go unjournaled?'
probe verdict-timing.sh   '/^FINDINGS/,/^$/'            'was the 😈 block written before the work, or after?'
fi

bold "Summary"
total=$((pass + fail))
if [ "$fail" -gt 0 ]; then
  printf '  \033[31m❌ %d of %d checks FAILED\033[0m — the workflow'\''s own integrity machinery is not clean\n' "$fail" "$total"
elif [ "$skipped" -gt 0 ]; then
  # Deliberately NOT green. Nothing failed, but $skipped check(s) did not run, so this run cannot
  # support the claim a green line would make. Reserving ✅ for verified is the entire fix.
  printf '  \033[33m⚠️  %d/%d suites passed, %d check(s) SKIPPED\033[0m — verification INCOMPLETE, not green\n' "$pass" "$total" "$skipped"
  printf '     A skipped check is not a passed check. See the SKIP lines above for what to install.\n'
  [ "$STRICT" -eq 1 ] && printf '     --strict: exiting non-zero because a check could not be verified.\n'
else
  printf '  \033[32m✅ ALL GREEN\033[0m — %d/%d checks passed, 0 skipped\n' "$pass" "$total"
fi

[ "$fail" -eq 0 ] && [ "$STRICT" -eq 1 ] && [ "$skipped" -gt 0 ] && exit 1
exit "$fail"
