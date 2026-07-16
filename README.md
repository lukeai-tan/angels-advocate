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

## The three roles

| Role | Job |
|---|---|
| 😇 **Angel** | Steelman. The strongest *honest* case for the direction — upside, the simplest path that works, why it's sound. Never a strawman. |
| 😈 **Devil** | Attack. What breaks, the hidden cost, the failure mode, the assumption held without checking. Reproduces failures with code when it can. |
| ⚖️ **Arbiter** | The decider (the main Claude). Invokes the lenses, weighs them, and issues a verdict every time. Stays clinical — tone carries zero weight in the ruling. |
| ✅ **Verifier** | The loop-closer. *After* the Arbiter acts, checks the work matched the verdict: each "resolved" dealbreaker landed, each "accepted" one wasn't silently worked around, nothing drifted out of scope. De-anchoring conformance — **not** independent QA. |

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
     synthesizes.
3. **Every gated decision ends in an accountable verdict** — a rigor line (how much scrutiny this
   got), the two cases, and a verdict that disposes of each Devil dealbreaker *by name*.
4. **The loop closes after the doing.** Once the Arbiter acts on a consequential verdict, it spawns
   the `verifier` — a read-only, fresh-context pass that checks the work *conformed* to the ruling:
   resolved dealbreakers actually landed, accepted ones weren't silently worked around, scope didn't
   creep. It's de-anchoring enforcement (catches the Arbiter defending its own closed call), **not**
   independent QA — it shares the model's blind spots and says so. Execution stays with the Arbiter,
   which holds the full context and the live tree; only the *check* is delegated.

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

## Using it

Point Claude Code at this folder (the `CLAUDE.md` and `.claude/agents/` are picked up automatically).
The `angel` and `devil` subagents register on startup — if you edit them mid-session, restart to
reload. Then just work: the gate stays quiet on small stuff and convenes the debate when a decision
earns it.

## Design note

This workflow is built to resist its own worst failure mode — *theater*: a decision dressed in the
costume of scrutiny it never received. Hence the honest rigor labels, the mandatory dealbreaker
accounting, the symmetric advocate formats (neither side is structurally pushed to over- or
under-claim), and the reproduce-when-you-can grounding rule. The agents have been run against their
own design to harden them.
