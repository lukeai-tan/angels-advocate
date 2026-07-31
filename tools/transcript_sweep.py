#!/usr/bin/env python3
"""transcript_sweep.py — a DENOMINATOR for the gate's under-firing question.

WHY THIS EXISTS. `gate-sweep.sh` answers "which *commits* look gate-worthy but were never
journaled?". That is a real signal, but its population is commits, so it is structurally blind to
every decision that never became one: work abandoned mid-way, work squashed into an unrelated
commit, and (most importantly) the long tail of edits made in conversation that were never
committed at all. The journal likewise only records what *did* fire. Neither tool has ever had a
denominator, which is why "is the gate under-firing?" has been unmeasurable rather than merely
unmeasured.

This scans the session TRANSCRIPTS instead and counts *decision episodes*: bursts of file-mutating
tool calls on the main thread, split on an idle gap. That is a different, wider population than
commits — and it is a PROXY, not the truth (see LIMITS in the output, printed every run).

WHAT IT DELIBERATELY DOES NOT DO — print a rate. A gate-worthy-episode count divided by an episode
count would look like a measurement of the gate's miss rate. It isn't: the denominator is a
heuristic over a proxy population, so a precise-looking percentage would manufacture exactly the
false confidence this repo exists to forbid. The output is RAW COUNTS scoped to an explicitly named
population. If you want a rate, you have to compute it yourself, having read what the numbers are.

PRIVACY. Transcripts contain file contents, tool output, and whatever the user typed. This tool
reads them and emits **only paths, line counts, and timestamps**. That is enforced structurally,
not by discipline: `mutations_from_entry()` converts each tool call into a `(path, adds, dels)`
tuple at the point of parsing and drops the text, so nothing downstream — including the printer —
ever holds transcript content to leak.

Read-only. See tools/transcript-sweep.sh for the wrapper.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import debate_lib as dl  # noqa: E402
import gate_sweep as gs  # noqa: E402

# Tool calls that mutate a file. Bash is excluded on purpose: a `sed -i` or a heredoc is a
# mutation too, but recovering "which file, how many lines" from an arbitrary shell string is
# guesswork, and a wrong path would corrupt the gate-worthiness scoring. Undercounting with a
# stated boundary beats overcounting with a silent one.
MUTATING_TOOLS = ("Edit", "Write", "NotebookEdit")

# Idle gap that ends an episode. Same reasoning as debate_lib.DEBATE_GAP_SECONDS (cluster by
# activity burst), but an order of magnitude looser: edits within one decision are minutes apart
# while separate decisions in a session are usually tens of minutes apart.
EPISODE_GAP_SECONDS = 20 * 60


# --- pure ---------------------------------------------------------------------

def _nlines(s):
    """Line count of a string, without retaining it."""
    if not s:
        return 0
    return s.count("\n") + 1


def mutations_from_entry(entry):
    """(path, adds, dels) for each file-mutating tool call in one transcript entry.

    Content is measured and discarded here — the returned tuples carry no transcript text, which
    is what makes the rest of this module safe to print.
    """
    if entry.get("type") != "assistant" or entry.get("isSidechain"):
        return []
    msg = entry.get("message") or {}
    out = []
    for block in msg.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name")
        if name not in MUTATING_TOOLS:
            continue
        inp = block.get("input") or {}
        if not isinstance(inp, dict):
            continue
        path = inp.get("file_path") or inp.get("notebook_path")
        if not path:
            continue
        if name == "Edit":
            adds, dels = _nlines(inp.get("new_string")), _nlines(inp.get("old_string"))
        elif name == "Write":
            adds, dels = _nlines(inp.get("content")), 0
        else:
            adds, dels = _nlines(inp.get("new_source")), 0
        out.append((str(path), adds, dels))
    return out


def group_episodes(muts, gap_s=EPISODE_GAP_SECONDS):
    """Cluster (epoch, session, path, adds, dels) tuples into episodes on an idle gap.

    Episodes never span sessions: two sessions can interleave in wall-clock time, and merging
    them would invent an episode that no single conversation ever had.
    """
    by_session = {}
    for m in muts:
        by_session.setdefault(m[1], []).append(m)
    episodes = []
    for session, items in by_session.items():
        items.sort(key=lambda m: m[0])
        cur = None
        for epoch, _sess, path, adds, dels in items:
            if cur is None or epoch - cur["end"] > gap_s:
                cur = {"session": session, "start": epoch, "end": epoch,
                       "paths": [], "adds": 0, "dels": 0, "calls": 0}
                episodes.append(cur)
            cur["end"] = epoch
            cur["calls"] += 1
            cur["adds"] += adds
            cur["dels"] += dels
            if path not in cur["paths"]:
                cur["paths"].append(path)
    episodes.sort(key=lambda e: e["start"])
    return episodes


def rel_paths(paths, root):
    """Repo-relative paths, so transcript absolutes compare against git's relatives. Paths
    outside the repo are kept verbatim (they still count as touched files)."""
    out = []
    for p in paths:
        try:
            r = os.path.relpath(p, root)
        except ValueError:
            r = p
        out.append(p if r.startswith("..") else r)
    return out


def commit_covered(ep, commits, window_s):
    """True if some commit within the window touched a file this episode touched.

    Path intersection matters: a commit that merely happened nearby is not evidence that THIS
    work was committed, and time-only matching would silently absorb the very episodes (never
    committed) that justify this tool existing.
    """
    want = set(ep["rel"])
    for c in commits:
        t = dl._ts_epoch(c["date"])
        if t is None or abs(t - ep["end"]) > window_s:
            continue
        if want & set(c["paths"]):
            return True
    return False


# --- impure -------------------------------------------------------------------

def transcript_files(root, home=None):
    d = dl.project_dir(root, home=home)
    if not os.path.isdir(d):
        return d, []
    files = [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".jsonl")]
    return d, files


def collect_mutations(files):
    """Stream every transcript; return (mutations, n_calls, n_unparsed_lines).

    Deduped by entry uuid: resumed and forked sessions replay earlier entries into a new file,
    and counting those twice would inflate the denominator in exactly the direction that makes
    the gate look better than it is.
    """
    seen, muts, calls, bad = set(), [], 0, 0
    for path in files:
        session = os.path.basename(path)[:-6]
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except Exception:
                    bad += 1
                    continue
                uuid = entry.get("uuid")
                if uuid:
                    if uuid in seen:
                        continue
                    seen.add(uuid)
                epoch = dl._ts_epoch(entry.get("timestamp"))
                for path_, adds, dels in mutations_from_entry(entry):
                    calls += 1
                    if epoch is not None:
                        muts.append((epoch, session, path_, adds, dels))
    return muts, calls, bad


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Count decision EPISODES from session transcripts — the denominator "
                    "gate-sweep.sh has never had. Prints raw counts, never a rate.")
    ap.add_argument("--gap", type=float, default=EPISODE_GAP_SECONDS / 60.0,
                    help="idle minutes that end an episode (default 20)")
    ap.add_argument("--window", type=float, default=3.0,
                    help="journal/commit coverage window in hours (default 3)")
    ap.add_argument("--list", action="store_true",
                    help="list the un-journaled gate-worthy episodes (paths and counts only)")
    ap.add_argument("--commits", type=int, default=400, help="how many commits to match against")
    ap.add_argument("--root", help="repo root override (testing)")
    ap.add_argument("--home", help="home dir override (testing)")
    args = ap.parse_args(argv)

    root = args.root or gs.repo_root(os.getcwd())
    tdir, files = transcript_files(root, home=args.home)
    if not files:
        print(f"transcript-sweep: no transcripts under {tdir}", file=sys.stderr)
        return 2

    muts, calls, bad = collect_mutations(files)
    episodes = group_episodes(muts, gap_s=args.gap * 60)
    for ep in episodes:
        ep["rel"] = rel_paths(ep["paths"], root)

    eps_journal, jpath = gs.journal_epochs(root)
    try:
        commits = gs.git_commits(root, args.commits)
    except Exception:
        commits = []
    window_s = args.window * 3600

    # An episode that ended before the journal's first entry CANNOT be journaled, so scoring it
    # as a miss invents under-firing out of the journal's own start date. Found the hard way on
    # the first run of this tool: all six reported "misses" were pre-journal work from 2026-07-16.
    journal_start = min(eps_journal) if eps_journal else None

    gate_worthy = []
    for ep in episodes:
        reasons, strong = gs.classify_commit(len(ep["rel"]), ep["adds"], ep["dels"], ep["rel"])
        if reasons:
            ep["reasons"], ep["strong"] = reasons, strong
            gate_worthy.append(ep)

    def score_at(w_s):
        """Partition the gate-worthy episodes at one coverage window."""
        pre, journ, miss, inv = [], 0, [], []
        for ep in gate_worthy:
            if journal_start is not None and ep["end"] < journal_start - w_s:
                pre.append(ep)
            elif gs.is_covered(ep["end"], eps_journal, w_s):
                journ += 1
            else:
                miss.append(ep)
                if not commit_covered(ep, commits, w_s):
                    inv.append(ep)
        return pre, journ, miss, inv

    prejournal, journaled, unjournaled, invisible = score_at(window_s)

    print("Angel's Advocate — transcript sweep (decision-episode denominator)")
    print()
    print("POPULATION — named, not universal:")
    print(f"  file-mutating tool calls ({'/'.join(MUTATING_TOOLS)}) on the MAIN thread of")
    print(f"  {len(files)} transcript(s) under {tdir},")
    print(f"  clustered into episodes by a {args.gap:g}-minute idle gap. A proxy for 'decisions'.")
    print()
    print("RAW COUNTS")
    print(f"  transcripts scanned                {len(files)}")
    print(f"  main-thread mutating tool calls    {calls}")
    print(f"  episodes                           {len(episodes)}")
    print(f"  gate-worthy episodes               {len(gate_worthy)}   "
          f"(gate_sweep.classify_commit heuristics)")
    if prejournal:
        first = datetime.datetime.fromtimestamp(
            journal_start, datetime.timezone.utc).strftime("%Y-%m-%d")
        print(f"    predate the journal ({first})    {len(prejournal)}   "
              f"not scoreable — excluded below")
    print(f"    journaled within ±{args.window:g}h            {journaled}")
    print(f"    NOT journaled                    {len(unjournaled)}")
    print(f"      of those, never committed      {len(invisible)}   "
          f"← invisible to gate-sweep.sh (the delta this tool exists for)")
    print(f"  journal entries available          {len(eps_journal)}  ({jpath})")

    # The miss count is a strong function of the coverage window, and printing one window's
    # figure alone invites quoting it as if it were the gate's miss rate. Show the curve so the
    # reader can see how much of the answer the window is doing.
    print()
    print("SENSITIVITY — the miss count is mostly a property of the coverage window:")
    for w in (3.0, 1.0, 0.5, 0.25):
        _pre, _j, _miss, _inv = score_at(w * 3600)
        mark = "  ←  reported above" if abs(w - args.window) < 1e-9 else ""
        print(f"  ±{w:>5g}h   journaled {_j:>3}   NOT journaled {len(_miss):>3}"
              f"   never committed {len(_inv):>3}{mark}")
    print("  A wider window covers an episode with any nearby entry, related or not; a narrower")
    print("  one demands the entry sit inside the work. Neither is the truth. If one window's")
    print("  number is the only one you can defend, you are quoting the window, not the gate.")

    # And the same disclosure for the other free constant. The episode COUNT is mostly a property
    # of --gap (it moves several-fold across plausible values), so the denominator's magnitude is
    # not a fact about the work. Whether the coverage conclusion also moves is the interesting
    # part, and it is the reader's to judge — so print both columns rather than summarising.
    print()
    print("SENSITIVITY — the episode count is mostly a property of the idle gap:")
    for g in (5, 10, 20, 45, 90):
        eps_g = group_episodes(muts, gap_s=g * 60)
        for e in eps_g:
            e["rel"] = rel_paths(e["paths"], root)
        gw = [e for e in eps_g
              if gs.classify_commit(len(e["rel"]), e["adds"], e["dels"], e["rel"])[0]]
        pre_g = sum(1 for e in gw
                    if journal_start is not None and e["end"] < journal_start - window_s)
        cov_g = sum(1 for e in gw
                    if not (journal_start is not None and e["end"] < journal_start - window_s)
                    and gs.is_covered(e["end"], eps_journal, window_s))
        mark = "  ←  reported above" if abs(g - args.gap) < 1e-9 else ""
        print(f"  {g:>3}min gap   episodes {len(eps_g):>4}   gate-worthy {len(gw):>3}"
              f"   pre-journal {pre_g:>3}   journaled {cov_g:>3}"
              f"   NOT journaled {len(gw) - pre_g - cov_g:>3}{mark}")
    print("  If the episode total swings several-fold here, treat it as a unit of clustering, not")
    print("  a count of decisions. A conclusion that survives every row is the one worth keeping.")

    if bad:
        print(f"  unparseable transcript lines       {bad}")
    if not eps_journal:
        print("  ⚠️  journal is empty here (gitignored/per-machine) — every episode reads as a miss.")

    if args.list and unjournaled:
        print()
        print("UN-JOURNALED GATE-WORTHY EPISODES (paths and counts only — no transcript content):")
        for ep in sorted(unjournaled,
                         key=lambda e: gs.score(e["reasons"], e["strong"], e["adds"], e["dels"],
                                                len(e["rel"])), reverse=True):
            tag = "no commit" if ep in invisible else "committed"
            when = datetime.datetime.fromtimestamp(
                ep["end"], datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
            shown = ", ".join(ep["rel"][:3]) + (f" (+{len(ep['rel']) - 3} more)"
                                                if len(ep["rel"]) > 3 else "")
            print(f"  {when}  [{tag}]  {ep['calls']} call(s), ~{ep['adds'] + ep['dels']} lines")
            print(f"      {' · '.join(ep['reasons'])}")
            print(f"      {shown}")

    print()
    print("LIMITS — why no rate is printed:")
    print("  · An episode is a burst of EDITS, not a decision. Advisory verdicts, forks settled in")
    print("    conversation, and decisions to NOT act make no edits and are invisible here too.")
    print("  · One episode can contain several decisions, and one decision can span episodes.")
    print("  · Gate-worthiness is the same commit-shaped heuristic gate-sweep uses; it was tuned")
    print("    for diffs, and an episode's line counts are an approximation of a diff.")
    print("  · 'journaled' is a ±window time match, not a link — a nearby unrelated entry covers.")
    print("  Dividing any two of these numbers produces a precise-looking figure that none of them")
    print("  supports. Read the counts; don't quote a percentage.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
