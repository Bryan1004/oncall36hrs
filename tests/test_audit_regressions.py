import asyncio
import json
import time
import httpx
import pytest
from app.config import Config
from app.main import create_app
from app.store import Store
from app.ai_review import evaluation_samples, select_references


def make_app(tmp_path):
    return create_app(Config(data_dir=str(tmp_path),bridge_token='isolated-regression-test-token',delivery_enabled=False,telegram_api_id='',telegram_api_hash=''),workers=False)


@pytest.mark.asyncio
async def test_old_pending_visible_and_ack_all_over_500(tmp_path):
    app=make_app(tmp_path)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'oncall'}) as c:
        s=app.state.store;s.upsert_groups('telegram',[{'id':'1','title':'test'}]);s.select('telegram','1',True)
        for i in range(1002):
            s.ingest(dict(platform='telegram',chat_id='1',message_id=str(i),timestamp=time.time(),mentioned=True))
        s.acknowledge(list(range(503,1003)))
        state=(await c.get('/api/state')).json()
        assert sum(a['status']=='pending' for a in state['alerts'])==502
        assert (await c.post('/api/ack-all',json={})).status_code==200
        assert not s.alerts(True)


@pytest.mark.asyncio
async def test_group_revision_conflict_is_atomic(tmp_path):
    app=make_app(tmp_path)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'oncall'}) as c:
        s=app.state.store;s.upsert_groups('whatsapp',[{'id':'1','title':'test'}])
        first=await c.post('/api/group-settings/whatsapp',json={'revision':0,'keywords':['小时群'],'selections':{}})
        assert first.json()['revision']==1
        stale=await c.post('/api/group-settings/whatsapp',json={'revision':0,'keywords':[],'selections':{'1':True}})
        assert stale.status_code==409
        assert s.get('group_hidden_keywords_whatsapp')==['小时群']
        assert not s.selected('whatsapp','1')


def test_sync_new_groups_removed_groups_and_rejoin(tmp_path):
    s=Store(str(tmp_path/'test.db'))
    try:
        groups=[{'id':'1','title':'运维'},{'id':'2','title':'新群'}]
        assert len(s.sync_groups('telegram',groups))==2
        assert s.get('group_sync_sequence')==1
        assert s.sync_groups('telegram',groups)==[]
        assert s.get('group_sync_sequence')==1
        s.select('telegram','1',True)
        s.ingest(dict(platform='telegram',chat_id='1',message_id='1',timestamp=time.time(),mentioned=True,text='history'))
        s.sync_groups('telegram',[groups[1]])
        assert not s.selected('telegram','1') and not s.alerts(True)
        assert s.rows('SELECT text FROM messages')[0]['text']=='history'
        assert [g['id'] for g in s.groups('telegram')]==['2']
        assert s.sync_groups('telegram',groups)==[groups[0]]
        assert not s.selected('telegram','1')
    finally:s.db.close()


def test_reference_retrieval_and_evaluation_coverage():
    refs=[{'id':i,'text':'unrelated','service':''} for i in range(10)]
    refs[0]['text']='生产数据库 故障'
    assert select_references(refs,{'text':'生产数据库 故障'})[0]['id']==0
    rows=[{'id':i,'split':'evaluation'} for i in range(100)]
    chosen=evaluation_samples(rows,10,'spread')
    assert len(chosen)==10 and chosen[0]['id']==0 and chosen[-1]['id']==99
    assert [r['id'] for r in evaluation_samples(rows,2,'latest')]==[98,99]


@pytest.mark.asyncio
async def test_cancel_ai_stops_remaining_calls_and_preserves_completed(tmp_path,monkeypatch):
    app=make_app(tmp_path)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'oncall'}) as c:
        s=app.state.store
        samples=[dict(id=i,platform='telegram',chat_id=str(i),title='test',text='down',context=[],service='',label='action',urgent=False,split='evaluation') for i in [1,2]]
        s.db.execute('INSERT INTO dataset_versions(created,rules,note,samples) VALUES (?,?,?,?)',(time.time(),'test','',json.dumps(samples)));s.db.commit()
        s.set('ai_config',{'base_url':'https://example.invalid/v1','model':'mock'});s.set('ai_api_key','mock')
        entered,release=asyncio.Event(),asyncio.Event();calls=[];original=httpx.AsyncClient.post
        async def fake(self,url,*args,**kwargs):
            if url=='https://example.invalid/v1/chat/completions':
                calls.append(url);entered.set();await release.wait()
                return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'label':'action','urgent':False,'reason':'test'})}}]},request=httpx.Request('POST',url))
            return await original(self,url,*args,**kwargs)
        monkeypatch.setattr(httpx.AsyncClient,'post',fake)
        request=asyncio.create_task(c.post('/api/ai/evaluate',json={'version_id':1}))
        await asyncio.wait_for(entered.wait(),2)
        assert (await c.post('/api/ai/runs/1/cancel',json={})).status_code==200
        assert (await c.post('/api/ai/evaluate',json={'version_id':1})).status_code==409
        release.set();await request
        run=(await c.get('/api/ai/runs/1')).json()
        assert run['status']=='cancelled' and len(run['result']['items'])==1 and len(calls)==1
        overview=(await c.get('/api/ai')).json()
        assert overview['runs'][0]['completed']==1 and 'result' not in overview['runs'][0]
        assert not s.rows('SELECT * FROM deliveries')


@pytest.mark.asyncio
async def test_own_messages_excluded_but_kept_in_context(tmp_path):
    app=make_app(tmp_path)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'oncall'}) as c:
        s=app.state.store;s.upsert_groups('telegram',[{'id':'1','title':'test'}]);s.select('telegram','1',True)
        now=time.time()
        own=s.ingest(dict(platform='telegram',chat_id='1',message_id='own',timestamp=now,text='my context',from_me=True))
        other=s.ingest(dict(platform='telegram',chat_id='1',message_id='other',timestamp=now,text='reply',from_me=False))
        result=(await c.get('/api/learning/samples?platform=telegram')).json()
        assert result['total']==1 and result['counts']['telegram']==1 and result['items'][0]['id']==other
        assert (await c.post(f'/api/learning/samples/{own}',json={'label':'action'})).status_code==404
        assert (await c.post(f'/api/samples/{own}/label',json={'label':'action'})).status_code==404
        context=(await c.get(f'/api/samples/{other}/context')).json()
        assert [r['id'] for r in context]==[own,other]
        # Legacy records have unknown ownership; user can identify a specific message.
        s.db.execute('UPDATE messages SET from_me=NULL WHERE id=?',(other,));s.db.commit()
        await c.post(f'/api/learning/samples/{other}',json={'label':'action'})
        await c.post('/api/learning/rules',json={'rules':'ops'})
        vid=(await c.post('/api/learning/publish',json={})).json()['id']
        snapshot=(await c.get(f'/api/learning/versions/{vid}')).json()
        assert (await c.post(f'/api/learning/samples/{other}/own',json={})).status_code==200
        assert (await c.get('/api/learning/samples?platform=telegram')).json()['total']==0
        assert (await c.get('/api/learning')).json()['reviewed']==0
        assert (await c.get(f'/api/learning/versions/{vid}')).json()==snapshot
        assert len(s.rows('SELECT * FROM messages'))==2


@pytest.mark.asyncio
@pytest.mark.parametrize('platform',['whatsapp','telegram'])
async def test_nontext_excluded_from_review_but_captions_and_alerts_remain(tmp_path,platform):
    app=make_app(tmp_path)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'oncall'}) as c:
        s=app.state.store;s.upsert_groups(platform,[{'id':'1','title':'test'}]);s.select(platform,'1',True)
        mids=[]
        for i,text in enumerate(['[非文字消息]','  \n\t\u3000','', '图片说明：生产报错，请处理']):
            mids.append(s.ingest(dict(platform=platform,chat_id='1',message_id=str(i),timestamp=time.time(),text=text,reply_to_me=True)))
        # Legacy reviewed placeholders must also disappear from new drafts and exports.
        s.db.execute("UPDATE messages SET label='action' WHERE id=?",(mids[0],));s.db.commit()
        result=(await c.get('/api/learning/samples?platform='+platform)).json()
        assert result['total']==1 and result['counts'][platform]==1
        assert [r['id'] for r in result['items']]==[mids[3]]
        assert len(s.alerts(True))==4  # This change applies only to responsibility review.
        for mid in mids[:3]:
            assert (await c.post(f'/api/learning/samples/{mid}',json={'label':'action'})).status_code==404
            assert (await c.post(f'/api/samples/{mid}/label',json={'label':'action'})).status_code==404
        assert (await c.get('/api/learning')).json()['reviewed']==0
        assert (await c.get('/api/export')).text==''
        await c.post(f'/api/learning/samples/{mids[3]}',json={'label':'action'})
        await c.post('/api/learning/rules',json={'rules':'ops'})
        vid=(await c.post('/api/learning/publish',json={})).json()['id']
        snap=(await c.get(f'/api/learning/versions/{vid}')).json()
        assert [r['id'] for r in snap['samples']]==[mids[3]]
        assert len(snap['samples'][0]['context'])==4


@pytest.mark.asyncio
async def test_bark_modes_migrate_and_route_independently(tmp_path):
    from app.notify import Notifier, DeliveryError
    s=Store(str(tmp_path/'alerts.sqlite3'));s.set('bark_device_key','legacy-away');s.db.close()
    app=make_app(tmp_path)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'oncall'}) as c:
        n=app.state.dispatcher.notifier
        assert n.device_key('away')=='legacy-away' and not n.device_key('home')
        state=(await c.get('/api/state')).json()
        assert state['configured']['bark_away'] and not state['configured']['bark_home']
        assert 'legacy-away' not in json.dumps(state)
        await c.post('/api/bark',json={'mode':'home','device_key':'home-key'})
        assert n.device_key('home')=='home-key' and n.device_key('away')=='legacy-away'
        await c.post('/api/bark',json={'mode':'away','device_key':'new-away'})
        assert n.device_key('home')=='home-key' and n.device_key('away')=='new-away'
        calls=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(json.loads(r.content)) or httpx.Response(200,json={'code':200}))) as client:
            real=Notifier(Config(delivery_enabled=True),client,app.state.store)
            await real.send('home',[],test=True);await real.send('away',[],test=True)
            assert [r['device_key'] for r in calls]==['home-key','new-away']
            assert calls[0]['level']=='critical' and calls[1]['level']=='active'
            missing_home=Notifier(Config(delivery_enabled=True,bark_device_key='old-env-away'),client)
            with pytest.raises(DeliveryError,match='在家'):await missing_home.send('home',[])
            assert len(calls)==2
    restarted=make_app(tmp_path)
    async with restarted.router.lifespan_context(restarted):
        assert restarted.state.dispatcher.notifier.device_key('away')=='new-away'
        assert restarted.state.dispatcher.notifier.device_key('home')=='home-key'


@pytest.mark.asyncio
async def test_group_notices_shared_read_and_new_arrivals(tmp_path):
    app=make_app(tmp_path)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Requested-With':'oncall'}) as a,httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as b:
        s=app.state.store
        s.sync_groups('telegram',[{'id':'1','title':'first'}]);s.select('telegram','1',True)
        notice=(await a.get('/api/state')).json()['group_sync_notices'][0]
        assert notice['groups'][0]['enabled']==1
        s.sync_groups('telegram',[{'id':'1','title':'first'},{'id':'2','title':'second'}])
        await a.post('/api/group-notices/read',json={'last_id':notice['id']})
        pending=(await b.get('/api/state')).json()['group_sync_notices']
        assert len(pending)==1 and pending[0]['groups'][0]['id']=='2'
        await a.post('/api/group-notices/read',json={'last_id':pending[0]['id']})
        await a.post('/api/group-notices/read',json={'last_id':notice['id']})
        assert (await b.get('/api/state')).json()['group_sync_notices']==[]
    restarted=make_app(tmp_path)
    async with restarted.router.lifespan_context(restarted):
        assert restarted.state.store.pending_group_notices()==[]
        restarted.state.store.sync_groups('whatsapp',[{'id':'3','title':'new after restart'}])
        assert len(restarted.state.store.pending_group_notices())==1


@pytest.mark.asyncio
async def test_old_group_notice_history_migrates_without_replaying(tmp_path):
    s=Store(str(tmp_path/'alerts.sqlite3'));s.sync_groups('whatsapp',[{'id':'1','title':'existing'}]);s.db.close()
    app=make_app(tmp_path)
    async with app.router.lifespan_context(app):
        s=app.state.store
        assert s.get('group_sync_seen')==1 and s.pending_group_notices()==[]
        s.sync_groups('whatsapp',[{'id':'1','title':'existing'},{'id':'2','title':'new'}])
        assert s.pending_group_notices()[0]['groups'][0]['id']=='2'


@pytest.mark.asyncio
async def test_whatsapp_paused_bridge_corrects_panel_state(tmp_path):
    app=make_app(tmp_path)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        s=app.state.store;s.set('whatsapp_disconnected',False);s.connector('whatsapp','disconnected')
        assert (await c.get('/api/state')).json()['disconnected']['whatsapp'] is True
        headers={'Authorization':'Bearer isolated-regression-test-token'}
        assert (await c.post('/internal/wa/heartbeat',headers=headers,json={'state':'disconnected'})).status_code==200
        assert s.get('whatsapp_disconnected') is True
        await c.post('/internal/wa/heartbeat',headers=headers,json={'state':'needs_scan'})
        assert (await c.get('/api/state')).json()['disconnected']['whatsapp'] is False
        await c.post('/internal/wa/heartbeat',headers=headers,json={'state':'connected'})
        assert (await c.get('/api/state')).json()['connections']['whatsapp']['state']=='connected'


@pytest.mark.asyncio
async def test_wa_group_auto_sync_connect_retry_and_no_restart_replay(tmp_path):
    from app.group_sync import WhatsAppGroupSync
    s=Store(str(tmp_path/'sync.db'));calls=[]
    async def fetch():
        calls.append(1)
        if len(calls)==1:raise RuntimeError('temporary failure')
        return [{'id':'1','title':'group'}]
    sync=WhatsAppGroupSync(s,fetch)
    try:
        assert await sync.sync() is False
        s.connector('whatsapp','connected')
        with pytest.raises(RuntimeError):await sync.sync()
        assert await sync.sync() is False
        sync.next_attempt=0
        assert await sync.sync() is True
        assert s.groups('whatsapp')[0]['title']=='group'
        assert s.pending_group_notices()[0]['kind']=='connection'
        seq=s.get('group_sync_sequence');s.set('group_sync_seen',seq)
        assert await sync.sync() is False
        restarted=WhatsAppGroupSync(s,fetch)
        assert await restarted.sync() is True
        assert s.pending_group_notices()==[]
        s.connector('whatsapp','disconnected');await restarted.sync()
        s.connector('whatsapp','connected');await restarted.sync()
        assert s.pending_group_notices()[0]['kind']=='connection'
    finally:s.db.close()


@pytest.mark.asyncio
async def test_sync_response_after_disconnect_cannot_replace_groups(tmp_path):
    from app.group_sync import WhatsAppGroupSync
    s=Store(str(tmp_path/'sync.db'));s.upsert_groups('whatsapp',[{'id':'1','title':'existing'}]);s.select('whatsapp','1',True);s.connector('whatsapp','connected')
    async def fetch():
        s.connector('whatsapp','disconnected')
        return []
    try:
        assert await WhatsAppGroupSync(s,fetch).sync() is False
        assert s.selected('whatsapp','1')
    finally:s.db.close()
