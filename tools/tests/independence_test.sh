#!/usr/bin/env bash
# independence_test.sh — regression suite for the cross-model independence checks.
#
# Two complementary guards, both pinned here:
#   * tools/debate_lib.py check_independence()/render_independence() — the POST-HOC,
#     ground-truth check: reads the ACTUAL runtime model each cross-model role ran on and
#     compares to the Arbiter's. Catches static misconfig AND availability-fallback.
#   * tools/preflight.sh — the PRE-spawn config guard: reads the model DECLARED in each
#     cross-model agent file, resolves aliases, compares to the Arbiter's. Catches static
#     misconfig only (it cannot see runtime fallback — asserted below).
#
# Same house pattern as debate_lib_test.sh: bash + python3 only, no framework, all fixtures
# under one mktemp dir removed on exit. Never touches real session/agent data.
#
# Run from anywhere:  bash tools/tests/independence_test.sh
# Exit 0 iff every test passed.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PY="${PYTHON_BIN:-python3}"
export PYTHONPATH="$TOOLS_DIR${PYTHONPATH:+:$PYTHONPATH}"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }

pyt() {
	local name="$1" body="$2" out
	if out="$("$PY" -c "$body" 2>&1)"; then
		pass "$name"
	else
		fail "$name" "$out"
	fi
}

# ---------------------------------------------------------------------------
# POST-HOC lib check — check_independence() on actual runtime models
# ---------------------------------------------------------------------------

# (1) held: a cross-model agent whose actual model differs from the Arbiter's
pyt "check_independence: differing models -> ok" "
import debate_lib as dl
agents = [{'role':'devil','model':'claude-sonnet-4-5'}, {'role':'verifier','model':'claude-sonnet-4-5'}]
r = dl.check_independence(agents, arbiter_model='claude-opus-4-8')
assert r['status']=='ok', r
assert r['checked']==2 and not any(f['collapsed'] for f in r['findings']), r
"

# (2) collapse: a cross-model agent that ACTUALLY ran on the Arbiter's model (the
#     availability-fallback case — declared sonnet, but ran opus). This is the failure
#     the preflight cannot see; the post-hoc check MUST catch it.
pyt "check_independence: cross-model agent ran on Arbiter's model -> collapse" "
import debate_lib as dl
agents = [{'role':'devil','model':'claude-opus-4-8'}, {'role':'verifier','model':'claude-sonnet-4-5'}]
r = dl.check_independence(agents, arbiter_model='claude-opus-4-8')
assert r['status']=='collapse', r
devil = [f for f in r['findings'] if f['role']=='devil'][0]
assert devil['collapsed'] is True, devil
"

# (3) infer the Arbiter's model from an inherit-role agent when not supplied
pyt "check_independence: infers Arbiter model from an inherit-role agent" "
import debate_lib as dl
agents = [{'role':'angel','model':'claude-opus-4-8'}, {'role':'devil','model':'claude-sonnet-4-5'}]
r = dl.check_independence(agents)  # no arbiter_model
assert r['status']=='ok', r
assert r['arbiter_model']=='claude-opus-4-8', r
assert r['arbiter_source']=='inferred from angel', r
"

# (4) fail-closed: no supplied model AND no inherit agent to infer from -> unverified
pyt "check_independence: undeterminable Arbiter model -> unverified (not a false pass)" "
import debate_lib as dl
agents = [{'role':'devil','model':'claude-sonnet-4-5'}]
r = dl.check_independence(agents)
assert r['status']=='unverified', r
"

# (5) nothing to check: no cross-model roles present
pyt "check_independence: no cross-model agents -> nothing-to-check" "
import debate_lib as dl
agents = [{'role':'angel','model':'claude-opus-4-8'}, {'role':'researcher','model':'claude-opus-4-8'}]
r = dl.check_independence(agents, arbiter_model='claude-opus-4-8')
assert r['status']=='nothing-to-check', r
"

# (5b) family-aware compare: same tier, DIFFERENT dated suffix -> still a collapse.
#      This is the latent bug the fork debate reproduced — an exact-string compare would
#      false-pass claude-sonnet-4-5-20250929 vs claude-sonnet-4-5-20250930 as "differs".
pyt "check_independence: same family, different dated suffix -> collapse (not a false pass)" "
import debate_lib as dl
agents = [{'role':'devil','model':'claude-sonnet-4-5-20250929'}]
r = dl.check_independence(agents, arbiter_model='claude-sonnet-4-5-20250930')
assert r['status']=='collapse', r
assert r['findings'][0]['collapsed'] is True, r
"

# (5c) family-aware compare: Haiku fallback vs Opus Arbiter -> genuinely differs (the
#      reactive-respawn escape hatch must still read as independent)
pyt "check_independence: haiku (dated) vs opus Arbiter -> ok (respawn escape hatch holds)" "
import debate_lib as dl
agents = [{'role':'verifier','model':'claude-haiku-4-5-20251001'}]
r = dl.check_independence(agents, arbiter_model='claude-opus-4-8')
assert r['status']=='ok', r
"

# (5d) model_family normalization + unknown-model fallback (never a false match)
pyt "model_family: tiers normalize; unknown ids fall back to exact string" "
import debate_lib as dl
assert dl.model_family('claude-sonnet-4-5-20250929')=='sonnet'
assert dl.model_family('claude-opus-4-8')=='opus'
assert dl.model_family('claude-haiku-4-5-20251001')=='haiku'
assert dl.model_family('some-future-model-x')=='some-future-model-x'
assert not dl.same_model('some-future-model-x','another-model-y')
assert dl.same_model('claude-sonnet-4-5','claude-sonnet-4-5-20250929')
"

# (6) render marks the collapsed role and warns
pyt "render_independence: collapse output names the role and warns of self-check" "
import debate_lib as dl
r = dl.check_independence([{'role':'devil','model':'claude-opus-4-8'}], arbiter_model='claude-opus-4-8')
s = dl.render_independence(r)
assert 'COLLAPSED' in s and 'devil' in s and 'self-check' in s, s
"

# ---------------------------------------------------------------------------
# PRE-spawn config guard — preflight.sh on declared models
# ---------------------------------------------------------------------------
PREFLIGHT="$TOOLS_DIR/preflight.sh"

# fake agents dir: declared models are frontmatter `model:` lines
mk_agents() {
	local dir="$1"; shift
	mkdir -p "$dir"
	# args come as role=model pairs
	for pair in "$@"; do
		local role="${pair%%=*}" model="${pair#*=}"
		{ echo "---"; echo "name: $role"; echo "model: $model"; echo "---"; echo "body"; } >"$dir/$role.md"
	done
}

# stable alias map so tests don't depend on the caller's env
export ANTHROPIC_DEFAULT_SONNET_MODEL="claude-sonnet-4-5"
export ANTHROPIC_DEFAULT_OPUS_MODEL="claude-opus-4-8"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="claude-haiku-4-5"

# (7) all cross-model roles declare sonnet, Arbiter opus -> ok, exit 0
run_pf() { AGENTS_DIR_OVERRIDE="$1" bash "$PREFLIGHT" "$2" >"$WORKDIR/pf.out" 2>&1; echo $?; }
D1="$WORKDIR/agents_ok"; mk_agents "$D1" devil=sonnet verifier=sonnet red-teamer=sonnet interpreter=sonnet
rc="$(run_pf "$D1" claude-opus-4-8)"
if [ "$rc" = "0" ] && grep -q "Config OK" "$WORKDIR/pf.out"; then pass "preflight: sonnet roles vs opus Arbiter -> exit 0"; else fail "preflight: ok case" "rc=$rc $(cat "$WORKDIR/pf.out")"; fi

# (8) Arbiter on sonnet -> every sonnet role collapses, exit 1
rc="$(run_pf "$D1" claude-sonnet-4-5)"
if [ "$rc" = "1" ] && grep -q "COLLAPSE" "$WORKDIR/pf.out"; then pass "preflight: sonnet roles vs sonnet Arbiter -> exit 1 collapse"; else fail "preflight: collapse case" "rc=$rc $(cat "$WORKDIR/pf.out")"; fi

# (9) alias resolution: Arbiter given as bare 'opus' compares equal to concrete opus id
rc="$(run_pf "$D1" opus)"
if [ "$rc" = "0" ]; then pass "preflight: alias 'opus' resolves like concrete id -> exit 0"; else fail "preflight: alias arbiter" "rc=$rc $(cat "$WORKDIR/pf.out")"; fi

# (10) a role declaring inherit == runs on Arbiter's model -> collapse
D2="$WORKDIR/agents_inherit"; mk_agents "$D2" devil=inherit verifier=sonnet red-teamer=sonnet interpreter=sonnet
rc="$(run_pf "$D2" claude-opus-4-8)"
if [ "$rc" = "1" ] && grep -q "devil" "$WORKDIR/pf.out"; then pass "preflight: a cross-model role set to inherit -> collapse"; else fail "preflight: inherit collapse" "rc=$rc $(cat "$WORKDIR/pf.out")"; fi

# (10b) family-aware: Arbiter given as a DATED sonnet id vs roles declaring the `sonnet`
#       alias (resolves to bare claude-sonnet-4-5) -> same family -> collapse. An exact-string
#       compare would have false-passed this; the family compare must catch it.
rc="$(run_pf "$D1" claude-sonnet-4-5-20250929)"
if [ "$rc" = "1" ] && grep -q "COLLAPSE" "$WORKDIR/pf.out"; then pass "preflight: dated-sonnet Arbiter vs sonnet-alias roles -> exit 1 (family compare)"; else fail "preflight: family suffix case" "rc=$rc $(cat "$WORKDIR/pf.out")"; fi

# (11) fail-closed: no Arbiter model arg AND no $ANTHROPIC_MODEL -> exit 2
rc="$(AGENTS_DIR_OVERRIDE="$D1" ANTHROPIC_MODEL="" bash "$PREFLIGHT" >"$WORKDIR/pf.out" 2>&1; echo $?)"
if [ "$rc" = "2" ] && grep -q "UNVERIFIED" "$WORKDIR/pf.out"; then pass "preflight: no model + no env -> exit 2 (fail-closed)"; else fail "preflight: fail-closed" "rc=$rc $(cat "$WORKDIR/pf.out")"; fi

# (12) fail-closed: cross-model agent files absent -> exit 2
D3="$WORKDIR/agents_empty"; mkdir -p "$D3"
rc="$(run_pf "$D3" claude-opus-4-8)"
if [ "$rc" = "2" ]; then pass "preflight: no cross-model agent files -> exit 2 (fail-closed)"; else fail "preflight: empty agents dir" "rc=$rc $(cat "$WORKDIR/pf.out")"; fi

# ---------------------------------------------------------------------------
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
