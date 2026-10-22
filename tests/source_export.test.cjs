const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync('static/source_export.js', 'utf8');

function setup(value = 'source', fileFormat = 'source') {
  const listeners = {}, inputs = [{disabled:false}, {disabled:false}];
  const layout = {value, addEventListener(name, fn) { listeners[name] = fn; }};
  const fields = {hidden:false, querySelectorAll() { return inputs; }};
  const original = {disabled:false};
  const format = {value:fileFormat, querySelector() { return original; }};
  const hint = {hidden:false};
  const elements = {'[data-export-layout]':layout, '[data-export-fields]':fields,
    '[data-export-format]':format, '[data-source-export-hint]':hint};
  vm.runInNewContext(script, {document:{querySelector(selector) { return elements[selector]; }}});
  return {layout, fields, format, original, hint, inputs, change:() => listeners.change()};
}

test('source layout hides and disables legacy field controls without changing selected format', () => {
  const page = setup();
  assert.equal(page.fields.hidden, true);
  assert.equal(page.inputs.every(input => input.disabled), true);
  assert.equal(page.format.value, 'source');
  assert.equal(page.hint.hidden, false);
});
test('switching to platform layout restores field controls and requests a concrete format', () => {
  const page = setup();
  page.layout.value = 'platform'; page.change();
  assert.equal(page.fields.hidden, false);
  assert.equal(page.inputs.every(input => !input.disabled), true);
  assert.equal(page.original.disabled, true);
  assert.equal(page.format.value, 'xlsx');
  assert.equal(page.hint.hidden, true);
});
test('an explicit user format is retained when switching layouts', () => {
  const page = setup('platform', 'csv');
  page.layout.value = 'source'; page.change();
  assert.equal(page.format.value, 'csv');
  assert.equal(page.original.disabled, false);
});
test('unrelated pages do not install export listeners', () => {
  assert.doesNotThrow(() => vm.runInNewContext(script, {document:{querySelector() { return null; }}}));
});
