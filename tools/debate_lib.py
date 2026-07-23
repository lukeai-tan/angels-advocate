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
import time

# Role -> emoji for the roster/detail. Unknown roles fall back to a neutral marker.
ROLE_EMOJI = {
    "angel": "😇",
    "devil": "😈",
    "arbiter": "⚖️",
    "verifier": "✅",
    "researcher": "🔎",
    "red-teamer": "🛡️",
    "interpreter": "🧭",
    "profiler": "📊",
    "historian": "📚",
    "scribe": "📝",
    "test-writer": "🧪",
    "tldr": "✂️",
}

# last-event-kind -> short activity label for an active agent
_ACTIVITY = {
    "thinking": "💭 thinking…",
    "tool_use": "🔧 running tool…",
    "tool_result": "⏳ got result…",
    "text": "📣 writing…",
    "prompt": "… briefed",
}


def role_emoji(role):
    return ROLE_EMOJI.get(role or "", "🔹")


def activity_label(last_kind):
    return _ACTIVITY.get(last_kind or "", "…")


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


def render_dump(agents):
    """Plain-text, grouped-by-role transcript for --once / non-TTY output.
    `agents` is a list of dicts: {role, model, events:[...]}. Returns a string."""
    lines = []
    for a in agents:
        role = a.get("role")
        lines.append(f"{role_emoji(role)} {role or '(unknown)'}  ({a.get('model') or '?'})")
        for e in a.get("events", []):
            k = e.get("kind")
            if k == "thinking":
                lines.append("  💭 thinking")
                for ln in (e.get("text") or "").splitlines():
                    lines.append(f"     {ln}")
            elif k == "text":
                lines.append("  📣 output")
                for ln in (e.get("text") or "").splitlines():
                    lines.append(f"     {ln}")
            elif k == "tool_use":
                lines.append(f"  🔧 {e.get('name', '')}  {short_tool_input(e.get('input'))}")
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
        if f["model"] == arbiter_model:
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
