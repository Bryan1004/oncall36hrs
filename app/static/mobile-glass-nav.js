/* Navigation glass enhancement; preserves desktop sidebar structure while applying fluid glass effects. */
(() => {
  'use strict';
  const nav = document.querySelector('aside nav');
  if (!nav) return;
  const buttons = [...nav.querySelectorAll('button[data-page]')];
  if (!buttons.length) return;
  const phone = matchMedia('(max-width:760px)');
  const reduced = matchMedia('(prefers-reduced-motion:reduce)');
  const position = document.createElement('span');
  const lens = document.createElement('span');
  position.className = 'phone-glass-lens-position';
  position.setAttribute('aria-hidden', 'true');
  lens.className = 'phone-glass-lens';
  position.append(lens);
  nav.prepend(position);
  nav.classList.add('phone-glass-nav');
  nav.setAttribute('aria-label', '主菜单');
  let selected = -1;
  let gesture = null;
  let travel = null;
  let bubble = null;
  let suppressClick = false;
  const canAnimate = () => !reduced.matches && typeof lens.animate === 'function';

  function move(index, animate = true) {
    if (index < 0 || !buttons[index]) return;
    const btn = buttons[index];
    const from = getComputedStyle(position).transform;
    travel?.cancel();
    position.style.width = `${btn.offsetWidth}px`;
    position.style.height = `${btn.offsetHeight}px`;
    const to = `translate3d(${btn.offsetLeft}px,${btn.offsetTop}px,0)`;
    position.style.transform = to;
    if (animate && canAnimate() && from && from !== 'none' && from !== to) {
      travel = position.animate([{ transform: from }, { transform: to }], {
        duration: 350, easing: 'cubic-bezier(.22,1,.36,1)',
      });
    }
  }

  function swell(hold = false, pulse = false) {
    const from = getComputedStyle(lens).transform;
    bubble?.cancel();
    const peak = phone.matches ? 'scale(1.24,1.28)' : 'scale(1.02,1.05)';
    lens.style.transform = hold && canAnimate() ? peak : 'scale(1)';
    if (!canAnimate()) return;
    bubble = lens.animate(pulse ? [
      { transform: from, offset: 0, easing: 'cubic-bezier(.22,1,.36,1)' },
      { transform: peak, offset: .22 },
      { transform: peak, offset: .36, easing: 'cubic-bezier(.22,1,.36,1)' },
      { transform: 'scale(1)', offset: 1 },
    ] : [{ transform: from }, { transform: hold ? peak : 'scale(1)' }], {
      duration: pulse ? 500 : 180, easing: pulse ? 'linear' : 'cubic-bezier(.22,1,.36,1)',
    });
  }

  function sync() {
    const next = buttons.findIndex(button => button.classList.contains('active'));
    buttons.forEach((button, index) => {
      if (index === next) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    if (next === selected) return;
    const initial = selected < 0;
    selected = next;
    if (!gesture) { move(selected, !initial); if (!initial) swell(false, true); }
  }
  const observer = new MutationObserver(sync);
  buttons.forEach(button => observer.observe(button, { attributes: true, attributeFilter: ['class'] }));

  function preview(index) {
    buttons.forEach((button, i) => button.classList.toggle('glass-preview', index === i));
    move(index);
  }
  function clearGesture() {
    gesture = null;
    nav.classList.remove('is-scrubbing');
    buttons.forEach(button => button.classList.remove('glass-preview'));
  }
  function cancel() {
    if (!gesture) return;
    clearGesture();
    move(selected);
    swell();
  }
  nav.addEventListener('pointerdown', event => {
    suppressClick = false;
    if (!phone.matches || event.pointerType === 'mouse' || !event.isPrimary) return;
    const index = buttons.findIndex(button => button.contains(event.target));
    if (index < 0) return;
    const bounds = buttons.map(button => button.getBoundingClientRect());
    gesture = { id: event.pointerId, index, bounds };
    nav.setPointerCapture(event.pointerId);
    nav.classList.add('is-scrubbing');
    preview(index);
    swell(true);
  });
  nav.addEventListener('pointermove', event => {
    if (!gesture || gesture.id !== event.pointerId) return;
    const index = gesture.bounds.findIndex(rect => event.clientX >= rect.left && event.clientX < rect.right);
    if (index >= 0 && index !== gesture.index) { gesture.index = index; preview(index); }
  });
  nav.addEventListener('pointerup', event => {
    if (!gesture || gesture.id !== event.pointerId) return;
    const index = gesture.index;
    clearGesture();
    suppressClick = true;
    // Reuse all existing page, URL, loading and scroll behavior.
    buttons[index].click();
    if (index === selected) swell();
  });
  nav.addEventListener('pointercancel', cancel);
  nav.addEventListener('lostpointercapture', cancel);
  nav.addEventListener('click', event => {
    if (suppressClick && event.detail !== 0) {
      event.preventDefault(); event.stopImmediatePropagation(); suppressClick = false;
    }
  }, true);
  function reset() {
    clearGesture(); travel?.cancel(); bubble?.cancel(); lens.style.transform = 'scale(1)'; move(selected, false);
  }
  phone.addEventListener('change', reset);
  reduced.addEventListener('change', reset);
  const resize = new ResizeObserver(() => move(gesture?.index ?? selected, false));
  resize.observe(nav);
  window.addEventListener('resize', () => move(selected, false), { passive: true });
  if (document.fonts?.ready) {
    document.fonts.ready.then(() => move(selected, false));
  }
  sync();
})();
