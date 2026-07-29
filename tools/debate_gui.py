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
  * Serves exactly two FIXED routes (/ and /snapshot.json). The request path is only ever
    compared for equality, never used to build a filesystem path — so there is no
    path-traversal surface and no arbitrary-file read.
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
PAGE = r"""<!doctype html>
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
    <span id="tcount" class="tb-count"></span>
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
<script>
let snap = null;
let sel = null;   // selected agent id (a.id = agent-<uuid>.jsonl, unique per agent)
let showWL = false;   // whether the full-width World Lines panel is open above the split
let sessionId = null; // selected session; null = whatever the server was launched with
let sessions = [];
let _lastDetailSel = null;   // which agent the transcript last showed (to preserve scroll)
let _lastRenderSig = null;   // signature of the last-rendered data; skip rebuild when unchanged

function key(a){ return a.id; }   // role+model would conflate two agents sharing both

// Disambiguate repeated roles: "angel #1", "angel #2" when a role appears more than once,
// so two agents that share a role (and maybe a model) are told apart in the roster.
function roleLabels(agents){
  const counts = {}, seen = {}, out = {};
  for (const a of agents) counts[a.role] = (counts[a.role] || 0) + 1;
  for (const a of agents){
    seen[a.role] = (seen[a.role] || 0) + 1;
    out[a.id] = counts[a.role] > 1 ? a.role + " #" + seen[a.role] : a.role;
  }
  return out;
}

const ROLE_EMOJI = { angel:"😇", devil:"😈", arbiter:"⚖️", verifier:"✅", researcher:"🔎",
  interpreter:"🧭", profiler:"📊", historian:"📚", scribe:"📝", "test-writer":"🧪", builder:"🔨" };
// mirrors debate_lib.IND_MARKS — glyph + tooltip per independence state
const IND = { ok:["√","independent — different model family than the arbiter"],
  collapse:["×","COLLAPSED — same model family as the arbiter; independence lost"],
  inherit:["~","inherits the arbiter's model by design"],
  unknown:["?","cross-model role but the arbiter's model is undetermined"],
  "n/a":["·",""] };
// heat palette, coldest→hottest (mirrors the terminal's HEAT buckets)
const HEAT = ["#3b6ea5","#4a9d9c","#c9b458","#d8934a","#d85a5a"];

function usageTotal(u){
  if (!u) return 0;
  return (u.input||0) + (u.output||0) + (u.cache_read||0) + (u.cache_create||0);
}
function fmtCost(c){ return c == null ? "—" : "$" + c.toFixed(c < 1 ? 4 : 2); }
function fmtDur(s){
  if (s == null) return "—";
  s = Math.floor(s);
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s/60) + "m" + String(s%60).padStart(2,"0") + "s";
  return Math.floor(s/3600) + "h" + String(Math.floor(s%3600/60)).padStart(2,"0") + "m";
}
function fmtTok(t){ return t >= 1000 ? (t/1000).toFixed(1) + "k" : "" + t; }
function stripModel(m){ return m ? m.replace(/^claude-/, "") : "(unknown)"; }

// Global timeline window across all agents; running bars extend to the latest activity.
function tlWindow(agents){
  let t0 = Infinity, t1 = -Infinity;
  for (const a of agents){
    if (a.start != null){ t0 = Math.min(t0, a.start); t1 = Math.max(t1, a.start); }
    if (a.end   != null){ t1 = Math.max(t1, a.end); }
  }
  if (!isFinite(t0) || !isFinite(t1)) return null;
  return { t0, t1, span: Math.max(t1 - t0, 0.001) };
}

function cell(tag, cls, txt){
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (txt != null) el.textContent = txt;
  return el;
}

// The left RAIL: one compact row per agent (status dot · role · independence mark · a mini
// token-heat bar). Kept deliberately narrow so the transcript gets the width; the precise
// metrics (model/tokens/took/cost) live in the stat header of the right pane, see renderStat.
function renderRail(){
  const host = document.getElementById("rail");
  // The 1.2s poll rebuilds this list from scratch; wiping the children resets the rail's
  // scroll to the top, so a user reading a lower row gets yanked up every tick. Save the
  // scroll offset and restore it after the rebuild (same idea as the detail pane's follow).
  const prevScroll = host.scrollTop;
  host.textContent = "";
  if (!snap || !snap.agents.length){
    host.appendChild(cell("div", "empty", "No agents yet."));
    return;
  }
  const labels = roleLabels(snap.agents);
  for (const a of snap.agents){
    const row = cell("div", "ra" + (key(a) === sel ? " sel" : ""));

    const dot = cell("span", "dot" + (a.status === "active" ? " active" : ""));
    dot.title = a.status === "active" ? (a.activity || "active") : "done";
    row.appendChild(dot);

    const emoji = ROLE_EMOJI[a.role] ? ROLE_EMOJI[a.role] + " " : "";
    row.appendChild(cell("span", "rrole", emoji + labels[a.id]));

    const im = IND[a.indep] || IND["n/a"];
    const ind = cell("span", "ind " + (a.indep || "na").replace("/", ""), im[0]);
    ind.title = im[1];
    row.appendChild(ind);

    // mini token-heat bar (bucket + share come from snapshot(), same as the old table)
    const total = usageTotal(a.usage);
    const bucket = Math.min(HEAT.length - 1, a.heat || 0);
    const bar = cell("div", "rbar");
    const fill = cell("i");
    fill.style.width = Math.max((a.tok_share || 0) * 100, total > 0 ? 8 : 0) + "%";
    fill.style.background = HEAT[bucket];
    bar.appendChild(fill);
    bar.title = fmtTok(total) + " tokens";
    row.appendChild(bar);

    row.onclick = () => { sel = key(a); render(); };
    host.appendChild(row);
  }
  host.scrollTop = prevScroll;   // restore the reader's position after the rebuild
}

// The stat header above the transcript: the metric columns the old roster table carried,
// for the SELECTED agent only, as a single scannable line (role · model · ind · tokens ·
// took · cost · status).
function renderStat(){
  const el = document.getElementById("stat");
  const prevLeft = el.scrollLeft;   // preserve horizontal scroll across the poll rebuild
  el.textContent = "";
  const a = snap && snap.agents.find(x => key(x) === sel);
  if (!a) return;
  const labels = roleLabels(snap.agents);
  const emoji = ROLE_EMOJI[a.role] ? ROLE_EMOJI[a.role] + " " : "";
  el.appendChild(cell("span", "stat-role", emoji + labels[a.id]));
  const im = IND[a.indep] || IND["n/a"];
  const bits = [
    stripModel(a.model),
    im[0] + " " + (a.indep || "n/a"),
    fmtTok(usageTotal(a.usage)) + " tok",
    fmtDur(a.duration_sec),
    fmtCost(a.cost),
    a.status === "active" ? (a.activity || "active") : "done",
  ];
  for (const b of bits){
    el.appendChild(cell("span", "sep", "  ·  "));
    el.appendChild(cell("span", null, b));
  }
  el.scrollLeft = prevLeft;
}

// --- Steins;Gate-style world lines ------------------------------------------
// Attractor fields are MODEL FAMILIES (from snapshot.attractor_fields, the Arbiter's own
// first). That is the show's conceit doing real work: every genuinely cross-model check
// diverges into a different field, so independence is something you can see at a glance.
const SVGNS = "http://www.w3.org/2000/svg";
const FIELD_GREEK  = ["α","β","γ","δ","ε","ζ"];
const FIELD_COLORS = ["#ffb545","#41ead4","#b18cff","#ff7ec7","#8ce99a","#ff9f6b"];

function svgEl(tag, attrs){
  const el = document.createElementNS(SVGNS, tag);
  if (attrs) for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}

// A nixie-tube readout: one glass tube per character. Text only ever set via textContent.
function nixie(text, cls){
  const wrap = cell("div", "nixie" + (cls ? " " + cls : ""));
  // Every character gets its own tube — separators included. The separator tube is the
  // SAME width as a digit tube (see .nx.dot in the CSS) so the glass run stays perfectly
  // even; the decimal point is a small dot centred in that tube. snapNixies() later re-lays
  // these on an exact device-pixel grid so the spacing is even at fractional DPR too.
  for (const ch of String(text))
    wrap.appendChild(cell("span", "nx" + (ch === "." || ch === ":" ? " dot" : ""), ch));
  return wrap;
}

// Snap every nixie readout onto the physical device-pixel grid.
//
// Why this exists: the tubes are equal-width flex items separated by a flex `gap`, but at
// the FRACTIONAL display scales Windows uses (125% / 150% / 175% => devicePixelRatio
// 1.25 / 1.5 / 1.75) neither the tube width nor the gap is a whole number of device pixels
// (e.g. gap 3px * 1.25 = 3.75). The browser then rounds each tube edge and each gap to a
// whole device pixel INDEPENDENTLY, and the leftover fraction accumulates unevenly across
// the row — so one gap (often the one on the decimal-point tube's right) ends up a pixel
// wider than the rest. It is invisible at 100%/200% (integer DPR), which is why it only
// shows up on some machines. Pure CSS cannot fix this: something always absorbs the
// fractional remainder.
//
// The fix is to lay the tubes out ourselves on an exact integer-device grid: (1) cancel
// the container's own sub-pixel offset so its left edge sits on a device line, then
// (2) absolutely position each tube at a device-pixel-rounded multiple of the pitch. Tube
// width and gap are read from the live computed style, so this works for both the normal
// and the .small readouts without hard-coding sizes. Re-run whenever the readouts are
// rebuilt or the DPR changes.
function snapNixies(root){
  const dpr = window.devicePixelRatio || 1;
  const snap = v => Math.round(v * dpr) / dpr;
  const host = root || document;
  for (const nx of host.querySelectorAll(".nixie")){
    const kids = [...nx.children];
    if (!kids.length) continue;
    // reset any prior snapping so we measure the natural flex layout first
    nx.style.transform = ""; nx.style.position = ""; nx.style.display = "";
    nx.style.width = ""; nx.style.height = "";
    for (const el of kids){
      el.style.position = ""; el.style.left = ""; el.style.top = "";
      el.style.width = ""; el.style.flex = "";
    }
    const cs = getComputedStyle(nx);
    const gap = parseFloat(cs.columnGap || cs.gap) || 0;
    const first = kids[0].getBoundingClientRect();
    const w = first.width, h = first.height;
    if (!w) continue;   // not laid out yet (e.g. panel hidden) — skip
    const pitch = snap(w) + snap(gap);
    // (1) cancel the container's sub-pixel device offset so its left edge is on a grid line
    const originDev = nx.getBoundingClientRect().left * dpr;
    nx.style.transform = "translateX(" + (-(originDev - Math.round(originDev)) / dpr) + "px)";
    // (2) place tubes on an exact integer-device grid
    nx.style.position = "relative"; nx.style.display = "block";
    nx.style.height = snap(h) + "px";
    nx.style.width = snap(pitch * (kids.length - 1) + w) + "px";
    kids.forEach((el, i) => {
      el.style.position = "absolute"; el.style.left = snap(i * pitch) + "px";
      el.style.top = "0px"; el.style.width = snap(w) + "px"; el.style.flex = "none";
    });
  }
}

// A world line: branch DOWN off the prime spine at its spawn instant, then flow (with a
// gentle sine wave) across time to where it ended. Pure geometry from start/end — no
// agent-authored text ever reaches the SVG.
function worldPath(xs, xe, ly, spineY, phase, amp = 3.2){
  const midY = (spineY + ly) / 2;
  let d = "M " + xs + " " + spineY + " C " + xs + " " + midY + ", " +
          xs + " " + midY + ", " + xs + " " + ly;          // branch off the spine
  const wl = 46, w = Math.max(xe - xs, 0);
  const n = Math.max(1, Math.round(w / 8));
  for (let k = 1; k <= n; k++){
    const x = xs + w * k / n;
    const y = ly + amp * Math.sin(phase + (x - xs) / wl * 2 * Math.PI);
    d += " L " + x.toFixed(1) + " " + y.toFixed(1);
  }
  return d;
}

function renderWorldLines(){
  const host = document.getElementById("worldlines");
  const prevScroll = host.scrollTop;   // preserve scroll across the 1.2s poll rebuild
  host.textContent = "";
  if (!snap || !snap.agents.length){
    host.appendChild(cell("div", "empty", "No world lines yet."));
    return;
  }
  const W = Math.max(host.clientWidth || 900, 520);
  const agents = snap.agents, labels = roleLabels(agents), win = tlWindow(agents);
  const fields = snap.attractor_fields || [];
  const fidx = f => { const i = fields.indexOf(f); return i < 0 ? 98 : i; };
  const colorOf = a => FIELD_COLORS[fidx(a.family) % FIELD_COLORS.length];
  // lanes grouped by attractor field (Arbiter's field first), then by spawn time
  const ordered = agents.slice().sort((a, b) =>
    (fidx(a.family) - fidx(b.family)) || ((a.start || 0) - (b.start || 0)));

  const crt = cell("div", "crt");

  // --- divergence meter -----------------------------------------------------
  const meter = cell("div", "meter");
  const grp = (lab, node, tip) => {
    const g = cell("div", "grp");
    g.appendChild(cell("span", "lab", lab));
    g.appendChild(node);
    if (tip) g.title = tip;
    return g;
  };
  const nActive = agents.filter(a => a.status === "active").length;
  meter.appendChild(grp("divergence", nixie((snap.divergence || 0).toFixed(6)),
    "REAL measurement, not a prop: the fraction of this debate's agents running on a model "
    + "family other than the Arbiter's (" + (snap.arbiter_model || "unknown") + "). "
    + "1.000000 would mean every agent diverged; 0.000000 means none did."));
  meter.appendChild(grp("world lines",
    nixie(String(agents.length).padStart(2, "0"), "teal small")));
  meter.appendChild(grp("live", nixie(String(nActive).padStart(2, "0"), "teal small")));
  if (win) meter.appendChild(grp("observed span", nixie(fmtDur(win.span), "teal small")));
  const fk = cell("div", "fieldkeys");
  fields.forEach((f, i) => {
    const s = cell("span", "fk", FIELD_GREEK[i % FIELD_GREEK.length] + " " + f);
    s.style.color = FIELD_COLORS[i % FIELD_COLORS.length];
    fk.appendChild(s);
  });
  if (fields.length)
    meter.appendChild(grp("attractor fields", fk,
      "One field per model family; the Arbiter's own field is α. Cross-model roles "
      + "(devil/verifier/red-teamer/interpreter) SHOULD sit in a divergent field — "
      + "that is what their independence looks like."));
  crt.appendChild(meter);
  host.appendChild(crt);   // attach now so meter.offsetHeight is measurable for the fit math

  // --- the world-line plot --------------------------------------------------
  // Adaptive lane spacing: compress the lanes so the WHOLE plot fits the panel's visible
  // height rather than scrolling forever when there are many agents. laneGap shrinks from a
  // comfortable default toward a legible floor, proportional to the agent count; the wave
  // amplitude, node radii and label size scale down with it so compressed lanes don't collide.
  const padL = 178, padR = 30, spineY = 18, topPad = 40, axisH = 28;
  const DEF_GAP = 27, MIN_GAP = 13;
  const maxPanelPx = Math.max(220, Math.round(window.innerHeight * 0.48));  // matches .wlpanel 48vh
  const plotBudget = Math.max(120, maxPanelPx - (meter.offsetHeight || 96) - topPad - axisH - 26);
  const laneGap = Math.max(MIN_GAP,
                           Math.min(DEF_GAP, Math.floor(plotBudget / Math.max(1, ordered.length))));
  const compressed = laneGap < 20;
  const labFont = compressed ? 9 : 11;
  const amp = Math.max(1, Math.min(3.2, laneGap * 0.16));
  const nodeR = compressed ? 2.1 : 2.6, endR = compressed ? 2.2 : 2.8, endActR = compressed ? 3.0 : 3.6;
  const H = topPad + ordered.length * laneGap + axisH;
  const x0 = padL, x1 = W - padR;
  const tx = t => win ? x0 + (t - win.t0) / win.span * (x1 - x0) : x0;
  const laneY = i => topPad + i * laneGap + 8;

  const svg = svgEl("svg", { class: "wl", width: W, height: H,
                             viewBox: "0 0 " + W + " " + H });

  // neon glow filter, defined once
  const defs = svgEl("defs");
  const f = svgEl("filter", { id: "wlglow", x: "-40%", y: "-40%",
                              width: "180%", height: "180%" });
  f.appendChild(svgEl("feGaussianBlur", { stdDeviation: "2.4", result: "b" }));
  const merge = svgEl("feMerge");
  merge.appendChild(svgEl("feMergeNode", { in: "b" }));
  merge.appendChild(svgEl("feMergeNode", { in: "SourceGraphic" }));
  f.appendChild(merge); defs.appendChild(f); svg.appendChild(defs);

  // attractor-field bands: one tinted block per run of same-field lanes
  let i0 = 0;
  while (i0 < ordered.length){
    let i1 = i0;
    while (i1 + 1 < ordered.length && ordered[i1 + 1].family === ordered[i0].family) i1++;
    const fi = fidx(ordered[i0].family), color = FIELD_COLORS[fi % FIELD_COLORS.length];
    const yTop = laneY(i0) - laneGap / 2 - 2, yBot = laneY(i1) + laneGap / 2 - 2;
    // Tinted band only. The α/β + family text used to sit in a left gutter, but at this
    // font size it overflowed the plot's left edge and rendered clipped — and it was
    // redundant with the colour-matched attractor-field legend in the meter above.
    svg.appendChild(svgEl("rect", { class: "wl-band", x: 2, y: yTop, width: W - 4,
                                    height: Math.max(yBot - yTop, laneGap),
                                    rx: 4, fill: color, "fill-opacity": .045 }));
    i0 = i1 + 1;
  }

  // the faint prime timeline the world lines branch off
  svg.appendChild(svgEl("line", { class: "wl-spine", x1: x0, y1: spineY, x2: x1, y2: spineY }));

  ordered.forEach((a, i) => {
    const color = colorOf(a), ly = laneY(i), active = a.status === "active";
    const xs = a.start != null ? tx(a.start) : x0;
    const xe = a.end != null ? tx(a.end) : (active ? x1 : xs);

    const cls = "wl-line" + (active ? " active" : "") +
                (sel != null ? (key(a) === sel ? " sel" : " dim") : "");
    const path = svgEl("path", { class: cls, stroke: color,
                                 d: worldPath(xs, xe, ly, spineY, i * 0.9, amp) });
    const t = svgEl("title");
    t.textContent = labels[a.id] + " · " + (a.family || "?") + " · " + fmtDur(a.duration_sec);
    path.appendChild(t);
    path.addEventListener("click", () => { sel = key(a); render(); });
    svg.appendChild(path);

    // branch node on the spine + end node on the lane
    svg.appendChild(svgEl("circle", { class: "wl-node", cx: xs, cy: spineY, r: nodeR }));
    svg.appendChild(svgEl("circle", { class: "wl-end" + (active ? " active" : ""),
                                      cx: xe, cy: ly, r: active ? endActR : endR, fill: color }));

    // lane label in the left gutter (role #n) — clicking it selects too
    const lab = svgEl("text", { class: "wl-label" + (key(a) === sel ? " sel" : ""),
                                x: x0 - 12, y: ly + 4, "text-anchor": "end", "font-size": labFont });
    const emoji = ROLE_EMOJI[a.role] ? ROLE_EMOJI[a.role] + " " : "";
    lab.textContent = emoji + labels[a.id];
    lab.addEventListener("click", () => { sel = key(a); render(); });
    svg.appendChild(lab);
  });

  // time axis along the bottom (elapsed from the first spawn)
  const ay = H - axisH + 12;
  svg.appendChild(svgEl("line", { class: "wl-tick", x1: x0, y1: ay - 6, x2: x1, y2: ay - 6 }));
  for (let k = 0; k <= 4; k++){
    const x = x0 + (x1 - x0) * k / 4;
    svg.appendChild(svgEl("line", { class: "wl-tick", x1: x, y1: ay - 9, x2: x, y2: ay - 3 }));
    const tt = svgEl("text", { class: "wl-ticktext", x: x, y: ay + 8,
                               "text-anchor": k === 0 ? "start" : (k === 4 ? "end" : "middle") });
    tt.textContent = win ? "+" + fmtDur(win.span * k / 4) : "";
    svg.appendChild(tt);
  }

  crt.appendChild(svg);
  crt.appendChild(cell("div", "lab-footer", "El Psy Kongroo"));
  host.scrollTop = prevScroll;   // restore after the rebuild (crt is already attached above)
}

function renderDetail(){
  const el = document.getElementById("detail");
  el.textContent = "";
  const a = snap && snap.agents.find(x => key(x) === sel);
  if (!a){
    el.appendChild(cell("div", "empty", "Select an agent."));
    return;
  }
  if (!a.events.length){
    el.appendChild(cell("div", "empty", "No output yet."));
    return;
  }
  for (const e of a.events){
    const div = document.createElement("div");
    div.className = "ev " + (e.kind || "");
    div.appendChild(cell("span", "k", e.kind + (e.name ? " · " + e.name : "")));
    let body = e.text;
    if (e.kind === "tool_use") body = JSON.stringify(e.input, null, 2);
    div.appendChild(document.createTextNode(body == null ? "" : body));  // text node = XSS-safe
    el.appendChild(div);
  }
}

function renderSessions(){
  const sel = document.getElementById("sess");
  const cur = (snap && snap.label) || sessionId;
  // rebuild only when the option set actually changes, so the dropdown stays usable
  const sig = sessions.map(s => s.id + ":" + s.agents).join("|") + "#" + cur;
  if (sel.dataset.sig === sig) return;
  sel.dataset.sig = sig;
  sel.textContent = "";
  for (const s of sessions){
    const o = document.createElement("option");
    o.value = s.id;
    o.textContent = s.id.slice(0, 8) + "…  (" + s.agents + " agent" +
                    (s.agents === 1 ? "" : "s") + ")";
    if (s.id === cur) o.selected = true;
    sel.appendChild(o);
  }
}

// A cheap signature of everything the rail + transcript actually render. If it's unchanged
// between polls, render() skips the DOM rebuild entirely and the user's scroll is left alone —
// this is the real fix for "scroll resets every tick" (and it means a finished debate, whose
// data never changes again, is never rebuilt at all while you read it).
function renderSig(){
  if (!snap || !snap.agents) return "none";
  const a = snap.agents.find(x => key(x) === sel);
  const rows = snap.agents.map(x =>
    x.id + ":" + x.status + ":" + (x.activity || "") + ":" + usageTotal(x.usage) +
    ":" + (x.duration_sec || 0) + ":" + (x.heat || 0) + ":" + (x.tok_share || 0)).join("|");
  return rows + "##sel=" + sel + "##ev=" + (a ? a.events.length : -1) +
         "##wl=" + showWL + "##ind=" + (snap.independence ? snap.independence.status : "") +
         "##lbl=" + (snap.label || "");
}

function render(){
  renderSessions();
  document.getElementById("arb").textContent =
    (snap && snap.arbiter_model) ? "arbiter: " + snap.arbiter_model : "";

  // timeline span caption (matches the terminal's "N agents over <span>")
  const spanEl = document.getElementById("span");
  if (snap && snap.agents.length){
    const win = tlWindow(snap.agents);
    const n = snap.agents.length;
    spanEl.textContent = win
      ? n + " agent" + (n === 1 ? "" : "s") + " over " + fmtDur(win.span)
      : n + " agent" + (n === 1 ? "" : "s");
  } else spanEl.textContent = "";

  const ind = document.getElementById("indep");
  if (snap && snap.independence){
    const s = snap.independence.status;
    ind.className = s;
    const map = { ok: "independence held", collapse: "INDEPENDENCE COLLAPSED",
      unverified: "independence unverified", "nothing-to-check": "no cross-model roles" };
    ind.textContent = map[s] || s;
  } else { ind.textContent = ""; ind.className = ""; }

  // toolbar: World Lines toggle state + a live agent count
  const n = snap ? snap.agents.length : 0;
  const wlPanel = document.getElementById("worldlines");
  wlPanel.classList.toggle("hidden", !showWL);   // hidden => clientWidth 0, so skip its render
  const wlBtn = document.getElementById("wl-toggle");
  wlBtn.classList.toggle("on", showWL);
  wlBtn.textContent = (showWL ? "▾ " : "▸ ") + "World Lines";
  document.getElementById("tcount").textContent =
    n ? n + " agent" + (n === 1 ? "" : "s") : "";

  // Change-guard: only rebuild the scrollable panes (rail + transcript) when their data actually
  // changed. On an unchanged poll — the common case, and EVERY poll once a debate has finished —
  // we return here, leaving their DOM and the user's scroll position completely untouched. The
  // header/toolbar bits above are cheap and don't scroll, so they refresh every tick regardless.
  const sig = renderSig();
  if (sig === _lastRenderSig) return;
  _lastRenderSig = sig;

  const dt = document.getElementById("detail");
  // Preserve the transcript's scroll across the 1.2s poll rebuild. Three cases: a NEW agent
  // was selected -> start at the top; you were parked at the bottom -> keep following new
  // output; you were reading mid-transcript -> stay exactly where you were.
  const sameSel = (sel === _lastDetailSel);
  const prevDetail = dt.scrollTop;
  const atBottom = sameSel && (dt.scrollHeight - dt.scrollTop - dt.clientHeight < 40);
  renderRail();
  renderStat();
  // render the world-lines SVG only when the panel is visible: it measures clientWidth, and
  // a hidden container reports 0.
  if (showWL){ renderWorldLines(); snapNixies(); }
  renderDetail();
  if (!sameSel) dt.scrollTop = 0;
  else if (atBottom) dt.scrollTop = dt.scrollHeight;
  else dt.scrollTop = prevDetail;
  _lastDetailSel = sel;
}

// the world-lines SVG is pixel-mapped to its container, so refit it on resize; this also
// re-runs snapNixies() (via render()) so the nixie grid re-aligns to the new width. Moving
// the window between monitors of different scaling changes devicePixelRatio WITHOUT always
// firing resize, so watch DPR explicitly too and re-render when it changes.
let _rz, _lastDpr = window.devicePixelRatio || 1;
window.addEventListener("resize", () => {
  clearTimeout(_rz);
  _rz = setTimeout(() => {
    _lastDpr = window.devicePixelRatio || 1;
    // geometry changed, not data — force past the change-guard so the SVG refits to the new width
    if (snap && showWL){ _lastRenderSig = null; render(); }
  }, 120);
});
setInterval(() => {
  const d = window.devicePixelRatio || 1;
  if (d !== _lastDpr){ _lastDpr = d; if (snap && showWL){ _lastRenderSig = null; render(); } }
}, 600);

async function poll(){
  try {
    const q = sessionId ? "?session=" + encodeURIComponent(sessionId) : "";
    const [snapRes, sessRes] = await Promise.all([
      fetch("/snapshot.json" + q, { cache: "no-store" }),
      fetch("/sessions.json", { cache: "no-store" })
    ]);
    if (!snapRes.ok) throw new Error("snapshot " + snapRes.status);
    snap = await snapRes.json();
    sessions = (await sessRes.json()).sessions || [];
    document.getElementById("err").textContent = "";
    if (sel === null && snap.agents.length) sel = key(snap.agents[0]);
    render();
  } catch (e) {
    document.getElementById("err").textContent = "disconnected — is the viewer still running?";
  }
}

document.getElementById("sess").addEventListener("change", ev => {
  sessionId = ev.target.value;
  sel = null; snap = null; _lastRenderSig = null;   // different session => old selection/render are meaningless
  poll();
});
document.getElementById("wl-toggle").addEventListener("click", () => {
  showWL = !showWL; render();
});
poll();
setInterval(poll, 1200);
</script>
</body>
</html>
"""


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
