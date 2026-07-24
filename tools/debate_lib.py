#!/usr/bin/env python3
"""debate_lib.py — pure, importable core for the Angel's Advocate debate viewer.

Claude Code writes a transcript per spawned subagent under
    ~/.claude/projects/<project-slug>/<session>/subagents/agent-<id>.jsonl
(and, for workflow-spawned agents, under .../subagents/workflows/**). This module
turns those transcripts into an ordered event stream per role (angel/devil/verifier/…)
so the viewer can show what each agent is thinking, doing, and concluding.

Everything here is a plain function with no curses, no I/O loop, and no global state —
so it is unit-testable (tools/tests/debate_lib_test.sh). The curses UI in
debate_view.py is a thin shell over these functions.

Transcript shape (verified against real Claude Code v2.1.x transcripts):
  each line is a JSON object; the fields this module reads:
    attributionAgent : role name ("angel","devil","verifier",…) on assistant lines
    agentId          : stable per-agent id (one file == one agent)
    timestamp        : ISO-8601 string
    message.model    : the model that ran this agent
    message.content  : either a plain string (the initial prompt, on a user line) or a
                       list of blocks:
                         {"type":"thinking","thinking":"..."}
                         {"type":"text","text":"..."}
                         {"type":"tool_use","name":"Bash","input":{...}}
                         {"type":"tool_result","content": "..." | [blocks]}   (user line)
"""
from __future__ import annotations

import glob
import json
import os
import re
import time

# Role -> emoji for the roster/detail. red-teamer is intentionally omitted (renders as plain
# text): its 🛡️ glyph carries a U+FE0F selector that misaligned the column on real terminals.
# Roles not listed here render with no glyph.
ROLE_EMOJI = {
    "angel": "😇",
    "devil": "😈",
    "arbiter": "⚖️",
    "verifier": "✅",
    "researcher": "🔎",
    "interpreter": "🧭",
    "profiler": "📊",
    "historian": "📚",
    "scribe": "📝",
    "test-writer": "🧪",
    "tldr": "✂️",
}

# last-event-kind -> short activity label for an active agent (plain text, no emoji)
_ACTIVITY = {
    "thinking": "thinking…",
    "tool_use": "running tool…",
    "tool_result": "got result…",
    "text": "writing…",
    "prompt": "briefed",
}


def role_emoji(role):
    """Emoji for a role, or '' for the roles we render as plain text (everything but angel/devil)."""
    return ROLE_EMOJI.get(role or "", "")


def activity_label(last_kind):
    return _ACTIVITY.get(last_kind or "", "…")


def sec_head(kind):
    """Plain-text detail/dump section header ('thinking' / 'output' / 'tool')."""
    return {"thinking": "thinking", "output": "output", "tool": "tool"}.get(kind, kind)


# --- session / file discovery ------------------------------------------------

def project_slug(cwd):
    """Claude Code slugifies a project path by replacing the path separator with '-'.
    /home/u/proj -> -home-u-proj (leading sep becomes a leading '-')."""
    return cwd.replace(os.sep, "-")


def project_dir(cwd, home=None):
    home = home or os.path.expanduser("~")
    return os.path.join(home, ".claude", "projects", project_slug(cwd))


def _dir_recency(subagents_dir):
    """Most recent mtime of the subagents dir OR any file directly inside it — some
    filesystems don't bump a dir's mtime when a nested file is appended."""
    m = -1.0
    try:
        m = os.path.getmtime(subagents_dir)
    except OSError:
        return m
    try:
        for name in os.listdir(subagents_dir):
            fp = os.path.join(subagents_dir, name)
            try:
                if os.path.isfile(fp):
                    m = max(m, os.path.getmtime(fp))
            except OSError:
                pass
    except OSError:
        pass
    return m


def discover_session(pdir):
    """Return the <session> dir under `pdir` whose subagents/ dir is most recently
    active, or None if there are none."""
    best, best_m = None, -2.0
    for sub in glob.glob(os.path.join(pdir, "*", "subagents")):
        if not os.path.isdir(sub):
            continue
        m = _dir_recency(sub)
        if m > best_m:
            best, best_m = os.path.dirname(sub), m
    return best


def agent_files(subagents_dir):
    """All agent transcript files under a subagents dir, including workflow-spawned
    ones nested under workflows/. Sorted, de-duplicated."""
    files = glob.glob(os.path.join(subagents_dir, "agent-*.jsonl"))
    files += glob.glob(
        os.path.join(subagents_dir, "workflows", "**", "agent-*.jsonl"), recursive=True
    )
    return sorted(set(files))


def file_role(path):
    """(role, model) for a transcript file, taken from its first tagged line(s).
    One file == one agent, so this is the canonical attribution for the whole file
    (tool_result lines lack attributionAgent, so per-line role is unreliable)."""
    role, model = None, None
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    o = json.loads(raw)
                except Exception:
                    continue
                if role is None:
                    role = o.get("attributionAgent")
                m = (o.get("message") or {}).get("model")
                if m and model is None:
                    model = m
                if role and model:
                    break
    except OSError:
        pass
    return role, model


# --- parsing -----------------------------------------------------------------

def _tool_result_text(content):
    """tool_result.content may be a string or a list of blocks; flatten to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text", ""))
            else:
                parts.append(str(b))
        return "".join(parts)
    return "" if content is None else str(content)


def events_from_obj(obj):
    """Turn one parsed transcript line into zero or more display events."""
    role = obj.get("attributionAgent")
    ts = obj.get("timestamp", "")
    msg = obj.get("message") or {}
    model = msg.get("model")
    content = msg.get("content")
    out = []

    def ev(kind, **kw):
        e = {"role": role, "model": model, "ts": ts, "kind": kind}
        e.update(kw)
        return e

    if isinstance(content, str):
        out.append(ev("prompt", text=content))
        return out
    if not isinstance(content, list):
        return out
    for c in content:
        if not isinstance(c, dict):
            continue
        t = c.get("type")
        if t == "thinking":
            out.append(ev("thinking", text=c.get("thinking", "")))
        elif t == "text":
            out.append(ev("text", text=c.get("text", "")))
        elif t == "tool_use":
            out.append(ev("tool_use", name=c.get("name", ""), input=c.get("input")))
        elif t == "tool_result":
            out.append(ev("tool_result", text=_tool_result_text(c.get("content"))))
    return out


def parse_new(path, offset=0):
    """Read new COMPLETE lines from `path` starting at byte `offset`.

    Returns (events, new_offset). Only newline-terminated lines are consumed; a partial
    trailing line (the file may be mid-write during a live tail) is left unconsumed for
    the next call, so a half-written line is never parsed. Malformed JSON lines are
    skipped, not fatal. A missing/unreadable file yields ([], offset)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read()
    except OSError:
        return [], offset
    if not data:
        return [], offset
    last_nl = data.rfind(b"\n")
    if last_nl == -1:
        return [], offset  # no complete line yet
    complete = data[: last_nl + 1]
    new_offset = offset + last_nl + 1
    events = []
    for raw in complete.split(b"\n"):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        events.extend(events_from_obj(obj))
    return events, new_offset


def agent_status(mtime, now=None, active_window=3.0):
    """HEURISTIC status. Claude Code gives no explicit per-agent 'finished' signal in
    the transcript, so 'active' means the file changed within `active_window` seconds,
    else 'done'. This is a best-effort guess, not ground truth — the UI labels it so."""
    now = time.time() if now is None else now
    return "active" if (now - mtime) <= active_window else "done"


# --- rendering (plain text; shared by --once dump and testable directly) ------

def short_tool_input(inp, limit=100):
    if not isinstance(inp, dict):
        return ""
    s = json.dumps(inp, ensure_ascii=False)
    return s if len(s) <= limit else s[: limit - 1] + "…"


# --- markdown-lite rendering -------------------------------------------------
#
# The advocates write Markdown (**bold**, `code`, # headers, - bullets). A terminal can't
# render Markdown, so raw text shows literal asterisks/backticks. These pure functions turn
# a line into styled segments the curses UI paints with real attributes, and strip the
# syntax for the plain --once dump. Kept here (not in the curses shell) so they're unit-tested.

def char_width(cp):
    """Terminal display width of a code point: 0 for zero-width (variation selectors, ZWJ,
    combining marks), 2 for wide/emoji, else 1. Approximate but matches how common terminals
    render — enough to keep columns aligned. The key case: a variation selector (U+FE0F, as in
    the 🛡️ shield) is a real code point but paints 0 columns, so counting it with len() shoves
    the rest of the row right."""
    if cp == 0:
        return 0
    if (0x200B <= cp <= 0x200F or 0xFE00 <= cp <= 0xFE0F      # ZW spaces/joiners, var selectors
            or 0x0300 <= cp <= 0x036F or cp == 0x2060):        # combining diacritics, word joiner
        return 0
    if (0x1100 <= cp <= 0x115F or 0x2E80 <= cp <= 0xA4CF or 0xAC00 <= cp <= 0xD7A3
            or 0xF900 <= cp <= 0xFAFF or 0xFE30 <= cp <= 0xFE4F or 0xFF00 <= cp <= 0xFF60
            or 0xFFE0 <= cp <= 0xFFE6 or 0x2600 <= cp <= 0x27BF or 0x2B00 <= cp <= 0x2BFF
            or 0x1F000 <= cp <= 0x1FAFF):                      # CJK + symbols/dingbats + emoji
        return 2
    return 1


def display_width(s):
    """Sum of char_width over a string — its rendered column count."""
    return sum(char_width(ord(c)) for c in s or "")


def clip_to_width(s, cols):
    """Longest prefix of s that fits in `cols` display columns (never splits a wide char)."""
    out, w = [], 0
    for c in s or "":
        cw = char_width(ord(c))
        if w + cw > cols:
            break
        out.append(c); w += cw
    return "".join(out)


def pad_display(s, width):
    """Left-justify s to `width` DISPLAY columns (clipping if longer), emoji-aware — so a field
    containing a 2-column emoji still lands on the same column boundary as a plain-text field."""
    s = clip_to_width(s or "", width)
    return s + " " * max(0, width - display_width(s))


def parse_inline(text):
    """Split one line into [(text, style)] segments, interpreting `**bold**` and `` `code` ``.
    style is 'plain' | 'b' (bold) | 'c' (code). Single `*` emphasis markers are stripped.
    Unmatched markers are treated as literal. Never raises."""
    segs, buf, i, n = [], [], 0, len(text or "")
    text = text or ""

    def push(s, st):
        if s:
            segs.append((s, st))

    while i < n:
        c = text[i]
        if c == "`":
            j = text.find("`", i + 1)
            if j == -1:
                buf.append(c); i += 1; continue
            push("".join(buf), "plain"); buf = []
            push(text[i + 1:j], "c"); i = j + 1; continue
        if c == "*" and i + 1 < n and text[i + 1] == "*":
            j = text.find("**", i + 2)
            if j == -1:
                buf.append("**"); i += 2; continue
            push("".join(buf), "plain"); buf = []
            push(text[i + 2:j], "b"); i = j + 2; continue
        if c == "*":            # single-asterisk emphasis: drop the marker, keep the word
            i += 1; continue
        buf.append(c); i += 1
    push("".join(buf), "plain")
    return segs or [("", "plain")]


def classify_line(line):
    """Classify a raw markdown line -> (kind, content, indent). kind is
    'header'|'bullet'|'quote'|'fence'|'normal'; content has the leading marker removed;
    indent is the leading-space count (for nesting)."""
    s = (line or "").rstrip("\n")
    stripped = s.lstrip(" ")
    lead = len(s) - len(stripped)
    if stripped.startswith("```"):
        return ("fence", "", lead)
    m = re.match(r"(#{1,6})\s+(.*)", stripped)
    if m:
        return ("header", m.group(2), lead)
    m = re.match(r"[-*+]\s+(.*)", stripped)
    if m:
        return ("bullet", m.group(1), lead)
    if stripped.startswith(">"):
        return ("quote", stripped[1:].lstrip(" "), lead)
    return ("normal", stripped, lead)


def strip_inline_md(text):
    """Plain-text form of a line with **/`/* markers removed (for the --once dump)."""
    return "".join(t for t, _ in parse_inline(text))


def _tokenize_words(segments):
    """Group [(text, style)] segments into words, where a word is a list of styled pieces
    with NO spaces. A word may span styles (so `**DEAL**:` stays one word `DEAL:` with the
    colon attached), and adjacent segments are only split where whitespace actually was —
    this avoids inserting spurious spaces around bold/code boundaries."""
    words, cur = [], []
    for text, style in segments:
        for part in re.split(r"(\s+)", text or ""):
            if part == "":
                continue
            if part.isspace():
                if cur:
                    words.append(cur); cur = []
            else:
                cur.append((part, style))
    if cur:
        words.append(cur)
    return words


def wrap_segments(segments, width, indent=0):
    """Word-wrap [(text, style)] into display rows (each a list of (text, style)), not
    exceeding `width` columns; every row is prefixed with `indent` spaces. Per-piece style
    is preserved across wrap points, and words that span styles stay intact. A single word
    longer than the width is left over-long (the caller clips it) rather than hard-split."""
    width = max(4, width)
    avail = max(1, width - indent)
    pad = " " * indent
    rows, cur, cur_len = [], [], 0

    def flush():
        nonlocal cur, cur_len
        row = ([(pad, "plain")] if indent else []) + (cur or [("", "plain")])
        rows.append(row)
        cur, cur_len = [], 0

    for word in _tokenize_words(segments):
        wl = sum(display_width(t) for t, _ in word)
        if cur_len == 0:
            cur.extend(word); cur_len = wl
        elif cur_len + 1 + wl <= avail:
            cur.append((" ", "plain")); cur.extend(word); cur_len += 1 + wl
        else:
            flush(); cur.extend(word); cur_len = wl
    flush()
    return rows


def render_dump(agents):
    """Plain-text, grouped-by-role transcript for --once / non-TTY output.
    `agents` is a list of dicts: {role, model, events:[...]}. Returns a string."""
    lines = []
    for a in agents:
        role = a.get("role")
        lines.append(f"{role_emoji(role)} {role or '(unknown)'}  ({a.get('model') or '?'})".lstrip())
        for e in a.get("events", []):
            k = e.get("kind")
            if k == "thinking":
                lines.append(f"  {sec_head('thinking')}")
                for ln in (e.get("text") or "").splitlines():
                    lines.append(f"     {strip_inline_md(ln)}")
            elif k == "text":
                lines.append(f"  {sec_head('output')}")
                for ln in (e.get("text") or "").splitlines():
                    lines.append(f"     {strip_inline_md(ln)}")
            elif k == "tool_use":
                lines.append(f"  {e.get('name', '')}  {short_tool_input(e.get('input'))}")
            elif k == "tool_result":
                snippet = " ".join((e.get("text") or "").split())[:120]
                lines.append(f"     ↳ {snippet}")
            elif k == "prompt":
                snippet = " ".join((e.get("text") or "").split())[:120]
                lines.append(f"  … briefed: {snippet}")
        lines.append("")
    return "\n".join(lines)


# --- cross-model independence check (ground truth, post-hoc) ------------------
#
# The workflow's load-bearing honesty claim is that these roles run on a DIFFERENT
# model than the Arbiter, so their scrutiny isn't a same-model self-check wearing a
# costume. This checks the ACTUAL runtime model each of them ran on (read from the
# transcript, not the agent file's declared `model:`), so it catches BOTH a static
# misconfig (agent file set to the Arbiter's model) AND the availability-fallback
# collapse (declared sonnet, but sonnet was unavailable so it fell back to the
# Arbiter's model) — the latter is invisible to any file-reading preflight.
CROSS_MODEL_ROLES = ("devil", "verifier", "red-teamer", "interpreter")

# Roles that inherit the Arbiter's model — used to *infer* the Arbiter's model from the
# session's own transcripts when it isn't supplied explicitly (an angel/researcher/… ran
# on exactly the Arbiter's model, by definition).
INHERIT_ROLES = ("angel", "historian", "profiler", "scribe", "researcher", "test-writer")

# Known model families (tiers). Independence is about shared lineage/blind spots, and within
# one vendor a tier IS the model — so two agents on the same tier are a collapse even if their
# dated snapshot suffixes differ (e.g. claude-sonnet-4-5-20250929 vs -20250930). Comparing raw
# IDs would miss that (a latent false "differs"); comparing families catches it.
MODEL_FAMILIES = ("opus", "sonnet", "haiku")


def model_family(model):
    """Normalize a model id to its family/tier for independence comparison.
    'claude-sonnet-4-5-20250929' -> 'sonnet'. An unrecognized id falls back to its own
    lowercased string, so unknown models still compare exactly (never a false match)."""
    if not model:
        return model
    lo = model.lower()
    for fam in MODEL_FAMILIES:
        if fam in lo:
            return fam
    return lo


def same_model(a, b):
    """True if two model ids are the same model for independence purposes — i.e. same family
    (so dated-suffix variants of one tier count as identical), NOT just byte-equal strings."""
    return bool(a) and bool(b) and model_family(a) == model_family(b)


def infer_arbiter_model(agents):
    """Best-effort: the Arbiter's model, taken from any inherit-role agent present in the
    session (those run on the Arbiter's model). Returns (model, source_role) or (None, None)."""
    for a in agents:
        if a.get("role") in INHERIT_ROLES and a.get("model"):
            return a["model"], a["role"]
    return None, None


def check_independence(agents, arbiter_model=None):
    """Verify every cross-model role in this session ran on a model != the Arbiter's.

    `agents`: list of dicts with at least {role, model} (e.g. from load_agents_full).
    `arbiter_model`: the Arbiter's ACTUAL model. If None, inferred from an inherit-role
    agent in the session; if it still can't be determined, the result is 'unverified'
    (fail-closed) rather than a false pass.

    Returns a dict:
      status        : 'ok' | 'collapse' | 'unverified' | 'nothing-to-check'
      arbiter_model : the model compared against (or None)
      arbiter_source: 'supplied' | 'inferred from <role>' | None
      findings      : [{role, model, collapsed: bool}]  (one per cross-model agent seen)
      checked       : number of cross-model agents seen
    """
    if arbiter_model:
        source = "supplied"
    else:
        arbiter_model, src_role = infer_arbiter_model(agents)
        source = f"inferred from {src_role}" if arbiter_model else None

    findings = []
    for a in agents:
        if a.get("role") in CROSS_MODEL_ROLES and a.get("model"):
            findings.append({"role": a["role"], "model": a["model"], "collapsed": False})

    if not findings:
        return {"status": "nothing-to-check", "arbiter_model": arbiter_model,
                "arbiter_source": source, "findings": [], "checked": 0}
    if not arbiter_model:
        return {"status": "unverified", "arbiter_model": None,
                "arbiter_source": None, "findings": findings, "checked": len(findings)}

    collapsed_any = False
    for f in findings:
        if same_model(f["model"], arbiter_model):  # family-aware: catches dated-suffix twins
            f["collapsed"] = True
            collapsed_any = True
    return {"status": "collapse" if collapsed_any else "ok",
            "arbiter_model": arbiter_model, "arbiter_source": source,
            "findings": findings, "checked": len(findings)}


def render_independence(result):
    """Human-readable report for check_independence(). Returns a string."""
    st = result["status"]
    am = result["arbiter_model"] or "?"
    src = result["arbiter_source"]
    lines = []
    if st == "nothing-to-check":
        lines.append("🔎 Independence — no cross-model agents (devil/verifier/red-teamer/"
                     "interpreter) ran in this session; nothing to check.")
        return "\n".join(lines)
    if st == "unverified":
        lines.append("⚠️  Independence UNVERIFIED — could not determine the Arbiter's model "
                     "(no inherit-role agent in session; pass --arbiter-model).")
        lines.append("   Cross-model agents seen (actual runtime model):")
        for f in result["findings"]:
            lines.append(f"     {role_emoji(f['role'])} {f['role']}  [{f['model']}]")
        return "\n".join(lines)
    head = "✅ Independence HELD" if st == "ok" else "❌ Independence COLLAPSED"
    lines.append(f"{head} — Arbiter ran on [{am}] ({src}); checked actual runtime models.")
    for f in result["findings"]:
        mark = "COLLAPSE — same model as Arbiter" if f["collapsed"] else "ok — differs"
        glyph = "❌" if f["collapsed"] else "✔"
        lines.append(f"   {glyph} {role_emoji(f['role'])} {f['role']:<12} [{f['model']}]  {mark}")
    if st == "collapse":
        lines.append("   → A 'cross-model' check ran on the Arbiter's own model: it is a "
                     "same-model self-check, not independent QA. Label the rigor honestly, or "
                     "flip the role's model: so it differs from the Arbiter.")
    return "\n".join(lines)


def load_agents_full(subagents_dir):
    """Read every agent transcript fully (offset 0) and return a list of
    {path, role, model, events, mtime} — the one-shot / replay view."""
    agents = []
    for path in agent_files(subagents_dir):
        role, model = file_role(path)
        events, _ = parse_new(path, 0)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0
        agents.append(
            {"path": path, "role": role, "model": model, "events": events, "mtime": mtime}
        )
    # order by first activity (file name is stable; sort by mtime of first event/file)
    agents.sort(key=lambda a: a["path"])
    return agents
