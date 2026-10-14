(() => {
  'use strict';
  document.querySelectorAll('[data-archive-form]').forEach(form => {
    const selection = form.querySelector('[name="selection"]');
    const limit = form.querySelector('[name="limit"]');
    const limitPanel = form.querySelector('[data-archive-limit]');
    const checkboxes = Array.from(form.querySelectorAll('[name="selected"]'));
    const exportButton = form.querySelector('[value="custom"]');
    const summary = form.querySelector('[data-archive-selection-summary]');
    if (!selection || !limit || !exportButton) return;
    const total = Number(form.dataset.archiveCount || 0);
    let searchChanged = false;
    function update() {
      const first = selection.value === 'first';
      const selected = selection.value === 'selected';
      const checked = checkboxes.filter(input => input.checked).length;
      const quantity = Number(limit.value);
      limit.disabled = !first;
      limit.required = first;
      if (limitPanel) limitPanel.hidden = !first;
      exportButton.disabled = searchChanged || !total || (selected && !checked) ||
        (first && (!Number.isInteger(quantity) || quantity < 1 || quantity > 50000));
      if (summary) summary.textContent = searchChanged ? '筛选条件已变化，请先预览' : selected ? `将导出本页勾选的 ${checked} 条` :
        first ? (quantity > 0 ? `将导出最多 ${Math.min(quantity, total)} 条` : '填写要导出的条数') :
        `将导出全部 ${total} 条匹配记录（包含其他页）`;
    }
    selection.addEventListener('change', update);
    limit.addEventListener('input', update);
    checkboxes.forEach(input => input.addEventListener('change', () => {
      selection.value = 'selected';
      update();
    }));
    const searchFields = ['scope', 'mode', 'q', 'document', 'round_id', 'code', 'status',
      'metadata_field', 'metadata_value', 'start', 'end', 'include_superseded'];
    searchFields.forEach(name => {
      const input = form.querySelector(`[name="${name}"]`);
      if (!input) return;
      ['input', 'change'].forEach(event => input.addEventListener(event, () => {
        searchChanged = true;
        update();
      }));
    });
    update();
  });
})();
