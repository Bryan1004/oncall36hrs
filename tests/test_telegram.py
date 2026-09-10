import time
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from telethon import types
from app.config import Config
from app.store import Store
from app.telegram import TelegramMonitor


@pytest.mark.asyncio
@pytest.mark.parametrize('entity,text,reply_sender,expected',[
    (types.MessageEntityMention(0, 7), '@myname', None, 'mention'),
    (types.MessageEntityMention(0, 7), '@others', None, None),
    (types.MessageEntityMentionName(0, 2, 42), '名字', None, 'mention'),
    (types.MessageEntityMentionName(0, 2, 43), '名字', None, None),
    (None, '@myname', None, None),
    (None, '处理了吗？', 42, 'reply'),
    (None, '处理了吗？', 43, None),
])
async def test_real_telegram_entities_and_reply_ownership(tmp_path,entity,text,reply_sender,expected):
    store=Store(str(tmp_path/'tg.sqlite'))
    try:
        store.upsert_groups('telegram',[{'id':'-1001','title':'运维'}])
        store.select('telegram','-1001',True)
        monitor=TelegramMonitor(Config(),store)
        monitor.me=SimpleNamespace(id=42,username='myname')
        message=types.Message(id=1,peer_id=types.PeerChat(1001),message=text,date=datetime.now(timezone.utc),
            entities=[entity] if entity else [],from_id=types.PeerUser(43),out=False)
        if reply_sender:
            message.reply_to=types.MessageReplyHeader(reply_to_msg_id=3)
        async def get_reply():
            return SimpleNamespace(sender_id=reply_sender) if reply_sender else None
        message.get_reply_message=get_reply
        async def get_sender():
            return SimpleNamespace(first_name='同事',last_name='')
        event=SimpleNamespace(is_group=True,chat_id=-1001,message=message,get_sender=get_sender)
        await monitor.handle(event)
        alerts=store.alerts(True)
        assert (alerts[0]['reason'] if alerts else None)==expected
    finally:
        store.db.close()
