'use strict';
(() => {
  const source = document.getElementById('export-source');
  const form = document.getElementById('export-options');
  if (source) {
    const target = source.querySelector('[name="target"]');
    const reloadButton = source.querySelector('button[type="submit"]');
    const snapshot = () => {
      const fields = source.querySelectorAll?.('select, input[type="checkbox"]') || [];
      return [...fields].filter(el => !el.disabled)
        .map(el => `${el.name}=${el.type === 'checkbox' ? el.checked : el.value}`).join('&');
    };
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
    refreshSource();
    const initialSnapshot = snapshot();
    function updateStaleState() {
      const stale = snapshot() !== initialSnapshot;
      if (reloadButton?.classList) reloadButton.classList.toggle('secondary', !stale);
      if (!form) return;
      form.classList?.toggle('is-stale-preview', stale);
      const staleHint = form.querySelector?.('[data-export-stale-hint]');
      if (staleHint) staleHint.hidden = !stale;
      const downloadBtn = form.querySelector?.('button[name="action"][value="download"]');
      if (downloadBtn) downloadBtn.disabled = stale;
    }
    target.addEventListener('change', () => { refreshSource(); updateStaleState(); });
    source.addEventListener?.('change', event => {
      if (event?.target !== target) updateStaleState();
    });
  }
  if (!form) return;
  for (const button of form.querySelectorAll('[data-export-select]')) {
    button.addEventListener('click', () => {
      const selected = button.dataset.exportSelect === 'all';
      for (const input of form.querySelectorAll('input[name="fields"]')) input.checked = selected;
    });
  }
})();
