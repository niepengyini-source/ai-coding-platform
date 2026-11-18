(() => {
  'use strict';
  const root = document.querySelector('[data-document-preparation]');
  if (!root) return;
  const reading = root.querySelector('[data-document-reading]');
  const form = root.querySelector('[data-document-selection]');
  const data = JSON.parse(document.getElementById('document-reader-data').textContent);
  const scrollKey = `document-reading:${location.pathname}:${new URLSearchParams(location.search).get('page') || 1}`;
  if (data.position !== null) {
    const blocks = Array.from(reading.querySelectorAll('[data-block-start]'));
    const target = blocks.filter(block => Number(block.dataset.blockStart) <= data.position).pop();
    if (target) target.scrollIntoView({block: 'start'});
  } else if (!location.hash) {
    try { reading.scrollTop = Number(sessionStorage.getItem(scrollKey)) || 0; } catch (_) {}
  }
  window.addEventListener('pagehide', () => {
    try { sessionStorage.setItem(scrollKey, String(reading.scrollTop)); } catch (_) {}
  });
  let pending = false;
  root.querySelectorAll('[data-document-panel]').forEach(button => {
    button.addEventListener('click', () => {
      const side = button.dataset.documentPanel;
      const collapsed = root.classList.toggle(`${side}-collapsed`);
      button.setAttribute('aria-expanded', String(!collapsed));
      button.setAttribute('aria-label', `${collapsed ? '展开' : '收起'}${side === 'outline' ? '文档目录' : '片段列表'}`);
    });
  });
  if (window.matchMedia('(max-width: 1100px)').matches) {
    root.classList.add('outline-collapsed');
    const toggle = root.querySelector('[data-document-panel="outline"]');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-label', '展开文档目录');
  }
  if (!form) return;
  const source = Array.from(data.text);
  const submit = form.querySelector('[data-add-selection]');
  const preview = form.querySelector('[data-selection-preview]');
  const hint = form.querySelector('[data-selection-hint]');
  const field = name => form.querySelector(`[name="${name}"]`);
  const endpoint = (container, offset) => {
    const element = container.nodeType === Node.ELEMENT_NODE ? container : container.parentElement;
    const paragraph = element && element.closest('[data-document-text]');
    if (!paragraph || !reading.contains(paragraph)) return null;
    const prefix = document.createRange();
    prefix.selectNodeContents(paragraph);
    prefix.setEnd(container, offset);
    return Number(paragraph.dataset.start) + Array.from(prefix.toString()).length;
  };
  reading.addEventListener('mouseup', () => {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    const start = endpoint(range.startContainer, range.startOffset);
    const end = endpoint(range.endContainer, range.endOffset);
    if (start === null || end === null || end <= start) return;
    const text = source.slice(start - data.start, end - data.start).join('');
    if (!text.trim()) return;
    field('start').value = start;
    field('end').value = end;
    field('text').value = text;
    preview.textContent = text;
    preview.hidden = false;
    hint.textContent = `已选择 ${Array.from(text).length} 个字符；加入后仍需确认统一片段。`;
    submit.disabled = false;
    pending = true;
    root.classList.remove('fragments-collapsed');
    const toggle = root.querySelector('[data-document-panel="fragments"]');
    toggle.setAttribute('aria-expanded', 'true');
    toggle.setAttribute('aria-label', '收起片段列表');
  });
  reading.addEventListener('keyup', () => {
    if (window.getSelection() && !window.getSelection().isCollapsed) {
      reading.dispatchEvent(new MouseEvent('mouseup'));
    }
  });
  root.querySelectorAll('form').forEach(current => current.addEventListener('submit', event => {
    if (pending && current !== form && !window.confirm('当前划选尚未加入候选片段，是否放弃这次划选并继续？')) {
      event.preventDefault();
      return;
    }
    pending = false;
  }));
  window.addEventListener('beforeunload', event => {
    if (!pending) return;
    event.preventDefault();
    event.returnValue = '';
  });
})();
