'use strict';
(() => {
  const body = document.body;
  const menus = [...document.querySelectorAll('[data-workbench-menu]')];
  menus.forEach(menu => menu.addEventListener('toggle', () => {
    if (menu.open) menus.forEach(other => { if (other !== menu) other.open = false; });
  }));
  document.addEventListener('click', event => menus.forEach(menu => {
    if (menu.open && (!menu.contains(event.target) || event.target.closest('a, button'))) menu.open = false;
  }));
  document.addEventListener('keydown', event => {
    const menu = menus.find(item => item.open);
    if (event.key === 'Escape' && menu) {
      event.preventDefault();
      menu.open = false;
      menu.querySelector('summary').focus();
    }
  });
  const sidebar = document.querySelector('.sidebar');
  const navToggle = document.querySelector('[data-sidebar-toggle]');
  if (sidebar && navToggle) {
    sidebar.id = 'workspace-sidebar';
    const shell = document.querySelector('.shell');
    const key = `platform:workbench-nav:${body.dataset.navigationUser}`;
    const projectMenu = sidebar.querySelector('#project-navigation');
    const management = sidebar.querySelector('.nav-management');
    let projectWasHidden = projectMenu?.hidden;
    let managementWasOpen = management?.open;
    let wasCompact = false;
    const mobile = matchMedia('(max-width: 760px)');
    let desktopCompact = true;
    let mobileOpen = false;
    try { desktopCompact = sessionStorage.getItem(key) !== 'expanded'; } catch { /* Optional display preference. */ }
    const isCompact = () => mobile.matches ? !mobileOpen : desktopCompact;
    body.classList.add('nav-ready');
    const backdrop = document.createElement('button');
    backdrop.className = 'sidebar-backdrop';
    backdrop.type = 'button';
    backdrop.setAttribute('aria-label', '关闭导航');
    body.append(backdrop);
    function displayNavigation() {
      const compact = isCompact();
      body.classList.toggle('nav-compact', compact);
      sidebar.inert = compact && mobile.matches;
      shell.inert = mobile.matches && mobileOpen;
      navToggle.setAttribute('aria-expanded', String(!compact));
      navToggle.setAttribute('aria-label', compact ? '展开侧边导航' : '收起侧边导航');
      if (compact) {
        if (!wasCompact) { projectWasHidden = projectMenu?.hidden; managementWasOpen = management?.open; }
        if (projectMenu) projectMenu.hidden = false;
        if (management) management.open = true;
      } else if (wasCompact) {
        if (projectMenu) projectMenu.hidden = projectWasHidden;
        if (management) management.open = managementWasOpen;
      }
      wasCompact = compact;
    }
    function toggleNavigation() {
      if (mobile.matches) {
        mobileOpen = !mobileOpen;
      } else {
        desktopCompact = !desktopCompact;
        try { sessionStorage.setItem(key, desktopCompact ? 'compact' : 'expanded'); } catch { /* Optional preference. */ }
      }
      displayNavigation();
      hideTip();
      if (mobile.matches) (mobileOpen ? sidebar.querySelector('a') : navToggle).focus();
    }
    navToggle.hidden = false;
    navToggle.addEventListener('click', toggleNavigation);
    backdrop.addEventListener('click', toggleNavigation);
    mobile.addEventListener('change', () => { mobileOpen = false; displayNavigation(); hideTip(); });
    window.addEventListener('pageshow', displayNavigation);
    displayNavigation();

    const tooltip = document.createElement('div');
    tooltip.className = 'nav-tooltip';
    tooltip.id = 'workbench-nav-tooltip';
    tooltip.role = 'tooltip';
    tooltip.hidden = true;
    body.append(tooltip);
    let tipLink;
    function hideTip() {
      tooltip.hidden = true;
      tipLink?.removeAttribute('aria-describedby');
    }
    sidebar.querySelectorAll('.nav-item').forEach(link => {
      link.setAttribute('aria-label', link.textContent.trim());
      function showTip() {
        if (!isCompact() || sidebar.inert) return;
        hideTip(); tipLink = link;
        tooltip.textContent = link.textContent.trim();
        tooltip.hidden = false;
        tooltip.style.left = `${sidebar.getBoundingClientRect().right + 8}px`;
        tooltip.style.top = `${Math.max(8, Math.min(innerHeight - tooltip.offsetHeight - 8, link.getBoundingClientRect().top))}px`;
        link.setAttribute('aria-describedby', tooltip.id);
      }
      link.addEventListener('mouseenter', showTip);
      link.addEventListener('focus', showTip);
      link.addEventListener('mouseleave', hideTip);
      link.addEventListener('blur', hideTip);
    });
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      hideTip();
      if (mobile.matches && mobileOpen) toggleNavigation();
    });
    sidebar.addEventListener('scroll', hideTip, true);
  }
})();
