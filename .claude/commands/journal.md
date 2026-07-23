---
description: Show the most recent Angel's Advocate decisions from the decision journal.
argument-hint: "[N]  (how many recent decisions; default 10)"
---

Run the journal reader and show the user its output:

```
tools/journal-report.sh --recent ${ARGUMENTS:-10}
```

`$ARGUMENTS` is an optional entry count (default 10). Run it from the project root, print
the output, and stop — this is a read-only view of `.angel-advoc/journal.jsonl`.

Do NOT hand-parse or re-summarize the JSONL yourself: the script exists precisely so the
counts and ordering are deterministic rather than model-eyeballed. Just relay what it prints.
If the user wants patterns across ALL decisions (recurring dealbreakers, verdicts that failed
verification, under-firing), point them at `/gate-audit` instead.
