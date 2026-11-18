(() => {
  const panel = document.querySelector('[data-document-reader]');
  if (!panel) return;
  function locate() {
    if (panel.hidden) return;
    const requested = location.hash.startsWith('#reading-document-block-') && document.getElementById(location.hash.slice(1));
    const target = requested || panel.querySelector('[data-current-document-block]');
    const scroll = panel.closest('.material-scroll');
    if (target && scroll) scroll.scrollTop += target.getBoundingClientRect().top - scroll.getBoundingClientRect().top - 20;
  }
  document.getElementById('document-tab')?.addEventListener('click', () => requestAnimationFrame(locate));
  requestAnimationFrame(locate);
})();
