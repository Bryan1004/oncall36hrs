import time
import asyncio
import pytest
import httpx
from app.config import Config
from app.store import Store
from app.notify import Dispatcher, Notifier, DeliveryError


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "test.sqlite"))
    s.upsert_groups("telegram", [{"id": "1", "title": "运维"}])
    s.select("telegram", "1", True)
    yield s
    s.db.close()


def event(**changes):
    return {"platform": "telegram", "chat_id": "1", "message_id": "7", "timestamp": time.time(),
            "text": "看看接口", "sender": "同事", "mentioned": True, **changes}


def test_dedup_and_selection(store):
    e = event()
    assert store.ingest(e)
    assert store.ingest(e) is None
    assert store.ingest(event(chat_id="unselected")) is None
    assert len(store.alerts(True)) == 1
    assert len(store.rows("SELECT * FROM messages")) == 1


@pytest.mark.parametrize("change", [{"from_me": True}, {"historical": True},
    {"timestamp": time.time()-600}, {"mentioned": False, "reply_to_me": False}])
def test_no_spurious_call(store, change):
    assert store.ingest(event(**change))
    assert not store.alerts(True)


def test_reply_triggers_and_unselect_cancels(store):
    store.ingest(event(mentioned=False, reply_to_me=True))
    assert store.alerts(True)[0]["reason"] == "reply"
    store.select("telegram", "1", False)
    assert not store.alerts(True)


def test_group_refresh_does_not_lose_selection(store):
    store.upsert_groups("telegram", [{"id": "1", "title": "新群名"}])
    assert store.selected("telegram", "1")
    assert store.groups()[0]["title"] == "新群名"


class FakeNotifier:
    def __init__(self, fails=False):
        self.calls = []
        self.fails = fails

    async def send(self, mode, alerts):
        self.calls.append((mode, [a["id"] for a in alerts]))
        if self.fails:
            raise DeliveryError("provider unavailable")
        return "accepted"


@pytest.mark.asyncio
async def test_dispatch_batches_repeats_ack_and_pause(store):
    fake = FakeNotifier()
    d = Dispatcher(store, fake)
    store.ingest(event())
    store.ingest(event(message_id="8"))
    await d.tick(1000)
    assert not fake.calls  # startup paused
    store.set("mode", "home")
    await d.tick(1000)
    assert len(fake.calls) == 1 and len(fake.calls[0][1]) == 2
    await d.tick(1050)
    assert len(fake.calls) == 1
    await d.tick(1120)
    assert len(fake.calls) == 2
    await d.acknowledge(fake.calls[0][1])
    await d.tick(1240)
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_failure_is_not_ack_and_retry_is_throttled(store):
    fake = FakeNotifier(fails=True)
    store.ingest(event())
    store.set("mode", "home")
    d = Dispatcher(store, fake)
    await d.tick(1000)
    await d.tick(1001)
    assert len(fake.calls) == 1
    assert len(store.alerts(True)) == 1
    assert store.rows("SELECT status FROM deliveries")[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_restart_does_not_immediately_redial(tmp_path):
    path = str(tmp_path / "restart.sqlite")
    s = Store(path)
    s.upsert_groups("telegram", [{"id": "1", "title": "Test"}])
    s.select("telegram", "1", True)
    s.ingest(event())
    s.set("mode", "home")
    fake = FakeNotifier()
    await Dispatcher(s, fake).tick(1000)
    s.db.close()
    s = Store(path)
    await Dispatcher(s, fake).tick(1001)
    assert len(fake.calls) == 1
    assert s.alerts(True)
    s.db.close()


@pytest.mark.asyncio
async def test_mode_change_only_uses_new_channel(store):
    fake = FakeNotifier()
    store.ingest(event())
    d = Dispatcher(store, fake)
    await d.set_mode("home")
    await d.set_mode("away")
    await d.tick(time.time()+1)
    assert fake.calls[0][0] == "away"
    await d.set_mode("paused")
    await d.tick(time.time()+1000)
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_bark_http200_errors_not_success():
    cfg = Config(delivery_enabled=True, bark_home_device_key="test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="Error - Not authorized"))) as client:
        with pytest.raises(DeliveryError):
            await Notifier(cfg, client).send("home", [{"id": 1}])


@pytest.mark.asyncio
async def test_push_no_provider_repeat_and_no_message_leak():
    requests = []
    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 200})
    cfg = Config(delivery_enabled=True, bark_device_key="test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        assert await Notifier(cfg, client).send("away", [{"text": "private content"}]) == "accepted"
    body = requests[0].content.decode()
    assert '"level":"active"' in body and "private" not in body and '"call"' not in body and '"retry"' not in body


@pytest.mark.asyncio
async def test_dry_run_does_not_access_network():
    def transport(request):
        pytest.fail("Dry run accessed network")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        assert await Notifier(Config(delivery_enabled=False), client).send("home", []) == "simulated"


def test_retention_preserves_pending_and_labels(store):
    now = time.time()
    mid = store.ingest(event(timestamp=now-30*86400))
    store.db.execute("UPDATE messages SET label='action' WHERE id=?", (mid,))
    store.db.commit()
    store.ingest(event(message_id="old", timestamp=now-30*86400))
    pending = store.ingest(event(message_id="pending"))
    store.db.execute("UPDATE messages SET created=? WHERE id=?", (now-30*86400, pending))
    store.db.commit()
    store.prune(14)
    assert {m['external_id'] for m in store.rows('SELECT * FROM messages')} == {'7', 'pending'}


@pytest.mark.asyncio
async def test_ack_completes_during_inflight_request_and_stops_next_call(store):
    started, release = asyncio.Event(), asyncio.Event()
    class SlowNotifier(FakeNotifier):
        async def send(self, mode, alerts):
            self.calls.append(mode)
            started.set()
            await release.wait()
            return 'accepted'
    fake = SlowNotifier()
    d = Dispatcher(store, fake)
    store.ingest(event())
    store.set('mode', 'home')
    sending = asyncio.create_task(d.tick(1000))
    await started.wait()
    ack = asyncio.create_task(d.acknowledge([store.alerts(True)[0]['id']]))
    await asyncio.sleep(0)
    assert ack.done()
    await asyncio.wait_for(d.set_mode("paused"), timeout=.1)
    release.set()
    await sending
    await ack
    await d.tick(2000)
    assert fake.calls == ['home']


@pytest.mark.asyncio
async def test_bark_home_payload():
    import json
    def transport(request):
        body=json.loads(request.content)
        assert str(request.url)=='https://api.day.app/push'
        assert body['level']=='critical' and body['call']=='1'
        assert body['sound']=='minuet'
        assert body['device_key']=='test' and 'private' not in request.content.decode()
        return httpx.Response(200,json={'code':200})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        assert await Notifier(Config(delivery_enabled=True,bark_home_device_key='test'),client).send('home',[{'text':'private'}])=='accepted'

@pytest.mark.asyncio
async def test_connection_alerts_never_trigger_repeat_but_work_alerts_do(store):
    store.upsert_groups('system',[{'id':'health','title':'连接异常'}])
    store.select('system','health',True)
    store.ingest(event(platform='system',chat_id='health'))
    store.set('mode','home')
    fake=FakeNotifier()
    dispatcher=Dispatcher(store,fake)
    now=time.time()
    await dispatcher.tick(now)
    await dispatcher.tick(now+121)
    assert fake.calls==[]
    assert store.rows('SELECT * FROM deliveries')==[]
    store.ingest(event())
    work_id=next(a['id'] for a in store.alerts(True) if a['platform']=='telegram')
    await dispatcher.tick(now+242)
    assert fake.calls==[('home',[work_id])]


def test_bark_sources_grouped_bounded_without_message_text():
    alerts=[{'platform':'telegram','chat_id':'1','title':'生产运维群','text':'secret'},
            {'platform':'telegram','chat_id':'1','title':'生产运维群','text':'secret'},
            {'platform':'whatsapp','chat_id':'1','title':'生产运维群','text':'secret'}]
    body=Notifier.message_body(alerts)
    assert 'Telegram · 生产运维群：2 条' in body
    assert 'WhatsApp · 生产运维群：1 条' in body
    assert '3 条消息' in body and 'secret' not in body
    alerts += [{'platform':'telegram','chat_id':str(i),'title':'a'*200+'\nnew line'} for i in range(2,10)]
    body=Notifier.message_body(alerts)
    assert '另有 5 个群组' in body and len(body)<600
