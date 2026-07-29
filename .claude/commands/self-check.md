Run every Angel's Advocate integrity check in one shot and relay the result:

```
tools/self-check.sh
```

Run it from the project root. It consolidates the workflow's own honesty machinery — which is
otherwise scattered across separate scripts that only run when someone remembers — behind one
green/red answer:

- **Test + calibration suites** (`tools/tests/*_test.sh`): the unit tests plus the calibration
  harnesses that prove the checkers aren't rubber-stamping — `verdict_lint_test.sh` (5 known-bad
  the verdict-lint must flag + 3 controls), `verifier_calibration_test.sh`, `independence_test.sh`,
  `journal*_test.sh`, `gate_sweep_test.sh`, `debate_lib_test.sh`.
- **Preflight** — the static cross-model config guard, so no `devil`/`verifier`/`red-teamer`/
  `interpreter` would silently collapse to a same-model self-check. Pass the arbiter model as an
  argument if it isn't `$ANTHROPIC_MODEL` (`tools/self-check.sh claude-opus-4-8`).
- **Gate-sweep** — scans recent commits for gate-worthy changes with no journal entry nearby
  (under-fire audit). Informational only; it never fails the run.

Relay the final summary line (ALL GREEN, or which checks FAILED). Exit code is 0 iff every failing
check passed. This is a health check on the *workflow itself* — run it after changing any of the
agents, the debate/verify machinery, or the calibration fixtures; it does not review your code
changes (that's the debate + verifier's job).
