#!/usr/bin/env python3
"""token_report.py — token usage across Angel's Advocate sessions.

Walks every session transcript under this project (main <id>.jsonl + its subagents) and sums
`message.usage`, split by type (input / output / cache-read / cache-create), per session and as
a grand total. All parsing lives in debate_lib (unit-tested); this is a thin reporting shell.

  tools/token-report.sh                 per-session table + grand total (this project)
  tools/token-report.sh --json          same data as JSON
  tools/token-report.sh --session <id> [--subagents-only] [--since <ISO-UTC>]
                                        one session (used by the journal to price a debate)

--since takes an ISO-8601 UTC stamp (e.g. 2026-07-24T10:00:00Z); only lines at/after it count.
Reads only local transcript files; nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import debate_lib as dl  # noqa: E402

_COLS = [("input", "INPUT"), ("output", "OUTPUT"),
         ("cache_read", "CACHE-R"), ("cache_create", "CACHE-C")]


def session_usage(info, since=None, subagents_only=False):
    """Total usage for one session = (main transcript unless subagents_only) + subagents."""
    u = dl.blank_usage()
    if not subagents_only and info.get("main"):
        u = dl.add_usage(u, dl.usage_from_file(info["main"], since))
    u = dl.add_usage(u, dl.usage_from_files(info.get("subagents", []), since))
    return u


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _session_mtime(info):
    paths = ([info["main"]] if info.get("main") else []) + info.get("subagents", [])
    return max((_mtime(p) for p in paths), default=0.0)


def collect(pdir, since=None, only_session=None, subagents_only=False):
    """Return (rows, grand_total). rows: [{"id","short","usage","agents"}], newest first."""
    sessions = dl.session_transcripts(pdir)
    if only_session:
        sessions = {k: v for k, v in sessions.items() if k == only_session}
    rows = []
    grand = dl.blank_usage()
    for sid, info in sessions.items():
        u = session_usage(info, since=since, subagents_only=subagents_only)
        grand = dl.add_usage(grand, u)
        rows.append({"id": sid, "short": sid[:8], "usage": u,
                     "agents": len(info.get("subagents", [])), "_m": _session_mtime(info)})
    rows.sort(key=lambda r: r["_m"], reverse=True)
    for r in rows:
        del r["_m"]
    return rows, grand


def _print_table(rows, grand, label):
    idw = 8
    numw = 9
    header = f"{'SESSION':<{idw}}  {'AGENTS':>6}  " + "  ".join(f"{h:>{numw}}" for _, h in _COLS) + f"  {'TOTAL':>{numw}}"
    print(f"Angel's Advocate — token usage · {label}\n")
    print(header)
    print("-" * len(header))
    for r in rows:
        u = r["usage"]
        cells = "  ".join(f"{dl.fmt_tokens(u[k]):>{numw}}" for k, _ in _COLS)
        print(f"{r['short']:<{idw}}  {r['agents']:>6}  {cells}  {dl.fmt_tokens(dl.usage_total(u)):>{numw}}")
    print("-" * len(header))
    cells = "  ".join(f"{dl.fmt_tokens(grand[k]):>{numw}}" for k, _ in _COLS)
    print(f"{'TOTAL':<{idw}}  {'':>6}  {cells}  {dl.fmt_tokens(dl.usage_total(grand)):>{numw}}")
    print(f"\n{len(rows)} session(s).")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Token usage across Angel's Advocate sessions.")
    ap.add_argument("--project", help="project dir override (default: derived from cwd)")
    ap.add_argument("--home", help="home dir override (testing)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--since", help="ISO-8601 UTC stamp; count only usage at/after it")
    ap.add_argument("--session", help="restrict to one session id")
    ap.add_argument("--subagents-only", action="store_true",
                    help="count only subagent transcripts (skip the main session)")
    args = ap.parse_args(argv)

    pdir = args.project or dl.project_dir(os.getcwd(), home=args.home)
    if not os.path.isdir(pdir):
        print(f"token-report: no project dir at {pdir}", file=sys.stderr)
        return 2

    rows, grand = collect(pdir, since=args.since, only_session=args.session,
                          subagents_only=args.subagents_only)
    if args.json:
        print(json.dumps({"sessions": rows, "total": grand,
                          "total_tokens": dl.usage_total(grand)},
                         ensure_ascii=False, separators=(",", ":")))
        return 0
    _print_table(rows, grand, os.path.basename(pdir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
