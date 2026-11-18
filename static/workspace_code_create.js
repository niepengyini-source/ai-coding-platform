(() => {
  const create = document.querySelector('.workspace-code-create');
  if (!create) return;
  const annotation = document.querySelector('.annotation-form');
  const selected = new URL(location.href).searchParams.get('new_code');
  const primary = annotation?.querySelector('[name=primary]');
  const label = [...(annotation?.querySelectorAll('[name=labels]') || [])].find(input => input.value === selected);
  if (selected && label) {
    label.checked = true;
    label.dispatchEvent(new Event('change', {bubbles:true}));
  }
  if (selected && primary?.options && [...primary.options].some(option => option.value === selected)) {
    primary.value = selected;
    primary.dispatchEvent(new Event('change', {bubbles:true}));
  }
  create.addEventListener('submit', async event => {
    event.preventDefault();
    const button=create.querySelector('button'), status=create.querySelector('[data-code-create-status]');
    button.disabled=true;
    try {
      if (annotation?.platformHasUnsavedChanges?.() && !await annotation.platformSaveBeforeNavigation()) {
        throw new Error('当前编码尚未确认保存，请先处理保存提示。');
      }
      const response=await fetch(create.dataset.endpoint,{method:'POST',credentials:'same-origin',headers:{
        'Content-Type':'application/json','X-CSRFToken':create.querySelector('[name=csrfmiddlewaretoken]').value},body:JSON.stringify({
          name:create.elements.name.value,definition:create.elements.definition.value,
          book_id:Number(create.dataset.bookId),revision:Number(create.dataset.bookRevision)})});
      const data=await response.json();
      if (!response.ok) throw new Error(data.error||'代码未添加，请核对后重试。');
      const url=new URL(location.href);url.searchParams.set('new_code',data.code.id);
      location.assign(url.href);
    } catch(error) {status.textContent=error.message;button.disabled=false;}
  });
})();
