'use strict';
(() => {
  const layout = document.querySelector('[data-export-layout]');
  if (!layout) return;
  const fields = document.querySelector('[data-export-fields]');
  const format = document.querySelector('[data-export-format]');
  const hint = document.querySelector('[data-source-export-hint]');
  function refresh() {
    const source = layout.value === 'source';
    if (fields) {
      fields.hidden = source;
      fields.querySelectorAll('input, select, textarea, button').forEach(input => { input.disabled = source; });
    }
    if (hint) hint.hidden = !source;
    const original = format?.querySelector('option[value="source"]');
    if (original) original.disabled = !source;
    if (!source && format?.value === 'source') format.value = 'xlsx';
  }
  layout.addEventListener('change', refresh);
  refresh();
})();
