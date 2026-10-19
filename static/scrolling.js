'use strict';
(() => {
  const sidebar = document.querySelector('.sidebar');
  if (!sidebar) return;
  sidebar.addEventListener('wheel', event => {
    if (event.defaultPrevented || event.ctrlKey || !event.deltaY || window.matchMedia('(max-width:760px)').matches) return;
    let target = event.target;
    while (target && sidebar.contains(target)) {
      const scrollable = target.scrollHeight > target.clientHeight && /auto|scroll|overlay/.test(getComputedStyle(target).overflowY);
      if (scrollable && (event.deltaY < 0 && target.scrollTop > 0
          || event.deltaY > 0 && Math.ceil(target.scrollTop + target.clientHeight) < target.scrollHeight)) return;
      target = target.parentElement;
    }
    event.preventDefault();
  }, {passive:false});
})();
