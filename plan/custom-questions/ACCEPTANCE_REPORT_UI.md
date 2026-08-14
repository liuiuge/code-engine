# 交付报告 · 「自定义题目」前端 Tab（分支 feat/custom-questions-ui）

> 交付编排：code-engine-team（主理人）→ PM 产品设计 + Dev 实现，质量门并行的完整交付
> 关联：后端分支 `feat/custom-problems`（23/23 已验收）；本前端为其补齐**用户可达入口**
> 时间：2026-08-14

## 一、背景与缺口
后端 custom-questions 能力 100% 完成并通过验收，但 `frontend/index.html` 仅有「题目」「Go 代码」两个 tab，**零处调用 custom-questions 路由**——用户无法从 UI 输入/验证，是 spec 规划遗漏导致的需求链断裂（非 W2 延后项）。

## 二、交付内容
| 类型 | 文件 | 说明 |
|------|------|------|
| 前端实现 | `frontend/index.html` | 新增「自定义题目」tab：输入表单（`text` + 默认不勾选的 `no_confirm`）、内嵌确认面板（复用/不相关/取消）、列表（裸数组、不分页、空态引导）、详情（编程/非编程双分支 + Go 代码跳转）、长耗时加载态、XSS 转义。对现有切换/`detail-back` 逻辑零改动 |
| 测试基建 | `web/tests/test_custom_questions_ui_contract.py`（22 用例） | 静态断言 + FastAPI TestClient 契约 + Node DOM 垫片跑真实前端 JS（CU-01~CU-18） |
| 测试基建 | `web/tests/_ui_harness.js` | 环境无 jsdom/playwright 时自建最小 DOM+fetch 垫片 |
| 新建 spec | `specs/custom-questions/CUSTOM_QUESTIONS_UI.md` | 完整 UI 产品规格：缺口/设计/CU-01~18/范围/决策/真实契约附录 |
| 修订 spec | `CUSTOM_QUESTIONS.md §6.3`、`CHECK_SPEC.md §1`、`PHASE1_PLAN.md` | 解除「确认弹窗 UI [scope expansion]」并指向 UI spec；新增风险「后端已交付但 UI 无入口」固化流程教训 |

## 三、质量门结果
| 测试 | 结果 |
|------|------|
| 新 UI 契约测试 `web/tests/test_custom_questions_ui_contract.py` | ✅ **22/22 通过**（静态 6 + 后端契约 4 + 前端运行时 12） |
| 现有回归 `test_verifier_regression.py` | ✅ 8/8 |
| 现有回归 `test_custom_check_regression.py` | ✅ 11/11 |
| 现有回归 `test_custom_questions_regression.py` | ✅ 4/4 |
| **合计** | **✅ 45/45 全绿**（22 新 + 23 回归，后端未改动无倒退） |

## 四、PM 纠正的关键契约（已落入实现，避坑）
- **E1** 请求体字段名是 `text`（非 `question`）——CU-05 机械断言防守。
- **E2** 确认判定依据 `status==="needs_confirm"`（precheck 无 `needs_confirm` 字段）——CU-08 防守。
- **E3** 列表接口返回**裸数组**——CU-13 防守（不读 `.items`/`.total`，免白屏）。
- **E4** `POST /api/custom-questions` 内部已查重，**单次提交**，前端不再先调 `/precheck`——避免双重 LLM 查重。
- **E5** `problems_dir` 为测试 override，绝不暴露 UI（CU-05 断言不含）。

## 五、D4 核实结论
`task_name == basename(task_dir)` 成立 → 详情页「查看生成的 Go 代码」做成**真实跳转链接**（用 `rec.task_dir` 为 `task_name`，并兜底从 `code_path` 父目录名取），非降级文本。

## 六、实现说明（Dev 偏离/补充）
1. precheck 桩目标修正为 `features.solver.precheck.precheck_custom_question`（实际定义处；`service` 仅 re-import）——与 PM 描述偏差，已按真实符号修正。
2. `init()` 末尾额外调了一次 `loadCustom()`（与 problems/gocode 列表对称加载）；若 PM 要求严格只在提交后刷新、tab 初次打开空白，可去掉该行。
3. 前端运行时测试用 Node 垫片而非真实浏览器（环境限制），覆盖项不受影响。
4. 未做任何 git 写操作；分支 `feat/custom-questions-ui` 由用户本地创建并提交。

## 七、验收结论
**✅ Accept（通过）**。前端补齐了后端能力唯一缺失的「用户可达入口」，18 条 UI AC（CU-01~CU-18）全部由测试覆盖；现有 23 回归不倒退；关键契约陷阱（字段名/确认分流/裸数组/单次提交）均已在设计与测试层闭合。

## 八、待办（用户本地）
- 创建分支 `feat/custom-questions-ui` 并提交本次前端 + spec 改动（Dev 未跑 git）。
- `web/routes/custom_questions.py` docstring L11「confirmation popup deferred to W2」表述过期，建议顺手更正（PM 已在 UI spec 标注）。
- 如需严格遵循「tab 初次空白」，移除 `init()` 中 `loadCustom()` 调用（说明 2）。
