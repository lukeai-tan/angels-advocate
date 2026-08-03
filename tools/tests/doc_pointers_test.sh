#!/usr/bin/env bash
# doc_pointers_test.sh — every tool a doc points at must actually exist.
#
# The Arbiter spec (.claude/rules/arbiter.md), README.md, CLAUDE.md, and the calibration notes are
# full of `tools/x.sh` references, and the spec's oldest surviving convention ("pointer, not a
# restatement") deliberately leans on them: content lives in one place and everywhere else points at
# it. That only works while the pointers resolve — and one in this repo has already gone stale
# undetected, which is exactly the failure a human eye does not catch on a spec that loads every turn.
#
# The house remedy for docs-vs-code drift here is a mechanical sweep, never an eyeball, so this is
# that sweep: extract every tools/… path the docs mention and assert the file is on disk. It is a
# FAILING check rather than an informational probe because, unlike the three sweeps, it has no
# judgement-call denominator — a pointer either resolves or it does not.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # tools/
ROOT="$(cd "$DIR/.." && pwd)"
pass=0 fail=0

for doc in CLAUDE.md README.md .claude/rules/arbiter.md docs/calibration-notes.md; do
  path="$ROOT/$doc"
  [ -f "$path" ] || { echo "FAIL  $doc — not found at $path"; fail=$((fail+1)); continue; }

  # Strip the trailing punctuation a prose sentence leaves attached to a path ("…gate-sweep.sh.").
  refs="$(grep -oE 'tools/[A-Za-z0-9_.-]+\.(sh|py|js)' "$path" | sed 's/[.,;:)]*$//' | sort -u)"
  [ -n "$refs" ] || { echo "FAIL  $doc — no tools/ references found at all (extractor broken?)"; fail=$((fail+1)); continue; }

  while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    if [ -e "$ROOT/$ref" ]; then
      pass=$((pass+1))
    else
      fail=$((fail+1))
      echo "FAIL  $doc points at $ref — no such file"
    fi
  done <<EOF
$refs
EOF
done

# Self-calibration: prove the check has teeth. A reference to a file that cannot exist MUST be
# caught by the same extractor, otherwise the loop above is green for the wrong reason.
probe="$(printf 'see tools/definitely-not-a-real-tool.sh for details\n' \
  | grep -oE 'tools/[A-Za-z0-9_.-]+\.(sh|py|js)' | sed 's/[.,;:)]*$//')"
if [ "$probe" = "tools/definitely-not-a-real-tool.sh" ] && [ ! -e "$ROOT/$probe" ]; then
  pass=$((pass+1))
else
  fail=$((fail+1)); echo "FAIL  self-calibration — extractor did not recover a known-bad pointer"
fi

if [ "$fail" -eq 0 ]; then
  echo "doc_pointers_test.sh: $pass checks passed, 0 failed"
  exit 0
fi
echo "doc_pointers_test.sh: $pass passed, $fail FAILED"
exit 1
