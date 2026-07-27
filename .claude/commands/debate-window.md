---
description: Open the live debate viewer in a separate Windows Terminal window (WSL).
---

Open the live debate viewer in its own window and tell the user what happened:

```
tools/debate-window.sh --force
```

Run it from the project root, then relay the one line the script prints — one of:

- opened a new Windows Terminal window running the live viewer, or
- a viewer window is already open (reusing it — page debates with `[` / `]`), or
- `wt.exe` not found, so this host isn't WSL + Windows Terminal (no-op).

`--force` skips the debounce lock the automatic hook uses, so this always opens a window on demand
(unless one is already open). The window runs the **live**, updating curses view — the thing a slash
command can't host inline. For a quick inline **snapshot** instead, use `/debate-view`.

This is WSL-specific: it shells out to `wt.exe` (Windows Terminal). On any other host the script is a
clean no-op. It only ever *reads* the session's subagent transcripts — nothing is sent anywhere and
nothing is modified. Just relay what the script prints; don't reconstruct anything yourself.
