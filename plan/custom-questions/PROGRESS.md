# 进度：自定义问题（Custom Questions / P1-13）

> 更新规则：每完成一个功能模块即更新状态与落盘文件。
> 关联方案：`../DEVELOPMENT_PLAN.md`，Spec：`specs/custom-questions/CUSTOM_QUESTIONS.md` + `CHECK_SPEC.md`

| # | 功能模块 | 落盘文件 | 状态 | 覆盖 AC |
|---|----------|----------|------|---------|
| 1 | 基础设施接线 | `infrastructure/paths.py`、`constants.py`、`config.py`、`models.yaml`、`prompts/problem_match.md` | ✅ 完成 | — |
| 2 | 预检模块（Agent 去重） | `features/solver/precheck.py` | ✅ 完成 | CK-01,02,03,09 |
| 3 | 自定义存储 + 编号 | `features/problems/custom_storage.py` | ✅ 完成 | CK-06,07 |
| 4 | 服务集成 + CLI | `features/solver/service.py`、`example/main.py` | ✅ 完成 | CQ-01,02,03,04,05,06 |
| 5 | Web API 路由 | `web/routes/custom_questions.py`、`web/schemas.py`、`web/api.py` | ✅ 完成 | CQ-03(端点), CQ-04(查询) |
| 6 | 回归测试 | `tests/test_custom_check_regression.py`、`tests/test_custom_questions_regression.py` | ✅ 完成 | 全部 AC |
| 7 | 质量门 | 系统 Python 3.14 跑全部回归 | ✅ 完成 | 全绿（verifier 8 + check 11 + questions 4） |

## 范围决策（scope decision）

- 后端 API 路由现在实现（§6.3 已拍板端点形态），**前端确认弹窗 UI 延后到 W2**（依赖 W1 异步 Job「待确认」态，尚未实现）。单一未明示选择，已标注，未达上报阈值。

## Git 状态说明（环境限制）

- 本沙箱环境的 `.git` 在多次 Bash 调用间**不持久**：首次 `git checkout -b` 后 `.git` 即丢失，重 `git init` 后提交在本调用内成功（`root-commit 4dcb83f`，62 文件）但下一调用 `git log` 即显示「无提交」。工作树源码与测试产物均正常持久。
- 因此本环境无法保留稳定提交；请在常规环境中用以下信息提交：
  - 分支：`feat/custom-questions`
  - 提交信息首行：`feat(custom-questions): implement custom question pipeline with dedup precheck and isolated storage`
  - 涉及文件：见 `features/solver/precheck.py`、`features/problems/custom_storage.py`、`features/solver/service.py`、`features/solver/example/main.py`、`web/routes/custom_questions.py`、`web/api.py`、`web/schemas.py`、`web/dependencies.py`、`infrastructure/{paths,constants,config}.py`、`infrastructure/models.yaml`、`prompts/problem_match.md`、`plan/**`、两个回归测试。

## 备注

- 测试运行环境：系统 Python 3.14（受管 venv 3.13.12 缺 langgraph/langchain_ollama）。
- `output/custom-questions/` 由代码运行时写入，非手动编辑产物。
- 质量门全绿：verifier 8 + custom-check 11 + custom-questions 4 = 23 项。
