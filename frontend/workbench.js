// Enhance existing server-rendered controls; the original forms and links keep their save guards.
const body = document.body;
const tabs = [...document.querySelectorAll('.reading-tabs [role=tab]')];
if (tabs.length) {
  const reader = document.querySelector('.material-scroll');
  const positions = new Map();
  let active = tabs[0];
  function selectTab(tab, focus = false) {
    positions.set(active.id, reader.scrollTop);
    active = tab;
    tabs.forEach(item => {
      const selected = item === tab;
      const panel = document.getElementById(item.getAttribute('aria-controls'));
      item.setAttribute('aria-selected', String(selected));
      item.tabIndex = selected ? 0 : -1;
      panel.role = 'tabpanel';
      panel.setAttribute('aria-labelledby', item.id);
      panel.tabIndex = 0;
      panel.hidden = !selected;
    });
    reader.scrollTop = positions.get(tab.id) || 0;
    if (focus) tab.focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => selectTab(tab));
    tab.addEventListener('keydown', event => {
      const direction = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
      if (direction || event.key === 'Home' || event.key === 'End') {
        event.preventDefault();
        selectTab(tabs[direction ? (index + direction + tabs.length) % tabs.length : event.key === 'Home' ? 0 : tabs.length - 1], true);
      }
    });
  });
  tabs[0].parentElement.hidden = false;
  selectTab(tabs.find(tab => tab.id === tabs[0].parentElement.dataset.defaultTab) || tabs[0]);
}

document.querySelectorAll('[data-workbench-sheet]').forEach(content => {
  if (content.id === 'workbench-management' && content.children.length === 1) {
    content.remove();
    return;
  }
  const dialog = document.createElement('dialog');
  dialog.className = 'workbench-dialog';
  const heading = document.createElement('header');
  const title = document.createElement('h2');
  title.id = `${content.id}-title`;
  title.textContent = content.dataset.sheetTitle;
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'icon-button';
  close.textContent = '×';
  close.setAttribute('aria-label', `关闭${title.textContent}`);
  close.addEventListener('click', () => dialog.close());
  heading.append(title, close);
  dialog.setAttribute('aria-labelledby', title.id);
  dialog.append(heading, content);
  if (content.tagName === 'DETAILS') content.open = true;
  body.append(dialog);
  const show = () => { if (!dialog.open) dialog.showModal(); };
  document.querySelectorAll(`[data-sheet-open="${content.id}"]`).forEach(button => {
    button.hidden = false;
    button.addEventListener('click', show);
  });
  function openLinkedPanel() {
    const id = location.hash.slice(1);
    if (id && (content.id === id || content.contains(document.getElementById(id))) ||
        content.id === 'workbench-management' && [...new URLSearchParams(location.search).keys()].some(key => key.startsWith('submitted_'))) {
      show();
      const linked = id && document.getElementById(id);
      if (linked?.tagName === 'DETAILS') linked.open = true;
    }
  }
  window.addEventListener('hashchange', openLinkedPanel);
  openLinkedPanel();
});

if ('showPopover' in HTMLElement.prototype) {
  const popover = document.createElement('section');
  popover.className = 'code-rule-popover';
  popover.id = 'workbench-code-rule';
  popover.setAttribute('popover', 'manual');
  popover.setAttribute('data-code-popover', '');
  popover.setAttribute('role', 'dialog');
  popover.setAttribute('aria-labelledby', 'code-rule-title');
  body.append(popover);
  let trigger, pinned = false, timer;
  function hide() {
    clearTimeout(timer);
    if (popover.matches(':popover-open')) popover.hidePopover();
    trigger?.setAttribute('aria-expanded', 'false');
    pinned = false;
  }
  function leave() { if (!pinned) timer = setTimeout(hide, 160); }
  function show(summary, pin = false) {
    clearTimeout(timer);
    if (trigger === summary && popover.matches(':popover-open')) { pinned ||= pin; return; }
    hide(); trigger = summary; pinned = pin;
    const card = summary.closest('[data-code-card]');
    const header = document.createElement('header');
    const title = document.createElement('h3');
    title.id = 'code-rule-title';
    title.textContent = card.querySelector('[data-code-choice]').dataset.label;
    const close = document.createElement('button');
    close.type = 'button'; close.className = 'icon-button'; close.textContent = '×';
    close.setAttribute('aria-label', '关闭编码规则');
    close.addEventListener('click', hide);
    header.append(title, close);
    popover.replaceChildren(header, card.querySelector('.code-rule-content').cloneNode(true));
    popover.showPopover(); summary.setAttribute('aria-expanded', 'true');
    const rect = summary.getBoundingClientRect();
    const width = popover.offsetWidth;
    popover.style.left = `${Math.max(12, Math.min(innerWidth - width - 12, rect.left - width - 12))}px`;
    popover.style.top = `${Math.max(12, Math.min(innerHeight - popover.offsetHeight - 12, rect.top))}px`;
  }
  document.querySelectorAll('.code-choice-details').forEach(details => {
    const summary = details.querySelector('summary');
    details.dataset.popupReady = 'true';
    summary.setAttribute('aria-controls', popover.id);
    summary.setAttribute('aria-expanded', 'false');
    summary.addEventListener('mouseenter', () => show(summary));
    summary.addEventListener('mouseleave', leave);
    summary.addEventListener('click', event => {
      event.preventDefault();
      if (trigger === summary && pinned) hide(); else show(summary, true);
    });
  });
  popover.addEventListener('mouseenter', () => clearTimeout(timer));
  popover.addEventListener('mouseleave', leave);
  document.addEventListener('pointerdown', event => { if (!popover.contains(event.target) && !event.target.closest('.code-choice-details')) hide(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && popover.matches(':popover-open')) { event.preventDefault(); hide(); trigger?.focus(); } }, true);
  document.querySelector('[name=code_search]')?.addEventListener('input', hide);
  window.addEventListener('resize', hide);
}
body.classList.add('workbench-enhanced');
