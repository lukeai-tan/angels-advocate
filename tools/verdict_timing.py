#!/usr/bin/env python3
"""verdict_timing.py — was the 😈 block written BEFORE the work, or after it?

WHY THIS EXISTS. The Arbiter spec's anti-retrospective rule rests on a 2026-07-27 audit that read
transcripts by hand and found the Devil's block typically composed *after* the fix had landed. The
file also carried a statistic said to corroborate it (light mode accepting 12% of dealbreakers vs
31% for structural, "a 2.6x gap"). That statistic was demoted on 2026-07-31: it reproduces, but
Fisher's exact gives p = 0.175, the whole gap rests on two events, and it decays toward 1.0x as n
grows. It was also the wrong instrument — acceptance rate is a *proxy* for ordering, and one with a
confound no sample size removes (structural fires on harder decisions, which genuinely carry more
unfixable risks).

The underlying claim, though, is not statistical at all. It is about ORDER: did the criticism
precede the work or follow it? That is a timestamp, sitting in the transcripts. This probe reads it
directly — no proxy, no confound from decision difficulty, and no reliance on the self-assigned
dispositions the rejected statistic was built from.

WHAT IT COMPARES. Every verdict block declares its own rigor, and several declare their own timing
outright ("Devil written before editing", "(retrospective)", "(written pre-build)"). So the probe
reports DECLARED timing against MEASURED timing. Two failures are distinguishable:

  · an *undeclared* light self-check that measures retrospective — the costume the Arbiter spec forbids;
  · a self-declared "before editing" that measures retrospective — a claim the clock contradicts.

And `(retrospective)` measuring retrospective is COMPLIANCE, not a failure: the rule explicitly
offers that label. Scoring it as a miss would invert the metric.

HONEST LIMITS, printed on every run:
  · Detection is glyph-based. Of 138 messages carrying any marker glyph in this repo's transcripts,
    only ~30 pass the strict filter (all of 🔎/😈/⚖️ *and* a parseable Rigor line) — the rest are
    mostly discussions *about* the format. The rejected count is printed so the filter's
    aggressiveness stays visible rather than being hidden in a clean-looking population.
  · "Edits before the verdict message" is not the same as "the fix preceded the criticism": a turn
    can hold investigation edits, then the verdict, then the fix. Those land in MIXED, which is
    reported as its own bucket and never collapsed into either verdict.
  · TWO AXES, AND ONLY ONE OF THEM RESOLVES. The edits axis cannot test the 07-27 audit's claim,
    because that claim is about the reading that surfaced the problems and reading leaves no edits.
    The investigation axis (added 2026-07-31) closes that gap and clears the no-resolution floor,
    but its direction flips with the clustering gap and its unresolved bucket swallows most of the
    population — so it returns a NEGATIVE RESULT: the two-thirds figure is not adjudicable here.
    That is printed on every run rather than resolved by choosing a flattering gap.
  · Decisions predating the rule (2026-07-27) cannot be judged against it and are bucketed by era.

PRIVACY. Rigor labels are normalised to a fixed vocabulary at parse time and message text is never
retained — the probe emits only labels from that vocabulary, counts, and timestamps.

Read-only. See tools/verdict-timing.sh for the wrapper.
"""
import argparse
import collections
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import debate_lib as dl  # noqa: E402
import gate_sweep as gs  # noqa: E402
import transcript_sweep as tsw  # noqa: E402

# All three must be present. Any weaker filter admits the many messages that merely *quote* the
# format — including, unavoidably, the conversations in which this file was designed.
MARKERS = ("\U0001F50E", "\U0001F608", "⚖")  # 🔎 rigor · 😈 devil · ⚖️ verdict
RIGOR_RE = re.compile(r"\U0001F50E\s*\*\*Rigor:\*\*\s*([^\n·]+)")

# The commit that added the anti-retrospective rule. Verdicts before it were written under no such
# obligation, so judging them against it would manufacture violations out of a date.
RULE_LANDED = "2026-07-27T16:07:34+08:00"

PRE_HINTS = ("pre-build", "before editing", "before any edit", "written before", "pre-declared")
DURING_HINTS = ("while editing", "during editing", "mid-edit")

# The investigation axis (added 2026-07-31). The edits axis above answers "was the criticism written
# before the FIX?" — but the 07-27 audit's actual claim was that the block is composed after the
# INVESTIGATION that surfaced the problems, and investigation is read-only. A verdict written after
# a long hunt but before the fix lands reads "pre-written" on the edits axis while being exactly the
# narration the audit described, so that axis cannot test the claim it was built to test.
#
# Read-only and unambiguous. Bash is deliberately NOT here: `git commit`, test runs, and post-fix
# verification are not investigation, and folding them in would count checking as looking. It is
# measured separately and reported only as a sensitivity row, never in the headline.
INVESTIGATION_TOOLS = ("Read", "Grep", "Glob")
AMBIGUOUS_TOOLS = ("Bash",)

# Above this share, "was there investigation before the verdict?" is answered yes for essentially
# everything and has stopped being a measurement. The probe says so instead of printing a ratio.
RESOLVING_FLOOR = 0.90


def normalise_rigor(raw):
    """Free-form Rigor text -> one label from a fixed vocabulary.

    Normalising here is what keeps the probe safe to print: the raw line is authored prose and can
    say anything, so it is classified and discarded rather than carried around.
    """
    low = raw.lower()
    if "structural" in low:
        return "structural debate"
    if "light" in low or "self-check" in low:
        if "retrospective" in low:
            return "light (declared: retrospective)"
        if any(h in low for h in PRE_HINTS):
            return "light (declared: pre-written)"
        if any(h in low for h in DURING_HINTS):
            return "light (declared: during)"
        return "light (undeclared)"
    if "skip" in low:
        return "skip"
    return "other"


def verdict_from_entry(entry):
    """(rigor_label, had_markers) for one entry.

    rigor_label is None unless the entry is a strict verdict block. had_markers reports whether it
    carried any marker glyph at all, so callers can count what the filter threw away.
    """
    if entry.get("type") != "assistant" or entry.get("isSidechain"):
        return None, False
    msg = entry.get("message") or {}
    text = "".join(b.get("text", "") for b in msg.get("content") or []
                   if isinstance(b, dict) and b.get("type") == "text")
    if not text:
        return None, False
    hits = [g for g in MARKERS if g in text]
    if not hits:
        return None, False
    if len(hits) < len(MARKERS):
        return None, True
    m = RIGOR_RE.search(text)
    if not m:
        return None, True
    return normalise_rigor(m.group(1).strip().strip("*").strip()), True


def classify_timing(before, after):
    """Ordering verdict for one decision. MIXED is a real answer, not a rounding error."""
    if before and after:
        return "mixed"
    if after:
        return "pre-written"
    if before:
        return "retrospective"
    return "no edits"


def probe_calls_from_entry(entry, tools):
    """Timestamps of read-only tool calls in one entry, for the named tools.

    Only the tool NAME is examined and only a count survives — inputs (file paths, patterns,
    commands) are never read, so this axis carries even less than the mutation scan does.
    """
    if entry.get("type") != "assistant" or entry.get("isSidechain"):
        return 0
    msg = entry.get("message") or {}
    return sum(1 for b in msg.get("content") or []
               if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in tools)


def collect_probe_calls(files, tools):
    """(epoch, session, '', 0, 0) tuples for read-only calls, shaped to feed group_episodes.

    Deduped by uuid for the same reason collect_mutations is: resumed/forked sessions replay
    earlier entries into a new file.
    """
    seen, out = set(), []
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
                    continue
                uuid = entry.get("uuid")
                if uuid:
                    if uuid in seen:
                        continue
                    seen.add(uuid)
                n = probe_calls_from_entry(entry, tools)
                if not n:
                    continue
                epoch = dl._ts_epoch(entry.get("timestamp"))
                if epoch is not None:
                    out.extend([(epoch, session, "", 0, 0)] * n)
    return out


def classify_investigation(inv_before, mut_before, mut_after):
    """The audit's actual shape, on the axis that can see it.

    'looked, judged, built'  — investigation preceded the verdict and every edit followed it. The
                               ordering the rule asks for.
    'looked, built, judged'  — investigation AND every edit preceded the verdict: the narration the
                               07-27 audit described, and the only one of these that is a finding.
    'mixed (edits both sides)' — edits on both sides. UNRESOLVED, and kept out of the finding for
                               the same reason MIXED is on the timing axis: folding it in lets the
                               clustering gap inflate the headline several-fold. A verdict with one
                               edit before it and twenty after is not the audit's shape.
    'judged cold'            — no investigation before the verdict at all.
    """
    if not inv_before:
        return "judged cold"
    if mut_before and mut_after:
        return "mixed (edits both sides)"
    if mut_before:
        return "looked, built, judged"
    if mut_after:
        return "looked, judged, built"
    return "looked, judged, no edits"


def locate_episode(v_epoch, session, episodes, gap_s):
    """The episode this verdict belongs to: the one containing it, else the nearest one in the
    same session within one gap. Verdicts with no episode are advisory (nothing was built)."""
    best = None
    for ep in episodes:
        if ep["session"] != session:
            continue
        if ep["start"] <= v_epoch <= ep["end"]:
            return ep
        gap = min(abs(ep["start"] - v_epoch), abs(ep["end"] - v_epoch))
        if best is None or gap < best[0]:
            best = (gap, ep)
    if best and best[0] <= gap_s:
        return best[1]
    return None


def collect_verdicts(files):
    """Stream the transcripts for verdict blocks. Deduped by uuid, same as the mutation scan."""
    seen, out, rejected = set(), [], 0
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
                    continue
                uuid = entry.get("uuid")
                if uuid:
                    if uuid in seen:
                        continue
                    seen.add(uuid)
                label, had = verdict_from_entry(entry)
                if label is None:
                    rejected += had
                    continue
                epoch = dl._ts_epoch(entry.get("timestamp"))
                if epoch is not None:
                    out.append({"epoch": epoch, "session": session, "rigor": label})
    out.sort(key=lambda v: v["epoch"])
    return out, rejected


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Check whether each verdict's Devil block was written before or after the "
                    "edits in its own episode. Measures the ordering claim directly.")
    ap.add_argument("--gap", type=float, default=tsw.EPISODE_GAP_SECONDS / 60.0,
                    help="idle minutes that end an episode (default 20)")
    ap.add_argument("--era", default=RULE_LANDED,
                    help="ISO timestamp the anti-retrospective rule landed")
    ap.add_argument("--root", help="repo root override (testing)")
    ap.add_argument("--home", help="home dir override (testing)")
    args = ap.parse_args(argv)

    root = args.root or gs.repo_root(os.getcwd())
    tdir, files = tsw.transcript_files(root, home=args.home)
    if not files:
        print(f"verdict-timing: no transcripts under {tdir}", file=sys.stderr)
        return 2

    muts, _calls, _bad = tsw.collect_mutations(files)
    episodes = tsw.group_episodes(muts, gap_s=args.gap * 60)
    probes = collect_probe_calls(files, INVESTIGATION_TOOLS)
    probes_bash = collect_probe_calls(files, INVESTIGATION_TOOLS + AMBIGUOUS_TOOLS)
    verdicts, rejected = collect_verdicts(files)
    era = dl._ts_epoch(args.era)

    def score(gap_s):
        """Assign every verdict a timing, with episodes clustered at this gap. Parameterised
        because the gap turns out to drive the MIXED share almost entirely (see SENSITIVITY)."""
        eps = tsw.group_episodes(muts, gap_s=gap_s)
        rows = []
        for v in verdicts:
            ep = locate_episode(v["epoch"], v["session"], eps, gap_s)
            if ep is None:
                before = after = 0
            else:
                before = sum(1 for m in muts if m[1] == v["session"]
                             and ep["start"] <= m[0] <= ep["end"] and m[0] < v["epoch"])
                after = sum(1 for m in muts if m[1] == v["session"]
                            and ep["start"] <= m[0] <= ep["end"] and m[0] > v["epoch"])
            rows.append(dict(v, before=before, after=after,
                             timing=classify_timing(before, after),
                             era=("after the rule" if era is not None and v["epoch"] >= era
                                  else "before the rule")))
        return rows

    def score_investigation(gap_s, probes):
        """The investigation axis, on WORK episodes — clustered over read-only calls AND edits.

        Clustering here differs from score() on purpose. Mutation episodes are bursts of edits, so
        a long read-only hunt followed later by the fix falls outside every one of them and the
        investigation would be invisible in exactly the case this axis exists to see. The two
        sections therefore use different windows, which is disclosed in the output rather than
        quietly reconciled: they answer different questions.
        """
        eps = tsw.group_episodes(muts + probes, gap_s=gap_s)
        rows = []
        for v in verdicts:
            ep = locate_episode(v["epoch"], v["session"], eps, gap_s)
            if ep is None:
                inv_b = mut_b = mut_a = 0
            else:
                def span(items, lo, hi):
                    return sum(1 for it in items if it[1] == v["session"]
                               and ep["start"] <= it[0] <= ep["end"] and lo(it[0], hi))
                before = lambda t, _: t < v["epoch"]  # noqa: E731
                after = lambda t, _: t > v["epoch"]   # noqa: E731
                inv_b = span(probes, before, None)
                mut_b = span(muts, before, None)
                mut_a = span(muts, after, None)
            rows.append(dict(v, inv_before=inv_b,
                             shape=classify_investigation(inv_b, mut_b, mut_a),
                             era=("after the rule" if era is not None and v["epoch"] >= era
                                  else "before the rule")))
        return rows

    scored = score(args.gap * 60)

    print("Angel's Advocate — verdict timing probe")
    print()
    print("QUESTION — was the 😈 block written before the work, or after it? Read off the clock,")
    print("not inferred from dispositions. Ordering is the actual claim the rule rests on.")
    print()
    print("DETECTION")
    print(f"  transcripts scanned                {len(files)}")
    print(f"  verdict blocks found               {len(verdicts)}   "
          f"(all of 🔎/😈/⚖️ present + a parseable Rigor line)")
    print(f"  messages rejected by the filter    {rejected}   "
          f"(carried a marker glyph but failed the filter)")
    if not verdicts:
        print("\n  Nothing to measure.")
        return 0

    cols = ["pre-written", "mixed", "retrospective", "no edits"]
    for era_name in ("after the rule", "before the rule"):
        rows = [v for v in scored if v["era"] == era_name]
        if not rows:
            continue
        when = datetime.datetime.fromtimestamp(
            era, datetime.timezone.utc).strftime("%Y-%m-%d") if era else "?"
        print()
        print(f"MEASURED TIMING — {era_name} ({when})   [declared ↓ · measured →]")
        print(f"  {'declared rigor':<32}" + "".join(f"{c:>15}" for c in cols))
        tab = collections.Counter((v["rigor"], v["timing"]) for v in rows)
        for label in sorted({v["rigor"] for v in rows}):
            print(f"  {label:<32}" + "".join(f"{tab[(label, c)]:>15}" for c in cols))

    # The MIXED share is almost entirely a property of the clustering gap, so a single gap's
    # cross-tab reads far more decisively than the data supports. Print the curve.
    print()
    print("SENSITIVITY — MIXED is mostly a property of the episode-clustering gap:")
    print(f"  {'gap':>7}   {'pre-written':>11} {'mixed':>6} {'retrospective':>13} {'no edits':>9}"
          f"   {'MIXED share':>11}   {'pre:retro':>9}")
    for g in (3, 5, 10, 20, 45):
        c = collections.Counter(r["timing"] for r in score(g * 60))
        tot = sum(c.values()) or 1
        ratio = (f"{c['pre-written'] / c['retrospective']:.1f}x"
                 if c["retrospective"] else "—")
        mark = "  ←" if abs(g - args.gap) < 1e-9 else ""
        print(f"  {g:>4}min   {c['pre-written']:>11} {c['mixed']:>6} {c['retrospective']:>13}"
              f" {c['no edits']:>9}   {100 * c['mixed'] / tot:>10.0f}%   {ratio:>9}{mark}")
    print("  A tight gap asks 'the edits right around the verdict'; a loose one asks 'the edits")
    print("  that session'. Neither is the question — so read the pre:retro column, which is the")
    print("  direction among cases the probe can actually resolve, and check it across rows.")

    # The failures worth naming, kept separate because they are different mistakes — and STRICT
    # is kept apart from AMBIGUOUS, since folding MIXED into a failure count would contradict the
    # limit stated below and inflate the finding by a factor the gap alone controls.
    after = [v for v in scored if v["era"] == "after the rule"]
    def count(rigor, timings):
        return sum(1 for v in after if v["rigor"] == rigor and v["timing"] in timings)
    print()
    print(f"FINDINGS (after the rule, at the {args.gap:g}min gap)")
    print(f"  undeclared light self-checks measuring retrospective   "
          f"{count('light (undeclared)', ('retrospective',)):>3}   ← the costume the rule forbids")
    print(f"    the same, measuring MIXED (unresolved, NOT counted above)  "
          f"{count('light (undeclared)', ('mixed',)):>3}")
    print(f"  self-declared 'written before editing', measured retrospective  "
          f"{count('light (declared: pre-written)', ('retrospective',)):>3}   ← clock contradicts")
    print(f"  self-declared '(retrospective)', measured retrospective  "
          f"{count('light (declared: retrospective)', ('retrospective',)):>3}   "
          f"← COMPLIANCE: the rule offers this label")

    # ---- the investigation axis --------------------------------------------------------------
    # The edits axis above cannot test the 07-27 audit's claim: the audit said the block is composed
    # after the INVESTIGATION, and investigation leaves no edits. This section adds that axis — and
    # checks its own resolving power first, because "was anything read before the verdict?" is the
    # kind of question that can come back 'yes' for everything and still look like a measurement.
    inv_scored = score_investigation(args.gap * 60, probes)
    with_inv = sum(1 for r in inv_scored if r["inv_before"])
    share = with_inv / len(inv_scored)
    print()
    print(f"INVESTIGATION AXIS — did the reading that found the problems precede the 😈 block?")
    print(f"  investigation tools counted        {', '.join(INVESTIGATION_TOOLS)}"
          f"   (read-only; Bash excluded — see below)")
    print(f"  verdicts with ANY investigation before them   {with_inv}/{len(inv_scored)}"
          f"  ({100 * share:.0f}%)")
    if share >= RESOLVING_FLOOR:
        print(f"  ⚠️  AXIS DOES NOT RESOLVE — at or above the {100 * RESOLVING_FLOOR:.0f}% floor, this")
        print("      is a constant, not a measurement: essentially every verdict has something read")
        print("      before it, for reasons mostly unrelated to the decision. The shape table below")
        print("      is printed for completeness; do not quote it as a finding.")
    else:
        print("  Axis resolves (below the floor), so the shape table below carries information.")
    for era_name in ("after the rule", "before the rule"):
        rows = [r for r in inv_scored if r["era"] == era_name]
        if not rows:
            continue
        tab = collections.Counter(r["shape"] for r in rows)
        print(f"  {era_name}:")
        for shape in ("looked, judged, built", "looked, built, judged",
                      "mixed (edits both sides)", "looked, judged, no edits", "judged cold"):
            flag = ""
            if shape == "looked, built, judged":
                flag = "   ← the shape the 07-27 audit described"
            elif shape == "mixed (edits both sides)":
                flag = "   ← UNRESOLVED, never folded into the line above"
            print(f"    {shape:<28}{tab[shape]:>4}{flag}")
    inv_b = score_investigation(args.gap * 60, probes_bash)
    wb = sum(1 for r in inv_b if r["inv_before"])
    print(f"  SENSITIVITY — including Bash as investigation: {wb}/{len(inv_b)} "
          f"({100 * wb / len(inv_b):.0f}%) have investigation before. Bash is dual-purpose")
    print("    (commits, test runs, post-fix verification), so it is never in the headline above.")
    # Same discipline as the timing axis: the unresolved bucket is gap-driven, so print its curve
    # rather than let one gap's table read as decisive.
    print(f"  SENSITIVITY — the unresolved bucket is gap-driven here too:")
    print(f"  {'gap':>7}   {'looked/judged/built':>19} {'looked/built/judged':>19}"
          f" {'mixed':>7} {'cold':>6}")
    for g in (3, 5, 10, 20, 45):
        c = collections.Counter(r["shape"] for r in score_investigation(g * 60, probes))
        mark = "  ←" if abs(g - args.gap) < 1e-9 else ""
        print(f"  {g:>4}min   {c['looked, judged, built']:>19} {c['looked, built, judged']:>19}"
              f" {c['mixed (edits both sides)']:>7} {c['judged cold']:>6}{mark}")
    print("  Apply the same test the timing axis passes — does one direction survive every row?")
    print("  Here it does not: the two shape columns TRADE PLACES as the gap widens, while the")
    print("  unresolved bucket grows to swallow most of the population. So this axis, though it")
    print("  resolves better than Bash-inclusive counting, still cannot adjudicate the 07-27")
    print("  audit's two-thirds claim. Report that as a negative result, not as a direction.")

    print()
    print("LIMITS")
    print("  · The investigation axis clusters episodes over read-only calls AND edits, while the")
    print("    timing axis clusters over edits alone. Different windows, different questions —")
    print("    a read-only hunt falls outside every edit-burst, which is the case that axis needs.")
    print("  · 'before the verdict message' is not 'the fix preceded the criticism' — a turn can")
    print("    hold investigation edits, then the verdict, then the fix. Those are MIXED, which is")
    print("    reported as its own bucket and never folded into a failure count.")
    print("  · Detection is glyph-based and strict; the rejected count above is the price paid.")
    print("  · An episode is a burst of edits (see transcript_sweep.py), so a decision whose work")
    print("    lands in a later session reads as 'no edits' here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
