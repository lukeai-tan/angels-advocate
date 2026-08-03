#!/usr/bin/env bash
# install.sh — install the Angel's Advocate workflow into another repo, or globally.
#
# The workflow is just files: the Arbiter spec (.claude/rules/arbiter.md), the subagents
# (.claude/agents/), the slash commands (.claude/commands/), a settings env, and the tools/
# scripts. This copies them where Claude Code will find them.
#
# The Arbiter spec ships as an unscoped Claude Code *rule* (.claude/rules/arbiter.md), which loads
# into every session automatically — so it never has to be stapled into your project's own CLAUDE.md.
# Your CLAUDE.md is left entirely untouched (a legacy managed block from older installs is removed).
#
# Modes:
#   ./install.sh --repo /path/to/other/repo    # self-contained: copies into THAT repo
#   ./install.sh --global                       # into ~/.claude, so EVERY repo gets it
#   ./install.sh --repo <path> --dry-run        # print what would happen, change nothing
#
# What it does, non-destructively:
#   - agents/commands/rules/tools : copied in (our files overwritten; yours left alone)
#   - .claude/settings.json       : the spawn-depth env is MERGED (existing keys preserved)
#   - CLAUDE.md                   : NOT touched, except to strip a legacy angel-advocate managed
#                                   block left by older versions (the spec is a rule now)
#   - global mode also rewrites `tools/...` references in the commands + rule to
#     ~/.claude/tools/ so they resolve from any working directory
#
# It never copies .angel-advoc/ (your per-machine journal) or .git/. Idempotent: safe to re-run.
#
# Model note: the cross-model roles (devil/verifier/red-teamer/interpreter) are pinned to
# `sonnet` assuming the Arbiter runs Opus. On a different Arbiter model, flip those pins — and
# check with `tools/preflight.sh <model>` (before) / `debate-view.sh --check-independence` (after).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE=""
DEST_REPO=""
DRY=0
while [ "$#" -gt 0 ]; do
	case "$1" in
		--global) MODE="global"; shift ;;
		--repo)   MODE="repo"; DEST_REPO="${2:-}"; shift 2 ;;
		--dry-run) DRY=1; shift ;;
		-h|--help) awk 'NR==1{next} /^set -euo/{exit} {sub(/^# ?/,""); print}' "${BASH_SOURCE[0]}"; exit 0 ;;
		-*) echo "install.sh: unknown option '$1'" >&2; exit 2 ;;
		*)  MODE="${MODE:-repo}"; DEST_REPO="$1"; shift ;;   # bare path implies --repo
	esac
done

[ -n "$MODE" ] || { echo "install.sh: choose --global or --repo <path> (see --help)" >&2; exit 2; }

PY="${PYTHON_BIN:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "install.sh: python3 not found (set PYTHON_BIN)." >&2; exit 1; }

# --- resolve destinations ----------------------------------------------------
if [ "$MODE" = "global" ]; then
	CLAUDE_DIR="$HOME/.claude"
	TOOLS_DEST="$CLAUDE_DIR/tools"
	CLAUDEMD="$CLAUDE_DIR/CLAUDE.md"
	TOOLS_REF="$HOME/.claude/tools"        # how commands/docs should reference tools
	REWRITE=1
	LABEL="global (~/.claude)"
else
	[ -n "$DEST_REPO" ] || { echo "install.sh: --repo needs a path" >&2; exit 2; }
	[ -d "$DEST_REPO" ] || { echo "install.sh: '$DEST_REPO' is not a directory" >&2; exit 2; }
	DEST_REPO="$(cd "$DEST_REPO" && pwd)"
	CLAUDE_DIR="$DEST_REPO/.claude"
	TOOLS_DEST="$DEST_REPO/tools"
	CLAUDEMD="$DEST_REPO/CLAUDE.md"
	TOOLS_REF="tools"                      # relative path works from the repo root
	REWRITE=0
	LABEL="repo ($DEST_REPO)"
	git -C "$DEST_REPO" rev-parse --show-toplevel >/dev/null 2>&1 \
		|| echo "install.sh: note — '$DEST_REPO' isn't a git repo; the journal falls back to \$ANGEL_ADVOC_JOURNAL." >&2
fi

RULES_DEST="$CLAUDE_DIR/rules"   # the Arbiter spec ships here as an unscoped rule (loads every session)

say() { printf '  %s\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then printf '  would: %s\n' "$*"; else eval "$*"; fi; }

echo "Installing Angel's Advocate -> $LABEL${DRY:+ }$([ "$DRY" = 1 ] && echo '(dry run)')"

# --- copy agents, commands, rules, tools ------------------------------------
run "mkdir -p '$CLAUDE_DIR/agents' '$CLAUDE_DIR/commands' '$RULES_DEST' '$TOOLS_DEST'"
say "agents  -> $CLAUDE_DIR/agents/"
run "cp '$SRC'/.claude/agents/*.md '$CLAUDE_DIR/agents/'"
say "commands-> $CLAUDE_DIR/commands/"
run "cp '$SRC'/.claude/commands/*.md '$CLAUDE_DIR/commands/'"
say "rules   -> $RULES_DEST/ (the Arbiter spec — loads every session)"
run "cp '$SRC'/.claude/rules/*.md '$RULES_DEST/'"
say "tools   -> $TOOLS_DEST/"
run "cp -R '$SRC'/tools/. '$TOOLS_DEST/'"

# global: rewrite bare tools/ refs in the copied commands AND the rule so they resolve anywhere
if [ "$REWRITE" = 1 ] && [ "$DRY" != 1 ]; then
	for f in "$CLAUDE_DIR"/commands/*.md "$RULES_DEST"/*.md; do
		[ -e "$f" ] || continue
		sed -i "s#\btools/#$TOOLS_REF/#g" "$f"
	done
	say "rewrote tools/ refs in commands + rule -> $TOOLS_REF/"
fi

# --- merge the settings env (preserve existing keys) -------------------------
say "settings-> $CLAUDE_DIR/settings.json (merge spawn-depth env)"
if [ "$DRY" != 1 ]; then
	SETTINGS="$CLAUDE_DIR/settings.json" "$PY" - <<'PY'
import json, os
p = os.environ["SETTINGS"]
try:
    cfg = json.load(open(p))
    if not isinstance(cfg, dict): cfg = {}
except Exception:
    cfg = {}
env = cfg.setdefault("env", {})
env.setdefault("CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH", "2")  # setdefault: never clobber yours
os.makedirs(os.path.dirname(p), exist_ok=True)
json.dump(cfg, open(p, "w"), indent=2)
open(p, "a").write("\n")
PY
fi

# --- migrate: strip any legacy managed CLAUDE.md block ----------------------
# The spec used to be injected into the consumer's CLAUDE.md between managed markers. It now ships as
# a rule (copied above), so we no longer touch CLAUDE.md — except to remove that legacy block on
# upgrade, so an old install doesn't leave a stale second copy of the spec loading every turn.
if [ -f "$CLAUDEMD" ]; then
	say "CLAUDE.md-> $CLAUDEMD (leave as-is; strip legacy angel-advocate block if present)"
	if [ "$DRY" != 1 ]; then
		CLAUDEMD="$CLAUDEMD" "$PY" - <<'PY'
import os
target = os.environ["CLAUDEMD"]
BEGIN = "<!-- >>> angel-advocate (managed by install.sh) >>> -->"
END = "<!-- <<< angel-advocate <<< -->"
cur = open(target, encoding="utf-8").read()
if BEGIN in cur and END in cur:                          # remove just the managed block
    pre = cur.split(BEGIN, 1)[0].rstrip("\n")
    post = cur.split(END, 1)[1].lstrip("\n")
    joined = (pre + ("\n\n" if pre and post else "") + post).strip("\n")
    open(target, "w", encoding="utf-8").write(joined + "\n" if joined else "")
    print("  removed legacy angel-advocate managed block (spec is a rule now)")
PY
	fi
fi

echo
echo "Done. Next:"
if [ "$MODE" = "global" ]; then
	echo "  • The workflow now loads in every repo (via ~/.claude/rules/arbiter.md). Add tools to PATH if you like:"
	echo "      export PATH=\"\$HOME/.claude/tools:\$PATH\""
else
	echo "  • Commit .claude/ (agents, commands, rules) and tools/ in $DEST_REPO to share it with the team."
	echo "  • Your CLAUDE.md was not modified — the Arbiter spec loads from .claude/rules/arbiter.md."
fi
echo "  • Verify cross-model independence for your Arbiter model:"
echo "      $TOOLS_REF/preflight.sh <your-arbiter-model>    # e.g. claude-opus-4-8"
