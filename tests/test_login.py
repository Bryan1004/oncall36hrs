from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import time
import pytest
import httpx
from telethon import errors
from app.config import Config
from app.main import create_app
from app.store import Store
from app.telegram import TelegramMonitor, LoginError

@pytest.fixture
def monitor(tmp_path):
    store=Store(str(tmp_path/'db.sqlite'))
    m=TelegramMonitor(Config(data_dir=str(tmp_path),telegram_api_id='123',telegram_api_hash='test'),store)
    async def dialogs():
        yield SimpleNamespace(id=-1001,name='Test',is_group=True)
    m.client=SimpleNamespace(is_connected=lambda:True,is_user_authorized=AsyncMock(return_value=False),
        send_code_request=AsyncMock(return_value=SimpleNamespace(phone_code_hash='private-hash')),
        sign_in=AsyncMock(),get_me=AsyncMock(return_value=SimpleNamespace(id=42)),
        add_event_handler=Mock(),iter_dialogs=dialogs)
    yield m
    store.db.close()

@pytest.mark.asyncio
async def test_panel_login_2fa_and_groups_default_off(monitor):
    m=monitor
    s=await m.send_code('+60 123456789')
    assert s['step']=='code' and 'private-hash' not in str(s) and '+60123456789' not in str(s)
    with pytest.raises(LoginError) as e: await m.send_code('+60123456789')
    assert e.value.status==429
    m.client.sign_in.side_effect=[errors.SessionPasswordNeededError(None),None]
    assert (await m.verify('12345'))['step']=='password'
    assert (await m.verify('secret',password=True))['step']=='connected'
    assert not m.store.selected('telegram','-1001')
    assert m.phone_hash is None
    assert m.client.add_event_handler.call_count==1

@pytest.mark.asyncio
async def test_invalid_expired_and_flood(monitor):
    m=monitor
    await m.send_code('+60123456789')
    m.client.sign_in.side_effect=errors.PhoneCodeInvalidError(None)
    with pytest.raises(LoginError,match='不正确'): await m.verify('12345')
    assert m.step=='code'
    m.verify_at=0
    m.client.sign_in.side_effect=errors.FloodWaitError(None,90)
    with pytest.raises(LoginError) as e: await m.verify('12345')
    assert e.value.status==429 and m.login_status()['retry_after']>=89
    m.expires=time.time()-1
    assert m.login_status()['step']=='phone'
    with pytest.raises(LoginError,match='失效'): await m.verify('12345')

@pytest.mark.asyncio
async def test_login_api_auth_and_no_secret_reflection(tmp_path):
    c=Config(data_dir=str(tmp_path),admin_password='test-password-long-enough',bridge_token='bridge-token-long-enough-for-tests',telegram_api_id='',telegram_api_hash='',delivery_enabled=False)
    app=create_app(c,workers=False)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/api/telegram/login')).status_code==200
        assert (await client.post('/api/telegram/login/send-code',json={'phone':'+60123456789'})).status_code==403
        client.headers['X-Requested-With']='oncall'
        assert not (await client.get('/api/telegram/login')).json()['configured']
        r=await client.post('/api/telegram/login/code',json={'code':'secret-value'})
        assert r.status_code==422 and 'secret-value' not in r.text
        assert (await client.post('/api/telegram/login/send-code',json={'phone':'+60123456789'})).status_code==409

@pytest.mark.asyncio
async def test_manual_disconnect_clears_session_and_requires_login(monitor):
    m=monitor
    m.client.disconnect=AsyncMock()
    m.me=SimpleNamespace(id=42)
    m.client.is_user_authorized.return_value=True
    m.client.log_out=AsyncMock(return_value=True)
    original=m.client
    from pathlib import Path
    session=Path(m.config.data_dir)/'telegram.session'
    session.write_text('old-session')
    await m.set_disconnected(True)
    assert m.store.get('telegram_disconnected') is True
    assert m.login_status()['step']=='disconnected'
    original.disconnect.assert_awaited_once()
    original.log_out.assert_awaited_once()
    assert m.client is None and m.me is None and not session.exists()
    with pytest.raises(LoginError,match='重新连接'): await m.ensure_client()
    await m.set_disconnected(False)
    assert m.me is None and m.store.get('telegram_disconnected') is False
    assert m.login_status()['step']=='phone'


@pytest.mark.asyncio
async def test_logout_network_failure_still_removes_local_session(monitor):
    from pathlib import Path
    m=monitor
    m.client.disconnect=AsyncMock()
    m.client.is_user_authorized.side_effect=OSError('offline')
    session=Path(m.config.data_dir)/'telegram.session'
    session.write_text('old-session')
    result=await m.set_disconnected(True)
    assert result['remote_logout'] is False
    assert not session.exists() and m.client is None
    await m.set_disconnected(False)
    assert m.login_status()['step']=='phone'
