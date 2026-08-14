# 交付报告 · 查重匹配提示词收紧（Invert Binary Tree ≠ Same Tree）

> 交付编排：code-engine-team（主理人）→ PM 定匹配口径 + Dev 改提示词补测试
> 触发：用户实测「用 go 二叉树反转」被查重误判命中「same-tree」
> 时间：2026-08-14

## 一、问题根因
`prompts/problem_match.md` 原文案「essentially the SAME problem... Judge by meaning」让 LLM 按**主题/数据结构相似度**判等。Invert Binary Tree(226，反转左右子树) 与 Same Tree(100，比较两树相等) 都涉及二叉树比较，被误判为重复。PM 核实：CK-01..09 只定义了「命中/未命中/降级」机制，**从未定义「什么算同一题」的语义边界**——这是真缺口。

## 二、PM 定的匹配口径
判 `match` 当且仅当**同操作 + 同输入结构 + 同目标/输出**三者全重合；任一不同（即便同数据结构/同主题）必须 `no_match`。边界判据：「A 的正确解能否不加任何逻辑直接解 B？」能→match，否→no_match。保守默认 no_match。
- 反例（必 no_match）：Invert(226) ≠ Same Tree(100)；「反转/翻转」≠「比较相等」。
- 正例（应 match）：二叉树反转/翻转二叉树/invert a binary tree 互 match；「用 go」前缀只是语言约束，不改题。

## 三、Dev 实现与质量门
**改动文件（2 个，均未动 `precheck.py` 逻辑与 `specs/`）**
- `prompts/problem_match.md`：判定段改为「SAME OPERATION + SAME KIND OF INPUT + SAME KIND OF OUTPUT」；补硬规则「不同操作于同结构 = 不同题 → no_match」；补 3 条 few-shot（负：二叉树反转 vs same-tree→no_match；正：翻转二叉树 vs invert-binary-tree→match；正：用 go 判断括号是否合法 vs valid-parentheses→match）。JSON 契约不变。
- `features/solver/tests/test_custom_check_regression.py`：新增 CK-10 / CK-10a / CK-10b 三个用例（stub LLM 断言负例 no_match、正例 match + 正确 slug）。

**测试结果（系统 Py3.14）**
| 套件 | 结果 |
|------|------|
| `test_custom_check_regression.py` | ✅ 14/14（原 11 + 新 3） |
| `test_verifier_regression.py` | ✅ 8/8 |
| `test_custom_questions_regression.py` | ✅ 4/4 |
| **合计** | **✅ 26/26 全绿**（原 23 全绿 + 3 新） |

## 四、真机验证（真实 ollama，全新 Python 进程）
本机 ollama(:11434) 可达，目录含 `same-tree`(100)、不含 `invert-binary-tree`(226)，复现用户场景。改后提示词实际返回：
```
{"status": "no_match", "matched_slug": null,
 "reason": "Inverting/Reversing nodes (swapping children) is different from checking if two trees are structurally identical."}
```
→ 「二叉树反转」不再误判命中 same-tree，且 reason 按**操作**区分。对照：`判断括号是否合法`→ 正确 match `valid-parentheses`（未误伤真重复）。

## 五、迭代中发现的关键坑（Dev 反馈，已采纳）
第一版「严格 guardrail（MUST NOT match）」措辞在**小模型**(qwen3.5-lowvram:9b) 上过度保守，**把真重复也误杀**（valid-parentheses 也被拒）。最终版改为「以操作为中心 + few-shot 正反例驱动、去掉绝对化措辞」，两方向同时正确。教训：对小模型，提示词严格度需与模型能力平衡，必须给「同操作异语言仍 match」的正例。

## 六、给你的操作提示
`infrastructure/config.py` 在**模块导入时**一次性读 `prompts/problem_match.md` 进内存。你正在跑的 `:8000` 服务缓存的是**旧**提示词——必须**重启 `:8000` 服务**（重新加载进程）才能在 UI 看到修复，仅改文件不重启无效。重启后于 UI 输入「用 go 二叉树反转」即可验证不再匹配 same-tree。

## 七、待办（可选）
- 将 CK-10 正式写入 `specs/custom-questions/CHECK_SPEC.md`（PM 已草拟 Given/When/Then，目前由回归用例承载，spec 文档未同步）。
- 本地创建/提交分支（Dev 未跑 git）。
