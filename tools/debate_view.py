#!/usr/bin/env python3
"""debate_view.py — live terminal viewer for Angel's Advocate agents.

Run it in a second terminal while a debate runs in Claude Code. It tails the current
session's subagent transcripts and shows, per role (angel/devil/verifier/…), the model,
a live status, and a stream of that agent's thinking + tool calls + output.

  tools/debate-view.sh                 live view of the most-recently-active session
  tools/debate-view.sh <session-id>    a specific session under this project
  tools/debate-view.sh --once          one-shot dump (replay / pipe to less); also the
                                       automatic mode when stdout is not a TTY
  tools/debate-view.sh --check-independence   verify cross-model roles ran on a model !=
                                       the Arbiter's, from ACTUAL runtime models (exit 1 on
                                       collapse, 2 if unverified) — the ground-truth check

All parsing lives in debate_lib (unit-tested). This file is the thin curses shell:
roster pane on top, streaming detail for the focused agent below.

Keys:  q quit   j/↓ next agent   k/↑ prev agent   f toggle follow-newest   a expand roster
The roster scrolls to keep the selected agent in view (▲/▼ mark more above/below); press `a`
to expand it full-screen and scan every agent when the cast is large.

Honest caveat surfaced in the UI: the active/done status is an mtime heuristic (Claude
Code emits no explicit per-agent 'finished' marker in the transcript), not ground truth.
"""
from __future__ import annotations

import argparse
import locale
import os
import sys
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


def run_check_independence(subagents_dir, label, arbiter_model):
    """Ground-truth, post-hoc check: did every cross-model role actually run on a model
    != the Arbiter's? Reads the actual runtime model from each transcript. Exit codes:
    0 held / nothing-to-check, 1 collapse, 2 unverified (fail-closed)."""
    agents = dl.load_agents_full(subagents_dir)
    if not agents:
        print(f"debate-view: no agent transcripts yet in session {label}.")
        return 2
    if not arbiter_model:
        arbiter_model = os.environ.get("ANTHROPIC_MODEL") or None
    result = dl.check_independence(agents, arbiter_model=arbiter_model)
    print(f"Angel's Advocate — session {label}\n")
    print(dl.render_independence(result))
    return {"ok": 0, "nothing-to-check": 0, "collapse": 1, "unverified": 2}[result["status"]]


# --- live curses UI ----------------------------------------------------------

def _detail_rows(agent, width):
    """Build the detail pane as styled rows. Each row is a list of (text, style) segments;
    style is a key into the attr map (_build_attrs). Markdown in thinking/output is rendered
    into real styles (bold/code/header/bullet) instead of showing literal ** and ` syntax."""
    rows = []

    def para(text, indent):
        for raw in (text or "").splitlines():
            kind, content, lead = dl.classify_line(raw)
            base = indent + (lead // 2)
            if kind == "fence":
                continue  # drop ``` fence lines; the content between still renders
            if kind == "header":
                for r in dl.wrap_segments(dl.parse_inline(content), width, base):
                    rows.append([(t, "mdh") for t, _ in r])   # whole header line accented
            elif kind == "bullet":
                rows.extend(dl.wrap_segments([("• ", "bullet")] + dl.parse_inline(content), width, base))
            elif kind == "quote":
                rows.extend(dl.wrap_segments([("┃ ", "dim")] + dl.parse_inline(content), width, base))
            elif content.strip() == "":
                rows.append([("", "plain")])
            else:
                rows.extend(dl.wrap_segments(dl.parse_inline(content), width, base))

    for e in agent["events"]:
        k = e["kind"]
        if k == "thinking":
            rows.append([(dl.sec_head("thinking"), "think")]); para(e.get("text"), 3)
        elif k == "text":
            rows.append([(dl.sec_head("output"), "label")]); para(e.get("text"), 3)
        elif k == "tool_use":
            rows.append([(f"{e.get('name', '')} ", "tool"),
                         (dl.short_tool_input(e.get("input")), "dim")])
        elif k == "tool_result":
            snippet = " ".join((e.get("text") or "").split())
            rows.extend(dl.wrap_segments([("↳ " + snippet[:400], "dim")], width, 3))
        elif k == "prompt":
            snippet = " ".join((e.get("text") or "").split())
            rows.extend(dl.wrap_segments([("briefed: " + snippet[:200], "dim")], width, 0))
    return rows


def _build_attrs(curses):
    """style-key -> curses attribute. Uses color when the terminal has it, degrading to
    bold/dim/reverse otherwise so it stays readable on a mono terminal."""
    a = {"plain": 0, "label": curses.A_BOLD, "think": curses.A_DIM, "b": curses.A_BOLD,
         "c": curses.A_REVERSE, "mdh": curses.A_BOLD, "dim": curses.A_DIM,
         "tool": curses.A_BOLD, "bullet": curses.A_BOLD,
         "bar": curses.A_REVERSE, "active": curses.A_BOLD, "done": curses.A_DIM,
         "hdr": curses.A_BOLD | curses.A_UNDERLINE}
    try:
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_MAGENTA, -1)
            C = curses.color_pair
            a["label"] = C(1) | curses.A_BOLD
            a["think"] = C(4) | curses.A_DIM
            a["mdh"] = C(1) | curses.A_BOLD
            a["c"] = C(3)
            a["tool"] = C(2) | curses.A_BOLD
            a["bullet"] = C(1) | curses.A_BOLD
            a["active"] = C(2) | curses.A_BOLD
            a["hdr"] = C(1) | curses.A_BOLD
    except Exception:
        pass  # any color init failure -> keep the mono fallbacks
    return a


def _safe_addstr(win, y, x, s, attr=0):
    try:
        win.addstr(y, x, s, attr)
    except Exception:
        pass  # off-screen / unrenderable glyph — never let drawing crash the loop


def _render_row(win, y, x0, row, attrs, maxx):
    """Paint one styled row (list of (text, style)) left-to-right, clipped to the width.
    Advances by DISPLAY width (not len) so variation-selector emoji like 🛡️ don't push the
    rest of the row out of alignment."""
    x = x0
    for text, key in row:
        avail = maxx - 1 - x
        if avail <= 0:
            break
        seg = dl.clip_to_width(text, avail)
        _safe_addstr(win, y, x, seg, attrs.get(key, 0))
        x += dl.display_width(seg)


def _clamp_scroll(focus, top, height, n):
    """Scroll offset that keeps `focus` inside a window of `height` rows over `n` items."""
    if focus < top:
        top = focus
    elif focus >= top + height:
        top = focus - height + 1
    return max(0, min(top, max(0, n - height)))


def _draw_roster(win, ordered, focus, roster_top, top_y, roster_h, attrs, maxx, now):
    """Draw the scrollable roster window. Shows ▶ on the focused agent and ▲/▼ on the top/
    bottom visible row when the list is scrolled past that edge, so no agent is silently hidden.

    Alignment note: emoji display width is terminal-dependent (a variation-selector glyph like
    🛡️ can render 1–3 columns, and no width table can predict every terminal/font). So the
    columns that must line up — name, model, status — are led by fixed-width ASCII and geometric
    glyphs only; the role emoji is placed at the END of the row, where its width shifts nothing."""
    n = len(ordered)
    for row_i in range(roster_h):
        idx = roster_top + row_i
        if idx >= n:
            break
        a = ordered[idx]
        status = dl.agent_status(a["mtime"], now=now)
        focused = (idx == focus)
        if focused:
            marker = "▶"
        elif row_i == 0 and roster_top > 0:
            marker = "▲"
        elif row_i == roster_h - 1 and idx < n - 1:
            marker = "▼"
        else:
            marker = " "
        dot = "*" if status == "active" else " "     # ASCII status flag (width-stable)
        act = dl.activity_label(a["events"][-1]["kind"]) if (status == "active" and a["events"]) else "done"
        st_key = "active" if status == "active" else "done"
        row = [
            (f"{marker} ", "active" if focused else "plain"),          # ▶/▲/▼ are width-1 geometric
            (f"{(a['role'] or '?'):<12}", "active" if focused else "plain"),
            (f"[{a['model'] or '?'}] ", "dim"),
            (f"{dot} ", st_key),
            (f"{act:<12}", st_key),
        ]
        em = dl.role_emoji(a["role"])                                   # only angel/devil; LAST so
        if em:                                                         # its width can't misalign
            row.append((f" {em}", "plain"))
        _render_row(win, top_y + row_i, 0, row, attrs, maxx)


def run_live(stdscr, subagents_dir, label):
    import curses

    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(int(POLL_SECONDS * 1000))
    attrs = _build_attrs(curses)

    agents = {}   # path -> {path, role, model, offset, events, mtime, order}
    order = 0
    focus = 0
    follow = True
    roster_top = 0     # scroll offset for the roster window
    expand = False     # 'a' -> full-screen roster (scan the whole agent list)

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
        n = len(ordered)

        pos = f"{focus + 1}/{n}" if n else "0/0"
        title = f" Angel's Advocate · {label} · {pos} agent(s) "
        _safe_addstr(stdscr, 0, 0, title.ljust(maxx - 1)[: maxx - 1], attrs["bar"])

        if not n:
            _safe_addstr(stdscr, 2, 0, " waiting for agents to spawn… ", attrs["dim"])
        elif expand:
            # full-screen roster: scan the whole list, scrolling to keep focus visible
            roster_h = max(1, maxy - 2)
            roster_top = _clamp_scroll(focus, roster_top, roster_h, n)
            _draw_roster(stdscr, ordered, focus, roster_top, 1, roster_h, attrs, maxx, now)
        else:
            # split: roster takes what it needs, up to ~60% of the screen; scrolls beyond that
            # (so a big cast never silently hides agents) — detail keeps the rest.
            roster_h = min(n, max(3, (maxy - 3) * 3 // 5))
            roster_top = _clamp_scroll(focus, roster_top, roster_h, n)
            _draw_roster(stdscr, ordered, focus, roster_top, 1, roster_h, attrs, maxx, now)

            sep_y = 1 + roster_h
            _safe_addstr(stdscr, sep_y, 0, "─" * (maxx - 1), attrs["dim"])
            a = ordered[focus]
            em = dl.role_emoji(a["role"])
            hg = f"{em} " if em else ""
            hdr = f" {hg}{a['role'] or '?'} — {a['model'] or '?'} "
            _safe_addstr(stdscr, sep_y + 1, 0, hdr.ljust(maxx - 1)[: maxx - 1], attrs["hdr"])

            body_top = sep_y + 2
            body_h = maxy - body_top - 1
            if body_h > 0:
                rows = _detail_rows(a, maxx - 1)
                hint = len(rows) > body_h          # more content than fits -> reserve a hint line
                content_h = body_h - (1 if hint else 0)
                tail = rows[-content_h:] if content_h > 0 else []
                if hint:
                    hidden = len(rows) - len(tail)
                    _safe_addstr(stdscr, body_top, 0,
                                 f"  ↑ {hidden} earlier line(s) · showing newest "[: maxx - 1],
                                 attrs["dim"])
                for j, r in enumerate(tail):
                    _render_row(stdscr, body_top + (1 if hint else 0) + j, 0, r, attrs, maxx)

        footer = (f" q quit · j/k select · f follow:{'on' if follow else 'off'} · "
                  f"a {'split view' if expand else 'all agents'} · ● active ")
        _safe_addstr(stdscr, maxy - 1, 0, footer.ljust(maxx - 1)[: maxx - 1], attrs["bar"])
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
        elif ch == ord("a"):
            expand = not expand


def main(argv=None):
    locale.setlocale(locale.LC_ALL, "")
    ap = argparse.ArgumentParser(description="Live terminal viewer for Angel's Advocate agents.")
    ap.add_argument("session", nargs="?", help="session id (default: most recent active)")
    ap.add_argument("--once", action="store_true", help="one-shot dump instead of live view")
    ap.add_argument("--check-independence", action="store_true",
                    help="verify cross-model roles ran on a model != the Arbiter's (ground "
                         "truth, from actual runtime models); exit 1 on collapse, 2 if unverified")
    ap.add_argument("--arbiter-model",
                    help="the Arbiter's actual model, for --check-independence (default: infer "
                         "from an inherit-role agent, else $ANTHROPIC_MODEL)")
    ap.add_argument("--project", help="project dir override (default: derived from cwd)")
    ap.add_argument("--subagents", help="point directly at a subagents/ dir (bypasses discovery)")
    ap.add_argument("--home", help="home dir override (testing)")
    args = ap.parse_args(argv)

    subagents_dir, label = resolve_subagents_dir(args)
    if not subagents_dir:
        print(f"debate-view: {label}", file=sys.stderr)
        return 2

    if args.check_independence:
        return run_check_independence(subagents_dir, label, args.arbiter_model)

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
