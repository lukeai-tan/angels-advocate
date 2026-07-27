---
description: Compress a long artifact — a debate transcript, the journal, a diff, a file, a wall of prose — into a faithful TL;DR.
argument-hint: "[path | git diff | a topic from this conversation]  (default: the current uncommitted diff)"
---

Spawn the `tldr` subagent to compress whatever the user named, and relay its output.

The target is: **$ARGUMENTS**

- If that is empty, compress the current uncommitted work (`git --no-pager diff`); if the tree is
  clean, compress the most recent debate instead (`tools/debate-view.sh --once`).
- If it is a path, hand the agent the path and let it read the file itself.
- If it names something from this conversation (a debate, a verdict, a report), hand the agent the
  **actual text**, not your recollection of it.

**Courier duty applies.** `tldr` can only be faithful to what it is actually given — its own prompt
says so. Hand it the real artifact or a path it can read. If you paste a paraphrase and ask it to
compress that, you get a summary of your paraphrase, and it will (correctly) flag that at the top.

Relay its output block as-is:

```
TL;DR: <the one takeaway>
- <load-bearing points, 0–5>
CAVEATS KEPT: <dealbreakers / dissent / uncertainty carried through>
FIDELITY: <faithful | lossy: what to still open the source for>
```

Do **not** rewrite, tighten, or "improve" the result — especially do not drop the `CAVEATS KEPT` or
`FIDELITY` lines. Those are the whole point: they are what stops a TL;DR from letting the reader skip
the original *and* walk away wrong. `FIDELITY: lossy` is a useful answer, not a failed one.

`TL;DR: source is already concise` is also a valid result — relay it rather than padding one out.

Notes: the agent is read-only and runs on `haiku` (summarization is the cheap-model sweet spot). It
carries no cross-model independence duty — it does not judge or check — so its model choice is purely
cost vs. fidelity. For a *subtle or high-stakes* debate where dropping a load-bearing caveat would
cost more than the tokens saved, spawn it with an `inherit` model override instead.
