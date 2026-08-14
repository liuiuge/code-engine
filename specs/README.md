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
- **Required section — 人类校验指引（Manual Acceptance）**：每份 spec 除回归测试映射外，
  **必须**含一节「## 人类校验指引（Manual Acceptance）」，用表格列出每条 AC 的
  `人类校验步骤 / 通过判定 / 失败判定` 以及统一的环境准备（如 `uvicorn` 启动 + 浏览器 `/ui` 打开路径、CLI 命令）。
  目的：让人类（PM/测试）能脱离回归套件手动验收每条 AC，降低「只能看代码、无法点」的验收门槛。
  格式示例：

  | AC | 人类校验步骤 | 通过判定 | 失败判定 |
  |----|------------|---------|---------|
  | AT-X | 点 X → 观察 Y | 出现 Z | 空白/报错 |
- **Cross-cutting plans** (refactors, architecture) → `specs/<NAME>.md`
  (e.g. `specs/REFACTOR_PLAN.md`).
- Keep READMEs and `requirements.txt` at the repo root; they describe the whole
  repo, not a single feature.
