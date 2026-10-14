const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/context.js'), 'utf8');

function element(tagName = 'div', values = {}) {
  let content = '';
  const el = {
    tagName, children: [], dataset: {}, disabled: false, handlers: {},
    addEventListener(type, handler) { this.handlers[type] = handler; },
    append(...nodes) {
      this.children.push(...nodes.flatMap(node => node.fragment ? node.children : [node]));
    },
    prepend(...nodes) {
      this.children.unshift(...nodes.flatMap(node => node.fragment ? node.children : [node]));
    },
    ...values,
  };
  Object.defineProperty(el, 'textContent', {
    get: () => content,
    set: value => { content = String(value); },
  });
  Object.defineProperty(el, 'innerHTML', {
    set: () => { throw new Error('Material text must not be inserted as HTML'); },
  });
  return el;
}

function initialRow(position) {
  const row = element();
  const index = element('span');
  index.textContent = position;
  const body = element();
  const text = element('p');
  text.textContent = '原记录 ' + position;
  body.append(text);
  row.append(index, body);
  return row;
}

function setup(fetchImplementation, {beforeDisabled = false, afterDisabled = false} = {}) {
  const status = element();
  const before = element();
  const after = element();
  before.append(initialRow(8), initialRow(9));
  after.append(initialRow(11), initialRow(12));
  const buttons = {
    before: element('button', {dataset: {contextMore: 'before', cursor: '8'}, disabled: beforeDisabled}),
    after: element('button', {dataset: {contextMore: 'after', cursor: '12'}, disabled: afterDisabled}),
  };
  const requests = [];
  const panel = {
    dataset: {endpoint: '/api/assignments/1/context/'},
    querySelector: selector => ({
      '[data-context-status]': status,
      '[data-context-before]': before,
      '[data-context-after]': after,
    })[selector],
    querySelectorAll: () => Object.values(buttons),
  };
  vm.runInNewContext(source, {
    document: {
      querySelector: () => panel,
      createElement: tag => element(tag),
      createDocumentFragment: () => element('#fragment', {fragment: true}),
    },
    URLSearchParams,
    fetch: (url, options) => {
      requests.push({url, options});
      return fetchImplementation(url, options);
    },
  });
  return {
    status, before, after, buttons, requests,
    click: direction => buttons[direction].handlers.click(),
    positions: direction => ({before, after})[direction].children.map(row => Number(row.children[0].textContent)),
  };
}

function response(records = [], cursor = 8, hasMore = true, {status = 200, contentType = 'application/json', error} = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {get: () => contentType},
    json: async () => ({records, cursor, has_more: hasMore, error}),
  };
}

test('pages without a context panel are a no-op', () => {
  let selectors = 0;
  vm.runInNewContext(source, {
    document: {
      querySelector: selector => { selectors++; assert.equal(selector, '[data-context-loader]'); return null; },
      createElement: () => { throw new Error('No context nodes should be created'); },
    },
    fetch: () => { throw new Error('No context requests should be sent'); },
  });
  assert.equal(selectors, 1);
});

test('upper context prepends each batch while preserving source order and cursor', async () => {
  let page = 0;
  const ui = setup(async () => {
    page++;
    const positions = page === 1 ? [5, 6, 7] : [2, 3, 4];
    return response(positions.map(position => ({position, text: '记录 ' + position})), positions[0]);
  });
  await ui.click('before');
  assert.deepEqual(ui.positions('before'), [5, 6, 7, 8, 9]);
  assert.equal(ui.buttons.before.dataset.cursor, '5');
  assert.equal(ui.buttons.before.disabled, false);
  assert.equal(ui.buttons.before.textContent, '↑ 查看上文 +3');
  await ui.click('before');
  assert.deepEqual(ui.positions('before'), [2, 3, 4, 5, 6, 7, 8, 9]);
  assert.deepEqual(ui.positions('after'), [11, 12]);
  assert.equal(ui.buttons.before.dataset.cursor, '2');
  const first = new URL(ui.requests[0].url, 'http://localhost');
  const second = new URL(ui.requests[1].url, 'http://localhost');
  assert.equal(first.pathname, '/api/assignments/1/context/');
  assert.equal(first.searchParams.get('direction'), 'before');
  assert.equal(first.searchParams.get('cursor'), '8');
  assert.equal(second.searchParams.get('cursor'), '5');
  assert.equal(ui.requests.every(request => request.options.credentials === 'same-origin'), true);
  assert.equal(ui.status.textContent, '');
});

test('lower context appends each batch and advances only its own cursor', async () => {
  const ui = setup(async () => response([13, 14, 15].map(position => ({position, text: '记录 ' + position})), 15));
  await ui.click('after');
  assert.deepEqual(ui.positions('after'), [11, 12, 13, 14, 15]);
  assert.deepEqual(ui.positions('before'), [8, 9]);
  assert.equal(ui.buttons.after.dataset.cursor, '15');
  assert.equal(ui.buttons.before.dataset.cursor, '8');
  assert.equal(ui.buttons.after.disabled, false);
  assert.equal(ui.buttons.after.textContent, '↓ 查看下文 +3');
  const request = new URL(ui.requests[0].url, 'http://localhost');
  assert.equal(request.searchParams.get('direction'), 'after');
  assert.equal(request.searchParams.get('cursor'), '12');
});

test('an exhausted short batch disables further loads at either boundary', async () => {
  for (const direction of ['before', 'after']) {
    const boundary = direction === 'before' ? 7 : 13;
    const ui = setup(async () => response([{position: boundary, text: '最后一条'}], boundary, false));
    await ui.click(direction);
    assert.equal(ui.buttons[direction].dataset.cursor, String(boundary));
    assert.equal(ui.buttons[direction].disabled, true);
    assert.equal(ui.buttons[direction].textContent, direction === 'before' ? '已到最前' : '已到最后');
    assert.equal(ui.positions(direction).length, 3);
    await ui.click(direction);
    assert.equal(ui.requests.length, 1);
  }
});

test('an empty final batch preserves displayed context and cannot be loaded again', async () => {
  const ui = setup(async () => response([], 8, false));
  await ui.click('before');
  assert.deepEqual(ui.positions('before'), [8, 9]);
  assert.equal(ui.buttons.before.dataset.cursor, '8');
  assert.equal(ui.buttons.before.disabled, true);
  await ui.click('before');
  assert.equal(ui.requests.length, 1);
});

test('initially unavailable context buttons do not send a request', async () => {
  const ui = setup(async () => { throw new Error('No request expected'); }, {beforeDisabled: true, afterDisabled: true});
  await ui.click('before');
  await ui.click('after');
  assert.equal(ui.requests.length, 0);
  assert.deepEqual(ui.positions('before'), [8, 9]);
  assert.deepEqual(ui.positions('after'), [11, 12]);
});

test('a pending double click sends only one batch and restores the button on success', async () => {
  let finish;
  const ui = setup(() => new Promise(resolve => { finish = resolve; }));
  const pending = ui.click('before');
  assert.equal(ui.buttons.before.disabled, true);
  assert.equal(ui.status.textContent, '正在加载…');
  await ui.click('before');
  assert.equal(ui.requests.length, 1);
  finish(response([{position: 7, text: '上文'}], 7));
  await pending;
  assert.deepEqual(ui.positions('before'), [7, 8, 9]);
  assert.equal(ui.buttons.before.disabled, false);
  assert.equal(ui.status.textContent, '');
});

test('network failure permits retry without changing the cursor or existing context', async () => {
  let calls = 0;
  const ui = setup(async () => {
    if (++calls === 1) throw new Error('网络暂时中断');
    return response([{position: 13, text: '恢复后的下文'}], 13);
  });
  await ui.click('after');
  assert.equal(ui.status.textContent, '网络暂时中断');
  assert.equal(ui.buttons.after.disabled, false);
  assert.equal(ui.buttons.after.dataset.cursor, '12');
  assert.deepEqual(ui.positions('after'), [11, 12]);
  await ui.click('after');
  assert.equal(ui.requests[0].url, ui.requests[1].url);
  assert.deepEqual(ui.positions('after'), [11, 12, 13]);
  assert.equal(ui.buttons.after.dataset.cursor, '13');
  assert.equal(ui.status.textContent, '');
});

test('a login HTML response leaves existing records untouched and allows retry', async () => {
  let calls = 0;
  const ui = setup(async () => ++calls === 1
    ? response([], 1, false, {contentType: 'text/html; charset=utf-8'})
    : response([{position: 7, text: '恢复后的上文'}], 7));
  await ui.click('before');
  assert.equal(ui.status.textContent, '登录已失效，请重新登录。');
  assert.equal(ui.buttons.before.disabled, false);
  assert.equal(ui.buttons.before.dataset.cursor, '8');
  assert.deepEqual(ui.positions('before'), [8, 9]);
  await ui.click('before');
  assert.equal(ui.requests[0].url, ui.requests[1].url);
  assert.deepEqual(ui.positions('before'), [7, 8, 9]);
  assert.equal(ui.status.textContent, '');
});

test('JSON 4xx and 503 responses preserve state, show the error and allow a successful retry', async () => {
  for (const status of [400, 401, 403, 404, 503]) {
    let calls = 0;
    const ui = setup(async () => ++calls === 1
      ? response([{position: 999, text: '不应出现'}], 999, false, {status, error: '暂时不能加载'})
      : response([{position: 13, text: '重新加载'}], 13));
    await ui.click('after');
    assert.equal(ui.status.textContent, '暂时不能加载');
    assert.equal(ui.buttons.after.disabled, false);
    assert.equal(ui.buttons.after.dataset.cursor, '12');
    assert.deepEqual(ui.positions('after'), [11, 12]);
    await ui.click('after');
    assert.equal(ui.requests[0].url, ui.requests[1].url);
    assert.deepEqual(ui.positions('after'), [11, 12, 13]);
    assert.equal(ui.status.textContent, '');
  }
});

test('an unreadable JSON response restores retry controls without advancing the cursor', async () => {
  const ui = setup(async () => ({
    ok: true,
    headers: {get: () => 'application/json'},
    json: async () => { throw new Error('响应格式不正确'); },
  }));
  await ui.click('before');
  assert.equal(ui.status.textContent, '响应格式不正确');
  assert.equal(ui.buttons.before.disabled, false);
  assert.equal(ui.buttons.before.dataset.cursor, '8');
  assert.deepEqual(ui.positions('before'), [8, 9]);
});

test('material text and conversation keys are rendered as literal text rather than HTML', async () => {
  const text = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
  const key = '<svg onload="alert(3)">组别 & 时间</svg>';
  const ui = setup(async () => response([{position: 13, text, context_key: key}], 13, false));
  await ui.click('after');
  const row = ui.after.children[2];
  assert.equal(row.className, 'context-row');
  assert.equal(row.children[0].textContent, '13');
  assert.equal(row.children[1].children[0].tagName, 'p');
  assert.equal(row.children[1].children[0].textContent, text);
  assert.equal(row.children[1].children[1].tagName, 'small');
  assert.equal(row.children[1].children[1].textContent, key);
  assert.equal(ui.status.textContent, '');
});
