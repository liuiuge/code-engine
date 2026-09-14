// background.js — MV3 service worker.
// 职责：代 content script 向本地 code-engine 后端发起请求（规避 https 页面的混合内容拦截）。
// 快捷键改由 content.js 在页面内用 keydown 捕获，因此不再依赖 chrome.commands，
// 也就不受 Chrome 对 Ctrl+Alt 组合的禁用限制。
'use strict';

const DEFAULT_BACKEND = 'http://localhost:8000';
// 做题模式默认走在线模型：'quality' 让 coder/fixer 首次即使用 minimax-m3:cloud。
const DEFAULT_PREFERENCE = 'quality';

// content script 请求真正生成：后台拉取后端。
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === 'request-generate') {
    handleGenerate(msg.text).then(sendResponse);
    return true; // 保持消息通道开放，等待异步结果。
  }
  return false;
});

async function handleGenerate(text) {
  const { backendUrl, preference } = await chrome.storage.sync.get({
    backendUrl: DEFAULT_BACKEND,
    preference: DEFAULT_PREFERENCE,
  });
  const base = (backendUrl || DEFAULT_BACKEND).replace(/\/+$/, '');
  try {
    const resp = await fetch(`${base}/api/custom-questions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, no_confirm: true, preference: preference || DEFAULT_PREFERENCE }),
    });
    if (!resp.ok) {
      return { error: `后端返回 ${resp.status}` };
    }
    const data = await resp.json();
    const code =
      (data && data.result && data.result.final_output) ||
      (data && data.record && data.record.final_output) ||
      null;
    if (!code) {
      const reason = (data && (data.reason || data.status)) || '无代码返回';
      return { error: String(reason) };
    }
    return { code };
  } catch (e) {
    return { error: e && e.message ? e.message : '请求失败' };
  }
}
