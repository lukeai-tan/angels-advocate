#!/usr/bin/env python3
"""debate_gui.py — zero-dependency BROWSER view for Angel's Advocate agents.

The terminal viewer (debate_view.py) is the primary monitor: tested, SSH-portable, and
co-located in the same pane the debate runs from. This is an OPTIONAL local browser
front-end for when a GUI is handier — a second way to look at the exact same data. It
shares the same parsing brain (debate_lib.snapshot), so the two views can never diverge.

Launch:  tools/debate-view.sh --gui        (the shell dispatches here)

It starts a loopback-only HTTP server, opens your browser at it, and the page polls a
JSON snapshot of the live transcripts every ~1.2s. Stdlib only — http.server, webbrowser,
json, threading — no third-party deps, same invariant as the rest of the toolkit.

Security posture (this is the one component in the toolkit that opens a socket):
  * Binds 127.0.0.1 ONLY — never 0.0.0.0; the server is unreachable from other machines.
  * Serves exactly three FIXED routes (/, /sessions.json, /snapshot.json). The request path
    is only ever compared for equality, never used to build a filesystem path — so there is
    no path-traversal surface and no arbitrary-file read. The page's JS sidecar is read once
    at import from a path built from __file__, never from anything a request supplies.
  * Read-only: it reads the same local transcript files the terminal viewer tails; it
    writes nothing and runs no agent code.
  * The page renders agent-authored transcript text via textContent / DOM text nodes,
    never innerHTML, so nothing in a transcript can inject markup or script into the page.
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import urllib.parse
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import debate_lib as dl  # noqa: E402


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_handler(subagents_dir, label, arbiter_model, project_dir=None):
    """Build a request handler with the session bound in — so the request path is never
    used to locate anything on disk.

    `project_dir` (optional) enables the session switcher: the server enumerates the real
    sessions itself via debate_lib.list_sessions() and the client may only pick an id from
    THAT list. A session id arriving over HTTP is matched against the enumeration and the
    directory comes from the matched entry — it is never joined into a path. So the
    'a request never builds a filesystem path' property survives the added feature; an
    id like '../../etc' simply fails to match and 404s.
    """

    def resolve(sess_id):
        """(subagents_dir, label) for a requested session id — allow-list only."""
        if not sess_id or sess_id == label:
            return subagents_dir, label
        if project_dir:
            for s in dl.list_sessions(project_dir):
                if s["id"] == sess_id:          # exact match against enumerated reality
                    return s["subagents"], s["id"]
        return None, None

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):  # silence the default per-request stderr access log
            pass

        def _send(self, code, body, ctype):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def do_GET(self):
            # Fixed-route dispatch: compare the path, never derive a file path from it.
            parts = self.path.split("?", 1)
            path = parts[0]
            query = urllib.parse.parse_qs(parts[1]) if len(parts) > 1 else {}
            if path == "/":
                self._send(200, PAGE, "text/html; charset=utf-8")
            elif path == "/sessions.json":
                sessions = [{"id": s["id"], "agents": s["agents"]}
                            for s in (dl.list_sessions(project_dir) if project_dir else [])]
                if not sessions:
                    sessions = [{"id": label, "agents": 0}]
                self._send(200, json.dumps({"sessions": sessions, "current": label}),
                           "application/json")
            elif path == "/snapshot.json":
                want = (query.get("session") or [None])[0]
                sub, lab = resolve(want)
                if sub is None:      # not in the server's own enumeration -> refuse
                    self._send(404, json.dumps({"error": "unknown session"}),
                               "application/json")
                    return
                try:
                    snap = dl.snapshot(sub, arbiter_model=arbiter_model,
                                       repo_root=_repo_root())
                    snap["label"] = lab
                    self._send(200, json.dumps(snap), "application/json")
                except Exception as e:  # never take the whole server down on one bad poll
                    self._send(500, json.dumps({"error": str(e)}),
                               "application/json")
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")

        do_HEAD = do_GET

    return Handler


# A stable default so the browser tab/bookmark keeps working across restarts, instead of a
# fresh ephemeral port every launch. Not privileged, and unlikely to collide with common
# dev servers (3000/5000/8000/8080). Pass port=0 for the old ephemeral behaviour.
DEFAULT_PORT = 8770


def main(subagents_dir, label, arbiter_model=None, open_browser=True,
         port=DEFAULT_PORT, project_dir=None):
    handler = make_handler(subagents_dir, label, arbiter_model, project_dir)
    try:
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as e:
        # Someone else holds it (another project's viewer, an unrelated app). Falling back
        # to an ephemeral port beats refusing to start — but SAY so, so the changed URL
        # isn't a mystery.
        if port == 0:
            raise
        print(f"debate-view --gui: port {port} unavailable ({e.strerror or e}); "
              f"falling back to an ephemeral port.", flush=True)
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = httpd.server_address
    url = f"http://{host}:{port}/"
    # flush=True is load-bearing: when this runs detached with stdout redirected to a log
    # (the debate-gui.sh launcher), stdout is block-buffered and serve_forever() below never
    # returns to flush it — so the launcher would never see the URL. Flush it out immediately.
    print(f"debate-view --gui: serving '{label}' at {url}  (loopback only; Ctrl-C to stop)",
          flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass  # headless / no browser — the URL above still works if opened manually
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()
    return 0


# ---------------------------------------------------------------------------
# The single-page front-end. All agent-authored text is inserted with textContent /
# createTextNode (never innerHTML), so transcript content cannot inject into the page.
# The roster mirrors the terminal viewer's columns (status/activity, role, model,
# independence mark, token heat + share, duration, cost) over the same debate_lib.snapshot()
# data; the timeline is rendered above it as a Steins;Gate-style world-lines SVG panel
# (each agent a glowing line branching off a shared spine at its spawn instant).
_SHELL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Angel's Advocate — debate view</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  /* hide scrollbars everywhere but keep scrolling (user preference: no visual scroll bar) */
  * { scrollbar-width: none; -ms-overflow-style: none; }
  *::-webkit-scrollbar { width:0; height:0; display:none; }
  body { margin:0; font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
         background:#0f1115; color:#d7dae0; }
  header { padding:10px 16px; border-bottom:1px solid #262a33; display:flex;
           gap:14px; align-items:baseline; flex-wrap:wrap; }
  header h1 { font-size:15px; margin:0; font-weight:600; }
  #arb { color:#8b93a1; font-size:12px; }
  #sess { font:inherit; font-size:12px; background:#161a21; color:#d7dae0;
          border:1px solid #2c3340; border-radius:4px; padding:3px 6px; max-width:46ch; }
  #sess:hover { border-color:#3d4757; }
  #span { color:#6f7787; font-size:12px; }
  #indep { font-size:12px; padding:2px 8px; border-radius:4px; background:#222; color:#9aa; }
  #indep.ok { background:#12341f; color:#7ee2a8; }
  #indep.collapse { background:#3a1720; color:#f2889b; }
  #indep.unverified, #indep.nothing-to-check { background:#33301a; color:#e0cf7a; }
  #err { color:#f2889b; font-size:12px; }
  main { display:flex; flex-direction:column; height:calc(100vh - 47px); }
  /* toolbar — a thin strip above the split; hosts the World Lines toggle + agent count.
     The world-lines timeline needs the FULL width, so it opens as a collapsible panel here
     (above the split) rather than living in one half of it. */
  .toolbar { display:flex; gap:10px; align-items:center; padding:6px 12px;
             border-bottom:1px solid #262a33; }
  .tbtn { font:inherit; font-size:12px; color:#8b93a1; background:#161a21;
          border:1px solid #2c3340; cursor:pointer; padding:5px 12px; border-radius:6px; }
  .tbtn:hover { color:#d7dae0; border-color:#3d4757; }
  .tbtn.on { color:#e7eaf0; border-color:#48d18c; background:#12251b; }
  .tb-count { color:#6f7787; font-size:12px; }
  /* running cost readout — amber (the nixie family, dimmed so it doesn't shout) and
     tabular-nums so the digits don't jitter as the totals tick up on every poll */
  .tb-spend { color:#c99a52; font-size:12px; font-variant-numeric:tabular-nums; }
  /* time-range filter. Same chrome as the session switcher (#sess) but it lives in the
     toolbar, not the header, because it narrows BOTH panes — rail and world lines — rather
     than changing which session you're looking at. Goes green when a filter is active, so a
     narrowed view never looks like the whole session. */
  #range { font:inherit; font-size:12px; background:#161a21; color:#d7dae0;
           border:1px solid #2c3340; border-radius:4px; padding:3px 6px; max-width:38ch; }
  #range:hover { border-color:#3d4757; }
  #range.on { border-color:#48d18c; color:#e7eaf0; background:#12251b; }
  /* keyboard help overlay ('?' toggles) — a small card over the whole page; the backdrop
     is clickable so it is never a trap for someone who opened it by accident */
  #help { position:fixed; inset:0; z-index:10; display:flex; align-items:center;
          justify-content:center; background:rgba(7,9,14,.7); cursor:pointer; }
  #help .card { background:#12151b; border:1px solid #2c3340; border-radius:8px;
                padding:14px 18px; font-size:12px; }
  #help h2 { margin:0 0 9px; font-size:9px; font-weight:600; color:#5c6675;
             letter-spacing:.28em; text-transform:uppercase; }
  #help dl { margin:0; display:grid; grid-template-columns:auto 1fr; gap:5px 18px; }
  #help dt { color:#ffb545; white-space:nowrap; }
  #help dd { margin:0; color:#8b93a1; }
  /* the world-lines panel: full width, collapsible, capped so it never eats the split */
  .wlpanel { max-height:48vh; overflow:auto; border-bottom:1px solid #262a33; flex:none; }
  /* the master–detail split: a compact agent RAIL on the left, the transcript on the right
     at full height (min-height:0 lets the inner panes actually scroll inside the flex row) */
  .split { display:flex; flex:1; min-height:0; }
  .rail { flex:0 0 236px; overflow:auto; border-right:1px solid #262a33; }
  .right { flex:1; display:flex; flex-direction:column; min-width:0; min-height:0; }
  /* one agent per rail row: status dot · role · independence mark · mini token-heat bar */
  .ra { display:flex; align-items:center; gap:8px; padding:6px 10px; cursor:pointer;
        border-bottom:1px solid #14171d; }
  .ra:hover { background:#161a21; }
  .ra.sel { background:#1d2530; }
  .ra .rrole { font-weight:600; flex:1; overflow:hidden; text-overflow:ellipsis;
               white-space:nowrap; }
  .ra .rbar { position:relative; flex:0 0 40px; height:8px; background:#161a21;
              border-radius:2px; overflow:hidden; }
  .ra .rbar > i { position:absolute; left:0; top:0; height:100%; min-width:2px; }
  /* the selected agent's metrics, as a one-line header above its transcript (these are the
     columns the old roster table carried; they move here so the rail stays narrow) */
  .stat { padding:8px 14px; border-bottom:1px solid #1b1f27; color:#8b93a1; font-size:12px;
          white-space:nowrap; overflow-x:auto; flex:none; }
  .stat .stat-role { color:#e7eaf0; font-weight:600; }
  .stat .sep { color:#3c4552; }
  .hidden { display:none !important; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  thead th { position:sticky; top:0; background:#12151b; color:#6f7787; font-weight:600;
             text-align:left; padding:6px 10px; border-bottom:1px solid #262a33;
             white-space:nowrap; }
  tbody td { padding:5px 10px; border-bottom:1px solid #1b1f27; white-space:nowrap;
             vertical-align:middle; }
  tbody tr { cursor:pointer; }
  tbody tr:hover { background:#161a21; }
  tbody tr.sel { background:#1d2530; }
  .role { font-weight:600; }
  .model { color:#8b93a1; }
  .ctr { text-align:center; font-variant-numeric:tabular-nums; }
  /* status dot + activity */
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px;
         background:#3a3f4a; vertical-align:middle; }
  .dot.active { background:#48d18c; box-shadow:0 0 0 0 rgba(72,209,140,.6);
                animation:pulse 1.4s infinite; }
  @keyframes pulse { 0%{box-shadow:0 0 0 0 rgba(72,209,140,.5);}
                     70%{box-shadow:0 0 0 6px rgba(72,209,140,0);}
                     100%{box-shadow:0 0 0 0 rgba(72,209,140,0);} }
  .act { color:#6f7787; }
  .act.active { color:#8fd6b4; }
  /* independence mark */
  .ind { font-weight:700; }
  .ind.ok { color:#7ee2a8; }
  .ind.collapse { color:#f2889b; }
  .ind.inherit, .ind.na { color:#5b6272; }
  .ind.unknown { color:#e0cf7a; }
  /* Steins;Gate-style world-lines timeline: each agent is a glowing world line that
     branches off a shared prime timeline at the moment it was spawned, then flows across
     time to where it ended. Active lines shimmer; the divergence meter apes the show's
     amber nixie readout. */
  #worldlines { background:radial-gradient(125% 115% at 12% 0%, #101a2b 0%, #07090e 74%); }
  /* CRT shell: scanlines + vignette over the whole panel, non-interactive */
  .crt { position:relative; padding-bottom:6px; }
  .crt::after { content:""; position:absolute; inset:0; pointer-events:none; z-index:3;
                background:repeating-linear-gradient(180deg, rgba(0,0,0,0) 0 2px,
                           rgba(0,0,0,.20) 2px 3px),
                           radial-gradient(120% 120% at 50% 40%, rgba(0,0,0,0) 55%,
                           rgba(0,0,0,.45) 100%); }
  /* divergence meter — nixie tubes */
  .meter { display:flex; gap:26px; align-items:flex-end; padding:12px 16px 8px;
           flex-wrap:wrap; position:relative; z-index:2; }
  .meter .grp { display:flex; flex-direction:column; gap:5px; }
  .meter .lab { color:#5c6675; font-size:9px; letter-spacing:.28em; text-transform:uppercase; }
  .nixie { display:flex; gap:3px; align-items:stretch; }
  /* Every tube is a flex box that CENTRES its glyph. Baseline alignment was the bug: the
     '.' of the divergence reading sat on the baseline near the tube's bottom edge and read
     as falling outside its box, while digits looked centred. Centring covers both. */
  /* FIXED width, not min-width: with min-width each tube sized to its own glyph, so a
     digit whose advance exceeded the minimum grew its box while the decimal tube (whose
     glyph is replaced by a 6px dot) stayed clamped at the minimum. Equal gaps between
     UNEQUAL boxes still read as an uneven run — worst on the dot's right edge. A fixed
     basis makes every tube identical regardless of what glyph it holds.
     NOTE: at fractional display scaling (Windows 125/150/175% => devicePixelRatio
     1.25/1.5/1.75) these CSS px sizes don't map to whole device pixels, so the browser
     rounds tube edges and gaps unevenly and one gap (often the decimal tube's right) ends
     up a pixel wider. That is corrected at runtime by snapNixies() below, which re-lays the
     tubes on an exact integer-device-pixel grid; the CSS here just defines the look. */
  .nixie .nx { position:relative; display:inline-flex; align-items:center;
               justify-content:center; flex:0 0 22px; width:22px; height:32px; padding:0;
               font-size:20px; line-height:1; color:#ffb545; border-radius:3px 3px 4px 4px;
               background:linear-gradient(#1c1408 0%, #0d0904 70%, #120c05 100%);
               border:1px solid #3d2c13; box-shadow:inset 0 0 9px rgba(255,140,30,.22);
               text-shadow:0 0 5px rgba(255,150,40,.95), 0 0 15px rgba(255,110,15,.6);
               animation:nxflicker 5s steps(1) infinite; }
  /* the ghost of the unlit cathodes stacked behind the lit digit — the nixie tell */
  .nixie .nx::before { content:"8"; position:absolute; inset:0; display:flex;
                       align-items:center; justify-content:center;
                       color:#ff8a2a; opacity:.075; pointer-events:none; }
  /* The decimal point gets a FULL-SIZE tube, identical to every digit tube — the readout's
     glass run stays perfectly even. A point-sized dot in a full-width tube leaves dead space
     either side, which is accepted here as the cost of uniform tube size. */
  .nixie .nx.dot { flex:0 0 22px; width:22px; font-size:0; }
  .nixie .nx.dot::before { content:""; }        /* no ghost cathode behind a decimal point */
  /* the neon dot itself: small, on the digits' baseline (bottom:9px — the 20px line box is
     centred in the 30px inner height, so the baseline lands ~9px up), dead-centre in its own
     tube. Even 6px width so left:50%/translateX(-50%) resolves to a whole-pixel centre. */
  .nixie .nx.dot::after { content:""; position:absolute; left:50%; bottom:9px;
                          transform:translateX(-50%);
                          width:6px; height:6px; border-radius:50%; background:currentColor;
                          box-shadow:0 0 5px rgba(255,150,40,.95), 0 0 11px rgba(255,110,15,.5); }
  .meter .small .nx.dot::after { width:4px; height:4px; bottom:7px; }
  @keyframes nxflicker { 0%,96%{opacity:1;} 97%{opacity:.78;} 98%{opacity:1;}
                         99%{opacity:.88;} 100%{opacity:1;} }
  .meter .small .nx { flex:0 0 16px; width:16px; height:25px; font-size:14px; }
  /* teal readouts for the non-divergence gauges (the show's UI accent) */
  .nixie.teal .nx { color:#5fe6d2; border-color:#123a37;
                    background:linear-gradient(#08201e 0%, #04100f 70%, #061715 100%);
                    box-shadow:inset 0 0 9px rgba(65,234,212,.16);
                    text-shadow:0 0 5px rgba(65,234,212,.85), 0 0 14px rgba(65,234,212,.45); }
  .nixie.teal .nx::before { color:#41ead4; }
  .fieldkeys { display:flex; gap:10px; align-items:center; }
  .fk { font-size:12px; letter-spacing:.08em; padding:2px 8px; border-radius:3px;
        border:1px solid currentColor; opacity:.9; }
  .lab-footer { position:relative; z-index:2; text-align:right; padding:0 16px 6px;
                color:#3c4552; font-size:9px; letter-spacing:.42em; text-transform:uppercase; }
  svg.wl { display:block; position:relative; z-index:1; }
  .wl-spine { stroke:#2c3a55; stroke-width:1; stroke-dasharray:2 5; }
  .wl-band { opacity:.5; }
  .wl-line { fill:none; stroke-width:2; filter:url(#wlglow); opacity:.85; cursor:pointer;
             transition:stroke-width .15s, opacity .15s; }
  .wl-line:hover { stroke-width:3.4; opacity:1; }
  .wl-line.sel   { stroke-width:3.4; opacity:1; }
  .wl-line.dim   { opacity:.28; }
  .wl-line.active { stroke-dasharray:7 6; animation:wlflow 1s linear infinite; }
  @keyframes wlflow { to { stroke-dashoffset:-13; } }
  .wl-node { fill:#ffb545; filter:url(#wlglow); }
  .wl-end  { filter:url(#wlglow); }
  .wl-end.active { animation:wlpulse 1.5s ease-in-out infinite; }
  @keyframes wlpulse { 0%,100%{ opacity:1; } 50%{ opacity:.35; } }
  .wl-label { font-size:11px; fill:#8b93a1; cursor:pointer; }
  .wl-label.sel { fill:#e7eaf0; }
  .wl-tick { stroke:#1e2635; stroke-width:1; }
  .wl-ticktext { font-size:9px; fill:#4c5565; letter-spacing:.1em; }
  /* token heat + share bar */
  .tok { display:flex; align-items:center; gap:8px; }
  .tok .bar { position:relative; width:70px; height:10px; background:#161a21;
              border-radius:2px; overflow:hidden; }
  .tok .bar > i { position:absolute; left:0; top:0; height:100%; min-width:2px; }
  .tok .n { color:#aeb4c0; font-variant-numeric:tabular-nums; min-width:44px;
            text-align:right; }
  #detail { flex:1; min-height:0; overflow:auto; padding:12px 18px; }
  .ev { margin:0 0 12px; white-space:pre-wrap; word-break:break-word; }
  .ev .k { display:block; font-size:11px; text-transform:uppercase; letter-spacing:.05em;
           margin-bottom:2px; color:#6f7787; }
  .ev.thinking .k { color:#8a7fb8; }
  .ev.text .k     { color:#7ea8d8; }
  .ev.tool_use .k { color:#d8a77e; }
  .ev.tool_result .k { color:#7eb89a; }
  .ev.prompt .k   { color:#a0a0a0; }
  .empty { color:#6f7787; padding:20px; }
</style>
</head>
<body>
<header>
  <h1>Angel's Advocate</h1>
  <select id="sess" title="Switch session — all debates recorded under this project"></select>
  <span id="arb"></span>
  <span id="span"></span>
  <span id="indep"></span>
  <span id="err"></span>
</header>
<main>
  <div class="toolbar">
    <button id="wl-toggle" class="tbtn">▸ World Lines</button>
    <select id="range"></select>
    <span id="tcount" class="tb-count"></span>
    <span id="tspend" class="tb-spend"></span>
  </div>
  <div id="worldlines" class="wlpanel hidden"><div class="empty">Waiting for world lines…</div></div>
  <div class="split">
    <aside id="rail" class="rail"><div class="empty">Waiting for agents…</div></aside>
    <section class="right">
      <div id="stat" class="stat"></div>
      <div id="detail"><div class="empty">Select an agent.</div></div>
    </section>
  </div>
</main>
<!-- keyboard help; static markup (no agent-authored text ever reaches it) -->
<div id="help" class="hidden">
  <div class="card">
    <h2>keys</h2>
    <dl>
      <dt>j / k&nbsp;&nbsp;·&nbsp;&nbsp;↓ / ↑</dt><dd>next / previous agent</dd>
      <dt>] / [</dt><dd>next / previous session</dd>
      <dt>f / F</dt><dd>next / previous time range (debate burst or window)</dd>
      <dt>w</dt><dd>toggle the World Lines panel</dd>
      <dt>?</dt><dd>show / hide this help</dd>
    </dl>
  </div>
</div>
<script>
@@JS@@</script>
</body>
</html>
"""

# The page's JavaScript lives in a real sibling .js file (editable, lintable, and reported
# at true file:line by `node --check`) and is spliced into the shell above at import time.
# Deliberately NOT served over its own /app.js route: a second route would make GET / emit
# a 1.6KB stub, and every failure mode of that split — typo'd src, wrong route, wrong
# content type — is silent in the browser. Splicing keeps ONE served document, so the
# route table, the loopback-only posture, and debate_lib_test.sh's `innerHTML not in page`
# guard all keep working on the real payload, unchanged. Byte-identity with the previously
# embedded page is asserted by tools/tests/gui_test.sh.
#
# Failure is loud on purpose: a missing placeholder or a missing/untracked asset raises at
# IMPORT time with the filename, rather than serving a half-built page. install.sh copies
# the working tree, so an un-`git add`ed asset would ship a broken clone — gui_test.sh
# asserts each asset is tracked by git to catch exactly that.
_ASSET_DIR = os.path.dirname(os.path.abspath(__file__))
_ASSETS = (("@@JS@@", "debate_gui.js"),)


def _splice(shell: str) -> str:
    """Inline each sidecar asset into the HTML shell. Raises rather than degrading."""
    for token, name in _ASSETS:
        if token not in shell:
            raise RuntimeError("asset placeholder %s missing from the HTML shell" % token)
        with open(os.path.join(_ASSET_DIR, name), encoding="utf-8") as fh:
            shell = shell.replace(token, fh.read(), 1)
    return shell


PAGE = _splice(_SHELL)


if __name__ == "__main__":
    # Standalone launch is uncommon (debate-view.sh --gui is the intended entry), but
    # honor a direct --subagents pointer so the module is runnable/testable on its own.
    import argparse
    ap = argparse.ArgumentParser(description="Browser view for Angel's Advocate agents.")
    ap.add_argument("--subagents", required=True, help="path to a subagents/ dir")
    ap.add_argument("--arbiter-model", default=None)
    ap.add_argument("--no-browser", action="store_true", help="don't auto-open a browser")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="0 = ephemeral")
    ap.add_argument("--project", default=None, help="project dir (enables session switching)")
    a = ap.parse_args()
    label = os.path.basename(os.path.dirname(a.subagents.rstrip("/")))
    sys.exit(main(a.subagents, label, a.arbiter_model, open_browser=not a.no_browser,
                  port=a.port, project_dir=a.project))
