---
description: Open the debate viewer as a browser GUI (zero-dep loopback web server; opens your browser).
argument-hint: "[session-id]  (optional; default: most-recently-active session)"
---

Launch the browser GUI for the debate subagents and relay what the launcher prints:

```
tools/debate-gui.sh ${ARGUMENTS}
```

`$ARGUMENTS` is an optional session id (default: the most-recently-active session under this
project). Run it from the project root. The launcher starts a **loopback-only** web server in
the background, tries to open your browser at it, and prints the URL. Relay to the user the URL
line and the stop hint the script prints — one of:

- opened the browser view at `http://127.0.0.1:<port>/` (running in the background), or
- launched but no URL appeared in time, followed by the server's output (for diagnosis).

Why a background launcher and not the live curses view: the GUI server blocks on `serve_forever`,
so a slash command (which runs inline in this turn) can't host it without hanging — the launcher
detaches it and hands back the URL. It's the browser counterpart to `/debate-window` (which opens
the **terminal** viewer in a tmux/Windows-Terminal pane). For a quick inline **snapshot** with no
server at all, use `/debate-view`.

The GUI shares the exact same data as the terminal viewer (it reuses `debate_lib.snapshot`): a
roster of each agent's role/model/status/duration/tokens/cost, a detail pane of thinking + tool
calls + output, and the cross-model independence banner. It only ever **reads** the session's
subagent transcripts (`~/.claude/projects/<project>/<session>/subagents/`) and serves them on
`127.0.0.1` only — nothing is sent anywhere and nothing is modified. Stop the server with `pkill -f 'debate_view.py --gui'`.

The server uses a **stable port (8770)** so the browser tab keeps working across restarts;
relaunching stops the previous viewer holding that port (by PID, so a GUI you have open for
another project is left alone) and rebinds it. Override with `ANGEL_ADVOC_GUI_PORT`. The page
also has a **session switcher** — every debate recorded under this project, picked from a
server-side allow-list so a session id from the browser is never joined into a filesystem path.

Just relay what the script prints; don't reconstruct anything yourself.
