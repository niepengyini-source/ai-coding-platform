'use strict';
(() => {
  const defaults = Object.freeze({primary:'Alt+Q', secondary:'Alt+E', save:'Alt+F', next:'Alt+G',
    pickerUp:'W', pickerDown:'S', pickerConfirm:'Enter', pickerClose:'Alt+X',
    modePrimary:'1', modeSecondary:'2', skip:'Alt+Enter'});
  const actions = Object.keys(defaults);
  const globalActions = ['primary', 'secondary', 'save', 'next'];
  const pickerActions = ['pickerUp', 'pickerDown', 'pickerConfirm', 'pickerClose'];
  const modeActions = ['modePrimary', 'modeSecondary', 'skip'];
  const modifiers = ['Ctrl', 'Alt', 'Shift', 'Meta'];
  const keyNames = {enter:'Enter', escape:'Escape', ' ':'Space', space:'Space', tab:'Tab',
    arrowup:'ArrowUp', arrowdown:'ArrowDown', arrowleft:'ArrowLeft', arrowright:'ArrowRight',
    home:'Home', end:'End', pageup:'PageUp', pagedown:'PageDown', backspace:'Backspace', delete:'Delete'};
  function normalize(value) {
    if (typeof value !== 'string') return '';
    const parts = value.split('+').map(part => part.trim());
    const key = parts.pop();
    if (!key || parts.some(part => !modifiers.includes(part)) || new Set(parts).size !== parts.length) return '';
    const canonical = keyNames[key.toLowerCase()] || (/^[a-z0-9]$/i.test(key) ? key.toUpperCase() : /^F(?:[1-9]|1[0-2])$/i.test(key) ? key.toUpperCase() : '');
    return canonical ? [...modifiers.filter(part => parts.includes(part)), canonical].join('+') : '';
  }
  function shortcutError(value, action) {
    const normalized = normalize(value);
    if (['pickerUp', 'pickerDown'].includes(action) && /^[A-Z]$/.test(normalized)) return '';
    if (['modePrimary', 'modeSecondary'].includes(action) && /^[0-9]$/.test(normalized)) return '';
    if (action === 'pickerConfirm' && normalized === 'Enter') return '';
    if (!normalized || !/^(?:Ctrl|Alt|Meta)(?:\+|$)/.test(normalized)) return '请使用 Ctrl、Alt 或 ⌘ 加其他键的组合。';
    const parts = normalized.split('+'), key = parts.pop();
    if (parts.includes('Ctrl') && parts.includes('Alt')) return 'Ctrl＋Alt 可能与输入法冲突，请换一个组合。';
    if (key === 'Escape' || key === 'Tab' || key === 'Space' || key === 'Delete' || key === 'Backspace') return '请避开输入、切换和取消操作常用的按键。';
    const command = parts.includes('Ctrl') || parts.includes('Meta');
    if (command && ['L','T','W','N','R','P','F','O','S','D','H','J','I','C','V','X','A','Z','Y','Q','B','E','G','U','F4','F5','F6','F11','F12'].includes(key)
        || parts.includes('Alt') && ['ArrowLeft','ArrowRight','Home','F4','Space'].includes(key)
        || ['F1','F5','F6','F10','F11','F12'].includes(key)) return '这个组合可能被浏览器或系统占用，请换一个。';
    return '';
  }
  function validate(values, names = actions) {
    const result = {}, seen = new Set();
    for (const action of names) {
      const error = shortcutError(values?.[action], action);
      if (error) return {error, action};
      const chord = normalize(values[action]);
      if (seen.has(chord)) return {error:'快捷键不能重复，请为每个操作设置不同组合。', action};
      seen.add(chord); result[action] = chord;
    }
    return {values:result};
  }
  function readBindings(saved) {
    if (!saved) return {values:{...defaults}};
    const values = JSON.parse(saved);
    const missingMode = values && modeActions.every(action => !Object.hasOwn(values, action));
    const legacyNames = missingMode && pickerActions.every(action => !Object.hasOwn(values, action)) ? globalActions
      : missingMode && pickerActions.every(action => Object.hasOwn(values, action)) ? [...globalActions, ...pickerActions] : null;
    if (values && legacyNames && globalActions.every(action => typeof values[action] === 'string')) {
      const legacy = validate(values, legacyNames);
      if (legacy.error) return legacy;
      const merged = {...defaults, ...legacy.values}, used = new Set(Object.values(legacy.values));
      for (const action of actions) if (!legacyNames.includes(action) && used.has(merged[action])) merged[action] = '';
      return {values:merged, warning:actions.some(action => !merged[action])
        ? '原快捷键已保留。新增选择框快捷键有冲突，请重新设置未设置项；也可继续使用方向键、Enter 和 Esc。' : ''};
    }
    return validate(values);
  }
  function eventChord(event) {
    if (event.isComposing || event.keyCode === 229 || event.getModifierState?.('AltGraph')) return '';
    const key = /^Key[A-Z]$/.test(event.code || '') ? event.code.slice(3)
      : /^Digit[0-9]$/.test(event.code || '') ? event.code.slice(5) : event.key;
    return normalize([...modifiers.filter(part => event[{Ctrl:'ctrlKey', Alt:'altKey', Shift:'shiftKey', Meta:'metaKey'}[part]]), key].join('+'));
  }
  function label(value) { return value ? value.replace('Meta', '⌘') : '未设置'; }
  window.PlatformShortcuts = {defaults, normalize, validate, eventChord};

  const user = document.body?.dataset.navigationUser;
  if (!user || user === 'anonymous') return;
  const storageKey = `ai-coding-platform:shortcuts:v1:${user}`;
  let bindings = {...defaults}, storageError = '';
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved) {
      const parsed = readBindings(saved);
      if (parsed.error) storageError = '已保存的快捷键无效，当前使用默认设置。';
      else { bindings = parsed.values; storageError = parsed.warning || ''; }
    }
  } catch (_) { storageError = '浏览器无法读取快捷键设置，当前使用默认设置。'; }
  const updateHints = () => document.querySelectorAll('[data-shortcut-hint]').forEach(el => {
    if (Object.hasOwn(bindings, el.dataset.shortcutHint)) el.textContent = label(bindings[el.dataset.shortcutHint]);
  });
  const settings = document.querySelector('[data-shortcut-settings]');
  let recording = null, recordingOriginal = '', pending = {...bindings};
  const settingsForm = settings?.querySelector('[data-shortcut-settings-form]');
  const settingsStatus = settings?.querySelector('[data-shortcut-settings-status]');
  const settingsFields = settings ? [...settings.querySelectorAll('[data-shortcut-record]')] : [];
  function message(text, error = false) {
    if (settingsStatus) { settingsStatus.textContent = text; settingsStatus.classList.toggle('error', error); }
  }
  function refreshSettings() { settingsFields.forEach(field => field.value = label(pending[field.dataset.shortcutRecord])); }
  function saveSettings(values, reset = false) {
    const checked = validate(values);
    if (checked.error) {
      const field = settingsFields.find(field => field.dataset.shortcutRecord === checked.action);
      const section = field?.closest?.('details'); if (section) section.open = true;
      field?.focus();
      message(checked.error, true);
      return;
    }
    try { localStorage.setItem(storageKey, JSON.stringify(checked.values)); }
    catch (_) { message('浏览器无法保存设置，快捷键未更改。请允许本站使用本地存储后重试。', true); return; }
    bindings = checked.values; pending = {...bindings}; recording = null; refreshSettings(); updateHints();
    window.dispatchEvent(new Event('platform:shortcuts-updated'));
    message(reset ? '已恢复并保存默认快捷键。' : '已保存，当前浏览器中的本账号立即生效。');
  }
  if (settings) {
    settings.hidden = false; refreshSettings();
    if (storageError) message(storageError, true);
    settingsFields.forEach(field => {
      field.addEventListener('focus', () => { recording = field; recordingOriginal = pending[field.dataset.shortcutRecord]; message('请按下新的组合键；Esc 取消。'); });
      field.addEventListener('blur', () => { if (recording === field) recording = null; });
    });
    settingsForm.addEventListener('submit', event => { event.preventDefault(); saveSettings(pending); });
    settings.querySelector('[data-shortcut-reset]').addEventListener('click', () => saveSettings({...defaults}, true));
  }

  const form = document.querySelector('.annotation-form');
  const primary = form?.querySelector('[name=primary]');
  const secondary = form ? [...form.querySelectorAll('[name=secondary]')] : [];
  const toolbar = document.querySelector('[data-shortcut-toolbar]');
  const dialog = document.querySelector('[data-shortcut-dialog]');
  const search = dialog?.querySelector('[data-shortcut-search]');
  const list = dialog?.querySelector('[data-shortcut-list]');
  const closeButton = dialog?.querySelector('[data-shortcut-close]');
  const title = dialog?.querySelector('#shortcut-dialog-title');
  const count = dialog?.querySelector('[data-shortcut-count]');
  const empty = dialog?.querySelector('[data-shortcut-empty]');
  const note = form?.querySelector('[name=note]');
  const skipPrompt = form?.querySelector('[data-shortcut-skip-prompt]');
  const skipButtons = [...document.querySelectorAll('[data-shortcut-skip]')];
  const nextLinks = [...document.querySelectorAll('[data-coding-next]')];
  const completionDialog = document.querySelector('[data-task-completion]');
  let pendingSkipTarget = '';
  let navigationPending = false;
  const openButtons = toolbar ? [...toolbar.querySelectorAll('[data-shortcut-open]')] : [];
  const modeStatus = toolbar?.querySelector?.('[data-shortcut-mode-status]');
  const quickModeKey = form?.dataset.mode === 'annotation' && form.dataset.shortcutScope
    ? `ai-coding-platform:quick-coding:v1:${user}:${form.dataset.shortcutScope}` : '';
  let quickMode = false, quickStorageFailed = false;
  try { if (quickModeKey) quickMode = sessionStorage.getItem(quickModeKey) === 'on'; }
  catch (_) { quickStorageFailed = true; }
  function showQuickMode() {
    if (form) form.dataset.quickMode = String(quickMode);
    if (modeStatus) { modeStatus.hidden = !quickMode; modeStatus.textContent = quickStorageFailed ? '快捷模式（仅本页）' : `快捷模式 · ${label(bindings.pickerConfirm)} 下一条`; }
  }
  function setQuickMode(enabled) {
    if (!quickModeKey) return;
    quickMode = enabled;
    try { if (enabled) sessionStorage.setItem(quickModeKey, 'on'); else sessionStorage.removeItem(quickModeKey); }
    catch (_) { quickStorageFailed = true; }
    showQuickMode();
  }
  const codeButtons = form ? [...form.querySelectorAll('[data-code-choice]')] : [];
  const codes = codeButtons.length ? codeButtons.map(button => ({
    id:button.dataset.codeChoice, label:button.dataset.label || button.textContent.trim(),
    definition:button.closest('[data-code-card]')?.querySelector('.code-choice-details .preserve')?.textContent.trim() || '', button,
  })) : primary ? [...primary.options].filter(option => option.value).map(option => ({id:option.value, label:option.textContent.trim(), definition:option.dataset.definition || ''})) : [];
  codes.forEach(code => code.search = `${code.label} ${code.definition}`.toLocaleLowerCase());
  let opened = false, mode = 'primary', filtered = [], active = 0, previousFocus = null;
  function canEdit() {
    return !!primary && !primary.disabled && form.dataset.locked !== 'true' && form.dataset.splitting !== 'true' && form.dataset.splitPreview !== 'true'
      && (form.dataset.busy !== 'true' || form.dataset.savingDraft === 'true');
  }
  function canOpen(action) { return canEdit() && codes.length > 0 && (action !== 'secondary' || !!primary.value && secondary.some(input => input.value !== primary.value && !input.disabled)); }
  function refreshButtons() {
    if (form) form.dataset.navigationPending = String(navigationPending);
    openButtons.forEach(button => { const disabled = !canOpen(button.dataset.shortcutOpen); if (button.disabled !== disabled) button.disabled = disabled; });
    skipButtons.forEach(button => { const disabled = navigationPending || !canSkip(); if (button.disabled !== disabled) button.disabled = disabled; });
    if (opened && !canOpen(mode)) close();
  }
  function close() {
    if (!opened) return;
    opened = false;
    if (dialog.open) dialog.close();
    dialog.hidden = true; search.setAttribute('aria-expanded', 'false'); search.removeAttribute('aria-activedescendant'); list.removeAttribute('aria-activedescendant');
    if (previousFocus?.isConnected && !previousFocus.disabled) previousFocus.focus();
  }
  function hideSkipPrompt() { if (skipPrompt) skipPrompt.hidden = true; pendingSkipTarget = ''; note?.classList.toggle('shortcut-skip-attention', false); }
  function exitQuickMode() { setQuickMode(false); hideSkipPrompt(); close(); }
  function textEntry(target) {
    return !!target?.isContentEditable || ['TEXTAREA', 'SELECT'].includes(target?.tagName)
      || target?.tagName === 'INPUT' && !['radio', 'checkbox', 'button', 'submit', 'reset', 'range', 'color', 'file', 'image', 'hidden'].includes(target.type);
  }
  function nativeActivation(target) {
    return target?.tagName === 'INPUT' && !['radio', 'checkbox'].includes(target.type)
      || !!target?.closest?.('a, button, summary, [role="button"], [role="link"]');
  }
  function canNavigate() { return !!primary && !primary.disabled && form.dataset.locked !== 'true' && form.dataset.splitting !== 'true' && form.dataset.splitPreview !== 'true'; }
  function canSkip() { return form?.dataset.mode === 'annotation' && !!skipPrompt && canNavigate(); }
  function navigationError(error) {
    const status = form?.querySelector('.save-status');
    if (status) { status.textContent = error.message || '当前编码尚未确认保存，请重试。'; status.className = 'save-status error'; }
  }
  async function advance() {
    if (navigationPending || !primary.value || !canNavigate()) return;
    navigationPending = true;
    refreshButtons();
    try {
      if (typeof form.platformSubmitNext === 'function') await form.platformSubmitNext();
      else { const button = form.querySelector('[data-action="submit_next"]'); if (canEdit() && button && !button.disabled) button.click(); }
    } catch (error) { navigationError(error); }
    finally { navigationPending = false; refreshButtons(); }
  }
  async function confirmSkip() {
    if (navigationPending || !canSkip() || skipPrompt.hidden) return;
    const target = pendingSkipTarget || form.dataset.skipUrl || '';
    if (!new RegExp(`^/r/${Number(form.dataset.shortcutScope)}/\\?a=\\d+$`).test(target)
        && target !== `/p/${Number(form.dataset.projectId)}/progress/`) { navigationError(new Error('无法找到下一条，请刷新后重试。')); return; }
    navigationPending = true;
    refreshButtons();
    try {
      if (typeof form.platformSaveBeforeNavigation !== 'function') throw new Error('保存功能尚未就绪，请刷新后重试。');
      if (await form.platformSaveBeforeNavigation({force:true}) === false) return;
      hideSkipPrompt(); close(); location.assign(target);
    } catch (error) { navigationError(error); }
    finally { navigationPending = false; refreshButtons(); }
  }
  function requestSkip(target) {
    if (navigationPending || !canSkip()) return;
    const requested = typeof target === 'string' && target ? target : pendingSkipTarget || form.dataset.skipUrl || '';
    if (!skipPrompt.hidden && requested === pendingSkipTarget) { confirmSkip(); return; }
    pendingSkipTarget = requested;
    close(); skipPrompt.hidden = false; note?.classList.toggle('shortcut-skip-attention', true); note?.focus();
  }
  function markActive(index) {
    active = Math.max(0, Math.min(index, filtered.length - 1));
    [...list.children].forEach((el, number) => el.setAttribute('aria-selected', String(number === active)));
    const chosen = list.children[active];
    if (chosen) { search.setAttribute('aria-activedescendant', chosen.id); list.setAttribute('aria-activedescendant', chosen.id); chosen.scrollIntoView?.({block:'nearest'}); }
    else { search.removeAttribute('aria-activedescendant'); list.removeAttribute('aria-activedescendant'); }
  }
  function commit() {
    const code = filtered[active];
    if (!code || !canOpen(mode)) return;
    if (mode === 'primary') {
      if (code.button) { if (code.button.disabled) return; code.button.click(); }
      else {
        primary.value = code.id;
        const noCode = form.querySelector('[name=no_code]'); if (noCode) noCode.checked = false;
        primary.dispatchEvent(new Event('change', {bubbles:true}));
      }
    } else {
      const selected = secondary.find(input => input.value === code.id);
      if (!selected || selected.disabled || selected.value === primary.value) return;
      secondary.forEach(input => input.checked = input === selected);
      const noCode = form.querySelector('[name=no_code]'); if (noCode) noCode.checked = false;
      selected.dispatchEvent(new Event('change', {bubbles:true}));
    }
    close();
    if (quickMode) form.focus();
  }
  function render(preferred = '') {
    const query = search.value.trim().toLocaleLowerCase();
    filtered = codes.filter(code => code.search.includes(query) && (mode !== 'secondary' || code.id !== primary.value && secondary.some(input => input.value === code.id && !input.disabled)));
    list.replaceChildren();
    const fragment = document.createDocumentFragment();
    filtered.forEach((code, index) => {
      const option = document.createElement('div');
      option.id = `shortcut-code-${index}`; option.className = 'shortcut-code-option'; option.setAttribute('role', 'option'); option.setAttribute('aria-selected', 'false');
      const heading = document.createElement('strong'); heading.textContent = code.label; option.append(heading);
      if (code.definition) { const description = document.createElement('span'); description.textContent = code.definition.length > 180 ? code.definition.slice(0, 180) + '…' : code.definition; option.append(description); }
      option.addEventListener('click', () => { markActive(index); commit(); }); fragment.append(option);
    });
    list.append(fragment); empty.hidden = filtered.length !== 0; count.textContent = `${filtered.length} 个代码`;
    markActive(Math.max(0, filtered.findIndex(code => code.id === preferred)));
  }
  function setPickerMode(action) {
    mode = action; title.textContent = action === 'secondary' ? '选择次代码' : '选择主代码';
    list.setAttribute('aria-label', title.textContent);
  }
  function open(action) {
    if (!dialog || typeof dialog.show !== 'function' || !canOpen(action)) return;
    hideSkipPrompt();
    if (action === 'primary' && !quickMode) setQuickMode(true);
    setPickerMode(action); if (!opened) previousFocus = document.activeElement;
    search.value = ''; dialog.hidden = false;
    if (!dialog.open) dialog.show();
    opened = true; search.setAttribute('aria-expanded', 'true');
    render(action === 'primary' ? primary.value : secondary.find(input => input.checked)?.value); list.focus();
  }
  if (form && primary && toolbar && dialog && typeof dialog.show === 'function') {
    form.dataset.shortcutsReady = 'true';
    const anchor = form.querySelector('[data-code-picker]') || form.querySelector('[data-primary-fallback]');
    if (anchor) anchor.before(toolbar);
    toolbar.hidden = false;
    openButtons.forEach(button => button.addEventListener('click', () => open(button.dataset.shortcutOpen)));
    skipButtons.forEach(button => button.addEventListener('click', requestSkip));
    nextLinks.forEach(link => link.addEventListener('click', event => {
      if (event.defaultPrevented || event.button && event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const target = new URL(link.href, location.href);
      if (target.origin !== location.origin || target.hash) { navigationError(new Error('无法找到下一条，请刷新后重试。')); return; }
      requestSkip(target.pathname + target.search);
    }));
    if (nextLinks.length) form.dataset.nextReminderReady = 'true';
    closeButton.addEventListener('click', close);
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    skipPrompt?.querySelector('[data-shortcut-skip-confirm]')?.addEventListener('click', confirmSkip);
    skipPrompt?.querySelector('[data-shortcut-skip-cancel]')?.addEventListener('click', () => { hideSkipPrompt(); form.focus(); });
    search.addEventListener('input', () => render());
    form.addEventListener('change', event => {
      const codeChange = event.target === primary || secondary.includes(event.target);
      if (codeChange) setPickerMode(event.target === primary ? 'primary' : 'secondary');
      refreshButtons();
      if (opened && codeChange) render(mode === 'primary' ? primary.value : secondary.find(input => input.checked)?.value);
    });
    form.addEventListener('input', refreshButtons);
    if (typeof MutationObserver !== 'undefined') {
      const observer = new MutationObserver(refreshButtons);
      observer.observe(primary, {attributes:true, attributeFilter:['disabled']});
      observer.observe(form, {attributes:true, attributeFilter:['data-locked', 'data-splitting', 'data-split-preview', 'data-busy', 'data-saving-draft']});
    }
    refreshButtons();
  }
  updateHints();
  showQuickMode();
  window.addEventListener('platform:split-preview-change', () => {
    refreshButtons();
    if (quickMode && form?.dataset.splitPreview !== 'true' && !opened) open(mode);
  });
  window.addEventListener('platform:shortcuts-updated', showQuickMode);
  window.addEventListener('platform:task-completion-open', () => { hideSkipPrompt(); close(); });
  window.addEventListener('storage', event => {
    if (event.key !== storageKey && event.key !== null) return;
    try {
      const current = readBindings(event.newValue);
      if (current.error) return;
      bindings = current.values; updateHints(); showQuickMode();
      if (!recording) { pending = {...bindings}; refreshSettings(); if (current.warning) message(current.warning, true); }
    } catch (_) { /* A partial or invalid storage update never replaces active shortcuts. */ }
  });
  window.addEventListener('pageshow', () => {
    try {
      const saved = localStorage.getItem(storageKey);
      const current = readBindings(saved);
      if (current.error) return;
      bindings = current.values; updateHints(); showQuickMode();
      if (!recording) { pending = {...bindings}; refreshSettings(); if (current.warning) message(current.warning, true); }
    } catch (_) { /* Back/forward restoration keeps active shortcuts if storage is unavailable. */ }
    if (quickModeKey) {
      try {
        quickMode = sessionStorage.getItem(quickModeKey) === 'on'; showQuickMode();
        if (quickMode && !opened) open('primary');
        else if (!quickMode && opened) close();
      } catch (_) { /* Unavailable session storage does not interrupt the current coding input. */ }
    }
  });
  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.repeat || event.getModifierState?.('AltGraph')) return;
    if (completionDialog?.open) return;
    if (recording && document.activeElement === recording) {
      if (event.key === 'Tab') return;
      event.preventDefault();
      if (event.key === 'Escape') { pending[recording.dataset.shortcutRecord] = recordingOriginal; recording.blur(); recording = null; refreshSettings(); message('已取消本次录入。'); return; }
      if (['Control', 'Alt', 'Shift', 'Meta'].includes(event.key)) return;
      const chord = eventChord(event), error = shortcutError(chord, recording.dataset.shortcutRecord);
      if (error) { message(error, true); return; }
      pending[recording.dataset.shortcutRecord] = chord; refreshSettings(); message('组合键已录入，点击“保存快捷键”生效。'); return;
    }
    const chord = eventChord(event);
    if (form?.dataset.splitPreview === 'true') {
      if (quickMode && chord && chord === bindings.pickerClose) { event.preventDefault(); exitQuickMode(); }
      return;
    }
    if (quickMode && chord && chord === bindings.skip) { event.preventDefault(); requestSkip(); return; }
    if (quickMode && chord && !textEntry(document.activeElement)) {
      if (chord === bindings.modePrimary || chord === bindings.modeSecondary) { event.preventDefault(); open(chord === bindings.modePrimary ? 'primary' : 'secondary'); return; }
      if (chord === bindings.pickerUp || chord === bindings.pickerDown) {
        event.preventDefault();
        if (!canOpen(mode)) return;
        if (!opened) open(mode);
        if (opened) { list.focus(); markActive(active + (chord === bindings.pickerDown ? 1 : -1)); }
        return;
      }
      if (opened && chord === bindings.pickerConfirm && document.activeElement !== form && !nativeActivation(document.activeElement)) {
        event.preventDefault(); commit(); return;
      }
    }
    if (opened && [list, search, closeButton].includes(document.activeElement)) {
      const action = chord ? pickerActions.find(name => bindings[name] === chord) : null;
      const textInput = action && ['pickerUp', 'pickerDown'].includes(action) && /^[A-Z]$/.test(chord) && document.activeElement !== list;
      const closeActivation = action === 'pickerConfirm' && chord === 'Enter' && event.target === closeButton;
      if (action && !textInput && !closeActivation) {
        event.preventDefault();
        if (action === 'pickerClose') exitQuickMode();
        else if (action === 'pickerConfirm') commit();
        else markActive(active + (action === 'pickerDown' ? 1 : -1));
      }
      else if (event.key === 'Escape') { event.preventDefault(); close(); }
      else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault(); markActive(event.key === 'Home' ? 0 : event.key === 'End' ? filtered.length - 1 : active + (event.key === 'ArrowDown' ? 1 : -1));
      } else if (event.key === 'Enter' && event.target !== closeButton) { event.preventDefault(); commit(); }
      else if (event.key === 'Tab') {
        if (document.activeElement === search && !event.shiftKey) { event.preventDefault(); list.focus(); }
      }
      else if (chord && globalActions.some(name => bindings[name] === chord)) {
        event.preventDefault();
        if (chord === bindings.primary) open('primary');
        else if (chord === bindings.secondary) open('secondary');
      }
      return;
    }
    if (event.defaultPrevented || !form) return;
    if (quickMode && chord && chord === bindings.pickerClose) { event.preventDefault(); exitQuickMode(); return; }
    const target = document.activeElement;
    const codingFocus = target === form || target?.matches?.('[data-code-choice]')
      || !textEntry(target) && !nativeActivation(target) && (target?.tagName !== 'INPUT' || form.contains(target));
    if (quickMode && chord && chord === bindings.pickerConfirm && codingFocus && (!skipPrompt || skipPrompt.hidden)) { event.preventDefault(); advance(); return; }
    const action = chord ? globalActions.find(name => bindings[name] === chord) : null;
    if (!action) return;
    event.preventDefault();
    if (action === 'primary' || action === 'secondary') { open(action); return; }
    const button = form.querySelector(`[data-action="${action === 'next' ? 'submit_next' : 'save'}"]`);
    if (canEdit() && button && !button.disabled) button.click();
  });
  if (quickMode) open('primary');
})();
