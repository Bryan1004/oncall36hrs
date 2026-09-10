import pytest
import httpx
from app.config import Config
from app.main import create_app


@pytest.fixture
def config(tmp_path):
    return Config(data_dir=str(tmp_path), admin_password="test-password-long-enough", bridge_token="bridge-token-long-enough-for-tests", delivery_enabled=False,
                  telegram_api_id="", telegram_api_hash="")


@pytest.mark.asyncio
async def test_auth_csrf_and_bridge_isolation(config):
    app = create_app(config, workers=False)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get('/healthz')).status_code == 200
        assert (await client.get('/')).status_code == 200
        assert (await client.get('/api/state')).status_code == 200
        assert (await client.post('/api/mode', json={'mode':'home'})).status_code == 403
        assert (await client.get('/internal/wa/config')).status_code == 401
        client.headers['X-Requested-With'] = 'oncall'
        assert (await client.post('/api/mode', json={'mode':'away'})).status_code == 200
        assert (await client.post('/api/mode', json={'mode':'invalid'})).status_code == 422
        assert (await client.post('/api/intervals', json={'call_interval':1,'push_interval':1})).status_code == 422


@pytest.mark.asyncio
async def test_demo_ack_flow(config):
    app = create_app(config, workers=False)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
              auth=('admin',config.admin_password), headers={'X-Requested-With':'oncall'}) as client:
        assert (await client.post('/api/demo',json={})).status_code == 200
        alerts=(await client.get('/api/state')).json()['alerts']
        assert len(alerts)==1 and alerts[0]['status']=='pending'
        assert (await client.post('/api/ack',json={'ids':[alerts[0]['id']]})).status_code==200
        assert (await client.get('/api/state')).json()['alerts'][0]['status']=='acknowledged'


@pytest.mark.asyncio
async def test_live_start_cancels_previous_demo(config):
    app = create_app(config, workers=False)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test",
            auth=('admin',config.admin_password),headers={'X-Requested-With':'oncall'}) as client:
        await client.post('/api/demo',json={})
    config.delivery_enabled=True
    app = create_app(config,workers=False)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test",
            auth=('admin',config.admin_password),headers={'X-Requested-With':'oncall'}) as client:
        assert (await client.post('/api/demo',json={})).status_code == 409
        assert not app.state.store.alerts(True)


@pytest.mark.asyncio
async def test_manual_test_has_no_cooldown_and_preserves_schedule(config):
    app=create_app(config,workers=False)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',auth=('admin',config.admin_password),headers={'X-Requested-With':'oncall'}) as client:
        store=app.state.store
        store.set('last_delivery',12345)
        store.set('next_delivery',9999999999)
        for channel in ['home','home','away','home']:
            r=await client.post('/api/test/'+channel,json={})
            assert r.status_code==200 and r.json()['status']=='simulated'
        assert store.get('mode')=='paused'
        assert store.get('last_delivery')==12345
        assert store.get('next_delivery')==9999999999
        assert not store.alerts(True)
        assert len(store.rows('SELECT * FROM deliveries'))==4


@pytest.mark.asyncio
async def test_manual_test_reports_missing_live_config(config):
    config.delivery_enabled = True
    config.bark_device_key = ''
    app = create_app(config, workers=False)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test",
            auth=('admin',config.admin_password),headers={'X-Requested-With':'oncall'}) as client:
        result = await client.post('/api/test/home', json={})
        assert result.json()['status'] == 'failed'
        assert 'Bark Device Key' in result.json()['detail']
        assert app.state.store.get('mode') == 'paused'

@pytest.mark.asyncio
async def test_group_filters_persist_without_changing_monitoring(config):
    app=create_app(config,workers=False)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',auth=('admin',config.admin_password),headers={'X-Requested-With':'oncall'}) as client:
        store=app.state.store
        store.upsert_groups('telegram',[{'id':'123','title':'小时群'}])
        store.select('telegram','123',True)
        r=await client.post('/api/group-filters',json={'keywords':[' 小时群 ','小时群','OPS','ops','']})
        assert r.json()['keywords']==['小时群','OPS']
        assert store.selected('telegram','123')
        assert (await client.post('/api/group-filters',json={'keywords':['x'*101]})).status_code==422
    restored=create_app(config,workers=False)
    async with restored.router.lifespan_context(restored), httpx.AsyncClient(transport=httpx.ASGITransport(app=restored),base_url='http://test',auth=('admin',config.admin_password)) as client:
        state=(await client.get('/api/state')).json()
        assert state['group_hidden_keywords']==['小时群','OPS']
        assert state['groups'][0]['enabled']


@pytest.mark.asyncio
async def test_bark_key_write_only_and_persistent(config):
    app=create_app(config,workers=False)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',auth=('admin',config.admin_password),headers={'X-Requested-With':'oncall'}) as client:
        assert (await client.post('/api/bark',json={'device_key':'https://api.day.app/secret'})).status_code==422
        assert (await client.post('/api/bark',json={'device_key':'privateDeviceKey'})).status_code==200
        r=await client.get('/api/state')
        assert r.json()['configured']['bark'] and 'privateDeviceKey' not in r.text
        assert app.state.dispatcher.notifier.device_key()=='privateDeviceKey'

@pytest.mark.asyncio
async def test_restart_cancels_legacy_connection_alerts(config):
    app=create_app(config,workers=False)
    async with app.router.lifespan_context(app):
        store=app.state.store
        store.upsert_groups('system',[{'id':'health','title':'连接异常'}])
        store.select('system','health',True)
        import time
        store.ingest({'platform':'system','chat_id':'health','message_id':'legacy','timestamp':time.time(),'mentioned':True})
        assert store.alerts(True)
    restored=create_app(config,workers=False)
    async with restored.router.lifespan_context(restored):
        assert not restored.state.store.alerts(True)
        assert restored.state.store.alerts()[0]['status']=='cancelled'

@pytest.mark.asyncio
async def test_connection_toggle_auth_and_persistence(config):
    app=create_app(config,workers=False)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',auth=('admin',config.admin_password)) as client:
        assert (await client.post('/api/connections/telegram/disconnect',json={})).status_code==403
        client.headers['X-Requested-With']='oncall'
        assert (await client.post('/api/connections/telegram/disconnect',json={})).status_code==409
        assert (await client.post('/api/connections/telegram/disconnect',json={'confirmed':True})).status_code==200
        assert (await client.get('/api/state')).json()['disconnected']['telegram']
        assert (await client.get('/api/telegram/login')).json()['step']=='disconnected'
        assert (await client.post('/api/connections/telegram/reconnect',json={})).status_code==200
        assert not (await client.get('/api/state')).json()['disconnected']['telegram']

@pytest.mark.asyncio
async def test_group_settings_isolated_atomic_and_cancel_on_save(config):
    import time
    app=create_app(config,workers=False)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',auth=('admin',config.admin_password),headers={'X-Requested-With':'oncall'}) as client:
        store=app.state.store
        for p in ['whatsapp','telegram']:
            store.upsert_groups(p,[{'id':'1','title':'小时群'},{'id':'2','title':'运维'}])
            store.select(p,'1',True)
        store.ingest({'platform':'telegram','chat_id':'1','message_id':'m','timestamp':time.time(),'mentioned':True})
        r=await client.post('/api/group-settings/whatsapp',json={'keywords':['小时群'],'selections':{'2':True}})
        assert r.status_code==200 and store.selected('whatsapp','2')
        state=(await client.get('/api/state')).json()
        assert state['group_settings']['telegram']['keywords']==[]
        assert state['group_settings']['whatsapp']['keywords']==['小时群']
        assert store.alerts(True)
        r=await client.post('/api/group-settings/telegram',json={'keywords':['new'],'selections':{'1':False,'missing':True}})
        assert r.status_code==409 and store.selected('telegram','1')
        assert store.get('group_hidden_keywords_telegram')==[] and store.alerts(True)
        r=await client.post('/api/group-settings/telegram',json={'keywords':[],'selections':{'1':False}})
        assert r.status_code==200 and not store.alerts(True)
