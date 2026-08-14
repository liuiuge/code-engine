# 自定义题目 · Go 代码跳转按钮 `goTask is not defined` 修复报告

- **日期**：2026-08-14
- **分支**：`feat/custom-problems`（前端修复随 Phase B 的 UI tab 一起合入，由用户本地提交）
- **严重度**：浏览器端阻断（UI 报错，无网络调用）—— 不影响后端数据，仅阻断"查看生成的 Go 代码"这一前端跳转
- **状态**：✅ 已修复并验证（23 UI 契约 + 26 后端回归全绿）

---

## 1. 现象（用户反馈）

```
ui/:542 Uncaught ReferenceError: goTask is not defined
    at HTMLButtonElement.<anonymous> (ui/:542:59)
```

生成完成后，点击"查看生成的 Go 代码"按钮报错，**没有产生任何网络调用**——
即按钮点了没反应、且 openGoCode 从未被触发。

## 2. 根因

`frontend/index.html` 的 `openCustom()` 中，`goTask` 在 `if (rec.code_path) { ... }`
代码块内用 `const` 声明（原约 531 行），但在块外的点击监听器里引用
（约 542 行 `tg.addEventListener('click', () => openGoCode(goTask))`）：

```js
if (rec.code_path) {
  const goTask = rec.task_dir || (...);   // 块级作用域
  ...
}
const tg = $('c-to-gocode');
if (tg) tg.addEventListener('click', () => openGoCode(goTask));  // ❌ 块外引用 → ReferenceError
```

`const`/`let` 是块级作用域，`goTask` 在 `if` 块外不可见 → 点击时抛出
`ReferenceError: goTask is not defined`，监听器回调在声明前即越界，openGoCode 不执行、
`/api/go-code/<task_name>` 请求从不发生。

> 注意：这不是后端 bug。后端 `custom-questions/<number>` 返回的 `code_path` /
> `task_dir` 一直正确；问题纯在前端 JS 作用域。

## 3. 修复

把 `goTask` 提升到 `openCustom()` 函数作用域，块内改为**赋值**而非声明：

```js
async function openCustom(number) {
  ...
  let goTask = null;                                  // ✅ 提升到函数作用域
  ...
  if (rec.code_path) {
    ...
    goTask = rec.task_dir || (rec.code_path.split('/').slice(-2, -1)[0] || '');  // ✅ 赋值
    if (goTask) {
      html += '<div class="block"><button class="btn" id="c-to-gocode">查看生成的 Go 代码 →</button></div>';
    }
  }
  ...
  const tg = $('c-to-gocode');
  if (tg) tg.addEventListener('click', () => openGoCode(goTask));  // ✅ 现在在作用域内
}
```

- 文件：`frontend/index.html`（约 524 / 532 / 543 行）
- 改动极小、局部、零副作用：非编程题分支（`else`）仍不渲染空编译区块；
  `goTask` 为 `null` 时不渲染按钮，行为不变。

## 4. 回归护栏（防复发）

新增 **CU-19** `test_cu19_gocode_link_clickable`，在 Node DOM + fetch 仿真
（`web/tests/_ui_harness.js`，真实跑 `index.html` 的 `<script>`）中：

1. 加载一个带 `code_path` 的编程题记录 → 断言 `#c-to-gocode` 按钮与监听器渲染成功；
2. **模拟点击**按钮 → 断言：
   - 不抛 `goTask is not defined`（`threw` 必须为 false）；
   - `openGoCode` 确实被调用且收到 `task_name=custom_task_x`
     （即 `/api/go-code/custom_task_x` 网络请求会发生）。

**负向验证**：Dev 把修复还原回 `const goTask` 复现该 bug，CU-19 立即变红
（`threw=true`）—— 证明该用例能拦住同样的回归，不是"装饰性绿"。

## 5. 质量门（本次交付）

| 套件 | 用例 | 结果 |
| --- | --- | --- |
| `web/tests/test_custom_questions_ui_contract.py` | 23（CU-01..CU-19，含静态+后端契约+前端运行时） | ✅ 全绿 |
| 后端回归（V-01..08 + CK-01..09,10,10a,10b + CQ-01..06） | 26 | ✅ 全绿 |
| **合计** | **49** | ✅ 全绿 |

> 运行时：系统 Python 3.14（`C:\Users\ltyal\AppData\Local\Programs\Python\Python314\python.exe`）。
> 受管 venv 3.13.x 缺 `langgraph`，不可用于回归套件。

## 6. 用户侧动作（必做）

前端是**静态文件**，运行中的服务不会自动热更。要让修复生效：

- **重启 `:8000` 服务**（推荐），或
- 浏览器**硬刷新**（Ctrl/Cmd+Shift+R 清缓存）重新拉取 `frontend/index.html`。

否则仍会加载到旧的、含 bug 的 `index.html`，点击按钮继续报 `goTask is not defined`。

## 7. 范围与边界

- 仅改前端 `index.html` 的作用域；后端 `custom_questions.py` / `custom_storage.py` / 匹配提示词
  均**未改动**（上一阶段已稳定）。
- 遵守仓库规约：**Dev 未执行任何 git 写命令**；分支与提交由用户本地完成。
- 关联待办（非本次）：CK-10 系列尚未回填 `specs/custom-questions/CHECK_SPEC.md`（现由回归用例承载）。

---

## 附：本阶段交付物

- 修复：`frontend/index.html`
- 回归护栏：`web/tests/test_custom_questions_ui_contract.py`（+`web/tests/_ui_harness.js`）
- 报告：`plan/custom-questions/UI_GOCODE_BUG_REPORT.md`（本文件）
