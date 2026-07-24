#!/usr/bin/env bash
# debate_lib_test.sh — regression suite for tools/debate_lib.py (the debate-viewer core)
#
# The viewer's value is a faithful, live-safe read of subagent transcripts: extract
# thinking/text/tool_use/tool_result, attribute each file to a role, read incrementally
# by byte offset WITHOUT ever consuming a half-written trailing line, tolerate malformed
# lines, and discover the active session. This suite pins that contract so a future edit
# can't silently break the parse. It also smoke-tests the --once dump end to end.
#
# Same house pattern as journal-report_test.sh: bash + python3 only, no framework, all
# fixtures under one mktemp dir removed on exit. Never touches real session data.
#
# Run from anywhere:  bash tools/tests/debate_lib_test.sh
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

# pyt <name> <python-body>: run a python assertion snippet; PASS if it exits 0.
pyt() {
	local name="$1" body="$2" out
	if out="$("$PY" -c "$body" 2>&1)"; then
		pass "$name"
	else
		fail "$name" "$out"
	fi
}

# A realistic 3-agent-ish transcript for one agent file (verifier), with all block kinds.
make_transcript() {
	cat >"$1" <<'JSONL'
{"attributionAgent":"devil","agentId":"aXYZ","timestamp":"2026-07-23T07:00:00Z","type":"user","message":{"role":"user","content":"You are the Devil. Attack the change."}}
{"attributionAgent":"devil","agentId":"aXYZ","timestamp":"2026-07-23T07:00:01Z","type":"assistant","message":{"model":"claude-sonnet-4-5","content":[{"type":"thinking","thinking":"The escape fires on a quote but what about a newline in a filename?","signature":"sig"}]}}
{"attributionAgent":"devil","agentId":"aXYZ","timestamp":"2026-07-23T07:00:02Z","type":"assistant","message":{"model":"claude-sonnet-4-5","content":[{"type":"text","text":"Let me reproduce."},{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"git diff HEAD"}}]}}
{"agentId":"aXYZ","timestamp":"2026-07-23T07:00:03Z","type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"diff output here"}]}}
{"attributionAgent":"devil","agentId":"aXYZ","timestamp":"2026-07-23T07:00:04Z","type":"assistant","message":{"model":"claude-sonnet-4-5","content":[{"type":"text","text":"DEALBREAKER: newline in filename."}]}}
JSONL
}

# ---------------------------------------------------------------------------
# (1) parse_new extracts all four block kinds + prompt, in order
# ---------------------------------------------------------------------------
t_parse_kinds() {
	local f="$WORKDIR/agent-parse.jsonl"; make_transcript "$f"
	pyt "parse_new extracts prompt/thinking/text/tool_use/tool_result in order" "
import debate_lib as dl
ev, off = dl.parse_new('$f', 0)
kinds = [e['kind'] for e in ev]
assert kinds == ['prompt','thinking','text','tool_use','tool_result','text'], kinds
th = [e for e in ev if e['kind']=='thinking'][0]
assert 'newline in a filename' in th['text'], th
tu = [e for e in ev if e['kind']=='tool_use'][0]
assert tu['name']=='Bash' and tu['input']['command']=='git diff HEAD', tu
tr = [e for e in ev if e['kind']=='tool_result'][0]
assert tr['text']=='diff output here', tr
assert off > 0
"
}

# ---------------------------------------------------------------------------
# (2) file_role: one file -> canonical role + model (even though tool_result
#     lines carry no attributionAgent)
# ---------------------------------------------------------------------------
t_file_role() {
	local f="$WORKDIR/agent-role.jsonl"; make_transcript "$f"
	pyt "file_role returns role+model from the file's tagged lines" "
import debate_lib as dl
role, model = dl.file_role('$f')
assert role=='devil', role
assert model=='claude-sonnet-4-5', model
"
}

# ---------------------------------------------------------------------------
# (3) incremental offset: a partial (non-newline-terminated) trailing line is
#     NOT consumed until it completes
# ---------------------------------------------------------------------------
t_incremental_partial() {
	local f="$WORKDIR/agent-inc.jsonl"
	# two complete lines + one partial (no trailing newline)
	printf '%s\n' '{"attributionAgent":"angel","type":"assistant","message":{"model":"opus","content":[{"type":"text","text":"one"}]}}' >"$f"
	printf '%s\n' '{"attributionAgent":"angel","type":"assistant","message":{"model":"opus","content":[{"type":"text","text":"two"}]}}' >>"$f"
	printf '%s'   '{"attributionAgent":"angel","type":"assistant","message":{"model":"opus","content":[{"type":"text","text":"thr' >>"$f"
	pyt "parse_new leaves a partial trailing line unconsumed, then reads it once completed" "
import debate_lib as dl
ev, off = dl.parse_new('$f', 0)
texts = [e['text'] for e in ev if e['kind']=='text']
assert texts == ['one','two'], texts        # partial 'thr...' NOT yet parsed
# now finish the partial line
with open('$f','a') as fh: fh.write('ee\"}]}}\n')
ev2, off2 = dl.parse_new('$f', off)
texts2 = [e['text'] for e in ev2 if e['kind']=='text']
assert texts2 == ['three'], texts2          # only the newly-completed line
assert off2 > off
"
}

# ---------------------------------------------------------------------------
# (4) malformed lines skipped, valid ones kept; no crash
# ---------------------------------------------------------------------------
t_malformed() {
	local f="$WORKDIR/agent-bad.jsonl"
	printf '%s\n' '{"attributionAgent":"angel","type":"assistant","message":{"model":"opus","content":[{"type":"text","text":"good"}]}}' >"$f"
	printf '%s\n' 'this is not json' >>"$f"
	printf '%s\n' '{"attributionAgent":"angel","type":"assistant","message":{"model":"opus","content":[{"type":"text","text":"also good"}]}}' >>"$f"
	pyt "parse_new skips malformed lines, keeps valid ones" "
import debate_lib as dl
ev, off = dl.parse_new('$f', 0)
texts = [e['text'] for e in ev if e['kind']=='text']
assert texts == ['good','also good'], texts
"
}

# ---------------------------------------------------------------------------
# (5) missing / empty file -> ([], offset), no crash
# ---------------------------------------------------------------------------
t_missing_empty() {
	local missing="$WORKDIR/does-not-exist.jsonl"
	local empty="$WORKDIR/agent-empty.jsonl"; : >"$empty"
	pyt "parse_new tolerates missing and empty files" "
import debate_lib as dl
ev, off = dl.parse_new('$missing', 0); assert ev==[] and off==0, (ev,off)
ev, off = dl.parse_new('$empty', 0);   assert ev==[] and off==0, (ev,off)
"
}

# ---------------------------------------------------------------------------
# (6) discover_session picks the most-recently-active session's dir; agent_files
#     includes workflow-nested transcripts
# ---------------------------------------------------------------------------
t_discover_and_agentfiles() {
	local proj="$WORKDIR/proj"
	mkdir -p "$proj/sess-old/subagents" "$proj/sess-new/subagents/workflows/wf1"
	printf '%s\n' '{"attributionAgent":"angel","type":"assistant","message":{"model":"opus","content":[]}}' >"$proj/sess-old/subagents/agent-old.jsonl"
	sleep 1.1
	printf '%s\n' '{"attributionAgent":"devil","type":"assistant","message":{"model":"sonnet","content":[]}}' >"$proj/sess-new/subagents/agent-new.jsonl"
	printf '%s\n' '{"attributionAgent":"angel","type":"assistant","message":{"model":"opus","content":[]}}' >"$proj/sess-new/subagents/workflows/wf1/agent-wf.jsonl"
	pyt "discover_session -> newest; agent_files includes workflow subdir" "
import os, debate_lib as dl
sess = dl.discover_session('$proj')
assert os.path.basename(sess)=='sess-new', sess
files = dl.agent_files(os.path.join(sess,'subagents'))
base = sorted(os.path.basename(f) for f in files)
assert base == ['agent-new.jsonl','agent-wf.jsonl'], base
"
}

# ---------------------------------------------------------------------------
# (7) project_slug matches Claude Code's path->slug convention
# ---------------------------------------------------------------------------
t_slug() {
	pyt "project_slug replaces path separators with dashes" "
import debate_lib as dl
assert dl.project_slug('/home/u/proj') == '-home-u-proj', dl.project_slug('/home/u/proj')
"
}

# ---------------------------------------------------------------------------
# (8) status heuristic: recent mtime -> active, old -> done
# ---------------------------------------------------------------------------
t_status() {
	pyt "agent_status: recent=active, stale=done" "
import debate_lib as dl
now = 1000.0
assert dl.agent_status(now-1, now=now) == 'active'
assert dl.agent_status(now-10, now=now) == 'done'
"
}

# ---------------------------------------------------------------------------
# (9) end-to-end: --once dump renders role + thinking + tool + dealbreaker text
# ---------------------------------------------------------------------------
t_once_dump() {
	local sub="$WORKDIR/sess-once/subagents"; mkdir -p "$sub"
	make_transcript "$sub/agent-XYZ.jsonl"
	local out
	out="$("$PY" "$TOOLS_DIR/debate_view.py" --once --subagents "$sub" 2>&1)"
	if ! grep -q "devil" <<<"$out"; then fail "--once dump: role shown" "$out"; return; fi
	if ! grep -q "newline in a filename" <<<"$out"; then fail "--once dump: thinking shown" "$out"; return; fi
	if ! grep -q "Bash" <<<"$out"; then fail "--once dump: tool call shown" "$out"; return; fi
	if ! grep -q "DEALBREAKER" <<<"$out"; then fail "--once dump: output shown" "$out"; return; fi
	pass "--once dump renders role + thinking + tool + output end to end"
}

# ---------------------------------------------------------------------------
# (10) markdown-lite rendering: inline parse, line classify, strip, wrap
# ---------------------------------------------------------------------------
t_markdown() {
	pyt "parse_inline splits bold/code and strips single-* emphasis" "
import debate_lib as dl
segs = dl.parse_inline('a **bold** and \`code\` and *em* end')
# no literal markdown markers survive in the rendered text
txt = ''.join(t for t,_ in segs)
assert '**' not in txt and '\`' not in txt, txt
assert ('bold','b') in segs, segs
assert ('code','c') in segs, segs
assert 'em' in txt and '*em*' not in txt, txt   # single-* stripped, word kept
"
	pyt "classify_line detects header/bullet/quote/fence, strips marker" "
import debate_lib as dl
assert dl.classify_line('## Verdict')[:2] == ('header','Verdict')
assert dl.classify_line('- point one')[:2] == ('bullet','point one')
assert dl.classify_line('  * nested')[0] == 'bullet'
assert dl.classify_line('> quoted')[:2] == ('quote','quoted')
assert dl.classify_line('\`\`\`python')[0] == 'fence'
assert dl.classify_line('plain text')[:2] == ('normal','plain text')
"
	pyt "only angel/devil carry an emoji; all other chrome is plain text" "
import debate_lib as dl
assert dl.role_emoji('angel') == '😇' and dl.role_emoji('devil') == '😈'
# every other role, and unknown roles, render with no glyph
for r in ('verifier','red-teamer','interpreter','researcher','profiler','historian',
          'scribe','test-writer','tldr','arbiter','nonesuch'):
    assert dl.role_emoji(r) == '', (r, dl.role_emoji(r))
# section headers + activity labels are plain (no emoji code points)
assert dl.sec_head('thinking') == 'thinking' and dl.sec_head('tool') == 'tool'
def has_emoji(s): return any(ord(c) >= 0x1F000 or 0x2600 <= ord(c) <= 0x27BF or 0xFE00 <= ord(c) <= 0xFE0F for c in s)
assert not has_emoji(dl.activity_label('thinking'))
# a devil dump shows 😈 but no other chrome emoji (thinking/output/tool markers gone)
agents=[{'role':'devil','model':'m','events':[{'kind':'thinking','text':'x'},{'kind':'tool_use','name':'Bash','input':{}}]}]
dump = dl.render_dump(agents)
assert '😈' in dump
assert '💭' not in dump and '🔧' not in dump and '📣' not in dump, repr(dump)
"
	pyt "strip_inline_md removes all inline markers" "
import debate_lib as dl
assert dl.strip_inline_md('**DEALBREAKER**: the \`fn\` breaks') == 'DEALBREAKER: the fn breaks'
"
	pyt "pad_display: fields reach the same DISPLAY width with or without an emoji (columns line up)" "
import debate_lib as dl
a = dl.pad_display('angel 😇', 13)      # name + 2-col emoji
b = dl.pad_display('red-teamer', 13)     # plain name
assert dl.display_width(a) == 13 and dl.display_width(b) == 13, (dl.display_width(a), dl.display_width(b))
assert dl.pad_display('verylongmodelname-that-overflows', 8) == 'verylong'  # clips, never over-wide
"
	pyt "display_width: emoji=2, variation-selector=0, ascii=1 (aligns 🛡️ with ✅)" "
import debate_lib as dl
assert dl.char_width(ord('a')) == 1
assert dl.display_width('abc') == 3
assert dl.display_width('✅') == 2                       # single-codepoint emoji
assert dl.display_width('🛡️') == 2                       # shield + U+FE0F selector -> 2, not 3
assert dl.display_width('🛡️') == dl.display_width('✅')   # the two roster glyphs align
assert dl.clip_to_width('🛡️xy', 2) == '🛡️'              # never splits a wide char; keeps selector
"
	pyt "wrap_segments wraps to width, preserves style, applies indent" "
import debate_lib as dl
rows = dl.wrap_segments([('one two three four five', 'plain')], 12, indent=2)
assert len(rows) >= 2, rows
for r in rows:
    assert r[0] == ('  ','plain'), r          # indent prefix on every row
    width = sum(len(t) for t,_ in r)
    assert width <= 12, (width, r)
styled = dl.wrap_segments([('keep','b')], 40)
assert styled[0][-1] == ('keep','b'), styled  # style survives wrapping
"
}

echo "debate_lib.py regression suite"
echo "  target : $TOOLS_DIR/debate_lib.py"
echo "  tmpdir : $WORKDIR"
echo

t_parse_kinds
t_file_role
t_incremental_partial
t_malformed
t_missing_empty
t_discover_and_agentfiles
t_slug
t_status
t_once_dump
t_markdown

echo
echo "-------------------------------------------"
printf 'total: %d   passed: %d   failed: %d\n' "$((PASS + FAIL))" "$PASS" "$FAIL"

[ "$FAIL" -eq 0 ]
