'use strict';
document.querySelectorAll('.nav-item').forEach(a => {
  if (new URL(a.href).pathname === location.pathname) {
    a.classList.add('active');
    const group = a.closest('details');
    if (group) group.open = true;
  }
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
  let secondaryChanged = false, savedSignal = Promise.resolve(), finishSave;
  const legacySecondary = (initial.secondary || []).length > 1 ? initial.secondary : null;
  const legacyNotice = form.querySelector('[data-legacy-secondary]');
  if (legacySecondary && legacyNotice) {
    const labels = [...form.querySelectorAll('[name=secondary]')].filter(el => legacySecondary.includes(Number(el.value))).map(el => el.parentElement.textContent.trim());
    legacyNotice.textContent = '原次代码：' + labels.join('；') + '。修改时请选择一个，原记录保留在历史中。';
    legacyNotice.hidden = false;
  }
  form.dataset.revision = revision;
  const locked = form.dataset.locked === 'true';
  if (locked) form.querySelectorAll('input,textarea,select,button').forEach(el => el.disabled = true);
  const setStatus = (text, kind = '') => { status.textContent = text; status.className = `save-status ${kind}`; };
  const uuid = () => globalThis.crypto?.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const payload = action => ({revision, action, token: uuid(), primary: primary.value ? Number(primary.value) : null,
    secondary: legacySecondary && !secondaryChanged ? legacySecondary : [...form.querySelectorAll('[name=secondary]:checked')].map(el => Number(el.value)),
    uncertain: form.querySelector('[name=uncertain]').checked, no_code: form.querySelector('[name=no_code]').checked, note: note.value});
  const schedule = () => {
    clearTimeout(timer);
    if (form.dataset.mode === 'annotation' && !halted && !locked) timer = setTimeout(() => write('save'), 1200);
  };
  const hasJudgment = () => !!(primary.value || form.querySelector('[name=no_code]').checked || form.querySelector('[name=uncertain]').checked)
    && (!form.querySelector('[name=uncertain]').checked || !!note.value.trim());
  const restoreControls = () => {
    const stopped = locked || committed || halted || form.dataset.splitting === 'true';
    const pendingSubmit = pending?.payload.action === 'submit';
    form.querySelectorAll('input:not([type=hidden]),textarea,select').forEach(el => el.disabled = stopped || pendingSubmit);
    form.querySelectorAll('button').forEach(el => el.disabled = stopped || pendingSubmit);
    const needsChoice = form.dataset.mode === 'annotation' && !hasJudgment();
    form.querySelectorAll('button[data-action]').forEach(el => el.disabled = stopped || inFlight || needsChoice);
    const choiceHint = form.querySelector('[data-choice-required]');
    if (choiceHint) {
      choiceHint.hidden = !needsChoice || stopped;
      choiceHint.textContent = form.querySelector('[name=uncertain]').checked ? '请补充不确定的原因' : '先选择代码';
    }
    form.platformRefreshCodes?.();
  };
  form.platformRestoreControls = restoreControls;
  async function write(action) {
    if (locked || halted || inFlight) return;
    clearTimeout(timer);
    const advance = action === 'submit_next';
    const saveAction = advance ? 'submit' : action;
    const savedRequest = pending || {payload: payload(saveAction), generation};
    const currentGeneration = savedRequest.generation;
    const request = savedRequest.payload;
    pending = savedRequest;
    let succeeded = false;
    let destination = null;
    inFlight = true;
    savedSignal = new Promise(resolve => finishSave = resolve);
    restoreControls();
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
      form.dataset.revision = revision;
      pending = null;
      succeeded = true;
      dirty = generation !== currentGeneration;
      if (data.annotation.status === 'submitted') {
        halted = true;
        committed = true;
        dirty = false;
        form.querySelectorAll('input,textarea,select,button').forEach(el => el.disabled = true);
        setStatus('已提交并锁定。重新打开时仍可查看。', 'success');
        const target = data.next_url || form.dataset.nextUrl || '';
        if (advance && /^\/(?:r\/\d+\/\?a=\d+|p\/\d+\/progress\/)$/u.test(target)) destination = target;
      } else {
        setStatus(dirty ? '已有新编辑，准备继续保存…' : `已保存 · 版本${revision} · ${new Date().toLocaleTimeString()}`, 'success');
      }
    } catch (error) {
      halted = false;
      setStatus(error.message || '网络异常，未确认保存。你的输入仍在，请稍后重试。', 'error');
    } finally {
      inFlight = false;
      restoreControls();
      finishSave();
      // Do not loop indefinitely on a network error or validation error.
      if (succeeded && saveAction === 'submit' && request.action !== 'submit') {
        Promise.resolve().then(() => write(action));
      } else if (dirty && !pending && generation !== currentGeneration) schedule();
      if (destination) location.assign(destination);
    }
  }
  form.addEventListener('input', event => {
    if (event?.target?.name === 'code_search') return;
    dirty = true; generation++; setStatus('有未保存的编辑'); restoreControls(); schedule();
  });
  form.addEventListener('change', event => {
    if (event?.target?.name === 'code_search') return;
    if (event?.target?.name === 'secondary') {
      secondaryChanged = true;
      if (event.target.checked) {
        form.querySelector('[name=no_code]').checked = false;
        form.querySelectorAll('[name=secondary]').forEach(el => { if (el !== event.target) el.checked = false; });
      }
      if (legacyNotice) legacyNotice.hidden = true;
    }
    dirty = true; generation++; restoreControls(); schedule();
  });
  primary.addEventListener('change', () => {
    form.querySelectorAll('[name=secondary]').forEach(el => { if (!primary.value || el.value === primary.value) el.checked = false; });
    if (primary.value) form.querySelector('[name=no_code]').checked = false;
    if (!primary.value) secondaryChanged = true;
  });
  if (form.dataset.mode === 'decision') form.querySelector('[name=no_code]').addEventListener('change', event => {
    if (event.target.checked) {
      primary.value = '';
      primary.dispatchEvent(new Event('change', {bubbles:true}));
    }
  });
  form.querySelector('[data-secondary-clear]')?.addEventListener('click', () => {
    if (primary.disabled) return;
    const choices = [...form.querySelectorAll('[name=secondary]')];
    choices.forEach(el => el.checked = false);
    choices[0]?.dispatchEvent(new Event('change', {bubbles:true}));
  });
  form.platformSaveBeforeSplit = async () => {
    clearTimeout(timer);
    if (pending?.payload.action === 'submit') throw new Error('正在提交，请确认完成后再拆分。');
    if (inFlight) await savedSignal;
    if (!locked && !committed && (dirty || pending)) await write('save');
    clearTimeout(timer);
    if (dirty || pending || inFlight || halted && !committed) throw new Error('当前编码尚未保存，请先核对保存提示后重试。');
    return revision;
  };
  form.addEventListener('submit', event => { event.preventDefault(); if (form.dataset.mode !== 'annotation' || hasJudgment()) write(event.submitter?.dataset.action || 'save'); });
  form.addEventListener('keydown', event => { if (event.ctrlKey && event.key === 'Enter') { event.preventDefault(); if (form.dataset.mode !== 'annotation' || hasJudgment()) write('save'); } });
  restoreControls();
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
