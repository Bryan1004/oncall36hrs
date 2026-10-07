let token = '';
let activeRequest = null;
const previewMode = location.protocol === 'file:' || new URLSearchParams(location.search).has('preview');

const statusEl = document.getElementById('status');
const detailEl = document.getElementById('detail');
const retryBtn = document.getElementById('retry');
const previewBar = document.getElementById('preview-bar');
const iconLoading = document.getElementById('icon-loading');
const iconSuccess = document.getElementById('icon-success');
const iconError = document.getElementById('icon-error');
const iconWarn = document.getElementById('icon-warn');
const timeBubble = document.getElementById('confirmed-time');
const timeText = document.getElementById('time-text');
const dashBtn = document.getElementById('dashboard-link');

function setViewState(type, message, subtext) {
  const body = document.body;
  if (!body) return;

  [iconLoading, iconSuccess, iconError, iconWarn].forEach(el => {
    if (el) el.hidden = true;
  });
  document.querySelector('.card')?.setAttribute('aria-busy', String(type === 'loading'));
  body.className = 'state-' + (type === 'notoken' ? 'warn' : type);
  if (timeBubble) timeBubble.classList.remove('show');

  const previewBtns = document.querySelectorAll('#preview-bar button');
  previewBtns.forEach(btn => {
    btn.classList.toggle('active', btn.dataset.state === type);
  });

  if (type === 'loading') {
    if (iconLoading) iconLoading.hidden = false;
    if (statusEl) statusEl.textContent = message || '正在确认收到…';
    if (detailEl) detailEl.textContent = subtext || '连接服务器后，将自动标记本轮消息为已收到并停止重复提醒。';
    if (retryBtn) retryBtn.hidden = true;
    if (dashBtn) dashBtn.className = 'btn btn-primary';
  } else if (type === 'success') {
    if (iconSuccess) iconSuccess.hidden = false;
    if (statusEl) statusEl.textContent = message || '已确认收到';
    if (detailEl) detailEl.textContent = subtext || '本轮警报已确认，已停止后续重复提醒；面板数据已同步更新。';
    if (retryBtn) retryBtn.hidden = true;
    if (dashBtn) dashBtn.className = 'btn btn-primary';
    const now = new Date();
    const timeStr = now.toLocaleTimeString('zh-CN', { hour12: false });
    if (timeText) timeText.textContent = '已于 ' + timeStr + ' 确认';
    if (timeBubble) timeBubble.classList.add('show');
  } else if (type === 'error') {
    if (iconError) iconError.hidden = false;
    if (statusEl) statusEl.textContent = message || '未能确认';
    if (detailEl) detailEl.textContent = subtext || '无法连接到推送服务器，请检查网络或确认 Tailscale 连接。';
    if (retryBtn) retryBtn.hidden = false;
    if (dashBtn) dashBtn.className = 'btn btn-secondary';
  } else if (type === 'notoken') {
    if (iconWarn) iconWarn.hidden = false;
    if (statusEl) statusEl.textContent = message || '没有确认凭证';
    if (detailEl) detailEl.textContent = subtext || '请从最新的 Bark 警报通知打开，或直接进入面板查看和确认。';
    if (retryBtn) retryBtn.hidden = true;
    if (dashBtn) dashBtn.className = 'btn btn-primary';
  }
}
window.setViewState = setViewState;

// 绑定预览栏点击事件（避免 CSP 拦截内联 onclick）
document.querySelectorAll('#preview-bar button').forEach(btn => {
  btn.addEventListener('click', () => {
    const s = btn.dataset.state;
    if (s) setViewState(s);
  });
});

// 本地 file:// 预览或 ?preview=1 时显示预览切换栏
if (previewBar && previewMode) {
  previewBar.hidden = false;
}

async function confirmNotification() {
  if (retryBtn) retryBtn.hidden = true;
  if (!token) {
    setViewState('notoken', '没有确认凭证', '请从最新的 Bark 警报通知打开，或直接进入面板查看和确认。');
    return;
  }
  if (activeRequest?.token === token) return;
  // Each click still confirms its own messages; only the latest request may update the page.
  const request = {token};
  activeRequest = request;

  setViewState('loading', '正在确认收到…', '连接服务器中，即将标记本轮警报为已处理…');

  try {
    const response = await fetch('/confirm', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Requested-With': 'oncall'},
      body: JSON.stringify({token: request.token}),
      signal: AbortSignal.timeout(15000)
    });
    const result = await response.json();
    if (activeRequest !== request) return;
    if (!response.ok) {
      const msg = typeof result.detail === 'string' ? result.detail : '服务器未能完成确认，请重试。';
      setViewState('error', '未能确认', msg);
      if (retryBtn) retryBtn.hidden = response.status < 500;
      return;
    }
    setViewState('success', '已确认收到', '本轮消息已确认，面板会自动更新；这些消息不会再触发后续提醒。');
  } catch {
    if (activeRequest !== request) return;
    const errMsg = '无法连接服务器。请检查网络或连接 Tailscale 后重试。';
    setViewState('error', '尚未确认成功', errMsg);
    if (retryBtn) retryBtn.hidden = false;
  } finally {
    if (activeRequest === request) activeRequest = null;
  }
}

if (retryBtn) {
  retryBtn.addEventListener('click', confirmNotification);
}

// Browsers can reuse /confirm by changing only its fragment, without reloading the script.
function consumeNotificationLink() {
  const nextToken = location.hash.slice(1);
  if (!nextToken) return false;
  token = nextToken;
  history.replaceState(history.state, '', location.pathname + location.search);
  confirmNotification();
  return true;
}

window.addEventListener('hashchange', consumeNotificationLink);
window.addEventListener('pageshow', consumeNotificationLink);
window.addEventListener('focus', consumeNotificationLink);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') consumeNotificationLink();
});

if (!consumeNotificationLink()) {
  if (previewMode) {
    // Simulated success is restricted to the explicit design preview.
    setViewState('loading');
    setTimeout(() => {
      if (!token) setViewState('success');
    }, 5000);
  } else {
    setViewState('notoken');
  }
}
