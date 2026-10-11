'use strict';
function splitPreview(text, offset) {
  const characters = Array.from(text);
  if (!Number.isInteger(offset) || offset <= 0 || offset >= characters.length) throw new Error('请选择文本中间的拆分位置。');
  const parts = [characters.slice(0, offset).join(''), characters.slice(offset).join('')];
  if (parts.some(part => !part.trim())) throw new Error('拆分后的两段都需要包含文字。');
  return parts;
}
if (typeof module !== 'undefined') module.exports = {splitPreview};
(() => {
  if (typeof document === 'undefined') return;
  const panel = document.querySelector('[data-split-panel]');
  if (!panel) return;
  const text = document.getElementById('current-unit-text');
  const form = document.querySelector('.annotation-form');
  const previewButton = panel.querySelector('[data-split-preview]');
  const confirmButton = panel.querySelector('[data-split-confirm]');
  const preview = panel.querySelector('[data-split-parts]');
  const reason = panel.querySelector('[name=split_reason]');
  const status = panel.querySelector('[data-split-status]');
  const personal = panel.dataset.mode === 'personal';
  let offset = null, pending = null, busy = false, conflict = false;
  function clean() {
    if (personal && Number(form.dataset.revision) !== 0) throw new Error('本条已保存。为保留判断，请继续编码，不拆分已有答案。');
    if (!pending && !window.dispatchEvent(new Event('platform:before-back', {cancelable:true}))) {
      throw new Error('有未确认保存的输入，请先保留并核对。个人拆分需在选择代码或写备注之前进行。');
    }
  }
  previewButton.addEventListener('mousedown', event => event.preventDefault());
  previewButton.addEventListener('click', () => {
    if (busy || pending || conflict) return;
    try {
      clean();
      const selection = window.getSelection();
      if (!selection || !selection.rangeCount || selection.isCollapsed) throw new Error('请先在当前原文中选中第一段文字，再点击预览。');
      const range = selection.getRangeAt(0);
      if (!text.contains(range.startContainer) || !text.contains(range.endContainer)) throw new Error('只能选择当前编码单元中的文字。');
      const prefix = document.createRange();
      prefix.selectNodeContents(text);
      prefix.setEnd(range.endContainer, range.endOffset);
      offset = Array.from(prefix.toString()).length;
      const parts = splitPreview(text.textContent, offset);
      panel.querySelector('[data-split-first]').textContent = parts[0];
      panel.querySelector('[data-split-second]').textContent = parts[1];
      preview.hidden = false;
      confirmButton.disabled = false;
      status.textContent = '请核对两段；原文不会删改。';
    } catch (error) {
      confirmButton.disabled = true;
      status.textContent = error.message;
    }
  });
  confirmButton.addEventListener('click', async () => {
    if (busy || conflict || offset === null) return;
    const controls = [...form.querySelectorAll('input,select,textarea,button')];
    const disabled = controls.map(el => el.disabled);
    try {
      clean();
      if (!personal && !reason.value.trim()) throw new Error('请说明拆分理由。');
      pending = pending || {offset, revision:Number(form.dataset.revision),
        token:globalThis.crypto?.randomUUID?.() || 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
          const n = Math.floor(Math.random() * 16);
          return (c === 'x' ? n : (n & 3) | 8).toString(16);
        }), reason:reason?.value.trim() || ''};
      busy = true;
      controls.forEach(el => el.disabled = true);
      previewButton.disabled = confirmButton.disabled = true;
      if (reason) reason.disabled = true;
      status.textContent = '正在确认，请等待…';
      const response = await fetch(panel.dataset.endpoint, {method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value},
        body:JSON.stringify(pending)});
      if (!(response.headers.get('content-type') || '').includes('application/json')) throw new Error('未确认成功。输入和原文仍保留，请核对登录状态后重试。');
      const data = await response.json();
      if (!response.ok) {
        if (response.status !== 503) pending = null;
        if (response.status === 409) conflict = true;
        throw new Error(data.error || '未确认成功，请重试。');
      }
      pending = null;
      if (personal) {
        busy = false;
        conflict = true; // The old assignment is now read-only, including while navigation finishes.
        if (!/^\/r\/\d+\/(?:\?a=\d+)?$/u.test(data.next_url || '')) throw new Error('拆分已完成，请刷新工作台查看新单元。');
        location.assign(data.next_url);
      } else {
        preview.hidden = true;
        offset = null;
        status.textContent = data.message;
      }
    } catch (error) {
      status.textContent = error.message || '网络异常，请使用原请求重试。';
    } finally {
      busy = false;
      controls.forEach((el, index) => el.disabled = disabled[index] || conflict || !!pending);
      previewButton.disabled = conflict || !!pending;
      confirmButton.disabled = conflict || offset === null;
      if (reason) reason.disabled = conflict || !!pending;
    }
  });
  window.addEventListener('beforeunload', event => {
    if (busy || pending) { event.preventDefault(); event.returnValue = ''; }
  });
  window.addEventListener('platform:before-back', event => { if (busy || pending) event.preventDefault(); });
})();
