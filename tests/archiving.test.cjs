// Archive UI checks use a fake DOM and fictional counts, never a real project.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/archiving.js'), 'utf8');
const searchNames = ['scope', 'mode', 'q', 'document', 'round_id', 'code', 'status',
  'metadata_field', 'metadata_value', 'start', 'end', 'include_superseded'];

function input(value = '') {
  return {value, checked: false, events: {}, addEventListener(name, callback) { this.events[name] = callback; }};
}

function setup({total = 30, missingSelection = false} = {}) {
  const fields = Object.fromEntries(searchNames.map(name => [name, input()]));
  Object.assign(fields, {selection: input('all'), limit: input(), format: input('xlsx')});
  const checkboxes = [input('1'), input('2')], button = {}, summary = {}, panel = {};
  const form = {
    dataset: {archiveCount: String(total)},
    querySelector(selector) {
      if (selector === '[data-archive-limit]') return panel;
      if (selector === '[value="custom"]') return button;
      if (selector === '[data-archive-selection-summary]') return summary;
      const match = selector.match(/name="([^"]+)"/);
      if (missingSelection && match && match[1] === 'selection') return null;
      return match ? fields[match[1]] : null;
    },
    querySelectorAll() { return checkboxes; },
  };
  vm.runInNewContext(source, {document: {querySelectorAll() { return [form]; }}});
  return {fields, checkboxes, button, summary, panel,
    choose(value) { fields.selection.value = value; fields.selection.events.change(); },
    quantity(value) { fields.limit.value = String(value); fields.limit.events.input(); },
    check(index, checked) { checkboxes[index].checked = checked; checkboxes[index].events.change(); },
  };
}

test('all-results export starts enabled and the unused N field is hidden and disabled', () => {
  const ui = setup();
  assert.equal(ui.button.disabled, false);
  assert.equal(ui.fields.limit.disabled, true);
  assert.equal(ui.fields.limit.required, false);
  assert.equal(ui.panel.hidden, true);
  assert.match(ui.summary.textContent, /全部 30 条.*其他页/);
});

test('empty search results cannot be exported', () => {
  const ui = setup({total: 0});
  assert.equal(ui.button.disabled, true);
  ui.choose('first'); ui.quantity(1);
  assert.equal(ui.button.disabled, true);
});

test('first-N mode shows a required number and rejects empty or invalid values', () => {
  const ui = setup();
  ui.choose('first');
  assert.equal(ui.panel.hidden, false);
  assert.equal(ui.fields.limit.disabled, false);
  assert.equal(ui.fields.limit.required, true);
  for (const value of ['', 0, -1, 1.2, 'not a number', 50001]) {
    ui.quantity(value);
    assert.equal(ui.button.disabled, true, `invalid N: ${value}`);
  }
  ui.quantity(2);
  assert.equal(ui.button.disabled, false);
  assert.match(ui.summary.textContent, /最多 2 条/);
});

test('first-N summary accurately handles fewer matches than requested', () => {
  const ui = setup({total: 8});
  ui.choose('first'); ui.quantity(50);
  assert.equal(ui.button.disabled, false);
  assert.match(ui.summary.textContent, /最多 8 条/);
});

test('checking a row switches to current-page selection and tracks the checked count', () => {
  const ui = setup();
  ui.check(0, true);
  assert.equal(ui.fields.selection.value, 'selected');
  assert.equal(ui.button.disabled, false);
  assert.match(ui.summary.textContent, /本页勾选的 1 条/);
  ui.check(1, true);
  assert.match(ui.summary.textContent, /本页勾选的 2 条/);
  ui.check(0, false); ui.check(1, false);
  assert.equal(ui.button.disabled, true);
});

test('selection mode disables the N field without hiding existing checkboxes', () => {
  const ui = setup();
  ui.choose('first'); ui.quantity(2); ui.check(0, true);
  assert.equal(ui.fields.limit.disabled, true);
  assert.equal(ui.fields.limit.required, false);
  assert.equal(ui.panel.hidden, true);
  assert.equal(ui.checkboxes[0].checked, true);
});

test('all-results mode includes the total across pages, not just the visible rows', () => {
  const ui = setup({total: 130});
  ui.check(0, true); ui.choose('all');
  assert.equal(ui.button.disabled, false);
  assert.match(ui.summary.textContent, /全部 130 条.*包含其他页/);
});

test('editing any search condition disables export until a fresh preview', () => {
  for (const name of searchNames) {
    for (const event of ['input', 'change']) {
      const ui = setup();
      ui.fields[name].value = 'changed';
      ui.fields[name].events[event]();
      assert.equal(ui.button.disabled, true, `${name}: ${event}`);
      assert.equal(ui.summary.textContent, '筛选条件已变化，请先预览');
    }
  }
});

test('changing the output format, range or N does not invalidate the search preview', () => {
  const ui = setup();
  assert.equal(ui.fields.format.events.input, undefined);
  assert.equal(ui.fields.format.events.change, undefined);
  ui.fields.format.value = 'json';
  assert.equal(ui.button.disabled, false);
  ui.choose('first'); ui.quantity(3);
  assert.equal(ui.button.disabled, false);
  assert.doesNotMatch(ui.summary.textContent, /条件已变化/);
  ui.check(0, true);
  assert.equal(ui.button.disabled, false);
});

test('changing selection controls cannot re-enable export after search conditions changed', () => {
  const ui = setup();
  ui.fields.q.events.input();
  ui.choose('first'); ui.quantity(4); ui.check(0, true); ui.choose('all');
  assert.equal(ui.button.disabled, true);
  assert.equal(ui.summary.textContent, '筛选条件已变化，请先预览');
});

test('pages without an archive form remain unaffected', () => {
  assert.doesNotThrow(() => vm.runInNewContext(source, {document: {querySelectorAll() { return []; }}}));
});

test('incomplete forms are ignored instead of breaking other controls', () => {
  assert.doesNotThrow(() => setup({missingSelection: true}));
});

test('both preview buttons bypass export-number validation while retaining server validation', () => {
  const template = fs.readFileSync(path.join(__dirname, '../templates/coding/archive.html'), 'utf8');
  const previewButtons = (template.match(/<button\b[^>]*>/g) || []).filter(tag =>
    /name="action"/.test(tag) && /value="preview"/.test(tag));
  assert.equal(previewButtons.length, 2);
  for (const tag of previewButtons) assert.match(tag, /\bformnovalidate\b/);
});
