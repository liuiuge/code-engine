// content.js — 注入到每个页面。
// 职责：
//   1. 记住最近聚焦过的可编辑元素（聊天框）。
//   2. 收到快捷键信号后，读取当前选中文字。
//   3. 把文字交给 background 去本地后端生成，拿到代码后写入目标输入框。
//   4. 可选：状态提示（toast）由选项页「显示提示」开关控制，默认关。
(function () {
  'use strict';

  let lastFocusedEditable = null;
  let showHints = false; // 选项页「显示提示」开关，默认关（不弹 toast）。
  let humanInput = false; // 选项页「拟人输入」开关，默认关（一次性写入）。
  let writingBusy = false; // 处理全程占锁（生成 20-30s + 写入），防重复触发串写。

  function isEditable(el) {
    if (!el || !el.tagName) return false;
    const tag = el.tagName;
    if (tag === 'TEXTAREA' || tag === 'INPUT') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      return !['submit', 'button', 'checkbox', 'radio', 'file', 'image', 'reset', 'hidden'].includes(type);
    }
    return el.isContentEditable === true;
  }

  document.addEventListener('focusin', (e) => {
    if (isEditable(e.target)) lastFocusedEditable = e.target;
  });

  // ---- 快捷键（可在扩展选项页配置；页面内 keydown 捕获，绕过 Chrome 对 Ctrl+Alt 的禁用）----
  // 组合键用 {ctrl,alt,shift,code} 描述；code 取 KeyboardEvent.code（物理键，布局无关）。
  const DEFAULT_SHORTCUT = { ctrl: true, shift: true, alt: false, code: 'KeyY' }; // Ctrl+Shift+Y
  let currentShortcut = DEFAULT_SHORTCUT;

  function shortcutMatches(e) {
    const s = currentShortcut;
    if (!s) return false;
    return (
      e.code === s.code &&
      !!e.ctrlKey === !!s.ctrl &&
      !!e.altKey === !!s.alt &&
      !!e.shiftKey === !!s.shift
    );
  }

  async function loadShortcut() {
    try {
      const v = await chrome.storage.sync.get('shortcut');
      if (v && v.shortcut) currentShortcut = v.shortcut;
    } catch (e) {
      /* 用默认 */
    }
  }
  loadShortcut();

  // 提示 / 拟人输入开关：读选项页配置；默认都关。
  async function loadFlags() {
    try {
      const v = await chrome.storage.sync.get({ showHints: false, humanInput: false });
      showHints = !!v.showHints;
      humanInput = !!v.humanInput;
    } catch (e) {
      /* 默认关 */
    }
  }
  loadFlags();

  // 设置页改了快捷键 / 提示 / 拟人输入，已打开的页面无需刷新立即生效。
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'sync') {
      if (changes.shortcut) currentShortcut = changes.shortcut.newValue || DEFAULT_SHORTCUT;
      if (changes.showHints) showHints = !!changes.showHints.newValue;
      if (changes.humanInput) humanInput = !!changes.humanInput.newValue;
    }
  });

  document.addEventListener(
    'keydown',
    (e) => {
      if (shortcutMatches(e)) {
        e.preventDefault();
        e.stopPropagation();
        generateAndInject();
      }
    },
    true // 捕获阶段，先于页面内输入框处理，便于抢键。
  );

  function firstVisibleEditable() {
    const nodes = document.querySelectorAll(
      'textarea, input, [contenteditable="true"], [contenteditable=""]'
    );
    for (const el of nodes) {
      if (!isEditable(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) return el;
    }
    return null;
  }

  // 目标优先级：当前聚焦且未处于「正在选中其内部文字」的可编辑元素 >
  // 最近聚焦过的可编辑元素 > 当前聚焦元素 > 页面首个可见可编辑元素。
  function resolveTarget() {
    const active = document.activeElement;
    const sel = window.getSelection();
    const selInActive = !!(sel && active && active.contains(sel.anchorNode));
    if (isEditable(active) && !selInActive) return active;
    if (isEditable(lastFocusedEditable) && lastFocusedEditable !== active) return lastFocusedEditable;
    if (isEditable(active)) return active;
    return firstVisibleEditable();
  }

  // 去掉每一行首部的缩进，保持聊天框内代码紧凑（Go 不依赖缩进编译）。
  function stripIndent(text) {
    return String(text)
      .split('\n')
      .map((line) => line.replace(/^\s+/, ''))
      .join('\n');
  }

  // 去掉 Markdown 代码围栏（```go ... ```），只把纯代码写入聊天框。
  function stripFence(text) {
    return String(text)
      .replace(/^\s*```[^\n]*\n/, '') // 开头围栏 + 语言标识整行
      .replace(/\n?```\s*$/, ''); // 结尾围栏（含前后空白/换行）
  }

  // ---- 拟人写入：一次一个单词 / 一个简单短行，1 秒一个 input 事件 ----
  // 逐字符一次塞上千个 input 事件会把页面卡死；改成分块慢写，模拟「人在打字」。
  const HUMAN_INTERVAL_MS = 1000; // 每个 input 事件间隔（毫秒）
  const SIMPLE_WORD_MAX = 5; // 一行 ≤5 个词视为「简单行」整行一次；更长按词拆

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // 把文本切成拟人节奏的块：短行整行一块；长行按「单词 + 随后的空白」一块。
  function humanChunks(text) {
    const chunks = [];
    const lineTokens = String(text).match(/[^\n]*\n|[^\n]+$/g) || [];
    for (const tok of lineTokens) {
      const hasNL = tok.endsWith('\n');
      const content = hasNL ? tok.slice(0, -1) : tok;
      if (content === '') {
        chunks.push('\n'); // 空行单独一块
        continue;
      }
      const words = content.match(/\S+\s*/g) || [];
      if (words.length === 0) {
        chunks.push(content + (hasNL ? '\n' : ''));
      } else if (words.length <= SIMPLE_WORD_MAX) {
        chunks.push(content + (hasNL ? '\n' : '')); // 简单短行整行一次
      } else {
        for (let i = 0; i < words.length; i++) {
          chunks.push(words[i] + (i === words.length - 1 && hasNL ? '\n' : ''));
        }
      }
    }
    return chunks;
  }

  function valueSetter(el) {
    const proto =
      el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    return Object.getOwnPropertyDescriptor(proto, 'value');
  }

  // Use the native setter so React/Vue controlled inputs observe the same value
  // transition as a real edit.  Do not emit an event here: humanInput emits one
  // event per chunk, while the regular path emits exactly one pair of events.
  function setNativeValue(el, value) {
    const desc = valueSetter(el);
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
  }

  // Append through the element's editing API when available.  The input event
  // is intentionally deferred until all chunks are written: some controlled
  // chat editors treat every synthetic input event as an insertion and append
  // the complete current value again.
  function appendNativeValue(el, value, chunk) {
    const next = value + chunk;
    // A synchronous controlled-input handler may already have committed this
    // chunk while processing the previous event.  Treat that as success.
    if (el.value === next) return;
    if (el.value !== value) setNativeValue(el, value);
    if (typeof el.setRangeText === 'function') {
      const end = value.length;
      try {
        el.setSelectionRange(end, end);
        el.setRangeText(chunk, end, end, 'end');
        return;
      } catch (_e) {
        // Some input types (for example number) do not support setRangeText.
      }
    }
    setNativeValue(el, next);
  }

  function dispatchInput(el, data) {
    // A final commit uses the plain bubbling event expected by React's value
    // tracker.  InputEvent is reserved for genuine chunk-level notifications.
    if (data == null) {
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return;
    }
    // InputEvent is understood by sites that inspect inputType/data.  The
    // fallback keeps the extension usable in older WebViews and test DOMs.
    let event;
    try {
      event = new InputEvent('input', {
        bubbles: true,
        inputType: 'insertText',
        data,
      });
    } catch (_e) {
      event = new Event('input', { bubbles: true });
    }
    el.dispatchEvent(event);
  }

  function setNativeValueAndNotify(el, value) {
    setNativeValue(el, value);
    dispatchInput(el, null);
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // 拟人分块写入（仅 textarea/input；默认关闭，需选项页开启）。
  // 按块推进：每次只更新一个新的增量；全部完成后只派发一次 input/change。
  // 注意：合成 input 事件在富文本编辑器 / 某些站点会被当作「整段插入」反复合并
  // （会把同一段代码重复追加几十遍），所以 contenteditable 一律走一次性写入。
  async function typeHumanly(el, text) {
    el.focus();
    let acc = el.value || '';
    const chunks = humanChunks(text);
    for (let i = 0; i < chunks.length; i++) {
      const chunk = chunks[i];
      const before = acc;
      acc += chunk;
      // Keep the visible value changing at the human-like pace, but do not
      // notify the page for every intermediate prefix (see above).
      appendNativeValue(el, before, chunk);
      if (i < chunks.length - 1) await sleep(HUMAN_INTERVAL_MS);
    }
    // setRangeText updates the visible DOM value but bypasses framework value
    // trackers.  Re-apply the completed value through the native setter before
    // the single notification so React/Vue commit it without a real keystroke.
    setNativeValue(el, acc);
    dispatchInput(el, null);
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // 默认「追加」而非「覆盖」，避免冲掉用户已有的提示词；目标为空时等同覆盖。
  // 写入前先去围栏、逐行去缩进。
  // humanInput=关（默认）：一次性写入（稳定）；humanInput=开：textarea/input 拟人分块慢写。
  async function appendCode(target, code) {
    const cleaned = stripIndent(stripFence(code));
    if (target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement) {
      const sep = target.value && !target.value.endsWith('\n') ? '\n' : '';
      if (humanInput) {
        await typeHumanly(target, sep + cleaned);
      } else {
        setNativeValueAndNotify(target, target.value + sep + cleaned);
      }
    } else {
      target.focus();
      target.appendChild(document.createTextNode('\n' + cleaned));
      target.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  let toastEl = null;
  let toastTimer = null;
  function showToast(msg, kind) {
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.id = '__codeengine_toast';
      toastEl.style.cssText =
        'position:fixed;left:50%;bottom:32px;transform:translateX(-50%);' +
        'z-index:2147483647;max-width:80vw;padding:10px 16px;border-radius:8px;' +
        'font:14px/1.4 system-ui,-apple-system,Segoe UI,sans-serif;color:#fff;' +
        'box-shadow:0 4px 16px rgba(0,0,0,.25);pointer-events:none;opacity:0;' +
        'transition:opacity .25s;';
      (document.body || document.documentElement).appendChild(toastEl);
    }
    const bg = kind === 'error' ? '#c0392b' : kind === 'warn' ? '#b9770e' : '#1f6f43';
    toastEl.style.background = bg;
    toastEl.textContent = msg;
    toastEl.style.opacity = '1';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toastEl.style.opacity = '0';
    }, 2800);
  }

  function getSelectionText() {
    const s = window.getSelection && window.getSelection();
    return s ? s.toString() : '';
  }

  async function generateAndInject() {
    // 全程占锁：生成（20-30s）+ 可能的拟人写入都算 busy。
    // 若不加锁，重复按键 / 键自动重复会在生成阶段并发触发多个写入，把同一段代码写多遍。
    if (writingBusy) {
      if (showHints) showToast('正在处理上一条，请稍候…', 'warn');
      return;
    }
    writingBusy = true;
    try {
      const text = getSelectionText().trim();
      if (!text) {
        if (showHints) showToast('未选中任何文字', 'warn');
        return;
      }
      const target = resolveTarget();
      if (!target) {
        if (showHints) showToast('当前页面没有可写入的聊天框', 'error');
        return;
      }
      if (showHints) showToast('正在生成代码…');
      let res;
      try {
        res = await chrome.runtime.sendMessage({ type: 'request-generate', text });
      } catch (e) {
        if (showHints) showToast('通信失败：' + (e && e.message), 'error');
        return;
      }
      if (!res || res.error) {
        if (showHints) showToast('生成失败：' + ((res && res.error) || '未知错误'), 'error');
        return;
      }
      await appendCode(target, res.code);
      if (showHints) showToast('已写入聊天框');
    } finally {
      writingBusy = false;
    }
  }

})();
