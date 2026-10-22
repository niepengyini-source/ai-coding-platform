'use strict';
(() => {
  const panel = document.querySelector('[data-context-loader]');
  if (!panel) return;
  const status = panel.querySelector('[data-context-status]');
  function sourceInformation(data) {
    if (!data || !Array.isArray(data.items)) return null;
    const summary = document.createElement('div');
    summary.className = 'source-information';
    const meta = document.createElement('div');
    meta.className = 'source-information-meta';
    data.items.forEach(item => {
      if (!item || typeof item.label !== 'string' || typeof item.value !== 'string' || !item.value) return;
      const entry = document.createElement('span');
      entry.className = 'source-information-item';
      const label = document.createElement('span');
      label.className = 'source-information-label';
      label.textContent = item.label;
      entry.append(label, ' ' + item.value);
      meta.append(entry);
    });
    if (meta.children.length) summary.append(meta);
    const reference = data.reference;
    if (reference && typeof reference.label === 'string' && typeof reference.text === 'string') {
      const details = document.createElement('details');
      details.className = 'source-reference';
      const title = document.createElement('summary');
      title.textContent = reference.label + (Number.isInteger(reference.position) && reference.position > 0
        ? ' · 原记录 ' + reference.position : '');
      const text = document.createElement('p');
      text.textContent = reference.text;
      details.append(title, text);
      summary.append(details);
    }
    return summary.children.length ? summary : null;
  }
  function row(record) {
    const el = document.createElement('div');
    el.className = 'context-row';
    const index = document.createElement('span');
    index.className = 'context-index';
    index.textContent = record.position;
    const body = document.createElement('div');
    const information = sourceInformation(record.source_information);
    if (information) body.append(information);
    const text = document.createElement('p');
    text.textContent = record.text;
    body.append(text);
    if (record.context_key) {
      const key = document.createElement('small');
      key.className = 'muted';
      key.textContent = record.context_key;
      body.append(key);
    }
    el.append(index, body);
    return el;
  }
  panel.querySelectorAll('[data-context-more]').forEach(button => {
    button.addEventListener('click', async () => {
      if (button.disabled) return;
      const direction = button.dataset.contextMore;
      button.disabled = true;
      status.textContent = '正在加载…';
      try {
        const query = new URLSearchParams({direction, cursor: button.dataset.cursor});
        const response = await fetch(panel.dataset.endpoint + '?' + query, {credentials: 'same-origin'});
        if (!(response.headers.get('content-type') || '').includes('application/json')) throw new Error('登录已失效，请重新登录。');
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || '加载失败，请重试。');
        const fragment = document.createDocumentFragment();
        data.records.forEach(record => fragment.append(row(record)));
        const container = panel.querySelector('[data-context-' + direction + ']');
        if (direction === 'before') container.prepend(fragment);
        else container.append(fragment);
        button.dataset.cursor = String(data.cursor);
        button.disabled = !data.has_more;
        button.textContent = data.has_more ? (direction === 'before' ? '↑ 查看上文 +3' : '↓ 查看下文 +3')
          : (direction === 'before' ? '已到最前' : '已到最后');
        status.textContent = '';
      } catch (error) {
        button.disabled = false;
        status.textContent = error.message || '加载失败，请重试。';
      }
    });
  });
})();
