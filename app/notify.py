import asyncio
import time
import httpx
from app.quiet_hours import quiet_active
from app.confirmation import sign


class DeliveryError(Exception):
    pass


class Notifier:
    def __init__(self, config, client=None, store=None):
        self.config = config
        self.store = store
        self.client = client or httpx.AsyncClient(timeout=45, follow_redirects=False)

    def device_key(self, mode="away"):
        stored = self.store.get("bark_" + mode + "_device_key") if self.store else None
        configured = self.config.bark_home_device_key if mode == "home" else self.config.bark_away_device_key or self.config.bark_device_key
        return stored or configured

    @staticmethod
    def message_body(alerts):
        groups = {}
        for alert in alerts:
            platform = {"whatsapp": "WhatsApp", "telegram": "Telegram"}.get(alert.get("platform"), "消息")
            title = " ".join(str(alert.get("title") or "未命名群组").split())
            title = title[:60] + ("…" if len(title) > 60 else "")
            key = (platform, str(alert.get("chat_id") or title))
            if key not in groups:
                groups[key] = {"title": title, "count": 0}
            groups[key]["count"] += 1
        lines = [f"有 {len(alerts)} 条消息待确认："]
        for (platform, _), group in list(groups.items())[:5]:
            lines.append(f"{platform} · {group['title']}：{group['count']} 条")
        if len(groups) > 5:
            lines.append(f"另有 {len(groups) - 5} 个群组，打开面板查看。")
        else:
            lines.append("点击通知自动标记本轮消息为已收到。")
        return "\n".join(lines)

    async def send(self, mode, alerts, test=False):
        if mode not in {"home", "away"}:
            raise DeliveryError("无效的提醒模式")
        if not self.config.delivery_enabled:
            return "simulated"
        if not self.device_key(mode):
            raise DeliveryError("请先在提醒通道保存「" + ("在家" if mode == "home" else "外出") + "」的 Bark Device Key")
        payload = {"device_key": self.device_key(mode),
            "title": "On Call 36 小时 · 测试" if test else "On Call 36 小时",
            "body": "这是一条测试提醒，无需处理。" if test else self.message_body(alerts),
            "url": self.config.public_url, "group": "oncall36", "isArchive": "0",
            "level": "critical" if mode == "home" else "active",
            "sound": "minuet"}
        if not test and alerts:
            base = self.config.confirmation_url or self.config.public_url
            payload["url"] = base + "/confirm#" + sign(self.config, [a["id"] for a in alerts])
        if mode == "home":
            payload.update({"call": "1", "volume": "5"})
        # Share source platform/group and counts, never message text; dispatcher owns retries.
        response = await self.client.post(self.config.bark_server + "/push", json=payload)
        response.raise_for_status()
        try:
            result = response.json()
        except ValueError:
            raise DeliveryError("Bark 返回了无效响应") from None
        if not isinstance(result, dict) or result.get("code") != 200:
            raise DeliveryError("Bark 未接受请求，请检查 Device Key 和服务状态")
        return "accepted"


class Dispatcher:
    def __init__(self, store, notifier):
        self.store, self.notifier = store, notifier
        # State mutations never wait for provider network I/O.
        self.lock = asyncio.Lock()
        self.dispatch_lock = asyncio.Lock()
        self.test_lock = asyncio.Lock()

    async def tick(self, now=None):
        now = time.time() if now is None else now
        async with self.dispatch_lock:
            mode = self.store.get("mode")
            if mode == "paused" or quiet_active(self.store, now) or self.store.get("next_delivery") > now:
                return
            alerts = [a for a in self.store.alerts(pending=True)
                      if a["platform"] in {"whatsapp", "telegram"}
                      or (a["platform"] == "demo" and not self.notifier.config.delivery_enabled)]
            if not alerts:
                return
            interval = self.store.get("call_interval" if mode == "home" else "push_interval")
            # Reserve before network I/O, so crash/restart doesn't create a rapid call loop.
            self.store.set("last_delivery", now)
            self.store.set("next_delivery", now + interval)
            ids = [a["id"] for a in alerts]
            try:
                async def send_current():
                    selected_ids = set(ids)
                    current = [a for a in self.store.alerts(pending=True) if a['id'] in selected_ids]
                    if self.store.get('mode') != mode or quiet_active(self.store, now) or not current:
                        return 'cancelled'
                    ids[:] = [a['id'] for a in current]
                    return await self.notifier.send(mode, current)
                result = await asyncio.wait_for(send_current(), timeout=45)
                if result != "cancelled":
                    self.store.delivery(mode, ids, result)
            except Exception as exc:
                # Do not log exception URLs: provider credentials appear in some requests.
                detail = str(exc) if isinstance(exc, DeliveryError) else type(exc).__name__
                self.store.delivery(mode, ids, "failed", detail)

    async def test(self, mode):
        """One explicit test, no pending alert and no automatic repeat."""
        async with self.test_lock:
            try:
                result = await asyncio.wait_for(self.notifier.send(mode, [], test=True), timeout=45)
                self.store.delivery(mode, [], result, "手动通道测试")
                return {"status": result}
            except Exception as exc:
                detail = str(exc) if isinstance(exc, DeliveryError) else type(exc).__name__
                self.store.delivery(mode, [], "failed", "手动测试：" + detail)
                return {"status": "failed", "detail": detail}

    async def set_mode(self, mode):
        async with self.lock:
            self.store.set("mode", mode)
            # Prevent rapid toggling from bypassing call throttling.
            interval = self.store.get("call_interval" if mode == "home" else "push_interval")
            self.store.set("next_delivery", max(time.time(), self.store.get("last_delivery") + interval))

    async def acknowledge(self, ids):
        async with self.lock:
            self.store.acknowledge(ids)
