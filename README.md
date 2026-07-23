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

The four core roles carry the debate; two optional support agents run in parallel to feed it evidence
and check correctness.

| Role | Job |
|---|---|
| 😇 **Angel** | Steelman. The strongest *honest* case for the direction — upside, the simplest path that works, why it's sound. Never a strawman. |
| 😈 **Devil** | Attack. What breaks, the hidden cost, the failure mode, the assumption held without checking. Reproduces failures with code when it can. |
| ⚖️ **Arbiter** | The decider (the main Claude). Invokes the lenses, weighs them, and issues a verdict every time. Stays clinical — tone carries zero weight in the ruling. |
| ✅ **Verifier** | The loop-closer. *After* the Arbiter acts, checks the work matched the verdict: each "resolved" dealbreaker landed, each "accepted" one wasn't silently worked around, nothing drifted out of scope. Runs **cross-model** by default (a different model than the Arbiter), so it's de-anchoring conformance *plus* **partial** independent QA — not full QA, but no longer sharing every blind spot. |
| 🔬 **Researcher** *(optional)* | Read-only evidence-gatherer. Runs **in parallel** with the advocates on evidence-heavy debates — reproductions, measurements, blast radius, external docs — and hands its `FINDINGS` to both so they argue from shared ground truth. Never takes a side or rules. |
| 🧪 **Test-Writer** *(optional)* | Runs **in parallel** with the verifier after a verdict is acted on, answering *does the code actually work?* (vs. the verifier's *did it conform?*). Adaptive: durable tests when there's a real surface, an honest "nothing to test" for prose/config. |

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
   outcome — so patterns like recurring dealbreakers or gate under-firing become visible over time. A
   `/journal` / `/gate-audit` reader to aggregate it is a planned follow-up.

### The four lenses
Every review covers **correctness/risk · approach/design · scope discipline · assumptions** (including
"is this even the right problem?"). The `angel` and `devil` agent files are the source of truth.

## Layout

```
CLAUDE.md                     The Arbiter — gate, modes, four lenses, output format
.claude/agents/angel.md       The steelman subagent
.claude/agents/devil.md       The attacker subagent
.claude/agents/verifier.md    The post-implementation conformance subagent
tools/speak.sh                Optional: speak responses aloud, one voice per role
tools/voices.conf             Maps language + speaker -> a Piper voice model
tools/README_tts.md           TTS setup (WSL + Piper)
```

## Spoken voices (optional)

Responses can be voiced aloud, with a **distinct voice per speaker** (Angel / Devil / Arbiter) and
support for **English, German, Chinese, and Japanese**. When you ask for a language, Claude writes
the response in that language and tags it so the audio router picks the matching voice trio. Runs in
WSL via [Piper](https://github.com/rhasspy/piper). See [tools/README_tts.md](tools/README_tts.md) to
set it up — build and test `speak.sh` standalone before wiring it to a Stop hook.

**Muting.** The Stop hook plays audio in the background, so a long verdict keeps talking after the
turn ends. To cut it off, run `tools/shush.sh` (or `/shush`, or type `! tools/shush.sh` in the prompt
for an instant shell-side stop). It kills the players and renderer but leaves the warm TTS daemon
running.

## Using it

Point Claude Code at this folder (the `CLAUDE.md` and `.claude/agents/` are picked up automatically).
The `angel` and `devil` subagents register on startup — if you edit them mid-session, restart to
reload. Then just work: the gate stays quiet on small stuff and convenes the debate when a decision
earns it.

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
