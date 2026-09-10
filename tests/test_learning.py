import json
import time
import httpx
import pytest
from app.main import create_app
from app.config import Config

@pytest.fixture
def app(tmp_path):
    return create_app(Config(data_dir=str(tmp_path),admin_password='test-password-long-enough',bridge_token='test-bridge-token-long-enough',delivery_enabled=False,telegram_api_id='',telegram_api_hash=''),workers=False)

def seed(app):
    s=app.state.store
    for chat in ['ref','eval']:
        s.upsert_groups('telegram',[{'id':chat,'title':chat}]);s.select('telegram',chat,True)
    mids=[]
    for i,chat in enumerate(['ref','eval']):
        mids.append(s.ingest({'platform':'telegram','chat_id':chat,'message_id':str(i),'timestamp':time.time(),'text':'example' if chat=='ref' else 'production down','sender':'operator'}))
    return mids

@pytest.mark.asyncio
async def test_version_snapshot_is_immutable_and_activation(app):
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',auth=('admin',app.state.telegram.config.admin_password),headers={'X-Requested-With':'oncall'}) as c:
        mids=seed(app)
        assert (await c.post('/api/learning/publish',json={})).status_code==409
        await c.post('/api/learning/rules',json={'rules':'production oncall'})
        await c.post('/api/learning/samples/'+str(mids[0]),json={'label':'action','service':'db','rationale':'owner'})
        v1=(await c.post('/api/learning/publish',json={'note':'first'})).json()['id']
        before=(await c.get('/api/learning/versions/'+str(v1))).json()
        await c.post('/api/learning/samples/'+str(mids[0]),json={'label':'irrelevant','rationale':'not owner'})
        await c.post('/api/learning/rules',json={'rules':'new responsibilities'})
        v2=(await c.post('/api/learning/publish',json={})).json()['id']
        assert (await c.get('/api/learning/versions/'+str(v1))).json()==before
        overview=(await c.get('/api/learning')).json()
        assert overview['versions'][0]['changed']==1
        for vid in [v2,v1]: assert (await c.post(f'/api/learning/versions/{vid}/activate',json={})).status_code==200
        assert (await c.get('/api/learning')).json()['active']==v1
        assert not app.state.store.alerts(True)

@pytest.mark.asyncio
async def test_evaluation_uses_holdout_no_labels_and_records_results(app,monkeypatch):
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',auth=('admin',app.state.telegram.config.admin_password),headers={'X-Requested-With':'oncall'}) as c:
        ref,evaluation=seed(app)
        await c.post('/api/learning/rules',json={'rules':'handle production'})
        for mid,split in [(ref,'reference'),(evaluation,'evaluation')]:
            await c.post('/api/learning/samples/'+str(mid),json={'label':'action','urgent':True,'rationale':'private-gold-reason','split':split})
        vid=(await c.post('/api/learning/publish',json={})).json()['id']
        assert (await c.post('/api/ai/evaluate',json={'version_id':vid})).status_code==409
        await c.post('/api/ai/config',json={'base_url':'https://example.com/v1','model':'test-model','api_key':'private-key'})
        assert 'private-key' not in (await c.get('/api/ai')).text
        assert (await c.post('/api/ai/config',json={'base_url':'https://other.example/v1','model':'test-model'})).status_code==422
        original=httpx.AsyncClient.post
        calls=[]
        async def fake(self,url,*args,**kwargs):
            if url=='https://example.com/v1/chat/completions':
                data=json.loads(kwargs['json']['messages'][1]['content']);calls.append(data)
                assert 'label' not in data['message'] and 'rationale' not in data['message']
                assert len(data['examples'])==1 and data['examples'][0]['text']=='example'
                return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'label':'action','urgent':True,'reason':'production'})}}],'usage':{'prompt_tokens':20,'completion_tokens':10}},request=httpx.Request('POST',url))
            return await original(self,url,*args,**kwargs)
        monkeypatch.setattr(httpx.AsyncClient,'post',fake)
        rid=(await c.post('/api/ai/evaluate',json={'version_id':vid})).json()['id']
        run=(await c.get('/api/ai/runs/'+str(rid))).json()
        assert run['status']=='complete' and run['metrics'][0]['correct']==1
        assert len(calls)==1 and run['result']['usage']['prompt_tokens']==20
        assert not app.state.store.alerts(True) and not app.state.store.rows('SELECT * FROM deliveries')
        # Malformed predictions must fail, never silently become irrelevant.
        async def invalid(self,url,*args,**kwargs):
            if url=='https://example.com/v1/chat/completions':return httpx.Response(200,json={'choices':[]},request=httpx.Request('POST',url))
            return await original(self,url,*args,**kwargs)
        monkeypatch.setattr(httpx.AsyncClient,'post',invalid)
        rid=(await c.post('/api/ai/evaluate',json={'version_id':vid})).json()['id']
        assert (await c.get('/api/ai/runs/'+str(rid))).json()['status']=='failed'
