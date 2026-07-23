---
description: Snapshot what the debate subagents (angel/devil/verifier/…) are thinking, doing, and returning.
argument-hint: "[session-id]  (optional; default: most-recently-active session)"
---

Run the debate viewer in one-shot dump mode and show the user its output:

```
tools/debate-view.sh --once ${ARGUMENTS}
```

`$ARGUMENTS` is an optional session id (default: the most-recently-active session under this
project). Run it from the project root, print the dump, and stop — this is a **read-only** view of
the session's subagent transcripts (`~/.claude/projects/<project>/<session>/subagents/`); nothing is
sent anywhere and nothing is modified.

Why `--once` and not the live UI: a slash command runs *inside* this Claude Code session, which can't
host the interactive curses view. So this gives an inline **snapshot** of each agent's role, model,
thinking, tool calls, and output. For the **live** stream that updates as a debate unfolds, tell the
user to run `tools/debate-view.sh` in a **separate terminal** (keys: `j`/`k` move, `f` follow newest,
`q` quit).

Honest caveats to pass along if the dump is empty:

- The viewer reads *subagent* transcripts, so it only has content when subagents actually ran —
  a **structural debate** or the **`angel-advoc-sweep`** workflow. A plain **light self-check** runs
  entirely inside the Arbiter and spawns nothing, so there is nothing to show for those turns.
- Agent "active/done" status is an **mtime heuristic** (Claude Code emits no explicit finished
  signal) — report it as such, don't present it as ground truth.

Just relay what the script prints; don't hand-reconstruct the transcript yourself.
