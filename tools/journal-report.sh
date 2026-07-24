#!/usr/bin/env bash
# journal-report.sh — READ the decision journal. Companion to journal.sh (which WRITES it).
#
# The journal (.angel-advoc/journal.jsonl) is append-only and, until now, write-only:
# journal.sh logs each gated verdict but nothing read them back. This closes the loop
# CLAUDE.md promised under "/journal" and "/gate-audit" — turning the log from a
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
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
JOURNAL="${ANGEL_ADVOC_JOURNAL:-$REPO_ROOT/.angel-advoc/journal.jsonl}"
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
        # bucket disposition into resolved / accepted / unresolved / other by prefix
        if disp.startswith("resolv"):
            disp_counts["resolved"] += 1
        elif disp.startswith("accept"):
            disp_counts["accepted"] += 1
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
    print("   (none logged tokens yet — add a `tokens` field via tools/token-report.sh; see CLAUDE.md)")
PY
