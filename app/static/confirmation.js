const token = location.hash.slice(1);
history.replaceState(null, '', location.pathname);

const status = document.getElementById('status');
const detail = document.getElementById('detail');
const retry = document.getElementById('retry');
const previewBar = document.getElementById('preview-bar');

// 如果实际携带了 token，说明是真实从 Bark / 链接点击进入，隐藏底部的交互式预览栏
if (token && previewBar) {
  previewBar.hidden = true;
}

async function confirmNotification() {
  if (retry) retry.hidden = true;
  if (!token) {
    if (typeof window.setViewState === 'function') {
      window.setViewState('notoken', '没有确认链接', '请从最新的 Bark 通知打开，或进入面板手动确认。');
    } else {
      if (status) status.textContent = '没有确认链接';
      if (detail) detail.textContent = '请从最新的 Bark 通知打开，或进入面板手动确认。';
    }
    return;
  }
  
  if (typeof window.setViewState === 'function') {
    window.setViewState('loading', '正在确认收到…', '连接服务器中，即将标记本轮警报为已处理…');
  } else {
    if (status) status.textContent = '正在确认…';
  }

  try {
    const response = await fetch('/confirm', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Requested-With': 'oncall'},
      body: JSON.stringify({token}),
      signal: AbortSignal.timeout(15000)
    });
    const result = await response.json();
    if (!response.ok) {
      const msg = typeof result.detail === 'string' ? result.detail : '服务器未能完成确认，请重试。';
      if (typeof window.setViewState === 'function') {
        window.setViewState('error', '未能确认', msg);
      } else {
        if (status) status.textContent = '未能确认';
        if (detail) detail.textContent = msg;
      }
      if (retry) retry.hidden = response.status < 500;
      return;
    }
    if (typeof window.setViewState === 'function') {
      window.setViewState('success', '已确认收到', '本轮消息已确认，面板会自动更新；这些消息不会再触发后续提醒。');
    } else {
      if (status) status.textContent = '✓ 已收到';
      if (detail) detail.textContent = '本轮消息已确认，面板会自动更新；这些消息不会再触发后续提醒。';
    }
  } catch {
    const errMsg = '无法连接服务器。请检查网络或连接 Tailscale 后重试。';
    if (typeof window.setViewState === 'function') {
      window.setViewState('error', '尚未确认成功', errMsg);
    } else {
      if (status) status.textContent = '尚未确认成功';
      if (detail) detail.textContent = errMsg;
    }
    if (retry) retry.hidden = false;
  }
}

if (retry) {
  retry.onclick = confirmNotification;
}

// 仅在真实带有 token 时自动触发请求；若直接双击预览 HTML，保持默认展示效果
if (token) {
  confirmNotification();
}
