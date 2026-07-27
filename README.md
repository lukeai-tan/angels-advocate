# Angel's Advocate

*Two advocates, one verdict.*

An adversarial self-review workflow for [Claude Code](https://claude.com/claude-code). Instead of
letting the model quietly commit to its first idea, significant decisions are examined by two opposed
lenses — an **Angel** who builds the strongest honest case *for* a direction, and a **Devil** who
attacks it — while an **Arbiter** weighs both and makes the call. The debate informs; the Arbiter
decides.

It's the inversion of the familiar Devil's Advocate: someone finally arguing *for* the idea, as hard
as the Devil argues against it.

> **How this was built.** This repo was built using the very workflow it defines: the author set the
> direction and made the verdicts; Claude argued both sides, implemented, and verified its own work.

---

## The roles

The four core roles carry the debate; a set of optional support agents run at specific points in the
lifecycle to feed it evidence, check correctness, and keep the workflow honest.

| Role | Job |
|---|---|
| 😇 **Angel** | Steelman. The strongest *honest* case for the direction — upside, the simplest path that works, why it's sound. Never a strawman. |
| 😈 **Devil** | Attack. What breaks, the hidden cost, the failure mode, the assumption held without checking. Reproduces failures with code when it can. |
| ⚖️ **Arbiter** | The decider (the main Claude). Invokes the lenses, weighs them, and issues a verdict every time. Stays clinical — tone carries zero weight in the ruling. |
| ✅ **Verifier** | The loop-closer. *After* the Arbiter acts, checks the work matched the verdict: each "resolved" dealbreaker landed, each "accepted" one wasn't silently worked around, nothing drifted out of scope. Runs **cross-model** by default (a different model than the Arbiter), so it's de-anchoring conformance *plus* **partial** independent QA — not full QA, but no longer sharing every blind spot. |
| 🔬 **Researcher** *(optional)* | Read-only evidence-gatherer. Runs **in parallel** with the advocates on evidence-heavy debates — reproductions, measurements, blast radius, external docs — and hands its `FINDINGS` to both so they argue from shared ground truth. Never takes a side or rules. |
| 🧪 **Test-Writer** *(optional)* | Runs **in parallel** with the verifier after a verdict is acted on, answering *does the code actually work?* (vs. the verifier's *did it conform?*). Adaptive: durable tests when there's a real surface, an honest "nothing to test" for prose/config. |
| 🏛️ **Historian** *(optional)* | Read-only. *Before* a debate, mines the decision journal + git history for `PRECEDENT` — similar past calls, recurring dealbreakers, verdicts that later failed. Gives the workflow an active memory. Never takes a side. |
| 🧭 **Interpreter** *(optional)* | Read-only, **cross-model**. Fires on the *wrong-problem* gate: when a request is ambiguous, returns 2–4 `INTERPRETATIONS` and the one question that resolves them — so the workflow doesn't build the wrong thing correctly. Hands the fork over; never picks. |
| 🛡️ **Red-Teamer** *(optional)* | A security-specialized Devil, **cross-model**. Attacks only the security surface — injection, secrets, authz, path/shell, deserialization, SSRF, supply-chain, unsafe defaults — and reproduces the exploit where it safely can. Returns `SECURITY FINDINGS`. |
| 📊 **Profiler** *(optional)* | A Researcher specialized to perf/cost. Measures latency, memory, scaling, and token/dollar cost with methodology instead of guessing. Can fan out parallel trials. Returns a `PROFILE`; never rules. |
| ✍️ **Scribe** *(optional)* | Runs at verification-time. Syncs docs (README, CLAUDE.md, comments) to a landed change — **docs only, never logic** — and returns a `DOC SYNC REPORT`. Honest "no change" when nothing drifted. |
| ✂️ **TL;DR** *(optional)* | Utility summarizer. Distills a long debate or verdict, *fidelity over brevity* — preserves dealbreakers, caveats, and dissent, never upgrades confidence, and flags if it was handed a paraphrase. |

## How it works

1. **The gate.** `CLAUDE.md` loads every turn but review does **not** fire on everything. It fires
   on decisions that are hard to reverse, architectural/multi-file, assumption-heavy, forked, or at
   risk of solving the wrong problem. Trivial, reversible, fully-specified work just gets done.
2. **Two modes, honestly labelled.**
   - *Light self-check* (default) — Claude voices both lenses itself in one pass, then arbitrates.
     This is a self-check, **not** an independent debate, and is labelled as such so polish never
     stands in for rigor.
   - *Structural debate* (heavy/irreversible/forked) — spawns the real `angel` and `devil`
     subagents in parallel (independent context + tools), runs one cross-examination round, then
     synthesizes. The `devil` runs **cross-model** (a different model than the Arbiter), so the
     debate isn't just Opus-vs-itself — the attacker doesn't share the proponent's blind spots.
     **Forked** decisions get a dedicated shape: one steelman per approach + one Devil across all, and
     a verdict that *picks* the winner (and can graft a runner-up's best idea onto it).
3. **Every gated decision ends in an accountable verdict** — a rigor line (how much scrutiny this
   got), the two cases, and a verdict that disposes of each Devil dealbreaker *by name*.
4. **The loop closes after the doing.** Once the Arbiter acts on a consequential verdict, it spawns
   the `verifier` — a read-only, fresh-context, **cross-model** pass that checks the work *conformed*
   to the ruling: resolved dealbreakers actually landed, accepted ones weren't silently worked around,
   scope didn't creep. Because it runs on a different model than the Arbiter, it's de-anchoring
   enforcement (catches the Arbiter defending its own closed call) **plus partial independent QA** (it
   no longer shares every blind spot) — though still not *full* QA, and it says so. Execution stays
   with the Arbiter, which holds the full context and the live tree; only the *check* is delegated.
   *(This independence assumes the Arbiter runs on Opus and the checks on Sonnet — the shipped config.
   Run a different Arbiter model? Flip `model:` in `devil.md`/`verifier.md` so they never match it.)*
5. **The workflow remembers.** Each gated decision is appended to a decision journal
   (`.angel-advoc/journal.jsonl`, gitignored, per-machine) — verdict, dealbreakers, and verifier
   outcome — so patterns like recurring dealbreakers or gate under-firing become visible over time.
   Read it back with **`/journal`** (recent decisions) or **`/gate-audit`** (aggregated patterns —
   recurring dealbreakers, verdicts that failed verification, gate under-firing).

### The four lenses
Every review covers **correctness/risk · approach/design · scope discipline · assumptions** (including
"is this even the right problem?"). The `angel` and `devil` agent files are the source of truth.

## Layout

```
CLAUDE.md                       The Arbiter — gate, modes, four lenses, output format
.claude/agents/
  angel.md  devil.md            The two core advocates
  verifier.md                   Post-implementation conformance check (cross-model)
  researcher.md  test-writer.md Optional investigators (may fan out helpers)
  historian.md  interpreter.md  Optional: memory, ambiguity-resolution (cross-model)
  red-teamer.md  profiler.md    Optional: security attack (cross-model), perf/cost
  scribe.md  tldr.md            Optional: doc sync, summarization
.claude/commands/
  journal.md  gate-audit.md     Read/aggregate the decision journal
  debate-view.md                Inline snapshot of the live debate subagents
.claude/workflows/
  angel-advoc-sweep.js          One debate per changed file, in parallel
tools/
  journal.sh                    Append a gated decision to the journal
  journal-report.sh             Reader behind /journal and /gate-audit
  debate-view.sh / *.py         Watch subagents think/act/answer, live in the terminal
  debate-window.sh              Auto-open the viewer in a separate window on spawn (WSL/wt.exe)
  tests/                        Regression suites (bash + python3, no framework)
```

## Using it

Point Claude Code at this folder (the `CLAUDE.md` and `.claude/agents/` are picked up automatically).
The `angel` and `devil` subagents register on startup — if you edit them mid-session, restart to
reload. Then just work: the gate stays quiet on small stuff and convenes the debate when a decision
earns it.

### Install it elsewhere — `install.sh`

To use the workflow in other projects, run the installer:

```
./install.sh --global              # into ~/.claude — loads in EVERY repo
./install.sh --repo /path/to/repo  # self-contained copy into ONE repo (commit it with the code)
./install.sh --repo <path> --dry-run   # preview, change nothing
```

It copies the agents, commands, and `tools/`, **merges** the spawn-depth env into `settings.json`
(your keys preserved), and injects the Arbiter instructions into `CLAUDE.md` between managed
markers — so re-running replaces just that block and never touches your surrounding content.
Global mode also rewrites `tools/…` references to `~/.claude/tools/` so they resolve anywhere.
It never copies your `.angel-advoc/` journal. After installing, verify cross-model independence
for your Arbiter model: `tools/preflight.sh <your-model>` (e.g. `claude-opus-4-8`).

### Summon it anywhere — the `/angel-advoc` command

The always-on gate only works *inside this repo*, where `CLAUDE.md` loads. To force the two-sided
debate on demand in **any** project, install it at user scope:

```
~/.claude/commands/angel-advoc.md      the /angel-advoc command (self-contained orchestration)
~/.claude/agents/{angel,devil,verifier}.md   the subagents it spawns
```

Then `/angel-advoc <what to scrutinize>` (or blank for the current git diff) runs the real structural
debate — parallel `angel` + `devil`, cross-examination, an arbitrated verdict — in whatever project
you're in. New commands/agents load at session start, so restart after installing.

**Keeping the global copies in sync.** The `~/.claude/agents/` files are **symlinks** back to this
repo's `.claude/agents/`, so improving an agent here updates the global command automatically — one
source of truth. The tradeoff: **don't move, rename, or delete this repo**, or the symlinks dangle
and `/angel-advoc` breaks elsewhere. (Prefer robustness over auto-sync? Use plain copies instead and
re-copy when you edit an agent.) The cross-model independence assumes an **Opus** main model; on a
Sonnet main model, flip `model:` in `devil.md`/`verifier.md` to `opus`.

### Fan out across a whole diff — the `angel-advoc-sweep` workflow

For a change that spans many files, `.claude/workflows/angel-advoc-sweep.js` runs **one debate per
changed file in parallel** — each with its own Angel + cross-model Devil + per-file Arbiter verdict —
then synthesizes a single cross-file ruling. It scopes out trivia (lockfiles, generated output,
whitespace) first, and pipelines so a fast file's verdict isn't blocked by a slow file's debate.
Invoke it via the Workflow tool (optionally pass a base ref like `main` to diff against; default is the
current uncommitted diff). This is deterministic orchestration, which is why it's a workflow script
rather than a prompt-driven command — the fan-out, concurrency cap, and structured collection are
scripted, not left to the model to juggle.

### Watch the debate live — `debate-view`

During a structural debate the advocates run as subagents, out of sight. `tools/debate-view.sh` opens
a live terminal view of what they're doing — each agent's role, model, and its thinking, tool calls,
and output as they stream in. It reads the session's subagent transcripts read-only and tails them as
they grow; run it in a second terminal while a debate is underway. The roster's **FACE** column shows
a tiny reactive face per agent that emotes by what it's doing — thinking, running a tool, writing,
or resting — and animates only while the agent is active (angel and devil get their own expressions).

The **TOKENS** column is a *heat map*: a bar plus a colour ramp (navy → azure → amber → orange → red)
showing each agent's token use against the **biggest consumer in the debate being shown**, so you can
see at a glance which subagent is eating the budget. Bar length and colour encode the same fact on
purpose — on a mono or 8-colour terminal, or for a red/green-colourblind reader, the bar alone still
carries the ranking, and the ramp deliberately contains no green because green already means "active"
in the STATUS column. Two honest caveats: the number is **billed** tokens (~98% of it is cache reads,
so it tracks context re-reads and turn count more than how hard an agent worked — the title bar says
`tok billed` for this reason), and because the scale follows the live peak, a finished agent can cool
as a still-running sibling overtakes it. That's how live monitors behave; the alternatives tested
worse (an absolute scale washes small debates flat, and a monotonic ratchet ends up pinning several
agents at max heat at once). `tools/debate-view.sh --once` prints
a one-shot dump instead of the live view (and is what runs automatically when there's no TTY). Agent
"active/done" status is an mtime heuristic — Claude Code emits no explicit finished signal — and the
view labels it as such.

For a quick inline peek from *inside* a Claude Code session, `/debate-view` runs the one-shot dump and
relays it — handy when you don't have a second terminal open. (The live, updating view still needs a
real terminal, since a slash command can't host the curses UI.)

**Auto-open the viewer in its own window (WSL).** `tools/debate-window.sh` pops the live viewer in a
**separate Windows Terminal window** so you never have to remember to open a second terminal. Two ways
to trigger it:

- **Automatic** — wired as a `PreToolUse` hook in `.claude/settings.json`, it fires the moment a
  subagent spawns. It self-guards (atomic lock + `pgrep`) so exactly one window opens per debate and
  an already-open viewer is reused. (Note: Claude Code loads `settings.json` at session start, so the
  hook takes effect on the next session.)
- **On demand** — `/debate-window` (or `tools/debate-window.sh --force`) opens it whenever you want,
  skipping the debounce lock the hook uses.

Either way it no-ops cleanly on any host without `wt.exe`. It's WSL-specific and project-scoped on
purpose, so `install.sh` does **not** propagate it.

## Design note

This workflow is built to resist its own worst failure mode — *theater*: a decision dressed in the
costume of scrutiny it never received. Hence the honest rigor labels, the mandatory dealbreaker
accounting, the symmetric advocate formats (neither side is structurally pushed to over- or
under-claim), the reproduce-when-you-can grounding rule, and — the sharpest cut against theater —
**cross-model checks**: the `devil` and `verifier` run on a different model than the Arbiter, so
"independent" review is independent in fact and not just in name. That independence has one honest
precondition, stated everywhere it matters: it holds only while those agents' model differs from the
Arbiter's, and the files say so rather than letting a same-model pass wear the costume. The agents
have been run against their own design to harden them.
