# Calibration notes — the rationale behind the Arbiter's honesty rules

**What this file is.** The operational spec (`.claude/rules/arbiter.md`) states the *rules* — "a light
self-check is narration, label it honestly"; "don't pin decaying statistics." This file holds the
*derivations and null results* behind those rules: the measurements that were run, what they found,
and why the rules survive despite the statistics being weak. It is **development-repo reference
material for maintaining the workflow**, not a per-turn operating instruction — so it lives here,
read on demand when you touch a calibration rule, rather than loading into context every turn.

It is deliberately **not** shipped by `install.sh`: a consumer repo needs the rules, not the story of
how they were calibrated on this repo's own journal.

---

## Why a light self-check is narration, not deliberation

**It is usually narration, not deliberation — know which you are doing.** A 2026-07-27 audit of this
repo's own journal traced light-mode decisions in the transcript and found the 😈 block is typically
composed *after* the investigation that found the problems and *after* the fix landed — the model
discovers a trap while working, fixes it, then writes it up as though freshly caught. That
qualitative reading — a transcript-traced look at what actually happened — is the load-bearing
evidence for the rule ("write the Devil's side before you edit, or mark it retrospective"), and it
still stands.

**The supporting statistic does not, and CLAUDE.md used to overstate it.** The 07-27 audit reported
light mode accepting 12% of its dealbreakers versus 31% for structural, "a 2.6x gap," and called the
fingerprint quantitative. A 2026-07-31 re-measurement over 54 entries reproduced those numbers
exactly (11.8% vs 31.2%, 2.66x) *and* tested them for the first time: **Fisher's exact p = 0.175 —
not significant.** The whole gap rested on **two** accepted light-mode dealbreakers out of 17 — move
one entry and the ratio swings by more than the effect being claimed. It has shrunk as n grew:
2.66x in the audited window (light 2/17, structural 10/32 — a window the audit fixed, so those
digits are stable), and it decays toward 1.0x on every wider slice, both entries-after-the-rule and
the whole journal pooled. *Every* comparison is null. Those wider figures are deliberately **not**
written out as digits: they move with every new journal entry, so any figure pinned into a file is
stale by the next decision. **`tools/journal-report.sh --audit` now recomputes it**: it
cross-tabulates dealbreaker acceptance by rigor and prints the ratio with a two-tailed Fisher's
exact p, labelling the result null or significant, on every run. Read the live numbers there and
don't re-freeze them. It buckets on `rigor` and says so in its own header — the trap below is
enforced by a test (`journal-report_test.sh`), not just warned about.

So the honest statement is: the ratio is **suggestive and underpowered**, consistent with the
narration reading and equally consistent with noise. It is not a measured fingerprint, and nothing
should be built on it as though the effect size were established. Treat the transcript evidence as the
reason for the rule and the ratio as a weak corroborator worth re-checking as n grows — the same
standard this workflow applies to everyone else's claims.

> **If you re-measure it: bucket entries by `rigor`, not by `gate`.** Bucketing on `gate` files every
> `light self-check` that fired on a fork under "structural" and materially changes the answer — an
> error made and caught while writing the original paragraph.

---

## The "two-thirds" narration figure is unmeasured (not null — unmeasured)

CLAUDE.md once said the audit found the retrospective-costume worn in "two-thirds" of the repo's own
gated decisions.

**That two-thirds figure is unverified, and `tools/verdict-timing.sh` was built specifically to check
it and could not.** The probe reads ordering straight off the transcript clock, on two axes, and
neither adjudicates the claim. The *edits* axis (was the 😈 block written before the fix?) reports a
direction that holds at every clustering gap — pre-written outnumbers retrospective 2.0x–4.5x — but
it cannot test the audit's claim at all, because the audit was about the block being composed after
the **investigation** that surfaced the problems, and investigation leaves no edits to timestamp.
The *investigation* axis, added 2026-07-31 to close exactly that gap, does resolve — read-only work
precedes most verdicts but not near-all of them, clearing the tool's 90% no-resolution floor, which
Bash-inclusive counting fails outright by hitting 100% and measuring nothing (which is why Bash is
excluded). But its answer **flips with a free constant**: the audit's shape leads at a short episode
gap and trails at a long one, while the unresolved both-sides bucket swells to most of the
population. No direction survives every row, which is the test the edits axis passes and this one
fails. Digits are deliberately not pinned here either — `verdict-timing.sh` recomputes the whole
sensitivity curve, and its own resolving-power floor, on every run; quoting one frozen row is
exactly the "pick a gap whose row agrees with you" error this section closes on.

So: two-thirds is a hand-read number that the mechanical probes can neither confirm nor refute. It
is **not** demoted the way the acceptance statistic above was — that one was measured and found
null; this one is simply *unmeasured*, and the honest response to an unmeasured number is to stop
citing it as settled, not to replace it with a different number. **The rule does not depend on it.**
The rule rests on the transcript-traced qualitative reading, which never needed a ratio, and on the
plain point that an objection written after the fix cannot change the fix — true at any frequency.
Don't quote two-thirds as evidence; if you want a figure here, the work is to make the both-sides
bucket resolvable, not to pick a gap whose row agrees with you.

---

## The three under-fire probes and their denominators

The journal only records what *did* fire, so three separate probes triangulate what didn't, each with
a different population — and all three are wired into `tools/self-check.sh` as **informational**
probes (they report counts, never a pass/fail, because their denominators are judgement calls and a
threshold on one would be the very costume this workflow forbids):

- `tools/gate-sweep.sh` — population is **commits**: scans recent git history for gate-worthy changes
  (multi-file, irreversible/schema, dependency, infra, large churn) with no journal entry nearby.
  Structurally blind to decisions that never became a commit.
- `tools/transcript-sweep.sh` — supplies that missing denominator by mining *edit episodes* from the
  session transcripts instead (raw counts + window sensitivity, never a rate).
- `tools/verdict-timing.sh` — measures a third thing again: the **order** the 😈 block was written in
  (see the two-thirds section above).

---

## What was deliberately NOT adopted (2026-07-29 structural debate, journaled)

A numeric/bucketed **confidence** score and a **calibration** loop. Self-reported confidence is
weakly calibrated and rephrase-unstable; a "signal-derived" High/Med/Low isn't cleanly mechanical
(Medium is undefinable from the existing fields); and the calibration loop mechanically *cannot
close* — the journal is append-only with no `held` write path, and the only available outcome signal
(`verifier: CONFORMS`) measures conformance-to-the-verdict, **not whether the verdict was right**.
Don't re-propose these without first solving the `held`-outcome and correctness-signal problems.
