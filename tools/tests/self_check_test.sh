#!/usr/bin/env bash
# self_check_test.sh — pin the aggregator's SKIP accounting in tools/self-check.sh.
#
# self-check.sh is the script that answers "is the workflow's own machinery still honest?", so it is
# the one script whose dishonesty is self-concealing. It had exactly that bug: it captured each
# suite's stdout into $out and discarded it on the success branch, so the two suites that SKIP their
# `node --check` parse on a box without a JS parser vanished into a green "✅ ALL GREEN — 13/13".
# That is the same failure bb1ae1e was written to remove, one layer up.
#
# The fix is only worth as much as this test, so this pins the four properties that matter:
#   (1) a skipped check is surfaced, counted, and NOT reported as green;
#   (2) the ^SKIP match is anchored — suites that merely discuss skipping don't inflate the count;
#   (3) --strict turns "could not verify" into a non-zero exit, and only then;
#   (4) a real failure still fails, and still outranks a skip in the summary.
#
# RECURSION. self-check.sh auto-enrols every tools/tests/*_test.sh via nullglob, so this file is in
# its own subject's input set: naively invoking self-check.sh here is self-check → this test →
# self-check → … forever. $ANGEL_ADVOC_TESTS_DIR is what makes the test possible at all — the child
# globs a temp dir instead, so it cannot re-enter this file. The recursion is broken by construction,
# not by a flag someone can forget. Test (0) asserts that redirection actually happened, because if
# it ever silently stopped working this suite would fork-bomb the machine rather than fail.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF_CHECK="$(cd "$SCRIPT_DIR/../.." && pwd)/tools/self-check.sh"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }

# make_suite <dir> <name> <exit-code> <line>...  -> a fake *_test.sh the aggregator will glob
make_suite() {
	local dir="$1" name="$2" rc="$3"; shift 3
	{
		printf '#!/usr/bin/env bash\n'
		local l
		for l in "$@"; do printf 'printf "%%s\\n" %q\n' "$l"; done
		printf 'exit %s\n' "$rc"
	} >"$dir/$name"
	chmod +x "$dir/$name"
}

# run_scoped <dir> [args...] -> OUT / RC from self-check.sh scoped to <dir>
run_scoped() {
	local dir="$1"; shift
	OUT="$(ANGEL_ADVOC_TESTS_DIR="$dir" bash "$SELF_CHECK" "$@" 2>&1)"
	RC=$?
}

# ---------------------------------------------------------------------------
# (0) the scoping actually redirects the glob — the guard that keeps this file
#     from recursing into the script it tests
# ---------------------------------------------------------------------------
t_scoping_redirects() {
	local d="$WORKDIR/scope"; mkdir -p "$d"
	make_suite "$d" "only_test.sh" 0 "PASS  the only suite that should run"
	run_scoped "$d"
	if grep -q "self_check_test.sh" <<<"$OUT"; then
		fail "scoping: child must NOT re-enter this test file (recursion risk)" "$OUT"; return
	fi
	if grep -q "journal_test.sh" <<<"$OUT"; then
		fail "scoping: child ran the repo's real tests dir, not the temp dir" "$OUT"; return
	fi
	grep -q "only_test.sh" <<<"$OUT" || { fail "scoping: temp suite did not run" "$OUT"; return; }
	pass "ANGEL_ADVOC_TESTS_DIR redirects the glob (no recursion, real tests dir untouched)"
}

# ---------------------------------------------------------------------------
# (1) THE REGRESSION. A suite that exits 0 while printing SKIP must not be
#     reported as green — this is the exact bug: node absent -> "ALL GREEN".
# ---------------------------------------------------------------------------
t_skip_not_green() {
	local d="$WORKDIR/skip"; mkdir -p "$d"
	make_suite "$d" "ok_test.sh" 0 "PASS  something real"
	make_suite "$d" "skipper_test.sh" 0 "SKIP  parses — node not found; a JS parser is required" "1 passed, 0 failed"
	run_scoped "$d"

	if grep -q "ALL GREEN" <<<"$OUT"; then
		fail "skip must not be green: summary said ALL GREEN over a skipped check" "$OUT"; return
	fi
	grep -q "1 check(s) SKIPPED" <<<"$OUT" || { fail "skip counted: expected '1 check(s) SKIPPED'" "$OUT"; return; }
	grep -q "verification INCOMPLETE" <<<"$OUT" || { fail "skip labelled INCOMPLETE" "$OUT"; return; }
	# the SKIP line itself must reach the user — burying it was the original defect
	grep -q "a JS parser is required" <<<"$OUT" || { fail "skip reason surfaced to the user" "$OUT"; return; }
	# nothing failed, so the default exit policy stays 0
	[ "$RC" -eq 0 ] || { fail "skip alone exits 0 without --strict" "rc=$RC"; return; }
	pass "a skipped check is surfaced, counted, and NOT reported as ALL GREEN"
}

# ---------------------------------------------------------------------------
# (2) ANCHORING. Suites in this repo legitimately print the word "skip" mid-line
#     (journal-report_test asserts on `skip-noted` gates, debate_lib_test on
#     skipped malformed lines). An unanchored/case-insensitive match invents
#     skips that never happened — the same lie, inverted. Measured before the
#     fix: 3 phantom hits across a clean run, 0 when anchored to ^SKIP.
# ---------------------------------------------------------------------------
t_anchored_no_false_positives() {
	local d="$WORKDIR/anchor"; mkdir -p "$d"
	make_suite "$d" "talky_test.sh" 0 \
		"PASS  under-firing: skip / skip-noted gates counted (2)" \
		"PASS  malformed lines skipped, not fatal" \
		"PASS  the word SKIP appears mid-line here but not at the start"
	run_scoped "$d"

	grep -q "ALL GREEN" <<<"$OUT" || { fail "anchoring: a clean suite must stay green" "$OUT"; return; }
	grep -q "0 skipped" <<<"$OUT" || { fail "anchoring: expected '0 skipped'" "$OUT"; return; }
	if grep -q "SKIPPED" <<<"$OUT"; then
		fail "anchoring: mid-line 'skip'/'SKIP' wrongly counted as a skipped check" "$OUT"; return
	fi
	pass "^SKIP is anchored: suites that merely discuss skipping don't inflate the count"
}

# ---------------------------------------------------------------------------
# (3) --strict escalates "could not verify" to a non-zero exit, and does so ONLY
#     when something was actually skipped.
# ---------------------------------------------------------------------------
t_strict() {
	local d="$WORKDIR/strict"; mkdir -p "$d"
	make_suite "$d" "skipper_test.sh" 0 "SKIP  no parser available"
	run_scoped "$d" --strict
	[ "$RC" -ne 0 ] || { fail "--strict: skip must exit non-zero" "rc=0"; return; }
	grep -q -- "--strict: exiting non-zero" <<<"$OUT" || { fail "--strict: says why it failed" "$OUT"; return; }

	local c="$WORKDIR/strict_clean"; mkdir -p "$c"
	make_suite "$c" "ok_test.sh" 0 "PASS  all good"
	run_scoped "$c" --strict
	[ "$RC" -eq 0 ] || { fail "--strict: a clean run must still exit 0" "rc=$RC"; return; }
	grep -q "ALL GREEN" <<<"$OUT" || { fail "--strict: clean run still green" "$OUT"; return; }
	pass "--strict exits non-zero on a skip, and only on a skip"
}

# ---------------------------------------------------------------------------
# (4) a genuine FAILURE still fails, and outranks a skip in the summary — the
#     new middle state must not swallow a red one.
# ---------------------------------------------------------------------------
t_failure_still_fails() {
	local d="$WORKDIR/failing"; mkdir -p "$d"
	make_suite "$d" "broken_test.sh" 1 "FAIL  something genuinely broke"
	make_suite "$d" "skipper_test.sh" 0 "SKIP  and something was skipped too"
	run_scoped "$d"
	[ "$RC" -ne 0 ] || { fail "failure: must exit non-zero" "rc=0"; return; }
	grep -q "checks FAILED" <<<"$OUT" || { fail "failure: summary reports FAILED" "$OUT"; return; }
	if grep -q "verification INCOMPLETE" <<<"$OUT"; then
		fail "failure: a red run must not be downgraded to the amber skip state" "$OUT"; return
	fi
	pass "a real failure still fails and outranks a concurrent skip"
}

# ---------------------------------------------------------------------------
# (5) SUITE-LEVEL DENOMINATOR, on the real gui_test.sh. The aggregator fix above
#     stops "ALL GREEN" hiding a skip, but a suite run STANDALONE has its own
#     tally, and gui_test's used to omit skips from both the numerator and the
#     denominator — "8 passed" quietly became "7 passed" with nothing on screen
#     stating that 8 was expected, so no number was left that could disagree.
#     Asserted by ARITHMETIC (declared total == passed+failed+skipped), never
#     against a hardcoded 8, so adding a check to gui_test doesn't break this.
# ---------------------------------------------------------------------------
t_suite_declares_its_denominator() {
	local gui="$SCRIPT_DIR/gui_test.sh"
	[ -f "$gui" ] || { fail "denominator: gui_test.sh not found" "$gui"; return; }
	local out line p f s tot
	out="$(NODE_BIN=/nonexistent-node bash "$gui" 2>&1)"
	line="$(grep -E '^[0-9]+ passed, [0-9]+ failed, [0-9]+ skipped' <<<"$out" | tail -1)"
	if [ -z "$line" ]; then
		fail "denominator: summary must report passed/failed/skipped" "$(tail -2 <<<"$out")"; return
	fi
	read -r p _ f _ s _ <<<"$line"
	tot="$(sed -E 's/.*\(([0-9]+) checks total\).*/\1/' <<<"$line")"
	if ! [[ "$tot" =~ ^[0-9]+$ ]]; then
		fail "denominator: summary must declare the expected total" "$line"; return
	fi
	[ "$s" -ge 1 ] || { fail "denominator: the skipped check must be COUNTED, not dropped" "$line"; return; }
	[ "$tot" -eq "$((p + f + s))" ] || {
		fail "denominator: declared total != passed+failed+skipped" "$line"; return; }
	grep -q "did not verify what it claims" <<<"$out" || {
		fail "denominator: a vacuous run must say so in words" "$line"; return; }
	pass "a skipping suite counts the skip and declares its own denominator ($line)"
}

echo "self-check.sh SKIP-accounting suite"
echo "  target : $SELF_CHECK"
echo "  tmpdir : $WORKDIR"
echo

t_scoping_redirects
t_skip_not_green
t_anchored_no_false_positives
t_strict
t_failure_still_fails
t_suite_declares_its_denominator

echo
echo "-------------------------------------------"
printf 'total: %d   passed: %d   failed: %d\n' "$((PASS + FAIL))" "$PASS" "$FAIL"

[ "$FAIL" -eq 0 ]
