const token = location.hash.slice(1);
history.replaceState(null, '', location.pathname);
const status = document.getElementById('status');
const detail = document.getElementById('detail');
const retry = document.getElementById('retry');
async function confirmNotification() {
  retry.hidden = true;
  if (!token) {
    status.textContent = '没有确认链接';
    detail.textContent = '请从最新的 Bark 通知打开，或进入面板手动确认。';
    return;
  }
  status.textContent = '正在确认…';
  try {
    const response = await fetch('/confirm', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Requested-With': 'oncall'},
      body: JSON.stringify({token}),
      signal: AbortSignal.timeout(15000)
    });
    const result = await response.json();
    if (!response.ok) {
      status.textContent = '未能确认';
      detail.textContent = typeof result.detail === 'string' ? result.detail : '服务器未能完成确认，请重试。';
      retry.hidden = response.status < 500;
      return;
    }
    status.textContent = '✓ 已收到';
    detail.textContent = '本轮消息已确认，面板会自动更新；这些消息不会再触发后续提醒。';
  } catch {
    status.textContent = '尚未确认成功';
    detail.textContent = '无法连接服务器。请检查网络或连接 Tailscale 后重试。';
    retry.hidden = false;
  }
}
retry.onclick = confirmNotification;
confirmNotification();
