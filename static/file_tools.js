'use strict';
(() => {
  const form = document.getElementById('export-options');
  if (!form) return;
  for (const button of form.querySelectorAll('[data-export-select]')) {
    button.addEventListener('click', () => {
      const selected = button.dataset.exportSelect === 'all';
      for (const input of form.querySelectorAll('input[name="fields"]')) input.checked = selected;
    });
  }
})();
