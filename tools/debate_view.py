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

Keys:  q quit   j/↓,k/↑ select agent   PgUp/PgDn scroll output (g/G top/bottom)
       [ ] prev/next debate   a all-agents toggle   f follow-newest
By default the roster shows only the CURRENT debate — a session's subagents/ dir accumulates
every agent for the whole session, so they're grouped into debates by start-time gap and only
the newest cluster is shown. `[`/`]` step through older debates; `a` shows the whole session.
The detail pane follows the newest output; PgUp scrolls up to read earlier reasoning (G resumes).

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


_ROLE_W = 15          # role column: fits the longest name ("interpreter"/"test-writer") + " 😇"
_TOK_W = 8            # tokens column (compact, e.g. "1.9M")
_STAT_W = 13          # status column
_FACE_W = 5           # reactive-face column (every face frame is exactly 5 cols)
_SEP = " │ "          # column separator (│ is width-1 geometric, alignment-safe)


def _roster_model_w(maxx):
    """Width of the model column, filling what's left after the fixed columns."""
    return max(8, maxx - 1 - 2 - _ROLE_W - _TOK_W - _STAT_W - _FACE_W - 4 * len(_SEP))


def _draw_roster(win, ordered, focus, roster_top, top_y, total_h, attrs, maxx, now):
    """Draw the scrollable roster as a table: a ROLE │ MODEL │ STATUS header + rule, then agent
    rows. Shows ▶ on the focused agent and ▲/▼ when scrolled past the top/bottom edge. Fields use
    display-aware padding, so the angel/devil emoji (placed right after the name) keeps the columns
    aligned. `total_h` includes the 2 header rows; the rest are agent rows."""
    model_w = _roster_model_w(maxx)
    frame = int(now * dl.FACE_FPS)                 # animation frame (advances with wall-clock)
    # header + rule
    header = ("  " + dl.pad_display("ROLE", _ROLE_W) + _SEP
              + dl.pad_display("MODEL", model_w) + _SEP
              + dl.pad_display("TOKENS", _TOK_W) + _SEP
              + dl.pad_display("STATUS", _STAT_W) + _SEP + "FACE")
    _safe_addstr(win, top_y, 0, header[: maxx - 1], attrs["hdr"])
    rule_w = min(maxx - 1, 2 + _ROLE_W + _TOK_W + _STAT_W + _FACE_W + 4 * len(_SEP) + model_w)
    _safe_addstr(win, top_y + 1, 0, "─" * rule_w, attrs["dim"])

    body_y = top_y + 2
    vis = max(1, total_h - 2)
    n = len(ordered)
    for row_i in range(vis):
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
        elif row_i == vis - 1 and idx < n - 1:
            marker = "▼"
        else:
            marker = " "
        dot = "*" if status == "active" else " "     # ASCII status flag (width-stable)
        last_kind = a["events"][-1]["kind"] if a["events"] else None
        act = dl.activity_label(last_kind) if (status == "active" and a["events"]) else "done"
        st_key = "active" if status == "active" else "done"
        em = dl.role_emoji(a["role"])                 # sits right after the name
        name = f"{a['role'] or '?'}" + (f" {em}" if em else "")
        tok = dl.fmt_tokens(dl.usage_total(a.get("usage") or {}))
        fc = dl.face(a["role"], dl.face_state(status, last_kind), frame)  # animated reactive face
        row = [
            (f"{marker} ", "active" if focused else "plain"),
            (dl.pad_display(name, _ROLE_W), "active" if focused else "plain"),
            (_SEP, "dim"),
            (dl.pad_display(a["model"] or "?", model_w), "dim"),
            (_SEP, "dim"),
            (dl.pad_display(tok, _TOK_W), "plain"),
            (_SEP, "dim"),
            (dl.pad_display(f"{dot} {act}", _STAT_W), st_key),
            (_SEP, "dim"),
            (dl.pad_display(fc, _FACE_W), st_key),
        ]
        _render_row(win, body_y + row_i, 0, row, attrs, maxx)


def run_live(stdscr, subagents_dir, label):
    import curses

    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(int(POLL_SECONDS * 1000))
    attrs = _build_attrs(curses)

    agents = {}   # path -> {path, role, model, offset, events, mtime, order, usage}
    order = 0
    focus = 0
    follow = True
    roster_top = 0     # scroll offset for the roster window
    show_all = False   # 'a' -> show the whole session; default is the current debate only
    debate_idx = 0     # which debate cluster is shown (default follows the newest)
    detail_off = 0     # detail-pane scroll: rows up from the newest; 0 = follow the tail
    detail_path = None # focused agent path, to reset detail scroll when focus changes
    detail_max = 0     # last frame's max detail scroll (for clamping key input)
    detail_page = 1    # last frame's detail page size (for PgUp/PgDn)

    while True:
        # --- ingest new data ---
        now = time.time()
        for path in dl.agent_files(subagents_dir):
            st = agents.get(path)
            if st is None:
                role, model = dl.file_role(path)
                st = {"path": path, "role": role, "model": model, "offset": 0,
                      "events": [], "mtime": 0.0, "order": order,
                      "usage": dl.blank_usage(), "usage_m": -1.0}
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
            if st["mtime"] > st["usage_m"]:          # re-sum usage only when the file changed
                st["usage"] = dl.usage_from_file(path)
                st["usage_m"] = st["mtime"]

        ordered = sorted(agents.values(), key=lambda a: a["order"])
        # scope to a single debate (default) or the whole session ('a' toggles show_all).
        # Debates are recovered by splitting on a start-time gap (see dl.cluster_debates).
        if show_all:
            clusters = [ordered] if ordered else []
        else:
            clusters = dl.cluster_debates(ordered)
        if follow and clusters:
            debate_idx = len(clusters) - 1          # live: follow the current (newest) debate
        debate_idx = max(0, min(debate_idx, len(clusters) - 1)) if clusters else 0
        shown = clusters[debate_idx] if clusters else []
        if shown and follow:
            focus = max(range(len(shown)), key=lambda i: shown[i]["mtime"])
        focus = max(0, min(focus, len(shown) - 1)) if shown else 0

        # --- draw ---
        stdscr.erase()
        maxy, maxx = stdscr.getmaxyx()
        n = len(shown)

        sess_u = dl.blank_usage()                    # token total is session-wide (all agents)
        for a in agents.values():
            sess_u = dl.add_usage(sess_u, a.get("usage") or {})
        toks = f" · {dl.fmt_tokens(dl.usage_total(sess_u))} tok" if agents else ""
        if show_all:
            scope = f"all {len(ordered)} agent(s)"
        elif clusters:
            scope = f"debate {debate_idx + 1}/{len(clusters)} · {n} agent(s)"
        else:
            scope = "0 agents"
        title = f" Angel's Advocate · {label} · {scope}{toks} "
        _safe_addstr(stdscr, 0, 0, title.ljust(maxx - 1)[: maxx - 1], attrs["bar"])

        if not n:
            _safe_addstr(stdscr, 2, 0, " waiting for agents to spawn… ", attrs["dim"])
        else:
            # split: roster (+2 for the header) up to ~60% of the screen, scrolls beyond that.
            roster_h = min(n + 2, max(5, (maxy - 3) * 3 // 5))
            roster_top = _clamp_scroll(focus, roster_top, roster_h - 2, n)
            _draw_roster(stdscr, shown, focus, roster_top, 1, roster_h, attrs, maxx, now)

            sep_y = 1 + roster_h
            _safe_addstr(stdscr, sep_y, 0, "─" * (maxx - 1), attrs["dim"])
            a = shown[focus]
            em = dl.role_emoji(a["role"])
            name = f"{a['role'] or '?'}" + (f" {em}" if em else "")   # emoji after the name
            hdr = f" {name} — {a['model'] or '?'} "
            _safe_addstr(stdscr, sep_y + 1, 0, hdr.ljust(maxx - 1)[: maxx - 1], attrs["hdr"])

            if a["path"] != detail_path:       # focus changed -> jump back to the newest
                detail_path = a["path"]
                detail_off = 0
            body_top = sep_y + 2
            body_h = maxy - body_top - 1
            if body_h > 0:
                rows = _detail_rows(a, maxx - 1)
                total = len(rows)
                scrollable = total > body_h
                avail = body_h - (1 if scrollable else 0)    # reserve a status line when scrolling
                detail_max = max(0, total - avail)
                detail_off = max(0, min(detail_off, detail_max))
                detail_page = max(1, avail - 1)
                end = total - detail_off
                start = max(0, end - avail)
                window = rows[start:end]
                if scrollable:
                    above, below = start, total - end
                    if detail_off == 0:
                        status = f"  ↑ {above} earlier line(s) · following newest — PgUp to scroll "
                    else:
                        status = f"  ↕ {above} above · {below} below · G newest · PgUp/PgDn "
                    _safe_addstr(stdscr, body_top, 0, status[: maxx - 1], attrs["dim"])
                    base = body_top + 1
                else:
                    base = body_top
                for j, r in enumerate(window):
                    _render_row(stdscr, base + j, 0, r, attrs, maxx)

        footer = (f" q quit · j/k select · PgUp/PgDn scroll · [ ] debate · "
                  f"a {'one debate' if show_all else 'all agents'} · f follow:{'on' if follow else 'off'} ")
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
            if shown:
                focus = min(focus + 1, len(shown) - 1)
        elif ch in (ord("k"), curses.KEY_UP):
            follow = False
            if shown:
                focus = max(focus - 1, 0)
        elif ch in (ord("]"), curses.KEY_RIGHT):       # next (newer) debate
            follow = False
            debate_idx = min(debate_idx + 1, max(0, len(clusters) - 1))
            focus = 0
        elif ch in (ord("["), curses.KEY_LEFT):        # previous (older) debate
            follow = False
            debate_idx = max(debate_idx - 1, 0)
            focus = 0
        elif ch == curses.KEY_NPAGE:                   # PgDn -> toward the newest (bottom)
            detail_off = max(0, detail_off - detail_page)
        elif ch == curses.KEY_PPAGE:                   # PgUp -> toward older output (top)
            detail_off = min(detail_max, detail_off + detail_page)
        elif ch == ord("g"):                           # jump to the oldest line
            detail_off = detail_max
        elif ch == ord("G"):                           # jump back to the newest (resume tail)
            detail_off = 0
        elif ch == ord("f"):
            follow = not follow
        elif ch == ord("a"):
            show_all = not show_all
            follow = False
            focus = 0


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
