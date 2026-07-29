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

import datetime
import glob
import json
import os
import re
import time
import unicodedata

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
    "builder": "🔨",
    # tldr omitted deliberately: its ✂️ carries a U+FE0F variation selector that many
    # terminals render as a narrow (width-1) text glyph while display_width() counts 2,
    # shifting the ROLE column. Same failure — and same fix (render plain) — as red-teamer's
    # 🛡️. Roles absent here render with no glyph.
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


# --- reactive roster faces ---------------------------------------------------
# A tiny animated face per agent that emotes by state (thinking / running a tool /
# writing / idle). Angel and devil get character-flavoured faces; every other role
# shares a neutral set. EVERY frame is exactly 5 display columns — parens + a 3-char
# middle — so the roster's FACE column never shifts. Frames cycle only while active;
# idle is a single static frame, so a quiet roster doesn't twitch.
#
# GLYPHS MUST BE PURE ASCII (ord < 128). Earlier faces used prettier marks like `·`,
# `ò`, `ō`, `˘` — but those are Unicode East-Asian *Ambiguous*-width: display_width()
# counts them as 1 while many terminals (tmux/TERM=screen, CJK/ambiguous locales) draw
# them as 2. The face then overflows its 5-col cell and gets clipped near the right
# edge — a broken face that worsens as the pane narrows. display_width() can't catch
# this (it shares the wrong width model), so the real guard is the ASCII-only assertion
# in debate_lib_test.sh. Keep every glyph in 0x20–0x7E and no terminal can widen it.
FACE_FPS = 2.0  # frame changes per second (matches the ~0.5s viewer poll)

_FACES = {
    "angel": {                                       # soft, blissful
        "thinking": ["(-.-)", "(u.u)"],
        "tool":     ["(o.o)", "(O.O)"],
        "writing":  ["(^_^)", "(^o^)"],
        "idle":     ["(^.^)"],
    },
    "devil": {                                       # sharp, scheming, smug
        "thinking": ["(>.>)", "(<.<)"],
        "tool":     ["(>o<)", "(>O<)"],
        "writing":  ["(-.~)", "(~.~)"],
        "idle":     ["(~_~)"],
    },
    "_default": {                                    # neutral (verifier, researcher, …)
        "thinking": ["(-.-)", "(o.o)"],
        "tool":     ["(o.o)", "(O.O)"],
        "writing":  ["(^.^)", "(-.-)"],
        "idle":     ["(-_-)"],
    },
}


def face_state(status, last_kind):
    """Map (heuristic status, last event kind) -> a face state. 'active' agents emote by
    what they're doing; anything not active is 'idle'."""
    if status != "active":
        return "idle"
    if last_kind in ("tool_use", "tool_result"):
        return "tool"
    if last_kind == "text":
        return "writing"
    return "thinking"  # thinking / prompt / unknown


def face(role, state, frame=0):
    """A 5-display-column face for a role in a given state. `frame` cycles animation
    frames (ignored for idle, which is static)."""
    role_set = _FACES.get(role if role in _FACES else "_default", _FACES["_default"])
    frames = role_set.get(state) or role_set["idle"]
    return frames[frame % len(frames)]


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


def list_sessions(pdir):
    """Every session under `pdir` that has a subagents/ dir, most recently active first.

    Returns [{"id","subagents","recency","agents"}]. This is the ALLOW-LIST the browser
    GUI's session switcher selects from: the server enumerates real directories itself and
    the request may only pick one of these ids, so a session id arriving over HTTP is never
    joined into a filesystem path. Keeps the 'request never builds a path' property intact
    even though the client can now choose a session.
    """
    out = []
    for sub in glob.glob(os.path.join(pdir, "*", "subagents")):
        if not os.path.isdir(sub):
            continue
        sess = os.path.dirname(sub)
        out.append({"id": os.path.basename(sess), "subagents": sub,
                    "recency": _dir_recency(sub), "agents": len(agent_files(sub))})
    out.sort(key=lambda s: s["recency"], reverse=True)
    return out


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

# --- secret redaction --------------------------------------------------------
# Transcripts capture tool output VERBATIM, so a subagent that runs `env` — a routine move
# when a debate needs to know which model it is running on — writes live credentials
# straight into agent-*.jsonl. Both viewers render those events and the GUI serves them
# over HTTP, so redact at the one chokepoint every event passes through
# (events_from_obj/ev below): that covers the terminal viewer, the browser GUI and
# snapshot() in a single place, rather than three front-ends each remembering to do it.
#
# DELIBERATELY CONSERVATIVE: match a known secret NAME or a known key PREFIX, never
# "looks like a long random string". A 40-char hex blob is far more often a git SHA in a
# diff than a credential, and redacting those would corrupt the very diffs the verifier
# reads. Missing an exotic secret is recoverable; shredding every diff is not.
# Header-carrying vars come FIRST and consume to end-of-line: their value is itself a
# "Name: value" pair, so a token-only match would stop at the header NAME and leave the
# credential after the space in the clear. That exact miss was caught in testing against
# a real `env` dump — 'ANTHROPIC_CUSTOM_HEADERS=Ocp-Apim-Subscription-Key: <key>' had the
# name redacted and the key still exposed.
_SECRET_ASSIGN_EOL = re.compile(
    r"(?i)\b(ANTHROPIC_CUSTOM_HEADERS|Ocp-Apim-Subscription-Key"
    r"|Authorization|Proxy-Authorization|X-Api-Key)(\s*[=:]\s*)([^\r\n\"']+)")
_SECRET_ASSIGN = re.compile(
    r"(?i)\b(ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN"
    r"|OPENAI_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY|AWS_SECRET_ACCESS_KEY"
    r"|GITHUB_TOKEN|GH_TOKEN|HF_TOKEN|NPM_TOKEN|SLACK_TOKEN)(\s*[=:]\s*)([^\s\"']+)")
# The leading \b is load-bearing, not decoration: without it 'sk-' matches INSIDE ordinary
# words — 'task-<id>', 'disk-<id>', 'risk-<id>' all end in 'sk' and would be silently
# mangled, which is exactly the diff/ID corruption this matcher is supposed to avoid.
_SECRET_TOKEN = re.compile(
    r"\b(sk-ant-[A-Za-z0-9_-]{6,}|sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{12,}|xox[abprs]-[A-Za-z0-9-]{10,})")

REDACTION = "«redacted"


def redact_secrets(text):
    """Replace credential VALUES in transcript text with a visible placeholder.

    Keeps the variable name and the value's length so the text still reads sensibly
    ('ANTHROPIC_API_KEY=«redacted:32c»') — a silent deletion would make a debate about
    environment handling unreadable, and an invisible one would leave the operator
    unaware the value was ever there."""
    if not isinstance(text, str) or not text:
        return text
    sub = lambda m: m.group(1) + m.group(2) + REDACTION + ":%dc»" % len(m.group(3))
    s = _SECRET_ASSIGN_EOL.sub(sub, text)     # header-style first (consumes to EOL)
    s = _SECRET_ASSIGN.sub(sub, s)
    return _SECRET_TOKEN.sub(
        lambda m: REDACTION + ":%dc»" % len(m.group(1)), s)


def _redact_deep(v):
    """redact_secrets over a nested tool_use `input` (dict/list/str), leaving shape intact."""
    if isinstance(v, str):
        return redact_secrets(v)
    if isinstance(v, dict):
        return {k: _redact_deep(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_redact_deep(x) for x in v]
    return v


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
        # Single redaction chokepoint: every event the viewers/GUI ever see is built here.
        e = {"role": role, "model": model, "ts": ts, "kind": kind}
        if "text" in kw:
            kw["text"] = redact_secrets(kw["text"])
        if "input" in kw:
            kw["input"] = _redact_deep(kw["input"])
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

# East-Asian "Ambiguous"-width policy. Chars like em-dash (—), curly quotes (“ ”), the
# ellipsis (…), Greek letters, and the middle dot (·) are Unicode East_Asian_Width = 'A':
# they render 1 column on Western terminals but 2 on CJK/ambiguous-as-wide ones — and tmux
# under TERM=screen is commonly in the wide camp. No stdlib call can query the terminal's
# actual mode, so this is a knob, not a guess: default 1 (correct for the common case), opt
# into 2 via ANGEL_ADVOC_AMBIGUOUS_WIDTH=2 on a terminal that draws ambiguous glyphs wide.
# This closes the wrap/pad undercount class as far as a terminal app can — a GUI owns its
# own layout engine and needs no such knob. Read once at import (viewer is launched fresh);
# tests set dl.AMBIGUOUS_WIDTH directly to exercise both policies.
AMBIGUOUS_WIDTH = 2 if os.environ.get("ANGEL_ADVOC_AMBIGUOUS_WIDTH", "").strip() == "2" else 1


def char_width(cp):
    """Terminal display width of a code point: 0 for zero-width (variation selectors, ZWJ,
    combining marks), 2 for wide/emoji, else 1. Approximate but matches how common terminals
    render — enough to keep columns aligned. The key case: a variation selector (U+FE0F, as in
    the 🛡️ shield) is a real code point but paints 0 columns, so counting it with len() shoves
    the rest of the row right.

    The explicit ranges below run FIRST and are the source of truth for the glyphs the roster
    actually uses (so their widths never regress). Anything they don't classify falls through
    to the Unicode database, which catches wide/ambiguous chars the ranges miss — notably the
    ambiguous-width prose (em-dash, curly quotes, …) that used to under-count in wrap_segments."""
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
    eaw = unicodedata.east_asian_width(chr(cp))
    if eaw in ("W", "F"):          # unambiguously wide / fullwidth the ranges above missed
        return 2
    if eaw == "A":                 # ambiguous — terminal-dependent; honor the policy knob
        return AMBIGUOUS_WIDTH
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
        u = a.get("usage")
        tok = f"  ·  {fmt_tokens(usage_total(u))} tok (out {fmt_tokens(u.get('output', 0))})" if u else ""
        lines.append(f"{role_emoji(role)} {role or '(unknown)'}  ({a.get('model') or '?'}){tok}".lstrip())
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


# --- token usage ------------------------------------------------------------
#
# Both main-session transcripts (<project>/<id>.jsonl) and subagent transcripts carry a
# `message.usage` object on assistant lines. These functions normalize and sum it so the
# viewer, a cross-session report, and the journal can all report token cost from one core.
USAGE_FIELDS = ("input", "output", "cache_read", "cache_create")


def blank_usage():
    return {k: 0 for k in USAGE_FIELDS}


def _norm_usage(u):
    """Map a raw message.usage dict to our normalized field names."""
    u = u or {}
    return {
        "input": u.get("input_tokens", 0) or 0,
        "output": u.get("output_tokens", 0) or 0,
        "cache_read": u.get("cache_read_input_tokens", 0) or 0,
        "cache_create": u.get("cache_creation_input_tokens", 0) or 0,
    }


def add_usage(a, b):
    return {k: (a.get(k, 0) + b.get(k, 0)) for k in USAGE_FIELDS}


def usage_total(u):
    return sum(u.get(k, 0) for k in USAGE_FIELDS)


def usage_from_file(path, since=None):
    """Sum message.usage across one transcript. If `since` (an ISO-8601 UTC timestamp string,
    e.g. '2026-07-24T10:00:00Z') is given, only lines with timestamp >= since are counted
    (lexicographic compare — valid for same-format Z-suffixed timestamps). Returns a
    normalized usage dict; a missing/unreadable file yields zeros."""
    tot = blank_usage()
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    o = json.loads(raw)
                except Exception:
                    continue
                if since:
                    ts = o.get("timestamp", "")
                    if ts and ts < since:
                        continue
                u = (o.get("message") or {}).get("usage")
                if u:
                    tot = add_usage(tot, _norm_usage(u))
    except OSError:
        pass
    return tot


def usage_from_files(paths, since=None):
    tot = blank_usage()
    for p in paths:
        tot = add_usage(tot, usage_from_file(p, since))
    return tot


TOK_NUM_W = 6         # max display width of fmt_tokens output — the cell budget depends on it


def fmt_tokens(n):
    """Compact human token count: 950, 1.2k, 3.4M. Never wider than TOK_NUM_W columns.

    The rollover check is `< 999.95`, not `< 1000`: at one decimal place 999,950 formats as
    '1000.0k' (7 cols), which silently broke the fixed-width TOKENS cell for every agent
    transiting 999,950..999,999 on its way past a million — a range real agents cross on
    every large run. Promoting to the next unit instead keeps the width bounded at the
    source, so no caller can reintroduce the jitter by picking a different formatter."""
    n = int(n or 0)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n < 1000:
        return f"{sign}{n}"
    for unit, scale in (("k", 10**3), ("M", 10**6), ("B", 10**9), ("T", 10**12)):
        v = n / scale
        if v < 999.95:          # rounds to <= 999.9 at one decimal -> candidate fits
            s = f"{sign}{v:.1f}{unit}"
            # Measure, don't assume: a minus sign costs a column the 999.95 threshold does
            # not budget for, so '-999.5k' is 7 wide. Promote to the next unit instead of
            # trusting the threshold alone.
            if display_width(s) <= TOK_NUM_W:
                return s
    return f"{sign}999T+"       # absurd magnitudes: stay bounded rather than widen


# --- token heat map ----------------------------------------------------------
# Colour + bar length encode each agent's token use relative to the PEAK AGENT IN THE
# SHOWN DEBATE. Two encodings of one fact, deliberately: on a mono or 8-colour terminal —
# or for a red/green-colourblind reader — bar length alone still carries the full ranking,
# so colour is never load-bearing. The ramp has NO green: green already means "active" in
# the STATUS column one over (see debate_view._build_attrs), and one hue must not carry two
# meanings on the same row. Blue->amber->red is also monotone in luminance, so it survives
# being screenshotted in grayscale.
#
# Normalizing to the live peak means a finished agent can visibly cool when a sibling
# overtakes it. That is real, and accepted: it is how live monitors (htop, docker stats)
# behave, and the two alternatives measure worse against this repo's own transcripts — an
# absolute scale erases a 1.55x true spread and a monotonic ratchet ends up pinning several
# agents at max heat at once. Peak is monotonically non-decreasing, so buckets only ever
# settle one way and never oscillate; once every agent is done the counts stop changing, so
# replay and --once are stable for free.
HEAT_BUCKETS = 5
HEAT_RAMP_256 = (25, 39, 214, 208, 196)   # navy -> azure -> amber -> orange -> red
TOK_BAR_W = 5         # bar cells in the TOKENS column
TOK_CELL_W = TOK_BAR_W + 1 + TOK_NUM_W    # bar + gap + count = 12

_EIGHTHS = " ▏▎▍▌▋▊▉"  # partial-cell fills, 0/8..7/8 (U+258F..U+2589 are all width-1)


def heat_bucket(value, peak, buckets=HEAT_BUCKETS):
    """Heat level 0..buckets-1 for `value` against the debate's `peak`. Monotone in value,
    so a bigger consumer can never render cooler than a smaller one. A falsy/zero peak means
    there is nothing to compare against, so everything reads coldest."""
    buckets = max(1, int(buckets))
    try:
        peak = float(peak or 0)
        frac = (float(value or 0) / peak) if peak > 0 else 0.0
    except (TypeError, ValueError):
        frac = 0.0
    frac = min(1.0, max(0.0, frac))
    return min(buckets - 1, int(frac * buckets))


def share_bar(value, peak, width=TOK_BAR_W):
    """A left-aligned block bar of EXACTLY `width` display columns for value/peak.
    Sub-cell eighths keep near-neighbours apart (153.4k vs 218.2k stay distinguishable
    where whole cells would flatten both). Every glyph is width-1 under char_width, so this
    cell can never shift the columns beside it."""
    width = max(0, int(width))
    if width == 0:
        return ""
    try:
        peak = float(peak or 0)
        frac = (float(value or 0) / peak) if peak > 0 else 0.0
    except (TypeError, ValueError):
        frac = 0.0
    frac = min(1.0, max(0.0, frac))
    full, rem = divmod(int(round(frac * width * 8)), 8)
    full = min(full, width)
    s = "█" * full
    if full < width and rem:
        s += _EIGHTHS[rem]
    return s + " " * (width - display_width(s))


def tok_cell(value, peak, bar_w=TOK_BAR_W, width=TOK_CELL_W):
    """The TOKENS cell: share bar + right-aligned count, at exactly `width` display columns."""
    bar = share_bar(value, peak, bar_w)
    num = clip_to_width(fmt_tokens(value), max(0, width - bar_w - 1))
    return bar + " " * max(0, width - bar_w - display_width(num)) + num


MODEL_DISPLAY_PREFIX = "claude-"


def strip_model_prefix(model):
    """Drop the constant 'claude-' for display. It is the same 7 columns on every row and
    carries no information — reclaiming them is what pays for the TOKENS bar, and it leaves
    the MODEL column wide enough to stop clipping dated snapshot ids. Anything not starting
    with the prefix is returned untouched."""
    m = model or ""
    return m[len(MODEL_DISPLAY_PREFIX):] if m.startswith(MODEL_DISPLAY_PREFIX) else m


# --- durations ---------------------------------------------------------------
DUR_W = 6


def agent_end(a):
    """Best-effort end time (epoch): the agent's last event timestamp, else its file mtime.
    Like agent_start this is a heuristic — Claude Code emits no explicit 'finished' record,
    so a still-running agent's 'end' is simply its latest activity."""
    for e in reversed(a.get("events") or []):
        t = _ts_epoch(e.get("ts"))
        if t is not None:
            return t
    return a.get("mtime") or 0.0


def agent_duration(a):
    """Wall-clock seconds the agent was active, or None if it can't be determined."""
    s, e = agent_start(a), agent_end(a)
    if not s or not e or e < s:
        return None
    return e - s


def fmt_duration(sec):
    """Compact elapsed time, never wider than DUR_W: '42s', '3m22s', '2h05m', '9d03h'."""
    if sec is None:
        return "—"
    s = int(max(0, sec))
    if s < 60:
        return f"{s}s"
    m, ss = divmod(s, 60)
    if m < 60:
        return f"{m}m{ss:02d}s"
    h, mm = divmod(m, 60)
    if h < 24:
        return f"{h}h{mm:02d}m"
    d, hh = divmod(h, 24)
    return f"{d}d{hh:02d}h" if d < 100 else "99d+"


def timeline_bar(start, end, t0, t1, width, running=False):
    """A Gantt strip of EXACTLY `width` display columns placing [start,end] inside the
    window [t0,t1]. Always paints at least one cell, so a 20-second agent next to a
    20-minute one still shows up instead of vanishing into a rounding error."""
    width = max(0, int(width))
    if width == 0:
        return ""
    span = (t1 - t0) if (t0 is not None and t1 is not None and t1 > t0) else 0
    if not span or start is None or end is None:
        return " " * width
    lo = min(max((start - t0) / span, 0.0), 1.0)
    hi = min(max((end - t0) / span, 0.0), 1.0)
    s = min(int(lo * width), width - 1)
    e = min(max(int(round(hi * width)), s + 1), width)
    return " " * s + ("░" if running else "█") * (e - s) + " " * (width - e)


# --- cost estimate -----------------------------------------------------------
# Indicative USD per MILLION tokens, by model family: (input, output, cache_read, cache_write).
# These are ESTIMATES for orientation, NOT billing. Keyed by family so a new dated snapshot
# doesn't need an entry; they go stale whenever pricing changes, and a gateway (a corporate
# proxy, a reseller) may bill at entirely different rates. Override by dropping a JSON file
# of the same shape at .angel-advoc/prices.json. An unpriced family renders '—' rather than
# a confidently wrong number.
PRICES_PER_MTOK = {
    "opus":   (15.00, 75.00, 1.50, 18.75),
    "sonnet": (3.00, 15.00, 0.30, 3.75),
    "haiku":  (1.00, 5.00, 0.10, 1.25),
}
COST_W = 7


def load_prices(repo_root=None):
    """PRICES_PER_MTOK overlaid with .angel-advoc/prices.json when present."""
    prices = dict(PRICES_PER_MTOK)
    if not repo_root:
        return prices
    try:
        with open(os.path.join(repo_root, ".angel-advoc", "prices.json"), "rb") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return prices            # missing/unreadable/not-JSON -> built-in table
    if not isinstance(data, dict):
        return prices
    for fam, row in data.items():
        # Guard PER ROW: a single bad entry must not discard the rows after it, which is
        # what a loop-wide try/except did — making the override's effect depend on key order.
        try:
            if isinstance(row, (list, tuple)) and len(row) == 4:
                prices[str(fam).lower()] = tuple(float(x) for x in row)
        except (TypeError, ValueError):
            continue
    return prices


def estimate_cost(usage, model, prices=None):
    """Indicative USD for one agent's usage, or None when the family has no price entry."""
    p = (prices if prices is not None else PRICES_PER_MTOK).get(model_family(model) or "")
    if not p:
        return None
    u = _norm_usage_keys(usage)
    return (u["input"] * p[0] + u["output"] * p[1]
            + u["cache_read"] * p[2] + u["cache_create"] * p[3]) / 1_000_000


def _norm_usage_keys(u):
    u = u or {}
    return {k: (u.get(k) or 0) for k in USAGE_FIELDS}


def fmt_cost(d):
    """Compact USD, never wider than COST_W: '—', '<$0.01', '$2.14', '$1,234', '$99999+'.

    Same measure-don't-assume rule as fmt_tokens: a `d < 1000` threshold looks safe but
    999.999 formats as '$1,000.00' (9 cols) once .2f rounds up, so each candidate is
    measured and the next-coarser form is used when it doesn't fit."""
    if d is None:
        return "—"
    if d <= 0:
        return "$0.00"
    if d < 0.01:
        return "<$0.01"
    for s in (f"${d:,.2f}", f"${d:,.0f}"):
        if display_width(s) <= COST_W:
            return s
    return "$99999+"


# --- independence badge ------------------------------------------------------
IND_W = 3
# Width-1 glyphs on purpose: '✓'/'✗' measure TWO columns under char_width, which would make
# this narrow column ragged beside the width-1 '~'/'·' states (the alignment bug class this
# viewer keeps re-learning). '√'/'×' read the same and measure 1.
IND_MARKS = {
    "ok":       ("√", "ind_ok"),
    "collapse": ("×", "ind_bad"),
    "inherit":  ("~", "dim"),
    "unknown":  ("?", "ind_warn"),
    "n/a":      ("·", "dim"),
}


def attractor_fields(models, arbiter_model=None):
    """Group a debate's models into 'attractor fields' — one per model FAMILY — and measure
    how much of the debate diverged from the Arbiter's own field.

    This is the cross-model independence property viewed as a shape instead of a flag: agents
    on the Arbiter's family sit in its home field, and every genuinely independent check
    (devil/verifier/red-teamer/interpreter on another family) sits in a divergent one. The
    browser GUI draws these as Steins;Gate-style world lines, but nothing here is decorative —
    the fields are `model_family` groupings and the divergence is a real ratio.

    Returns (fields, divergence):
      fields     ordered list of families, the Arbiter's FIRST (so it is always the home
                 field) and the rest in first-seen order; unknown models are skipped.
      divergence fraction (0.0-1.0) of models NOT in the Arbiter's family. 0.0 when the
                 Arbiter's model is undetermined — unknown never reads as divergent, the same
                 fail-closed stance independence_state takes.
    """
    fams = [model_family(m) for m in models if m]
    home = model_family(arbiter_model) if arbiter_model else None
    ordered = []
    if home:
        ordered.append(home)
    for f in fams:
        if f and f not in ordered:
            ordered.append(f)
    if not home or not fams:
        return ordered, 0.0
    return ordered, sum(1 for f in fams if f != home) / len(fams)


def independence_state(role, model, arbiter_model):
    """Where this agent sits against the cross-model independence rule:
      'ok'       cross-model role genuinely on a different family than the Arbiter
      'collapse' cross-model role that ended up on the Arbiter's family — independence lost
      'inherit'  role that runs on the Arbiter's model by design (not a failure)
      'unknown'  cross-model role but the Arbiter's model is undetermined (fail-closed)
      'n/a'      anything else
    """
    if role in CROSS_MODEL_ROLES:
        if not model or not arbiter_model:
            return "unknown"
        return "collapse" if same_model(model, arbiter_model) else "ok"
    if role in INHERIT_ROLES:
        return "inherit"
    return "n/a"


def independence_mark(role, model, arbiter_model):
    """(glyph, style-key) for the roster's IND column."""
    return IND_MARKS[independence_state(role, model, arbiter_model)]


# --- debate structure highlighting -------------------------------------------
# The advocates emit a consistent vocabulary (DEALBREAKER, CASE FOR/AGAINST, HONEST
# CONCESSION, [PASS]/[FAIL], ...). Colouring it by severity turns a wall of grey prose into
# something skimmable — when you open a debate mid-flight, "what did it find and how bad is
# it" is the first thing you want, and today it reads as undifferentiated text.
_DEBATE_PATTERNS = (
    (re.compile(r"^(?:[-*+•]\s*)?\**\s*DEALBREAKER", re.I), "db"),
    (re.compile(r"^\**\s*\[?FAILS?\b", re.I), "db"),
    (re.compile(r"^\**\s*SHARPEST\s+(?:OBJECTION|ATTACK)", re.I), "db"),
    # The bottom-line verdict of a report, both ways. These MUST precede the generic
    # 'OVERALL' section rule below: without them a failing verdict fell through to the
    # neutral 'sect' style (which draws no severity marker at all) while the passing one
    # matched 'ok' — making bad news strictly less prominent than good news, which is the
    # exact inversion of why this highlighting exists.
    (re.compile(r"^\**\s*OVERALL:?\s*\**\s*FAILS?\b", re.I), "db"),
    (re.compile(r"^(?:[-*+•]\s*)?\**\s*WORTH.NOTING", re.I), "warn"),
    (re.compile(r"^\**\s*\[PARTIAL", re.I), "warn"),
    (re.compile(r"^\**\s*IF YOU FIX ONE THING", re.I), "warn"),
    (re.compile(r"^\**\s*\[PASS\]", re.I), "ok"),
    (re.compile(r"^(?:[-*+•]\s*)?\**\s*(?:HONEST\s+)?CONCE(?:SSION|DE)", re.I), "ok"),
    (re.compile(r"^\**\s*(?:WHAT HOLDS UP|CONFORMS)", re.I), "ok"),
    (re.compile(r"^\**\s*OVERALL:?\s*\**\s*(?:CONFORMS|PASSES)\b", re.I), "ok"),
    (re.compile(r"^\**\s*(?:CASE\s+(?:FOR|AGAINST)|STRONGEST\s+GROUND|CROSS-EXAMINATION"
                r"|CONFORMANCE\s+CHECK|SCOPE\s+DRIFT|OVERALL|FINDINGS|PRECEDENT"
                r"|INTERPRETATIONS|TEST\s+REPORT|DOC\s+SYNC\s+REPORT|HONEST\s+LIMIT)\b",
                re.I), "sect"),
)


def debate_line_style(text):
    """Severity style for a debate line, or None for ordinary prose.
    'db' dealbreaker/fail · 'warn' caveat · 'ok' concession/pass · 'sect' section header."""
    s = (text or "").lstrip()
    for pat, key in _DEBATE_PATTERNS:
        if pat.match(s):
            return key
    return None


# --- roster layout -----------------------------------------------------------
ROLE_W, STAT_W, FACE_W = 15, 13, 5
COL_SEP = " │ "
MODEL_MIN_W = 8
# (key, header, width, drop-priority). width None = elastic: MODEL absorbs the slack.
# drop-priority None = never dropped; otherwise the HIGHEST number is dropped first, so a
# narrowing terminal sheds COST, then TOOK, then FACE, then IND, then STATUS — cosmetic
# columns before load-bearing ones.
ROSTER_SPEC = (
    ("role",   "ROLE",   ROLE_W,     None),
    ("model",  "MODEL",  None,       None),
    ("ind",    "IND",    IND_W,      2),
    ("tokens", "TOKENS", TOK_CELL_W, None),
    ("took",   "TOOK",   DUR_W,      4),
    ("cost",   "COST",   COST_W,     5),
    ("status", "STATUS", STAT_W,     1),
    ("face",   "FACE",   FACE_W,     3),
)


def fit_roster_columns(maxx, spec=ROSTER_SPEC, lead=2, sep=COL_SEP, model_min=MODEL_MIN_W):
    """Pick the roster columns that fit `maxx` and size the elastic one.

    Eight columns do not fit 80 chars, so rather than clipping the row (which would hide
    whichever column happened to land last) this sheds optional columns cheapest-first and
    gives MODEL whatever is left over. Returns [(key, header, width)] in display order.
    Pure, so every layout is testable without a terminal."""
    cols = list(spec)
    avail = max(0, int(maxx) - 1 - lead)

    def need(cs):
        fixed = sum((w if w is not None else model_min) for _, _, w, _ in cs)
        return fixed + len(sep) * max(0, len(cs) - 1)

    while need(cols) > avail:
        droppable = [c for c in cols if c[3] is not None]
        if not droppable:
            break                          # only load-bearing columns left; let it clip
        cols.remove(max(droppable, key=lambda c: c[3]))
    slack = max(0, avail - need(cols))
    return [(k, h, (model_min + slack) if w is None else w) for k, h, w, _ in cols]


def session_transcripts(pdir):
    """Map every session under a project dir to its transcripts:
    {session_id: {"main": <path or None>, "subagents": [paths]}}. The main transcript is
    <pdir>/<id>.jsonl; subagents live under <pdir>/<id>/subagents/."""
    sessions = {}
    for f in glob.glob(os.path.join(pdir, "*.jsonl")):
        sid = os.path.splitext(os.path.basename(f))[0]
        sessions.setdefault(sid, {"main": None, "subagents": []})["main"] = f
    for sub in glob.glob(os.path.join(pdir, "*", "subagents")):
        sid = os.path.basename(os.path.dirname(sub))
        sessions.setdefault(sid, {"main": None, "subagents": []})["subagents"] = agent_files(sub)
    return sessions


# --- debate clustering -------------------------------------------------------
#
# A session's subagents/ dir accumulates every agent for the whole session lifetime, so a
# long session shows dozens of agents from many separate debates. Agents within one debate
# spawn seconds apart; debates are minutes-to-hours apart. Splitting on a start-time gap
# recovers the individual debates so the viewer can show just the current one.
DEBATE_GAP_SECONDS = 300   # a gap larger than this between agent starts begins a new debate


def _ts_epoch(ts):
    """ISO-8601 timestamp string -> epoch seconds, or None if unparseable."""
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def agent_start(a):
    """Best-effort start time (epoch) of an agent: its first event's timestamp, else its
    file mtime. Used to order and cluster agents into debates."""
    for e in a.get("events") or []:
        t = _ts_epoch(e.get("ts"))
        if t is not None:
            return t
    return a.get("mtime") or 0.0


def cluster_debates(agents, gap_seconds=DEBATE_GAP_SECONDS):
    """Group agents into debate clusters by start-time gaps. Returns a list of clusters (each a
    list of agents) in chronological order; a gap > gap_seconds between consecutive starts opens
    a new cluster. Agents within a cluster keep their given order."""
    ordered = sorted(agents, key=agent_start)
    clusters, cur, prev = [], [], None
    for a in ordered:
        s = agent_start(a)
        if prev is not None and (s - prev) > gap_seconds:
            clusters.append(cur)
            cur = []
        cur.append(a)
        prev = s
    if cur:
        clusters.append(cur)
    return clusters


def load_agents_full(subagents_dir):
    """Read every agent transcript fully (offset 0) and return a list of
    {path, role, model, events, mtime, usage} — the one-shot / replay view."""
    agents = []
    for path in agent_files(subagents_dir):
        role, model = file_role(path)
        events, _ = parse_new(path, 0)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0
        agents.append(
            {"path": path, "role": role, "model": model, "events": events,
             "mtime": mtime, "usage": usage_from_file(path)}
        )
    # order by first activity (file name is stable; sort by mtime of first event/file)
    agents.sort(key=lambda a: a["path"])
    return agents


def snapshot(subagents_dir, arbiter_model=None, now=None, repo_root=None):
    """A JSON-serializable view of a debate for non-curses consumers (the browser GUI).

    Reuses the SAME tested brain as the terminal viewer — load + status + duration + cost
    + independence — so the two front-ends can never diverge in what they report. Pure
    data: no curses, and no I/O beyond reading the transcripts load_agents_full already reads.

    Returns {"agents": [...], "independence": {...}, "arbiter_model": <str|None>}. Each agent:
    {id, role, model, status, start, end, duration_sec, usage, cost, last_kind, activity,
    indep, family, heat, tok_share, events}, where `id` is the
    agent's transcript filename — a STABLE, UNIQUE handle so a consumer can distinguish two
    agents that share a role AND model (e.g. two angels in a fork, or two verifier passes on
    the same model); keying on role+model alone would conflate them. `events` are the raw event
    dicts (kind + text/name/input) for the front-end to render as it sees fit.
    """
    if now is None:
        now = time.time()
    agents_raw = load_agents_full(subagents_dir)
    prices = load_prices(repo_root)
    indep = check_independence(agents_raw, arbiter_model=arbiter_model)
    arbiter = indep.get("arbiter_model")
    # Token heat is a CROSS-agent derivation: it needs the debate's peak before any single
    # row can be coloured. Compute it here (via the lib's own heat_bucket / usage_total) so the
    # GUI colours rows straight from a `heat` field instead of re-deriving the bucket math —
    # the same reason status/activity/indep are derived server-side. `heat` is the 0..N-1 bucket
    # (N = HEAT_BUCKETS, matching the terminal); `tok_share` is the raw value/peak fraction the
    # front-end needs only for a proportional bar width (a rendering concern, not a derivation).
    peak = max((usage_total(a.get("usage")) for a in agents_raw), default=0)
    # Attractor fields (model families) + the debate's divergence from the Arbiter's own
    # field — another cross-agent derivation, so it belongs here rather than in the GUI.
    fields, divergence = attractor_fields([a["model"] for a in agents_raw], arbiter)
    out = []
    for a in agents_raw:
        events = a.get("events") or []
        last_kind = events[-1]["kind"] if events else None
        status = agent_status(a["mtime"], now=now)
        total = usage_total(a.get("usage"))
        out.append({
            "id": os.path.basename(a["path"]),   # agent-<uuid>.jsonl — unique per agent
            "role": a["role"],
            "model": a["model"],
            "status": status,
            "start": agent_start(a),
            "end": agent_end(a),
            "duration_sec": agent_duration(a),
            "usage": a.get("usage") or blank_usage(),
            "cost": estimate_cost(a.get("usage"), a["model"], prices),
            # Derived here (not client-side) so the GUI shares the terminal's exact brain:
            "last_kind": last_kind,                    # raw last event kind
            "activity": activity_label(last_kind) if status == "active" else "",
            "indep": independence_state(a["role"], a["model"], arbiter),  # ok/collapse/inherit/…
            "family": model_family(a["model"]),        # attractor field this world line sits in
            "heat": heat_bucket(total, peak),          # 0..HEAT_BUCKETS-1, lib-owned bucket
            "tok_share": (total / peak) if peak > 0 else 0.0,  # for a proportional bar width
            "events": events,
        })
    return {"agents": out, "independence": indep, "arbiter_model": arbiter,
            "heat_buckets": HEAT_BUCKETS,
            "attractor_fields": fields,      # model families, the Arbiter's own first
            "divergence": divergence}        # real ratio of agents outside the Arbiter's family
