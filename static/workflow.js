'use strict';
(() => {
  const panel = document.querySelector('[data-progress-endpoint]');
  if (!panel) return;
  const status = document.getElementById('progress-update-status');
  const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const renderCounts = (container, values, attribute) => {
    if (!container || !values) return;
    container.querySelectorAll(`[${attribute}]`).forEach(element => {
      const key = element.getAttribute(attribute);
      if (number(values[key])) element.textContent = String(values[key]);
    });
    const bar = container.querySelector('[data-summary-bar], [data-row-bar]');
    if (bar && number(values.percent)) bar.value = Math.min(100, values.percent);
  };
  let pending = false;
  async function refresh() {
    if (document.hidden || pending) return;
    pending = true;
    try {
      const response = await fetch(panel.dataset.progressEndpoint, {credentials: 'same-origin'});
      if (!response.ok || !(response.headers.get('content-type') || '').includes('application/json')) {
        throw new Error('progress unavailable');
      }
      const data = await response.json();
      renderCounts(panel.querySelector('[data-summary="my"]'), data.my, 'data-metric');
      renderCounts(panel.querySelector('[data-summary="team"]'), data.team, 'data-metric');
      for (const row of Array.isArray(data.rows) ? data.rows : []) {
        if (Number.isSafeInteger(row.round_id) && row.round_id > 0) {
          renderCounts(panel.querySelector(`[data-round-progress="${row.round_id}"]`), row, 'data-row-metric');
        }
      }
      for (const member of Array.isArray(data.members) ? data.members : []) {
        if (Number.isSafeInteger(member.coder_id) && member.coder_id > 0) {
          renderCounts(panel.querySelector(`[data-member-progress="${member.coder_id}"]`), member, 'data-row-metric');
        }
      }
      if (status) status.textContent = `进度已更新 · ${new Date().toLocaleTimeString()}。新增批次或成员请刷新页面。`;
    } catch (_) {
      if (status) status.textContent = '进度暂未更新，请稍后刷新；不会影响已保存的编码。';
    } finally {
      pending = false;
    }
  }
  setInterval(refresh, 10000);
})();
