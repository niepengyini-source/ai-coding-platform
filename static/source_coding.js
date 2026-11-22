(function () {
  'use strict';
  const points = value => Array.from(value || '');
  const normalizeText = value => String(value || '').replace(/\r\n?/g, '\n');
  const regionKeys = ['x', 'y', 'width', 'height'];
  const sameRange = (left, right) => left && right && (left.region || right.region
    ? !!left.region && !!right.region && regionKeys.every(key => left.region[key] === right.region[key])
    : left.start === right.start && left.end === right.end);
  const regionName = region => region.width === 1 && region.height === 1 ? '整张图片' : '图片区域';
  const selectionName = value => value.region ? regionName(value.region) : value.text;
  function segments(text, start, annotations) {
    const chars = points(text), end = start + chars.length, events = new Map();
    const change = (at, kind, id) => {
      if (!events.has(at)) events.set(at, {add: [], remove: []});
      events.get(at)[kind].push(id);
    };
    annotations.forEach(annotation => {
      const left = Math.max(start, annotation.start), right = Math.min(end, annotation.end);
      if (left < right) { change(left, 'add', annotation.id); change(right, 'remove', annotation.id); }
    });
    const boundaries = [...new Set([start, end, ...events.keys()])].sort((a, b) => a - b);
    const active = new Set(), result = [];
    boundaries.slice(0, -1).forEach((left, index) => {
      const event = events.get(left);
      event?.remove.forEach(id => active.delete(id));
      event?.add.forEach(id => active.add(id));
      result.push({text: chars.slice(left - start, boundaries[index + 1] - start).join(''), ids: [...active].sort((a, b) => a - b)});
    });
    return result;
  }
  function fingerprint(value) {
    return JSON.stringify({start: value.start, end: value.end, text: normalizeText(value.text), region: value.region || null,
      primary: value.primary || null, secondary: [...(value.secondary || [])].sort((a, b) => a - b),
      labels: [...(value.labels || [])].sort((a, b) => a - b), uncertain: !!value.uncertain,
      no_code: !!value.no_code, note: value.note || '', new_code: value.new_code || null});
  }
  const helpers = {points, normalizeText, sameRange, segments, fingerprint};
  if (typeof module !== 'undefined' && module.exports) module.exports = helpers;
  if (typeof document === 'undefined') return;
  const root = document.querySelector('[data-source-coding]');
  if (!root) return;
  const data = JSON.parse(document.getElementById('source-coding-data').textContent);
  const find = selector => root.querySelector(selector), form = find('[data-source-annotation-form]');
  const reading = find('[data-source-reading]'), fieldset = find('[data-source-fields]');
  const imageStage = find('[data-source-image]'), imageSelection = find('[data-source-image-selection]');
  const selectionHint = find('[data-source-selection-hint]').textContent;
  const saveButton = find('[data-source-save]'), status = find('[data-source-status]');
  const completeButton = find('[data-source-complete]'), reloadButton = find('[data-source-reload]');
  const unsaved = document.querySelector('[data-source-unsaved]'), history = document.querySelector('[data-source-history]');
  const codeContainer = find('[data-source-code-list]'), overview = find('[data-source-annotation-list]');
  const source = points(data.text), pageStart = Number(data.start || 0);
  const csrf = form.querySelector('[name=csrfmiddlewaretoken]').value;
  let selected = null, baseline = '', dirty = false, busy = false, conflicted = false, pending = null;
  let listLimit = 50, activeLoad = 0, guardResolve = null, suppressMarkClickUntil = 0;
  const scrollKey = `source-reading:${data.task.id}:${new URLSearchParams(location.search).get('reader_page') || 1}`;
  const field = name => form.querySelector(`[name=${name}]`);
  const codeName = field('new_code_name'), codeDefinition = field('new_code_definition');
  const codeSearch = find('[data-source-code-search]'), emptyCodeHint = find('[data-source-empty-codes]');
  const clearNewCode = () => { if (codeName) { codeName.value = ''; codeDefinition.value = ''; } };
  const locked = () => data.round.state !== 'active' || data.task.completed;
  const setStatus = (message, kind = '') => { status.textContent = message; status.dataset.kind = kind; };
  const idToken = () => globalThis.crypto?.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  function state() {
    const name = codeName?.value.trim() || '', definition = codeDefinition?.value.trim() || '';
    return {...selected, primary: Number(form.querySelector('[name=primary]:checked')?.value) || null,
      secondary: [...form.querySelectorAll('[name=secondary]:checked')].map(input => Number(input.value)),
      labels: [...form.querySelectorAll('[name=labels]:checked')].map(input => Number(input.value)),
      no_code: field('no_code').checked, uncertain: field('uncertain').checked, note: field('note').value,
      new_code: name || definition ? {name, definition} : null};
  }
  function canSave() {
    if (!selected || busy || locked() || conflicted) return false;
    const value = state();
    if (value.new_code && !value.new_code.name) return false;
    const hasCode = !!value.new_code || !!value.primary || value.labels.length > 0;
    return (hasCode || value.no_code || value.uncertain) && (!value.uncertain || !!value.note.trim());
  }
  function refreshControls() {
    fieldset.disabled = !selected || busy || locked();
    saveButton.disabled = !canSave();
    completeButton.disabled = busy || data.round.state !== 'active';
    completeButton.textContent = data.task.completed ? '继续编辑本份材料' : '本份材料编码完成';
    find('[data-source-task-state]').textContent = data.round.state !== 'active' ? '只读' : data.task.completed ? '材料已完成' : selected ? (dirty ? '未保存' : '已保存') : '等待划选';
    imageStage?.classList.toggle('source-image-locked', busy || locked());
    find('[data-source-image-whole]')?.toggleAttribute('disabled', busy || locked());
    find('[data-source-image-form] button')?.toggleAttribute('disabled', busy || locked());
    document.querySelector('[data-source-finish]')?.toggleAttribute('disabled', busy || data.round.state !== 'active');
    reloadButton.hidden = !conflicted;
  }
  function renderCodes(value = selected || {}) {
    codeContainer.replaceChildren();
    codeSearch.hidden = !data.codes.length;
    if (emptyCodeHint) emptyCodeHint.hidden = data.codes.length > 0;
    if (!data.codes.length) return;
    const policy = data.book.policy;
    const groups = policy === 'parallel' ? [['labels', '选择代码（可多选）', 'checkbox']] :
      [['primary', '选择代码', 'radio'], ...(policy === 'multi' ? [['secondary', '次代码（可选）', 'radio']] : [])];
    groups.forEach(([name, title, type]) => {
      const group = document.createElement('section'), heading = document.createElement('p'), options = document.createElement('div');
      group.className = 'source-code-group'; heading.textContent = title; options.className = 'source-code-options';
      data.codes.forEach(code => {
        const label = document.createElement('label'), input = document.createElement('input'), copy = document.createElement('span');
        label.className = 'source-code-option'; input.type = type; input.name = name; input.value = code.id;
        input.checked = name === 'primary' ? Number(value.primary) === code.id : (value[name] || []).includes(code.id);
        if (name === 'secondary' && Number(value.primary) === code.id) input.disabled = true;
        input.setAttribute('aria-label', code.key === code.name ? code.name : `${code.key} · ${code.name}`);
        copy.textContent = code.name || code.key;
        if (code.key && code.key !== code.name) { const key = document.createElement('small'); key.textContent = code.key; copy.append(key); }
        if (code.definition) label.title = code.definition;
        label.dataset.codeSearch = `${code.key} ${code.name} ${code.definition || ''}`.toLocaleLowerCase();
        label.append(input, copy); options.append(label);
      });
      group.append(heading, options);
      if (name === 'secondary') {
        const clear = document.createElement('button'); clear.className = 'text-button'; clear.type = 'button'; clear.textContent = '清空次代码';
        clear.addEventListener('click', () => { group.querySelectorAll('input').forEach(input => { input.checked = false; }); edited(); });
        group.append(clear);
      }
      codeContainer.append(group);
    });
    filterCodes();
  }
  function filterCodes() {
    const query = codeSearch.value.trim().toLocaleLowerCase();
    codeContainer.querySelectorAll('[data-code-search]').forEach(label => { label.hidden = !!query && !label.dataset.codeSearch.includes(query); });
  }
  function codeSummary(annotation) {
    if (annotation.no_code) return '无适用代码';
    const ids = [annotation.primary, ...(annotation.secondary || []), ...(annotation.labels || [])].filter(Boolean);
    const labels = annotation.code_names?.length ? annotation.code_names : ids.map(id => data.codes.find(code => code.id === id)?.name || data.codes.find(code => code.id === id)?.key || '历史代码');
    return [labels.join(' · '), annotation.uncertain ? '需复核' : ''].filter(Boolean).join(' · ') || '尚未选码';
  }
  function renderList() {
    overview.replaceChildren();
    const annotations = [...data.annotations].sort((left, right) => left.start - right.start || left.end - right.end || left.id - right.id);
    if (!annotations.length) { const empty = document.createElement('li'); empty.className = 'hint'; empty.textContent = imageStage ? '尚未保存图片区域。' : '尚未保存片段。请在正文中划选需要编码的文字。'; overview.append(empty); }
    annotations.slice(0, listLimit).forEach(annotation => {
      const li = document.createElement('li'), button = document.createElement('button'), summary = document.createElement('small');
      button.type = 'button'; button.textContent = selectionName(annotation); button.setAttribute('aria-current', String(selected?.id === annotation.id));
      summary.textContent = codeSummary(annotation); button.append(summary);
      button.addEventListener('click', () => openAnnotation(annotation)); li.append(button); overview.append(li);
    });
    if (annotations.length > listLimit) {
      const li = document.createElement('li'), button = document.createElement('button'); button.type = 'button';
      button.textContent = `显示更多片段（${listLimit} / ${annotations.length}）`;
      button.addEventListener('click', () => { listLimit += 50; renderList(); }); li.append(button); overview.append(li);
    }
    find('[data-source-count]').textContent = data.annotations.length;
  }
  function renderHighlights() {
    showImageRegion(selected?.region);
    reading.querySelectorAll('[data-source-text]').forEach(paragraph => {
      const start = Number(paragraph.dataset.start), end = Number(paragraph.dataset.end);
      const text = source.slice(start - pageStart, end - pageStart).join('');
      const fragment = document.createDocumentFragment();
      segments(text, start, data.annotations).forEach(piece => {
        if (!piece.ids.length) fragment.append(document.createTextNode(piece.text));
        else {
          const mark = document.createElement('mark'); mark.textContent = piece.text; mark.dataset.sourceAnnotations = piece.ids.join(',');
          mark.dataset.sourceCurrent = String(piece.ids.includes(selected?.id)); mark.tabIndex = 0; mark.setAttribute('role', 'button');
          mark.setAttribute('aria-label', `查看此处 ${piece.ids.length} 个已保存片段`); fragment.append(mark);
        }
      });
      paragraph.replaceChildren(fragment);
    });
  }
  function showImageRegion(region) {
    if (!imageSelection) return;
    regionKeys.forEach(key => imageSelection.setAttribute(key, Math.round((region?.[key] || 0) * 10000)));
  }
  function progress(result) {
    if (result.task) data.task = result.task;
    if (result.progress) data.progress = result.progress;
    document.querySelectorAll('[data-source-progress]').forEach(element => { element.textContent = `${data.progress.done} / ${data.progress.total}`; });
    find('[data-source-total-count]').textContent = data.progress.annotations;
    refreshControls();
  }
  function displaySelection(value, saved = false) {
    selected = {...value}; conflicted = false; pending = null;
    clearNewCode();
    renderCodes(value); field('no_code').checked = !!value.no_code; field('uncertain').checked = !!value.uncertain; field('note').value = value.note || '';
    find('[data-source-selection-hint]').textContent = saved ? (locked() ? '已保存的片段，只读查看。' : '已保存的片段，可继续修改。') : '当前选中的片段';
    const preview = find('[data-source-selection-text]'); preview.hidden = false; preview.textContent = selectionName(value);
    const labels = ['左', '上', '宽', '高'];
    const position = value.region ? regionKeys.map((key, index) => `${labels[index]} ${Number((value.region[key] * 100).toFixed(2))}%`).join(' · ') : `${points(value.text).length} 个字符`;
    find('[data-source-selection-position]').textContent = `${position}${value.id ? ' · 已保存' : ' · 新片段'}`;
    baseline = saved ? fingerprint(state()) : ''; dirty = !saved;
    const instruction = codeName ? '请选择已有代码或写入新代码，再保存。' : '请选择代码并保存。';
    setStatus(saved ? '已保存。修改后请再次保存。' : instruction, saved ? 'success' : '');
    find('[data-source-overlap-list]').hidden = true;
    refreshControls(); renderList(); renderHighlights();
  }
  function edited() {
    if (!selected || busy || locked()) return;
    const value = state();
    form.querySelectorAll('[name=secondary]').forEach(input => {
      input.disabled = Number(input.value) === value.primary;
      if (input.disabled) input.checked = false;
    });
    dirty = fingerprint(state()) !== baseline;
    const message = value.new_code && !value.new_code.name ? '请先写入代码。' : '有未保存的修改。';
    setStatus(dirty ? message : '已保存。', dirty ? '' : 'success'); refreshControls();
  }
  async function request(url, payload) {
    let response;
    try { response = await fetch(url, {method: payload ? 'POST' : 'GET', credentials: 'same-origin',
      headers: payload ? {'Content-Type': 'application/json', 'X-CSRFToken': csrf} : {}, body: payload ? JSON.stringify(payload) : undefined}); }
    catch (_) { throw new Error('网络连接中断，输入仍保留。请重试保存。'); }
    let result;
    try { result = await response.json(); } catch (_) { throw new Error('无法确认请求结果，输入仍保留。请检查登录状态后重试。'); }
    if (!response.ok) { const error = new Error(result.error || '操作未完成，输入仍保留。'); error.status = response.status; throw error; }
    return result;
  }
  async function saveSelection() {
    if (!selected || busy || locked() || conflicted) return false;
    if (!canSave()) {
      const message = state().new_code?.name === '' ? '请先写入代码。' : '请选择或写入代码，或标记无适用代码／需复核；需复核时请填写原因。';
      setStatus(message, 'error'); return false;
    }
    const value = state(), key = fingerprint(value);
    if (!pending || pending.key !== key) pending = {key, payload: {...value, revision: selected.revision || 0,
      book_id: data.book.id, book_revision: data.book.revision, token: idToken()}};
    busy = true; refreshControls(); setStatus('正在保存…');
    try {
      const result = await request(data.urls.annotations, pending.payload), annotation = result.annotation;
      if (result.codes) { data.codes = result.codes; data.book.revision = result.book_revision; codeSearch.value = ''; }
      const index = data.annotations.findIndex(item => item.id === annotation.id);
      if (index < 0) data.annotations.push(annotation); else data.annotations[index] = annotation;
      busy = false; progress(result); displaySelection(annotation, true);
      return true;
    } catch (error) {
      if (error.status === 409) conflicted = true;
      setStatus(error.message, 'error'); return false;
    } finally { busy = false; refreshControls(); }
  }
  async function settleDraft() {
    if (busy) { setStatus('正在保存，请稍后再切换。'); return false; }
    if (!dirty) return true;
    if (guardResolve) return false;
    document.querySelector('[data-source-unsaved-status]').textContent = '';
    return new Promise(resolve => { guardResolve = resolve; unsaved.showModal(); });
  }
  function resolveGuard(answer) { const resolve = guardResolve; guardResolve = null; unsaved.close(); resolve?.(answer); }
  document.querySelector('[data-source-leave-save]').addEventListener('click', async () => {
    if (await saveSelection()) resolveGuard(true);
    else document.querySelector('[data-source-unsaved-status]').textContent = status.textContent;
  });
  document.querySelector('[data-source-leave-discard]').addEventListener('click', () => {
    if (selected?.id) displaySelection(selected, true);
    else {
      selected = null; baseline = ''; dirty = false; pending = null; conflicted = false;
      clearNewCode(); renderCodes(); field('no_code').checked = false; field('uncertain').checked = false; field('note').value = '';
      find('[data-source-selection-text]').hidden = true; find('[data-source-selection-position]').textContent = '';
      find('[data-source-selection-hint]').textContent = selectionHint;
      setStatus('等待划选'); refreshControls(); renderList(); renderHighlights();
    }
    resolveGuard(true);
  });
  document.querySelector('[data-source-leave-cancel]').addEventListener('click', () => resolveGuard(false));
  unsaved.addEventListener('cancel', event => { event.preventDefault(); resolveGuard(false); });
  async function openAnnotation(annotation, guarded = true) {
    if (guarded && !(await settleDraft())) return;
    const paragraph = [...reading.querySelectorAll('[data-source-text]')].find(element => Number(element.dataset.start) <= annotation.start && Number(element.dataset.end) > annotation.start);
    if (!paragraph && !annotation.region && annotation.url) { location.assign(annotation.url); return; }
    const load = ++activeLoad;
    try {
      const url = new URL(data.urls.annotations, location.href); url.searchParams.set('id', annotation.id);
      const result = await request(url), full = result.annotation || result;
      if (load !== activeLoad) return;
      displaySelection(full, true);
      if (paragraph) reading.scrollTop += paragraph.getBoundingClientRect().top - reading.getBoundingClientRect().top - 18;
    } catch (error) { setStatus(error.message, 'error'); }
  }
  function endpoint(container, offset) {
    const element = container.nodeType === Node.ELEMENT_NODE ? container : container.parentElement;
    const paragraph = element?.closest('[data-source-text]');
    if (!paragraph || !reading.contains(paragraph)) return null;
    const prefix = document.createRange(); prefix.selectNodeContents(paragraph); prefix.setEnd(container, offset);
    return Number(paragraph.dataset.start) + points(prefix.toString()).length;
  }
  async function readSelection() {
    if (locked() || busy) return;
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return;
    const range = selection.getRangeAt(0), start = endpoint(range.startContainer, range.startOffset), end = endpoint(range.endContainer, range.endOffset);
    if (start === null || end === null || end <= start) return;
    suppressMarkClickUntil = performance.now() + 350;
    const value = {start, end, text: source.slice(start - pageStart, end - pageStart).join(''), primary: null, secondary: [], labels: [], note: '', revision: 0};
    if (!value.text.trim() || sameRange(selected, value)) return;
    if (!(await settleDraft())) return;
    const existing = data.annotations.find(item => sameRange(item, value));
    if (existing) await openAnnotation(existing, false); else { activeLoad++; displaySelection(value); }
  }
  reading.addEventListener('mouseup', readSelection);
  reading.addEventListener('touchend', () => setTimeout(readSelection, 0));
  reading.addEventListener('keyup', event => { if (event.key.startsWith('Arrow') || event.key === 'Shift') readSelection(); });
  if (imageStage) {
    let drag = null;
    const position = event => {
      const rect = imageStage.getBoundingClientRect();
      const fraction = (offset, size) => Math.round(Math.max(0, Math.min(1, offset / size)) * 10000);
      return {x: fraction(event.clientX - rect.left, rect.width), y: fraction(event.clientY - rect.top, rect.height)};
    };
    const rectangle = (from, to) => ({x: Math.min(from.x, to.x) / 10000, y: Math.min(from.y, to.y) / 10000,
      width: Math.abs(from.x - to.x) / 10000, height: Math.abs(from.y - to.y) / 10000});
    async function selectRegion(region) {
      showImageRegion(selected?.region);
      if (busy || locked() || !region.width || !region.height) return;
      if (region.x + region.width > 1.00000001 || region.y + region.height > 1.00000001) {
        setStatus('请选择图片范围内有效的矩形区域。', 'error'); return;
      }
      const value = {start: 0, end: 0, text: '', region, primary: null, secondary: [], labels: [], note: '', revision: 0};
      if (sameRange(selected, value) || !(await settleDraft())) return;
      const existing = data.annotations.find(item => sameRange(item, value));
      if (existing) await openAnnotation(existing, false); else { activeLoad++; displaySelection(value); }
    }
    imageStage.addEventListener('pointerdown', event => {
      if (busy || locked() || event.button !== 0 || !event.isPrimary || !imageStage.querySelector('img').naturalWidth) return;
      imageStage.setPointerCapture(event.pointerId);
      drag = {id: event.pointerId, point: position(event)};
    });
    imageStage.addEventListener('pointermove', event => {
      if (drag?.id === event.pointerId) showImageRegion(rectangle(drag.point, position(event)));
    });
    imageStage.addEventListener('pointerup', event => {
      if (drag?.id !== event.pointerId) return;
      const region = rectangle(drag.point, position(event)); drag = null; selectRegion(region);
    });
    imageStage.addEventListener('pointercancel', () => { drag = null; showImageRegion(selected?.region); });
    find('[data-source-image-whole]').addEventListener('click', () => selectRegion({x: 0, y: 0, width: 1, height: 1}));
    find('[data-source-image-form]').addEventListener('submit', event => {
      event.preventDefault();
      const region = Object.fromEntries(regionKeys.map(key => [key, Math.round(Number(event.currentTarget.elements[key].value) * 100) / 10000]));
      selectRegion(region);
    });
  }
  function showOverlaps(mark) {
    const annotations = mark.dataset.sourceAnnotations.split(',').map(id => data.annotations.find(item => item.id === Number(id))).filter(Boolean);
    if (annotations.length === 1) { openAnnotation(annotations[0]); return; }
    const choices = find('[data-source-overlap-list]'); choices.replaceChildren();
    const hint = document.createElement('p'); hint.className = 'hint'; hint.textContent = '此处包含多个片段，请选择要查看的记录。'; choices.append(hint);
    annotations.forEach(annotation => { const button = document.createElement('button'); button.type = 'button'; button.textContent = `${annotation.text} · ${codeSummary(annotation)}`; button.addEventListener('click', () => openAnnotation(annotation)); choices.append(button); });
    choices.hidden = false;
  }
  reading.addEventListener('click', event => {
    const mark = event.target.closest('[data-source-annotations]');
    if (mark && performance.now() > suppressMarkClickUntil && window.getSelection()?.isCollapsed) showOverlaps(mark);
  });
  reading.addEventListener('keydown', event => {
    const mark = event.target.closest('[data-source-annotations]');
    if (mark && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); showOverlaps(mark); }
  });
  form.addEventListener('submit', event => { event.preventDefault(); saveSelection(); });
  form.addEventListener('input', event => {
    if (event.target === codeSearch) return;
    if (event.target === codeName && codeName.value.trim()) {
      field('no_code').checked = false;
      const primary = form.querySelector('[name=primary]:checked');
      if (primary) primary.checked = false;
    }
    edited();
  });
  form.addEventListener('change', event => {
    if (event.target.name === 'no_code' && field('no_code').checked) {
      clearNewCode();
      form.querySelectorAll('[name=primary],[name=secondary],[name=labels]').forEach(input => { input.checked = false; });
    }
    if (event.target.name === 'primary' && event.target.checked) clearNewCode();
    if (['primary', 'secondary', 'labels'].includes(event.target.name) && event.target.checked) field('no_code').checked = false;
    edited();
  });
  codeSearch.addEventListener('input', filterCodes);
  saveButton.addEventListener('click', saveSelection);
  reloadButton.addEventListener('click', () => {
    if (window.confirm('刷新会放弃当前未保存的修改。请先复制需要保留的内容，确定刷新核对？')) { dirty = false; location.reload(); }
  });
  completeButton.addEventListener('click', async () => {
    if (!(await settleDraft())) return;
    busy = true; refreshControls();
    try { const result = await request(data.urls.complete, {completed: !data.task.completed, revision: data.task.revision}); progress(result); setStatus(data.task.completed ? '本份材料已标记完成。' : '已重新打开，可以继续编码。', 'success'); }
    catch (error) { setStatus(error.message, 'error'); }
    finally { busy = false; refreshControls(); }
  });
  document.querySelector('[data-source-finish]')?.addEventListener('click', async () => {
    if (!(await settleDraft()) || !window.confirm('结束后本批编码只读保留。确认所有材料已经完成并检查？')) return;
    busy = true; refreshControls();
    try { const result = await request(data.urls.finish, {revision: data.round.revision}); data.round = result.round; document.querySelector('[data-source-round-state]').textContent = '已结束'; setStatus('本批编码已结束，记录只读保留。', 'success'); }
    catch (error) { setStatus(error.message, 'error'); }
    finally { busy = false; refreshControls(); }
  });
  document.querySelector('[data-source-history-close]').addEventListener('click', () => history.close());
  document.querySelector('[data-source-history-open]').addEventListener('click', async () => {
    const content = document.querySelector('[data-source-history-content]'); content.replaceChildren(); history.showModal();
    if (!selected?.id) { content.textContent = '请先打开一个已保存片段。'; return; }
    content.textContent = '正在读取…';
    try {
      const result = await request(`${data.urls.history}${selected.id}/history/`); content.replaceChildren();
      (result.events || []).forEach(event => {
        const section = document.createElement('section'), heading = document.createElement('p'), detail = document.createElement('pre'); section.className = 'source-history-event';
        heading.textContent = [event.created_at, event.actor, event.reason].filter(Boolean).join(' · ');
        const after = event.after || {}; detail.textContent = [selectionName(after), codeSummary(after), after.note].filter(Boolean).join('\n') || '已保留本次修改记录。'; section.append(heading, detail); content.append(section);
      });
      if (!content.children.length) content.textContent = '暂无修改历史。';
    } catch (error) { content.textContent = error.message; }
  });
  document.addEventListener('click', async event => {
    const link = event.target.closest('a[href]');
    if (!link || event.defaultPrevented || !dirty || link.target === '_blank' || link.hasAttribute('download') || link.id === 'page-back' || event.ctrlKey || event.metaKey || event.shiftKey || event.button) return;
    const target = new URL(link.href, location.href);
    if (!['http:', 'https:'].includes(target.protocol) || target.href === location.href || target.pathname === location.pathname && target.search === location.search && target.hash) return;
    event.preventDefault(); if (await settleDraft()) location.assign(target.href);
  });
  window.addEventListener('platform:before-back', event => {
    if (!dirty) return;
    event.preventDefault(); settleDraft().then(continueBack => { if (continueBack) document.getElementById('page-back')?.click(); });
  });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pagehide', () => { try { sessionStorage.setItem(scrollKey, String(reading.scrollTop)); } catch (_) {} });
  document.addEventListener('keydown', event => {
    if (event.isComposing || event.repeat || history.open || unsaved.open || document.querySelector('[data-workbench-menu][open]')) return;
    if (event.ctrlKey && event.key === 'Enter' || event.altKey && !event.ctrlKey && event.key.toLowerCase() === 'f') { event.preventDefault(); saveSelection(); }
  });
  find('[data-source-collapse]').addEventListener('click', event => {
    const collapsed = root.classList.toggle('source-list-collapsed'); event.currentTarget.setAttribute('aria-expanded', String(!collapsed));
    event.currentTarget.setAttribute('aria-label', collapsed ? '展开材料与片段' : '收起材料与片段');
  });
  renderCodes(); renderList(); renderHighlights(); refreshControls();
  if (innerWidth <= 1100) find('[data-source-collapse]').click();
  const initialAnnotation = data.annotations.find(annotation => annotation.id === Number(data.selected_annotation_id));
  if (initialAnnotation) {
    const load = openAnnotation(initialAnnotation, false);
    if (location.hash === '#source-history') Promise.resolve(load).then(() => document.querySelector('[data-source-history-open]').click());
  }
  else { try { reading.scrollTop = Number(sessionStorage.getItem(scrollKey)) || 0; } catch (_) {} }
})();
