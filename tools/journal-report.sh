#!/usr/bin/env bash
# journal-report.sh — READ the decision journal. Companion to journal.sh (which WRITES it).
#
# The journal (.angel-advoc/journal.jsonl) is append-only and, until now, write-only:
# journal.sh logs each gated verdict but nothing read them back. This closes the loop
# the Arbiter spec promised under "/journal" and "/gate-audit" — turning the log from a
# write-only file into an actual feedback loop.
#
# Two modes:
#   journal-report.sh [--recent] [N]   list the most recent N decisions (default 10),
#                                      human-readable. This is what `/journal` runs.
#   journal-report.sh --audit          aggregate ALL decisions into patterns: rigor and
#                                      gate distribution, dealbreaker dispositions,
#                                      verifier outcomes (esp. verdicts that FAILED
#                                      verification), recurring dealbreakers, and the
#                                      gate's real risk — under-firing. This is `/gate-audit`.
#
# Read-only: it never writes the journal. Same path resolution + python3 bridge as
# journal.sh, so it stays consistent and testable (see tools/tests/journal-report_test.sh).
#
# Journal location: $ANGEL_ADVOC_JOURNAL, else <repo-root>/.angel-advoc/journal.jsonl.
# A missing/empty journal is not an error — it reports "0 decisions" and exits 0.
#
# Usage:
#   bash tools/journal-report.sh                # recent 10
#   bash tools/journal-report.sh --recent 25    # recent 25
#   bash tools/journal-report.sh --audit        # full audit
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Same journal resolution as journal.sh: $ANGEL_ADVOC_JOURNAL, else the working repo (git),
# else this script's own repo — so a global install reads the current project's journal.
if [ -n "${ANGEL_ADVOC_JOURNAL:-}" ]; then
	JOURNAL="$ANGEL_ADVOC_JOURNAL"
else
	ROOT="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null || true)"
	[ -n "$ROOT" ] || ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
	JOURNAL="$ROOT/.angel-advoc/journal.jsonl"
fi
PY="${PYTHON_BIN:-python3}"

command -v "$PY" >/dev/null 2>&1 || { echo "journal-report.sh: python3 not found (set PYTHON_BIN)." >&2; exit 1; }

# --- arg parse: default mode=recent, N=10 ------------------------------------
MODE="recent"
N="10"
while [ "$#" -gt 0 ]; do
	case "$1" in
		--audit)  MODE="audit"; shift ;;
		--recent) MODE="recent"; shift ;;
		--help|-h)
			sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
			exit 0 ;;
		''|*[!0-9]*)
			echo "journal-report.sh: unknown argument '$1' (want --recent [N] | --audit)" >&2
			exit 2 ;;
		*) N="$1"; shift ;;
	esac
done

export JOURNAL_PATH="$JOURNAL" REPORT_MODE="$MODE" REPORT_N="$N"

"$PY" - <<'PY'
import json, os, sys, re

path = os.environ["JOURNAL_PATH"]
mode = os.environ["REPORT_MODE"]
try:
    want_n = int(os.environ.get("REPORT_N", "10"))
except ValueError:
    want_n = 10

# --- load: tolerate a missing file and skip (but count) malformed lines ------
entries, malformed = [], 0
if os.path.exists(path):
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                malformed += 1
                continue
            if isinstance(obj, dict):
                entries.append(obj)
            else:
                malformed += 1

def g(obj, key, default=""):
    v = obj.get(key, default)
    return v if v is not None else default

def one_line(s, width=100):
    s = " ".join(str(s).split())
    return s if len(s) <= width else s[: width - 1] + "…"

# verifier string -> bucket. "FAILS:2" -> failed; "CONFORMS" -> conforms; "n/a" -> n/a.
def verifier_bucket(v):
    v = str(v).strip().lower()
    if not v or v == "n/a":
        return "n/a"
    if v.startswith("conform"):
        return "conforms"
    if v.startswith("fail"):
        return "failed"
    return "other"

# --- token cost (optional `tokens` field on an entry) ------------------------
def fmt_tok(nn):
    nn = int(nn or 0)
    if nn < 1000:
        return str(nn)
    if nn < 1_000_000:
        return f"{nn / 1000:.1f}k"
    return f"{nn / 1_000_000:.1f}M"

def tok_total(tk):
    if not isinstance(tk, dict):
        return 0
    if "total" in tk:
        return int(tk.get("total") or 0)
    return sum(int(tk.get(k, 0) or 0) for k in ("input", "output", "cache_read", "cache_create"))

def tok_out(tk):
    return int(tk.get("output", 0) or 0) if isinstance(tk, dict) else 0

if not entries:
    extra = f"  ({malformed} malformed line(s) skipped)" if malformed else ""
    print(f"Angel's Advocate journal: 0 decisions logged.{extra}")
    print(f"  ({path})")
    sys.exit(0)

# --- recent mode -------------------------------------------------------------
if mode == "recent":
    recent = entries[-want_n:]
    print(f"Angel's Advocate — last {len(recent)} of {len(entries)} decision(s)")
    if malformed:
        print(f"  (note: {malformed} malformed line(s) skipped)")
    print()
    for obj in recent:
        ts = g(obj, "ts", "(no ts)")
        gate = g(obj, "gate", "?")
        rigor = g(obj, "rigor", "?")
        verifier = g(obj, "verifier", "n/a")
        print(f"{ts}  [{gate} / {rigor}]  verifier: {verifier}")
        if g(obj, "target"):
            print(f"    target : {one_line(g(obj, 'target'))}")
        if g(obj, "verdict"):
            print(f"    verdict: {one_line(g(obj, 'verdict'))}")
        dbs = obj.get("dealbreakers") or []
        if isinstance(dbs, list) and dbs:
            print("    dealbreakers:")
            for d in dbs:
                if not isinstance(d, dict):
                    continue
                disp = g(d, "disposition", "?")
                item = one_line(g(d, "item", "(no item)"), 90)
                print(f"      - [{disp}]  {item}")
        tk = obj.get("tokens")
        if isinstance(tk, dict) and tok_total(tk):
            print(f"    tokens : {fmt_tok(tok_total(tk))} total  (out {fmt_tok(tok_out(tk))})")
        print()
    sys.exit(0)

# --- audit mode --------------------------------------------------------------
from collections import Counter

n = len(entries)
first_ts = g(entries[0], "ts", "?")
last_ts = g(entries[-1], "ts", "?")

def norm_gate(v):
    # collapse "skip-noted"/"skip" so under-firing signal is countable regardless of label
    return str(v).strip().lower()

gate_counts = Counter(norm_gate(g(e, "gate", "?")) for e in entries)
rigor_counts = Counter(str(g(e, "rigor", "?")).strip().lower() for e in entries)

disp_counts = Counter()
db_items = []          # (normalized_key, original_item) for recurring detection
for e in entries:
    for d in (e.get("dealbreakers") or []):
        if not isinstance(d, dict):
            continue
        disp = str(g(d, "disposition", "?")).strip().lower()
        # bucket disposition into resolved / accepted / refuted / unresolved / other by prefix.
        # 'refuted' is its own bucket on purpose: a disproved attack filed under "accepted"
        # reads as the Arbiter conceding a risk it actually killed, which inflates the
        # accepted count and understates how often the debate is adversarial in both directions.
        if disp.startswith("resolv"):
            disp_counts["resolved"] += 1
        elif disp.startswith("accept"):
            disp_counts["accepted"] += 1
        elif disp.startswith("refut"):
            disp_counts["refuted"] += 1
        elif disp.startswith("unresolv") or "incomplete" in disp or "re-run" in disp:
            disp_counts["unresolved"] += 1
        else:
            disp_counts["other"] += 1
        item = g(d, "item", "")
        if item:
            key = " ".join(str(item).lower().split())[:50]
            db_items.append((key, item))

verifier_counts = Counter(verifier_bucket(g(e, "verifier", "n/a")) for e in entries)
failed = [e for e in entries if verifier_bucket(g(e, "verifier", "n/a")) == "failed"]
skips = [e for e in entries if norm_gate(g(e, "gate", "")).startswith("skip")]

def bar(counter, indent="   "):
    if not counter:
        print(f"{indent}(none)")
        return
    width = max(len(k) for k in counter)
    for k, c in counter.most_common():
        print(f"{indent}{k.ljust(width)}  {c}")

print(f"Angel's Advocate — gate audit")
print(f"  {n} decision(s)   {first_ts} .. {last_ts}")
if malformed:
    print(f"  note: {malformed} malformed line(s) skipped")
print()

print("Rigor distribution:")
bar(rigor_counts)
print()

print("Gate distribution:")
bar(gate_counts)
print()

print("Dealbreaker dispositions:")
bar(disp_counts)
print()


def _fisher_exact_2x2(a, b, c, d):
    """Two-tailed Fisher's exact p for [[a,b],[c,d]]. No scipy.

    Written out longhand rather than imported because the only reason to print this number is
    to keep "every comparison is null" checkable — a check that silently vanishes on a machine
    without scipy is not a check.
    """
    from math import comb
    n = a + b + c + d
    if n == 0:
        return None
    r1, r2, c1 = a + b, c + d, a + c
    denom = comb(n, c1)
    if denom == 0:
        return None

    def p_of(x):
        return comb(r1, x) * comb(r2, c1 - x) / denom

    p_obs = p_of(a)
    lo, hi = max(0, c1 - r2), min(r1, c1)
    return min(1.0, sum(p_of(x) for x in range(lo, hi + 1) if p_of(x) <= p_obs * (1 + 1e-9)))


# Acceptance rate by RIGOR — the cross-tab this script never had, which is why the Arbiter spec was
# reduced to hand-copying the figure and going stale the same day it was written.
#
# Bucketed on `rigor`, NEVER on `gate`. Bucketing on gate files every "light self-check" that
# fired on a fork under "structural" and materially changes the answer. That error was made and
# caught by hand once; computing it here is what stops it being made again silently.
def _rigor_bucket(v):
    s = str(v).strip().lower()
    if s.startswith("light"):
        return "light self-check"
    if "structural" in s:
        return "structural debate"
    return None


acc = {}   # rigor bucket -> [accepted, total dealbreakers]
for e in entries:
    b = _rigor_bucket(g(e, "rigor", ""))
    if not b:
        continue
    for d in (e.get("dealbreakers") or []):
        if not isinstance(d, dict):
            continue
        slot = acc.setdefault(b, [0, 0])
        slot[1] += 1
        if str(g(d, "disposition", "")).strip().lower().startswith("accept"):
            slot[0] += 1

print("Dealbreaker acceptance by RIGOR (bucketed on `rigor`, never on `gate`):")
if not acc:
    print("   (no entries carry a recognised rigor label)")
else:
    width = max(max(len(k) for k in acc), len("ratio structural/light"))
    for k in sorted(acc):
        a_, t_ = acc[k]
        rate = f"{100.0 * a_ / t_:.1f}%" if t_ else "—"
        print(f"   {k.ljust(width)}  accepted {a_}/{t_} = {rate}")
    L, S = acc.get("light self-check"), acc.get("structural debate")
    if L and S and L[1] and S[1] and L[0]:
        ratio = (S[0] / S[1]) / (L[0] / L[1])
        p = _fisher_exact_2x2(L[0], L[1] - L[0], S[0], S[1] - S[0])
        if p is None:
            tail = "p unavailable"
        elif p >= 0.05:
            tail = f"Fisher's exact (2-tailed) p = {p:.3f}  →  null"
        else:
            tail = (f"Fisher's exact (2-tailed) p = {p:.3f}  →  SIGNIFICANT at .05 — "
                    "docs/calibration-notes.md's demotion of this statistic needs re-reading")
        print(f"   {'ratio structural/light'.ljust(width)}  {ratio:.2f}x   {tail}")
    print("   This is the figure the Arbiter spec deliberately does not pin. It moves with every entry —")
    print("   read it here; do not copy it back into a file that cannot recompute it.")
print()

print("Verifier outcomes:")
bar(verifier_counts)
print()

print("Verdicts that FAILED verification  (the ones to learn from):")
if failed:
    for e in failed:
        print(f"   - {g(e,'ts','?')}  {one_line(g(e,'target','(no target)'), 80)}")
        print(f"       verifier: {g(e,'verifier','?')}")
else:
    print("   (none)")
print()

# recurring dealbreakers: heuristic — normalized (lowercased, whitespace-collapsed,
# first 50 chars) prefix match. Deliberately labeled as heuristic; it groups
# near-identical items, it does NOT understand meaning.
key_counts = Counter(k for k, _ in db_items)
first_orig = {}
for k, orig in db_items:
    first_orig.setdefault(k, orig)
recurring = [(k, c) for k, c in key_counts.most_common() if c >= 2]
print("Recurring dealbreakers  (heuristic: normalized prefix seen >1x):")
if recurring:
    for k, c in recurring:
        print(f"   {c}x  {one_line(first_orig[k], 85)}")
else:
    print("   (none repeated)")
print()

# under-firing signal
print("Under-firing signal:")
print(f"   {len(skips)} decision(s) logged as a noted 'skip'.")
print("   The gate's real risk is UNDER-firing (skipping review that should have fired).")
print("   That is only measurable if near-misses get logged as skip — a low count here")
print("   with many real decisions may mean skips aren't being recorded, not that none happened.")
print()

# token cost (only entries that logged a `tokens` field)
priced = [e for e in entries if tok_total(e.get("tokens"))]
print("Token cost  (decisions that logged tokens):")
if priced:
    tot = sum(tok_total(e.get("tokens")) for e in priced)
    out = sum(tok_out(e.get("tokens")) for e in priced)
    print(f"   {len(priced)} of {n} decision(s) logged tokens: {fmt_tok(tot)} total, {fmt_tok(out)} output")
    costliest = sorted(priced, key=lambda e: tok_total(e.get("tokens")), reverse=True)[:3]
    for e in costliest:
        print(f"   - {fmt_tok(tok_total(e.get('tokens'))):>7}  {one_line(g(e,'target','(no target)'), 70)}")
else:
    print("   (none logged tokens yet — add a `tokens` field via tools/token-report.sh; see the Arbiter spec)")
PY
