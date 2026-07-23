#!/usr/bin/env bash
# preflight.sh — static config guard for cross-model independence, run BEFORE a debate.
#
# The workflow's load-bearing honesty claim is that devil/verifier/red-teamer/interpreter
# run on a DIFFERENT model than the Arbiter, so their scrutiny isn't a same-model self-check
# wearing a costume. This checks the model DECLARED in each of those agent files against the
# Arbiter's model, resolving aliases (sonnet/opus/haiku -> concrete IDs) so the comparison is
# apples-to-apples, and warns loudly on a match — so you catch the misconfig before spending a
# whole debate on it.
#
#   tools/preflight.sh <arbiter-model>     # e.g. tools/preflight.sh claude-opus-4-8
#   tools/preflight.sh                     # falls back to $ANTHROPIC_MODEL
#
# HONEST LIMITATION — read this. This checks the agent files' DECLARED model. It CANNOT see
# the availability-fallback collapse: if a role declares `sonnet` but sonnet is unavailable at
# spawn and Claude Code falls back to the Arbiter's model, this still reports OK. Only the
# post-hoc ground-truth check sees that, because it reads the ACTUAL model each agent ran on:
#     tools/debate-view.sh --check-independence      # run AFTER the debate
# So this is an early warning for static misconfig, NOT proof independence held. Fail-closed:
# if the Arbiter's model can't be determined it exits non-zero rather than reporting a pass.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS_DIR="${AGENTS_DIR_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)/.claude/agents}"
CROSS_MODEL_ROLES=(devil verifier red-teamer interpreter)

arbiter_model="${1:-${ANTHROPIC_MODEL:-}}"
if [ -z "$arbiter_model" ]; then
	echo "⚠️  Independence UNVERIFIED — no Arbiter model given and \$ANTHROPIC_MODEL is unset." >&2
	echo "   Pass it explicitly:  tools/preflight.sh <arbiter-model>" >&2
	exit 2
fi

# resolve <alias-or-id>: map sonnet/opus/haiku to a concrete model ID via the env alias map
# (what Claude Code itself uses), falling back to a built-in default if the env var is unset.
# A concrete ID (anything else, including `inherit` handled by the caller) is returned as-is.
resolve() {
	case "$1" in
		sonnet) echo "${ANTHROPIC_DEFAULT_SONNET_MODEL:-claude-sonnet-4-5}" ;;
		opus)   echo "${ANTHROPIC_DEFAULT_OPUS_MODEL:-claude-opus-4-8}" ;;
		haiku)  echo "${ANTHROPIC_DEFAULT_HAIKU_MODEL:-claude-haiku-4-5}" ;;
		*)      echo "$1" ;;
	esac
}

arbiter_resolved="$(resolve "$arbiter_model")"
echo "🔎 Preflight (static config only) — Arbiter model: $arbiter_model -> $arbiter_resolved"

collapsed=0
checked=0
for role in "${CROSS_MODEL_ROLES[@]}"; do
	f="$AGENTS_DIR/$role.md"
	if [ ! -f "$f" ]; then
		echo "   ⚠️  $role — agent file not found ($f); skipped"
		continue
	fi
	# first `model:` line in the frontmatter; empty if the role declares none (== inherit)
	declared="$(grep -m1 -E '^model:[[:space:]]*' "$f" | sed -E 's/^model:[[:space:]]*//; s/[[:space:]]*$//' || true)"
	[ -z "$declared" ] && declared="inherit"
	checked=$((checked + 1))
	# `inherit` means it runs on the Arbiter's model — a collapse for a cross-model role.
	if [ "$declared" = "inherit" ]; then
		resolved="$arbiter_resolved"
	else
		resolved="$(resolve "$declared")"
	fi
	if [ "$resolved" = "$arbiter_resolved" ]; then
		echo "   ❌ $role — declares '$declared' -> $resolved  == Arbiter: COLLAPSE (same-model self-check)"
		collapsed=$((collapsed + 1))
	else
		echo "   ✔ $role — declares '$declared' -> $resolved  differs from Arbiter: ok"
	fi
done

echo "   (static config only — run 'tools/debate-view.sh --check-independence' AFTER the debate for runtime truth)"

if [ "$checked" -eq 0 ]; then
	echo "⚠️  Independence UNVERIFIED — no cross-model agent files found under $AGENTS_DIR." >&2
	exit 2
fi
if [ "$collapsed" -gt 0 ]; then
	echo "❌ $collapsed of $checked cross-model role(s) would collapse to a same-model self-check."
	echo "   Flip the role's model: to something != the Arbiter, or label the rigor honestly."
	exit 1
fi
echo "✅ Config OK — all $checked cross-model role(s) declare a model != the Arbiter's."
