# 验收报告 · 分支 `feat/custom-problems`（custom-questions 功能）

> 交付编排：code-engine-team（主理人）→ PM 验收评审 + Dev 质量门验证
> 分支：`feat/custom-problems`　提交：`dc7b80b feat(custom-questions): implement custom question pipeline with dedup precheck and isolated storage`
> 相对 `main`：1 个提交，17 个文件改动，工作树干净

## 一、功能概要

本分支交付了一条**非 LeetCode 的自由文本题目输入链路**：

自定义问题进入 → **Agent（LLM）去重预检**（`precheck.py`）→ 命中则发确认、否则按 `classifier` 结果走代码生成（产 Go）或问答（不产 Go）→ 落盘到与 LeetCode 题库**物理隔离**的 `output/custom-questions/`（带 `source:"custom"` 标记，独立 `C-<seq>` 单调编号，永不写 `problems_index.json`）→ 通过独立 FastAPI 资源 `/api/custom-questions` 暴露。

## 二、验收结论

| 维度 | 负责人 | 结论 |
|------|--------|------|
| 产品验收（AC 映射） | PM（code-engine-pm） | ✅ **Accept with conditions（有条件通过）**——15 条 AC 全部 PASS |
| 质量门（回归测试） | Dev（code-engine-dev） | ✅ **PASS**——3 套件 23/23 全绿，0 失败 0 错误 |

**合并建议：批准合入 `main`，并建跟踪项闭环下列非阻断条件。**

## 三、验收标准（AC）核对结果

15 条 AC（CQ-01~06 + CK-01~09）全部有实现对应，且每条至少映射 1 个回归用例，功能判定均 PASS：

- **CQ-01** 自定义编程题走原路径产 Go ✅
- **CQ-02** 自定义非编程题回到问答、不产 Go ✅
- **CQ-03** Agent 去重预检 + 命中发确认 + 复用/不相关分流 ✅（not_related 分支仅间接覆盖，见条件 2）
- **CQ-04** 自定义问题独立存储（`source:"custom"`，不进 `problems_index.json`）✅
- **CQ-05** 非交互 / `--no-confirm` 不阻塞 ✅
- **CQ-06** 不存在则新建并编号（`C-<seq>` 隔离、单调）✅
- **CK-01~09** 预检匹配/不匹配、LLM 损坏降级、命中发确认不启 solver、确认复用不新建、独立存储、单调编号、headless 不阻塞、LeetCode 引用跳过预检 ✅（全部 PASS）

## 四、质量门验证（Dev 实测）

环境：系统 Python 3.14.7 + Go 1.26.5（真实编译，`verify_mode="off"`）；LLM 由测试内 `mock.patch` 桩掉。

| 测试文件 | 用例数 | 结果 |
|----------|--------|------|
| `test_verifier_regression.py` | 8 | ✅ 8/8（52.6s） |
| `test_custom_check_regression.py` | 11（CK-01..09） | ✅ 11/11（16.8s） |
| `test_custom_questions_regression.py` | 4（CQ-01/02/04/06） | ✅ 4/4（11.0s） |
| **合计** | **23** | **✅ 23/23 全绿** |

连通性：5 个 Web 路由均注册且 import 正常；TestClient 真机冒烟（真实 Go + 桩 LLM，临时目录）跑通 `POST /api/custom-questions`（`no_confirm=true` → 200 / `created` / `C-0001`，Go 真实编译通过）→ list → open 全链路。冒烟产物已清理，`git status` 为空。

## 五、非阻断性闭环项（建议 Dev 闭环，不阻塞合并）

来自 PM 验收评审：
1. **文档一致性**：修正 `specs/custom-questions/CUSTOM_QUESTIONS.md §6.2` 的 mermaid，对齐 CHECK_SPEC §2「预检在前、覆盖全部自定义问题」的顺序（裁决 R1）。
2. **测试闭合**：补一条显式回归，断言 `confirm_custom_question(decision="not_related")` 走新建并生成 `C-<seq>` 记录（闭合 G1 / CQ-03 第三分支）。
3. **代码整洁**：`custom_storage.py:23` 的 `__import__("re")` 改为顶层 `import re`（闭合 O1）。
4. **可选**：若确需 HTTP 202 表达「需确认」，再调 Web 路由；当前 200 + `needs_confirm` 语义等价（裁决 R2）。

来自 Dev 实现侧观察（均与 spec 措辞的微小偏差，不影响 PASS）：
- CK-04 HTTP 状态码：spec 举例「如 202」，实现返回 200 + `needs_confirm` 负载，行为正确。
- CQ-05 / CK-08 headless 命中语义：实现在 `match + no_confirm` 时**新建一条自定义记录**（而非复用命中题），已被 `test_ck08` 显式编码为「新建」，属合理取舍——若 PM 期望 headless 命中=复用，需调整 `service.generate_custom_question`（L132-144）。
- `preecheck.py`（CHECK_SPEC §3 建议名）vs 实现 `precheck.py`（全仓一致，纯命名差异）。

## 六、需求歧义裁决（PM）

- **R1 预检顺序**：以 CHECK_SPEC §2 为准（预检在前、对所有自定义问题生效），实现相符；修正 CUSTOM_QUESTIONS.md mermaid。
- **R2 确认态 HTTP 语义**：以可测 AC（CK-04）为权威，200 + `needs_confirm` 等价，保留现状。
- **R3 前端确认 UI 延后**：spec 已标 `[scope expansion]`、PROGRESS 已记录「延后 W2」，属已拍板范围决策，不计入缺口。

---
*本报告由 code-engine 交付编排主理人汇编，依据 PM 验收评审与 Dev 质量门验证两份成员产出。*
