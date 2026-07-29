---
description: Open the live debate viewer in a separate pane/window (tmux split, or Windows Terminal on WSL).
---

Open the live debate viewer in its own pane/window and tell the user what happened:

```
tools/debate-window.sh --force
```

Run it from the project root, then relay the one line the script prints — one of:

- opened the viewer in a tmux split pane (focus stays on the current pane), or
- opened a new Windows Terminal window running the live viewer, or
- a viewer is already open (reusing it — page debates with `[` / `]`), or
- no supported terminal (not in tmux, and no `wt.exe`), so this host is a no-op.

`--force` skips the debounce lock the automatic hook uses, so this always opens the viewer on demand
(unless one is already open). It runs the **live**, updating curses view — the thing a slash command
can't host inline. For a quick inline **snapshot** instead, use `/debate-view`.

Backend is chosen automatically: inside a tmux session it opens a split pane; otherwise, on WSL with
Windows Terminal, it shells out to `wt.exe`; on any other host the script is a clean no-op. It only
ever *reads* the session's subagent transcripts — nothing is sent anywhere and nothing is modified.
Just relay what the script prints; don't reconstruct anything yourself.
