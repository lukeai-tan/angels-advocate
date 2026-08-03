# Angel's Advocate — development repo

This is the source repo for the Angel's Advocate adversarial-review workflow. If you are working
*inside* this repo, you are developing the workflow itself.

## Where the workflow lives

- **`.claude/rules/arbiter.md`** — the operational Arbiter spec: the trigger gate, the two modes
  (light self-check vs structural debate), the five lenses, the output format, the verification pass,
  and the decision journal. It is an unscoped project rule, so **it loads into every session
  automatically** (same as a `CLAUDE.md`) — here and in any repo that installs the workflow. This is
  the single canonical artifact `install.sh` ships to consumers; edit the workflow's behaviour there,
  not here.
- **`docs/calibration-notes.md`** — the rationale and null results behind the honesty rules (the
  light-mode acceptance statistic, the unmeasured "two-thirds" figure, the rejected confidence-score
  design). Reference material, **read on demand** — not loaded every turn, and not shipped to
  consumers. Read it before you touch a calibration rule or re-measure any of the numbers.
- **`.claude/agents/`**, **`.claude/commands/`**, **`.claude/workflows/`**, **`tools/`** — the
  subagents, slash commands, orchestration scripts, and the honesty/integrity tooling.
- **`README.md`** — the human-facing overview of the whole workflow and how to install it elsewhere.

## Developing here

- After changing any agent, the debate/verify machinery, the rule, or a calibration fixture, run
  **`tools/self-check.sh`** — it runs every integrity/calibration suite behind one green/red answer.
  (`--strict` also fails on any skipped check; add the arbiter model as an arg if it isn't the
  default, e.g. `tools/self-check.sh claude-opus-4-8`.)
- The workflow reviews *your* changes to itself: the gate in `.claude/rules/arbiter.md` applies to
  edits made in this repo just as it would anywhere else.

<!-- The operational spec deliberately does NOT live in this file: it lives in .claude/rules/arbiter.md
     so it is a single canonical artifact that install.sh copies verbatim (no per-repo drift, no
     marker surgery in a consumer's own CLAUDE.md). Keep this file a thin dev pointer. -->
