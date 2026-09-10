from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from app.quiet_hours import QuietHours, quiet_active
from app.store import Store
from app.notify import Dispatcher
from types import SimpleNamespace


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('Asia/Kuala_Lumpur')).timestamp()


def test_overnight_boundaries_and_selected_start_day(tmp_path):
    store=Store(str(tmp_path/'test.db'))
    store.set('quiet_hours',QuietHours(enabled=True,days=[4]).model_dump())
    for date, expected in [('2026-09-11T17:59',False),('2026-09-11T18:00',True),('2026-09-12T08:59',True),('2026-09-12T09:00',False),('2026-09-12T18:00',False)]:
        assert quiet_active(store,stamp(date)) is expected
    store.set('quiet_hours',QuietHours(enabled=True,start='09:00',end='18:00',days=[4]).model_dump())
    assert quiet_active(store,stamp('2026-09-11T09:00'))
    assert not quiet_active(store,stamp('2026-09-11T18:00'))
    store.set('quiet_hours',QuietHours().model_dump())
    assert not quiet_active(store,stamp('2026-09-11T20:00'))


def test_validation():
    for args in [dict(start='25:00'),dict(days=[7]),dict(timezone='invalid'),dict(enabled=True,days=[]),dict(enabled=True,start='09:00',end='09:00')]:
        with pytest.raises(ValueError): QuietHours(**args)


@pytest.mark.asyncio
async def test_blocks_both_modes_without_consuming_pending(tmp_path):
    store=Store(str(tmp_path/'test.db'))
    store.set('quiet_hours',QuietHours(enabled=True).model_dump())
    store.alerts=lambda pending: [{'id':1,'platform':'whatsapp'}]
    calls=[]
    async def send(mode,alerts,test=False):
        calls.append((mode,test));return 'accepted'
    notifier=SimpleNamespace(config=SimpleNamespace(delivery_enabled=True),send=send)
    dispatcher=Dispatcher(store,notifier)
    for mode in ['home','away']:
        store.set('mode',mode);store.set('next_delivery',0)
        await dispatcher.tick(stamp('2026-09-11T20:00'))
        assert store.get('next_delivery')==0
    assert calls==[]
    await dispatcher.tick(stamp('2026-09-12T09:00'))
    assert calls==[('away',False)]
    await dispatcher.test('home')
    assert calls[-1]==('home',True)
