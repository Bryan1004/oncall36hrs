/* Loading masks never participate in layout. CSS owns the viewport dimensions. */
const loadingUI = (() => {
  let sequence = 0;
  function begin(targets, {initial = false} = {}) {
    const nodes = typeof targets === 'string' ? [...document.querySelectorAll(targets)] : [targets];
    const ticket = String(++sequence);
    const active = nodes.filter(node => !initial || node.dataset.loaded !== 'true');
    for (const node of active) {
      node.querySelector(':scope > .load-error')?.remove();
      node.dataset.loadTicket = ticket;
      node.dataset.loading = 'true';
      node.setAttribute('aria-busy', 'true');
      node.inert = true;
    }
    return (error, retry) => {
      for (const node of active) {
        if (node.dataset.loadTicket !== ticket) continue;
        node.dataset.loading = 'false';
        node.setAttribute('aria-busy', 'false');
        node.inert = false;
        if (!error) node.dataset.loaded = 'true';
        else if (node.dataset.loaded !== 'true') {
          const message = document.createElement('div');
          message.className = 'load-error';
          message.setAttribute('role', 'status');
          message.textContent = '数据加载失败，请重试。';
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'secondary';
          button.textContent = '重新加载';
          button.onclick = () => {
            document.querySelector('#error').hidden = true;
            Promise.resolve().then(retry).catch(showError);
          };
          message.append(button);
          node.append(message);
        }
      }
    };
  }
  document.querySelectorAll('[data-loading="true"]').forEach(node => {
    node.setAttribute('aria-busy', 'true');
    node.inert = true;
  });
  return {begin};
})();
