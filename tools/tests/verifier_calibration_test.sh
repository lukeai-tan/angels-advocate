#!/usr/bin/env bash
# verifier_calibration_test.sh — regression suite for tools/verifier_calibration.py.
#
# The calibration harness only helps if its scoring is trustworthy: reduce a verifier's output
# to CONFORMS/FAILS correctly, and score those against the fixtures' expected outcomes. This pins
# that pure logic. The actual verifier run (spawning the subagent) is an Arbiter-driven exercise,
# not unit-tested here.
#
# Same house pattern: bash + python3, no framework. Run from anywhere:
#   bash tools/tests/verifier_calibration_test.sh   (exit 0 iff every test passed)
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

pyt "bucket_verdict reduces verifier output to CONFORMS/FAILS/UNKNOWN" "
import verifier_calibration as v
assert v.bucket_verdict('OVERALL: FAILS:2') == 'FAILS'
assert v.bucket_verdict('OVERALL: CONFORMS') == 'CONFORMS'
assert v.bucket_verdict('lots of text ... OVERALL:  **CONFORMS**') == 'CONFORMS'
# the LAST overall wins (reasoning may mention both words earlier)
assert v.bucket_verdict('might FAILS but OVERALL: CONFORMS') == 'CONFORMS'
assert v.bucket_verdict('') == 'UNKNOWN'
assert v.bucket_verdict('no verdict word here') == 'UNKNOWN'
"

pyt "load_fixtures reads the 4 real fixtures with expected outcomes" "
import verifier_calibration as v
fx = v.load_fixtures()
ids = {f['id'] for f in fx}
assert {'resolved-not-landed','accepted-silently-fixed','scope-drift','conformant-control'} <= ids, ids
byid = {f['id']: f['expected'] for f in fx}
assert byid['conformant-control'] == 'CONFORMS'
assert byid['scope-drift'] == 'FAILS'
# every fixture carries a diff + dealbreakers + rationale
for f in fx:
    assert f.get('diff') and f.get('dealbreakers') and f.get('rationale'), f['id']
"

pyt "score marks a correct run all-OK, and a rubber-stamp as not-ok" "
import verifier_calibration as v
fx = v.load_fixtures()
good = {'resolved-not-landed':'OVERALL: FAILS:1','accepted-silently-fixed':'OVERALL: FAILS:1',
        'scope-drift':'OVERALL: FAILS:2','conformant-control':'OVERALL: CONFORMS'}
rows = v.score(fx, good)
assert all(r['ok'] for r in rows), rows
# a verifier that rubber-stamps everything CONFORMS -> known-bad rows fail
stamp = {f['id']:'OVERALL: CONFORMS' for f in fx}
rows2 = v.score(fx, stamp)
bad = [r for r in rows2 if not r['ok']]
assert len(bad) == 3 and all(r['expected']=='FAILS' for r in bad), rows2
"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
