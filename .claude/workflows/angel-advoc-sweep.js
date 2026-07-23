export const meta = {
  name: 'angel-advoc-sweep',
  description: "Fan out the Angel's Advocate debate across every changed file in a diff — one Angel/Devil debate per file in parallel, then a synthesized cross-file verdict.",
  whenToUse: "When a change spans many files and you want each reviewed with its own two-sided debate concurrently, instead of one serial pass over the whole diff.",
  phases: [
    { title: 'Scope', detail: 'list changed files worth debating' },
    { title: 'Debate', detail: 'per file: Angel + cross-model Devil, in parallel' },
    { title: 'Synthesize', detail: 'roll per-file verdicts into one cross-file ruling' },
  ],
}

// --- Phase 1: scope — which files actually warrant a debate ---
// args (optional): a base ref to diff against (e.g. "main", "HEAD~3"). Default: the
// current uncommitted diff (staged + unstaged) vs HEAD.
phase('Scope')
// Shell-single-quote a value so an untrusted ref or diff filename can't break out
// of the git command we hand to an agent. Defense-in-depth: the agent runs these via
// its Bash tool, so an unescaped `'` in a maliciously-named file (from a reviewed PR)
// would otherwise be a command-injection vector. POSIX-safe: close-quote, escaped
// quote, reopen-quote for each embedded `'`.
const shq = (s) => `'${String(s).replace(/'/g, "'\\''")}'`

const base = (typeof args === 'string' && args.trim()) ? args.trim() : ''
const diffCmd = base
  ? `git diff --name-only ${shq(base)}...HEAD`
  : `git diff --name-only HEAD`

const scope = await agent(
  `List the files worth an independent code-review debate from this change set.\n` +
  `Run: \`${diffCmd}\` (and \`git diff --name-only --staged\` if no base was given) to get changed files.\n` +
  `EXCLUDE files that don't merit a debate: pure lockfiles, generated output, vendored deps, ` +
  `binary assets, and trivial one-line/whitespace changes. KEEP source files with real logic changes.\n` +
  `For each kept file, note in one phrase why it's worth reviewing (what changed).\n` +
  `If nothing merits review, return an empty files array.`,
  {
    label: 'scope-diff',
    schema: {
      type: 'object',
      required: ['files'],
      properties: {
        files: {
          type: 'array',
          items: {
            type: 'object',
            required: ['path', 'why'],
            properties: {
              path: { type: 'string' },
              why: { type: 'string' },
            },
          },
        },
      },
    },
  }
)

const files = (scope?.files || []).filter(f => f && f.path)
if (files.length === 0) {
  log('No files merit a debate — clean or trivial diff. Nothing to sweep.')
  return { reviewed: 0, files: [], verdicts: [] }
}
log(`Sweeping ${files.length} file(s): ${files.map(f => f.path).join(', ')}`)

// --- Phase 2 + per-file synthesis, pipelined ---
// Each file runs its own debate independently: Angel and Devil in PARALLEL (Devil
// cross-model for real independence), then that file's Arbiter folds the two into a
// per-file verdict. Pipelined so a fast file's verdict isn't blocked by a slow file's
// debate.
const FINDINGS = {
  type: 'object',
  required: ['file', 'verdict', 'dealbreakers'],
  properties: {
    file: { type: 'string' },
    verdict: { type: 'string' },
    dealbreakers: {
      type: 'array',
      items: {
        type: 'object',
        required: ['item', 'severity', 'disposition'],
        properties: {
          item: { type: 'string' },
          severity: { type: 'string', enum: ['dealbreaker', 'worth-noting'] },
          disposition: { type: 'string' },
        },
      },
    },
  },
}

const verdicts = await pipeline(
  files,
  // Stage 1: run Angel + Devil concurrently on this one file's diff.
  async (file) => {
    const diffOne = base
      ? `git --no-pager diff ${shq(base)}...HEAD -- ${shq(file.path)}`
      : `git --no-pager diff HEAD -- ${shq(file.path)}`
    const brief =
      `You are reviewing ONE file: \`${file.path}\` (changed because: ${file.why}).\n` +
      `Get its actual diff by running: \`${diffOne}\` (also check \`--staged\`).\n` +
      `Argue across the four lenses: correctness/risk, approach/design, scope discipline, assumptions.`

    const [angelCase, devilCase] = await parallel([
      () => agent(`${brief}\n\nBuild the strongest HONEST case FOR this change (steelman).`,
        { agentType: 'angel', label: `angel:${file.path}`, phase: 'Debate' }),
      () => agent(`${brief}\n\nAttack this change: what breaks, hidden costs, failure modes. Mark DEALBREAKER vs WORTH-NOTING. Reproduce where you can.`,
        { agentType: 'devil', label: `devil:${file.path}`, phase: 'Debate' }),
    ])
    return { file, angelCase, devilCase }
  },
  // Stage 2: this file's Arbiter weighs the two into a per-file verdict.
  async (r) => {
    if (!r) return null
    // Fail loud, don't paper over a half-debate. A missing advocate (agent skipped or
    // died mid-run) used to be rendered as '(no case returned)' with the verdict formed
    // anyway — a one-sided debate wearing a two-sided costume, the exact false-confidence
    // this workflow exists to prevent. Instead: refuse to arbitrate and surface the gap as
    // an unresolved dealbreaker so the cross-file synthesis can't mistake it for a clean review.
    const missing = [!r.angelCase && 'angel', !r.devilCase && 'devil'].filter(Boolean)
    if (missing.length) {
      return {
        file: r.file.path,
        verdict: `⚠️ DEGRADED RIGOR — no verdict formed: ${missing.join(' and ')} `
          + `advocate returned nothing (agent skipped or died mid-run). This file was NOT `
          + `reviewed two-sided; re-run the sweep before trusting any ruling on it.`,
        dealbreakers: [{
          item: `${missing.join(' and ')} advocate produced no case for ${r.file.path}`,
          severity: 'dealbreaker',
          disposition: 'unresolved — debate incomplete, re-run required',
        }],
      }
    }
    return agent(
      `You are the Arbiter for the file \`${r.file.path}\`. Two advocates reviewed its diff.\n\n` +
      `ANGEL (case for):\n${r.angelCase}\n\n` +
      `DEVIL (case against):\n${r.devilCase}\n\n` +
      `Weigh them and issue a verdict for THIS file. Dispose of each Devil dealbreaker by name ` +
      `(resolved-by / accepting-because). Stay clinical; tone carries no weight.`,
      { label: `arbiter:${r.file.path}`, phase: 'Debate', schema: FINDINGS }
    )
  }
)

const good = verdicts.filter(Boolean)

// --- Phase 3: cross-file synthesis ---
// A barrier is correct here: the final ruling genuinely needs ALL per-file verdicts
// at once to spot cross-file themes and rank what matters most.
phase('Synthesize')
const summary = await agent(
  `You are the lead Arbiter. Below are per-file verdicts from a parallel review sweep. ` +
  `Synthesize ONE cross-file ruling: the overall health of the change, the highest-severity ` +
  `dealbreakers across all files (ranked), any theme that recurs across files, and a clear ` +
  `bottom line (ship / fix-first / rethink).\n\n` +
  `PER-FILE VERDICTS:\n${JSON.stringify(good, null, 2)}`,
  { label: 'synthesize', phase: 'Synthesize' }
)

return {
  reviewed: good.length,
  files: files.map(f => f.path),
  verdicts: good,
  synthesis: summary,
}
