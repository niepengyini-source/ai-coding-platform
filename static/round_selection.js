'use strict';

const roundSelectionTools = (() => {
  function parsePositions(value, total, limit = 10000) {
    const result = new Set();
    for (const part of value.trim().replace(/\s*-\s*/g, '-').split(/[\s,，、;；]+/).filter(Boolean)) {
      const match = /^([0-9]+)(?:-([0-9]+))?$/.exec(part);
      if (!match) throw Error('请输入材料顺序号或区间，例如 3、8、15-30。');
      const first = Number(match[1]), last = Number(match[2] || match[1]);
      if (!Number.isSafeInteger(first) || !Number.isSafeInteger(last) || first < 1 || last < first || last > total)
        throw Error(`材料顺序号应在1至${total}之间，区间从小到大填写。`);
      if (last - first + 1 > limit) throw Error(`一次最多选择${limit}条，请缩小范围。`);
      for (let position = first; position <= last; position++) {
        result.add(position);
        if (result.size > limit) throw Error(`一次最多选择${limit}条，请缩小范围。`);
      }
    }
    return [...result];
  }

  function createSelection(index, initial = [], limit = 10000) {
    const allowed = new Set(index.map(String));
    let selected = new Set(), version = 0;
    function add(values) {
      const merged = new Set(selected);
      values.forEach(value => {
        const id = String(value);
        if (!allowed.has(id)) throw Error('部分材料已不可用，请刷新页面后重新选择。');
        merged.add(id);
      });
      if (merged.size > limit) throw Error(`一次最多选择${limit}条，请缩小范围。`);
      selected = merged;
      version++;
    }
    add(initial);
    return {add, has:id => selected.has(String(id)), values:() => [...selected],
      remove:id => { selected.delete(String(id)); version++; },
      clear:() => { selected.clear(); version++; },
      get size() { return selected.size; }, get version() { return version; }};
  }

  function requestSequence() {
    let current = 0;
    return {next:() => ++current, isCurrent:ticket => ticket === current};
  }
  return {parsePositions, createSelection, requestSequence};
})();

if (typeof module !== 'undefined' && module.exports) module.exports = roundSelectionTools;

if (typeof document !== 'undefined') (() => {
  const slidingSwitch = document.querySelector?.('[data-sliding-switch]');
  if (slidingSwitch) {
    const links = [...slidingSwitch.querySelectorAll('a')];
    links.forEach(link => link.addEventListener('click', event => {
      if (link.getAttribute('aria-current') === 'page' || event.metaKey || event.ctrlKey || event.shiftKey || event.button) return;
      event.preventDefault();
      links.forEach(item => item.removeAttribute('aria-current'));
      link.setAttribute('aria-current', 'page');
      slidingSwitch.dataset.active = link.dataset.switchKey || '';
      setTimeout(() => location.assign(link.href), 240);
    }));
  }
  const docForm = document.getElementById('document-round-arrangement');
  if (docForm) {
    const syncOption = docForm.querySelector('[data-sync-only-option]');
    const syncCheckbox = docForm.querySelector('[name="share_annotations"]');
    const syncToggle = () => {
      const isSync = docForm.querySelector('[name="collab_mode"]:checked')?.value === 'sync';
      if (syncOption) syncOption.dataset.syncDisabled = String(!isSync);
      if (syncCheckbox) syncCheckbox.disabled = !isSync;
    };
    docForm.addEventListener('change', syncToggle);
    syncToggle();
  }
  const form = document.getElementById('round-arrangement');
  const indexElement = document.getElementById('round-unit-index');
  if (!form || !indexElement) return;
  const index = JSON.parse(indexElement.textContent).map(String);
  const byName = name => form.querySelector(`[name="${name}"]`);
  const find = name => form.querySelector(`[data-round-${name}]`);
  const mode = () => form.querySelector('[name="selection_mode"]:checked').value;
  const retained = byName('unit_ids'), bulk = byName('manual_positions');
  const preview = find('preview'), status = find('status'), list = find('list');
  const countLabel = find('selected-count'), showSelected = find('show-selected');
  const matching = find('select-matching'), filter = find('filter');
  const previous = find('previous'), next = find('next'), pageLabel = find('page');
  const fields = {q:document.getElementById('round-unit-query'), document:document.getElementById('round-unit-document'),
    metadata_field:document.getElementById('round-unit-field'), metadata_value:document.getElementById('round-unit-value')};
  const readFilters = () => Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, input.value]));
  let appliedFilters = readFilters();
  const initialIds = retained.value.split(/[\s,，、;；]+/).filter(Boolean);
  let selected, recoveryRequired = false;
  try { selected = roundSelectionTools.createSelection(index, initialIds); }
  catch (_) {
    recoveryRequired = true;
    const available = new Set(index);
    const validIds = initialIds.filter(id => available.has(id));
    selected = roundSelectionTools.createSelection(index, validIds.length <= 10000 ? validIds : []);
  }
  const requests = roundSelectionTools.requestSequence();
  let controller, currentPage = 1, pages = 1, viewingSelected = false, stale = false, busy = false;
  const initialHash = byName('pool_hash').value;
  const issue = element('div', undefined, 'notice danger');
  const issueText = element('p');
  const refresh = element('button', '重新载入材料', 'button secondary');
  issue.setAttribute('role', 'alert'); refresh.type = 'button';
  refresh.addEventListener('click', () => location.assign(location.pathname + location.search));
  issue.append(issueText, refresh); form.prepend(issue);

  function recoveryNotice() {
    issue.hidden = !recoveryRequired && !stale;
    issueText.textContent = stale
      ? '材料已发生变化，当前选择仍保留。请重新载入材料后核对。'
      : '原已选内容中有无法恢复的材料，尚未清除。请切换到“手动选择”后点击“清空已选”重新选择，或重新载入材料。';
  }

  function message(text, error = false) {
    status.textContent = text;
    status.dataset.error = String(error);
  }
  function pagination() {
    previous.disabled = busy || currentPage <= 1;
    next.disabled = busy || currentPage >= pages;
    filter.disabled = busy || stale;
    matching.disabled = busy || stale || viewingSelected;
    preview.disabled = busy || stale || recoveryRequired;
    recoveryNotice();
  }
  function selectedSummary() {
    if (!recoveryRequired) retained.value = selected.values().join(',');
    countLabel.textContent = `已选${selected.size}条（${recoveryRequired ? '另有旧选择待处理' : '跨页保留'}）`;
    showSelected.textContent = viewingSelected ? '返回全部材料' : `查看已选（${selected.size}）`;
    showSelected.setAttribute('aria-pressed', String(viewingSelected));
    list.querySelectorAll('input[type="checkbox"]').forEach(input => { input.checked = selected.has(input.value); });
  }
  function query(page, all = false, filters = appliedFilters) {
    const values = {...filters};
    values.page = page;
    if (viewingSelected) values.selected = selected.values().map(Number);
    if (all) values.all = true;
    return values;
  }
  async function choices(payload) {
    const ticket = requests.next();
    if (controller) controller.abort();
    controller = new AbortController();
    busy = true; pagination(); message('正在读取材料…');
    try {
      const response = await fetch(form.dataset.unitChoicesUrl, {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json', 'X-CSRFToken':byName('csrfmiddlewaretoken').value},
        body:JSON.stringify(payload), signal:controller.signal});
      let data;
      try { data = await response.json(); }
      catch (_) { throw Error('读取材料失败，请重试或刷新后重新登录。'); }
      if (!requests.isCurrent(ticket)) return null;
      if (!response.ok) throw Error(data.error || '读取材料失败，请重试或刷新后重新登录。');
      if (data.pool_hash !== initialHash) {
        stale = true;
        throw Error('材料已发生变化，请刷新页面后重新核对顺序号和已选内容。');
      }
      return data;
    } catch (error) {
      if (requests.isCurrent(ticket) && error.name !== 'AbortError') message(error.message || '读取材料失败，请重试。', true);
      return null;
    } finally {
      if (requests.isCurrent(ticket)) { busy = false; pagination(); }
    }
  }
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function render(data) {
    currentPage = data.page; pages = Math.max(1, data.pages);
    list.replaceChildren();
    for (const unit of data.results) {
      const row = element('label', undefined, 'round-unit-option');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox'; checkbox.value = String(unit.id); checkbox.checked = selected.has(unit.id);
      checkbox.setAttribute('aria-label', `选择材料第${unit.position}条`);
      checkbox.addEventListener('change', () => {
        try {
          if (checkbox.checked) selected.add([unit.id]); else selected.remove(unit.id);
          selectedSummary();
          if (viewingSelected) load(currentPage);
        } catch (error) { checkbox.checked = false; message(error.message, true); }
      });
      const copy = element('span', undefined, 'round-unit-copy');
      copy.append(element('strong', `材料第${unit.position}条`), element('p', unit.text),
        element('small', `${unit.document} · 原记录${unit.source_position} · 系统编号 #${unit.id}`));
      row.append(checkbox, copy); list.append(row);
    }
    if (!data.results.length) list.append(element('p', viewingSelected ? '已选清单中暂无匹配材料。' : '暂无匹配材料，请调整筛选条件。', 'round-picker-empty'));
    pageLabel.textContent = `${currentPage} / ${pages}页 · ${data.count}条`;
    matching.textContent = `选择全部匹配（${data.count}）`;
    message(`${viewingSelected ? '已选清单' : '当前筛选'}：找到${data.count}条。勾选会跨页保留。`);
    pagination(); selectedSummary();
  }
  async function load(page = 1, applyFilters = false) {
    const filters = applyFilters ? readFilters() : appliedFilters;
    const data = await choices(query(page, false, filters));
    if (data && mode() === 'manual') { appliedFilters = filters; render(data); }
  }
  function addPositions() {
    try {
      const positions = roundSelectionTools.parsePositions(bulk.value, index.length);
      if (!positions.length) return true;
      selected.add(positions.map(position => index[position - 1]));
      bulk.value = ''; selectedSummary();
      message(`已加入选择，合计${selected.size}条。`);
      if (viewingSelected) load(1);
      return true;
    } catch (error) { message(error.message, true); bulk.focus(); return false; }
  }
  function updateMode() {
    const value = mode();
    form.querySelectorAll('[data-round-panel]').forEach(panel => {
      const active = panel.dataset.roundPanel === value;
      panel.hidden = !active;
      panel.querySelectorAll('input,select,textarea,button').forEach(input => { input.disabled = !active; });
    });
    const count = byName('count');
    find('count').hidden = value === 'manual'; count.disabled = value === 'manual';
    retained.disabled = value !== 'manual';
    find('count-hint').textContent = value === 'sequential'
      ? '这是本轮共选的条数，不是每人数量。填0表示从起点选到最后。'
      : '这是本轮的材料总数，不是每人数量。填0表示选取全部有效材料。';
    if (value === 'manual') { selectedSummary(); load(1, true); }
    else { requests.next(); if (controller) controller.abort(); busy = false; }
    pagination();
  }
  function updateDistribution() {
    const mixed = byName('distribution').value === 'mixed';
    find('mixed').hidden = !mixed; byName('double_percent').disabled = !mixed;
  }
  find('add').hidden = false; find('picker').hidden = false;
  form.querySelectorAll('[name="selection_mode"]').forEach(input => input.addEventListener('change', updateMode));
  byName('distribution').addEventListener('change', updateDistribution);
  find('add').addEventListener('click', addPositions);
  find('clear').addEventListener('click', () => {
    recoveryRequired = false; selected.clear(); selectedSummary(); pagination();
    if (viewingSelected) load(1); else message('已清空选择。');
  });
  showSelected.addEventListener('click', () => { viewingSelected = !viewingSelected; selectedSummary(); load(1); });
  filter.addEventListener('click', () => load(1, true));
  Object.values(fields).forEach(input => input.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); load(1, true); } }));
  previous.addEventListener('click', () => { if (currentPage > 1) load(currentPage - 1); });
  next.addEventListener('click', () => { if (currentPage < pages) load(currentPage + 1); });
  matching.addEventListener('click', async () => {
    const version = selected.version;
    const data = await choices(query(1, true));
    if (!data) return;
    if (version !== selected.version) { message('读取期间已选内容发生变化，请再次点击“选择全部匹配”。', true); return; }
    try { selected.add(data.ids); selectedSummary(); message(`已选择全部${data.ids.length}条匹配材料，合计${selected.size}条。`); }
    catch (error) { message(error.message, true); }
  });
  form.addEventListener('submit', event => { if (busy || stale || recoveryRequired || (mode() === 'manual' && !addPositions())) event.preventDefault(); });
  updateDistribution(); updateMode();
})();
