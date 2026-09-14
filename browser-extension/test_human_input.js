/* Offline regression for content.js human input.
 * Run with: node browser-extension/test_human_input.js
 * No browser, extension runtime, or Ollama is required.
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class FakeEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.bubbles = !!init.bubbles;
    Object.assign(this, init);
  }

  preventDefault() {}
  stopPropagation() {}
}

class FakeEventTarget {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    const list = this.listeners.get(type) || [];
    list.push(listener);
    this.listeners.set(type, list);
  }

  dispatchEvent(event) {
    if (!event.target) event.target = this;
    for (const listener of this.listeners.get(event.type) || []) listener.call(this, event);
    return true;
  }
}

class FakeEditable extends FakeEventTarget {
  constructor(tagName = 'TEXTAREA') {
    super();
    this.tagName = tagName;
    this.value = '';
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.attributes = { type: 'text' };
  }

  getAttribute(name) {
    return this.attributes[name] || null;
  }

  contains() {
    return false;
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  setSelectionRange(start, end) {
    this.selectionStart = start;
    this.selectionEnd = end;
  }

  setRangeText(text, start, end, selectionMode) {
    this.value = this.value.slice(0, start) + text + this.value.slice(end);
    if (selectionMode === 'end') {
      this.selectionStart = this.selectionEnd = start + text.length;
    }
  }

  getBoundingClientRect() {
    return { width: 300, height: 40 };
  }
}

class FakeDocument extends FakeEventTarget {
  constructor() {
    super();
    this.body = { appendChild() {} };
    this.documentElement = this.body;
    this.activeElement = null;
    this.editable = null;
  }

  querySelectorAll() {
    return this.editable ? [this.editable] : [];
  }

  createElement() {
    return { style: {}, appendChild() {}, set textContent(_) {} };
  }

  createTextNode(text) {
    return { textContent: text };
  }

  getSelection() {
    return { anchorNode: {}, toString: () => 'make a small program' };
  }
}

async function run() {
  const document = new FakeDocument();
  const target = new FakeEditable();
  target.ownerDocument = document;
  target.value = 'existing prompt';
  document.editable = target;

  const inputEvents = [];
  target.addEventListener('input', (event) => {
    inputEvents.push({ value: target.value, data: event.data, inputType: event.inputType });
    // Model a controlled input: the page reads the complete DOM value and
    // commits that same value back, rather than appending the event payload.
    target.value = inputEvents[inputEvents.length - 1].value;
  });

  const chrome = {
    storage: {
      sync: {
        async get(keyOrDefaults) {
          if (typeof keyOrDefaults === 'string') return {};
          return { ...keyOrDefaults, humanInput: true, showHints: false };
        },
      },
      onChanged: { addListener() {} },
    },
    runtime: {
      async sendMessage() {
        return { code: '```go\npackage main\n\nfunc main() {\n    println("ok")\n}\n```' };
      },
    },
  };

  const context = {
    chrome,
    document,
    window: { getSelection: () => document.getSelection() },
    HTMLTextAreaElement: class HTMLTextAreaElement extends FakeEditable {},
    HTMLInputElement: class HTMLInputElement extends FakeEditable {},
    Event: FakeEvent,
    InputEvent: class InputEvent extends FakeEvent {},
    setTimeout(callback) {
      callback();
      return 0;
    },
    clearTimeout() {},
    console,
  };
  Object.setPrototypeOf(target, context.HTMLTextAreaElement.prototype);
  vm.runInNewContext(fs.readFileSync(__dirname + '/content.js', 'utf8'), context);

  document.dispatchEvent(new FakeEvent('focusin'));
  document.dispatchEvent(new FakeEvent('keydown', {
    code: 'KeyY', ctrlKey: true, shiftKey: true, altKey: false,
  }));
  // Let the async message and human-input loop finish.
  for (let i = 0; i < 10; i++) await Promise.resolve();

  const expected = 'existing prompt\npackage main\n\nfunc main() {\nprintln("ok")\n}';
  assert.equal(target.value, expected);
  assert.equal(inputEvents.length, 1);
  assert.equal(inputEvents[0].inputType, undefined);
  assert.deepEqual(inputEvents.map((event) => event.value).at(-1), expected);
  assert.equal(inputEvents[0].data, undefined);
  console.log('human input simulation passed');
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
