'use strict';
function splitPreview(text, offset) {
  const characters = Array.from(text);
  if (!Number.isInteger(offset) || offset <= 0 || offset >= characters.length) throw new Error('请选择文本中间的拆分位置。');
  const parts = [characters.slice(0, offset).join(''), characters.slice(offset).join('')];
  if (parts.some(part => !part.trim())) throw new Error('拆分后的两段都需要包含文字。');
  return parts;
}
function selectionPreview(text, start, end) {
  const characters = Array.from(text);
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || start >= end || end > characters.length) throw new Error('请选择有效的文字片段。');
  if (!characters.slice(start, end).join('').trim()) throw new Error('所选片段需要包含文字。');
  if (!characters.slice(0, start).join('').trim()) start = 0;
  if (!characters.slice(end).join('').trim()) end = characters.length;
  if (start === 0 && end === characters.length) throw new Error('请选择一部分文字，不要选中整条。');
  const parts = [[0,start],[start,end],[end,characters.length]].filter(([left,right]) => left < right).map(([left,right]) => characters.slice(left,right).join(''));
  return {parts, start, end};
}
function cutsPreview(text, cuts) {
  const characters = Array.from(text);
  if (!Array.isArray(cuts) || cuts.length < 1 || cuts.length > 49 || cuts.some((cut,index) => !Number.isInteger(cut) || cut <= 0 || cut >= characters.length || (index > 0 && cut <= cuts[index-1]))) throw new Error('最多拆分为 50 条，请调整选区。');
  const boundaries = [0,...cuts,characters.length];
  const parts = boundaries.slice(0,-1).map((left,index) => characters.slice(left,boundaries[index+1]).join(''));
  if (parts.some(part => !part.trim())) throw new Error('每个单元都需要包含文字，请调整选区。');
  return parts;
}
if (typeof module !== 'undefined') module.exports = {splitPreview, selectionPreview, cutsPreview};
(() => {
  if (typeof document === 'undefined') return;
  const panel = document.querySelector('[data-split-panel]');
  if (!panel) return;
  const text = document.getElementById('current-unit-text');
  const source = document.getElementById('unit-source-data');
  if (source) text.textContent = JSON.parse(source.textContent);
  const form = document.querySelector('.annotation-form');
  const previewButton = panel.querySelector('[data-split-preview]');
  const confirmButton = panel.querySelector('[data-split-confirm]');
  const undoButton = panel.querySelector('[data-split-undo]');
  const cancelButton = panel.querySelector('[data-split-cancel]');
  const modeControls = panel.querySelector('[data-split-controls]');
  const preview = panel.querySelector('[data-split-parts]');
  const list = panel.querySelector('[data-split-list]');
  const reason = panel.querySelector('[name=split_reason]');
  const status = panel.querySelector('[data-split-status]');
  const personal = panel.dataset.mode === 'personal';
  let selected = null, cached = null, cuts = [], changes = [], active = false, pending = null, busy = false, conflict = false;
  const tooShort = Array.from(text.textContent).filter(character => character.trim()).length < 2;
  function selectionBounds() {
    const selection = window.getSelection();
    if (!selection || !selection.rangeCount || selection.isCollapsed) return null;
    const range = selection.getRangeAt(0);
    if (!text.contains(range.startContainer) || !text.contains(range.endContainer)) return null;
    const prefix = document.createRange();
    prefix.selectNodeContents(text);
    prefix.setEnd(range.startContainer, range.startOffset);
    const start = Array.from(prefix.toString()).length;
    prefix.setEnd(range.endContainer, range.endOffset);
    return {start,end:Array.from(prefix.toString()).length};
  }
  function render() {
    list.replaceChildren();
    if (cuts.length) cutsPreview(text.textContent,cuts).forEach((part,index) => {
      const row = document.createElement('div');
      row.dataset.splitPiece = '';
      const title = document.createElement('h3');
      title.textContent = `单元 ${index+1}`;
      const content = document.createElement('pre');
      content.textContent = part;
      row.append(title,content);
      list.append(row);
    });
    modeControls.hidden = !active;
    preview.hidden = !active || !cuts.length;
    undoButton.disabled = busy || conflict || !!pending || !changes.length;
    cancelButton.disabled = busy || conflict || !!pending;
    confirmButton.disabled = busy || conflict || !cuts.length;
    confirmButton.textContent = personal ? `确认拆分并编码（${cuts.length+1}条）` : '发送建议';
    previewButton.disabled = tooShort || busy || conflict || !!pending;
    previewButton.textContent = active ? '继续选择文字' : '拆分单元';
    text.classList.toggle('split-selecting',active);
    if (tooShort) status.textContent = '此单元无需拆分';
  }
  function addSelection(bounds) {
    if (!bounds || busy || pending || conflict || tooShort) return;
    try {
      const next = selectionPreview(text.textContent,bounds.start,bounds.end);
      const length = Array.from(text.textContent).length;
      const nextCuts = [...new Set([...(personal ? cuts : []),next.start,next.end].filter(cut => cut > 0 && cut < length))].sort((left,right) => left-right);
      cutsPreview(text.textContent,nextCuts);
      cached = bounds;
      if (JSON.stringify(nextCuts) === JSON.stringify(cuts)) {
        selected = next;
        status.textContent = '此片段已划分';
        return;
      }
      changes.push({cuts:[...cuts],selected});
      cuts = nextCuts;
      selected = next;
      status.textContent = '';
      render();
    } catch (error) {
      status.textContent = error.message;
    }
  }
  function rememberSelection() {
    const bounds = selectionBounds();
    if (active) addSelection(bounds);
    else if (bounds) {
      try { selectionPreview(text.textContent,bounds.start,bounds.end); cached = bounds; } catch (_) { /* Keep the last valid selection. */ }
    }
  }
  text.addEventListener('mousedown',event => {
    if (active && !busy && !pending && !conflict && !event.shiftKey && (event.detail || 1) < 2 && (event.button || 0) === 0) window.getSelection()?.removeAllRanges();
  });
  text.addEventListener('mouseup',rememberSelection);
  text.addEventListener('keyup',rememberSelection);
  document.addEventListener('selectionchange',() => {
    const bounds = selectionBounds();
    if (bounds) {
      try { selectionPreview(text.textContent,bounds.start,bounds.end); cached = bounds; } catch (_) { /* Keep the last valid selection. */ }
    }
  });
  previewButton.addEventListener('mousedown', event => event.preventDefault());
  previewButton.addEventListener('click', () => {
    if (tooShort || busy || pending || conflict) return;
    const wasActive = active;
    active = true;
    render();
    const bounds = selectionBounds() || (!wasActive ? cached : null);
    if (bounds) addSelection(bounds);
  });
  undoButton.addEventListener('click',() => {
    if (busy || pending || conflict || !changes.length) return;
    const previous = changes.pop();
    cuts = previous.cuts;
    selected = previous.selected;
    cached = null;
    window.getSelection()?.removeAllRanges();
    status.textContent = '';
    render();
  });
  cancelButton.addEventListener('click',() => {
    if (busy || pending || conflict) return;
    active = false;
    cuts = [];
    changes = [];
    selected = cached = null;
    window.getSelection()?.removeAllRanges();
    status.textContent = '';
    render();
  });
  confirmButton.addEventListener('click', async () => {
    if (busy || conflict || selected === null) return;
    const controls = [...form.querySelectorAll('input,select,textarea,button')];
    try {
      if (!personal && !reason.value.trim()) throw new Error('请说明拆分理由。');
      busy = true;
      form.dataset.splitting = 'true';
      controls.forEach(el => el.disabled = true);
      render();
      if (!pending && personal) {
        status.textContent = '正在保存当前判断…';
        if (form.platformSaveBeforeSplit) await form.platformSaveBeforeSplit();
        else throw new Error('编码页面未准备好，请刷新后重试。');
      }
      pending = pending || {...(personal ? {cuts:[...cuts],start:selected.start} : {offset:selected.end}), revision:Number(form.dataset.revision),
        token:globalThis.crypto?.randomUUID?.() || 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
          const n = Math.floor(Math.random() * 16);
          return (c === 'x' ? n : (n & 3) | 8).toString(16);
        }), reason:reason?.value.trim() || ''};
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
        active = false;
        cuts = [];
        changes = [];
        selected = null;
        status.textContent = data.message;
      }
    } catch (error) {
      status.textContent = error.message || '网络异常，请使用原请求重试。';
    } finally {
      busy = false;
      form.dataset.splitting = String(conflict || !!pending);
      form.platformRestoreControls?.();
      render();
      if (reason) reason.disabled = conflict || !!pending;
    }
  });
  window.addEventListener('beforeunload', event => {
    if (busy || pending) { event.preventDefault(); event.returnValue = ''; }
  });
  window.addEventListener('platform:before-back', event => { if (busy || pending) event.preventDefault(); });
  render();
})();
