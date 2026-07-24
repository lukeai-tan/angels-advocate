#!/usr/bin/env python3
"""verifier_calibration.py — is the verifier catching problems, or rubber-stamping?

The verifier has (historically) returned CONFORMS on every real decision, never FAILS. That's
either clean work or a rubber stamp, and you can't tell which without a controlled probe. This
manages a set of checked-in fixtures — known-bad (verdict, diff) pairs the verifier MUST FAIL,
plus a clean control it MUST CONFORM — and scores the verifier's verdicts against the expected
outcome. The verifier is a Claude Code subagent (only the Arbiter can spawn it), so this tool
prepares the prompts and scores the results; the Arbiter runs the actual verifier in between.

  tools/verifier-calibration.sh list                 fixtures + expected outcome
  tools/verifier-calibration.sh prompt <id>          the verifier prompt for one fixture
  tools/verifier-calibration.sh score <results.jsonl> score verifier verdicts vs expected
                                                     (results: one {"id","overall"} per line)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

FIX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "verifier-fixtures")


def load_fixtures(d=FIX_DIR):
    fx = []
    for f in sorted(glob.glob(os.path.join(d, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            o = json.load(fh)
        o["_file"] = os.path.basename(f)
        fx.append(o)
    return fx


def bucket_verdict(text):
    """Reduce a verifier's output to CONFORMS / FAILS / UNKNOWN. Prefers the final OVERALL line
    (the verifier's mandated closing verdict), falling back to any clear signal."""
    t = (text or "").upper()
    m = re.findall(r"OVERALL[:\s]*\**\s*(CONFORMS|FAILS)", t)
    if m:
        return m[-1]
    if "FAILS" in t and "CONFORMS" not in t:
        return "FAILS"
    if "CONFORMS" in t and "FAILS" not in t:
        return "CONFORMS"
    return "UNKNOWN"


def build_prompt(fx):
    """The verifier prompt for a fixture — self-contained, so the verifier judges from the
    material alone (no repo to inspect). Deliberately does NOT reveal the expected outcome."""
    return (
        "You are the Verifier — a read-only conformance pass. Below is a verdict's Dealbreakers "
        "line and the diff that followed. Check conformance per the workflow: each **resolved** "
        "item must have ACTUALLY landed; each **accepted** item must NOT have been silently worked "
        "around or fixed; and flag any scope drift beyond what the verdict ruled. Judge ONLY from "
        "the material below (there is no repository to inspect). Be strict and specific.\n\n"
        f"## User's request\n{fx['request']}\n\n"
        f"## Verdict — Dealbreakers\n{fx['dealbreakers']}\n\n"
        f"## The diff that followed\n```diff\n{fx['diff']}\n```\n\n"
        "End with a single line exactly: `OVERALL: CONFORMS` or `OVERALL: FAILS:{n}`."
    )


def score(fixtures, results):
    """results: {id -> verifier output text (or a bare CONFORMS/FAILS)}. Returns per-fixture
    {id, expected, actual, ok} rows."""
    rows = []
    for fx in fixtures:
        actual = bucket_verdict(results.get(fx["id"], ""))
        expected = str(fx["expected"]).upper()
        rows.append({"id": fx["id"], "expected": expected, "actual": actual,
                     "ok": actual == expected})
    return rows


def _load_results(path):
    res = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            res[o["id"]] = o.get("overall", "")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verifier calibration: known-bad fixtures + scoring.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list fixtures and expected outcomes")
    p = sub.add_parser("prompt", help="print the verifier prompt for a fixture id")
    p.add_argument("id")
    s = sub.add_parser("score", help="score a results jsonl against expected")
    s.add_argument("results")
    ap.add_argument("--dir", default=FIX_DIR, help="fixtures dir (testing)")
    args = ap.parse_args(argv)

    fixtures = load_fixtures(args.dir)
    if not fixtures:
        print(f"verifier-calibration: no fixtures in {args.dir}", file=sys.stderr)
        return 2

    if args.cmd == "list":
        print(f"{len(fixtures)} calibration fixture(s):\n")
        for fx in fixtures:
            print(f"  [{fx['expected']:>8}]  {fx['id']}")
            print(f"             {fx['rationale']}")
        return 0

    if args.cmd == "prompt":
        fx = next((f for f in fixtures if f["id"] == args.id), None)
        if not fx:
            print(f"verifier-calibration: no fixture id '{args.id}'", file=sys.stderr)
            return 2
        print(build_prompt(fx))
        return 0

    if args.cmd == "score":
        rows = score(fixtures, _load_results(args.results))
        correct = sum(1 for r in rows if r["ok"])
        caught = sum(1 for r in rows if r["expected"] == "FAILS" and r["actual"] == "FAILS")
        known_bad = sum(1 for r in rows if r["expected"] == "FAILS")
        print("Verifier calibration:\n")
        for r in rows:
            mark = "OK " if r["ok"] else "XX "
            print(f"  {mark} {r['id']:<26} expected {r['expected']:<9} got {r['actual']}")
        print(f"\n  {correct}/{len(rows)} correct · caught {caught}/{known_bad} known-bad")
        if caught < known_bad:
            print("  ❌ MISCALIBRATED — the verifier rubber-stamped a known-bad diff.")
            return 1
        if correct < len(rows):
            print("  ⚠️  over-firing — the verifier FAILED the clean control (crying wolf).")
            return 1
        print("  ✅ calibrated — caught every known-bad and passed the control.")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
