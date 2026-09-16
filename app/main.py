import asyncio
import base64
import io
import html
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import qrcode
import qrcode.image.svg
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, SecretStr

from app.ai_review import install_ai
from app.learning import install_learning
from app.config import Config
from app.confirmation import verify
from app.group_sync import WhatsAppGroupSync
from app.store import Store
from app.notify import Dispatcher, Notifier
from app.quiet_hours import QuietHours, quiet_active
from app.telegram import TelegramMonitor, LoginError


class BarkSettings(BaseModel):
    mode: Literal["home", "away"] = "away"
    device_key: SecretStr


class Mode(BaseModel):
    mode: Literal["home", "away", "paused"]


class ConnectionConfirmation(BaseModel):
    confirmed: bool = False


class Selection(BaseModel):
    enabled: bool


class GroupSettings(BaseModel):
    revision: int = Field(default=0, ge=0)
    keywords: list[str] = Field(max_length=50)
    selections: dict[str, bool] = Field(default_factory=dict, max_length=10000)


class GroupNoticeRead(BaseModel):
    last_id: int = Field(ge=0)


class GroupFilters(BaseModel):
    keywords: list[str] = Field(max_length=50)


class LoginPhone(BaseModel):
    phone: str = Field(min_length=7, max_length=30)


class LoginCode(BaseModel):
    code: str = Field(pattern=r"^\d{4,10}$")


class LoginPassword(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class SignedAck(BaseModel):
    token: str = Field(min_length=1, max_length=100000)


class Ack(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


class Intervals(BaseModel):
    call_interval: int = Field(ge=120, le=86400)
    push_interval: int = Field(ge=60, le=86400)


class Label(BaseModel):
    label: Literal["action", "inform", "irrelevant", "unclear"]
    urgent: bool = False


class Event(BaseModel):
    platform: Literal["whatsapp"]
    chat_id: str = Field(max_length=200)
    message_id: str = Field(max_length=200)
    sender: str = Field(default="", max_length=500)
    text: str = Field(default="", max_length=100000)
    timestamp: float = Field(allow_inf_nan=False, ge=0)
    from_me: bool = False
    historical: bool = False
    mentioned: bool = False
    reply_to_me: bool = False


class Heartbeat(BaseModel):
    state: Literal["connecting", "needs_scan", "connected", "logged_out", "reconnecting", "error", "message_error", "disconnected"]


def create_app(config=None, workers=True):
    config = config or Config()
    os.umask(0o077)
    if len(config.bridge_token) < 24:
        raise RuntimeError("请先运行 scripts/setup.py 并加载 .env；BRIDGE_TOKEN至少24位")
    store = Store(str(Path(config.data_dir) / "alerts.sqlite3"))
    if store.get("group_sync_seen") is None:
        # Existing sync history predates shared read state; do not replay it on new devices.
        store.set("group_sync_seen", store.get("group_sync_sequence") or 0)
    if store.get("bark_away_device_key") is None:
        legacy_key = store.get("bark_device_key")
        if legacy_key:
            store.set("bark_away_device_key", legacy_key)
    if config.delivery_enabled:
        # A past dry-run message must never become a real phone call after restart.
        store.select("demo", "demo", False)
    for platform in ("whatsapp", "telegram"):
        key = "group_hidden_keywords_" + platform
        if store.get(key) is None:
            store.set(key, store.get("group_hidden_keywords") or [])
    # Legacy connection alerts must never become repeating work notifications.
    store.select("system", "health", False)
    notifier = Notifier(config, store=store)
    dispatcher = Dispatcher(store, notifier)
    telegram = TelegramMonitor(config, store)
    client = httpx.AsyncClient(timeout=20)

    def connection_status():
        result = {r["name"]: r for r in store.rows("SELECT * FROM connectors")}
        for name in ["telegram", "whatsapp"]:
            result.setdefault(name, {"name": name, "state": "waiting", "updated": 0, "changed": 0})
            row = result[name]
            if row["state"] not in {"not_configured", "needs_login", "disconnected"} and time.time() - row["updated"] > 45:
                row["state"] = "offline"
        return result

    async def loop():
        last_prune = 0
        while True:
            try:
                await dispatcher.tick()
                if time.time() - last_prune > 3600:
                    store.prune(config.retention_days)
                    last_prune = time.time()
            except asyncio.CancelledError:
                raise
            except Exception:
                store.connector("scheduler", "error")
            await asyncio.sleep(2)

    async def group_loop():
        tg_connection, tg_next = None, 0
        while True:
            try:
                await wa_sync.sync()
                tg = connection_status()['telegram']
                if tg['state'] == 'connected' and (tg_connection != tg['changed'] or time.time() >= tg_next):
                    tg_connection, tg_next = tg['changed'], time.time() + 15
                    if await asyncio.wait_for(telegram.refresh(), timeout=20):
                        tg_next = time.time() + 300
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # Retry automatically; never convert a sync failure into an empty group list.
            await asyncio.sleep(2)

    @asynccontextmanager
    async def lifespan(app):
        tasks = [asyncio.create_task(loop()), asyncio.create_task(telegram.run()), asyncio.create_task(group_loop())] if workers else []
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.aclose()
        await notifier.client.aclose()
        store.db.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.dispatcher = store, dispatcher
    app.state.telegram = telegram
    install_learning(app, store)
    install_ai(app, store)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if request.url.path.startswith('/api/telegram/login') or request.url.path in {'/api/bark','/api/ai/config'}:
            return JSONResponse({'detail': '输入格式不正确，请检查后重试'}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(LoginError)
    async def login_error(request, exc):
        return JSONResponse({"detail": str(exc), "login": telegram.login_status()}, status_code=exc.status)

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if request.url.path != "/healthz":
            if request.url.path.startswith("/internal/"):
                authorization = request.headers.get("authorization", "")
                ok = secrets.compare_digest(authorization.encode(), ("Bearer " + config.bridge_token).encode())
                if not ok:
                    return Response(status_code=401, headers={"WWW-Authenticate": 'Bearer realm="On-call"'})
            elif request.method not in {"GET", "HEAD", "OPTIONS"}:
                if request.headers.get("x-requested-with") != "oncall":
                    return JSONResponse({"detail": "缺少请求校验头"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.get("/confirm", response_class=HTMLResponse)
    async def confirmation_page():
        template = (Path(__file__).parent / "static/confirm.html").read_text(encoding="utf-8")
        escaped_url = html.escape(config.public_url, quote=True)
        rendered = template.replace('href="/"', f'href="{escaped_url}"')
        return HTMLResponse(rendered)

    @app.get("/confirm/style.css")
    async def confirmation_style():
        return FileResponse(Path(__file__).parent / "static/confirm.css")

    @app.get("/confirm/client.js")
    async def confirmation_script():
        return FileResponse(Path(__file__).parent / "static/confirmation.js")

    @app.post("/confirm")
    async def confirm_notification(body: SignedAck):
        try:
            ids = verify(config, body.token)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        await dispatcher.acknowledge(ids)
        return {"status": "acknowledged"}

    @app.get("/healthz")
    async def health():
        return {"ok": True}

    @app.api_route("/", methods=["GET", "HEAD"])
    async def index():
        return FileResponse(Path(__file__).parent / "static/index.html")

    @app.api_route("/favicon.ico", methods=["GET", "HEAD"])
    async def favicon():
        return FileResponse(Path(__file__).parent / "static/favicon.ico")

    @app.api_route("/apple-touch-icon.png", methods=["GET", "HEAD"])
    @app.api_route("/apple-touch-icon-precomposed.png", methods=["GET", "HEAD"])
    async def apple_touch_icon():
        return FileResponse(Path(__file__).parent / "static/apple-touch-icon.png")

    @app.api_route("/manifest.json", methods=["GET", "HEAD"])
    async def manifest():
        return FileResponse(Path(__file__).parent / "static/manifest.json")

    @app.api_route("/assets/{name:path}", methods=["GET", "HEAD"])
    async def asset(name: str):
        static_dir = (Path(__file__).parent / "static").resolve()
        file_path = (static_dir / name).resolve()
        if not file_path.is_relative_to(static_dir) or not file_path.is_file():
            raise HTTPException(status_code=404, detail="Asset not found")
        return FileResponse(file_path)

    @app.get("/api/state")
    async def state():
        connections = connection_status()
        return {"mode": store.get("mode"), "delivery_enabled": config.delivery_enabled,
            "call_interval": store.get("call_interval"), "push_interval": store.get("push_interval"),
            "next_delivery": store.get("next_delivery"), "connections": connections,
            "quiet_hours": store.get("quiet_hours") or QuietHours().model_dump(), "quiet_active": quiet_active(store),
            "configured": {"bark": bool(notifier.device_key("away")),
                "bark_home": bool(notifier.device_key("home")), "bark_away": bool(notifier.device_key("away")),
                "telegram": bool(config.telegram_api_id and config.telegram_api_hash)},
            "groups": store.groups(), "alerts": store.alerts(),
            "disconnected": {p: bool(store.get(p + "_disconnected")) or (p == "whatsapp" and connections[p]["state"] == "disconnected") for p in ("telegram", "whatsapp")},
            "group_sync_notices": store.pending_group_notices(),
            "group_sync_seen": store.get("group_sync_seen") or 0,
            "group_settings": {p: {"revision": store.get("group_revision_" + p) or 0, "keywords": store.get("group_hidden_keywords_" + p) or []} for p in ("whatsapp", "telegram")},
            "group_hidden_keywords": store.get("group_hidden_keywords") or [],
            "deliveries": store.rows("SELECT * FROM deliveries ORDER BY id DESC LIMIT 30")}

    @app.post("/api/group-notices/read")
    async def read_group_notices(body: GroupNoticeRead):
        latest = min(body.last_id, store.get("group_sync_sequence") or 0)
        seen = max(store.get("group_sync_seen") or 0, latest)
        store.set("group_sync_seen", seen)
        return {"seen": seen}

    @app.post("/api/bark")
    async def bark_settings(body: BarkSettings):
        key = body.device_key.get_secret_value().strip()
        if not key or len(key) > 256 or any(not (c.isascii() and (c.isalnum() or c in "_-")) for c in key):
            raise HTTPException(422, "请填写 Device Key 本身，不要粘贴整条推送网址")
        store.set("bark_" + body.mode + "_device_key", key)
        return {"ok": True}

    @app.post("/api/mode")
    async def mode(body: Mode):
        await dispatcher.set_mode(body.mode)
        return {"ok": True}

    @app.get("/api/telegram/login")
    async def telegram_login_status():
        return telegram.login_status()

    @app.post("/api/telegram/login/send-code")
    async def telegram_send_code(body: LoginPhone):
        return await telegram.send_code(body.phone)

    @app.post("/api/telegram/login/code")
    async def telegram_verify_code(body: LoginCode):
        return await telegram.verify(body.code)

    @app.post("/api/telegram/login/password")
    async def telegram_verify_password(body: LoginPassword):
        return await telegram.verify(body.password, password=True)

    @app.post("/api/telegram/login/cancel")
    async def telegram_cancel_login():
        return await telegram.cancel_login()

    @app.post("/api/quiet-hours")
    async def save_quiet_hours(body: QuietHours):
        store.set("quiet_hours", body.model_dump())
        return {"ok": True}

    @app.post("/api/intervals")
    async def intervals(body: Intervals):
        async with dispatcher.lock:
            store.set("call_interval", body.call_interval)
            store.set("push_interval", body.push_interval)
            seconds = body.call_interval if store.get("mode") == "home" else body.push_interval
            store.set("next_delivery", max(time.time(), store.get("last_delivery") + seconds))
        return {"ok": True}

    @app.post("/api/ack")
    async def ack(body: Ack):
        await dispatcher.acknowledge(body.ids)
        return {"ok": True}

    @app.post("/api/ack-all")
    async def ack_all():
        async with dispatcher.lock:
            store.acknowledge([a['id'] for a in store.alerts(pending=True)])
        return {"ok": True}

    @app.post("/api/test/{channel}")
    async def test_channel(channel: Literal["home", "away"]):
        result = await dispatcher.test(channel)
        return result

    @app.post("/api/group-settings/{platform}")
    async def save_group_settings(platform: Literal["whatsapp", "telegram"], body: GroupSettings):
        keywords = []
        for value in body.keywords:
            keyword = value.strip()
            if len(keyword) > 100:
                raise HTTPException(422, "每个关键词最多 100 个字符")
            if keyword and keyword.casefold() not in {k.casefold() for k in keywords}:
                keywords.append(keyword)
        async with dispatcher.lock:
            revision = store.get('group_revision_' + platform) or 0
            if body.revision != revision:
                raise HTTPException(409, '另一台设备已修改设置。请先重置草稿以加载最新设置，再重新勾选。')
            known = {g["id"] for g in store.groups(platform)}
            if not set(body.selections).issubset(known):
                raise HTTPException(409, "部分群组已不存在，请刷新后重试")
            with store.db:
                store.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('group_revision_' + platform, json.dumps(revision + 1)))
                store.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",
                    ("group_hidden_keywords_" + platform, json.dumps(keywords)))
                for chat_id, enabled in body.selections.items():
                    store.db.execute("UPDATE groups SET enabled=? WHERE platform=? AND id=?", (int(enabled), platform, chat_id))
                    if not enabled:
                        store.db.execute("""UPDATE alerts SET status='cancelled' WHERE status='pending'
                            AND message_id IN (SELECT id FROM messages WHERE platform=? AND chat_id=?)""", (platform, chat_id))
        return {"keywords": keywords, "revision": revision + 1, "ok": True}

    @app.post("/api/group-filters")
    async def group_filters(body: GroupFilters):
        keywords = []
        for value in body.keywords:
            keyword = value.strip()
            if len(keyword) > 100:
                raise HTTPException(422, "每个关键词最多 100 个字符")
            if keyword and keyword.casefold() not in {k.casefold() for k in keywords}:
                keywords.append(keyword)
        store.set("group_hidden_keywords", keywords)
        return {"keywords": keywords}

    @app.post("/api/groups/{platform}/{chat_id}")
    async def selection(platform: Literal["telegram", "whatsapp"], chat_id: str, body: Selection):
        async with dispatcher.lock:
            if not store.select(platform, chat_id, body.enabled):
                raise HTTPException(404, "群不存在，请先刷新")
            store.set("group_revision_" + platform, (store.get("group_revision_" + platform) or 0) + 1)
        return {"ok": True}

    async def bridge(route, method="GET"):
        try:
            response = await client.request(method, config.wa_url + route, headers={"Authorization": "Bearer " + config.bridge_token})
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "WhatsApp 接入尚未就绪，请查看连接状态")

    wa_sync = WhatsAppGroupSync(store, lambda: bridge("/groups"))
    app.state.wa_sync = wa_sync

    @app.post("/api/connections/{platform}/{operation}")
    async def connection_action(platform: Literal["telegram", "whatsapp"], operation: Literal["disconnect", "reconnect"], body: ConnectionConfirmation):
        disconnected = operation == "disconnect"
        if disconnected and not body.confirmed:
            raise HTTPException(409, "请先确认退出账号；之后需要重新扫码或登录")
        if platform == "telegram":
            return await telegram.set_disconnected(disconnected)
        result = await bridge("/pause" if disconnected else "/resume", method="POST")
        store.set("whatsapp_disconnected", disconnected)
        store.connector("whatsapp", "disconnected" if disconnected else "connecting")
        return {"ok": True, "remote_logout": result.get("remote_logout", True)}

    @app.get("/api/whatsapp/qr")
    async def wa_qr():
        status = await bridge("/status")
        if status.get("qr"):
            img = qrcode.make(status["qr"], image_factory=qrcode.image.svg.SvgPathImage)
            buf = io.BytesIO()
            img.save(buf)
            status["image"] = "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()
        status.pop("qr", None)
        return status

    @app.post("/api/refresh/{platform}")
    async def refresh(platform: Literal["telegram", "whatsapp"]):
        if platform == "whatsapp":
            if not await wa_sync.sync(force=True):
                raise HTTPException(409, "WhatsApp 尚未连接，请稍后重试")
        else:
            try:
                ok = await telegram.refresh()
            except Exception:
                raise HTTPException(503, "Telegram 读取群列表失败，请稍后重试")
            if not ok:
                raise HTTPException(409, "请先在「连接与设置」完成 Telegram 登录")
        return {"ok": True}

    @app.get("/api/samples")
    async def samples():
        return store.rows("""SELECT m.*,g.title FROM messages m JOIN groups g
          ON g.platform=m.platform AND g.id=m.chat_id WHERE m.platform IN ('telegram','whatsapp') AND COALESCE(m.from_me,0)=0 AND has_review_text(m.text)=1 AND COALESCE(m.reason,'') <> 'mention'
          ORDER BY m.id DESC LIMIT 100""")

    @app.get("/api/samples/{mid}/context")
    async def context(mid: int):
        row = store.rows("SELECT * FROM messages WHERE id=?", (mid,))
        if not row:
            raise HTTPException(404)
        message = row[0]
        return store.rows("""SELECT * FROM messages WHERE platform=? AND chat_id=? AND (created<? OR (created=? AND id<=?))
          ORDER BY created DESC,id DESC LIMIT 6""", (message["platform"], message["chat_id"], message["created"], message["created"], mid))[::-1]

    @app.post("/api/samples/{mid}/label")
    async def label(mid: int, body: Label):
        cur = store.db.execute("UPDATE messages SET label=?,urgent=? WHERE id=? AND COALESCE(from_me,0)=0 AND has_review_text(text)=1 AND COALESCE(reason,'') <> 'mention'", (body.label, body.urgent, mid))
        store.db.commit()
        if not cur.rowcount:
            raise HTTPException(404)
        return {"ok": True}

    @app.get("/api/export")
    async def export():
        rows = store.rows("SELECT * FROM messages WHERE label IS NOT NULL AND COALESCE(from_me,0)=0 AND has_review_text(text)=1 AND COALESCE(reason,'') <> 'mention' ORDER BY id")
        return Response("\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
            media_type="application/x-ndjson", headers={"Content-Disposition": 'attachment; filename="labels.jsonl"'})

    @app.post("/api/demo")
    async def demo():
        if config.delivery_enabled:
            raise HTTPException(409, "真实提醒已开启，演练消息入口已关闭")
        store.upsert_groups("demo", [{"id": "demo", "title": "演练 · 运维工作群"}])
        store.select("demo", "demo", True)
        store.ingest({"platform": "demo", "chat_id": "demo", "message_id": secrets.token_hex(12),
            "sender": "演练同事", "text": "@你：监控发现接口延迟升高，请帮忙查看。这是一条演练消息。",
            "timestamp": time.time(), "mentioned": True})
        return {"ok": True}

    @app.get("/internal/wa/config")
    async def wa_config():
        return {"selected": [g["id"] for g in store.groups("whatsapp") if g["enabled"]]}

    @app.post("/internal/wa/heartbeat")
    async def wa_heartbeat(body: Heartbeat):
        store.connector("whatsapp", body.state)
        store.set("whatsapp_disconnected", body.state == "disconnected")
        return {"ok": True}

    @app.post("/internal/wa/events")
    async def wa_event(body: Event):
        return {"id": None if store.get("whatsapp_disconnected") else store.ingest(body.model_dump())}

    return app


def factory():
    return create_app()
