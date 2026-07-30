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
# (9b) snapshot(): the JSON brain shared by the browser GUI is serializable,
#      reuses the same derivations as the terminal view, and stays independent-aware
# ---------------------------------------------------------------------------
t_snapshot() {
	local sub="$WORKDIR/sess-snap/subagents"; mkdir -p "$sub"
	make_transcript "$sub/agent-XYZ.jsonl"
	pyt "snapshot(): json-serializable; reuses role/model/events/status/independence" "
import json, debate_lib as dl
snap = dl.snapshot('$sub', arbiter_model='claude-opus-4-8')
s = json.dumps(snap)                                  # must be fully serializable
snap2 = json.loads(s)
assert set(snap2) >= {'agents','independence','arbiter_model'}, list(snap2)
ag = snap2['agents']
assert len(ag) == 1 and ag[0]['role'] == 'devil', ag
a = ag[0]
assert set(a) >= {'id','role','model','status','start','end','duration_sec','usage','cost',
                  'last_kind','activity','indep','heat','tok_share','events'}, list(a)
# heat is the lib-owned bucket (0..HEAT_BUCKETS-1), NOT re-derived in the GUI:
assert snap2['heat_buckets'] == dl.HEAT_BUCKETS
assert 0 <= a['heat'] < dl.HEAT_BUCKETS, a['heat']
# this fixture carries no usage -> zero peak reads coldest (heat 0, empty share), never a crash
assert a['heat'] == 0 and a['tok_share'] == 0.0, (a['heat'], a['tok_share'])
# world-lines fields: the sonnet devil sits in a divergent attractor field vs an opus arbiter
assert a['family'] == 'sonnet', a['family']
assert snap2['attractor_fields'][0] == 'opus', snap2['attractor_fields']   # arbiter = home
assert snap2['divergence'] == 1.0, snap2['divergence']
assert a['id'] == 'agent-XYZ.jsonl', a['id']
assert a['status'] in ('active','done'), a['status']
kinds = [e['kind'] for e in a['events']]
assert kinds == ['prompt','thinking','text','tool_use','tool_result','text'], kinds
# parity fields the GUI roster renders (status/activity + independence mark):
assert a['last_kind'] == 'text', a['last_kind']          # last event drives the activity label
# activity is the human label ONLY while active; blank when done (mirrors the terminal)
assert a['activity'] == (dl.activity_label('text') if a['status']=='active' else ''), a
# a cross-model devil vs an opus arbiter is genuinely independent
assert a['indep'] == 'ok', a['indep']
# a cross-model devil vs an opus arbiter reads as independence held (not collapse)
assert snap2['independence']['status'] == 'ok', snap2['independence']
assert snap2['arbiter_model'] == 'claude-opus-4-8'
"
}

# ---------------------------------------------------------------------------
# (9b') snapshot(): two agents sharing BOTH role and model still get DISTINCT ids —
#       the fix for the GUI bug where clicking one 'angel' selected both (they keyed on
#       role+model, which is identical for e.g. two angels in a fork or two verifier passes)
# ---------------------------------------------------------------------------
t_snapshot_unique_ids() {
	local sub="$WORKDIR/sess-dup/subagents"; mkdir -p "$sub"
	# two angels, SAME role AND SAME model — the exact case that used to collide
	printf '%s\n' '{"attributionAgent":"angel","timestamp":"2026-07-28T10:00:00Z","type":"assistant","message":{"model":"claude-opus-4-8","content":[{"type":"text","text":"first angel: case FOR option A"}]}}' > "$sub/agent-aaa.jsonl"
	printf '%s\n' '{"attributionAgent":"angel","timestamp":"2026-07-28T10:00:05Z","type":"assistant","message":{"model":"claude-opus-4-8","content":[{"type":"text","text":"second angel: case FOR option B"}]}}' > "$sub/agent-bbb.jsonl"
	pyt "snapshot(): same role+model -> distinct ids + distinct events (GUI tab-collision fix)" "
import debate_lib as dl
ag = dl.snapshot('$sub')['agents']
assert len(ag) == 2, ag
assert ag[0]['role'] == ag[1]['role'] == 'angel'
assert ag[0]['model'] == ag[1]['model']              # same role AND model...
assert ag[0]['id'] != ag[1]['id'], (ag[0]['id'], ag[1]['id'])   # ...but ids are unique
assert {ag[0]['id'], ag[1]['id']} == {'agent-aaa.jsonl','agent-bbb.jsonl'}
# and each id maps to its OWN transcript, not a shared one
byid = {a['id']: a for a in ag}
assert 'option A' in byid['agent-aaa.jsonl']['events'][0]['text']
assert 'option B' in byid['agent-bbb.jsonl']['events'][0]['text']
"
}

# ---------------------------------------------------------------------------
# (9b'') snapshot(): the per-agent independence mark the GUI roster shows tracks
#        independence_state — a same-model devil collapses, an angel inherits by design
# ---------------------------------------------------------------------------
t_snapshot_indep_field() {
	local sub="$WORKDIR/sess-indep/subagents"; mkdir -p "$sub"
	# devil on the SAME model as the arbiter -> collapse; angel inherits by design
	printf '%s\n' '{"attributionAgent":"devil","timestamp":"2026-07-28T10:00:00Z","type":"assistant","message":{"model":"claude-opus-4-8","content":[{"type":"text","text":"attack"}]}}' > "$sub/agent-dev.jsonl"
	printf '%s\n' '{"attributionAgent":"angel","timestamp":"2026-07-28T10:00:01Z","type":"assistant","message":{"model":"claude-opus-4-8","content":[{"type":"text","text":"steelman"}]}}' > "$sub/agent-ang.jsonl"
	pyt "snapshot(): per-agent indep = collapse (same-model devil) / inherit (angel)" "
import debate_lib as dl
byid = {a['id']: a for a in dl.snapshot('$sub', arbiter_model='claude-opus-4-8')['agents']}
assert byid['agent-dev.jsonl']['indep'] == 'collapse', byid['agent-dev.jsonl']['indep']
assert byid['agent-ang.jsonl']['indep'] == 'inherit', byid['agent-ang.jsonl']['indep']
"
}

# ---------------------------------------------------------------------------
# (9b''') attractor_fields(): the GUI's world-lines view groups agents by model FAMILY and
#         reports a real divergence ratio. Load-bearing: the Arbiter's family must always be
#         the home field (index 0), and an undetermined Arbiter must never read as divergent.
# ---------------------------------------------------------------------------
t_attractor_fields() {
	pyt "attractor_fields: arbiter's family is home; divergence is a real ratio; fails closed" "
import debate_lib as dl
A = dl.attractor_fields
# 1 opus arbiter + 3 sonnets -> 3/4 diverged, home field first
f, d = A(['claude-opus-4-8','claude-sonnet-5','claude-sonnet-5','claude-sonnet-5'], 'claude-opus-4-8')
assert f[0] == 'opus' and set(f) == {'opus','sonnet'}, f
assert abs(d - 0.75) < 1e-9, d
# family-aware: a dated-suffix twin of the arbiter is NOT divergence
f, d = A(['claude-sonnet-4-5-20250929'], 'claude-sonnet-4-5-20250930')
assert f == ['sonnet'] and d == 0.0, (f, d)
# the arbiter's family leads even when no agent ran on it
f, d = A(['claude-sonnet-5'], 'claude-opus-5')
assert f == ['opus','sonnet'] and d == 1.0, (f, d)
# FAIL CLOSED: unknown arbiter never reports divergence (mirrors independence_state)
f, d = A(['claude-sonnet-5','claude-opus-5'], None)
assert d == 0.0, d
assert A([], 'claude-opus-5') == (['opus'], 0.0)
"
}

# ---------------------------------------------------------------------------
# (9d) secret redaction: a subagent that runs `env` writes live credentials into its
#      transcript, and BOTH viewers (plus the GUI's HTTP snapshot) render event text
#      verbatim. Redaction happens at the events_from_obj chokepoint so all three are
#      covered at once. Two load-bearing properties: real secrets die, and git SHAs live.
# ---------------------------------------------------------------------------
t_redact_secrets() {
	pyt "redact_secrets: kills credential values (incl. header-with-space) but spares SHAs" "
import debate_lib as dl
R = dl.redact_secrets
# --- must REDACT -----------------------------------------------------------
assert 'deadbeefcafe0123456789abcdef0123' not in R('ANTHROPIC_API_KEY=deadbeefcafe0123456789abcdef0123')
# THE REGRESSION THIS TEST EXISTS FOR: a header value is 'Name: value' — a token-only
# match stops at the header NAME and leaves the credential after the space in the clear.
h = 'ANTHROPIC_CUSTOM_HEADERS=Ocp-Apim-Subscription-Key: deadbeefcafe0123456789abcdef0123'
assert 'deadbeefcafe' not in R(h), R(h)
assert 'Ocp-Apim-Subscription-Key: sekrit99deadbeef' not in R('Ocp-Apim-Subscription-Key: sekrit99deadbeef')
for s in ['export GITHUB_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345',
          'sk-ant-api03-Zz9_abcdefghijklmnop',
          'AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIbKxDdENGbPxRfiCYEXAMPLEKEY',
          'Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345']:
    out = R(s)
    assert '«redacted' in out, (s, out)
# the placeholder keeps the length so the text still reads sensibly
assert R('ANTHROPIC_API_KEY=0123456789abcdef') == 'ANTHROPIC_API_KEY=«redacted:16c»', R('ANTHROPIC_API_KEY=0123456789abcdef')
# --- must NOT touch (false positives would corrupt the diffs the verifier reads) ------
keep = ['commit 5872276abcdef0123456789abcdef0123456789 fix the thing',
        'index a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0..0f1e2d3c 100644',
        'ANTHROPIC_BASE_URL=https://llm-api.example.com/Anthropic',
        'sha256: 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
        'ANTHROPIC_MODEL=claude-opus-4-8',
        # REGRESSION (caught by the verifier): 'sk-' with no left word boundary matched
        # INSIDE ordinary words — task-/disk-/risk- all end in 'sk' — silently mangling
        # exactly the kind of identifier that shows up in real infra debates.
        'task-a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8',
        'see disk-9f86d081884c7d659a2feaa0c55ad015a3bf4f1 for details',
        'risk-0123456789abcdef0123456789',
        'kiosk-abcdefghijklmnopqrstuvwxyz01']
for s in keep:
    assert R(s) == s, ('should be untouched:', s, R(s))
# ...while a REAL prefixed token at a word boundary still dies
assert '«redacted' in R('key=sk-abcdefghijklmnopqrstuvwxyz01')
assert R(None) is None and R('') == ''
"
}

# ---------------------------------------------------------------------------
# (9d') the chokepoint property: redaction must apply to EVERY event the viewers see —
#       tool_result text AND tool_use input (a secret passed as a command argument) —
#       because it is applied once in events_from_obj rather than per front-end.
# ---------------------------------------------------------------------------
t_redact_chokepoint() {
	pyt "events_from_obj redacts tool_result text AND nested tool_use input" "
import json, debate_lib as dl
SEK = 'deadbeefcafe0123456789abcdef0123'
obj = {'attributionAgent':'devil','timestamp':'2026-07-28T10:00:00Z',
       'message':{'model':'claude-sonnet-5','content':[
         {'type':'tool_result','content':'===env===\\nANTHROPIC_API_KEY=' + SEK},
         {'type':'tool_use','name':'Bash','input':{'command':'curl -H \"X-Api-Key: ' + SEK + '\" x'}},
         {'type':'text','text':'the key is ANTHROPIC_API_KEY=' + SEK},
         {'type':'thinking','thinking':'ANTHROPIC_API_KEY=' + SEK}]}}
evs = dl.events_from_obj(obj)
assert len(evs) == 4, evs
# ensure_ascii=False matters: json.dumps would escape the '«' placeholder to \\u00ab
blob = json.dumps(evs, ensure_ascii=False)
assert SEK not in blob, [e['kind'] for e in evs if SEK in json.dumps(e)]
assert blob.count('«redacted') >= 4, blob.count('«redacted')
# and the surrounding text survives — redaction must not eat the whole event
assert 'env' in evs[0]['text'] and evs[1]['input']['command'].startswith('curl')
"
}

# ---------------------------------------------------------------------------
# (9c) the GUI http server: loopback bind, fixed routes only (no path traversal),
#      snapshot JSON served, unknown paths 404 — smoke-tested over a real socket
# ---------------------------------------------------------------------------
t_gui_server() {
	local sub="$WORKDIR/sess-gui/subagents"; mkdir -p "$sub"
	make_transcript "$sub/agent-XYZ.jsonl"
	pyt "debate_gui server: loopback, fixed routes, JSON snapshot, no path traversal" "
import http.server, threading, urllib.request, json, debate_gui as g
h = g.make_handler('$sub', 'sess-gui', 'claude-opus-4-8')
srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), h)
host, port = srv.server_address
assert host == '127.0.0.1', host                      # loopback only, never 0.0.0.0
t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
try:
    base = 'http://127.0.0.1:%d' % port
    page = urllib.request.urlopen(base + '/').read().decode()
    assert '<!doctype html>' in page.lower(), page[:80]
    assert 'innerHTML' not in page, 'GUI must render agent text via textContent, not innerHTML'
    snap = json.loads(urllib.request.urlopen(base + '/snapshot.json').read())
    assert snap['label'] == 'sess-gui' and snap['agents'][0]['role'] == 'devil', snap
    # a path that would traverse must NOT read a file — fixed-route dispatch returns 404
    code = 0
    try:
        urllib.request.urlopen(base + '/../../../../etc/passwd')
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 404, code
finally:
    srv.shutdown(); srv.server_close()
"
}

# ---------------------------------------------------------------------------
# (9e) the session switcher: the client may now CHOOSE a session, which is the one thing
#      that could reintroduce path traversal. The server answers only from its OWN
#      enumeration (list_sessions), so a crafted ?session= must 404 rather than escape.
# ---------------------------------------------------------------------------
t_gui_session_switch() {
	local proj="$WORKDIR/proj-multi"
	mkdir -p "$proj/sess-aaa/subagents" "$proj/sess-bbb/subagents"
	make_transcript "$proj/sess-aaa/subagents/agent-AAA.jsonl"
	make_transcript "$proj/sess-bbb/subagents/agent-BBB.jsonl"
	# a decoy OUTSIDE the project dir that traversal would try to reach
	mkdir -p "$WORKDIR/secret-sess/subagents"
	make_transcript "$WORKDIR/secret-sess/subagents/agent-SECRET.jsonl"
	pyt "GUI session switch: allow-list only — traversal/unknown ids 404, never escape" "
import http.server, threading, urllib.request, urllib.error, urllib.parse, json, debate_gui as g, debate_lib as dl
sessions = dl.list_sessions('$proj')
assert {s['id'] for s in sessions} == {'sess-aaa','sess-bbb'}, sessions
assert all(s['agents'] == 1 for s in sessions), sessions
h = g.make_handler('$proj/sess-aaa/subagents', 'sess-aaa', 'claude-opus-4-8', '$proj')
srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), h)
t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
def get(p):
    return json.loads(urllib.request.urlopen('http://127.0.0.1:%d' % srv.server_address[1] + p).read())
def code(p):
    try:
        urllib.request.urlopen('http://127.0.0.1:%d' % srv.server_address[1] + p); return 200
    except urllib.error.HTTPError as e:
        return e.code
try:
    listed = get('/sessions.json')
    assert {s['id'] for s in listed['sessions']} == {'sess-aaa','sess-bbb'}, listed
    assert listed['current'] == 'sess-aaa'
    # switching to a legitimately enumerated session works and serves ITS agents
    assert get('/snapshot.json?session=sess-bbb')['label'] == 'sess-bbb'
    assert get('/snapshot.json')['label'] == 'sess-aaa'          # default = launch session
    # ...and anything not in the enumeration is refused, however it is spelled
    for evil in ['../secret-sess', '..%2Fsecret-sess', '/etc', 'secret-sess',
                 '$WORKDIR/secret-sess', 'sess-aaa/../../secret-sess', '']:
        c = code('/snapshot.json?session=' + urllib.parse.quote(evil, safe=''))
        assert c in (200, 404), c
        if evil:
            assert c == 404, (evil, c)
finally:
    srv.shutdown(); srv.server_close()
"
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
	pyt "roles carry emoji except red-teamer/tldr (plain), and pad_display keeps the column aligned" "
import debate_lib as dl
assert dl.role_emoji('red-teamer') == ''            # omitted: its 🛡️ selector misaligned
assert dl.role_emoji('tldr') == ''                  # omitted: its ✂️ selector misaligned (same class)
for r in ('angel','devil','arbiter','verifier','researcher','interpreter',
          'profiler','historian','scribe','test-writer'):
    assert dl.role_emoji(r), r                       # everyone else keeps a glyph
# the role column (name + optional emoji) pads to the SAME display width for every role,
# so the table columns stay aligned whether or not the role has a glyph
W = 15
def field(r):
    em = dl.role_emoji(r); return f'{r} {em}' if em else r
widths = {r: dl.display_width(dl.pad_display(field(r), W)) for r in
          ('angel','devil','red-teamer','verifier','interpreter','test-writer','tldr')}
assert set(widths.values()) == {W}, widths
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
	pyt "char_width: unicodedata fallthrough — wide->2, ambiguous honors AMBIGUOUS_WIDTH policy" "
import debate_lib as dl
# Unambiguously wide chars the explicit ranges miss still measure 2 (via east_asian_width W/F).
assert dl.char_width(ord('あ')) == 2                     # HIRAGANA A (W)
assert dl.char_width(ord('Ａ')) == 2                     # FULLWIDTH LATIN A (F)
# The roster glyph must NOT regress: scales U+2696 stays wide via the explicit range.
assert dl.char_width(0x2696) == 2
# Ambiguous-width prose (em-dash, curly quotes, ellipsis, middle dot) follows the policy knob.
amb = [ord(c) for c in '—“”…·']                          # all East_Asian_Width == 'A'
import unicodedata
assert all(unicodedata.east_asian_width(chr(c)) == 'A' for c in amb), amb
saved = dl.AMBIGUOUS_WIDTH
try:
    dl.AMBIGUOUS_WIDTH = 1
    assert all(dl.char_width(c) == 1 for c in amb), ('policy=1', amb)
    dl.AMBIGUOUS_WIDTH = 2
    assert all(dl.char_width(c) == 2 for c in amb), ('policy=2', amb)
finally:
    dl.AMBIGUOUS_WIDTH = saved
# Plain ASCII and Western-narrow still measure 1 regardless of policy.
assert dl.char_width(ord('a')) == 1 and dl.char_width(ord('é')) == 1
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
	pyt "reactive faces: every frame is exactly 5 display cols (roster FACE column never shifts)" "
import debate_lib as dl
bad = []
for role, states in dl._FACES.items():
    for state, frames in states.items():
        for fr in frames:
            w = dl.display_width(fr)
            if w != 5:
                bad.append((role, state, fr, w))
assert not bad, bad
# every real role resolves to a face set; unknown roles fall back to neutral
for role in list(dl.ROLE_EMOJI) + ['angel','devil','verifier','unknown-role', None]:
    for st in ('thinking','tool','writing','idle'):
        assert dl.display_width(dl.face(role, st, 0)) == 5, (role, st)
"
	pyt "reactive faces: glyphs are pure ASCII (ambiguous-width chars break in tmux/CJK terminals)" "
import debate_lib as dl
# display_width() cannot catch this: it counts East-Asian Ambiguous chars (·, ò, ō, ˘, …)
# as 1, but real terminals draw them as 2, overflowing the 5-col cell. ASCII (ord<128) is
# guaranteed width-1 everywhere, so this — not the display_width check above — is the guard.
bad = [(role, state, fr, c, hex(ord(c)))
       for role, states in dl._FACES.items()
       for state, frames in states.items()
       for fr in frames
       for c in fr if ord(c) > 127]
assert not bad, bad
"
	pyt "reactive faces: state mapping + animation cycles only while active" "
import debate_lib as dl
assert dl.face_state('active','tool_use') == 'tool'
assert dl.face_state('active','tool_result') == 'tool'
assert dl.face_state('active','text') == 'writing'
assert dl.face_state('active','thinking') == 'thinking'
assert dl.face_state('active','prompt') == 'thinking'   # unknown/briefed -> thinking
assert dl.face_state('done','tool_use') == 'idle'       # not active -> idle regardless
assert dl.face_state('active', None) == 'thinking'
# idle is static across frames; an active multi-frame state actually changes
assert dl.face('angel','idle',0) == dl.face('angel','idle',9)
assert dl.face('angel','thinking',0) != dl.face('angel','thinking',1)
# frame index wraps (never IndexErrors)
assert dl.face('devil','tool',1000) in dl._FACES['devil']['tool']
"
}

# ---------------------------------------------------------------------------
# (11) token usage: per-file sum, --since filter, aggregate, fmt, session map
# ---------------------------------------------------------------------------
t_tokens() {
	local f="$WORKDIR/agent-usage.jsonl"
	cat >"$f" <<'JSONL'
{"attributionAgent":"devil","timestamp":"2026-07-24T07:00:00Z","type":"assistant","message":{"model":"m","usage":{"input_tokens":10,"output_tokens":100,"cache_read_input_tokens":1000,"cache_creation_input_tokens":5},"content":[{"type":"text","text":"a"}]}}
{"agentId":"x","timestamp":"2026-07-24T07:00:01Z","type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"r"}]}}
{"attributionAgent":"devil","timestamp":"2026-07-24T08:00:00Z","type":"assistant","message":{"model":"m","usage":{"input_tokens":20,"output_tokens":200,"cache_read_input_tokens":2000,"cache_creation_input_tokens":7},"content":[{"type":"text","text":"b"}]}}
JSONL
	pyt "usage_from_file sums usage; add/total; --since filters by timestamp" "
import debate_lib as dl
u = dl.usage_from_file('$f')
assert u == {'input':30,'output':300,'cache_read':3000,'cache_create':12}, u
assert dl.usage_total(u) == 3342, dl.usage_total(u)
# --since drops the 07:00 line, keeps the 08:00 one
u2 = dl.usage_from_file('$f', since='2026-07-24T07:30:00Z')
assert u2 == {'input':20,'output':200,'cache_read':2000,'cache_create':7}, u2
assert dl.usage_from_files(['$f','$f'])['output'] == 600
"
	pyt "cluster_debates splits agents into debates by start-time gap" "
import debate_lib as dl
def A(sec): return {'events':[{'ts': f'2026-07-24T00:{sec//60:02d}:{sec%60:02d}Z'}], 'mtime':0}
# three bursts: t=0,10s | t=+7min | t=+7min again (gaps > 300s split them)
agents=[A(0),A(10),A(20), A(600),A(605), A(1200)]
cl=dl.cluster_debates(agents, gap_seconds=300)
assert [len(c) for c in cl]==[3,2,1], [len(c) for c in cl]
# newest cluster is last; agent_start orders within
assert dl.agent_start(cl[0][0]) < dl.agent_start(cl[2][0])
# tighter gap keeps them together
assert len(dl.cluster_debates(agents, gap_seconds=100000))==1
"
	pyt "fmt_tokens: compact human counts" "
import debate_lib as dl
assert dl.fmt_tokens(0)=='0' and dl.fmt_tokens(950)=='950'
assert dl.fmt_tokens(1500)=='1.5k' and dl.fmt_tokens(2_000_000)=='2.0M'
"
	pyt "fmt_tokens: never exceeds TOK_NUM_W, incl. the .1f rounding boundary" "
import debate_lib as dl
# 999_950 formats as '1000.0k' (7 cols) under a naive '< 1_000_000' check -- it must roll
# over to the next unit instead. Real agents cross this range on the way past a million.
assert dl.fmt_tokens(999_950)=='1.0M', dl.fmt_tokens(999_950)
assert dl.fmt_tokens(999_999)=='1.0M', dl.fmt_tokens(999_999)
assert dl.fmt_tokens(999_949)=='999.9k', dl.fmt_tokens(999_949)
assert dl.fmt_tokens(999_950_000)=='1.0B', dl.fmt_tokens(999_950_000)
# Sweep every unit boundary in BOTH signs: a minus sign costs a column that the .1f
# rollover threshold does not budget for, so '-999.5k' (7 cols) must promote to '-1.0M'.
assert dl.fmt_tokens(-999_499)=='-1.0M', dl.fmt_tokens(-999_499)
cases=[0,1,999,10**9,10**12,10**15,10**18]
for scale in (10**3,10**6,10**9,10**12):
    cases += [scale-1, scale, int(999.5*scale), int(999.94*scale), int(999.95*scale), 1000*scale]
cases += [-c for c in cases]
cases += list(range(999_940,1_000_060)) + [-n for n in range(999_940,1_000_060)]
bad=[(n, dl.fmt_tokens(n), dl.display_width(dl.fmt_tokens(n)))
     for n in cases if dl.display_width(dl.fmt_tokens(n)) > dl.TOK_NUM_W]
assert not bad, bad[:5]
"
	pyt "heat_bucket: in range, monotone, and safe on a zero/None peak" "
import debate_lib as dl
B = dl.HEAT_BUCKETS
assert dl.heat_bucket(0, 100)==0 and dl.heat_bucket(100, 100)==B-1
assert dl.heat_bucket(500, 100)==B-1           # clamped, never out of range
assert dl.heat_bucket(50, 0)==0 and dl.heat_bucket(50, None)==0   # nothing to compare against
assert dl.heat_bucket(-10, 100)==0             # clamped low
prev=-1
for v in range(0, 101):                        # monotone: bigger never renders cooler
    b = dl.heat_bucket(v, 100)
    assert 0 <= b < B and b >= prev, (v, b, prev)
    prev = b
assert len(dl.HEAT_RAMP_256)==B
"
	pyt "share_bar/tok_cell: exact display width for every input (TOKENS column never shifts)" "
import debate_lib as dl
bad=[]
peaks=[0, None, 1, 999_950, 1_531_420, 3_517_723, 10**12]
vals=[0, 1, 153_400, 218_200, 999_949, 999_950, 999_999, 1_100_000, 3_517_723, 10**12, -5]
for p in peaks:
    for v in vals:
        for w in (0, 1, 5, 8):
            b = dl.share_bar(v, p, w)
            if dl.display_width(b) != w: bad.append(('bar', v, p, w, b))
        c = dl.tok_cell(v, p)
        if dl.display_width(c) != dl.TOK_CELL_W: bad.append(('cell', v, p, c, dl.display_width(c)))
assert not bad, bad[:5]
# value > peak clamps to a full bar rather than overflowing the cell
assert dl.share_bar(200, 100, 5)=='█████'
# sub-cell eighths keep near neighbours distinguishable
assert dl.share_bar(153_400, 1_100_000, 5) != dl.share_bar(218_200, 1_100_000, 5)
"
	pyt "fmt_duration/fmt_cost: bounded width, incl. None and absurd magnitudes" "
import debate_lib as dl
assert dl.fmt_duration(42)=='42s' and dl.fmt_duration(202)=='3m22s'
assert dl.fmt_duration(7385)=='2h03m' and dl.fmt_duration(None)=='—'
bad=[(s,dl.fmt_duration(s)) for s in [None,0,1,59,60,61,3599,3600,86399,86400,10**7,10**12,-5]
     if dl.display_width(dl.fmt_duration(s)) > dl.DUR_W]
assert not bad, bad
assert dl.fmt_cost(None)=='—' and dl.fmt_cost(0)=='\$0.00' and dl.fmt_cost(0.004)=='<\$0.01'
bad=[(c,dl.fmt_cost(c)) for c in [None,0,1e-9,0.004,0.01,2.1449,999.994,999.999,1000,1234.5,1e6,1e12]
     if dl.display_width(dl.fmt_cost(c)) > dl.COST_W]
assert not bad, bad
"
	pyt "estimate_cost: prices by family, None for an unpriced model (never a wrong number)" "
import debate_lib as dl
u={'input':1_000_000,'output':0,'cache_read':0,'cache_create':0}
assert dl.estimate_cost(u,'claude-opus-5')==15.0        # 1M input at opus rate
assert dl.estimate_cost(u,'claude-sonnet-5')==3.0
assert dl.estimate_cost(u,'claude-haiku-4-5')==1.0
assert dl.estimate_cost(u,'some-other-llm') is None     # unpriced -> no guess
assert dl.estimate_cost(None,'claude-opus-5')==0.0
# an override table is honoured
assert dl.estimate_cost(u,'claude-opus-5',{'opus':(1.0,1.0,1.0,1.0)})==1.0
# cache tokens are priced separately, not at the input rate
u2={'input':0,'output':0,'cache_read':1_000_000,'cache_create':0}
assert dl.estimate_cost(u2,'claude-opus-5')==1.5
"
	pyt "independence_state: cross-model ok/collapse, inherit, and fail-closed unknown" "
import debate_lib as dl
S=dl.independence_state
assert S('devil','claude-sonnet-5','claude-opus-5')=='ok'
assert S('verifier','claude-opus-5','claude-opus-5')=='collapse'
# family-aware: dated twins of one tier still count as a collapse
assert S('devil','claude-sonnet-4-5-20250929','claude-sonnet-4-5-20250930')=='collapse'
assert S('angel','claude-opus-5','claude-opus-5')=='inherit'
assert S('devil','claude-sonnet-5',None)=='unknown'     # fail closed, never a false pass
assert S('arbiter','claude-opus-5','claude-opus-5')=='n/a'
# every badge glyph is width-1 so the narrow IND column stays flush ('✓' is width 2!)
for st,(g,_k) in dl.IND_MARKS.items():
    assert dl.display_width(g)==1, (st,g,dl.display_width(g))
"
	pyt "debate_line_style: severity vocabulary, and no false positives on prose" "
import debate_lib as dl
f=dl.debate_line_style
assert f('- **DEALBREAKER (reproduced)** — x')=='db' and f('SHARPEST OBJECTION: y')=='db'
assert f('- WORTH-NOTING — z')=='warn' and f('[PARTIAL — see note] q')=='warn'
assert f('[PASS] item')=='ok' and f('HONEST CONCESSION: two')=='ok'
assert f('OVERALL: CONFORMS — 1 item')=='ok'            # more specific rule wins over 'sect'
assert f('CASE AGAINST:')=='sect' and f('SCOPE DRIFT: none')=='sect'
assert f('OVERALL: things')=='sect'
# prose must not trip it: mid-sentence mentions and lookalike words
assert f('The dealbreaker was mentioned mid-sentence') is None
assert f('Failed to parse the file') is None
assert f('') is None and f(None) is None
"
	pyt "fit_roster_columns: sheds cosmetic columns first and always fits the terminal" "
import debate_lib as dl
prev=None
for maxx in range(45,201):
    cols=dl.fit_roster_columns(maxx)
    keys=[c[0] for c in cols]
    row=2+sum(c[2] for c in cols)+len(dl.COL_SEP)*(len(cols)-1)
    # load-bearing columns are never dropped
    for k in ('role','model','tokens'): assert k in keys, (maxx,keys)
    if maxx>=60: assert row<=maxx-1, (maxx,row)      # fits, once there is room at all
    # column count is monotone in width: widening never REMOVES a column
    if prev is not None: assert len(keys)>=prev, (maxx,keys)
    prev=len(keys)
# drop order: cost goes before took, took before face, face before ind
assert 'cost' in [c[0] for c in dl.fit_roster_columns(100)]
assert 'cost' not in [c[0] for c in dl.fit_roster_columns(90)]
assert 'took' not in [c[0] for c in dl.fit_roster_columns(80)]
# MODEL absorbs the slack, and never drops below its floor
for maxx in (60,80,120,200):
    mw=[c[2] for c in dl.fit_roster_columns(maxx) if c[0]=='model'][0]
    assert mw>=dl.MODEL_MIN_W, (maxx,mw)
"
	pyt "timeline_bar: exact width, always visible, clamped to the window" "
import debate_lib as dl
for w in (0,1,4,20,60):
    for a,b in ((0,10),(5,5),(0,0),(9,10),(-5,50)):
        s=dl.timeline_bar(a,b,0,10,w)
        assert dl.display_width(s)==w, (w,a,b,s,dl.display_width(s))
# a zero-length agent still paints one cell rather than vanishing
assert dl.timeline_bar(5,5,0,10,10).count('█')==1
# full span fills the strip; degenerate/None windows render blank but exact-width
assert dl.timeline_bar(0,10,0,10,10)=='██████████'
assert dl.timeline_bar(0,10,0,0,8)=='        '
assert dl.timeline_bar(None,None,0,10,8)=='        '
# a running agent is drawn with a distinct glyph
assert set(dl.timeline_bar(0,10,0,10,4,running=True))=={'░'}
"
	pyt "strip_model_prefix: drops the constant 'claude-', leaves anything else alone" "
import debate_lib as dl
assert dl.strip_model_prefix('claude-sonnet-4-5-20250929')=='sonnet-4-5-20250929'
assert dl.strip_model_prefix('claude-opus-5')=='opus-5'
assert dl.strip_model_prefix('gpt-4')=='gpt-4'        # non-claude id untouched
assert dl.strip_model_prefix('')=='' and dl.strip_model_prefix(None)==''
# the point of the strip: the longest real id now fits the 80-col MODEL column
assert dl.display_width(dl.strip_model_prefix('claude-sonnet-4-5-20250929')) <= 20
"
	# session discovery + token_report end to end over a fake project dir
	local proj="$WORKDIR/proj/-x-y"; mkdir -p "$proj/S1/subagents"
	cp "$f" "$proj/S1.jsonl"                # main transcript
	cp "$f" "$proj/S1/subagents/agent-a.jsonl"
	pyt "session_transcripts groups main + subagents by session id" "
import debate_lib as dl
s = dl.session_transcripts('$proj')
assert 'S1' in s, s
assert s['S1']['main'].endswith('S1.jsonl')
assert len(s['S1']['subagents']) == 1, s['S1']
"
	local out
	out="$("$PY" "$TOOLS_DIR/token_report.py" --project "$proj" --json 2>&1)"
	if "$PY" -c "
import json,sys
o=json.loads('''$out''')
# S1 = main(300 out) + subagent(300 out) = 600 output
assert o['total']['output']==600, o
assert o['total_tokens']==2*3342, o
" 2>/dev/null; then pass "token_report --json aggregates main + subagents"; else fail "token_report --json" "$out"; fi
}

# ---------------------------------------------------------------------------
# (12) adversarial edges for the roster/timeline/cost/severity columns.
#
# This repo has shipped the SAME width bug twice (fmt_tokens(999_950) -> '1000.0k',
# fmt_cost(999.999) -> '$1,000.00'): a `value < THRESHOLD` guard that ignored what .Nf
# rounding does at the top of the range. These tests sweep every formatter across its unit
# boundaries in both signs, pin the exact-width contract of every cell renderer under
# degenerate inputs, fuzz the responsive layout, and cover the failure paths of the price
# override, the fail-closed independence badge, and the severity classifier.
# ---------------------------------------------------------------------------
t_edges() {
	pyt "fmt_duration: no third width-budget overflow — dense sweep of every unit boundary" "
import debate_lib as dl
# Same shape of function as fmt_tokens/fmt_cost, which each shipped a rounding-boundary
# overflow. Sweep 0.01s resolution around every unit rollover, both signs, ints and floats.
cases=[None,0,1,59,60,61,3599,3600,86399,86400,10**7,10**12,10**18,-5,-10**9]
for base in (0,60,3600,86400,86400*99,86400*100):
    x=float(base)-2.0
    while x<=base+2.0:
        cases.append(round(x,3)); x+=0.01
cases+=[-c for c in cases if isinstance(c,(int,float))]
bad=[(s,dl.fmt_duration(s),dl.display_width(dl.fmt_duration(s)))
     for s in cases if dl.display_width(dl.fmt_duration(s))>dl.DUR_W]
assert not bad, bad[:5]
# It TRUNCATES rather than rounds, which is what keeps it safe where the other two were not:
# 59.9s stays '59s' instead of becoming a 7-column '60.0s'. Pin that at every rollover.
assert dl.fmt_duration(59.9)=='59s', dl.fmt_duration(59.9)
assert dl.fmt_duration(3599.7)=='59m59s', dl.fmt_duration(3599.7)
assert dl.fmt_duration(86399.9)=='23h59m', dl.fmt_duration(86399.9)
assert dl.fmt_duration(99*86400+86399.9)=='99d23h'   # widest possible output, exactly DUR_W
assert dl.fmt_duration(100*86400)=='99d+'            # beyond the format, stays bounded
# non-finite / negative inputs degrade to a bounded string instead of a wide one or a raise
assert dl.fmt_duration(float('nan'))=='0s' and dl.fmt_duration(-1)=='0s'
"
	pyt "fmt_cost: width holds across a 20k-sample fuzz, incl. the ,.2f -> ,.0f handoff" "
import random, debate_lib as dl
random.seed(7)
cs=[None,0,-1,-1e9,1e-12,0.0049,0.005,0.0099,0.01,9.999,99.999,999.994,999.995,999.999,
    1000,9999.994,9999.995,9999.999,10000,99999.4,99999.5,99999.994,100000,1e6,1e9,1e12,1e300]
for base in (1,10,100,1000,10000,100000,1e6):
    cs+=[base+d for d in (-0.011,-0.005,-0.001,0,0.001,0.005,0.011)]
cs+=[random.uniform(0,200000) for _ in range(20000)]
bad=[(c,dl.fmt_cost(c),dl.display_width(dl.fmt_cost(c)))
     for c in cs if dl.display_width(dl.fmt_cost(c))>dl.COST_W]
assert not bad, bad[:5]
# each candidate is MEASURED, so the coarser form takes over exactly where the finer one
# would overflow -- 9999.995 would render as a 10-column '\$10,000.00' under a naive threshold
assert dl.fmt_cost(999.999)=='\$1,000', dl.fmt_cost(999.999)
assert dl.fmt_cost(9999.995)=='\$10,000', dl.fmt_cost(9999.995)
assert dl.fmt_cost(99999.4)=='\$99,999' and dl.fmt_cost(99999.5)=='\$99999+'
assert dl.fmt_cost(-1)=='\$0.00'                     # a negative estimate never renders wide
"
	pyt "timeline_bar: exact width on degenerate windows, reversed spans, out-of-window agents" "
import debate_lib as dl
bad=[]
ts=[None,-1e9,-5,0,1,5,9,10,11,1e9,1e12]
windows=[(0,10),(10,0),(0,0),(5,5),(None,10),(0,None),(None,None),(-10,10),(1e9,1e9+1)]
for w in (-10,-1,0,1,2,4,5,20,60,200):
    for start in ts:
        for end in ts:
            for t0,t1 in windows:
                for run in (False,True):
                    s=dl.timeline_bar(start,end,t0,t1,w,run)
                    if dl.display_width(s)!=max(0,w):
                        bad.append((start,end,t0,t1,w,run,repr(s)))
assert not bad, bad[:5]
# a reversed window (t0>t1) and a zero-length window render blank but still EXACT width
assert dl.timeline_bar(0,10,10,0,6)=='      '
assert dl.timeline_bar(0,10,5,5,6)=='      '
# start>end must not produce a negative run count or a short string
assert dl.timeline_bar(10,0,0,10,10)=='         █'
# an agent entirely outside the window is clamped to an edge cell, never dropped
assert dl.timeline_bar(-100,-90,0,10,6)=='█     '
assert dl.timeline_bar(100,200,0,10,6)=='     █'
# a negative width degrades to empty rather than raising or producing junk
assert dl.timeline_bar(0,10,0,10,-5)==''
"
	pyt "share_bar/tok_cell: exact width on negative, None, float and absurd values" "
import debate_lib as dl
bad=[]
vals=[0,None,-1,-10**9,1,0.5,1e-9,999_949,999_950,10**12,10**18]
peaks=[0,None,-1,1,1e-9,10**12]
for v in vals:
    for p in peaks:
        for w in (-3,0,1,2,5,8,40):
            b=dl.share_bar(v,p,w)
            if dl.display_width(b)!=max(0,w): bad.append(('bar',v,p,w,repr(b)))
        c=dl.tok_cell(v,p)
        if dl.display_width(c)!=dl.TOK_CELL_W: bad.append(('cell',v,p,repr(c)))
assert not bad, bad[:5]
# a non-numeric value must not raise -- it reads as empty, not as a ragged cell
assert dl.display_width(dl.share_bar('abc',100,5))==5
# a narrower cell clips the COUNT (never the bar) and still lands on the exact width
assert dl.display_width(dl.tok_cell(999_949,10**6,5,8))==8
assert dl.tok_cell(0,0)=='           0'
"
	pyt "fit_roster_columns: drop order is exactly cost->took->face->ind->status; sets nest" "
import debate_lib as dl
# derive the drop ORDER by narrowing, rather than hard-coding the thresholds
order, prev = [], set(c[0] for c in dl.fit_roster_columns(400))
for m in range(400,0,-1):
    cur=set(c[0] for c in dl.fit_roster_columns(m))
    assert cur <= prev, (m, sorted(prev), sorted(cur))   # narrowing never ADDS a column
    order+=sorted(prev-cur)
    prev=cur
assert order==['cost','took','face','ind','status'], order
# widening never removes one either: the surviving sets are strictly nested
prev=set()
for m in range(0,400):
    cur=set(c[0] for c in dl.fit_roster_columns(m))
    assert prev <= cur, (m, sorted(prev), sorted(cur))
    prev=cur
# load-bearing columns survive at ANY width, incl. tiny/negative/huge, without raising
for m in (-100,-1,0,1,20,43,44,45,79,80,120,10**6,10**9):
    keys=[c[0] for c in dl.fit_roster_columns(m)]
    for k in ('role','model','tokens'): assert k in keys, (m,keys)
    assert [c[2] for c in dl.fit_roster_columns(m) if c[0]=='model'][0] >= dl.MODEL_MIN_W
# 44 is the narrowest terminal the minimum row fits; from there up the row is EXACTLY maxx-1
for m in range(44,400):
    cols=dl.fit_roster_columns(m)
    row=2+sum(c[2] for c in cols)+len(dl.COL_SEP)*(len(cols)-1)
    assert row<=m-1, (m,row)
    assert row==m-1, (m,row)          # MODEL absorbs all remaining slack, no dead columns
"
	pyt "load_prices: every malformed override shape falls back to the built-in table" "
import copy, os, debate_lib as dl
root='$WORKDIR/prices-root'
P=os.path.join(root,'.angel-advoc','prices.json')
BUILTIN=copy.deepcopy(dl.PRICES_PER_MTOK)
def write(s):
    with open(P,'w') as fh: fh.write(s)
for bad in ('not json at all', '', 'null', '[1,2,3]', '\"hello\"', '123',
            '{\"opus\":[1,2,3]}', '{\"opus\":[1,2,3,4,5]}', '{\"opus\":{\"in\":1}}',
            '{\"opus\":[\"a\",\"b\",\"c\",\"d\"]}', '{\"opus\":[null,1,1,1]}',
            '{\"opus\":\"abcd\"}', '{\"opus\":[]}'):
    write(bad)
    assert dl.load_prices(root)==BUILTIN, (bad, dl.load_prices(root))
os.remove(P)
assert dl.load_prices(root)==BUILTIN                 # missing file
os.mkdir(P)
assert dl.load_prices(root)==BUILTIN                 # prices.json is a DIRECTORY
os.rmdir(P)
write('{\"opus\":[1,2,3,4]}')
os.chmod(P,0o000)
if os.geteuid()!=0:
    assert dl.load_prices(root)==BUILTIN             # unreadable
os.chmod(P,0o644)
# a VALID override is honoured, is case-insensitive on the family key, may add a new family,
# and never mutates the module-level table (a stale process must not inherit it)
write('{\"OPUS\":[1,2,3,4],\"llama\":[9,9,9,9]}')
p=dl.load_prices(root)
assert p['opus']==(1.0,2.0,3.0,4.0) and p['llama']==(9.0,9.0,9.0,9.0), p
assert p['sonnet']==BUILTIN['sonnet']                # untouched families keep built-ins
assert dl.PRICES_PER_MTOK==BUILTIN, 'load_prices mutated the module-level table'
assert dl.load_prices(None)==BUILTIN and dl.load_prices(None) is not dl.PRICES_PER_MTOK
"
	pyt "load_prices: one bad row never discards the others — result is key-order independent" "
import os, debate_lib as dl
# Regression test. A wrong-TYPE row used to escape to a loop-wide except and abandon every row
# after it, so the same override content applied or didn't depending on JSON key order. The
# guard is now per-row: a bad entry is skipped, valid entries always land.
root='$WORKDIR/prices-root'
P=os.path.join(root,'.angel-advoc','prices.json')
def load(s):
    with open(P,'w') as fh: fh.write(s)
    return dl.load_prices(root)
bad_first=load('{\"opus\":[\"x\",\"x\",\"x\",\"x\"],\"sonnet\":[1,1,1,1]}')
good_first=load('{\"sonnet\":[1,1,1,1],\"opus\":[\"x\",\"x\",\"x\",\"x\"]}')
assert bad_first==good_first, (bad_first, good_first)      # key order is irrelevant
assert bad_first['sonnet']==(1.0,1.0,1.0,1.0)              # the valid row always applies
assert bad_first['opus']==dl.PRICES_PER_MTOK['opus']       # the bad row falls back, alone
# a wrong-SHAPE row is likewise skipped per-row without abandoning the rest
after=load('{\"opus\":[1,2,3],\"sonnet\":[1,1,1,1]}')
assert after['sonnet']==(1.0,1.0,1.0,1.0), after
assert after['opus']==dl.PRICES_PER_MTOK['opus'], after
"
	pyt "estimate_cost: cache tokens at cache rates, unpriced family -> None (never 0 or a guess)" "
import debate_lib as dl
E=dl.estimate_cost
M=1_000_000
# each usage bucket is priced from its OWN column, not lumped in with input
assert E({'input':M},'claude-opus-5')==15.0
assert E({'output':M},'claude-opus-5')==75.0
assert E({'cache_read':M},'claude-opus-5')==1.5      # cache read is 10x cheaper than input
assert E({'cache_create':M},'claude-opus-5')==18.75  # cache write is DEARER than input
assert E({'input':M,'output':M,'cache_read':M,'cache_create':M},'claude-opus-5')==110.25
# family-keyed, so a dated snapshot needs no entry of its own
assert E({'input':M},'claude-sonnet-4-5-20250929')==3.0
assert E({'input':M},'claude-haiku-4-5')==1.0
# an unpriced family yields None -- the UI renders it '—' rather than a confidently wrong 0
for m in ('gpt-4','some-other-llm','claude-unknown-9','',None):
    assert E({'input':M},m) is None, m
assert E({'input':M},'claude-opus-5',{}) is None     # empty table is a table, not a fallback
assert dl.fmt_cost(E({'input':M},'gpt-4'))=='—'
# missing/partial/None usage fields are zeros, not crashes
assert E(None,'claude-opus-5')==0.0 and E({},'claude-opus-5')==0.0
assert E({'input':None,'output':None},'claude-opus-5')==0.0
assert E({'input':M,'bogus':10**9},'claude-opus-5')==15.0   # unknown keys ignored
"
	pyt "independence_state: fails CLOSED — never 'ok' when either model is unknown" "
import debate_lib as dl
S=dl.independence_state
seen=set()
for role in list(dl.CROSS_MODEL_ROLES)+list(dl.INHERIT_ROLES)+['arbiter','tldr',None,'']:
    for model in ('claude-sonnet-5','claude-opus-5',None,''):
        for arb in ('claude-opus-5',None,''):
            st=S(role,model,arb); seen.add(st)
            # the load-bearing property: an undetermined model can never read as independent
            assert not ((not model or not arb) and st=='ok'), (role,model,arb,st)
assert seen <= set(dl.IND_MARKS), seen                 # every state has a badge
# all four cross-model roles behave identically
for r in dl.CROSS_MODEL_ROLES:
    assert S(r,'claude-sonnet-5','claude-opus-5')=='ok', r
    assert S(r,'claude-opus-5','claude-opus-5')=='collapse', r
    assert S(r,'claude-sonnet-5',None)=='unknown', r
    assert S(r,None,'claude-opus-5')=='unknown', r
# family-aware: dated twins of ONE tier are a collapse even though the strings differ
assert S('devil','claude-opus-4-1-20250805','claude-opus-4-5-20251101')=='collapse'
assert S('verifier','claude-sonnet-4-5-20250929','claude-sonnet-4-5-20250930')=='collapse'
# the reactive-respawn ladder's rung: a haiku verifier under an opus Arbiter is genuinely ok
assert S('verifier','claude-haiku-4-5','claude-opus-5')=='ok'
# an UNRECOGNISED id compares exactly, so it never false-matches another unknown
assert S('devil','my-llm-v1','my-llm-v1')=='collapse' and S('devil','my-llm-v1','my-llm-v2')=='ok'
# every badge glyph is exactly ONE display column and fits IND_W ('✓' measures 2, which is
# why '√' was chosen) -- a 2-column glyph here shifts every column to its right
for st,(g,key) in dl.IND_MARKS.items():
    assert dl.display_width(g)==1, (st,g,dl.display_width(g))
    assert dl.display_width(g)<=dl.IND_W and key, (st,g,key)
assert dl.independence_mark('devil','claude-sonnet-5','claude-opus-5')==dl.IND_MARKS['ok']
assert dl.independence_mark('nobody',None,None)==dl.IND_MARKS['n/a']
"
	pyt "every style key the lib emits exists in the viewer's attr table (else it paints unstyled)" "
import types, debate_lib as dl, debate_view as dv
# _render_row does attrs.get(key, 0), so a missing key does not crash -- it silently renders
# with no attribute at all, i.e. a dealbreaker that should be red comes out as plain prose.
stub=types.SimpleNamespace(A_NORMAL=0,A_BOLD=1,A_DIM=2,A_REVERSE=4,A_UNDERLINE=8,
                           has_colors=lambda: False)
attrs=dv._build_attrs(stub)
need=set(k for _g,k in dl.IND_MARKS.values())                  # independence badge styles
need|={'db','warn','ok','sect'}                                # debate severity styles
need|={'heat%d'%i for i in range(dl.HEAT_BUCKETS)}             # token heat ramp
need|={'plain','dim','active','done','hdr'}
missing=sorted(need-set(attrs))
assert not missing, missing
"
	pyt "debate_line_style: the full severity vocabulary the agents actually emit" "
import debate_lib as dl
f=dl.debate_line_style
for line in ('DEALBREAKER: x','- **DEALBREAKER (reproduced)** — y','**Dealbreakers**',
             'dealbreakers:','• DEALBREAKER z','SHARPEST OBJECTION: q','SHARPEST ATTACK',
             '[FAIL] widths overflow','FAILS — 2 item(s)','**FAILS: 2**'):
    assert f(line)=='db', (line,f(line))
for line in ('WORTH-NOTING — z','- WORTH NOTING: y','[PARTIAL — see note]',
             'IF YOU FIX ONE THING'):
    assert f(line)=='warn', (line,f(line))
for line in ('[PASS] widths hold','HONEST CONCESSION: two','CONCESSION','- CONCEDE: fine',
             'WHAT HOLDS UP','CONFORMS','OVERALL: CONFORMS'):
    assert f(line)=='ok', (line,f(line))
for line in ('CASE FOR the change','CASE AGAINST','STRONGEST GROUND','CROSS-EXAMINATION',
             'CONFORMANCE CHECK','SCOPE DRIFT: none','FINDINGS','PRECEDENT','INTERPRETATIONS',
             'TEST REPORT:','DOC SYNC REPORT','HONEST LIMIT','OVERALL: things'):
    assert f(line)=='sect', (line,f(line))
# precedence: the specific CONFORMS rule must beat the generic OVERALL section rule
assert f('OVERALL: CONFORMS — 1 item')=='ok'
"
	pyt "debate_line_style: no false positives on prose, and robust on empty/huge lines" "
import time, debate_lib as dl
f=dl.debate_line_style
# inflections that merely LOOK like the vocabulary must stay plain prose
for line in ('The dealbreaker was mentioned mid-sentence','Failed to parse the file',
             'Failure to launch','Failing tests are listed below','FAILSAFE mode enabled',
             'FAILOVER to the replica','This overall approach works','OVERALLOCATED buffers',
             'overallocation of memory','Conceding nothing here','The case for it is weak',
             'CASE FORWARD','Passing the test is easy','passed the check','FINDINGSOMETHING',
             'PRECEDENTED behaviour','A findings section follows','conformance checks are useful',
             'scope drifted a bit','No dealbreakers found — proceed','the test report says'):
    assert f(line) is None, (line,f(line))
# degenerate input never raises and never classifies
for line in (None,'','   ','\t\n','*'*50,'**',' - ','•','- '):
    assert f(line) is None, repr(line)
# leading whitespace is stripped before matching (indented report lines still classify)
assert f('    DEALBREAKER: indented')=='db' and f('\t[PASS] tabbed')=='ok'
# a pathological line must not blow up the render loop (no catastrophic backtracking)
for line in ('*'*200000+'x', ' '*200000+'x', '- '*100000+'x', 'x'*200000):
    t0=time.perf_counter(); f(line); dt=time.perf_counter()-t0
    assert dt<5.0, (len(line),dt)
"
	pyt "bulleted verdict lines classify through the real two-step path (classify_line -> style)" "
import debate_lib as dl, debate_view as dv
# The viewer never hands debate_line_style a RAW line: _detail_rows runs classify_line first,
# which strips the '-'/'*'/'+' bullet marker. So the per-item verdicts this repo's own reports
# emit as bullets (verifier.md:81, test-writer.md:95) must classify AFTER that strip.
def styled(line):
    _kind, content, _lead = dl.classify_line(line)
    return dl.debate_line_style(content)
for line in ('- [FAIL] the dealbreaker never landed','* [FAIL] star bullet','+ [FAIL] plus'):
    assert styled(line)=='db', (line, styled(line))
for line in ('- [PASS] scope held','* [PASS] x','+ [PASS] y'):
    assert styled(line)=='ok', (line, styled(line))
assert styled('- **DEALBREAKER** — reproduced')=='db'
assert styled('- WORTH-NOTING — minor')=='warn'
# and end to end through the detail pane: the severity reaches the rendered row's style
rows=dv._detail_rows({'events':[{'kind':'text','text':'- [FAIL] x'}]}, 78)
assert any('db' in {s for _t,s in r} for r in rows), rows
"
	# --- KNOWN DEFECT: this test FAILS against the current implementation. ------------------
	# Left red on purpose (the test-writer does not patch production code); see the TEST
	# REPORT for the write-up. tools/debate_lib.py:958-962 (_DEBATE_PATTERNS).
	pyt "DEFECT: 'OVERALL: FAILS' reads as a neutral section header, while 'OVERALL: CONFORMS' reads ok" "
import debate_lib as dl
f=dl.debate_line_style
assert f('OVERALL: CONFORMS')=='ok'          # the success verdict IS special-cased today
# ...but its failure counterpart is swallowed by the generic 'OVERALL' section rule, so the
# single most important line of a failing verifier/test-writer report is the LEAST prominent
assert f('OVERALL: FAILS — 2 item(s) need attention')=='db', repr(f('OVERALL: FAILS — 2 item(s) need attention'))
assert f('OVERALL: FAILS — 3 failing')=='db', repr(f('OVERALL: FAILS — 3 failing'))
assert f('OVERALL: things')=='sect'          # a bare OVERALL stays a section header
"
	pyt "agent_end/agent_duration: no events, unparseable, out-of-order and end<start fail safe" "
import debate_lib as dl
T=lambda s: {'ts':s}
# no events at all -> falls back to file mtime for BOTH ends (duration 0, not None/negative)
assert dl.agent_end({'events':[],'mtime':1000.0})==1000.0
assert dl.agent_duration({'events':[],'mtime':1000.0})==0.0
assert dl.agent_end({'mtime':1000.0})==1000.0            # events key missing entirely
assert dl.agent_end({'events':None,'mtime':1000.0})==1000.0
assert dl.agent_end({})==0.0 and dl.agent_duration({}) is None
# unparseable / missing / non-string timestamps all fall through to mtime rather than raising
for ev in ([T('garbage')],[T('')],[T(None)],[T(1234567890)],[T({'a':1})],[{'kind':'text'}]):
    a={'events':ev,'mtime':1000.0}
    assert dl.agent_start(a)==1000.0 and dl.agent_end(a)==1000.0, ev
    assert dl.agent_duration(a)==0.0, ev
# start scans FORWARD and end scans BACKWARD for the first parseable ts, so a bad timestamp
# at either edge does not truncate the span
mid=[T('nope'),T('2026-07-24T00:00:00Z'),T('2026-07-24T00:02:00Z'),T('nope')]
assert dl.agent_duration({'events':mid,'mtime':5.0})==120.0
# OUT OF ORDER (end < start) is refused rather than reported as a negative duration
back={'events':[T('2026-07-24T00:05:00Z'),T('2026-07-24T00:00:00Z')],'mtime':5.0}
assert dl.agent_end(back) < dl.agent_start(back)
assert dl.agent_duration(back) is None, dl.agent_duration(back)
assert dl.fmt_duration(dl.agent_duration(back))=='—'
# a real ordered pair, incl. sub-second timestamps, measures the wall clock
run={'events':[T('2026-07-24T00:00:00.123456Z'),T('2026-07-24T00:01:40.123456Z')],'mtime':5.0}
assert dl.agent_duration(run)==100.0 and dl.fmt_duration(dl.agent_duration(run))=='1m40s'
# agent_start/agent_end bound every event, so timeline_bar can never be handed end<t0
assert dl.agent_start(run) <= dl.agent_end(run)
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
t_snapshot
t_snapshot_unique_ids
t_snapshot_indep_field
t_attractor_fields
t_redact_secrets
t_redact_chokepoint
t_gui_server
t_gui_session_switch
t_markdown
t_tokens
mkdir -p "$WORKDIR/prices-root/.angel-advoc"   # fixture root for the price-override tests
t_edges

echo
echo "-------------------------------------------"
printf 'total: %d   passed: %d   failed: %d\n' "$((PASS + FAIL))" "$PASS" "$FAIL"

[ "$FAIL" -eq 0 ]
