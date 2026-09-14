// options.js — 配置后端地址 + 快捷键（持久化到 chrome.storage.sync）。
'use strict';

const DEFAULT_SHORTCUT = { ctrl: true, shift: true, alt: false, code: 'KeyY' };

const urlInput = document.getElementById('url');
const statusEl = document.getElementById('status');
const prefSelect = document.getElementById('pref');
const hintsCheck = document.getElementById('hints');
const humanCheck = document.getElementById('human');
const recBtn = document.getElementById('recBtn');
const resetBtn = document.getElementById('resetBtn');
const shortcutLabel = document.getElementById('shortcutLabel');
const humanTestBtn = document.getElementById('humanTestBtn');
const humanTestOutput = document.getElementById('humanTestOutput');
const humanTestStatus = document.getElementById('humanTestStatus');

const HUMAN_TEST_INTERVAL_MS = 1000;
const HUMAN_TEST_POEM = `Shall I compare thee to a summer's day?
Thou art more lovely and more temperate:
Rough winds do shake the darling buds of May,
And summer's lease hath all too short a date;
Sometime too hot the eye of heaven shines,
And often is his gold complexion dimm'd;
And every fair from fair sometime declines,
By chance or nature's changing course untrimm'd;
But thy eternal summer shall not fade,
Nor lose possession of that fair thou ow'st;
Nor shall death brag thou wander'st in his shade,
When in eternal lines to time thou grow'st:
So long as men can breathe or eyes can see,
So long lives this, and this gives life to thee.`;

// ---------- 后端地址 ----------
chrome.storage.sync.get(
  { backendUrl: 'http://localhost:8000', preference: 'quality', showHints: false, humanInput: false },
  (v) => {
    urlInput.value = v.backendUrl || 'http://localhost:8000';
    prefSelect.value = v.preference || 'quality';
    hintsCheck.checked = !!v.showHints;
    humanCheck.checked = !!v.humanInput;
  }
);
document.getElementById('save').addEventListener('click', () => {
  const value = urlInput.value.trim();
  if (!value) return;
  chrome.storage.sync.set(
    { backendUrl: value, preference: prefSelect.value, showHints: hintsCheck.checked, humanInput: humanCheck.checked },
    () => {
      statusEl.textContent = '已保存';
      setTimeout(() => {
        statusEl.textContent = '';
      }, 1500);
    }
  );
});

// ---------- 模型路由 ----------
prefSelect.addEventListener('change', () => {
  chrome.storage.sync.set({ preference: prefSelect.value });
});

// ---------- 提示开关 ----------
hintsCheck.addEventListener('change', () => {
  chrome.storage.sync.set({ showHints: hintsCheck.checked });
});

// ---------- 拟人输入开关（默认关） ----------
humanCheck.addEventListener('change', () => {
  chrome.storage.sync.set({ humanInput: humanCheck.checked });
});

// ---------- 拟人输入测试 ----------
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function humanTestChunks(text) {
  const chunks = [];
  const lines = String(text).match(/[^\n]*\n|[^\n]+$/g) || [];
  for (const token of lines) {
    const hasNewline = token.endsWith('\n');
    const line = hasNewline ? token.slice(0, -1) : token;
    if (!line) {
      chunks.push('\n');
      continue;
    }
    const words = line.match(/\S+\s*/g) || [];
    if (words.length <= 5) {
      chunks.push(line + (hasNewline ? '\n' : ''));
    } else {
      for (let i = 0; i < words.length; i++) {
        chunks.push(words[i] + (i === words.length - 1 && hasNewline ? '\n' : ''));
      }
    }
  }
  return chunks;
}

async function runHumanTest() {
  humanTestBtn.disabled = true;
  humanTestOutput.value = '';
  humanTestStatus.textContent = '测试进行中…';
  let output = '';
  const chunks = humanTestChunks(HUMAN_TEST_POEM);
  for (let i = 0; i < chunks.length; i++) {
    output += chunks[i];
    humanTestOutput.value = output;
    humanTestOutput.scrollTop = humanTestOutput.scrollHeight;
    if (i < chunks.length - 1) await sleep(HUMAN_TEST_INTERVAL_MS);
  }
  humanTestStatus.textContent = '测试完成';
  humanTestBtn.disabled = false;
}

humanTestBtn.addEventListener('click', runHumanTest);

// ---------- 快捷键 ----------
// code 来自 KeyboardEvent.code；其余取修饰键状态，组合成一个人类可读标签。
function describe(s) {
  if (!s) return '（默认 Ctrl+Shift+Y）';
  const labels = {
    Comma: ',',
    Period: '.',
    Slash: '/',
    Backslash: '\\',
    Space: 'Space',
    Tab: 'Tab',
    Enter: 'Enter',
  };
  let keyLabel;
  if (labels[s.code]) keyLabel = labels[s.code];
  else if (s.code && s.code.startsWith('Key')) keyLabel = s.code.slice(3);
  else if (s.code && s.code.startsWith('Digit')) keyLabel = s.code.slice(5);
  else keyLabel = s.code || '?';
  return (s.ctrl ? 'Ctrl+' : '') + (s.alt ? 'Alt+' : '') + (s.shift ? 'Shift+' : '') + keyLabel;
}

function refreshShortcut() {
  chrome.storage.sync.get('shortcut', (v) => {
    shortcutLabel.textContent = describe(v.shortcut);
  });
}
refreshShortcut();

let recording = false;
recBtn.addEventListener('click', () => {
  recording = true;
  recBtn.textContent = '请按下快捷键…';
  recBtn.classList.add('recording');
  recBtn.focus();
});

document.addEventListener(
  'keydown',
  (e) => {
    if (!recording) return;
    e.preventDefault();
    e.stopPropagation();
    recording = false;
    recBtn.textContent = '点击录制';
    recBtn.classList.remove('recording');
    const s = { ctrl: e.ctrlKey, alt: e.altKey, shift: e.shiftKey, code: e.code };
    chrome.storage.sync.set({ shortcut: s }, refreshShortcut);
  },
  true
);

resetBtn.addEventListener('click', () => {
  chrome.storage.sync.remove('shortcut', refreshShortcut);
});
