'use strict';
(() => {
  const group = document.querySelector('[data-project-nav]');
  if (!group) return;
  const toggle = group.querySelector('[data-project-nav-toggle]');
  const panel = toggle && document.getElementById(toggle.getAttribute('aria-controls'));
  if (!toggle || !panel) return;
  const label = toggle.querySelector('[data-project-nav-label]');
  const key = 'ai-coding-platform:project-menu:v1:' + document.body.dataset.navigationUser + ':' + group.dataset.projectId;
  function display(open) {
    panel.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    const text = open ? '收起项目功能' : '展开项目功能';
    toggle.setAttribute('aria-label', text);
    if (label) label.textContent = text;
  }
  function restore() {
    let open = false;
    try { open = sessionStorage.getItem(key) === 'open'; } catch (_) { /* The toggle still works without storage. */ }
    display(open);
  }
  restore();
  toggle.addEventListener('click', () => {
    const open = panel.hidden;
    display(open);
    try { sessionStorage.setItem(key, open ? 'open' : 'closed'); } catch (_) { /* Keep this page's chosen state. */ }
  });
  window.addEventListener('pageshow', event => { if (event.persisted) restore(); });
})();
