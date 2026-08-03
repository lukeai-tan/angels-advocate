---
description: Lint a structural-debate verdict's Dealbreakers block — coverage vs the Devil, well-formed dispositions, refuted-with-evidence.
argument-hint: "[verdict.md]  (optional; default: the Dealbreakers block you just issued)"
---

Lint the Dealbreakers block **before acting on the verdict**, and relay the result:

```bash
# 1. the block under test — paste it VERBATIM, '**Dealbreakers**' header included.
#    (If $ARGUMENTS names a file, use that instead and skip this step.)
cat > /tmp/verdict-block.md <<'EOF'
**Dealbreakers**
- <name> → **resolved by** …
EOF

# 2. this debate's Devil transcript: the newest devil-tagged agent-<id>.jsonl in the
#    most-recently-active session (-r also reaches workflow-spawned devils under workflows/).
P=~/.claude/projects/"$(pwd | tr / -)"
S="$(ls -dt "$P"/*/subagents | head -1)"
DEVIL="$(grep -rl '"attributionAgent":"devil"' "$S" --include='agent-*.jsonl' | xargs -r ls -t | head -1)"

# 3. lint it
python3 tools/verdict-lint.py --devil "$DEVIL" /tmp/verdict-block.md
```

Run it from the project root. The Arbiter spec (`.claude/rules/arbiter.md`) mandates this lint on any structural-debate verdict, but
step 2 is the reason the rule gets skipped by hand — hence the snippet. If `$DEVIL` comes out empty
(a light self-check spawns nothing), either drop `--devil` for the block-only checks or pass
`--devil-count <n>` with the count you read off the Devil's output yourself.

What it checks (all three, all mechanical):

- **Coverage** — the block must dispose of at least as many items as the Devil raised. It counts
  `DEALBREAKER` markers in the Devil's *own* assistant output only, so your cross-examination
  prompt wording can't inflate the count. "none raised" against a Devil that raised ≥1 is a hard FAIL.
- **Well-formedness** — every bullet carries a recognised disposition (resolved / accepted /
  accepting / refuted).
- **Refuted-with-evidence** — a `refuted` bullet with no evidence token (reproduction, measurement,
  counter-example, exit code, `file:line`) FAILs. This is the Arbiter-spec rule, now enforced.

Exit `0` = CLEAN, `1` = one or more FAILs. Note the quiet third case: if the `--devil` transcript
can't be parsed it warns on stderr and **skips the coverage check** — a CLEAN with that warning is a
weaker result than a CLEAN without one, so read the stderr line before trusting it.

The honest limit, state it when you relay: this checks the **shape** of the Dealbreakers block, not
whether the dispositions are *right*. It cannot tell you a `resolved` actually landed in the diff
(that's the `verifier`) or that the Arbiter reasoned soundly (unfalsifiable from a transcript — the
reasoning-audit agent was proposed and rejected). Relay the pass/fail plus any FAIL lines verbatim;
on a FAIL, fix the block and re-run rather than shipping the verdict.
