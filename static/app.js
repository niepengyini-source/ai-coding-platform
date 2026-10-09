'use strict';
document.querySelectorAll('.nav-item').forEach(a => {
  if (new URL(a.href).pathname === location.pathname) a.classList.add('active');
});
document.querySelectorAll('form[data-confirm]').forEach(el => el.addEventListener('submit', event => {
  if (!window.confirm(el.dataset.confirm)) event.preventDefault();
}));
const form = document.querySelector('.annotation-form');
if (form) {
  const initial = JSON.parse(document.getElementById('annotation-data').textContent);
  const status = form.querySelector('.save-status');
  const primary = form.querySelector('[name=primary]');
  const note = form.querySelector('[name=note]');
  primary.value = initial.primary || '';
  note.value = initial.note || '';
  ['uncertain','no_code'].forEach(k => form.querySelector(`[name=${k}]`).checked = !!initial[k]);
  form.querySelectorAll('[name=secondary]').forEach(el => el.checked = (initial.secondary || []).includes(Number(el.value)));
  let revision = initial.revision, dirty = false, generation = 0, inFlight = false, timer, halted = false, pending = null, committed = false;
  const locked = form.dataset.locked === 'true';
  if (locked) form.querySelectorAll('input,textarea,select,button').forEach(el => el.disabled = true);
  const setStatus = (text, kind = '') => { status.textContent = text; status.className = `save-status ${kind}`; };
  const uuid = () => globalThis.crypto?.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const payload = action => ({revision, action, token: uuid(), primary: primary.value ? Number(primary.value) : null,
    secondary: [...form.querySelectorAll('[name=secondary]:checked')].map(el => Number(el.value)),
    uncertain: form.querySelector('[name=uncertain]').checked, no_code: form.querySelector('[name=no_code]').checked, note: note.value});
  const schedule = () => {
    clearTimeout(timer);
    if (form.dataset.mode === 'annotation' && !halted && !locked) timer = setTimeout(() => write('save'), 1200);
  };
  async function write(action) {
    if (locked || halted || inFlight) return;
    clearTimeout(timer);
    const savedRequest = pending || {payload: payload(action), generation};
    const currentGeneration = savedRequest.generation;
    const request = savedRequest.payload;
    pending = savedRequest;
    let succeeded = false;
    inFlight = true;
    form.querySelectorAll('button').forEach(el => el.disabled = true);
    if (request.action === 'submit') form.querySelectorAll('input:not([type=hidden]),textarea,select').forEach(el => el.disabled = true);
    setStatus('正在保存，请等待确认…');
    try {
      const response = await fetch(form.dataset.endpoint, {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value}, body:JSON.stringify(request)});
      const contentType = response.headers.get('content-type') || '';
      if (!contentType.includes('application/json')) throw new Error('登录已失效或请求被拒绝。输入仍保留，请重新登录后核对。');
      const data = await response.json();
      if (!response.ok) {
        if (response.status !== 503) pending = null;
        if (response.status === 409) halted = true;
        setStatus(data.error || '未确认保存成功。', 'error');
        return;
      }
      revision = data.annotation.revision;
      pending = null;
      succeeded = true;
      dirty = generation !== currentGeneration;
      if (data.annotation.status === 'submitted') {
        halted = true;
        committed = true;
        dirty = false;
        form.querySelectorAll('input,textarea,select,button').forEach(el => el.disabled = true);
        setStatus('已提交并锁定。重新打开时仍可查看。', 'success');
      } else {
        setStatus(dirty ? '已有新编辑，准备继续保存…' : `已保存 · 版本${revision} · ${new Date().toLocaleTimeString()}`, 'success');
      }
    } catch (error) {
      halted = false;
      setStatus(error.message || '网络异常，未确认保存。你的输入仍在，请稍后重试。', 'error');
    } finally {
      inFlight = false;
      if (!committed) {
        form.querySelectorAll('input:not([type=hidden]),textarea,select').forEach(el => el.disabled = false);
        form.querySelectorAll('button').forEach(el => el.disabled = halted);
      }
      // Do not loop indefinitely on a network error or validation error.
      if (succeeded && action === 'submit' && request.action !== 'submit') {
        Promise.resolve().then(() => write('submit'));
      } else if (dirty && !pending && generation !== currentGeneration) schedule();
    }
  }
  form.addEventListener('input', () => { dirty = true; generation++; setStatus('有未保存的编辑'); schedule(); });
  form.addEventListener('change', () => { dirty = true; generation++; schedule(); });
  primary.addEventListener('change', () => {
    form.querySelectorAll('[name=secondary]').forEach(el => { if (el.value === primary.value) el.checked = false; });
  });
  form.addEventListener('submit', event => { event.preventDefault(); write(event.submitter?.dataset.action || 'save'); });
  form.addEventListener('keydown', event => { if (event.ctrlKey && event.key === 'Enter') { event.preventDefault(); write('save'); } });
  window.addEventListener('beforeunload', event => { if (dirty || inFlight || pending) { event.preventDefault(); event.returnValue = ''; } });
  // The global return button must never leave an unconfirmed coding edit behind.
  window.addEventListener('platform:before-back', event => {
    if (dirty || inFlight || pending) event.preventDefault();
  });
}
const workbenchPath = location.pathname.match(/^\/r\/(\d+)\/$/);
if (workbenchPath && document.getElementById('my-progress')) {
  const refreshProgress = async () => {
    if (document.hidden) return;
    try {
      const response = await fetch(`/api/rounds/${workbenchPath[1]}/progress/`, {credentials:'same-origin'});
      if (!response.ok || !(response.headers.get('content-type') || '').includes('application/json')) return;
      const data = await response.json();
      document.getElementById('my-progress').textContent = `${data.my_done} / ${data.my_total}`;
      document.getElementById('round-progress').textContent = `${data.done} / ${data.total}`;
    } catch (_) { /* Editing and save confirmations remain independent from progress polling. */ }
  };
  setInterval(refreshProgress, 10000);
}
