#!/usr/bin/env bash
# journal.sh — append one decision-journal entry, as validated JSONL.
#
# The Angel's Advocate workflow decides a lot but remembers nothing — verdicts are
# ephemeral. This logs each gated decision so a later reader (/journal, /gate-audit)
# can spot patterns: recurring dealbreakers, verdicts that failed verification, and
# — the gate's real risk — decisions that should have fired review but didn't.
#
# The Arbiter calls this after a gated verdict (and after the verifier runs, if it
# was going to). It is model-driven best-effort logging, NOT a guaranteed hook — it
# is only as reliable as the Arbiter remembering to call it.
#
# Usage: pipe the entry as a JSON object on stdin. The script validates it, adds a
# `ts` timestamp, and appends it as ONE line to the journal:
#
#   printf '%s' '{"gate":"fork","rigor":"structural debate","target":"...",
#     "verdict":"...","dealbreakers":[{"item":"...","disposition":"accepted",
#     "why":"..."}],"verifier":"CONFORMS"}' | tools/journal.sh
#
# Journal location: $ANGEL_ADVOC_JOURNAL, else <repo-root>/.angel-advoc/journal.jsonl
# (repo root = this script's parent dir's parent). The .angel-advoc/ dir is created
# on demand and is gitignored — a private, per-machine log.
#
# Exit 0 on success. Non-zero (and NOTHING written) if stdin isn't valid JSON, so a
# malformed entry can never corrupt the log with a half-line.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
JOURNAL="${ANGEL_ADVOC_JOURNAL:-$REPO_ROOT/.angel-advoc/journal.jsonl}"
PY="${PYTHON_BIN:-python3}"

command -v "$PY" >/dev/null 2>&1 || { echo "journal.sh: python3 not found (set PYTHON_BIN)." >&2; exit 1; }

# Read the entry from stdin into an env var. We pass it to python via the
# environment, NOT a pipe: `python3 - <<'PY'` already binds stdin to the heredoc
# (that's where the program comes from), so a pipe into it would be discarded and
# sys.stdin would be empty.
JOURNAL_INPUT="$(cat)"
export JOURNAL_INPUT

# Validate + normalize to a single line + inject ts, all in one python pass. If the
# input isn't valid JSON (or isn't a JSON object), python exits non-zero and we
# append nothing. The timestamp is stamped here so the model never hand-types dates.
LINE="$("$PY" - <<'PY'
import json, os, sys, datetime
try:
    obj = json.loads(os.environ.get("JOURNAL_INPUT", ""))
except Exception as e:
    sys.stderr.write(f"journal.sh: input is not valid JSON: {e}\n")
    sys.exit(1)
if not isinstance(obj, dict):
    sys.stderr.write("journal.sh: entry must be a JSON object\n")
    sys.exit(1)
# UTC, seconds precision, Z-suffixed. Stamp only if the caller didn't supply one.
obj.setdefault("ts", datetime.datetime.now(datetime.timezone.utc)
               .strftime("%Y-%m-%dT%H:%M:%SZ"))
# Compact, one line, non-ASCII preserved (emoji in verdict text stays readable).
sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
PY
)"

mkdir -p "$(dirname "$JOURNAL")"
# Concurrency safety: this is a single O_APPEND write (`>>`), which POSIX guarantees
# is atomic up to PIPE_BUF (4096 bytes on Linux) on a local filesystem — so parallel
# appenders can't interleave a normal entry (~600 bytes observed). This invariant
# holds only while (a) the append stays a SINGLE write of one line ≤4096 bytes, and
# (b) the journal is on a local fs (NFS weakens O_APPEND atomicity). Only the Arbiter
# writes here, sequentially, so no lock is needed; if that changes or entries can
# exceed 4096 bytes, add an flock around this line. Regression-tested in tools/tests/.
printf '%s\n' "$LINE" >> "$JOURNAL"
echo "journal.sh: logged to $JOURNAL" >&2
