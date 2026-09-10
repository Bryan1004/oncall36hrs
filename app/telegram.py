import asyncio
import time
import re
from pathlib import Path
from telethon import TelegramClient, events, types, errors


class LoginError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class TelegramMonitor:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self.client = None
        self.me = None
        self.lock = asyncio.Lock()
        self.phone = None
        self.phone_hash = None
        self.step = "phone"
        self.expires = 0
        self.retry_at = 0
        self.verify_at = 0
        self.handler_added = False

    async def refresh(self):
        if not self.client or not self.me or not self.client.is_connected():
            return False
        groups = [{"id": str(d.id), "title": d.name} async for d in self.client.iter_dialogs() if d.is_group]
        if not self.client or not self.me or not self.client.is_connected() or self.store.get("telegram_disconnected"):
            return False
        self.store.sync_groups("telegram", groups)
        return True

    async def handle(self, event):
        if self.store.get("telegram_disconnected"):
            return
        if not event.is_group or not self.store.selected("telegram", str(event.chat_id)):
            return
        message = event.message
        # Use typed mention entities, never plain substring '@name' matches.
        usernames = {self.me.username.lower()} if self.me.username else set()
        usernames.update(u.username.lower() for u in (getattr(self.me, "usernames", None) or []) if u.active)
        mentioned = False
        for entity, text in message.get_entities_text():
            if isinstance(entity, types.MessageEntityMentionName) and entity.user_id == self.me.id:
                mentioned = True
            if isinstance(entity, types.MessageEntityMention) and text.lstrip("@").lower() in usernames:
                mentioned = True
        reply_to_me = False
        if message.is_reply and not message.out:
            reply = await message.get_reply_message()
            reply_to_me = bool(reply and reply.sender_id == self.me.id)
        sender = await event.get_sender()
        sender_name = " ".join(filter(None, [getattr(sender, "first_name", ""), getattr(sender, "last_name", "")]))
        self.store.ingest({"platform": "telegram", "chat_id": str(event.chat_id),
            "message_id": str(message.id), "sender": sender_name or str(message.sender_id),
            "text": message.raw_text or "[非文字消息]", "timestamp": message.date.timestamp(),
            "from_me": bool(message.out), "mentioned": mentioned, "reply_to_me": reply_to_me})

    def login_status(self):
        expired = bool(self.expires and time.time() > self.expires)
        return {"step": "disconnected" if self.store.get("telegram_disconnected") else "connected" if self.me else "phone" if expired else self.step,
                "configured": bool(self.config.telegram_api_id and self.config.telegram_api_hash),
                "phone_hint": ("••••" + self.phone[-4:]) if self.phone else "",
                "retry_after": max(0, int(self.retry_at - time.time()) + 1)}

    def clear_challenge(self):
        self.phone = self.phone_hash = None
        self.step, self.expires = "phone", 0

    async def ensure_client(self):
        if self.store.get("telegram_disconnected"):
            raise LoginError("账号已断开，请先点击重新连接", 409)
        if not self.config.telegram_api_id or not self.config.telegram_api_hash:
            raise LoginError("请先配置 Telegram API ID / Hash 并重新创建容器", 409)
        if not self.client:
            self.client = TelegramClient(str(Path(self.config.data_dir) / "telegram"),
                int(self.config.telegram_api_id), self.config.telegram_api_hash,
                auto_reconnect=True, sequential_updates=True, flood_sleep_threshold=0,
                request_retries=1, connection_retries=3)
        if not self.client.is_connected():
            await asyncio.wait_for(self.client.connect(), 20)

    async def activate(self):
        self.me = await self.client.get_me()
        if not self.me:
            raise LoginError("登录尚未完成，请重新获取验证码", 409)
        if not self.handler_added:
            async def safe_handle(event):
                try:
                    await self.handle(event)
                except Exception:
                    self.store.connector("telegram", "message_error")
            self.client.add_event_handler(safe_handle, events.NewMessage())
            self.handler_added = True
        self.clear_challenge()
        self.store.connector("telegram", "connected")
        # Group-list failure must not undo a successful authorization.
        try:
            await asyncio.wait_for(self.refresh(), 15)
        except Exception:
            pass

    def translate_error(self, exc):
        if isinstance(exc, LoginError):
            return exc
        if isinstance(exc, errors.FloodWaitError):
            self.retry_at = max(self.retry_at, time.time() + exc.seconds)
            self.verify_at = self.retry_at
            return LoginError(f"Telegram 请求过于频繁，请等待 {exc.seconds} 秒", 429)
        if isinstance(exc, errors.PhoneCodeExpiredError):
            self.clear_challenge()
            return LoginError("验证码已过期，请重新获取")
        if isinstance(exc, errors.AuthRestartError):
            self.clear_challenge()
        messages = {
            errors.PhoneNumberInvalidError: "手机号格式无效，请包含国家区号，例如 +60…",
            errors.PhoneNumberBannedError: "这个手机号暂时无法登录 Telegram",
            errors.PhoneCodeInvalidError: "验证码不正确，请重新输入",
            errors.PasswordHashInvalidError: "两步验证密码不正确，请重新输入",
            errors.ApiIdInvalidError: "Telegram API ID / Hash 无效，请检查配置",
            errors.PhoneNumberUnoccupiedError: "请使用已经注册的 Telegram 账号",
            errors.AuthRestartError: "Telegram 要求重新开始登录，请重新获取验证码",
        }
        for error_type, message in messages.items():
            if isinstance(exc, error_type):
                return LoginError(message)
        return LoginError("Telegram 暂时无法完成请求，请稍后重试", 503)

    async def send_code(self, phone):
        phone = re.sub(r"[\s()-]", "", phone)
        if not re.fullmatch(r"\+[1-9]\d{6,14}", phone):
            raise LoginError("请输入带国家区号的手机号，例如 +60…")
        async with self.lock:
            if self.me:
                return self.login_status()
            if time.time() < self.retry_at:
                raise LoginError(f"请等待 {self.login_status()['retry_after']} 秒后再获取验证码", 429)
            self.retry_at = time.time() + 60
            try:
                await self.ensure_client()
                if await self.client.is_user_authorized():
                    await self.activate()
                    return self.login_status()
                result = await asyncio.wait_for(self.client.send_code_request(phone), 25)
                self.phone, self.phone_hash = phone, result.phone_code_hash
                self.step, self.expires = "code", time.time() + 600
                self.store.connector("telegram", "needs_login")
                return self.login_status()
            except Exception as exc:
                raise self.translate_error(exc) from None

    async def verify(self, value, password=False):
        async with self.lock:
            if self.expires and time.time() > self.expires:
                self.clear_challenge()
            status = self.login_status()
            if self.me:
                return status
            expected = "password" if password else "code"
            if self.step != expected or not self.phone_hash:
                raise LoginError("登录步骤已失效，请从获取验证码重新开始", 409)
            if time.time() < self.verify_at:
                raise LoginError("请稍候再尝试验证", 429)
            self.verify_at = time.time() + 2
            try:
                await self.ensure_client()
                if password:
                    await asyncio.wait_for(self.client.sign_in(password=value), 25)
                else:
                    await asyncio.wait_for(self.client.sign_in(phone=self.phone, code=value,
                        phone_code_hash=self.phone_hash), 25)
                await self.activate()
                return self.login_status()
            except errors.SessionPasswordNeededError:
                self.step = "password"
                self.verify_at = 0
                return self.login_status()
            except Exception as exc:
                raise self.translate_error(exc) from None

    async def cancel_login(self):
        async with self.lock:
            self.clear_challenge()
            return self.login_status()

    async def clear_login(self):
        remote_logout = True
        client = self.client
        try:
            if not client and (Path(self.config.data_dir) / "telegram.session").exists():
                client = TelegramClient(str(Path(self.config.data_dir) / "telegram"),
                    int(self.config.telegram_api_id), self.config.telegram_api_hash,
                    flood_sleep_threshold=0, request_retries=0, connection_retries=0)
            if client:
                if not client.is_connected():
                    await asyncio.wait_for(client.connect(), 10)
                if await asyncio.wait_for(client.is_user_authorized(), 10):
                    remote_logout = bool(await asyncio.wait_for(client.log_out(), 10))
        except Exception:
            remote_logout = False
        finally:
            if client:
                await client.disconnect()
                if getattr(client, "session", None):
                    client.session.close()
            self.client = self.me = None
            self.handler_added = False
            self.clear_challenge()
            for suffix in ("", "-journal", "-wal", "-shm"):
                (Path(self.config.data_dir) / ("telegram.session" + suffix)).unlink(missing_ok=True)
        return remote_logout

    async def set_disconnected(self, disconnected):
        async with self.lock:
            remote_logout = True
            if disconnected:
                self.store.set("telegram_disconnected", True)
                remote_logout = await self.clear_login()
                self.store.connector("telegram", "disconnected")
            else:
                # Also invalidate sessions retained by the old pause-only implementation.
                if self.store.get("telegram_disconnected"):
                    remote_logout = await self.clear_login()
                self.store.set("telegram_disconnected", False)
                self.store.connector("telegram", "needs_login")
        return {"ok": True, "remote_logout": remote_logout}

    async def run(self):
        if not self.config.telegram_api_id or not self.config.telegram_api_hash:
            self.store.connector("telegram", "not_configured")
            return
        try:
            while True:
                if self.store.get("telegram_disconnected"):
                    self.store.connector("telegram", "disconnected")
                    await asyncio.sleep(1)
                    continue
                try:
                    async with self.lock:
                        if self.store.get("telegram_disconnected"):
                            self.store.connector("telegram", "disconnected")
                            continue
                        await self.ensure_client()
                        if not self.me:
                            if await asyncio.wait_for(self.client.is_user_authorized(), 15):
                                await self.activate()
                            else:
                                self.store.connector("telegram", "needs_login")
                        else:
                            self.store.connector("telegram", "connected")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.store.connector("telegram", "error")
                await asyncio.sleep(10)
        finally:
            if self.client:
                await self.client.disconnect()
