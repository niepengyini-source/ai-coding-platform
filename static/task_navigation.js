'use strict';
(() => {
  const button = document.querySelector('[data-coding-last]');
  const dialog = document.querySelector('[data-task-completion]');
  const form = document.querySelector('.annotation-form');
  if (!button || !dialog || !form || typeof dialog.showModal !== 'function') return;
  const status = dialog.querySelector('[data-task-completion-status]');
  const resume = dialog.querySelector('[data-task-continue]');
  const save = dialog.querySelector('[data-task-save]');
  const cancel = dialog.querySelector('[data-task-cancel]');
  const retry = dialog.querySelector('[data-task-retry]');
  let target = '', checking = false, leaving = false;
  function blocked() { return form.dataset.navigationPending === 'true' || form.dataset.splitting === 'true' || form.dataset.splitPreview === 'true' || form.dataset.busy === 'true' && form.dataset.savingDraft !== 'true'; }
  function close() { if (!checking && !leaving) { dialog.close(); button.focus(); } }
  async function check() {
    if (checking || leaving) return;
    checking = true; target = ''; resume.disabled = true; retry.hidden = true;
    status.className = ''; status.textContent = '正在检查编码进度…';
    try {
      const response = await fetch(dialog.dataset.progressUrl, {credentials:'same-origin', cache:'no-store'});
      if (!response.ok || !(response.headers.get('content-type') || '').includes('application/json')) throw new Error('暂时无法检查进度，请重试。');
      const data = await response.json();
      if (!Number.isInteger(data.my_remaining) || data.my_remaining < 0) throw new Error('进度信息无效，请重新检查。');
      const scope = Number(form.dataset.shortcutScope);
      if (data.my_remaining && !new RegExp(`^/r/${scope}/\\?a=\\d+$`).test(data.next_unfinished_url)) throw new Error('未完成任务已变化，请重新检查。');
      target = data.next_unfinished_url || '';
      resume.disabled = !target;
      status.textContent = data.my_remaining ? `还有 ${data.my_remaining} 条未完成。是否继续？草稿也计入未完成。` : '本批编码已全部完成。';
      status.className = data.my_remaining ? '' : 'task-complete-success';
    } catch (error) { status.textContent = error.message || '暂时无法检查进度，请重试。'; status.className = 'error'; retry.hidden = false; }
    finally { checking = false; }
  }
  async function leave(destination) {
    if (checking || leaving || blocked() || !destination) return;
    if (destination !== dialog.dataset.projectsUrl && !new RegExp(`^/r/${Number(form.dataset.shortcutScope)}/\\?a=\\d+$`).test(destination)) return;
    if (destination === dialog.dataset.projectsUrl && destination !== '/projects/') return;
    leaving = true; resume.disabled = true; save.disabled = true; cancel.disabled = true; retry.disabled = true;
    try {
      if (typeof form.platformSaveBeforeNavigation !== 'function') throw new Error('保存功能尚未就绪，请刷新后重试。');
      if (await form.platformSaveBeforeNavigation({force:form.dataset.locked !== 'true'}) === false) throw new Error('当前编码尚未保存，请重试。');
      location.assign(destination);
    } catch (error) {
      status.textContent = error.message || '保存失败，当前输入仍保留，请重试。'; status.className = 'error';
    } finally { leaving = false; resume.disabled = !target; save.disabled = false; cancel.disabled = false; retry.disabled = false; }
  }
  button.addEventListener('click', () => {
    if (blocked() || dialog.open) return;
    window.dispatchEvent(new Event('platform:task-completion-open'));
    dialog.showModal(); check();
  });
  resume.addEventListener('click', () => leave(target));
  save.addEventListener('click', () => leave(dialog.dataset.projectsUrl));
  cancel.addEventListener('click', close);
  retry.addEventListener('click', check);
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
  button.disabled = false;
})();
