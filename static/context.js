'use strict';
(() => {
  const panel = document.querySelector('[data-context-loader]');
  if (!panel) return;
  const status = panel.querySelector('[data-context-status]');
  function row(record) {
    const el = document.createElement('div');
    el.className = 'context-row';
    const index = document.createElement('span');
    index.className = 'context-index';
    index.textContent = record.position;
    const body = document.createElement('div');
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
