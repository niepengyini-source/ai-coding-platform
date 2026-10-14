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
  const secondaryChoices = [...form.querySelectorAll('[name=secondary]')];
  const cards = [...picker.querySelectorAll('[data-code-card]')].map(card => ({card,text:card.textContent.toLocaleLowerCase()}));
  function refresh() {
    choices.forEach(button => {
      button.disabled = primary.disabled;
      button.setAttribute('aria-pressed', String(button.dataset.codeChoice === primary.value));
    });
    const chosen = choices.find(button => button.dataset.codeChoice === primary.value);
    summary.textContent = chosen ? '已选：' + chosen.dataset.label : '尚未选择代码';
    secondaryChoices.forEach(el => {
      el.disabled = primary.disabled || !primary.value || el.value === primary.value;
      if (!primary.disabled && el.value === primary.value) el.checked = false;
    });
    search.disabled = false;
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
    if (noCode.checked) {
      primary.value = '';
      secondaryChoices.forEach(el => el.checked = false);
      primary.dispatchEvent(new Event('change', {bubbles:true}));
    }
    refresh();
  });
  primary.addEventListener('change', refresh);
  secondaryChoices.forEach(el => el.addEventListener('change', () => {
    if (el.checked) {
      noCode.checked = false;
      secondaryChoices.forEach(other => { if (other !== el) other.checked = false; });
    }
  }));
  form.platformRefreshCodes = refresh;
  search.addEventListener('input', () => {
    const query = search.value.trim().toLocaleLowerCase();
    let visible = 0;
    cards.forEach(({card,text}) => {
      card.hidden = !text.includes(query);
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
