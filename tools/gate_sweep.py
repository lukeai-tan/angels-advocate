#!/usr/bin/env python3
"""gate_sweep.py — surface the gate's blind spot: decisions that should have fired review.

The Angel's Advocate gate's real risk is UNDER-firing — shipping something gate-worthy without
any review or journal record. That's invisible by construction. This scans recent git commits,
scores each for gate-worthiness (multi-file, irreversible/schema, dependency, infra, large
churn), checks whether a decision-journal entry plausibly covers it (by time proximity), and
flags gate-worthy commits with NO nearby journal entry as candidate under-fires.

  tools/gate-sweep.sh                 scan the last 30 commits
  tools/gate-sweep.sh 60              scan the last 60
  tools/gate-sweep.sh --since 2.weeks scan by git date range
  tools/gate-sweep.sh --all          show coverage for every scanned commit, not just misses
  tools/gate-sweep.sh --window 4      journal-coverage window in hours (default 3)

HONEST CAVEAT: coverage is heuristic (a journal entry within the window ≈ "reviewed"). The
journal is gitignored/per-machine, so on a fresh clone it's empty and everything reads as a
miss. This is a prompt for human judgment, not a verdict.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import debate_lib as dl  # noqa: E402  (reuse _ts_epoch for ISO parsing)

# filename bases / substrings that signal a higher-stakes change
_DEP_FILES = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "requirements.txt",
    "pipfile", "pipfile.lock", "poetry.lock", "go.mod", "go.sum", "cargo.toml", "cargo.lock",
    "gemfile", "gemfile.lock", "composer.json", "composer.lock", "pyproject.toml",
}
_INFRA_HINTS = ("dockerfile", "docker-compose", ".github/workflows/", "makefile", ".tf")
_IRREVERSIBLE_HINTS = ("migration", "migrate", "schema", ".sql")


def _risky_category(path):
    """Return the risk category for a changed path, or None."""
    low = path.lower()
    base = low.rsplit("/", 1)[-1]
    if any(h in low for h in _IRREVERSIBLE_HINTS):
        return "irreversible/schema"
    if base in _DEP_FILES:
        return "dependency change"
    if any(h in low for h in _INFRA_HINTS):
        return "infra/CI"
    return None


def classify_commit(n_files, adds, dels, paths, big_churn=200, many_files=3):
    """Reasons a commit looks gate-worthy (empty list = not gate-worthy). Strong signals
    (risky paths) come first so callers can rank by severity."""
    reasons, strong = [], False
    cats = {}
    for p in paths:
        c = _risky_category(p)
        if c:
            cats.setdefault(c, []).append(p)
    for cat in ("irreversible/schema", "dependency change", "infra/CI"):
        ps = cats.get(cat)
        if ps:
            strong = True
            ex = ps[0] + (f" (+{len(ps) - 1} more)" if len(ps) > 1 else "")
            reasons.append(f"{cat}: {ex}")
    if n_files >= many_files:
        reasons.append(f"multi-file ({n_files} files)")
    churn = adds + dels
    if churn >= big_churn:
        reasons.append(f"large churn (~{churn} lines)")
    return reasons, strong


def is_covered(commit_epoch, journal_epochs, window_s):
    """True if any journal entry falls within +/- window_s of the commit time."""
    if commit_epoch is None:
        return False
    return any(abs(commit_epoch - j) <= window_s for j in journal_epochs if j is not None)


def score(reasons, strong, adds, dels, n_files):
    """Rank key: strong signals dominate, then churn, then file count."""
    return (1 if strong else 0, len(reasons), adds + dels, n_files)


# --- git / journal gathering (impure) ----------------------------------------

def repo_root(start):
    try:
        out = subprocess.run(["git", "-C", start, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:
        return start


def git_commits(root, n, since=None):
    fmt = "\x01%H\x1f%cI\x1f%s"
    cmd = ["git", "-C", root, "log", f"-n{n}", "--numstat", f"--format={fmt}"]
    if since:
        cmd += ["--since", since]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    commits, cur = [], None
    for line in out.splitlines():
        if line.startswith("\x01"):
            if cur:
                commits.append(cur)
            h, date, subj = (line[1:].split("\x1f", 2) + ["", "", ""])[:3]
            cur = {"hash": h, "date": date, "subject": subj,
                   "files": 0, "adds": 0, "dels": 0, "paths": []}
        elif line.strip() and cur is not None:
            parts = line.split("\t")
            if len(parts) == 3:
                a, d, p = parts
                cur["files"] += 1
                cur["adds"] += int(a) if a.isdigit() else 0
                cur["dels"] += int(d) if d.isdigit() else 0
                cur["paths"].append(p)
    if cur:
        commits.append(cur)
    return commits


def journal_epochs(root):
    path = os.environ.get("ANGEL_ADVOC_JOURNAL") or os.path.join(root, ".angel-advoc", "journal.jsonl")
    eps = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    e = dl._ts_epoch(json.loads(raw).get("ts"))
                except Exception:
                    e = None
                if e is not None:
                    eps.append(e)
    return eps, path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Flag commits that look gate-worthy but were never journaled.")
    ap.add_argument("n", nargs="?", type=int, default=30, help="how many recent commits to scan (default 30)")
    ap.add_argument("--since", help="git --since date range (e.g. '2.weeks', '2026-07-01')")
    ap.add_argument("--window", type=float, default=3.0, help="journal-coverage window in hours (default 3)")
    ap.add_argument("--all", action="store_true", help="show coverage for every commit, not only misses")
    ap.add_argument("--root", help="repo root override (testing)")
    args = ap.parse_args(argv)

    root = args.root or repo_root(os.getcwd())
    try:
        commits = git_commits(root, args.n, since=args.since)
    except Exception as e:
        print(f"gate-sweep: git log failed: {e}", file=sys.stderr)
        return 2
    eps, jpath = journal_epochs(root)
    window_s = args.window * 3600

    scanned = len(commits)
    gate_worthy = misses = 0
    rows = []
    for c in commits:
        reasons, strong = classify_commit(c["files"], c["adds"], c["dels"], c["paths"])
        if not reasons:
            continue
        gate_worthy += 1
        cov = is_covered(dl._ts_epoch(c["date"]), eps, window_s)
        if not cov:
            misses += 1
        if args.all or not cov:
            rows.append((score(reasons, strong, c["adds"], c["dels"], c["files"]), c, reasons, cov))
    rows.sort(key=lambda r: r[0], reverse=True)

    print("Angel's Advocate — under-firing sweep")
    print(f"  scanned {scanned} commit(s); {gate_worthy} look gate-worthy; "
          f"{misses} have NO journal entry within ±{args.window:g}h")
    print(f"  journal: {jpath} ({len(eps)} dated entr{'y' if len(eps) == 1 else 'ies'})")
    if not eps:
        print("  ⚠️  journal is empty here (it's gitignored/per-machine) — every commit reads as a miss.")
    print()
    if not rows:
        print("  No candidate under-fires. ✅" if not args.all else "  (no gate-worthy commits)")
        return 0
    label = "All gate-worthy commits:" if args.all else "Candidate under-fires (gate-worthy, no journal entry nearby):"
    print(label)
    for _, c, reasons, cov in rows:
        tag = "covered" if cov else "MISS"
        print(f"  {c['hash'][:9]}  {c['date'][:16]}  [{tag}]  {c['subject'][:70]}")
        print(f"      {' · '.join(reasons)}")
    print("\n  Heuristic: 'covered' = a journal entry within the window. Review each MISS: if it "
          "should have been gated, note it (even a 'skip-noted' entry makes under-firing measurable).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
