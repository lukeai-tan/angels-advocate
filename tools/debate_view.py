#!/usr/bin/env python3
"""debate_view.py — live terminal viewer for Angel's Advocate agents.

Run it in a second terminal while a debate runs in Claude Code. It tails the current
session's subagent transcripts and shows, per role (angel/devil/verifier/…), the model,
a live status, and a stream of that agent's thinking + tool calls + output.

  tools/debate-view.sh                 live view of the most-recently-active session
  tools/debate-view.sh <session-id>    a specific session under this project
  tools/debate-view.sh --once          one-shot dump (replay / pipe to less); also the
                                       automatic mode when stdout is not a TTY

All parsing lives in debate_lib (unit-tested). This file is the thin curses shell:
roster pane on top, streaming detail for the focused agent below.

Keys:  q quit   j/↓ next agent   k/↑ prev agent   f toggle follow-newest

Honest caveat surfaced in the UI: the active/done status is an mtime heuristic (Claude
Code emits no explicit per-agent 'finished' marker in the transcript), not ground truth.
"""
from __future__ import annotations

import argparse
import locale
import os
import sys
import textwrap
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import debate_lib as dl  # noqa: E402

POLL_SECONDS = 0.5


def resolve_subagents_dir(args):
    """Return (subagents_dir, session_label) or (None, reason)."""
    if args.subagents:
        return (args.subagents, os.path.basename(os.path.dirname(args.subagents)))
    pdir = args.project or dl.project_dir(os.getcwd(), home=args.home)
    if args.session:
        sess = os.path.join(pdir, args.session)
    else:
        sess = dl.discover_session(pdir)
    if not sess:
        return (None, f"no sessions with subagents found under {pdir}")
    sub = os.path.join(sess, "subagents")
    if not os.path.isdir(sub):
        return (None, f"no subagents/ dir in {sess}")
    return (sub, os.path.basename(sess))


# --- one-shot / non-TTY dump -------------------------------------------------

def run_once(subagents_dir, label):
    agents = dl.load_agents_full(subagents_dir)
    if not agents:
        print(f"debate-view: no agent transcripts yet in session {label}.")
        return 0
    print(f"Angel's Advocate — session {label}  ({len(agents)} agent(s))\n")
    print(dl.render_dump(agents))
    return 0


# --- live curses UI ----------------------------------------------------------

def _detail_lines(agent, width):
    """Flatten one agent's events into wrapped display lines for the detail pane."""
    out = []
    wrap = lambda s, sub: textwrap.wrap(s, max(10, width - len(sub)), subsequent_indent=" " * len(sub)) or [""]
    for e in agent["events"]:
        k = e["kind"]
        if k == "thinking":
            out.append("💭 thinking")
            for ln in (e.get("text") or "").splitlines():
                out.extend("   " + w for w in wrap(ln, "   "))
        elif k == "text":
            out.append("📣 output")
            for ln in (e.get("text") or "").splitlines():
                out.extend("   " + w for w in wrap(ln, "   "))
        elif k == "tool_use":
            head = f"🔧 {e.get('name', '')}  {dl.short_tool_input(e.get('input'))}"
            out.extend(wrap(head, ""))
        elif k == "tool_result":
            snippet = " ".join((e.get("text") or "").split())
            out.extend("   ↳ " + w for w in wrap(snippet[:400], "   ↳ "))
        elif k == "prompt":
            snippet = " ".join((e.get("text") or "").split())
            out.extend("… briefed: " + w for w in wrap(snippet[:200], "… briefed: "))
    return out


def _safe_addstr(win, y, x, s, attr=0):
    try:
        win.addstr(y, x, s, attr)
    except Exception:
        pass  # off-screen / unrenderable glyph — never let drawing crash the loop


def run_live(stdscr, subagents_dir, label):
    import curses

    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(int(POLL_SECONDS * 1000))

    agents = {}   # path -> {path, role, model, offset, events, mtime, order}
    order = 0
    focus = 0
    follow = True

    while True:
        # --- ingest new data ---
        now = time.time()
        for path in dl.agent_files(subagents_dir):
            st = agents.get(path)
            if st is None:
                role, model = dl.file_role(path)
                st = {"path": path, "role": role, "model": model,
                      "offset": 0, "events": [], "mtime": 0.0, "order": order}
                order += 1
                agents[path] = st
            new_events, st["offset"] = dl.parse_new(path, st["offset"])
            if new_events:
                st["events"].extend(new_events)
                # backfill role/model if the first tagged line arrived only now
                if not st["role"] or not st["model"]:
                    r, m = dl.file_role(path)
                    st["role"] = st["role"] or r
                    st["model"] = st["model"] or m
            try:
                st["mtime"] = os.path.getmtime(path)
            except OSError:
                pass

        ordered = sorted(agents.values(), key=lambda a: a["order"])
        if ordered and follow:
            # focus the most-recently-active agent
            newest = max(range(len(ordered)), key=lambda i: ordered[i]["mtime"])
            focus = newest
        focus = max(0, min(focus, len(ordered) - 1)) if ordered else 0

        # --- draw ---
        stdscr.erase()
        maxy, maxx = stdscr.getmaxyx()
        roster_h = min(len(ordered) + 1, max(3, maxy // 3)) if ordered else 1

        _safe_addstr(stdscr, 0, 0,
                     f" Angel's Advocate · session {label} · {len(ordered)} agent(s) "
                     f"· follow:{'on' if follow else 'off'} "[: maxx - 1],
                     curses.A_REVERSE)

        for i, a in enumerate(ordered):
            y = 1 + i
            if y >= roster_h:
                break
            status = dl.agent_status(a["mtime"], now=now)
            dot = "●" if status == "active" else "○"
            act = dl.activity_label(a["events"][-1]["kind"]) if (status == "active" and a["events"]) else "done"
            line = f" {dl.role_emoji(a['role'])} {(a['role'] or '?'):<11} [{a['model'] or '?'}] {dot} {act}"
            attr = curses.A_BOLD if i == focus else 0
            _safe_addstr(stdscr, y, 0, line[: maxx - 1], attr)

        sep_y = roster_h
        _safe_addstr(stdscr, sep_y, 0, "─" * (maxx - 1))

        if ordered:
            a = ordered[focus]
            hdr = f" {dl.role_emoji(a['role'])} {a['role'] or '?'} — {a['model'] or '?'} "
            _safe_addstr(stdscr, sep_y + 1, 0, hdr[: maxx - 1], curses.A_UNDERLINE)
            body_top = sep_y + 2
            body_h = maxy - body_top - 1
            lines = _detail_lines(a, maxx - 1)
            tail = lines[-body_h:] if body_h > 0 else []
            for j, ln in enumerate(tail):
                _safe_addstr(stdscr, body_top + j, 0, ln[: maxx - 1])
        else:
            _safe_addstr(stdscr, sep_y + 1, 0, " waiting for agents to spawn… ")

        _safe_addstr(stdscr, maxy - 1, 0,
                     " q quit · j/k switch agent · f follow · status is an mtime heuristic "[: maxx - 1],
                     curses.A_REVERSE)
        stdscr.refresh()

        # --- input ---
        try:
            ch = stdscr.getch()
        except KeyboardInterrupt:
            break
        if ch in (ord("q"), 27):
            break
        elif ch in (ord("j"), curses.KEY_DOWN):
            follow = False
            if ordered:
                focus = min(focus + 1, len(ordered) - 1)
        elif ch in (ord("k"), curses.KEY_UP):
            follow = False
            if ordered:
                focus = max(focus - 1, 0)
        elif ch == ord("f"):
            follow = not follow


def main(argv=None):
    locale.setlocale(locale.LC_ALL, "")
    ap = argparse.ArgumentParser(description="Live terminal viewer for Angel's Advocate agents.")
    ap.add_argument("session", nargs="?", help="session id (default: most recent active)")
    ap.add_argument("--once", action="store_true", help="one-shot dump instead of live view")
    ap.add_argument("--project", help="project dir override (default: derived from cwd)")
    ap.add_argument("--subagents", help="point directly at a subagents/ dir (bypasses discovery)")
    ap.add_argument("--home", help="home dir override (testing)")
    args = ap.parse_args(argv)

    subagents_dir, label = resolve_subagents_dir(args)
    if not subagents_dir:
        print(f"debate-view: {label}", file=sys.stderr)
        return 2

    if args.once or not sys.stdout.isatty():
        return run_once(subagents_dir, label)

    import curses
    try:
        curses.wrapper(run_live, subagents_dir, label)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
