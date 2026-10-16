document.querySelectorAll('[data-export-rows]').forEach(form => {
  const selection = form.querySelector('[name="selection"]');
  const limit = form.querySelector('[name="limit"]');
  const retained = form.querySelector('[name="retained_selected"]');
  const visible = form.querySelector('[name="visible_ids"]');
  const summary = form.querySelector('[data-export-row-summary]');
  const limitPanel = form.querySelector('[data-export-row-limit]');
  const download = form.querySelector('[value="download"]');
  if (!selection || !limit || !retained || !visible || !summary || !limitPanel || !download) return;
  const checkboxes = [...form.querySelectorAll('[name="selected"]')];
  const total = Number(form.dataset.rowCount) || 0;
  let searchChanged = false;
  const selectedCount = () => {
    const ids = new Set(retained.value.split(',').filter(Boolean));
    visible.value.split(',').forEach(id => ids.delete(id));
    checkboxes.forEach(box => { if (box.checked) ids.add(box.value); });
    return ids.size;
  };
  const update = () => {
    const first = selection.value === 'first';
    const selected = selection.value === 'selected';
    const amount = Number(limit.value);
    const validAmount = Number.isInteger(amount) && amount >= 1 && amount <= 50000;
    const count = selectedCount();
    limitPanel.hidden = !first;
    limit.disabled = !first;
    limit.required = first;
    download.disabled = searchChanged || (first && (!validAmount || !total)) || (selected && !count);
    if (searchChanged) summary.textContent = '筛选条件已变化，请点击“筛选并预览”。';
    else if (selected) summary.textContent = `将导出勾选的 ${count} 条记录（包含其他页）。`;
    else if (first) summary.textContent = validAmount ? `将导出筛选结果的前 ${Math.min(amount, total)} 条记录。` : '请填写要导出的条数（1—50000）。';
    else summary.textContent = total ? `将导出全部 ${total} 条筛选结果。也可逐条勾选，支持跨页选择。` : '暂无符合条件的记录，可导出空表。';
  };
  selection.addEventListener('change', update);
  limit.addEventListener('input', update);
  checkboxes.forEach(box => box.addEventListener('change', () => { selection.value = 'selected'; update(); }));
  form.querySelectorAll('[data-export-row-select]').forEach(button => button.addEventListener('click', () => {
    const mode = button.dataset.exportRowSelect;
    if (mode === 'clear') retained.value = '';
    checkboxes.forEach(box => { box.checked = mode === 'all'; });
    selection.value = 'selected';
    update();
  }));
  ['row_query', 'row_match', 'row_metadata_field', 'row_metadata_value', 'row_status'].forEach(name => {
    const input = form.querySelector(`[name="${name}"]`);
    if (!input) return;
    ['input', 'change'].forEach(event => input.addEventListener(event, () => { searchChanged = true; update(); }));
  });
  update();
});
