# specs/ — Product Manager artifacts

This directory is the single home for **product / planning** artifacts: what the
system should do and why, independent of how the code is organized.

Developer source code lives **outside** this directory, at the repository root:

| Area        | Owner  | Location (repo root)                          |
|-------------|--------|----------------------------------------------|
| PM specs    | PM     | `specs/` (this directory)                    |
| Dev code    | Dev    | `features/`, `infrastructure/`, `web/`, `prompts/`, `frontend/` |
| Generated   | Runtime| `output/` (problems, go-code)                |

## Conventions

- **Feature specs / acceptance tests** → `specs/<feature-slug>/<NAME>.md`
  (e.g. `specs/verifier-node/VERIFIER_ACCEPTANCE.md`). The acceptance doc is
  test-first: each `AT-*` maps 1:1 to a case in
  `features/solver/tests/test_verifier_regression.py`.
- **Cross-cutting plans** (refactors, architecture) → `specs/<NAME>.md`
  (e.g. `specs/REFACTOR_PLAN.md`).
- Keep READMEs and `requirements.txt` at the repo root; they describe the whole
  repo, not a single feature.
