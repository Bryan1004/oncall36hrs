import time

import httpx
import pytest

from app.config import Config
from app.main import create_app
from app.store import Store


@pytest.mark.asyncio
@pytest.mark.parametrize('platform', ['whatsapp', 'telegram'])
async def test_group_mentions_only(tmp_path, platform):
    app = create_app(Config(data_dir=str(tmp_path), bridge_token='group-mentions-only-test-token',
                            delivery_enabled=False, telegram_api_id='', telegram_api_hash=''), workers=False)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://test',
        headers={'X-Requested-With': 'oncall'},
    ) as client:
        store = app.state.store
        store.upsert_groups(platform, [{'id': '1', 'title': 'ops'}, {'id': '2', 'title': 'other'}])
        store.select(platform, '1', True)
        store.select(platform, '2', True)
        def ingest(mid, **kwargs):
            return store.ingest(dict(platform=platform, chat_id='1', message_id=mid,
                                     timestamp=time.time(), text='检查服务', **kwargs))
        old = ingest('old', reply_to_me=True)
        assert (await client.post(f'/api/learning/samples/{old}', json={'label': 'action'})).status_code == 200
        result = await client.post(f'/api/group-settings/{platform}', json={
            'keywords': [], 'mentions_only': {'1': True}})
        assert result.status_code == 200
        assert not store.alerts(True)
        assert (await client.post('/api/learning/publish', json={})).status_code == 409
        for kwargs in [{}, {'reply_to_me': True}, {'historical': True}, {'mentioned': True, 'from_me': True}]:
            assert ingest(str(kwargs), **kwargs) is None
        mention = ingest('mention', mentioned=True)
        assert [a['message_id'] for a in store.alerts(True)] == [mention]
        assert ingest('history-mention', mentioned=True, historical=True)
        assert len(store.alerts(True)) == 1
        listing = (await client.get(f'/api/learning/samples?platform={platform}')).json()
        assert listing['total'] == 0
        assert (await client.get('/api/samples')).json() == []
        assert (await client.get('/api/export')).text == ''
        assert (await client.get('/api/learning')).json()['reviewed'] == 0
        assert (await client.post(f'/api/learning/samples/{old}', json={'label': 'action'})).status_code == 404
        assert (await client.post(f'/api/samples/{old}/label', json={'label': 'action'})).status_code == 404
        store.upsert_groups(platform, [{'id': '1', 'title': 'renamed'}])
        assert next(g for g in store.groups(platform) if g['id'] == '1')['mentions_only'] == 1
        reopened = Store(store.db.execute('PRAGMA database_list').fetchone()[2])
        assert next(g for g in reopened.groups(platform) if g['id'] == '1')['mentions_only'] == 1
        reopened.db.close()
        assert store.ingest(dict(platform=platform, chat_id='2', message_id='other', timestamp=time.time(), text='normal'))
        assert (await client.post(f'/api/group-settings/{platform}', json={
            'revision': 0, 'keywords': [], 'mentions_only': {'1': False}})).status_code == 409
        assert (await client.post(f'/api/group-settings/{platform}', json={
            'revision': 1, 'keywords': [], 'mentions_only': {'missing': True}})).status_code == 409
        assert (await client.post(f'/api/group-settings/{platform}', json={
            'revision': 1, 'keywords': [], 'mentions_only': {'1': False}})).status_code == 200
        assert ingest('restored', reply_to_me=True)
        assert len(store.alerts(True)) == 2
