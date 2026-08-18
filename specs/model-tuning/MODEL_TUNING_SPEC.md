# 默认模型/thinking 调优 + 速度质量开关 + 多解误判（P1-9 子任务）

> 归属：Role 1 PM（`specs/<feature-slug>/<NAME>.md`）
> 关联：阶段一 `specs/PHASE1_PLAN.md` 的 P1-9；本任务**自包含**实现 `used_model` 最小必要字段，与 P1-8 observability 后续合入不冲突；verifier 多解修复见 `specs/verifier-node/VERIFIER_ACCEPTANCE.md §8`。
> 性质：**开发就绪的子任务 spec**（含 `## Acceptance Criteria` + `## Test Scenarios`，每条 AC 1:1 映射回归用例）。
> Wave：W3（**含 P1-9FE 速度/质量优先开关子 Epic，与 P1-9 同波同批验收**，见 PHASE1_PLAN §1.1 矩阵）。

---

## 1. 范围与边界

本 spec 只覆盖「**缩短耗时、减少空跑重生成**」：

### 要做什么
1. `infrastructure/models.yaml`：角色路由 `routing.roles.code_generator` / `code_fixer` 保持 `local`（`thinking:false`）为「速度优先」**基线**；顶层 `default: minimax` 不变（仅用于 `config.llm` 共享实例 / 非路由角色兜底）。新增 `preference` 概念——`quality` 优先时 escalatable 角色首试即用 `online`（minimax，`thinking:true`）。
2. `infrastructure/config.py` `get_llm_for_role(role, retry_count=0, difficulty=None, preference=None)`（行 166）：新增 `preference` 参数；`preference=="quality"` 且 `role in _ESCALATE_ROLES` → 首试即返回 `get_llm(_ESCALATE_TO)`（minimax），跳过 local 尝试。（节点经 `invoke_model` 间接调用，见 §3.1；`preference` 由 web 层透传。）
3. `web/routes/problems.py` `generate_problem_code`（行 322，装饰器在 317）：增 query 参数 `preference: "speed" | "quality"`（默认 `speed`），透传 `generate_for_problem(..., preference=...)` → `run_pipeline(..., preference=...)` → 节点经 `invoke_model` 用 `get_llm_for_role(..., preference)`。（自定义问题 `generate_for_query` 同理透传 preference。）
4. `features/solver/verifier.py` 多解容错（见 VERIFIER_ACCEPTANCE §8）：**仅当题目记录标记 `multi_answer=true`（如 two-sum 系列）时**，对返回列表做**无序/排序比较**（解析为标量元素切片后排序再比较）；顺序敏感题意（区间合并、K 大、需特定顺序的数组）保持 JSON 顺序敏感比较（与 VERIFIER_ACCEPTANCE.md §4 一致）。`integer[][]` 按子数组逐个比较（每个子数组若是标量序列也可排序比较）。
   - 实现：发射进生成测试 harness 的 Go helper `cevEqual`（verifier.py 行 161 模板）改为带 `normalize` 参数：仅当 `normalize==true`（即 `multi_answer`）时，两侧可 `json.Unmarshal` 为标量元素切片则排序后比较；否则回退 `strings.ReplaceAll(s," ","")` 去空格字符串比较（详见 §3.2）。

### 不做什么（本任务边界）
- 不改 difficulty 预升级逻辑（行 181 `hard_escalate_roles` 保留）。
- 不改 verifier 的 skip / panic / timeout 分类。
- 不改动 P1-8 observability 的 `/health` 实现；本任务自包含 `used_model` 最小字段，与其可独立/并行合入。

### multi_answer 生产者（记录构造 / 落库机制）
`multi_answer` 决定 verifier 是否对返回列表做无序/排序比较（见 §1#4 / §3.2 / PF-02）。LeetCode 接口**不返回**该字段，因此必须在 code 侧**生产**它；否则生产环境恒为 False，PF-02 手动验收无法触发（只能手改 record）。选定机制：**白名单常量表（候选 A+B 合一）+ 加载回填（含人工覆盖，候选 C）**。

- **字段口径**：`record["multi_answer"]: bool`。
- **权威白名单**：`infrastructure/constants.py` 新增常量 `MULTI_ANSWER_SLUGS: frozenset[str]` 与辅助函数 `is_multi_answer_problem(slug, title=None) -> bool`。
  - **初始种子数据（slug）**：`two-sum`、`two-sum-ii-input-array-is-sorted`。
  - **判定规则**：`slug` 小写命中 `MULTI_ANSWER_SLUGS` → `True`；并以 `slug` 含子串 `"two-sum"` 作便捷兜底，覆盖同族变体。集合由 PM/运营随已知多解题补录扩展。
- **具体代码落点（Dev 实现点）**：
  1. `features/problems/models.py` `normalize_problem(problem)`（行 207）：构造规范 record 时写入 `"multi_answer": is_multi_answer_problem(slug, record.get("title"))`。该构造器是**唯一**规范记录来源，被 `save_problem`（落库）与 `fetch_live_problem`（实时）共用 → 新建/拉取/实时题均带字段。
  2. `features/problems/storage.py` `load_problem_file(path)`（行 93）：读取已落盘 `.json` 时，若 record **缺失** `multi_answer` 键则回填 `is_multi_answer_problem(rec.get("titleSlug"), rec.get("title"))`。**目的**：修复「历史已缓存题（如 two-sum.json）无该字段」导致手动验收空跑的问题，无需重拉即可生效。
     - **优先级（候选 C 人工可编辑）**：若 record **已显式携带** `multi_answer`（无论 True/False），以记录值为准、**不覆盖**；仅在键缺失时按白名单计算。未来运营/PM 可直接在题录 JSON 写 `multi_answer` 做人工覆盖。
- **不做什么**：不改 LeetCode 拉取协议；不引入新配置文件；白名单集中 `infrastructure/constants.py` 一处维护。

---

## 2. 处理流程（mermaid）

```mermaid
flowchart TD
    UI[前端 P1-5 面板 速度/质量开关] --> API[POST /api/problems/{id}/generate?preference=]
    API --> SVC[generate_for_problem preference]
    SVC --> ROLE[invoke_model -> get_llm_for_role preference]
    ROLE -->|speed| LOCAL[local thinking=false]
    ROLE -->|quality| ONLINE[minimax thinking=true]
    VER[verify_go_code] --> NORM[cevEqual normalize=multi_answer]
    NORM -->|two-sum 多解 multi_answer=true| PASS[判 pass]
```

---

## 3. 组件与契约（供 Dev 实现参考，非代码）

### 3.1 preference 透传链（含真实集成点）
- 端点：`POST /api/problems/{id}/generate?preference=speed|quality`（默认 `speed`；**仅 POST**，删除原 GET 设想）。
- 透传：`generate_problem_code`（`web/routes/problems.py:322`）读取 `preference` query 参数 → 透传 `generate_for_problem(..., preference=...)` → `run_pipeline(..., preference=preference)`；`app.invoke` 的 state 增加 `preference` 键 → 节点经 `invoke_model` 消费。
- **真实改点清单**（Dev 实现须覆盖）：
  1. `infrastructure/config.py` `get_llm_for_role(role, retry_count=0, difficulty=None, preference=None)`（行 166）：新增 `preference` 参数；`preference=="quality" and role in _ESCALATE_ROLES` → 首试即 `get_llm(_ESCALATE_TO)`（minimax）。
  2. `infrastructure/config.py` `invoke_model(role, prompt, retry_count=0, difficulty=None, preference=None, **kwargs)`（行 217）：新增 `preference` 参数，下传 `get_llm_for_role(role, retry_count, difficulty, preference=preference)`。节点**不直接**调 `get_llm_for_role`，统一经 `invoke_model` 以保持 timeout/escalate 逻辑一致。
  3. `features/solver/state.py` `AgentState`：增加 `preference: str`（默认 `"speed"`）键。
  4. `features/solver/nodes.py` `code_generator_node` / `code_fixer_node`：从 `state[StateKey.PREFERENCE]` 取 preference，传给 `invoke_model(..., preference=preference)`（替换现有直接 `invoke_model(role, prompt, difficulty=...)` 调用）。
- 说明：`preference` 仅影响**首试路由**；retry/escalate 逻辑不变（speed 首试 local 失败仍会升 online）。

### 3.2 verifier 归一化（改 `features/solver/verifier.py` 发射的 Go helper `cevEqual`）
`cevEqual` 是 verifier 以**字符串模板发射进生成测试 harness（`verify_test.go`）的 Go 函数**（verifier.py 行 161 模板），不是 Python。改为带 `normalize` 参数：

```go
// cevEqual compares two JSON-serialized verifier outputs.
// normalize==true (multi_answer problem): scalar-element slices are
// order-normalized (parse -> sort -> compare) so valid reorderings such as
// two-sum index pairs [0,1] vs [1,0] count as equal. Otherwise the comparison
// is order-sensitive (whitespace-stripped string equality), matching
// VERIFIER_ACCEPTANCE.md §4.
func cevEqual(got, expected string, normalize bool) bool {
    if normalize {
        var g, e interface{}
        if json.Unmarshal([]byte(got), &g) == nil &&
           json.Unmarshal([]byte(expected), &e) == nil {
            if gs, ok := g.([]interface{}); ok {
                if es, ok2 := e.([]interface{}); ok2 && len(gs) > 0 {
                    if _, isScalar := gs[0].(float64); isScalar {
                        return cevSorted(gs) == cevSorted(es)
                    }
                }
            }
        }
    }
    return strings.ReplaceAll(got, " ", "") ==
        strings.ReplaceAll(strings.TrimSpace(expected), " ", "")
}

// cevSorted stringifies slice elements and returns them sorted, so element
// order no longer matters for scalar-element slices.
func cevSorted(xs []interface{}) string {
    strs := make([]string, len(xs))
    for i, v := range xs {
        strs[i] = fmt.Sprint(v)
    }
    sort.Strings(strs)
    return strings.Join(strs, ",")
}
```
- 调用方（`_emit_test`）：`normalize` 取自 `problem_record.get("multi_answer") == True`；`multi_answer` 缺失/False 时走原顺序敏感比较。
- `integer[][]` 处理：顶层 `g` 为 `[]interface{}` 但其元素是 `[]interface{}`（非 scalar），`isScalar` 为 false → 落回去空格字符串比较（即按子数组逐个字符串比较）。如需对嵌套子数组也排序比较，可在 `cevSorted` 递归处理，本任务不强制。
- 兜底：无法解析为 JSON 列表时回退原去空格字符串比较（two-sum `[0,1]` 与 `[1,0]` 在 normalize 下排序后相等 → 判 pass）。

### 3.3 used_model 自包含契约（本任务内定义，不硬依赖 P1-8）
- 定义：`GenerateResult.used_model: str | None` = `code_generator` **首试**实际命中模型名（`preference=speed` → `local`，`quality` → `minimax`）。
- 填充：`code_generator_node` 首试返回时，把实际使用的模型名写入 state 的 `used_model` 键；`run_pipeline` 将其随结果 dict 透传；web 层 `generate_problem_code` / `_do_generate`（`web/routes/problems.py`）读取并填入 `GenerateResult.used_model`。
- 消费：PF-FE 用该字段验证开关生效（终态 `used_model` 随开关在 `local` / `minimax` 间变化）。
- 说明：本任务**自包含**实现 `used_model` 最小必要字段；P1-8 observability（O-02）后续也消费 `used_model`，二者口径一致、可并行合入不冲突。若 P1-8 先行，本任务复用其字段即可。

---

## 变更点小结（Dev 必改）

- `infrastructure/models.yaml`：仅确认角色路由 `code_generator`/`code_fixer` 为 `local`；不改顶层 `default`。
- `infrastructure/constants.py`：新增 `MULTI_ANSWER_SLUGS` + `is_multi_answer_problem`（multi_answer 生产者白名单）。
- `features/problems/models.py`：`normalize_problem` 构造 record 时写入 `multi_answer`。
- `features/problems/storage.py`：`load_problem_file` 对缺 `multi_answer` 键的历史记录回填。
- `infrastructure/config.py`：`get_llm_for_role` + `invoke_model` 增 `preference` 参数。
- `features/solver/state.py`：`AgentState` 增 `preference`、`used_model` 键。
- `features/solver/nodes.py`：`code_generator_node`/`code_fixer_node` 透传 `preference`；`code_generator_node` 首试填 `used_model`。
- `features/solver/verifier.py`：`cevEqual` Go 模板增 `normalize`（按 `multi_answer`）。
- `web/routes/problems.py`：`generate_problem_code` 增 `preference` query 参数并透传；填 `used_model` 到 `GenerateResult`。
- `web/schemas.py`：`GenerateResult` 增 `used_model: str | None = None`（若尚未由 P1-8 加）。
- `frontend/index.html`（P1-9FE）：速度/质量优先开关（见 PF-FE）。

---

## 4. Acceptance Criteria

### PF-01 — 速度优先端到端耗时低于质量优先
- **Given** 同一简单题（如 two-sum），分别 `preference=speed` 与 `preference=quality`。
- **When** 跑两次生成并计时。
- **Then**（**硬验收门 = stub 回归，确定性**）：stub `get_llm_for_role` 记录首试模型、stub local 快/online 慢，断言 speed 路径首试 `local`、quality 路径首试 `minimax`，且 speed 总耗时 **≤** quality 总耗时。
- **手动验收（信息性/视环境而定）**：真实环境 local 与 online 相对速度不定，仅作观察，不构成通过/失败硬判据。

### PF-02 — 多解题正确解不再误判 verified_fail（仅 multi_answer）
- **Given** 题目记录 `multi_answer=true` 的 two-sum 类多解题，代码返回合法但顺序不同的解（如 `[1,0]` vs 期望 `[0,1]`）。
- **When** `verify_go_code` 在 `assert` 模式运行。
- **Then** 正确解判 `pass`，**不再** `verified_fail`。
- **回归护栏**：`multi_answer=false`/缺失的顺序敏感题意，`[1,0]` vs `[0,1]` 仍须判 `verified_fail`（不得因排序泛化而误放）。

### PF-FE（P1-9FE）— 速度/质量开关驱动首试模型
- **Given** 前端 P1-5 面板的「速度优先 / 质量优先」开关。
- **When** 切换开关并触发 `POST /api/problems/{id}/generate?preference=speed|quality`。
- **Then** 首试模型随开关变化；终态读取并展示 P1-8FE 的 `used_model` 元素（复用，不重复造 UI）：speed → `local`，quality → `minimax`。`PF-01` 的端到端耗时对比须由该开关触发，UI 不可缺失。

### PF-03 — multi_answer 由记录生产链自动置位（PF-02 的前置条件）
- **Given** 题录 slug 命中白名单（如 `two-sum`）。
- **When** 该题被拉取/保存（`save_problem`→`normalize_problem`）或被读取（`load_problem_file`，含历史缓存缺字段）。
- **Then** 返回的 record 含 `multi_answer=True`；无需人工编辑题录即可触发 PF-02 无序比较。历史已缓存的 `two-sum.json` 在读取时被回填为 `True`。
- **回归护栏**：非白名单 slug（如 `climbing-stairs`）`multi_answer` 必须为 `False`，PF-02 不受影响。

---

## 5. Test Scenarios（映射回归用例）

> **回归文件由 Dev 新建**：`features/solver/tests/test_model_tuning_regression.py`（PF-01/PF-02 用例；并在 `features/solver/tests/test_verifier_regression.py` 增 two-sum 多解用例）。前端契约 `web/tests/test_realtime_panel_contract.py` **与 observability spec 共用**：以 `O-FE` / `PF-FE` 分测试类，互不冲突。stub `get_llm_for_role` 记录首试模型；stub LLM 用不同 delay 测耗时。
> **producer 回归（建议 `features/problems/tests/test_problems_regression.py`，或并入既有 problems 回归）**：断言 `is_multi_answer_problem` / `normalize_problem` 对 two-sum 置 `True`、`load_problem_file` 对缺字段历史记录回填 `True`、非白名单题为 `False`。

| 场景 | 覆盖 AC | 说明 |
|------|---------|------|
| `get_llm_for_role("code_generator", preference="speed")` → local；`preference="quality"` → minimax（首试） | PF-01 | monkeypatch 记录首试模型；断言 speed≠quality |
| stub local 快 / online 慢 → speed 路径总耗时不大于 quality（确定性） | PF-01 | 用 `time` 量两路径；**不依赖真实模型速度** |
| two-sum `multi_answer=true`，解 `[1,0]` vs 期望 `[0,1]` → `verify_go_code` 返 `pass` | PF-02 | 新增 verifier 用例 |
| two-sum `multi_answer=false`，解 `[1,0]` vs 期望 `[0,1]` → 仍 `verified_fail`（护栏） | PF-02 | 确保排序泛化不误放顺序敏感题意 |
| `code_generator_node` 首试填 `used_model`（speed→local / quality→minimax） | PF-FE | 断言 `GenerateResult.used_model` 取值正确 |
| 前端切 quality → 终态 `used_model` 含 minimax | PF-FE | 解析渲染 HTML / 接口字段（`test_realtime_panel_contract.py` PF-FE 类） |
| `normalize_problem`/`is_multi_answer_problem` 对 slug=`two-sum` → `multi_answer=True`；非白名单题 → `False` | PF-03 | 单测 `is_multi_answer_problem` / `normalize_problem` |
| 历史 `two-sum.json` 缺 `multi_answer` 键 → `load_problem_file` 回填 `True`；已显式写的记录不被覆盖 | PF-03 | 直接读 JSON 断言回填与覆盖优先级 |

---

## 6. 依赖与注意

- **自包含，不硬依赖 P1-8**：`used_model` 最小字段在本任务内定义（§3.3），与 P1-8 observability 后续合入不冲突。
- `preference` 仅影响**首试路由**；retry/escalate 逻辑不变（speed 首试 local 失败仍会升 online）。
- `multi_answer` 归一化**仅**对标记题目生效；顺序敏感题意保持 JSON 顺序敏感比较（VERIFIER_ACCEPTANCE §4）。
- quality 优先会消耗 online 配额/延迟，文档说明权衡。
- `used_model` 口径固定为「code_generator 首试实际命中模型名」，本任务与 P1-8 一致，避免验证错位。

---

## 7. 人类校验指引（Manual Acceptance）

除回归测试外，每条 AC 须可手动验收。
**环境**：`uvicorn web.main:app --port 8000`；浏览器开 `/ui`；ollama 本地 two-sum 类模型可达。

| AC | 人类校验步骤 | 通过判定 | 失败判定 |
|----|------------|---------|---------|
| PF-01（信息性） | 切「速度优先」生成简单题 vs 「质量优先」→ 看耗时与终态 `used_model` | 两模式首试模型分别为 local / minimax（硬门以 stub 回归为准） | 两模式首试模型相同 |
| PF-02 | 用**已拉取/缓存**的 two-sum 题（slug 命中白名单，`multi_answer` 由生产者自动置 True，见 §1 multi_answer 生产者）生成 → 看 verify 结果 | 正确解判 pass，不因索引顺序误判 | 正确解被判 `verified_fail` |
| PF-03 | 拉取/读取 two-sum 题后，查看其题录 JSON 或生成日志中的 `multi_answer` 字段 | `multi_answer=True`（无需手改题录） | 字段缺失或为 False |
| PF-FE | P1-5 面板切开关 → 终态 `used_model` 随开关变 | 速度=local / 质量=minimax，面板同步 | 面板不随开关变 |

---

## 8. 附带修正项（non-blocking，留给 Dev）

> 下列为代码侧/其他 spec 的陈旧引用修正，**不阻塞本任务验收**，记录于此供 Dev 顺带处理；本任务**不改** observability spec 其他内容、不改动 dev 代码以外的文件。

1. `features/solver/service.py`（约行 51）`generate_for_problem` 的 docstring 陈旧写成 `web/routes/go_code.py` 为 FastAPI 层入口；应改为 `web/routes/problems.py`（与本文 §3.1 一致）。
2. `specs/observability/OBSERVABILITY_SPEC.md` 同样误指 `web/routes/go_code.py` 的 `_do_generate`（`_do_generate` 实际在 `web/routes/problems.py`）及其 §5 引用的 `test_realtime_panel_contract.py` 与本文共用；需一并校正（仅记录，本任务不改 observability spec）。
