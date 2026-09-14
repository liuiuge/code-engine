# CodeEngine Inject（浏览器插件）

把本地 `code-engine` 变成一个浏览器插件：选中题目文字 → 按快捷键（**默认 `Ctrl+Shift+Y`，可在扩展选项页修改**）→
插件调用本地后端生成 Go 代码 → 自动写入当前页面的聊天框（textarea / input / contenteditable）。

> 快捷键改为「页面内 keydown 捕获」实现，不再依赖 Chrome 的 `commands` API，
> 因此绕开了 Chrome 对 `Ctrl+Alt+<键>` 的禁用（与 AltGr 冲突）。想要 `Ctrl+Alt+,`
> 也能在选项页里设；默认用 `Ctrl+Shift+Y` 只是因为它在 Edge 里不冲突。

## 架构

```
Ctrl+Shift+Y（可配置） ──►  content.js (当前页 keydown 捕获)
                              │ 读取 window.getSelection()
                              │ 把选中文字发给 background
                              ▼
                          background.js (service worker)
                              │ POST http://localhost:8000/api/custom-questions {text, no_confirm:true}
                              │ 取 result.final_output（Go 代码）
                              ▼
                          content.js
                              │ 写入最近聚焦的聊天输入框（追加，兼容 React 受控组件，无界面提示）
```

为什么 fetch 放在 background 而不是 content script：
当前页若是 https（如 ChatGPT），content script 直接 `fetch http://localhost`
会被浏览器按「混合内容」拦截；background service worker 发起的请求不受此限制。

## 加载步骤

1. 打开 `chrome://extensions`（Edge 为 `edge://extensions`）。
2. 右上角开启「开发者模式」。
3. 点「加载已解压的扩展程序」，选择本目录 `browser-extension/`。
4. 在扩展管理页点「扩展选项」可设置后端地址与快捷键。

## 用法

1. 确保本地后端在跑：`uvicorn web.main:app --port 8000`（且 Ollama 可用）。
2. 在网页上**选中**一段题目 / 需求文字。
3. 先点一下目标聊天框让它获得焦点（插件默认写入「最近聚焦过的可编辑元素」）。
4. 按快捷键（默认 `Ctrl+Shift+Y`）：插件静默调用本地后端生成代码，成功后直接写入聊天框，过程中无任何界面提示。

## 选项（扩展选项页）

- **后端地址**：默认 `http://localhost:8000`。
- **代码生成模型路由**：默认 `quality`（coder/fixer 首次即用在线 `gemma4:cloud`）；可切 `speed`（本地优先，编译失败 1 次后升级到 `gemma4:cloud`）；也可直接选 `gemma4:cloud`。后端 `models.yaml` 的 `default` 与 `routing.escalate_to` 现已指向 `gemma4:cloud`，即「初始版本」默认在线模型就是 gemma4；`minimax-m3:cloud` 仍注册在册但不再作为默认/升级目标。新增在线模型只需在 `models.yaml` 的 `models:` 下加一条、并把名字作为本下拉项即可。
- **显示状态提示（toast）**：开关，默认**关**。开启后，做题过程中会弹出轻量提示（未选中文字 / 无聊天框 / 正在生成 / 通信失败 / 生成失败 / 已写入）；关闭时全程静默，仅把代码写入聊天框。开关在已打开的页面立即生效。
- **拟人输入（慢速逐词写入）**：开关，默认**关**（一次性写入，稳定）。开启后对 textarea/input 以「1 秒一个 input 事件、一次一个单词或简单短行」的拟人节奏写入，用于规避站点对「瞬间整段粘贴」的识别；contenteditable 不受此开关影响（仍一次性）。如出现内容被重复追加的现象，关闭此项。
- **快捷键**：点「点击录制」后直接按下组合键即可保存；「恢复默认」回到 `Ctrl+Shift+Y`。
  组合键在页面内捕获，修改后立即对新页面生效（已打开的页面需刷新一次）。

## 注入目标判定（优先级）

1. 当前聚焦的可编辑元素，且选中文字不在它内部；
2. 最近聚焦过的可编辑元素（即聊天框，最常用的情况）；
3. 当前聚焦元素；
4. 页面首个可见可编辑元素。

写法是**追加**而非覆盖，避免冲掉你已有的提示词。写入时会先去掉 Markdown 代码围栏（```go … ```），再**逐行去除缩进**。默认**一次性写入**（稳定）；勾选选项页「**拟人输入**」（默认关）后，对 textarea/input 改为**拟人分块慢写**：每 1 秒写入一个单词；≤5 词的简单短行（如 `package main`、`}`、空行）整行一次写入。写入过程中只逐块更新输入框的可见值，全部完成后才派发一次 `input/change`，避免受控输入框把每次事件中的完整前缀重复追加。注意：contenteditable / 富文本编辑器对合成 input 的合并行为不可靠（会把整段内容重复追加几十遍），因此即使开启拟人输入，contenteditable 也仍走一次性写入。节奏参数在 `content.js`：`HUMAN_INTERVAL_MS`、`SIMPLE_WORD_MAX`。如需改成覆盖、保留围栏或保留缩进，改 `content.js` 的 `appendCode` / `stripFence` / `stripIndent`。

## 已知限制

- 需要后端在线且能联网到 Ollama；生成失败时为静默失败（不弹提示，仅后台不写入）。
- `chrome://`、`chrome-extension://` 等页面 content script 不注入，快捷键无效。
- 快捷键是页面内捕获，若当前网页自己占用了同一组合键，可能被该网页抢先处理。

拟人输入的离线回归不依赖浏览器、后端或 Ollama，可运行 `node browser-extension/test_human_input.js` 验证受控输入框不会重复追加。
