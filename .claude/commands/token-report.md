---
description: Token usage across Angel's Advocate sessions — the whole-project total, or one debate's subagent cost for a journal entry.
argument-hint: "[session-id]  (optional; default: every session — per-session table + grand total)"
---

Run the token reporter and show the user its output:

```
tools/token-report.sh
```

Run it from the project root. It walks every session transcript under this project (the main
`<id>.jsonl` plus its `subagents/`) and sums `message.usage` into a per-session table — input /
output / cache-read / cache-create / total, newest session first — and a grand total. Table numbers
are abbreviated (`84.8M`); `--json` carries the exact integers.

The second use is pricing a **structural** decision for its journal entry (the Arbiter spec's
journal section). Pass the timestamp of your *first* spawn so earlier work in the same session isn't billed
to this debate:

```
tools/token-report.sh --session <this-session-id> --subagents-only --since <ISO-UTC> --json
```

Drop the printed `total` object (`{input,output,cache_read,cache_create}`) straight into the entry's
`"tokens"` field. The flags, in full:

- `--session <id>` — restrict to one session.
- `--subagents-only` — count only the subagent transcripts, skipping the main conversation. This is
  what makes the number "what the debate cost" rather than "what the whole turn cost".
- `--since <ISO-8601 UTC>` — e.g. `2026-07-30T09:00:00Z`; only lines at/after it count.
- `--json` — emit JSON instead of the table.

(`--project` / `--home` also exist, but they're test overrides — don't reach for them here.)

Reads only local transcript files under `~/.claude/projects/<project>/`; nothing is sent anywhere,
nothing is modified. It is **best-effort**, the same reliability caveat as the journal itself: it
sums whatever usage the transcripts happen to carry, so a line without a `usage` object simply
doesn't count. Report totals as a floor, not a census — and the `"tokens"` field is optional, so
omit it for advisory or light-check decisions rather than logging a number you don't trust.

Just relay what the script prints; don't re-add the columns yourself.
