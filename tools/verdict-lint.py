#!/usr/bin/env python3
"""verdict-lint.py — a mechanical lint on an Arbiter verdict's Dealbreakers block.

WHY THIS EXISTS
---------------
A 2026-07-29 structural debate proposed a cross-model "verdict-auditor" agent that would
audit the Arbiter's *reasoning*. The debate rejected the agent form: auditing "anchoring" or
"manufactured balance" is unfalsifiable from a transcript (a genuinely stronger Angel argument
and a biased Arbiter produce identical records), so such a role can only rubber-stamp or
hallucinate — the exact false-confidence costume the Arbiter spec forbids.

But one slice of the idea IS falsifiable and, crucially, is NOT covered by the `verifier`: the
verifier is handed only the verdict's Dealbreakers *line*, never the Devil's raw transcript
(verifier.md), so by construction it cannot notice that a dealbreaker the Devil actually raised
was silently dropped from the block. That is what this lint checks. It is a script + Arbiter
rule (the tldr precedent for "audit the Arbiter's own output"), NOT an agent.

WHAT IT CHECKS (all mechanical, all falsifiable — see tools/tests/verdict_lint_test.sh)
  1. Coverage — every DEALBREAKER the Devil raised is disposed of in the Dealbreakers block.
     If a Devil transcript (or an explicit --devil-count) is supplied and the block disposes
     of FEWER items than the Devil raised, that is a probable silent drop -> FAIL. If the block
     says "none raised" while the Devil raised >=1, that is a hard FAIL.
  2. Well-formedness — every bullet carries a recognised disposition
     (resolved / accepted / accepting / refuted).
  3. Refuted-with-evidence — the Arbiter spec allows `refuted` ONLY with evidence (a reproduction, a
     measurement, a counter-example, a file:line). A `refuted` bullet with no evidence token -> FAIL.

WHAT IT DELIBERATELY DOES NOT CHECK
  - Whether a `resolved` claim actually landed in the diff — that is the `verifier`'s job.
  - Whether the Arbiter *reasoned* soundly / was anchored — unfalsifiable, out of scope by design.

USAGE
  tools/verdict-lint.py VERDICT.md                     # verdict-block-only checks
  tools/verdict-lint.py --devil AGENT.jsonl VERDICT.md # + coverage vs the Devil's transcript
  tools/verdict-lint.py --devil-count 4 VERDICT.md     # + coverage vs an explicit count
  cat verdict.txt | tools/verdict-lint.py --devil-count 3   # verdict on stdin

Exit code: 0 = clean, 1 = one or more FAILs (mirrors verifier-calibration.sh's contract).
"""
from __future__ import annotations

import argparse
import json
import re
import sys

# A disposition keyword marks a bullet as a real, disposed dealbreaker.
_DISPOSITIONS = ("resolved", "accepting", "accepted", "refuted")
# Evidence tokens that make a `refuted` legitimate (Arbiter spec: "refuted only with evidence").
_EVIDENCE = re.compile(
    r"(?i)("
    r"reproduc|test|measur|benchmark|counter-?example|verified|ran it|output|"
    r"\bexit\b|\d+\s*%|\b\d+\s*/\s*\d+\b|"          # exit codes, percentages, N/M ratios
    r"[\w./-]+\.(?:py|sh|js|ts|md|json|txt|c|go|rs|rb):\d+|"  # file:line
    r"\bline\s+\d+\b|\bfile\b"
    r")"
)


def extract_dealbreakers_block(text):
    """Return the lines of the '**Dealbreakers**' block as a list of bullet strings.

    A bullet may span multiple physical lines; each new bullet starts with '- '. The block
    ends at the next bold header (e.g. '**Runners-up**', '**BOTTOM LINE**') or a markdown
    heading ('##') or end of text. Returns (bullets, found) — found is False if there is no
    Dealbreakers block at all.
    """
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"\s*\**\s*Dealbreakers\s*\**\s*$", ln.strip("# ").strip()) \
           or re.match(r"\s*\*\*Dealbreakers\*\*\s*$", ln.strip()):
            start = i + 1
            break
    if start is None:
        return [], False

    bullets, cur = [], None
    for ln in lines[start:]:
        s = ln.strip()
        # end of the block: a new bold section header or a markdown heading
        if re.match(r"\*\*(?!.*resolved|.*accept|.*refut)", s) and not s.startswith("- "):
            if re.match(r"\*\*[A-Z]", s) and "Dealbreaker" not in s:
                break
        if s.startswith("##"):
            break
        if s.startswith("- ") or s.startswith("* "):
            if cur is not None:
                bullets.append(cur.strip())
            cur = s[2:]
        elif cur is not None:
            if s == "":
                # a blank line inside the block; keep accumulating unless the block clearly ended
                cur += " "
            else:
                cur += " " + s
    if cur is not None:
        bullets.append(cur.strip())
    return [b for b in bullets if b], True


def is_none_raised(bullets):
    """True if the block is the canonical single 'none raised' line."""
    return len(bullets) == 1 and re.search(r"none\s+raised", bullets[0], re.I) is not None


def devil_dealbreaker_count(transcript_path):
    """Count DEALBREAKER markers in the Devil's OWN (assistant) output in a JSONL transcript.

    Only assistant/model text is counted — never the prompts fed TO the Devil — so the Arbiter's
    own use of the word 'DEALBREAKER' in a cross-examination prompt can't inflate the count.
    Best-effort: returns None if the file can't be parsed.
    """
    try:
        chunks = []
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                msg = obj.get("message", obj)
                role = msg.get("role") or obj.get("type")
                if role not in ("assistant", "model"):
                    continue
                content = msg.get("content")
                if isinstance(content, str):
                    chunks.append(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            chunks.append(part.get("text", ""))
        blob = "\n".join(chunks)
        if not blob:
            return None
        # Count DEALBREAKER markers but not WORTH-NOTING; collapse "**DEALBREAKER**" etc.
        return len(re.findall(r"(?<!WORTH-)\bDEALBREAKER\b", blob))
    except OSError:
        return None


def lint(verdict_text, devil_count=None):
    """Return (findings, ok). findings is a list of (level, message); ok is False on any FAIL."""
    findings = []
    bullets, found = extract_dealbreakers_block(verdict_text)

    if not found:
        findings.append(("FAIL", "no '**Dealbreakers**' block found — every gated verdict must "
                                 "carry one (write 'none raised' if there were none)."))
        return findings, False

    none_raised = is_none_raised(bullets)
    disposed = 0 if none_raised else len(bullets)

    # 1. Coverage vs the Devil's raised dealbreakers
    if devil_count is not None:
        if devil_count > 0 and none_raised:
            findings.append(("FAIL", f"verdict says 'none raised' but the Devil raised "
                                     f"{devil_count} dealbreaker(s) — all silently dropped."))
        elif disposed < devil_count:
            findings.append(("FAIL", f"verdict disposes {disposed} item(s) but the Devil raised "
                                     f"{devil_count} — {devil_count - disposed} may have been "
                                     f"dropped. If items were merged, name the merge in the block."))
        else:
            findings.append(("OK", f"coverage: {disposed} disposed >= {devil_count} raised."))

    if none_raised:
        findings.append(("OK", "block declares 'none raised'."))
        return findings, all(f[0] != "FAIL" for f in findings)

    # 2 & 3. Per-bullet well-formedness + refuted-with-evidence
    for b in bullets:
        low = b.lower()
        disp = next((d for d in _DISPOSITIONS if d in low), None)
        # a short label prefix is fine; we only require a recognised disposition somewhere
        if disp is None:
            findings.append(("FAIL", f"bullet has no recognised disposition "
                                     f"(resolved/accepted/refuted): \"{_snip(b)}\""))
            continue
        if "refuted" in low and not _EVIDENCE.search(b):
            findings.append(("FAIL", f"'refuted' with no evidence cited (needs a reproduction, "
                                     f"measurement, counter-example, or file:line): \"{_snip(b)}\""))
        else:
            findings.append(("OK", f"{disp}: \"{_snip(b)}\""))

    return findings, all(f[0] != "FAIL" for f in findings)


def _snip(s, n=70):
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "…"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lint an Arbiter verdict's Dealbreakers block.")
    ap.add_argument("verdict", nargs="?", help="verdict file (default: stdin)")
    ap.add_argument("--devil", help="the Devil's agent-<id>.jsonl transcript (counts raised "
                                    "dealbreakers from its own output)")
    ap.add_argument("--devil-count", type=int, default=None,
                    help="explicit count of dealbreakers the Devil raised (overrides --devil)")
    args = ap.parse_args(argv)

    verdict_text = open(args.verdict, encoding="utf-8").read() if args.verdict else sys.stdin.read()

    devil_count = args.devil_count
    if devil_count is None and args.devil:
        devil_count = devil_dealbreaker_count(args.devil)
        if devil_count is None:
            print("verdict-lint: warning — could not parse --devil transcript; "
                  "skipping the coverage check.", file=sys.stderr)

    findings, ok = lint(verdict_text, devil_count)

    for level, msg in findings:
        mark = {"FAIL": "❌", "OK": "✔", "WARN": "⚠"}.get(level, "·")
        print(f"  {mark} {level}: {msg}")
    n_fail = sum(1 for lvl, _ in findings if lvl == "FAIL")
    print(f"\nverdict-lint: {'CLEAN' if ok else f'{n_fail} FAIL(s)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
