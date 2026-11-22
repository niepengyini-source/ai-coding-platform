'use strict';
(() => {
  const source = document.getElementById('export-source');
  if (source) {
    const target = source.querySelector('[name="target"]');
    function refreshSource() {
      for (const [selector, visible] of [
        ['[data-export-book]', target.value === 'codebook'],
        ['[data-export-round]', !['codebook', 'units'].includes(target.value)],
        ['[data-export-history]', target.value === 'units'],
      ]) {
        const section = source.querySelector(selector);
        section.hidden = !visible;
        for (const input of section.querySelectorAll('input, select')) input.disabled = !visible;
      }
    }
    target.addEventListener('change', refreshSource);
    refreshSource();
  }
  const form = document.getElementById('export-options');
  if (!form) return;
  for (const button of form.querySelectorAll('[data-export-select]')) {
    button.addEventListener('click', () => {
      const selected = button.dataset.exportSelect === 'all';
      for (const input of form.querySelectorAll('input[name="fields"]')) input.checked = selected;
    });
  }
})();
