#!/usr/bin/env bash
# journal_test.sh — regression suite for tools/journal.sh
#
# journal.sh has no other guard than this file: it validates JSON on stdin,
# injects a UTC `ts` (unless the caller supplied one), compacts to one line, and
# appends that line with a single O_APPEND write. This suite pins that contract
# so a future edit can't silently break validation, ts handling, compaction,
# corruption-safety, or the append.
#
# Zero external deps beyond bash + python3 (which journal.sh itself requires).
# No test framework is installed in this repo (no bats), so this is a plain,
# portable, self-reporting harness.
#
# Every test points ANGEL_ADVOC_JOURNAL at a per-run mktemp file, so the suite
# NEVER touches the repo's real .angel-advoc/journal.jsonl. All temp files live
# under one TMPDIR that is removed on exit.
#
# Run from anywhere:  bash tools/tests/journal_test.sh
# Exit 0 iff every test passed.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOURNAL_SH="$(cd "$SCRIPT_DIR/../.." && pwd)/tools/journal.sh"
PY="${PYTHON_BIN:-python3}"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }

# fresh_journal: echo a unique, non-existent temp journal path for a test.
fresh_journal() { mktemp -u "$WORKDIR/journal.XXXXXX.jsonl"; }

# run_journal <journal_path> <stdin>  -> runs journal.sh, sets RC (exit code),
# stderr swallowed to keep output clean (tests assert on file + RC, not stderr).
run_journal() {
	local jp="$1" input="$2"
	ANGEL_ADVOC_JOURNAL="$jp" bash "$JOURNAL_SH" >/dev/null 2>&1 <<<"$input"
	RC=$?
}

# ---------------------------------------------------------------------------
# (1) valid object -> exit 0, exactly one line, ts injected
# ---------------------------------------------------------------------------
t_valid_append() {
	local jp; jp="$(fresh_journal)"
	run_journal "$jp" '{"gate":"light","verdict":"ok"}'
	if [ "$RC" -ne 0 ]; then fail "valid append: exit 0" "got rc=$RC"; return; fi
	local n; n="$(wc -l <"$jp")"
	if [ "$n" -ne 1 ]; then fail "valid append: one line" "got $n lines"; return; fi
	if ! grep -q '"ts":"' "$jp"; then fail "valid append: ts injected" "no ts field"; return; fi
	# ts must be ISO-8601 UTC (Z-suffixed), verified by python.
	if ! "$PY" - "$jp" <<'PY'
import json, sys, datetime
obj = json.loads(open(sys.argv[1]).read())
datetime.datetime.strptime(obj["ts"], "%Y-%m-%dT%H:%M:%SZ")
assert obj["gate"] == "light" and obj["verdict"] == "ok"
PY
	then fail "valid append: ts well-formed + payload intact"; return; fi
	pass "valid append -> exit 0, one line, well-formed ts injected, payload intact"
}

# ---------------------------------------------------------------------------
# (2) caller-supplied ts is preserved, not overwritten
# ---------------------------------------------------------------------------
t_ts_preserved() {
	local jp; jp="$(fresh_journal)"
	run_journal "$jp" '{"gate":"skip","ts":"2020-01-02T03:04:05Z"}'
	if [ "$RC" -ne 0 ]; then fail "ts preserved: exit 0" "got rc=$RC"; return; fi
	local got; got="$("$PY" -c 'import json,sys;print(json.load(open(sys.argv[1]))["ts"])' "$jp")"
	if [ "$got" != "2020-01-02T03:04:05Z" ]; then
		fail "ts preserved" "got '$got'"; return
	fi
	pass "caller-supplied ts preserved (setdefault, not overwrite)"
}

# ---------------------------------------------------------------------------
# (3) non-JSON rejected: non-zero exit, NOTHING written
# ---------------------------------------------------------------------------
t_non_json_rejected() {
	local jp; jp="$(fresh_journal)"
	run_journal "$jp" 'this is not json'
	if [ "$RC" -eq 0 ]; then fail "non-JSON rejected: non-zero exit" "got rc=0"; return; fi
	if [ -e "$jp" ]; then fail "non-JSON rejected: no file created" "file exists"; return; fi
	pass "non-JSON rejected -> non-zero exit, no write"
}

# ---------------------------------------------------------------------------
# (4) JSON array rejected (must be an object)
# ---------------------------------------------------------------------------
t_array_rejected() {
	local jp; jp="$(fresh_journal)"
	run_journal "$jp" '[1,2,3]'
	if [ "$RC" -eq 0 ]; then fail "array rejected: non-zero exit" "got rc=0"; return; fi
	if [ -e "$jp" ]; then fail "array rejected: no write" "file exists"; return; fi
	pass "JSON array rejected -> non-zero exit, no write"
}

# ---------------------------------------------------------------------------
# (5) JSON scalar rejected (string / number / bool are valid JSON but not objects)
# ---------------------------------------------------------------------------
t_scalar_rejected() {
	local jp rc_all=0
	for scalar in '42' '"a string"' 'true' 'null'; do
		jp="$(fresh_journal)"
		run_journal "$jp" "$scalar"
		if [ "$RC" -eq 0 ] || [ -e "$jp" ]; then
			fail "scalar rejected ($scalar)" "rc=$RC exists=$([ -e "$jp" ] && echo y || echo n)"
			rc_all=1
		fi
	done
	[ "$rc_all" -eq 0 ] && pass "JSON scalars (number/string/bool/null) rejected -> no write"
}

# ---------------------------------------------------------------------------
# (6) empty stdin rejected
# ---------------------------------------------------------------------------
t_empty_rejected() {
	local jp; jp="$(fresh_journal)"
	ANGEL_ADVOC_JOURNAL="$jp" bash "$JOURNAL_SH" >/dev/null 2>&1 </dev/null
	RC=$?
	if [ "$RC" -eq 0 ]; then fail "empty stdin rejected: non-zero exit" "got rc=0"; return; fi
	if [ -e "$jp" ]; then fail "empty stdin rejected: no write" "file exists"; return; fi
	pass "empty stdin rejected -> non-zero exit, no write"
}

# ---------------------------------------------------------------------------
# (7) corruption-safety: bad input against a seeded non-empty journal leaves it
#     byte-for-byte identical (the core promise: a malformed entry can never
#     corrupt the log with a half-line).
# ---------------------------------------------------------------------------
t_corruption_safety() {
	local jp; jp="$(fresh_journal)"
	# Seed a valid entry first.
	run_journal "$jp" '{"gate":"light","verdict":"seed"}'
	if [ "$RC" -ne 0 ]; then fail "corruption-safety: seed write" "seed rc=$RC"; return; fi
	local before; before="$("$PY" -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$jp")"
	# Now throw several bad inputs at it.
	run_journal "$jp" 'garbage{not json'
	run_journal "$jp" '[1,2]'
	run_journal "$jp" ''
	local after; after="$("$PY" -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$jp")"
	if [ "$before" != "$after" ]; then
		fail "corruption-safety: seeded journal unchanged by bad input" "hash changed"; return
	fi
	local n; n="$(wc -l <"$jp")"
	if [ "$n" -ne 1 ]; then fail "corruption-safety: still one line" "got $n"; return; fi
	pass "corruption-safety: bad input leaves seeded journal byte-identical"
}

# ---------------------------------------------------------------------------
# (8) unicode / emoji preserved verbatim (ensure_ascii=False)
# ---------------------------------------------------------------------------
t_unicode_preserved() {
	local jp; jp="$(fresh_journal)"
	run_journal "$jp" '{"verdict":"⚖️ 决定 決定 ✅ naïve"}'
	if [ "$RC" -ne 0 ]; then fail "unicode: exit 0" "got rc=$RC"; return; fi
	if ! grep -q '⚖️ 决定 決定 ✅ naïve' "$jp"; then
		fail "unicode preserved" "emoji/CJK not found verbatim in output"; return
	fi
	# And it round-trips as JSON.
	local got; got="$("$PY" -c 'import json,sys;print(json.load(open(sys.argv[1]))["verdict"])' "$jp")"
	if [ "$got" != "⚖️ 决定 決定 ✅ naïve" ]; then fail "unicode round-trip" "got '$got'"; return; fi
	pass "unicode/emoji preserved verbatim and round-trips"
}

# ---------------------------------------------------------------------------
# (9) multi-line / pretty-printed JSON input -> single compact output line
# ---------------------------------------------------------------------------
t_pretty_compacted() {
	local jp; jp="$(fresh_journal)"
	local pretty='{
    "gate": "structural",
    "dealbreakers": [
        { "item": "x", "disposition": "resolved" }
    ]
}'
	run_journal "$jp" "$pretty"
	if [ "$RC" -ne 0 ]; then fail "pretty compacted: exit 0" "got rc=$RC"; return; fi
	local n; n="$(wc -l <"$jp")"
	if [ "$n" -ne 1 ]; then fail "pretty compacted: one line" "got $n lines"; return; fi
	# Compact form has no ", " or ": " separators.
	if grep -Eq ', |: ' "$jp"; then fail "pretty compacted: no whitespace separators" "found ', ' or ': '"; return; fi
	pass "multi-line/pretty JSON -> single compact line"
}

# ---------------------------------------------------------------------------
# (10) O_APPEND atomicity boundary.
#
# journal.sh relies on a single O_APPEND write being atomic. POSIX guarantees
# this only up to PIPE_BUF (4096 bytes on Linux) on a local filesystem; beyond
# that, concurrent writers may tear lines. Real entries are ~600 bytes, well
# under the limit. This test drives many concurrent appends at that realistic
# size and asserts NO torn lines — every line is independently valid JSON and
# the line count matches the number of writers. It does NOT prove atomicity
# above 4096 bytes (that guarantee simply does not exist; see journal.sh).
# ---------------------------------------------------------------------------
t_concurrent_no_torn_lines() {
	local jp; jp="$(fresh_journal)"
	local writers=40
	# ~600-byte payload (well under PIPE_BUF), matching observed real entries.
	local filler; filler="$("$PY" -c 'print("x"*550)')"
	local pids=()
	local i
	for ((i = 0; i < writers; i++)); do
		printf '{"gate":"light","n":%d,"pad":"%s"}' "$i" "$filler" \
			| ANGEL_ADVOC_JOURNAL="$jp" bash "$JOURNAL_SH" >/dev/null 2>&1 &
		pids+=("$!")
	done
	local ok=0 p
	for p in "${pids[@]}"; do wait "$p" || ok=1; done
	if [ "$ok" -ne 0 ]; then fail "concurrent append: all writers exit 0" "a writer failed"; return; fi
	local n; n="$(wc -l <"$jp")"
	if [ "$n" -ne "$writers" ]; then
		fail "concurrent append: line count == writers" "got $n, want $writers"; return
	fi
	# Every line must be independently valid JSON (no interleaving/torn lines).
	if ! "$PY" - "$jp" "$writers" <<'PY'
import json, sys
path, want = sys.argv[1], int(sys.argv[2])
seen = set()
with open(path, encoding="utf-8") as fh:
    for ln, raw in enumerate(fh, 1):
        obj = json.loads(raw)          # raises if a line is torn
        assert isinstance(obj, dict)
        assert len(obj["pad"]) == 550  # payload not truncated/merged
        seen.add(obj["n"])
assert len(seen) == want, f"expected {want} distinct writers, saw {len(seen)}"
PY
	then fail "concurrent append: every line is valid, whole JSON"; return; fi
	pass "concurrent append (40x ~600B): no torn lines, every line valid JSON"
}

# --- run all -----------------------------------------------------------------
echo "journal.sh regression suite"
echo "  target : $JOURNAL_SH"
echo "  tmpdir : $WORKDIR"
echo

t_valid_append
t_ts_preserved
t_non_json_rejected
t_array_rejected
t_scalar_rejected
t_empty_rejected
t_corruption_safety
t_unicode_preserved
t_pretty_compacted
t_concurrent_no_torn_lines

echo
echo "-------------------------------------------"
printf 'total: %d   passed: %d   failed: %d\n' "$((PASS + FAIL))" "$PASS" "$FAIL"

[ "$FAIL" -eq 0 ]
