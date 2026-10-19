'use strict';
(() => {
  const panel = document.querySelector('[data-unsplit-panel]');
  const form = document.querySelector('.annotation-form');
  const source = document.getElementById('split-restore-data');
  if (!panel || !form || !source) return;
  const option = JSON.parse(source.textContent);
  const button = panel.querySelector('[data-unsplit-confirm]');
  const status = panel.querySelector('[data-unsplit-status]');
  let busy = false, pending = null, conflict = false;
  function render() {
    button.disabled = busy || conflict || form.dataset.splitPreview === 'true' || form.dataset.splitting === 'true' && !pending;
    button.textContent = pending ? '重试撤回拆分' : '撤回本次拆分';
  }
  window.addEventListener('platform:split-preview-change', render);
  button.addEventListener('click', async () => {
    if (busy || conflict || form.dataset.splitPreview === 'true' || form.dataset.splitting === 'true' && !pending) return;
    if (!pending && !window.confirm('撤回本次拆分，恢复原句？片段编码将保留在历史记录中。')) return;
    const controls = [...form.querySelectorAll('input,select,textarea,button')];
    try {
      busy = true;
      form.dataset.splitting = 'true';
      window.dispatchEvent(new Event('platform:unit-operation-change'));
      controls.forEach(control => control.disabled = true);
      render();
      if (!pending) {
        status.textContent = '正在保存当前编码…';
        if (!form.platformSaveBeforeSplit) throw new Error('编码页面未准备好，请刷新后重试。');
        await form.platformSaveBeforeSplit();
        pending = {parent_id:option.parent_id, parent_revision:option.parent_revision, branch_token:option.branch_token,
          revision:Number(form.dataset.revision), book_id:form.dataset.bookId ? Number(form.dataset.bookId) : undefined,
          token:globalThis.crypto?.randomUUID?.() || 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, character => {
            const number = Math.floor(Math.random() * 16);
            return (character === 'x' ? number : (number & 3) | 8).toString(16);
          }), confirm_coded:true};
      }
      status.textContent = '正在撤回拆分…';
      const response = await fetch(panel.dataset.endpoint, {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json', 'X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value}, body:JSON.stringify(pending)});
      if (!(response.headers.get('content-type') || '').includes('application/json')) throw new Error('未确认撤回成功，请核对登录状态后重试。');
      const data = await response.json();
      if (!response.ok) {
        if (response.status !== 503) pending = null;
        if (response.status === 409) conflict = true;
        throw new Error(data.error || '未确认撤回成功，请重试。');
      }
      pending = null;
      conflict = true;
      const target = data.next_url || '';
      if (!new RegExp(`^/r/${Number(form.dataset.shortcutScope)}/\\?a=\\d+$`).test(target)) throw new Error('拆分已撤回，请刷新工作台查看原句。');
      busy = false;
      location.assign(target);
    } catch (error) {
      status.textContent = error.message || '网络异常，请使用原请求重试。';
    } finally {
      busy = false;
      form.dataset.splitting = String(conflict || !!pending);
      form.platformRestoreControls?.();
      window.dispatchEvent(new Event('platform:unit-operation-change'));
      render();
    }
  });
  window.addEventListener('beforeunload', event => { if (busy || pending) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('platform:before-back', event => { if (busy || pending) event.preventDefault(); });
  render();
})();
