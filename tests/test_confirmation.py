import time

import httpx
import pytest

from app.config import Config
from app.confirmation import sign, verify, TTL
from app.main import create_app
from app.notify import Notifier


def test_signature_expiry_and_tampering():
    config = Config(bridge_token='a' * 32)
    token = sign(config, [1, 2])
    assert verify(config, token) == [1, 2]
    for bad in [token + '0', 'invalid', sign(config, [1], now=time.time()-TTL-1)]:
        with pytest.raises(ValueError):
            verify(config, bad)
    with pytest.raises(ValueError):
        verify(Config(bridge_token='b' * 32), token)


@pytest.mark.asyncio
async def test_notification_link_and_scoped_confirmation(tmp_path):
    config = Config(data_dir=str(tmp_path), bridge_token='a' * 32, delivery_enabled=False,
                    telegram_api_id='', telegram_api_hash='', confirmation_url='https://ack.example.com')
    app = create_app(config, workers=False)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'X-Requested-With': 'oncall'}) as client:
        await client.post('/api/demo', json={})
        first = app.state.store.alerts(True)
        payloads = []
        def bark(request):
            import json
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={'code': 200})
        config.delivery_enabled = True
        config.bark_home_device_key = 'test-device'
        async with httpx.AsyncClient(transport=httpx.MockTransport(bark)) as bark_client:
            notifier = Notifier(config, client=bark_client)
            await notifier.send('home', first)
            await notifier.send('home', [], test=True)
        url = payloads[0]['url']
        assert url.startswith('https://ack.example.com/confirm#')
        assert payloads[1]['url'] == config.public_url
        token = url.split('#')[1]
        config.delivery_enabled = False
        await client.post('/api/demo', json={})
        before = app.state.store.alerts(True)
        assert len(before) == 2
        assert (await client.get('/confirm')).status_code == 200
        assert (await client.get('/confirm/client.js')).status_code == 200
        assert len(app.state.store.alerts(True)) == 2  # GET never acknowledges.
        assert (await client.post('/confirm', json={'token': token+'x'})).status_code == 403
        assert len(app.state.store.alerts(True)) == 2
        for _ in range(2):  # Safe retry; newer messages remain pending.
            assert (await client.post('/confirm', json={'token': token})).status_code == 200
            assert [a['id'] for a in app.state.store.alerts(True)] == [a['id'] for a in before if a['id'] != first[0]['id']]
