let snap = null;
let sel = null;   // selected agent id (a.id = agent-<uuid>.jsonl, unique per agent)
let showWL = false;   // whether the full-width World Lines panel is open above the split
let showHelp = false; // whether the keyboard-help overlay is up ('?')
let range = "all";    // time-range filter id: "all" | "1h"|"6h"|"24h" | "burst:<n>"
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

// Debate-wide running totals for the toolbar readout: tokens and indicative USD across every
// agent in the snapshot. It sums the SAME per-agent numbers renderStat() shows (a.usage and
// a.cost, priced server-side by debate_lib.estimate_cost) rather than re-deriving them, so the
// toolkit keeps exactly one pricing table. Agents whose model family has no price entry come
// back with cost == null; they are counted separately so the readout can say the total is a
// floor instead of quietly under-reporting.
function debateTotals(agents){
  let tok = 0, cost = 0, priced = 0, unpriced = 0;
  for (const a of agents){
    tok += usageTotal(a.usage);
    if (a.cost == null) unpriced++; else { cost += a.cost; priced++; }
  }
  return { tok, cost: priced ? cost : null, unpriced };
}
function fmtDur(s){
  if (s == null) return "—";
  s = Math.floor(s);
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s/60) + "m" + String(s%60).padStart(2,"0") + "s";
  return Math.floor(s/3600) + "h" + String(Math.floor(s%3600/60)).padStart(2,"0") + "m";
}
function fmtTok(t){ return t >= 1000 ? (t/1000).toFixed(1) + "k" : "" + t; }
function stripModel(m){ return m ? m.replace(/^claude-/, "") : "(unknown)"; }

// --- time-range filter --------------------------------------------------------
// A long-lived session accumulates many debates hours or days apart, and the world-line plot
// maps the whole span onto one axis — so a 4-minute debate inside a 46-hour session is a
// hairline against two days of idle gap. This narrows BOTH panes (rail + world lines) to a
// slice of time. Two kinds of slice:
//   * BURSTS — clusters of activity separated by a long idle gap. This is the one that
//     actually fixes the width problem, because it removes the gaps rather than scaling them.
//   * WINDOWS — plain "last 1h / 6h / 24h", as a coarse fallback across several bursts.
//
// Windows are anchored to the session's LATEST ACTIVITY, not to wall-clock now. Anchoring to
// now would show an empty view for any session you are browsing after the fact — which is most
// of them. The <option> text says "of activity" so the anchor isn't a surprise.
const BURST_GAP = 20 * 60;   // idle seconds that separate one debate from the next

function aStart(a){ return a.start != null ? a.start : (a.end != null ? a.end : 0); }
function aEnd(a){ return a.end != null ? a.end : (a.start != null ? a.start : 0); }

// Cluster agents into debates by idle gap. Returns oldest-first; each burst carries its own
// [t0,t1] and members. Overlapping/nested agents extend the running end, so a long-running
// parent never splits away from the children it spawned.
function bursts(agents){
  const sorted = agents.slice().sort((a, b) => aStart(a) - aStart(b));
  const out = [];
  for (const a of sorted){
    const last = out[out.length - 1];
    if (last && aStart(a) - last.t1 <= BURST_GAP){
      last.items.push(a);
      last.t1 = Math.max(last.t1, aEnd(a));
    } else {
      out.push({ items: [a], t0: aStart(a), t1: aEnd(a) });
    }
  }
  return out;
}

function fmtAgo(sec){
  if (sec < 90) return "just now";
  if (sec < 3600) return Math.round(sec / 60) + "m ago";
  if (sec < 86400) return Math.round(sec / 3600) + "h ago";
  return Math.round(sec / 86400) + "d ago";
}

// The dropdown's contents, newest-first: every detected burst, then the fixed windows, then
// "All time". Each entry knows how to test an agent, so viewAgents() stays a one-liner.
// Memoized on the agent set's shape because viewAgents() (and therefore this) is called
// several times per render, and the burst walk sorts.
let _roCache = null, _roKey = "";
function rangeOptions(agents){
  // the minute bucket is in the key so the "…m ago" labels don't freeze at page-load time on a
  // finished session; it costs one recompute a minute, and renderRanges() won't rebuild the
  // <select> unless the resulting text actually changed
  const key = agents.length + "@" + Math.floor(Date.now() / 60000) + ":" +
              agents.map(a => aStart(a) + "/" + aEnd(a)).join(",");
  if (_roKey === key && _roCache) return _roCache;
  const opts = [];
  if (!agents.length){
    _roKey = key;
    return (_roCache = [{ id: "all", label: "All time", group: "", test: () => true, n: 0 }]);
  }
  const bs = bursts(agents);
  const now = Date.now() / 1000;
  bs.slice().reverse().forEach((b, i) => {
    const idx = bs.length - i;   // chronological number: #1 is the oldest
    opts.push({
      id: "burst:" + idx,
      label: (i === 0 ? "Latest debate" : "Debate #" + idx) +
             "  ·  " + b.items.length + " agent" + (b.items.length === 1 ? "" : "s") +
             "  ·  " + fmtAgo(Math.max(0, now - b.t1)),
      group: "debates",
      n: b.items.length,
      // inclusive on both ends: a burst's own boundary agents must fall inside it
      test: a => aEnd(a) >= b.t0 && aStart(a) <= b.t1,
    });
  });
  const anchor = agents.reduce((m, a) => Math.max(m, aEnd(a)), -Infinity);
  for (const [h, lab] of [[1, "Last 1 hour"], [6, "Last 6 hours"], [24, "Last 24 hours"]]){
    const cut = anchor - h * 3600;
    const n = agents.filter(a => aEnd(a) >= cut).length;
    if (n === agents.length) continue;   // identical to "All time" — don't offer the same view twice
    opts.push({ id: h + "h", label: lab + " of activity", group: "windows", n,
                test: a => aEnd(a) >= cut });
  }
  opts.push({ id: "all", label: "All time", group: "windows", n: agents.length, test: () => true });
  _roKey = key;
  return (_roCache = opts);
}

function curRange(){
  const opts = rangeOptions((snap && snap.agents) || []);
  return opts.find(o => o.id === range) || opts[opts.length - 1];   // fall back to All time
}

// THE filter. Every consumer — rail, stat, world lines, cost readout, keyboard nav, renderSig —
// reads agents through this, so the panes can never disagree about what is in view.
function viewAgents(){
  const all = (snap && snap.agents) || [];
  if (range === "all") return all;
  const o = curRange();
  return all.filter(o.test);
}

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
  // labels come from the FULL agent set so "devil #2" keeps its number when a filter is on —
  // renumbering rows as you narrow the range would make the same agent look like a different one
  const labels = roleLabels(snap.agents);
  const view = viewAgents();
  if (!view.length){
    host.appendChild(cell("div", "empty", "No agents in this time range."));
    return;
  }
  for (const a of view){
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
  const a = viewAgents().find(x => key(x) === sel);   // out-of-range selection shows nothing
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
  // plot only the agents in the current time range — that is the whole point of the filter:
  // dropping the idle gaps between debates so the surviving lines get the full axis width
  const agents = viewAgents(), labels = roleLabels(snap.agents), win = tlWindow(agents);
  if (!agents.length){
    host.appendChild(cell("div", "empty", "No world lines in this time range."));
    return;
  }
  // colours/field indices stay keyed to the FULL field list so a role keeps its colour as you
  // filter; only the legend below is narrowed to the fields actually present in view
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
  // Divergence is recomputed over the agents IN VIEW, not taken from snap.divergence: quoting the
  // session-wide figure beside a narrowed plot would be a plain lie about what you're looking at.
  // The formula is the server's own (fraction whose model family differs from the Arbiter's field,
  // attractor_fields[0]) and reproduces snap.divergence exactly when the range is "All time".
  const arbField = fields.length ? fields[0] : null;
  const divergence = (arbField == null)
    ? (snap.divergence || 0)
    : agents.filter(a => a.family !== arbField).length / agents.length;
  meter.appendChild(grp("divergence", nixie(divergence.toFixed(6)),
    "REAL measurement, not a prop: the fraction of the agents IN VIEW running on a model "
    + "family other than the Arbiter's (" + (snap.arbiter_model || "unknown") + "). "
    + "1.000000 would mean every agent diverged; 0.000000 means none did."));
  meter.appendChild(grp("world lines",
    nixie(String(agents.length).padStart(2, "0"), "teal small")));
  meter.appendChild(grp("live", nixie(String(nActive).padStart(2, "0"), "teal small")));
  if (win) meter.appendChild(grp("observed span", nixie(fmtDur(win.span), "teal small")));
  const fk = cell("div", "fieldkeys");
  const inView = new Set(agents.map(a => a.family));
  fields.forEach((f, i) => {
    if (!inView.has(f)) return;   // a field with nobody in view is a dead legend entry
    const s = cell("span", "fk", FIELD_GREEK[i % FIELD_GREEK.length] + " " + f);
    s.style.color = FIELD_COLORS[i % FIELD_COLORS.length];
    fk.appendChild(s);
  });
  if (fk.childNodes.length)
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

// The time-range <select>: auto-detected debate bursts first, then relative windows. Same
// dataset.sig guard as renderSessions() — a rebuild on every 1.2s poll would fight the mouse.
function renderRanges(){
  const el = document.getElementById("range");
  const opts = rangeOptions((snap && snap.agents) || []);
  // the burst list changes as a debate runs; if the selected range evaporated, fall back to
  // "All time" HERE, before renderSig() reads `range`, so the guard sees the value we render
  if (!opts.some(o => o.id === range)) range = "all";
  if (document.activeElement === el) return;   // never rebuild the list under an open dropdown
  const sig = opts.map(o => o.id + ":" + o.n + ":" + o.label).join("|") + "#" + range;
  if (el.dataset.sig === sig) return;
  el.dataset.sig = sig;
  el.textContent = "";
  const groups = {};
  for (const o of opts){
    if (!groups[o.group]){
      const g = document.createElement("optgroup");
      g.label = o.group === "debates" ? "Debates (auto-detected)" : "Time windows";
      el.appendChild(g);
      groups[o.group] = g;
    }
    const opt = document.createElement("option");
    opt.value = o.id;
    opt.textContent = o.label;
    if (o.id === range) opt.selected = true;
    groups[o.group].appendChild(opt);
  }
}

// Change the visible range. If the selected agent falls outside the new view, re-point it at the
// first visible one — otherwise the detail pane would keep showing an agent the rail no longer lists.
function setRange(id){
  range = id;
  const view = viewAgents();
  if (!view.some(a => key(a) === sel)) sel = view.length ? key(view[0]) : null;
  render();
}

// A cheap signature of everything the rail + transcript actually render. If it's unchanged
// between polls, render() skips the DOM rebuild entirely and the user's scroll is left alone —
// this is the real fix for "scroll resets every tick" (and it means a finished debate, whose
// data never changes again, is never rebuilt at all while you read it).
function renderSig(){
  if (!snap || !snap.agents) return "none";
  // signed over the agents IN VIEW: with a filter on, an agent outside the range changing must
  // NOT trigger a rebuild — nothing on screen would differ, and the rebuild would cost the scroll
  const view = viewAgents();
  const a = view.find(x => key(x) === sel);
  const rows = view.map(x =>
    x.id + ":" + x.status + ":" + (x.activity || "") + ":" + usageTotal(x.usage) +
    ":" + (x.duration_sec || 0) + ":" + (x.heat || 0) + ":" + (x.tok_share || 0)).join("|");
  return rows + "##sel=" + sel + "##ev=" + (a ? a.events.length : -1) +
         "##rng=" + range +
         "##wl=" + showWL + "##help=" + showHelp +
         "##ind=" + (snap.independence ? snap.independence.status : "") +
         "##lbl=" + (snap.label || "");
}

function render(){
  renderSessions();
  renderRanges();   // before renderSig() below: it can reset a range whose burst has evaporated
  document.getElementById("arb").textContent =
    (snap && snap.arbiter_model) ? "arbiter: " + snap.arbiter_model : "";

  // Everything user-facing below counts the FILTERED view, not the whole session — a caption or a
  // spend figure describing agents you can't see is the same lie as the divergence meter's.
  const view = viewAgents();
  const vn = view.length;

  // timeline span caption (matches the terminal's "N agents over <span>")
  const spanEl = document.getElementById("span");
  if (vn){
    const win = tlWindow(view);
    spanEl.textContent = win
      ? vn + " agent" + (vn === 1 ? "" : "s") + " over " + fmtDur(win.span)
      : vn + " agent" + (vn === 1 ? "" : "s");
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
    !n ? "" : (vn === n ? n + " agent" + (n === 1 ? "" : "s")
                        : "showing " + vn + " of " + n + " agents");

  // the range picker lights up whenever it is hiding something, so a narrowed view is never silent
  const rangeEl = document.getElementById("range");
  rangeEl.classList.toggle("on", range !== "all");
  rangeEl.title = "Limit the rail and world lines to one debate burst or a recent window. "
    + "Bursts are detected from idle gaps; windows are measured back from the session's last "
    + "activity, not from now. Keys: f / F.";

  // live cost readout: the running spend of the agents IN VIEW, refreshed on every poll
  const tot = debateTotals(view);
  const spend = document.getElementById("tspend");
  spend.textContent = vn
    ? fmtTok(tot.tok) + " tok  ·  " + fmtCost(tot.cost) +
      (tot.cost != null && tot.unpriced ? "+" : "")
    : "";
  spend.title = vn
    ? "running totals across the " + vn + " agent" + (vn === 1 ? "" : "s") +
      " in view; the cost is indicative, from debate_lib's price table" +
      (tot.unpriced ? " — " + tot.unpriced + " agent" + (tot.unpriced === 1 ? " has" : "s have")
                      + " no price entry for their model family, so the total is a floor" : "")
    : "";

  document.getElementById("help").classList.toggle("hidden", !showHelp);

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
    if (sel === null){ const v = viewAgents(); if (v.length) sel = key(v[0]); }
    render();
  } catch (e) {
    document.getElementById("err").textContent = "disconnected — is the viewer still running?";
  }
}

// Switching sessions — shared by the dropdown and the [ / ] keys so both reset the same state.
function selectSession(id){
  if (!id) return;
  sessionId = id;
  sel = null; snap = null; _lastRenderSig = null;   // different session => old selection/render are meaningless
  range = "all";                                    // another session's bursts are not this one's
  poll();
}

document.getElementById("sess").addEventListener("change", ev => {
  selectSession(ev.target.value);
});
document.getElementById("range").addEventListener("change", ev => {
  setRange(ev.target.value);
});
document.getElementById("wl-toggle").addEventListener("click", () => {
  showWL = !showWL; render();
});
document.getElementById("help").addEventListener("click", () => {
  showHelp = false; render();
});

// --- keyboard navigation ------------------------------------------------------
// Move the rail selection (j/k, arrows), step sessions ([ / ]) and time ranges (f / F), toggle
// the panels (w, ?).
// Every key is ignored while a form control has focus, so the session <select> keeps its own
// arrow/type-ahead behaviour — otherwise 'w' inside the dropdown would toggle World Lines.
function typing(el){
  return !!el && (/^(input|select|textarea)$/i.test(el.tagName) || el.isContentEditable);
}

// Step along the rail in its rendered order (the filtered view), clamped at both ends — no wrap,
// so holding a key parks you at the first/last agent instead of cycling.
function moveSel(step){
  const view = viewAgents();
  if (!view.length) return;
  const i = view.findIndex(a => key(a) === sel);
  const next = Math.min(view.length - 1, Math.max(0, i < 0 ? 0 : i + step));
  sel = key(view[next]);
  render();   // sel is part of renderSig(), so this rebuilds and the .sel row exists below
  const row = document.querySelector("#rail .ra.sel");
  // block:"nearest" scrolls the rail only when the row is actually off-screen, so it leaves
  // the reader's position alone in the common case (same spirit as the scroll save/restore).
  if (row) row.scrollIntoView({ block: "nearest" });
}

// Cycle the time range in the dropdown's own order (newest debate → older debates → windows →
// All time), clamped like moveSel so holding f parks on "All time" instead of wrapping around.
function stepRange(step){
  const opts = rangeOptions((snap && snap.agents) || []);
  if (!opts.length) return;
  const i = opts.findIndex(o => o.id === range);
  const next = Math.min(opts.length - 1, Math.max(0, i < 0 ? opts.length - 1 : i + step));
  if (opts[next].id !== range) setRange(opts[next].id);
}

function stepSession(step){
  if (!sessions.length) return;
  const cur = (snap && snap.label) || sessionId;
  const i = sessions.findIndex(s => s.id === cur);
  const next = Math.min(sessions.length - 1, Math.max(0, i < 0 ? 0 : i + step));
  if (sessions[next].id !== cur) selectSession(sessions[next].id);
}

document.addEventListener("keydown", ev => {
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return;   // leave browser shortcuts alone
  if (typing(ev.target)) return;
  const k = ev.key;
  if (k === "j" || k === "ArrowDown") moveSel(1);
  else if (k === "k" || k === "ArrowUp") moveSel(-1);
  else if (k === "f") stepRange(1);
  else if (k === "F") stepRange(-1);
  else if (k === "]") stepSession(1);
  else if (k === "[") stepSession(-1);
  else if (k === "w") { showWL = !showWL; render(); }
  else if (k === "?") { showHelp = !showHelp; render(); }
  else return;
  ev.preventDefault();   // only for keys we actually handled (arrows would scroll the page)
});
poll();
setInterval(poll, 1200);
