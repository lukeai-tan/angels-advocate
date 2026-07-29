export const meta = {
  name: 'build-sweep',
  description: "Implement an already-decided change in parallel: decompose it into independent DISJOINT-FILE units, build each with its own Builder concurrently (no two touch the same file, so they self-integrate into the working tree with no merge step), then verify the combined result.",
  whenToUse: "After you've ruled on a change that splits into several independent units you'd otherwise build one-by-one. NOT for tightly-coupled, context-heavy work — the Arbiter builds that inline (a Builder knows less than you). If units can't be made file-disjoint, that coupling means it isn't parallelisable; keep it inline.",
  phases: [
    { title: 'Decompose', detail: 'split the change into independent, file-disjoint units' },
    { title: 'Build', detail: 'one Builder per unit, in parallel, writing to the working tree' },
    { title: 'Verify', detail: 'run the project tests / a verification command over the combined result' },
  ],
}

// ---------------------------------------------------------------------------
// args:
//   - a STRING  -> the change brief (what to build; already debated + ruled).
//   - an OBJECT -> { brief, units?, verifyCmd? }
//       brief:     string (required) — the ruled change to implement.
//       units:     [{ task, files }] — skip auto-decomposition and use these units directly.
//       verifyCmd: string — the exact command to run in Verify (else the agent finds the tests).
//
// Why NO worktree isolation here: the decomposer guarantees each unit owns a DISJOINT set of
// files, so parallel Builders writing straight into the working tree never clobber each other and
// their changes are integrated the instant they land — no fragile worktree-merge dance. The cost
// is that file-SHARED work can't be parallelised: that's not a limitation to route around, it's the
// execution principle (coupled work stays with the Arbiter). Overlapping units are merged into one
// serial unit below so the disjoint invariant always holds.
// ---------------------------------------------------------------------------
const brief = (typeof args === 'string') ? args : (args && args.brief)
if (!brief || !String(brief).trim()) {
  log('build-sweep: no change brief supplied (pass a string, or {brief}). Nothing to build.')
  return { built: 0, error: 'no brief' }
}
const context = `You are implementing part of an already-decided, already-ruled change.\n\n` +
  `--- CHANGE BRIEF (verbatim) ---\n${brief}\n--- END BRIEF ---\n`

// --- Phase 1: decompose into disjoint-file units ---
phase('Decompose')
let units = (args && Array.isArray(args.units)) ? args.units.filter(u => u && u.task) : null
let coupledNote = ''
if (!units) {
  const plan = await agent(
    `${context}\nDecompose this change into INDEPENDENT units of implementation work, each owning a ` +
    `DISJOINT set of files (no file may appear in two units) so they can be built in parallel without ` +
    `conflict. For each unit give a precise task and the exact file paths it will touch. If two pieces ` +
    `of work must edit the SAME file, they are coupled — put them in ONE unit. If some work is too ` +
    `coupled to split at all, return it as a single unit and say so in coupledNote.`,
    {
      label: 'decompose', phase: 'Decompose',
      schema: {
        type: 'object', required: ['units'],
        properties: {
          units: {
            type: 'array',
            items: {
              type: 'object', required: ['task', 'files'],
              properties: { task: { type: 'string' }, files: { type: 'array', items: { type: 'string' } } },
            },
          },
          coupledNote: { type: 'string' },
        },
      },
    })
  units = (plan?.units || []).filter(u => u && u.task)
  coupledNote = plan?.coupledNote || ''
}
if (units.length === 0) {
  log('build-sweep: nothing to decompose. Done.')
  return { built: 0, units: [], coupledNote }
}

// Enforce the disjoint invariant: merge any units that share a file into one serial unit, so no
// two PARALLEL builders can ever race on the same file. (Incremental grouping — a unit merges into
// the first existing group it shares a file with; the group accumulates files, so a chain
// A~B~C collapses correctly as long as the shared files transit.)
function disjointGroups(us) {
  const groups = []
  for (const u of us) {
    const files = (u.files || []).map(String)
    const hit = groups.find(g => files.some(f => g.files.includes(f)))
    if (hit) {
      hit.tasks.push(u.task)
      for (const f of files) if (!hit.files.includes(f)) hit.files.push(f)
    } else {
      groups.push({ tasks: [u.task], files: [...files] })
    }
  }
  return groups.map(g => ({ task: g.tasks.join('  AND  '), files: g.files }))
}
const groups = disjointGroups(units)
if (groups.length < units.length) {
  log(`Merged ${units.length} proposed unit(s) into ${groups.length} file-disjoint group(s) to keep parallel builders from sharing a file.`)
}
log(`Building ${groups.length} unit(s) in parallel: ${groups.map(g => g.files.join('+') || '(no files listed)').join('  |  ')}`)

// --- Phase 2: parallel builders, one per disjoint unit ---
const reports = (await parallel(groups.map((g, i) => () =>
  agent(`${context}\nBuild THIS unit ONLY. You own exactly these files — touch no others; if you find ` +
        `you need a file not in this list, STOP and report the overlap instead of editing it:\n` +
        `  ${g.files.length ? g.files.join(', ') : '(infer from the task, but stay minimal)'}\n\n` +
        `UNIT: ${g.task}`,
    { agentType: 'builder', label: `build#${i + 1}`, phase: 'Build' })
    .then(r => ({ files: g.files, task: g.task, report: r }))))).filter(Boolean)

// --- Phase 3: verify the combined result ---
phase('Verify')
const verifyCmd = (args && args.verifyCmd) ? String(args.verifyCmd) : null
const verify = await agent(
  (verifyCmd
    ? `Run this verification command from the repo root and report pass/fail with the actual output:\n\`${verifyCmd}\``
    : `The parallel build above just landed several units in the working tree. Find the project's ` +
      `test suite(s)/linters and run them from the repo root, then report pass/fail with the actual ` +
      `output. If there is nothing runnable to verify, say so plainly.`) +
  `\n\nUnits just built:\n${reports.map(r => '- ' + r.task).join('\n')}`,
  { label: 'verify', phase: 'Verify' })

return {
  built: reports.length,
  units: groups,
  coupledNote,
  reports,
  verify,
  note: 'Changes were written directly to the working tree (disjoint units, no worktree isolation, ' +
        'so no merge step). Review the diff and run the Arbiter verification pass before committing.',
}
