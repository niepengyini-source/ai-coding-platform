'use strict';
(() => {
  const button = document.getElementById('page-back');
  if (!button) return;
  const hint = document.getElementById('page-back-hint');
  const identity = document.body.dataset.navigationUser || 'anonymous';
  const storageKey = 'ai-coding-platform:navigation:v1:' + identity;
  const current = location.pathname + location.search;
  const allowedPage = /^\/(?:$|login\/$|guide\/$|accounts\/(?:register|password(?:\/done)?)\/$|groups\/join\/$|projects\/new\/$|p\/\d+\/(?:$|members\/$|materials\/$|units\/$|exports\/$|books\/(?:new|\d+(?:\/import)?)\/$|rounds\/new\/$)|r\/\d+\/(?:$|review\/$|disagreements\/$|report\/$))/;
  const safePath = value => {
    if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || value.length > 4000) return null;
    try {
      const url = new URL(value, location.origin);
      return url.origin === location.origin && !url.hash && allowedPage.test(url.pathname) ? url.pathname + url.search : null;
    } catch (_) { return null; }
  };
  const fallback = safePath(button.getAttribute('href')) || (identity === 'anonymous' ? '/login/' : '/');
  let stack = [], target = fallback;
  const load = () => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey));
      return Array.isArray(saved) ? saved.filter(path => safePath(path) === path).slice(-50) : [];
    } catch (_) { return []; }
  };
  const store = value => {
    try { sessionStorage.setItem(storageKey, JSON.stringify(value.slice(-50))); } catch (_) { /* Fallback link works without storage. */ }
  };
  function refresh(traversal = false) {
    stack = load();
    // A real browser Back may restore a cached document; align the logical path stack.
    if (traversal && stack.length > 1 && stack[stack.length - 2] === current) stack.pop();
    const bookPage = location.pathname.match(/^\/p\/(\d+)\/books\/\d+\/$/);
    if (bookPage) {
      // Leaving a completed/cancelled import returns to the working page, not a wizard
      // whose replay-safe endpoint would redirect straight back to this codebook.
      if (stack[stack.length - 1] === current) stack.pop();
      const importPage = new RegExp('^/p/' + bookPage[1] + '/books/\\d+/import/(?:\\?|$)');
      while (stack.length && importPage.test(stack[stack.length - 1])) stack.pop();
    }
    if (safePath(current) && stack[stack.length - 1] !== current) stack.push(current);
    stack = stack.slice(-50);
    store(stack);
    // Completing a password change should not reopen the password-entry form.
    target = [...stack.slice(0, -1)].reverse().find(path => path !== current &&
      !(current === '/accounts/password/done/' && path === '/accounts/password/')) || fallback;
    const disabled = target === current;
    button.setAttribute('href', target);
    button.setAttribute('aria-disabled', String(disabled));
    button.setAttribute('title', disabled ? '当前已在起始页面，没有可返回的上一页。' : '返回刚才浏览的页面；直接打开时返回项目或工作空间。');
    button.textContent = disabled ? '← 已在首页' : '← 返回上一页';
  }
  refresh(performance.getEntriesByType('navigation')[0]?.type === 'back_forward');
  window.addEventListener('pageshow', event => { if (event.persisted) refresh(true); });
  button.addEventListener('click', event => {
    event.preventDefault();
    if (button.getAttribute('aria-disabled') === 'true') return;
    const beforeBack = new Event('platform:before-back', {cancelable:true});
    if (!window.dispatchEvent(beforeBack)) {
      hint.textContent = '还有未确认保存的编码，请先确认“已保存”再返回；如有保存冲突，请先保留输入并核对最新版本。';
      return;
    }
    hint.textContent = '';
    // Explicit GET navigation avoids reposting earlier forms or opening an external site.
    const previousIndex = stack.lastIndexOf(target, stack.length - 2);
    store(previousIndex >= 0 ? stack.slice(0, previousIndex + 1) : [target]);
    location.assign(target);
  });
})();
