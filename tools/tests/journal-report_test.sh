#!/usr/bin/env bash
# journal-report_test.sh — regression suite for tools/journal-report.sh
#
# journal-report.sh is the READER for the decision journal (journal.sh writes it).
# Its value is trustworthy aggregation: if the counts are wrong, /gate-audit lies.
# This suite pins the reader's contract — recent-mode listing, audit-mode counting
# (rigor/gate/disposition/verifier distributions), FAILED-verification surfacing,
# recurring-dealbreaker heuristic, under-firing (skip) count, malformed-line
# tolerance, and graceful empty/missing-journal handling.
#
# Same house pattern as journal_test.sh: bash + python3 only, no framework, each
# test uses a per-run temp journal via $ANGEL_ADVOC_JOURNAL so the suite NEVER
# touches the repo's real .angel-advoc/journal.jsonl.
#
# Run from anywhere:  bash tools/tests/journal-report_test.sh
# Exit 0 iff every test passed.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORT_SH="$(cd "$SCRIPT_DIR/../.." && pwd)/tools/journal-report.sh"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }

fresh_journal() { mktemp -u "$WORKDIR/journal.XXXXXX.jsonl"; }

# run_report <journal_path> <args...> -> captures stdout in OUT, exit code in RC.
run_report() {
	local jp="$1"; shift
	OUT="$(ANGEL_ADVOC_JOURNAL="$jp" bash "$REPORT_SH" "$@" 2>/dev/null)"
	RC=$?
}

# seed_line <journal_path> <json>  -> append a raw line (bypasses journal.sh so we
# can also seed intentionally-malformed lines).
seed_line() { printf '%s\n' "$2" >>"$1"; }

# ---------------------------------------------------------------------------
# (1) empty / missing journal: exit 0, reports 0 decisions, does not crash
# ---------------------------------------------------------------------------
t_empty_ok() {
	local jp; jp="$(fresh_journal)"   # never created
	run_report "$jp" --audit
	if [ "$RC" -ne 0 ]; then fail "empty journal: exit 0" "rc=$RC"; return; fi
	if ! grep -q "0 decisions logged" <<<"$OUT"; then fail "empty journal: reports 0" "$OUT"; return; fi
	run_report "$jp"   # recent mode too
	if [ "$RC" -ne 0 ]; then fail "empty journal (recent): exit 0" "rc=$RC"; return; fi
	pass "empty/missing journal -> exit 0, '0 decisions', no crash (both modes)"
}

# ---------------------------------------------------------------------------
# (2) audit distributions: rigor, gate, disposition, verifier counts are correct
# ---------------------------------------------------------------------------
t_audit_counts() {
	local jp; jp="$(fresh_journal)"
	seed_line "$jp" '{"gate":"fork","rigor":"structural debate","verifier":"CONFORMS","dealbreakers":[{"item":"a","disposition":"resolved"},{"item":"b","disposition":"accepted"}]}'
	seed_line "$jp" '{"gate":"light","rigor":"light self-check","verifier":"n/a","dealbreakers":[{"item":"c","disposition":"resolved"}]}'
	seed_line "$jp" '{"gate":"fork","rigor":"structural debate","verifier":"FAILS:1","dealbreakers":[]}'
	run_report "$jp" --audit
	if [ "$RC" -ne 0 ]; then fail "audit counts: exit 0" "rc=$RC"; return; fi
	# 3 decisions total
	grep -q "3 decision(s)" <<<"$OUT" || { fail "audit: 3 decisions" "$OUT"; return; }
	# gate: fork 2, light 1  (assert the 'fork  2' row)
	grep -Eq 'fork[[:space:]]+2' <<<"$OUT" || { fail "audit: fork==2" "$OUT"; return; }
	grep -Eq 'light[[:space:]]+1' <<<"$OUT" || { fail "audit: light==1" "$OUT"; return; }
	# disposition: resolved 2, accepted 1
	grep -Eq 'resolved[[:space:]]+2' <<<"$OUT" || { fail "audit: resolved==2" "$OUT"; return; }
	grep -Eq 'accepted[[:space:]]+1' <<<"$OUT" || { fail "audit: accepted==1" "$OUT"; return; }
	# verifier: conforms 1, n/a 1, failed 1
	grep -Eq 'conforms[[:space:]]+1' <<<"$OUT" || { fail "audit: conforms==1" "$OUT"; return; }
	grep -Eq 'failed[[:space:]]+1' <<<"$OUT" || { fail "audit: failed==1" "$OUT"; return; }
	pass "audit distributions (gate/rigor/disposition/verifier) counted correctly"
}

# ---------------------------------------------------------------------------
# (2b) 'refuted' is its own bucket, not lumped into resolved/accepted/other.
# A disproved Devil attack filed as "accepted" reads as the Arbiter conceding a risk it
# actually killed — it inflates the accepted count and understates two-way adversarial health.
# ---------------------------------------------------------------------------
t_refuted_bucket() {
	local jp; jp="$(fresh_journal)"
	seed_line "$jp" '{"gate":"fork","rigor":"structural debate","verifier":"CONFORMS","dealbreakers":[{"item":"a","disposition":"refuted","why":"reproduced the opposite"},{"item":"b","disposition":"accepted"}]}'
	seed_line "$jp" '{"gate":"light","rigor":"light self-check","verifier":"n/a","dealbreakers":[{"item":"c","disposition":"refuted because measured 3N+2, not 8N+5"}]}'
	run_report "$jp" --audit
	if [ "$RC" -ne 0 ]; then fail "refuted bucket: exit 0" "rc=$RC"; return; fi
	# both spellings ('refuted' and 'refuted because ...') bucket by prefix
	grep -Eq 'refuted[[:space:]]+2' <<<"$OUT" || { fail "audit: refuted==2" "$OUT"; return; }
	# and they must NOT leak into the neighbouring buckets
	grep -Eq 'accepted[[:space:]]+2' <<<"$OUT" && { fail "audit: refuted leaked into accepted" "$OUT"; return; }
	grep -Eq 'resolved[[:space:]]+[1-9]' <<<"$OUT" && { fail "audit: refuted leaked into resolved" "$OUT"; return; }
	grep -Eq 'other[[:space:]]+[1-9]' <<<"$OUT" && { fail "audit: refuted fell into 'other'" "$OUT"; return; }
	pass "audit: 'refuted' counts as its own disposition, not resolved/accepted/other"
}

# ---------------------------------------------------------------------------
# (3) FAILED-verification verdicts are surfaced by target
# ---------------------------------------------------------------------------
t_failed_surfaced() {
	local jp; jp="$(fresh_journal)"
	seed_line "$jp" '{"gate":"fork","verifier":"CONFORMS","target":"the good one"}'
	seed_line "$jp" '{"gate":"fork","verifier":"FAILS:2","target":"THE BROKEN ONE"}'
	run_report "$jp" --audit
	# under the FAILED section, the broken target must appear; the conforming one must not be in that section
	grep -q "THE BROKEN ONE" <<<"$OUT" || { fail "failed surfaced: broken target listed" "$OUT"; return; }
	# ensure the FAILED header isn't the '(none)' branch
	if grep -A2 "FAILED verification" <<<"$OUT" | grep -q "(none)"; then
		fail "failed surfaced: should not say (none)" "$OUT"; return
	fi
	pass "verdicts that FAILED verification are surfaced by target"
}

# ---------------------------------------------------------------------------
# (4) recurring-dealbreaker heuristic groups normalized-identical items
# ---------------------------------------------------------------------------
t_recurring() {
	local jp; jp="$(fresh_journal)"
	# same item, different case/whitespace -> should be grouped (count 2)
	seed_line "$jp" '{"gate":"light","dealbreakers":[{"item":"Unescaped shell interpolation","disposition":"resolved"}]}'
	seed_line "$jp" '{"gate":"light","dealbreakers":[{"item":"unescaped   shell    interpolation","disposition":"resolved"}]}'
	seed_line "$jp" '{"gate":"light","dealbreakers":[{"item":"a one-off thing","disposition":"accepted"}]}'
	run_report "$jp" --audit
	grep -Eq '2x' <<<"$OUT" || { fail "recurring: grouped count 2x" "$OUT"; return; }
	# the one-off must NOT be listed as recurring
	if grep -A5 "Recurring dealbreakers" <<<"$OUT" | grep -q "one-off"; then
		fail "recurring: one-off wrongly listed" "$OUT"; return
	fi
	pass "recurring-dealbreaker heuristic groups normalized-identical items, ignores one-offs"
}

# ---------------------------------------------------------------------------
# (5) under-firing: 'skip'/'skip-noted' gates are counted
# ---------------------------------------------------------------------------
t_skip_count() {
	local jp; jp="$(fresh_journal)"
	seed_line "$jp" '{"gate":"skip-noted","target":"noted a skip"}'
	seed_line "$jp" '{"gate":"skip","target":"another skip"}'
	seed_line "$jp" '{"gate":"fork","target":"not a skip"}'
	run_report "$jp" --audit
	grep -q "2 decision(s) logged as a noted 'skip'" <<<"$OUT" || { fail "skip count: expected 2" "$OUT"; return; }
	pass "under-firing: skip / skip-noted gates counted (2)"
}

# ---------------------------------------------------------------------------
# (6) recent mode: honors N, most-recent-last ordering
# ---------------------------------------------------------------------------
t_recent_n() {
	local jp; jp="$(fresh_journal)"
	local i
	for i in $(seq 1 15); do
		seed_line "$jp" "{\"gate\":\"light\",\"target\":\"decision $i\"}"
	done
	run_report "$jp" --recent 5
	if [ "$RC" -ne 0 ]; then fail "recent N: exit 0" "rc=$RC"; return; fi
	grep -q "last 5 of 15 decision(s)" <<<"$OUT" || { fail "recent N: header 5 of 15" "$OUT"; return; }
	grep -q "decision 15" <<<"$OUT" || { fail "recent N: includes newest (15)" "$OUT"; return; }
	grep -q "decision 11" <<<"$OUT" || { fail "recent N: includes 11 (last 5 = 11..15)" "$OUT"; return; }
	if grep -q "decision 10" <<<"$OUT"; then fail "recent N: should NOT include 10" "$OUT"; return; fi
	pass "recent mode honors N and lists the newest entries"
}

# ---------------------------------------------------------------------------
# (7) malformed lines are tolerated (skipped + noted), not fatal
# ---------------------------------------------------------------------------
t_malformed_tolerated() {
	local jp; jp="$(fresh_journal)"
	seed_line "$jp" '{"gate":"fork","target":"valid entry"}'
	seed_line "$jp" 'this is not json at all'
	seed_line "$jp" '[1,2,3]'
	run_report "$jp" --audit
	if [ "$RC" -ne 0 ]; then fail "malformed: exit 0 (tolerant)" "rc=$RC"; return; fi
	grep -q "1 decision(s)" <<<"$OUT" || { fail "malformed: 1 valid decision" "$OUT"; return; }
	grep -q "2 malformed line(s) skipped" <<<"$OUT" || { fail "malformed: notes 2 skipped" "$OUT"; return; }
	pass "malformed lines tolerated: valid entries still counted, skips noted, no crash"
}

# ---------------------------------------------------------------------------
# (8) bad flag -> non-zero exit (guards the arg parser)
# ---------------------------------------------------------------------------
t_bad_flag() {
	local jp; jp="$(fresh_journal)"
	seed_line "$jp" '{"gate":"light"}'
	run_report "$jp" --bogus
	if [ "$RC" -eq 0 ]; then fail "bad flag: non-zero exit" "rc=0"; return; fi
	pass "unknown flag -> non-zero exit"
}

# ---------------------------------------------------------------------------
# (10) acceptance-by-rigor cross-tab: counts, ratio, and a Fisher p checked
#      against a textbook value. The p is computed longhand in-script (no scipy),
#      so an untested implementation would silently print a wrong number into the
#      one place CLAUDE.md now tells readers to go for it.
# ---------------------------------------------------------------------------
t_acceptance_by_rigor() {
	local jp; jp="$(fresh_journal)"
	# light: 3 accepted of 4   structural: 1 accepted of 4  ->  2x2 = [[3,1],[1,3]]
	# That is Fisher's tea-tasting table; its two-tailed exact p is 0.4857.
	seed_line "$jp" '{"ts":"2026-07-01T00:00:00Z","gate":"light","rigor":"light self-check","target":"t","verdict":"v","dealbreakers":[{"item":"a","disposition":"accepted"},{"item":"b","disposition":"accepted"},{"item":"c","disposition":"accepted"},{"item":"d","disposition":"resolved"}],"verifier":"n/a"}'
	seed_line "$jp" '{"ts":"2026-07-02T00:00:00Z","gate":"structural","rigor":"structural debate","target":"t","verdict":"v","dealbreakers":[{"item":"e","disposition":"accepted"},{"item":"f","disposition":"resolved"},{"item":"g","disposition":"resolved"},{"item":"h","disposition":"resolved"}],"verifier":"CONFORMS"}'
	run_report "$jp" --audit

	case "$OUT" in
		*"light self-check"*"accepted 3/4 = 75.0%"*) pass "acceptance-by-rigor: light counted 3/4" ;;
		*) fail "acceptance-by-rigor: light counted 3/4" "not found in audit output" ;;
	esac
	case "$OUT" in
		*"structural debate"*"accepted 1/4 = 25.0%"*) pass "acceptance-by-rigor: structural counted 1/4" ;;
		*) fail "acceptance-by-rigor: structural counted 1/4" "not found in audit output" ;;
	esac
	# Fisher's exact, two-tailed, on [[3,1],[1,3]] = 0.4857 -> printed to 3dp.
	case "$OUT" in
		*"p = 0.486"*) pass "acceptance-by-rigor: Fisher p matches the textbook 0.4857" ;;
		*) fail "acceptance-by-rigor: Fisher p matches the textbook 0.4857" \
			"$(printf '%s\n' "$OUT" | grep -i 'fisher' || echo '(no fisher line printed)')" ;;
	esac
	# p >= .05 must be labelled null, not left for the reader to judge.
	case "$OUT" in
		*"→  null"*) pass "acceptance-by-rigor: a non-significant p is labelled null" ;;
		*) fail "acceptance-by-rigor: a non-significant p is labelled null" "no null label" ;;
	esac
}

# ---------------------------------------------------------------------------
# (11) THE BUCKETING TRAP. CLAUDE.md records this exact error being made by hand:
#      bucketing on `gate` files every "light self-check" that fired on a fork
#      under "structural" and materially changes the answer. A light entry whose
#      GATE is "fork" must still count as LIGHT.
# ---------------------------------------------------------------------------
t_acceptance_buckets_on_rigor_not_gate() {
	local jp; jp="$(fresh_journal)"
	# gate=fork but rigor=light -> a gate-bucketing implementation files this under structural.
	seed_line "$jp" '{"ts":"2026-07-03T00:00:00Z","gate":"fork","rigor":"light self-check","target":"t","verdict":"v","dealbreakers":[{"item":"a","disposition":"accepted"},{"item":"b","disposition":"accepted"}],"verifier":"n/a"}'
	seed_line "$jp" '{"ts":"2026-07-04T00:00:00Z","gate":"structural","rigor":"structural debate","target":"t","verdict":"v","dealbreakers":[{"item":"c","disposition":"resolved"},{"item":"d","disposition":"resolved"}],"verifier":"CONFORMS"}'
	run_report "$jp" --audit

	case "$OUT" in
		*"light self-check"*"accepted 2/2 = 100.0%"*)
			pass "bucketing trap: gate=fork + rigor=light counts as LIGHT" ;;
		*) fail "bucketing trap: gate=fork + rigor=light counts as LIGHT" \
			"a gate-bucketed implementation would show light as absent and structural as 2/4" ;;
	esac
	case "$OUT" in
		*"structural debate"*"accepted 0/2 = 0.0%"*)
			pass "bucketing trap: structural keeps only its own 2 dealbreakers" ;;
		*) fail "bucketing trap: structural keeps only its own 2 dealbreakers" "miscounted" ;;
	esac
	# The header must say which key it bucketed on, so the output cannot mislead a reader.
	case "$OUT" in
		*'bucketed on `rigor`, never on `gate`'*)
			pass "bucketing trap: output states which key it bucketed on" ;;
		*) fail "bucketing trap: output states which key it bucketed on" "header missing" ;;
	esac
}

echo "journal-report.sh regression suite"
echo "  target : $REPORT_SH"
echo "  tmpdir : $WORKDIR"
echo

t_empty_ok
t_audit_counts
t_refuted_bucket
t_failed_surfaced
t_recurring
t_skip_count
t_recent_n
t_malformed_tolerated
t_bad_flag
t_acceptance_by_rigor
t_acceptance_buckets_on_rigor_not_gate

echo
echo "-------------------------------------------"
printf 'total: %d   passed: %d   failed: %d\n' "$((PASS + FAIL))" "$PASS" "$FAIL"

[ "$FAIL" -eq 0 ]
