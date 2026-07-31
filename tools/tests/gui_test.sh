#!/usr/bin/env bash
# gui_test.sh — regression suite for the browser GUI's page assembly (tools/debate_gui.py).
#
# WHY THIS EXISTS. The GUI's 834 lines of JavaScript used to live inside a Python string, where
# nothing could parse it: self-check.sh runs bash suites, py_compile only proves the *string* is a
# valid string, and a deleted brace inside it shipped a green ALL GREEN over a syntactically dead
# page (reproduced on a clone during the 2026-07-30 structural debate). The journal even recorded
# that gap as `resolved` by a "verify gate" that had never landed. The JS now lives in a real
# sibling file spliced into the shell at import time, and this suite is the gate that was claimed.
#
# Five properties are pinned:
#
#   (1) PARSE GUARD — tools/debate_gui.js must parse. This is the whole point of the extraction:
#       errors now report a true file:line instead of an offset into a string literal.
#   (2) ASSETS TRACKED — every sidecar in _ASSETS must exist AND be tracked by git. install.sh
#       copies the WORKING TREE, so a forgotten `git add` would leave a working local install and
#       a broken clone. Import-time failure is loud but only fires for whoever cloned it; this
#       fires for whoever forgot.
#   (3) SPLICE FAILS LOUD — a missing placeholder or a missing asset must RAISE at import, never
#       degrade to a half-built page. Checked on throwaway copies; the repo is never mutated.
#   (4) NO TAG BREAKOUT — the JS must not contain `</script`, which would close the tag early and
#       spill the remainder into the document as markup.
#   (5) ONE SERVED DOCUMENT — the assembled PAGE must actually contain the JS. This pins the
#       property the debate turned on: debate_lib_test.sh asserts `innerHTML not in page` against
#       the served body, so if the JS is ever moved out to a `<script src>` route that XSS guard
#       silently becomes unable to fire. This test fails first, and says why.
#
# House pattern from workflow_test.sh: bash + python3 (+ node for (1) only), no framework, all
# fixtures under one mktemp dir removed on exit.
#
# Run from anywhere:  bash tools/tests/gui_test.sh
# Exit 0 iff every test that RAN passed. Check (1) needs a JS parser and is skipped when none is
# installed — the summary reports the skip and the expected total, so a vacuous run cannot read as a
# clean one. tools/self-check.sh counts those skips and withholds its green line on the same basis.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$TOOLS_DIR/.." && pwd)"
NODE="${NODE_BIN:-node}"
PY="${PYTHON_BIN:-python3}"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

PASS=0
FAIL=0
SKIPPED=0
pass() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
# skip() counts. It used to increment nothing, so a skipped check left BOTH the numerator and the
# denominator: the tally went from "8 passed" to "7 passed" with no line stating what 8 should have
# been, so there was no number left that could disagree with reality. Counting it — and printing the
# total — is what makes a missing check visible when this suite is run on its own.
skip() { SKIPPED=$((SKIPPED + 1)); printf 'SKIP  %s\n' "$1"; }

# Single summary for both exit paths, so the early abort below can never drift from the normal end.
# Lowercase "skipped" on purpose: self-check.sh counts skips by matching ^SKIP, so this line must not
# look like one to the aggregator.
summary() {
	printf '\n%d passed, %d failed, %d skipped  (%d checks total)\n' \
		"$PASS" "$FAIL" "$SKIPPED" "$((PASS + FAIL + SKIPPED))"
	[ "$SKIPPED" -gt 0 ] && printf 'NOTE  %d check(s) did not run — this suite did not verify what it claims to.\n' "$SKIPPED"
	return 0
}

# The asset list is read FROM the module, not hardcoded here, so adding a sidecar (e.g. pulling the
# CSS out too) is covered by (1)-(3) for free instead of silently escaping the gate.
mapfile -t ASSETS < <("$PY" -c "
import sys; sys.path.insert(0, '$TOOLS_DIR')
import debate_gui
for _tok, name in debate_gui._ASSETS: print(name)
" 2>"$WORKDIR/import.err")
if [ "${#ASSETS[@]}" -eq 0 ]; then
	fail "tools/debate_gui.py imports and exposes _ASSETS" "$(tail -3 "$WORKDIR/import.err")"
	summary
	exit 1
fi
pass "tools/debate_gui.py imports; ${#ASSETS[@]} sidecar asset(s) declared"

# --- (1) parse guard + (4) no tag breakout, per asset -----------------------------------------
for asset in "${ASSETS[@]}"; do
	path="$TOOLS_DIR/$asset"

	# (2) exists and is tracked by git — the forgotten-`git add` trap.
	if [ ! -f "$path" ]; then
		fail "$asset exists"
		continue
	fi
	pass "$asset exists"
	if git -C "$ROOT" ls-files --error-unmatch "tools/$asset" >/dev/null 2>&1; then
		pass "$asset is tracked by git (install.sh copies the working tree)"
	else
		fail "$asset is tracked by git" "run: git add tools/$asset — an untracked sidecar installs locally but ships a broken clone"
	fi

	# (4) tag breakout.
	if grep -qF '</script' "$path"; then
		fail "$asset contains no '</script' (would close the tag early)"
	else
		pass "$asset contains no '</script' (would close the tag early)"
	fi

	# (1) parse. Only the .js assets, and only when a parser is present.
	case "$asset" in
	*.js)
		if ! command -v "$NODE" >/dev/null 2>&1; then
			skip "$asset parses — $NODE not found; a JS parser is required to check this"
		elif out="$("$NODE" --check "$path" 2>&1)"; then
			pass "$asset parses ($NODE --check)"
		else
			fail "$asset parses ($NODE --check)" "$(printf '%s' "$out" | head -3 | tr '\n' ' ')"
		fi
		;;
	esac
done

# --- (5) one served document: the assembled PAGE really contains the JS ------------------------
"$PY" - "$TOOLS_DIR" <<'PY' >"$WORKDIR/spliced.out" 2>&1
import io, os, sys
tools = sys.argv[1]
sys.path.insert(0, tools)
import debate_gui
page = debate_gui.PAGE
ok = True
for _tok, name in debate_gui._ASSETS:
    body = io.open(os.path.join(tools, name), encoding="utf-8").read()
    if body not in page:
        print("MISSING %s (%d bytes) from the assembled page" % (name, len(body)))
        ok = False
for tok, _name in debate_gui._ASSETS:
    if tok in page:
        print("UNSUBSTITUTED placeholder %s survived into the page" % tok)
        ok = False
print("OK" if ok else "BAD", len(page.encode("utf-8")))
PY
read -r verdict pagelen < <(tail -1 "$WORKDIR/spliced.out")
if [ "$verdict" = "OK" ]; then
	pass "assembled PAGE inlines every asset ($pagelen bytes, one served document)"
else
	fail "assembled PAGE inlines every asset" "$(head -3 "$WORKDIR/spliced.out" | tr '\n' ' ')"
fi

# --- (3) splice fails loud, on throwaway copies ------------------------------------------------
# Two independent failure modes, each on its own copy of tools/ so the repo is never touched.
splice_must_raise() { # <case-name> <mutation-shell-snippet> <expected-substring>
	local name="$1" mutate="$2" want="$3"
	local sandbox="$WORKDIR/$name"
	mkdir -p "$sandbox"
	cp "$TOOLS_DIR"/*.py "$sandbox/" 2>/dev/null
	for a in "${ASSETS[@]}"; do cp "$TOOLS_DIR/$a" "$sandbox/" 2>/dev/null; done
	(cd "$sandbox" && eval "$mutate")
	local out
	out="$("$PY" -c "
import sys; sys.path.insert(0, '$sandbox')
import debate_gui
print('NO-RAISE: import succeeded with a broken splice')
" 2>&1)"
	if printf '%s' "$out" | grep -qF "$want"; then
		pass "splice fails loud: $name"
	else
		fail "splice fails loud: $name" "expected '$want', got: $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"
	fi
}

first_asset="${ASSETS[0]}"
splice_must_raise "missing-asset" \
	"rm -f '$first_asset'" \
	"$first_asset"
splice_must_raise "missing-placeholder" \
	"$PY - <<'EOS'
import io
s = io.open('debate_gui.py', encoding='utf-8').read()
i = s.index('_SHELL = r\"\"\"')
head, tail = s[:i], s[i:]
tail = tail.replace('@@JS@@', '', 1)
io.open('debate_gui.py', 'w', encoding='utf-8').write(head + tail)
EOS" \
	"asset placeholder"

# ---------------------------------------------------------------------------
summary
[ "$FAIL" -eq 0 ]
