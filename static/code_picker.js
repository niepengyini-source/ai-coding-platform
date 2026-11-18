'use strict';
(() => {
  const picker = document.querySelector('[data-code-picker]');
  if (!picker) return;
  const form = picker.closest('form');
  const primary = form.querySelector('[name=primary]');
  const parallel = form.dataset?.labelPolicy === 'parallel';
  const labelChoices = parallel ? [...form.querySelectorAll('[name=labels]')] : [];
  const noCode = form.querySelector('[name=no_code]');
  const choices = [...picker.querySelectorAll('[data-code-choice]')];
  const search = picker.querySelector('[name=code_search]');
  const summary = picker.querySelector('[data-code-summary]');
  const empty = picker.querySelector('[data-code-empty]');
  const secondaryChoices = [...form.querySelectorAll('[name=secondary]')];
  const cards = [...picker.querySelectorAll('[data-code-card]')].map(card => ({card,text:card.textContent.toLocaleLowerCase()}));
  function refresh() {
    const disabled = parallel ? labelChoices.every(input => input.disabled) : primary.disabled;
    choices.forEach(button => {
      button.disabled = disabled;
      button.setAttribute('aria-pressed', String(parallel ? labelChoices.some(input => input.value === button.dataset.codeChoice && input.checked) : button.dataset.codeChoice === primary.value));
    });
    const chosen = choices.find(button => button.dataset.codeChoice === primary.value);
    const selected = parallel ? choices.filter(button => labelChoices.some(input => input.checked && input.value === button.dataset.codeChoice)) : [];
    summary.textContent = parallel ? (selected.length ? '已选：' + selected.map(button => button.dataset.label).join('；') : '尚未选择代码') : chosen ? '已选：' + chosen.dataset.label : '尚未选择代码';
    secondaryChoices.forEach(el => {
      el.disabled = primary.disabled || !primary.value || el.value === primary.value;
      if (!primary.disabled && el.value === primary.value) el.checked = false;
    });
    search.disabled = false;
  }
  choices.forEach(button => button.addEventListener('click', () => {
    if (button.disabled || primary.disabled) return;
    if (parallel) {
      const input = labelChoices.find(input => input.value === button.dataset.codeChoice);
      if (!input || input.disabled) return;
      input.checked = !input.checked;
      noCode.checked = false;
      input.dispatchEvent(new Event('change', {bubbles:true}));
      refresh();
      return;
    }
    primary.value = button.dataset.codeChoice;
    noCode.checked = false;
    primary.dispatchEvent(new Event('change', {bubbles:true}));
    refresh();
  }));
  picker.querySelector('[data-code-clear]').addEventListener('click', () => {
    if (primary.disabled) return;
    labelChoices.forEach(input => input.checked = false);
    if (parallel) labelChoices[0]?.dispatchEvent(new Event('change', {bubbles:true}));
    primary.value = '';
    primary.dispatchEvent(new Event('change', {bubbles:true}));
    refresh();
  });
  noCode.addEventListener('change', () => {
    if (noCode.checked) {
      primary.value = '';
      secondaryChoices.forEach(el => el.checked = false);
      labelChoices.forEach(el => el.checked = false);
      primary.dispatchEvent(new Event('change', {bubbles:true}));
    }
    refresh();
  });
  primary.addEventListener('change', refresh);
  labelChoices.forEach(input => input.addEventListener('change', () => { if (input.checked) noCode.checked = false; refresh(); }));
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
