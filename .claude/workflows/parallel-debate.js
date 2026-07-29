export const meta = {
  name: 'parallel-debate',
  description: "Run ONE decision through a fanned-out Angel's Advocate debate: an Angel plus a Devil lens-panel (one cross-model Devil per lens) in parallel, a Researcher and Historian alongside, one cross-examination round, then a structured hand-back for the Arbiter to rule — with an optional worktree-isolated parallel-implementation phase.",
  whenToUse: "For a single heavy/forked decision where you want the attack widened across all lenses at once (correctness, design+maintainability, scope, assumptions, caller ergonomics) instead of one Devil, and/or want the resulting multi-file change implemented by isolated workers in parallel. For a change that spans many files each needing its own debate, use angel-advoc-sweep instead.",
  phases: [
    { title: 'Panel', detail: 'Angel + one cross-model Devil per lens + Researcher + Historian, in parallel' },
    { title: 'Cross-exam', detail: 'Angel rebuts the collected dealbreakers; a Devil counters the Angel' },
    { title: 'Collect', detail: 'hand the debate material back to the Arbiter (it does NOT rule here)' },
    { title: 'Implement', detail: 'optional: worktree-isolated workers apply independent tasks in parallel' },
  ],
}

// ---------------------------------------------------------------------------
// args:
//   - a STRING  -> the decision brief (the verbatim request + the artifact/diff/plan).
//   - an OBJECT -> { brief, lenses?, implement? }
//       brief:     string, as above (required)
//       lenses:    string[] override of the Devil lens-panel (default: the five lenses)
//       implement: [{ task, files? }]  independent units of work to apply in parallel in
//                  worktree isolation AFTER you've ruled. Only pass this once the Arbiter
//                  (you, outside the workflow) has issued a verdict and wants it built out.
// The workflow gathers and returns the debate material; it NEVER issues the verdict itself —
// "the debate informs; the Arbiter decides" (CLAUDE.md). Rule from the returned object, then
// run tools/verdict-lint.py on your Dealbreakers block.
// ---------------------------------------------------------------------------
const brief = (typeof args === 'string') ? args : (args && args.brief)
if (!brief || !String(brief).trim()) {
  log('parallel-debate: no decision brief supplied (pass a string, or {brief}). Nothing to debate.')
  return { error: 'no brief', ruled: false }
}

const DEFAULT_LENSES = [
  { key: 'correctness/risk', ask: 'What breaks? Edge cases, races, prod failures, unhandled inputs. On hard-to-reverse changes, attack the recovery/rollback story. Reproduce where you can.' },
  { key: 'approach/design & maintainability', ask: "What's the simpler path being ignored? Is this clever-but-costly to live with — needless complexity, tight coupling, a shape that will drift?" },
  { key: 'scope discipline', ask: 'Where is this over-built, gold-plated, or solving problems that do not exist yet?' },
  { key: "assumptions / wrong-problem", ask: 'Which hidden assumption sinks this? Is it solving what the user actually asked for, or building the wrong thing correctly?' },
  { key: 'caller/consumer ergonomics', ask: 'For any caller surface (API/CLI/command/library/human-read output): where is it awkward, surprising, undiscoverable, or unsafe to invoke? What will the next user trip over?' },
]
const lenses = (args && Array.isArray(args.lenses) && args.lenses.length)
  ? args.lenses.map(k => ({ key: String(k), ask: `Attack strictly through the "${k}" lens.` }))
  : DEFAULT_LENSES

const DEALBREAKERS = {
  type: 'object',
  required: ['lens', 'findings'],
  properties: {
    lens: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['item', 'severity', 'grounded'],
        properties: {
          item: { type: 'string' },
          severity: { type: 'string', enum: ['dealbreaker', 'worth-noting'] },
          grounded: { type: 'string', enum: ['reproduced', 'reasoned'] },
          evidence: { type: 'string' },
        },
      },
    },
  },
}

// --- Phase 1: the panel, all in parallel (independence — none can anchor on another) ---
phase('Panel')
log(`Debating with a ${lenses.length}-lens Devil panel + Angel + Researcher + Historian.`)

const COURIER =
  `You are debating ONE decision. Here is the raw material verbatim — do NOT trust any paraphrase:\n\n` +
  `--- DECISION BRIEF ---\n${brief}\n--- END BRIEF ---\n`

// One thunk per panelist; parallel() is a barrier so we collect the whole panel before cross-exam.
const panelThunks = [
  () => agent(`${COURIER}\nBuild the strongest HONEST case FOR this direction (steelman across all five lenses). Concede what you can't defend.`,
    { agentType: 'angel', label: 'angel', phase: 'Panel' }).then(t => ({ kind: 'angel', text: t })),
  () => agent(`${COURIER}\nYou are the Researcher — read-only, neutral. Gather the facts this debate turns on: reproductions, measurements, ground truth from the code (cite file:line), and the integration/blast-radius (grep every caller/dependent, enumerate what would have to move). Return FINDINGS; take no side.`,
    { agentType: 'researcher', label: 'researcher', phase: 'Panel' }).then(t => ({ kind: 'researcher', text: t })),
  () => agent(`${COURIER}\nYou are the Historian — read-only. Read .angel-advoc/journal.jsonl and git history and surface PRECEDENT relevant to THIS decision: similar past decisions, recurring dealbreakers, and any verdict that later failed verification. Honest "nothing relevant" is fine.`,
    { agentType: 'historian', label: 'historian', phase: 'Panel' }).then(t => ({ kind: 'historian', text: t })),
  ...lenses.map((L, i) => () =>
    agent(`${COURIER}\nYou are the Devil's Advocate, assigned ONE lens only: **${L.key}**.\n${L.ask}\n` +
          `Ground each finding (reproduce where you can). Mark each DEALBREAKER vs WORTH-NOTING. Stay in your lane — another Devil covers the other lenses. If nothing real breaks on your lens, say so plainly.`,
      { agentType: 'devil', label: `devil:${L.key}`, phase: 'Panel', schema: DEALBREAKERS })
      .then(f => ({ kind: 'devil', lens: L.key, findings: f }))),
]

const panel = (await parallel(panelThunks)).filter(Boolean)

const angel = panel.find(p => p.kind === 'angel')?.text || null
const researcher = panel.find(p => p.kind === 'researcher')?.text || null
const historian = panel.find(p => p.kind === 'historian')?.text || null
const devilPanel = panel.filter(p => p.kind === 'devil' && p.findings)

// Flatten every raised finding so the Arbiter (and verdict-lint later) sees the full set.
const allFindings = devilPanel.flatMap(d =>
  (d.findings.findings || []).map(f => ({ lens: d.lens, ...f })))
const dealbreakers = allFindings.filter(f => f.severity === 'dealbreaker')

// A debate with no Angel or no Devil is a one-sided costume — refuse to dress it as two-sided.
if (!angel || devilPanel.length === 0) {
  const missing = [!angel && 'angel', devilPanel.length === 0 && 'devil-panel'].filter(Boolean)
  log(`DEGRADED: ${missing.join(' and ')} produced nothing — returning without a cross-exam.`)
  return { ruled: false, degraded: missing, angel, researcher, historian, dealbreakers: allFindings }
}

// --- Phase 2: one cross-examination round ---
phase('Cross-exam')
const findingsBlob = JSON.stringify(allFindings, null, 2)
const evidence =
  (researcher ? `\n\nRESEARCHER FINDINGS (shared ground truth):\n${researcher}` : '') +
  (historian ? `\n\nHISTORIAN PRECEDENT:\n${historian}` : '')

const [angelRebuttal, devilCounter] = await parallel([
  () => agent(`${COURIER}${evidence}\n\nThe Devil panel raised these, across lenses:\n${findingsBlob}\n\n` +
              `CROSS-EXAMINATION: respond to each dealbreaker ONE BY ONE — rebut, concede, or refute with evidence. Do NOT re-issue your opening.`,
    { agentType: 'angel', label: 'angel:cross-exam', phase: 'Cross-exam' }),
  () => agent(`${COURIER}${evidence}\n\nThe Angel's opening case was:\n${angel}\n\n` +
              `CROSS-EXAMINATION: attack the Angel's ACTUAL claims one by one — rebut, concede, or refute each. Do NOT re-issue your opening.`,
    { agentType: 'devil', label: 'devil:cross-exam', phase: 'Cross-exam' }),
])

// --- Phase 3: hand back to the Arbiter (no ruling here) ---
phase('Collect')
const material = {
  ruled: false,   // the Arbiter rules OUTSIDE the workflow, then runs tools/verdict-lint.py
  brief,
  lenses: lenses.map(L => L.key),
  angel,
  researcher,
  historian,
  dealbreakers: allFindings,
  dealbreakerCount: dealbreakers.length,
  crossExam: { angelRebuttal, devilCounter },
  note: 'Debate material only. The Arbiter must weigh this, issue the verdict disposing of every ' +
        'dealbreaker by name, then run tools/verdict-lint.py on the Dealbreakers block.',
}

// --- Phase 4 (optional): parallel worktree-isolated implementation ---
const toImplement = (args && Array.isArray(args.implement)) ? args.implement.filter(t => t && t.task) : []
if (toImplement.length === 0) return material

phase('Implement')
log(`Applying ${toImplement.length} independent task(s) in parallel via the Builder role, each in its own git worktree.`)
// PREFER `build-sweep` for parallel implementation: if the units are file-disjoint (the vast
// majority of cases) use that workflow instead — it writes straight to the working tree and
// self-integrates with NO merge step. Reach for this worktree-isolated path ONLY when units
// genuinely share files and can't be decomposed further. HONEST CAVEAT: isolated changes land on
// each worktree's own branch and do NOT auto-merge; the Arbiter integrates them afterward with git
// merge (semantic conflicts stay with the Arbiter — that's coupled, context-heavy work per
// builder.md, and exactly why a 2026-07-29 structural debate ruled AGAINST an `integrator` role:
// fire-rate 0, git covers the mechanical merge, and a context-poor subagent reporting "MERGED" on a
// semantic conflict is a false-confidence trap. See .angel-advoc/journal.jsonl).
const applied = await parallel(toImplement.map((t, i) => () =>
  agent(`Implement this ONE unit of work from a decision that has already been debated and ruled on:\n\n` +
        `TASK: ${t.task}\n` +
        (t.files ? `FILES YOU OWN (touch no others): ${[].concat(t.files).join(', ')}\n` : '') +
        `\nContext (the decision brief):\n${brief}\n\n` +
        `Build it minimally and in the surrounding style; verify where cheap; report exactly what you changed. Stop and report if you hit an overlap or a gap in the brief rather than guessing.`,
    { agentType: 'builder', label: `implement#${i + 1}`, phase: 'Implement', isolation: 'worktree' })
    .then(r => ({ task: t.task, result: r }))))

return { ...material, implemented: applied.filter(Boolean) }
