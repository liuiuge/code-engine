# 开发方案：自定义问题（Custom Questions / P1-13）

> Owner: Role 2 Developer（`.workbuddy/agents/dev.md`）
> Spec 来源（PM，只读）：`specs/custom-questions/CUSTOM_QUESTIONS.md`、`specs/custom-questions/CHECK_SPEC.md`、`specs/PHASE1_PLAN.md` §2 P1-13
> 进度记录：`plan/custom-questions/PROGRESS.md`
> 关联里程碑：阶段一 W2（可靠 / 功能）

---

## 1. 目标与范围

让系统支持**任意自由文本**作为问题输入，并按以下契约运行：

- **CQ-01** 自定义*编程*题 → 走原生成→编译→(验证) 路径，产出 Go 代码。
- **CQ-02** 自定义*非编程*题 → 进入问答（`general_assistant`），不生成 Go 文件。
- **CQ-03** 管线前预检：由 **Agent(LLM)** 比对本地 problem 列表，命中已有 → 发「确认」请求，用户确认前不进入生成；确认复用则打开已有，确认不相关则新建。
- **CQ-04** 自定义问题与 LeetCode **独立存储**（`output/custom-questions/`，`source:"custom"`），不进 `problems_index.json` / `/api/problems` 默认列表。
- **CQ-05** 非交互（CLI / `--no-confirm`）降级：跳过确认、直接按判定复用/新建，不阻塞。
- **CQ-06** 不存在则新建并赋予隔离编号 `C-<seq>`。

子任务 `CHECK_SPEC` 的 AC（CK-01…09）覆盖「预检 + 存储 + 编号 + 降级」后端逻辑，是本实现的**质量门核心**。

## 2. 已拍板决策（直接采用，不再请示）

| 项 | 决策 |
|----|------|
| 确认形态 | (a) 管线前预检（`run_pipeline` 之前由 service 调 Agent 比对） |
| 存储布局 | (A) 独立目录 `output/custom-questions/<number>.json` |
| 相似度 | 由 Agent(LLM) 判断，**不**实现独立字符串/编辑距离算法 |
| 编号 | 非已存在 / 确认不相关时新建，自增 `C-<seq>`（与 LeetCode 题号隔离） |
| 端点 | 独立 `/api/custom-questions`（list/create/open/precheck/confirm） |

## 3. 功能拆分（按功能写代码）

1. **基础设施接线** — `paths.DEFAULT_CUSTOM_QUESTIONS_DIR`、`PromptKey.PROBLEM_MATCH`、`prompts/problem_match.md`、models.yaml `problem_match: local` 路由。
2. **预检模块** `features/solver/precheck.py` — `precheck_custom_question()`：取本地 problem 标题/slug 清单喂 LLM，要求只回 JSON `{exists, matched_slug, reason}`；坏 JSON/缺字段降级为 `no_match`（不抛异常）；LeetCode 可解析引用直接跳过预检。
3. **自定义存储** `features/problems/custom_storage.py` — `save_custom_question()`（写 `<number>.json` + `source:"custom"` + 单调编号）、`list_custom_questions()`、`load_custom_question()`；**不**写 `problems_index.json`。
4. **服务集成** `features/solver/service.py` — 统一入口 `generate_for_query()`（LeetCode 引用→旧路径；否则自定义）；`generate_custom_question()`（预检→`needs_confirm` 或 新建+跑管线）；`confirm_custom_question()`（复用/不相关）。CLI 加 `--no-confirm`。
5. **Web API** `web/routes/custom_questions.py` + `web/schemas.py` + `web/api.py` 注册。
6. **回归测试** `test_custom_check_regression.py`（CK-01…09）、`test_custom_questions_regression.py`（CQ-01…06），stub LLM 跑真实管线。

## 4. 范围说明（scope decision，已记入 report）

- **实现后端 API 路由**（契约已在 §6.3 拍板），但 **前端确认弹窗 UI 按 spec 延后到 W2**（前端属 heavy 改动，且依赖 W1 的 Job「待确认」态，尚未实现）。此为 1 项未明示选择，单独标注，未达「≥3 项需上报 Orchestrator」阈值。
- 不改动 LeetCode 拉题/富化主流程（仅新增自定义输入分支）。
- 不手动编辑 `output/` 生成产物；`output/custom-questions/` 由代码运行时写入，符合角色归属。

## 5. 复用现有能力（避免重复造轮子）

- 路由 / 去重：复用 `workflow.py` 的 `intent_classifier → (task_summarizer → code_generator → code_executor → verifier) | general_assistant` 现成图——CQ-01/02 的「编程/非编程分流」**无需改 workflow**，classifier 直接对自由文本生效。
- 模型路由：预检走 `invoke_model(role="problem_match")`，复用 `infrastructure/config.py` 的 role-based routing，默认 local 控成本。
- 存储隔离：Go 代码仍落 `output/go-code/<task_dir>/`（供 `/api/go-code` 查看），自定义**记录**落 `output/custom-questions/`，二者解耦、互不污染。

## 6. 质量门（提交前全绿）

```
PYTHONPATH=. python features/solver/tests/test_verifier_regression.py
PYTHONPATH=. python features/solver/tests/test_custom_check_regression.py
PYTHONPATH=. python features/solver/tests/test_custom_questions_regression.py
```
（全量管线需 langchain_ollama，运行于系统 Python 3.14；受管 venv 3.13.12 缺该包，故测试用系统 3.14。）
