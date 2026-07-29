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
            # The advocates emit a fixed severity vocabulary (DEALBREAKER, WORTH-NOTING,
            # CONCEDE, [PASS]/[FAIL], CASE FOR/AGAINST...). When a line opens with one, paint
            # the WHOLE line that severity so the transcript is skimmable by seriousness
            # rather than being a uniform wall of prose.
            sev = dl.debate_line_style(content)
            if sev:
                marker = {"db": "▌", "warn": "▌", "ok": "▌", "sect": ""}[sev]
                segs = ([(marker, sev)] if marker else []) + [(t, sev) for t, _ in
                                                              dl.parse_inline(content)]
                rows.extend(dl.wrap_segments(segs, width, base))
            elif kind == "header":
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
         "hdr": curses.A_BOLD | curses.A_UNDERLINE,
         # debate-structure severity + independence badge; mono fallbacks keep them
         # distinguishable by weight alone when there is no colour at all.
         "db": curses.A_BOLD | curses.A_REVERSE, "warn": curses.A_BOLD,
         "ok": curses.A_DIM, "sect": curses.A_BOLD | curses.A_UNDERLINE,
         "ind_ok": curses.A_DIM, "ind_bad": curses.A_BOLD | curses.A_REVERSE,
         "ind_warn": curses.A_BOLD}
    try:
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_MAGENTA, -1)
            curses.init_pair(5, curses.COLOR_RED, -1)   # severity: dealbreaker / collapse
            C = curses.color_pair
            a["label"] = C(1) | curses.A_BOLD
            a["think"] = C(4) | curses.A_DIM
            a["mdh"] = C(1) | curses.A_BOLD
            a["c"] = C(3)
            a["tool"] = C(2) | curses.A_BOLD
            a["bullet"] = C(1) | curses.A_BOLD
            a["active"] = C(2) | curses.A_BOLD
            a["hdr"] = C(1) | curses.A_BOLD
            # severity palette: red = dealbreaker/collapse, yellow = caveat, green = conceded/ok
            a["db"] = curses.color_pair(5) | curses.A_BOLD
            a["warn"] = C(3) | curses.A_BOLD
            a["ok"] = C(2)
            a["sect"] = C(1) | curses.A_BOLD | curses.A_UNDERLINE
            a["ind_ok"] = C(2) | curses.A_BOLD
            a["ind_bad"] = curses.color_pair(5) | curses.A_BOLD
            a["ind_warn"] = C(3) | curses.A_BOLD
    except Exception:
        pass  # any color init failure -> keep the mono fallbacks

    # Heat ramp for the TOKENS column. Deliberately its OWN try/except, placed AFTER the
    # block above: init_pair() with a 256-colour index raises ValueError on an 8-colour
    # terminal ("Color number is greater than COLORS-1"), and sharing the try above would
    # abort it partway, silently reverting label/tool/active/hdr to mono on terminals where
    # they work today. Gated on COLORS >= 256 with an 8/16-colour branch below, so the ramp
    # degrades instead of taking the rest of the palette down with it.
    # Mono is not a fallback that needs building: bar length already carries the ranking.
    for i in range(dl.HEAT_BUCKETS):
        a[f"heat{i}"] = 0
    a[f"heat{dl.HEAT_BUCKETS - 1}"] = curses.A_BOLD      # mono: hottest stands out
    try:
        if curses.has_colors():
            base = 10                                    # pair ids above the 4 already used
            if getattr(curses, "COLORS", 0) >= 256:
                for i, col in enumerate(dl.HEAT_RAMP_256):
                    curses.init_pair(base + i, col, -1)
                    a[f"heat{i}"] = curses.color_pair(base + i) | (curses.A_BOLD if i >= 3 else 0)
            else:                                        # 8/16-colour: no green (means "active")
                fallback = [(curses.COLOR_BLUE, curses.A_DIM), (curses.COLOR_BLUE, 0),
                            (curses.COLOR_CYAN, 0), (curses.COLOR_YELLOW, curses.A_BOLD),
                            (curses.COLOR_RED, curses.A_BOLD)]
                for i, (col, extra) in enumerate(fallback[: dl.HEAT_BUCKETS]):
                    curses.init_pair(base + i, col, -1)
                    a[f"heat{i}"] = curses.color_pair(base + i) | extra
    except Exception:
        pass  # ramp unavailable -> heat keys stay at the mono defaults set above
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


_ROLE_W = dl.ROLE_W   # role column: fits the longest name ("interpreter"/"test-writer") + " 😇"
_TOK_W = dl.TOK_CELL_W  # tokens column: heat bar + count, e.g. "███▍   1.9M"
_STAT_W = dl.STAT_W   # status column
_FACE_W = dl.FACE_W   # reactive-face column (every face frame is exactly 5 cols)
_SEP = dl.COL_SEP     # column separator (│ is width-1 geometric, alignment-safe)


def _roster_cell(key, a, ctx):
    """One roster cell -> (text, style-key), before padding. Keeping this a lookup keeps
    _draw_roster's loop the same shape no matter which columns survived the width fit."""
    if key == "role":
        em = dl.role_emoji(a["role"])                 # sits right after the name
        return (f"{a['role'] or '?'}" + (f" {em}" if em else ""),
                "active" if ctx["focused"] else "plain")
    if key == "model":
        return (dl.strip_model_prefix(a["model"]) or "?", "dim")
    if key == "ind":
        return dl.independence_mark(a["role"], a["model"], ctx["arbiter"])
    if key == "tokens":
        return (dl.tok_cell(ctx["used"], ctx["peak"]), f"heat{dl.heat_bucket(ctx['used'], ctx['peak'])}")
    if key == "took":
        return (dl.fmt_duration(dl.agent_duration(a)), ctx["st_key"])
    if key == "cost":
        return (dl.fmt_cost(dl.estimate_cost(a.get("usage"), a["model"], ctx["prices"])), "dim")
    if key == "status":
        return (f"{'*' if ctx['status'] == 'active' else ' '} {ctx['act']}", ctx["st_key"])
    if key == "face":
        return (dl.face(a["role"], dl.face_state(ctx["status"], ctx["last_kind"]), ctx["frame"]),
                ctx["st_key"])
    return ("", "plain")


def _draw_roster(win, ordered, focus, roster_top, top_y, total_h, attrs, maxx, now,
                 arbiter=None, prices=None):
    """Draw the scrollable roster as a table: a header + rule, then agent rows. Shows ▶ on the
    focused agent and ▲/▼ when scrolled past the top/bottom edge. Which columns appear depends on
    the terminal width (dl.fit_roster_columns sheds the cosmetic ones first). Fields use
    display-aware padding, so the angel/devil emoji keeps the columns aligned. `total_h` includes
    the 2 header rows; the rest are agent rows."""
    cols = dl.fit_roster_columns(maxx)
    frame = int(now * dl.FACE_FPS)                 # animation frame (advances with wall-clock)
    # Heat denominator: the biggest consumer among the agents SHOWN (i.e. this debate), so
    # "hottest cell == biggest consumer here" holds at any debate size. Session-wide or
    # absolute scales flatten small debates into a uniform wash; see debate_lib's heat notes.
    peak = max((dl.usage_total(x.get("usage") or {}) for x in ordered), default=0)

    header = "  " + _SEP.join(dl.pad_display(h, w) for _k, h, w in cols)
    _safe_addstr(win, top_y, 0, header[: maxx - 1], attrs["hdr"])
    rule_w = min(maxx - 1, dl.display_width(header))
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
        last_kind = a["events"][-1]["kind"] if a["events"] else None
        ctx = {
            "focused": focused, "status": status, "last_kind": last_kind, "frame": frame,
            "st_key": "active" if status == "active" else "done",
            "act": dl.activity_label(last_kind) if (status == "active" and a["events"]) else "done",
            "used": dl.usage_total(a.get("usage") or {}), "peak": peak,
            "arbiter": arbiter, "prices": prices,
        }
        row = [(f"{marker} ", "active" if focused else "plain")]
        for i, (key, _hdr, w) in enumerate(cols):
            if i:
                row.append((_SEP, "dim"))
            text, style = _roster_cell(key, a, ctx)
            row.append((dl.pad_display(text, w), style))
        _render_row(win, body_y + row_i, 0, row, attrs, maxx)


def _draw_timeline(win, ordered, top_y, height, attrs, maxx, now):
    """Gantt panel: when each agent ran, relative to the shown debate's window. Makes the
    debate's shape legible — a parallel opening round lines up, a cross-examination steps
    right. Replaces the detail pane on 't'."""
    if not ordered:
        return
    starts = [dl.agent_start(a) for a in ordered]
    ends = [dl.agent_end(a) for a in ordered]
    t0, t1 = min(starts), max(ends)
    # Give the bar a real budget before the label takes it: on a narrow terminal a fixed
    # _ROLE_W label eats the whole row and the Gantt silently renders off-screen — the point
    # of the panel disappears while looking fine. Shrink the name instead.
    name_w = min(_ROLE_W, max(6, maxx - 1 - dl.DUR_W - 2 - 8))
    label_w = 1 + name_w + dl.DUR_W + 1
    bar_w = max(4, maxx - 1 - label_w)
    span = dl.fmt_duration(t1 - t0 if t1 > t0 else 0)
    _safe_addstr(win, top_y, 0,
                 f" TIMELINE · {len(ordered)} agent(s) over {span} "[: maxx - 1], attrs["hdr"])
    for i, a in enumerate(ordered):
        y = top_y + 1 + i
        if y >= top_y + height:
            break
        running = dl.agent_status(a["mtime"], now=now) == "active"
        em = dl.role_emoji(a["role"])
        name = f"{a['role'] or '?'}" + (f" {em}" if em else "")
        bar = dl.timeline_bar(starts[i], ends[i], t0, t1, bar_w, running)
        _render_row(win, y, 0, [
            (" " + dl.clip_to_width(dl.pad_display(name, name_w), name_w), "plain"),
            (dl.pad_display(dl.fmt_duration(dl.agent_duration(a)), dl.DUR_W), "dim"),
            (" ", "plain"),
            (bar, "active" if running else "done"),
        ], attrs, maxx)


def _repo_root():
    """This checkout's root (tools/ lives directly under it) — where a price override sits."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    show_timeline = False              # 't' -> Gantt of the debate instead of the detail pane
    prices = dl.load_prices(_repo_root())   # cost table, overridable per repo

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
        # "billed" is not padding: ~98% of this number is cache_read/cache_create, so it
        # tracks context re-reads (turn count), not how much work an agent actually did.
        toks = f" · {dl.fmt_tokens(dl.usage_total(sess_u))} tok billed (incl. cache)" if agents else ""
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
            arbiter, _src = dl.infer_arbiter_model(list(agents.values()))
            _draw_roster(stdscr, shown, focus, roster_top, 1, roster_h, attrs, maxx, now,
                         arbiter=arbiter, prices=prices)

            sep_y = 1 + roster_h
            _safe_addstr(stdscr, sep_y, 0, "─" * (maxx - 1), attrs["dim"])
            a = shown[focus]
            if a["path"] != detail_path:       # focus changed -> jump back to the newest
                detail_path = a["path"]
                detail_off = 0
            body_top = sep_y + 2
            body_h = maxy - body_top - 1

            if show_timeline:                  # 't' swaps the detail pane for the Gantt
                _draw_timeline(stdscr, shown, sep_y + 1, maxy - sep_y - 2, attrs, maxx, now)
                body_h = 0                     # nothing else claims the lower pane
            else:
                em = dl.role_emoji(a["role"])
                name = f"{a['role'] or '?'}" + (f" {em}" if em else "")   # emoji after the name
                hdr = f" {name} — {a['model'] or '?'} "
                _safe_addstr(stdscr, sep_y + 1, 0, hdr.ljust(maxx - 1)[: maxx - 1], attrs["hdr"])
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
                  f"a {'one debate' if show_all else 'all agents'} · f follow:{'on' if follow else 'off'} · "
                  f"t {'detail' if show_timeline else 'timeline'} ")
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
        elif ch == ord("t"):
            show_timeline = not show_timeline


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
    ap.add_argument("--gui", action="store_true",
                    help="open a local browser view instead of the terminal UI (loopback-only "
                         "http server; same data, shares debate_lib.snapshot)")
    ap.add_argument("--port", type=int, default=None,
                    help="port for --gui (default: a stable 8770 so the tab keeps working "
                         "across restarts; 0 = ephemeral)")
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

    if args.gui:
        import debate_gui
        # Hand the GUI the project dir so its session switcher has an allow-list to
        # enumerate; --subagents bypasses discovery, so there's no project to offer then.
        pdir = None if args.subagents else (args.project
                                            or dl.project_dir(os.getcwd(), home=args.home))
        return debate_gui.main(subagents_dir, label, args.arbiter_model,
                               port=(debate_gui.DEFAULT_PORT if args.port is None
                                     else args.port),
                               project_dir=pdir)

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
