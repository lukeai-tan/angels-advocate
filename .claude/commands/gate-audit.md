---
description: Audit the decision journal for patterns — recurring dealbreakers, failed verifications, and gate under-firing.
---

Run the deterministic audit over the whole decision journal and then interpret it:

```
tools/journal-report.sh --audit
```

Run it from the project root. The script does the *counting* (rigor/gate distribution,
dealbreaker dispositions, verifier outcomes, verdicts that FAILED verification, recurring
dealbreakers, and the under-firing skip count) so the numbers are trustworthy rather than
model-estimated. **Do not recompute the stats yourself** — read them from the script's output.

Then add a short Arbiter interpretation *on top of* the raw numbers, focused on what CLAUDE.md
says the journal exists to catch:

- **Verdicts that failed verification** — if any, these are the highest-signal rows. What went
  wrong, and is there a common shape?
- **Recurring dealbreakers** — the heuristic groups normalized-identical items (it matches text,
  not meaning, so read it as a hint, not proof). A dealbreaker that keeps reappearing is a
  standing weakness worth fixing at the root, not re-litigating each time.
- **Under-firing** — the gate's real risk is *skipping review that should have fired*. A `skip`
  count of ~0 alongside many real decisions more likely means skips aren't being logged than that
  none happened; say so honestly rather than reading 0 as "the gate is perfectly calibrated."
- **Rigor vs. outcome** — did light self-checks correlate with later verification failures more
  than structural debates? If the sample is too small to say (it usually is early on), say that.

Keep the interpretation honest and short. The journal is best-effort model-logged (not a
guaranteed hook), so treat absolute counts as a floor, not a census.
