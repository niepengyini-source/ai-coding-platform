'use strict';
(() => {
  const picker = document.querySelector('[data-code-picker]');
  if (!picker) return;
  const form = picker.closest('form');
  const primary = form.querySelector('[name=primary]');
  const noCode = form.querySelector('[name=no_code]');
  const choices = [...picker.querySelectorAll('[data-code-choice]')];
  const search = picker.querySelector('[name=code_search]');
  const summary = picker.querySelector('[data-code-summary]');
  const empty = picker.querySelector('[data-code-empty]');
  function refresh() {
    choices.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.codeChoice === primary.value)));
    const chosen = choices.find(button => button.dataset.codeChoice === primary.value);
    summary.textContent = chosen ? '已选：' + chosen.dataset.label : '尚未选择代码';
  }
  choices.forEach(button => button.addEventListener('click', () => {
    if (button.disabled || primary.disabled) return;
    primary.value = button.dataset.codeChoice;
    noCode.checked = false;
    primary.dispatchEvent(new Event('change', {bubbles:true}));
    refresh();
  }));
  picker.querySelector('[data-code-clear]').addEventListener('click', () => {
    if (primary.disabled) return;
    primary.value = '';
    primary.dispatchEvent(new Event('change', {bubbles:true}));
    refresh();
  });
  noCode.addEventListener('change', () => {
    if (noCode.checked && primary.value) {
      primary.value = '';
      form.querySelectorAll('[name=secondary]').forEach(el => el.checked = false);
      primary.dispatchEvent(new Event('change', {bubbles:true}));
    }
    refresh();
  });
  primary.addEventListener('change', refresh);
  search.addEventListener('input', () => {
    const query = search.value.trim().toLocaleLowerCase();
    let visible = 0;
    picker.querySelectorAll('[data-code-card]').forEach(card => {
      card.hidden = !card.textContent.toLocaleLowerCase().includes(query);
      if (!card.hidden) visible++;
    });
    empty.hidden = visible !== 0;
  });
  form.querySelector('[data-primary-fallback]').hidden = true;
  if ([...form.querySelectorAll('[name=secondary]')].some(el => el.checked)) {
    const secondary = form.querySelector('[data-secondary-options]');
    if (secondary) secondary.open = true;
  }
  // Read-only records can still search and expand rules, but cannot change a choice.
  search.disabled = false;
  picker.hidden = false;
  refresh();
})();
