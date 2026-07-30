#!/usr/bin/env bash
# workflow_test.sh — regression suite for the .claude/workflows/*.js scripts.
#
# The workflows are the only executable part of this repo with NO test coverage: they run inside
# Claude Code, not from a shell, so nothing here ever exercised them. Three things are pinned:
#
#   (1) PARSE GUARD — every workflow must still parse. These files are NOT standalone modules:
#       the runtime strips the `export const meta = {...}` block and wraps the remaining body in
#       an async function, which is why top-level `await` and `return` are legal in them. A naive
#       `node --check` on the raw file therefore dies with "Illegal return statement" — expected,
#       NOT a bug. So the check below reproduces the runtime's shape (split off meta, wrap the
#       body) before parsing. Loops the glob, so a newly added workflow is covered for free.
#   (2) META VALIDATION — the meta block is read by the workflow loader before the body ever runs,
#       so it must be a PURE literal (no variables, calls, template interpolation, spreads) and
#       must carry a usable name/description (+ a title on every phase).
#   (3) disjointGroups() in build-sweep.js — the function that guarantees no two PARALLEL builders
#       ever write the same file. If it is wrong, build-sweep is unsafe, so its partition invariant
#       is pinned here rather than trusted from its comment.
#
# Same house pattern as independence_test.sh: bash + node only, no framework, all fixtures under
# one mktemp dir removed on exit. Never modifies the workflow files.
#
# Run from anywhere:  bash tools/tests/workflow_test.sh
# Exit 0 iff every test passed.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$TOOLS_DIR/.." && pwd)"
WORKFLOWS_DIR="$ROOT/.claude/workflows"
NODE="${NODE_BIN:-node}"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }

# A JS parser is the one hard dependency here. Skipping loudly beats failing the whole self-check
# run on a box without node — but say so, never pass silently.
if ! command -v "$NODE" >/dev/null 2>&1; then
	printf 'SKIP  workflow_test.sh — %s not found; the workflow scripts need a JS parser to check\n' "$NODE"
	exit 0
fi

# ---------------------------------------------------------------------------
# Helper: split a workflow into (meta literal, blanked meta code, wrapped body)
#
# Brace-matches the meta object with a scanner that knows about strings and comments — a plain
# regex would stop at the first `}` inside a description. It also emits a "blanked" copy of the
# meta text with every string/comment interior replaced by spaces, so the purity check below can
# look for `(`, `...` and backticks in CODE only and not trip over prose (an ellipsis in a
# description is fine; a spread is not).
# ---------------------------------------------------------------------------
cat >"$WORKDIR/split.js" <<'SPLIT'
const fs = require('fs')
const [src_path, out_prefix] = process.argv.slice(2)
const src = fs.readFileSync(src_path, 'utf8')

const PREFIX = 'export const meta'
if (src.indexOf(PREFIX) !== 0) {
  console.error(`${src_path}: must begin with "${PREFIX} = {...}" (found: ${JSON.stringify(src.slice(0, 40))})`)
  process.exit(2)
}
const open = src.indexOf('{', PREFIX.length)
if (open < 0) { console.error(`${src_path}: no object literal after "${PREFIX}"`); process.exit(2) }

let depth = 0, i = open, end = -1
const blank = src.split('')   // mutated copy: string/comment interiors -> spaces
while (i < src.length) {
  const c = src[i], d = src[i + 1]
  if (c === '/' && d === '/') {                                  // line comment
    while (i < src.length && src[i] !== '\n') { blank[i] = ' '; i++ }
    continue
  }
  if (c === '/' && d === '*') {                                  // block comment
    blank[i] = blank[i + 1] = ' '; i += 2
    while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) { if (src[i] !== '\n') blank[i] = ' '; i++ }
    blank[i] = blank[i + 1] = ' '; i += 2
    continue
  }
  if (c === "'" || c === '"' || c === '`') {                     // string / template literal
    const q = c; i++
    while (i < src.length && src[i] !== q) {
      if (src[i] === '\\') { blank[i] = ' '; i++ }
      if (src[i] !== '\n') blank[i] = ' '
      i++
    }
    i++                                                          // closing quote (kept in blank)
    continue
  }
  if (c === '{' || c === '[') depth++
  if (c === '}' || c === ']') { depth--; if (depth === 0) { end = i; break } }
  i++
}
if (end < 0) { console.error(`${src_path}: unterminated meta object literal`); process.exit(2) }

const meta = src.slice(open, end + 1)
const body = src.slice(end + 1)
fs.writeFileSync(`${out_prefix}.meta.txt`, meta)
fs.writeFileSync(`${out_prefix}.metacode.txt`, blank.slice(open, end + 1).join(''))
// Reproduce the runtime's shape: the body runs inside an async function, so top-level `await`
// and `return` are legal there and must parse.
fs.writeFileSync(`${out_prefix}.body.js`, `(async () => {\n${body}\n})()\n`)
SPLIT

# Evaluates the meta literal in a bare vm context (no globals from here, no require, no process),
# so a variable reference or a call to anything non-intrinsic throws instead of silently working —
# then asserts the fields the loader depends on.
cat >"$WORKDIR/meta-check.js" <<'METACHECK'
const fs = require('fs'), vm = require('vm'), assert = require('assert')
const text = fs.readFileSync(process.argv[2], 'utf8')
const meta = vm.runInNewContext(`(${text})`, Object.create(null), { timeout: 1000 })

assert(typeof meta.name === 'string' && meta.name.trim(), `meta.name must be a non-empty string, got ${JSON.stringify(meta.name)}`)
assert(typeof meta.description === 'string' && meta.description.trim(), `meta.description must be a non-empty string, got ${JSON.stringify(meta.description)}`)
if ('phases' in meta) {
  assert(Array.isArray(meta.phases), 'meta.phases must be an array when present')
  meta.phases.forEach((p, i) => assert(p && typeof p.title === 'string' && p.title.trim(),
    `meta.phases[${i}] must have a non-empty title, got ${JSON.stringify(p)}`))
}
METACHECK

# ---------------------------------------------------------------------------
# (1) + (2) parse guard and meta validation, over every workflow in the glob
# ---------------------------------------------------------------------------
shopt -s nullglob
WORKFLOWS=("$WORKFLOWS_DIR"/*.js)
shopt -u nullglob

# Fail closed: an empty glob would otherwise "pass" the whole section by looping zero times.
if [ "${#WORKFLOWS[@]}" -eq 0 ]; then
	fail "workflows found" "no *.js under $WORKFLOWS_DIR"
else
	pass "workflows found (${#WORKFLOWS[@]})"
fi

for wf in "${WORKFLOWS[@]}"; do
	name="$(basename "$wf")"
	prefix="$WORKDIR/${name%.js}"

	if ! out="$("$NODE" "$WORKDIR/split.js" "$wf" "$prefix" 2>&1)"; then
		fail "$name: meta block splits off cleanly" "$out"
		continue
	fi
	pass "$name: starts with a brace-matched \`export const meta = {...}\`"

	# (1) the body, wrapped the way the runtime wraps it, must parse
	if out="$("$NODE" --check "$prefix.body.js" 2>&1)"; then
		pass "$name: body parses when wrapped as the runtime wraps it"
	else
		fail "$name: body parses when wrapped as the runtime wraps it" "$out"
	fi

	# (2a) purity: no calls, spreads or template literals in the meta CODE (prose is blanked out)
	if grep -Eq '\(|\.\.\.|`' "$prefix.metacode.txt"; then
		fail "$name: meta is a pure literal" "found a call/spread/template literal in the meta code: $(grep -Eon '\(|\.\.\.|`' "$prefix.metacode.txt" | head -3 | tr '\n' ' ')"
	else
		pass "$name: meta is a pure literal (no calls, spreads, template literals)"
	fi

	# (2b) fields the loader depends on, evaluated in a sandbox with no globals of ours
	if out="$("$NODE" "$WORKDIR/meta-check.js" "$prefix.meta.txt" 2>&1)"; then
		pass "$name: meta has name/description (+ a title on every phase)"
	else
		fail "$name: meta has name/description (+ a title on every phase)" "$(printf '%s' "$out" | head -4)"
	fi
done

# ---------------------------------------------------------------------------
# (3) disjointGroups() — the parallel-write safety invariant of build-sweep
# ---------------------------------------------------------------------------
BUILD_SWEEP="$WORKFLOWS_DIR/build-sweep.js"
export BUILD_SWEEP

# The function isn't importable (the file is a runtime-wrapped body, not a module), so extract its
# source and eval it. Also exports the partition checker every case below asserts: the whole point
# of the function is that its output is a PARTITION — each input task lands in exactly one group,
# and no file is claimed by two groups (two groups == two parallel builders).
cat >"$WORKDIR/dg.js" <<'DG'
const fs = require('fs'), assert = require('assert')
const src = fs.readFileSync(process.env.BUILD_SWEEP, 'utf8')
const m = src.match(/^function disjointGroups[\s\S]*?^\}/m)
if (!m) throw new Error(`disjointGroups() not found in ${process.env.BUILD_SWEEP}`)
const disjointGroups = eval(`(${m[0]})`)

function partitionViolations(units, groups) {
  const errs = []
  const owner = new Map()
  for (const g of groups) for (const f of g.files) {
    if (owner.has(f)) errs.push(`file "${f}" is claimed by two groups: "${owner.get(f)}" and "${g.task}"`)
    else owner.set(f, g.task)
  }
  const got = groups.flatMap(g => g.task.split('  AND  ')).sort()
  const want = units.map(u => String(u.task)).sort()
  if (JSON.stringify(got) !== JSON.stringify(want)) errs.push(`tasks not preserved exactly once: ${JSON.stringify(got)} vs ${JSON.stringify(want)}`)
  return errs
}
const sorted = (g) => [...g.files].sort()
module.exports = { disjointGroups, partitionViolations, sorted, assert }
DG
export DG="$WORKDIR/dg.js"

njs() {
	local name="$1" body="$2" out
	if out="$("$NODE" -e "const { disjointGroups, partitionViolations, sorted, assert } = require(process.env.DG);$body" 2>&1)"; then
		pass "$name"
	else
		fail "$name" "$(printf '%s' "$out" | head -6)"
	fi
}

# (3a) nothing shared -> nothing merged, files preserved
njs "disjointGroups: non-overlapping units pass through unchanged" "
const us = [{task:'A',files:['a.js']},{task:'B',files:['b.js']},{task:'C',files:['c.js','c2.js']}]
const gs = disjointGroups(us)
assert.strictEqual(gs.length, 3, JSON.stringify(gs))
assert.deepStrictEqual(gs.map(g => g.task), ['A','B','C'])
assert.deepStrictEqual(gs.map(g => g.files), [['a.js'],['b.js'],['c.js','c2.js']])
assert.deepStrictEqual(partitionViolations(us, gs), [])
"

# (3b) one shared file -> one serial unit, tasks joined, files unioned
njs "disjointGroups: two units sharing a file merge into one group (union of files)" "
const us = [{task:'A',files:['shared.js','a.js']},{task:'B',files:['b.js','shared.js']}]
const gs = disjointGroups(us)
assert.strictEqual(gs.length, 1, JSON.stringify(gs))
assert.strictEqual(gs[0].task, 'A  AND  B')
assert.deepStrictEqual(sorted(gs[0]), ['a.js','b.js','shared.js'])
assert.deepStrictEqual(partitionViolations(us, gs), [])
"

# (3c) the chain the function's own comment hedges about ('collapses correctly as long as the
#      shared files transit'): A[x] ~ B[x,y] ~ C[y] — A and C share nothing directly, B bridges.
njs "disjointGroups: chain A[x]~B[x,y]~C[y] collapses to ONE group" "
const us = [{task:'A',files:['x']},{task:'B',files:['x','y']},{task:'C',files:['y']}]
const gs = disjointGroups(us)
assert.strictEqual(gs.length, 1, JSON.stringify(gs))
assert.strictEqual(gs[0].task, 'A  AND  B  AND  C')
assert.deepStrictEqual(sorted(gs[0]), ['x','y'])
assert.deepStrictEqual(partitionViolations(us, gs), [])
"

# (3d) a unit with no files must not crash, and must not get swallowed by an unrelated group
njs "disjointGroups: empty/missing files neither crash nor merge into an unrelated group" "
const us = [{task:'A',files:['a.js']},{task:'E',files:[]},{task:'M'}]
const gs = disjointGroups(us)
assert.strictEqual(gs.length, 3, JSON.stringify(gs))
assert.deepStrictEqual(gs.map(g => g.task), ['A','E','M'])
assert.deepStrictEqual(gs.map(g => g.files), [['a.js'],[],[]])
assert.deepStrictEqual(partitionViolations(us, gs), [])
"

# (3e) THE INVARIANT, order-independently. Same chain as (3c) with the BRIDGING unit arriving
#      LAST: A[x], C[y], then B[x,y]. B merges into the FIRST group it hits (A's) and C's group is
#      left behind still owning y — so y ends up in two groups, i.e. two PARALLEL builders are
#      handed the same file. This is the exact race the function exists to prevent.
#      KNOWN FAILURE — a real bug in .claude/workflows/build-sweep.js, not a bad test. Do not
#      weaken this assertion; fix disjointGroups() to keep merging until no group shares a file
#      (or union-find), then this goes green.
njs "disjointGroups: output is a partition regardless of unit order (bridging unit last)" "
const us = [{task:'A',files:['x']},{task:'C',files:['y']},{task:'B',files:['x','y']}]
const gs = disjointGroups(us)
assert.deepStrictEqual(partitionViolations(us, gs), [], 'DISJOINT INVARIANT BROKEN: ' + JSON.stringify(gs))
assert.strictEqual(gs.length, 1, JSON.stringify(gs))
"

# ---------------------------------------------------------------------------
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
