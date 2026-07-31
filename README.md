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

The four core roles carry the debate; optional support agents run at specific points in the lifecycle
to feed it evidence, check correctness, and keep the workflow honest; and a `builder` executor lets the
Arbiter delegate parallel-independent build work.

| Role | Job |
|---|---|
| 😇 **Angel** | Steelman. The strongest *honest* case for the direction — upside, the simplest path that works, why it's sound. Never a strawman. |
| 😈 **Devil** | Attack. What breaks, the hidden cost, the failure mode, the assumption held without checking. Reproduces failures with code when it can. |
| ⚖️ **Arbiter** | The decider (the main Claude). Invokes the lenses, weighs them, and issues a verdict every time. Stays clinical — tone carries zero weight in the ruling. |
| ✅ **Verifier** | The loop-closer. *After* the Arbiter acts, checks the work matched the verdict: each "resolved" dealbreaker landed, each "accepted" one wasn't silently worked around, nothing drifted out of scope. Runs **cross-model** by default (a different model than the Arbiter), so it's de-anchoring conformance *plus* **partial** independent QA — not full QA, but no longer sharing every blind spot. |
| 🔬 **Researcher** *(optional)* | Read-only evidence-gatherer. Runs **in parallel** with the advocates on evidence-heavy debates — reproductions, measurements, blast radius, external docs — and hands its `FINDINGS` to both so they argue from shared ground truth. Never takes a side or rules. |
| 🧪 **Test-Writer** *(optional)* | Runs **in parallel** with the verifier after a verdict is acted on, answering *does the code actually work?* (vs. the verifier's *did it conform?*). Adaptive: durable tests when there's a real surface, an honest "nothing to test" for prose/config. |
| 🔨 **Builder** *(optional)* | Executor. The Arbiter delegates one self-contained build unit to it — Edit/Write/Bash, **inherits** the Arbiter's model, stays strictly in the briefed scope, verifies where cheap, and reports exactly what changed (stops-and-asks on ambiguity rather than guessing). Fan out one per file-disjoint unit to build in parallel; coupled, context-heavy work stays with the Arbiter. Returns a `BUILD REPORT`, never a verdict. |
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
   got), the two cases, a verdict that disposes of each Devil dealbreaker *by name*, and a
   **falsifier**: the one fact that, if it turned out true, would flip the ruling. `tools/verdict-lint.py`
   mechanically checks a structural-debate verdict against the Devil's own transcript — every raised
   dealbreaker disposed of, every `refuted` backed by evidence — a gap the verifier can't cover because
   it never sees that transcript.
4. **The loop closes after the doing.** Once the Arbiter acts on a consequential verdict, it spawns
   the `verifier` — a read-only, fresh-context, **cross-model** pass that checks the work *conformed*
   to the ruling: resolved dealbreakers actually landed, accepted ones weren't silently worked around,
   scope didn't creep. Because it runs on a different model than the Arbiter, it's de-anchoring
   enforcement (catches the Arbiter defending its own closed call) **plus partial independent QA** (it
   no longer shares every blind spot) — though still not *full* QA, and it says so. Execution stays
   with the Arbiter, which holds the full context and the live tree; only the *check* is delegated.
   *(This independence assumes the Arbiter on Opus and the four cross-model checks —
   `devil`/`verifier`/`red-teamer`/`interpreter` — on Sonnet; the shipped default is an Opus 4.8
   Arbiter with Sonnet 4.6 checks. `tools/preflight.sh <model>` verifies the config before a debate and
   `debate-view.sh --check-independence` the runtime after; `/self-check` folds the preflight in. Run a
   different Arbiter model? Flip `model:` in those four files so they never match it.)*
5. **The workflow remembers.** Each gated decision is appended to a decision journal
   (`.angel-advoc/journal.jsonl`, gitignored, per-machine) — verdict, dealbreakers, and verifier
   outcome — so patterns like recurring dealbreakers or gate under-firing become visible over time.
   Read it back with **`/journal`** (recent decisions) or **`/gate-audit`** (aggregated patterns —
   recurring dealbreakers, verdicts that failed verification, gate under-firing). **`/tldr`**
   compresses any long artifact — a debate transcript, the journal, a diff, a file — into a faithful
   TL;DR that keeps the dealbreakers and caveats rather than smoothing them away. **`/self-check`** runs
   every integrity harness at once — the calibration fixtures that prove the checkers aren't
   rubber-stamping, the cross-model preflight guard, and the gate under-fire sweep — behind one
   green/red answer.

### The five lenses
Every review covers **correctness/risk · approach/design (incl. maintainability) · scope discipline ·
assumptions** (including "is this even the right problem?") **· caller/consumer ergonomics**. The
`angel` and `devil` agent files are the source of truth.

## Layout

```
CLAUDE.md                       The Arbiter — gate, modes, five lenses, output format (+ falsifier)
.claude/agents/
  angel.md  devil.md            The two core advocates
  verifier.md                   Post-implementation conformance check (cross-model)
  builder.md                    Executor — delegate a scoped build unit (inherits model)
  researcher.md  test-writer.md Optional investigators (may fan out helpers)
  historian.md  interpreter.md  Optional: memory, ambiguity-resolution (cross-model)
  red-teamer.md  profiler.md    Optional: security attack (cross-model), perf/cost
  scribe.md  tldr.md            Optional: doc sync, summarization
  reversibility.md              Non-rostered manual tool (recovery-story attack; folded into devil)
.claude/commands/
  journal.md  gate-audit.md     Read/aggregate the decision journal
  self-check.md                 Run every integrity check at once
  debate-view.md  debate-window.md  debate-gui.md   Watch the live debate (inline / window / browser)
  tldr.md                       Compress a long artifact
.claude/workflows/
  angel-advoc-sweep.js          One debate per changed file, in parallel
  parallel-debate.js            One decision, fanned across a Devil lens-panel + advocates
  build-sweep.js                Decompose a change into disjoint units, build in parallel, verify
tools/
  journal.sh / journal-report.sh   Append / read the decision journal
  verdict-lint.py               Lint a verdict's Dealbreakers block (coverage vs the Devil + evidence)
  preflight.sh                  Static cross-model config guard (run before a structural debate)
  self-check.sh                 Consolidated integrity run (tests + preflight + gate-sweep)
  gate-sweep.sh                 Scan recent commits for gate under-fires
  transcript-sweep.sh           The denominator gate-sweep lacks: decision *episodes* mined from
                                session transcripts (raw counts + window sensitivity, never a rate)
  verifier-calibration.sh       Prove the verifier isn't rubber-stamping (checked-in fixtures)
  token-report.sh               Per-session / per-subagent token + cost totals
  debate-view.sh / debate-gui.sh / *.py   Watch subagents live (terminal / loopback browser)
  debate-window.sh              Auto-open the viewer in a separate window on spawn (WSL/wt.exe)
  tests/                        Regression + calibration suites (bash + python3, no framework)
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
Sonnet main model, flip `model:` in the four cross-model files (`devil`/`verifier`/`red-teamer`/`interpreter`)
to `opus`, and confirm with `tools/preflight.sh <model>`.

### Fan out across a whole diff — the `angel-advoc-sweep` workflow

For a change that spans many files, `.claude/workflows/angel-advoc-sweep.js` runs **one debate per
changed file in parallel** — each with its own Angel + cross-model Devil + per-file Arbiter verdict —
then synthesizes a single cross-file ruling. It scopes out trivia (lockfiles, generated output,
whitespace) first, and pipelines so a fast file's verdict isn't blocked by a slow file's debate.
Invoke it via the Workflow tool (optionally pass a base ref like `main` to diff against; default is the
current uncommitted diff). This is deterministic orchestration, which is why it's a workflow script
rather than a prompt-driven command — the fan-out, concurrency cap, and structured collection are
scripted, not left to the model to juggle.

### Widen a single debate — the `parallel-debate` workflow

For one heavy or forked decision, `.claude/workflows/parallel-debate.js` widens the attack instead of
the file set: an Angel, **one cross-model Devil per lens** (a lens-panel), plus a Researcher and
Historian all run in parallel, then one cross-examination round, then it hands the collected material
back for the Arbiter to rule — it never rules itself ("the debate informs; the Arbiter decides"). Five
independent adversaries, each on its own lens, surface what a single Devil misses. An optional final
phase fans `builder`s out (worktree-isolated) to implement the ruling.

### Build in parallel — the `build-sweep` workflow and the `builder` role

Execution stays with the Arbiter *by default* — it holds the full conversation and the live tree, so
tightly-coupled, context-heavy work is built inline (a subagent knows less). But **parallel-independent**
work pays to delegate: `.claude/workflows/build-sweep.js` decomposes a ruled change into **file-disjoint**
units, builds each with its own `builder` in parallel, and verifies the combined result — self-integrating
with *no merge step*, because no two builders ever touch the same file. Work that can't be split into
disjoint files is coupled, not parallelisable — which is exactly why it stays with the Arbiter, not a
limitation to route around.

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
agents at max heat at once).

The rest of the roster: **IND** is a live independence badge — `√` a cross-model role genuinely on a
different family than the Arbiter, `×` one that **collapsed** onto the Arbiter's family, `~` a role
that inherits the Arbiter's model by design, `?` when the Arbiter's model can't be determined
(fail-closed). It surfaces this repo's central invariant while the debate is still running instead of
only in a separate `--check-independence` run. **TOOK** is each agent's wall-clock duration, and
**COST** an *indicative* dollar estimate — list prices per model family, hardcoded, so treat it as
orientation and not billing; an unpriced family shows `—` rather than a wrong number, and you can
override the table with a `.angel-advoc/prices.json` of the same shape. Columns are shed
cheapest-first as the terminal narrows (COST → TOOK → FACE → IND → STATUS), so the roster always fits
instead of clipping whichever column happened to land last.

Press **`t`** for a **timeline** — a Gantt of the shown debate that makes its shape legible: a
parallel opening round lines up, a cross-examination steps right, and a still-running agent is drawn
with a distinct glyph.

In the detail pane, the advocates' own severity vocabulary is **colour-coded** — DEALBREAKER and
FAILS in red, WORTH-NOTING and PARTIAL in amber, concessions and PASS in green, section headers
(CASE FOR/AGAINST, STRONGEST GROUND, SCOPE DRIFT…) underlined — so you can skim a long transcript by
seriousness instead of reading a uniform wall of prose. `tools/debate-view.sh --once` prints
a one-shot dump instead of the live view (and is what runs automatically when there's no TTY). Agent
"active/done" status is an mtime heuristic — Claude Code emits no explicit finished signal — and the
view labels it as such.

For a quick inline peek from *inside* a Claude Code session, `/debate-view` runs the one-shot dump and
relays it — handy when you don't have a second terminal open. (The live, updating view still needs a
real terminal, since a slash command can't host the curses UI.)

### …or watch it in the browser — `debate-gui`

`tools/debate-gui.sh` (or `/debate-gui`) serves the **same** `debate_lib.snapshot()` data as a local,
**loopback-only** web page — `127.0.0.1` only, read-only over the transcripts, nothing sent anywhere,
transcript text rendered as DOM text so nothing in it can inject. It's a master–detail layout: the
compact agent rail on the left, the selected agent's transcript at full height on the right, a
collapsible Steins;Gate-style **world-lines** timeline (each agent a glowing line branching off a shared
spine, coloured by model *family* so cross-model independence is visible at a glance), and the same
independence badge. The launcher runs the server detached on a stable port and prints the URL. Prefer
the terminal viewer over SSH; reach for the browser view when a GUI is simply handier.

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
**cross-model checks**: the `devil`, `verifier`, `red-teamer`, and `interpreter` run on a different
model than the Arbiter, so "independent" review is independent in fact and not just in name. That
independence has one honest precondition, stated everywhere it matters: it holds only while those
agents' model differs from the Arbiter's, and the files say so rather than letting a same-model pass
wear the costume — with `preflight.sh` and `--check-independence` there to *prove* it rather than
assume it. The same anti-theater instinct runs through the tooling: `verdict-lint` catches a verdict
that silently drops a dealbreaker, and calibration fixtures (run via `/self-check`) prove the verifier
and the lint aren't just rubber-stamping. The agents have been run against their own design to harden
them.
