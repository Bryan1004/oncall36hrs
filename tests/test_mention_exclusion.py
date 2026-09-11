"""Regression tests for mention sample exclusion (plan section 7)."""
import json
import time
import sqlite3
import httpx
import pytest
from app.config import Config
from app.main import create_app
from app.store import Store


def make_app(tmp_path):
    return create_app(Config(
        data_dir=str(tmp_path),
        bridge_token='mention-exclusion-test-token',
        delivery_enabled=False,
        telegram_api_id='', telegram_api_hash='',
    ), workers=False)


def client_args(app):
    return dict(
        transport=httpx.ASGITransport(app=app),
        base_url='http://test',
        headers={'X-Requested-With': 'oncall'},
    )


@pytest.mark.asyncio
async def test_new_mention_message_ingested_with_alert_but_excluded_from_samples(tmp_path):
    """New mention → message stored, real-time alert created, sample list excludes it."""
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app), httpx.AsyncClient(**client_args(app)) as c:
        s = app.state.store
        s.upsert_groups('whatsapp', [{'id': '1', 'title': 'ops'}])
        s.select('whatsapp', '1', True)

        mid = s.ingest(dict(
            platform='whatsapp', chat_id='1', message_id='m1',
            timestamp=time.time(), text='@你 看一下', sender='同事',
            mentioned=True,
        ))
        assert mid is not None
        assert len(s.alerts(True)) == 1  # Alert created

        result = (await c.get('/api/learning/samples?platform=whatsapp')).json()
        assert result['total'] == 0
        assert result['counts']['whatsapp'] == 0
        assert result['items'] == []


@pytest.mark.asyncio
async def test_mention_plus_reply_excludes_by_mention(tmp_path):
    """If both mentioned and reply_to_me, reason='mention' → excluded from samples, alert normal."""
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app), httpx.AsyncClient(**client_args(app)) as c:
        s = app.state.store
        s.upsert_groups('telegram', [{'id': '1', 'title': 'ops'}])
        s.select('telegram', '1', True)

        mid = s.ingest(dict(
            platform='telegram', chat_id='1', message_id='m1',
            timestamp=time.time(), text='回复+@', sender='同事',
            mentioned=True, reply_to_me=True,
        ))
        assert mid is not None
        msg = s.rows('SELECT reason FROM messages WHERE id=?', (mid,))[0]
        assert msg['reason'] == 'mention'
        assert len(s.alerts(True)) == 1

        result = (await c.get('/api/learning/samples?platform=telegram')).json()
        assert result['total'] == 0


@pytest.mark.asyncio
async def test_reply_only_remains_in_samples(tmp_path):
    """Reply without mention → reason='reply' → included in samples."""
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app), httpx.AsyncClient(**client_args(app)) as c:
        s = app.state.store
        s.upsert_groups('whatsapp', [{'id': '1', 'title': 'ops'}])
        s.select('whatsapp', '1', True)

        mid = s.ingest(dict(
            platform='whatsapp', chat_id='1', message_id='r1',
            timestamp=time.time(), text='回复你', sender='同事',
            reply_to_me=True,
        ))
        result = (await c.get('/api/learning/samples?platform=whatsapp')).json()
        assert result['total'] == 1
        assert result['items'][0]['id'] == mid


@pytest.mark.asyncio
async def test_email_and_at_others_not_excluded(tmp_path):
    """Messages with email addresses or @others but mentioned=false → not excluded."""
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app), httpx.AsyncClient(**client_args(app)) as c:
        s = app.state.store
        s.upsert_groups('telegram', [{'id': '1', 'title': 'ops'}])
        s.select('telegram', '1', True)

        for i, text in enumerate([
            'admin@company.com 请查看',
            '@张三 帮忙处理一下',
            '发到 test@test.com 邮箱',
        ]):
            s.ingest(dict(
                platform='telegram', chat_id='1', message_id=f'e{i}',
                timestamp=time.time(), text=text, sender='同事',
            ))

        result = (await c.get('/api/learning/samples?platform=telegram')).json()
        assert result['total'] == 3


@pytest.mark.asyncio
async def test_normal_work_message_in_samples(tmp_path):
    """Normal text messages remain in sample list."""
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app), httpx.AsyncClient(**client_args(app)) as c:
        s = app.state.store
        s.upsert_groups('whatsapp', [{'id': '1', 'title': 'ops'}])
        s.select('whatsapp', '1', True)

        mid = s.ingest(dict(
            platform='whatsapp', chat_id='1', message_id='w1',
            timestamp=time.time(), text='数据库延迟高', sender='同事',
        ))
        result = (await c.get('/api/learning/samples?platform=whatsapp')).json()
        assert result['total'] == 1


@pytest.mark.asyncio
async def test_own_nontext_empty_still_excluded(tmp_path):
    """Original exclusion rules (from_me, non-text, empty) still work alongside mention."""
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app), httpx.AsyncClient(**client_args(app)) as c:
        s = app.state.store
        s.upsert_groups('telegram', [{'id': '1', 'title': 'ops'}])
        s.select('telegram', '1', True)

        s.ingest(dict(platform='telegram', chat_id='1', message_id='own',
                      timestamp=time.time(), text='my msg', from_me=True))
        s.ingest(dict(platform='telegram', chat_id='1', message_id='nontext',
                      timestamp=time.time(), text='[非文字消息]'))
        s.ingest(dict(platform='telegram', chat_id='1', message_id='empty',
                      timestamp=time.time(), text=''))

        result = (await c.get('/api/learning/samples?platform=telegram')).json()
        assert result['total'] == 0


@pytest.mark.asyncio
async def test_null_reason_old_messages_preserved(tmp_path):
    """Old messages with NULL or empty reason are NOT excluded."""
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app), httpx.AsyncClient(**client_args(app)) as c:
        s = app.state.store
        s.upsert_groups('whatsapp', [{'id': '1', 'title': 'ops'}])
        s.select('whatsapp', '1', True)

        mid = s.ingest(dict(
            platform='whatsapp', chat_id='1', message_id='old1',
            timestamp=time.time(), text='旧消息', sender='同事',
        ))
        # Simulate old data with NULL reason
        s.db.execute('UPDATE messages SET reason=NULL WHERE id=?', (mid,))
        s.db.commit()

        result = (await c.get('/api/learning/samples?platform=whatsapp')).json()
        assert result['total'] == 1


@pytest.mark.asyncio
async def test_historical_mention_backfill_no_sample_no_stale_alert(tmp_path):
    """Historical mention → enters DB, no alert, not in samples."""
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app), httpx.AsyncClient(**client_args(app)) as c:
        s = app.state.store
        s.upsert_groups('telegram', [{'id': '1', 'title': 'ops'}])
        s.select('telegram', '1', True)

        mid = s.ingest(dict(
            platform='telegram', chat_id='1', message_id='hist',
            timestamp=time.time() - 600, text='旧@消息',
            sender='同事', mentioned=True, historical=True,
        ))
        assert mid is not None
        assert len(s.alerts(True)) == 0

        result = (await c.get('/api/learning/samples?platform=telegram')).json()
        assert result['total'] == 0


@pytest.mark.asyncio
async def test_ws_tg_pagination_counts_consistent(tmp_path):
    """WS/TG pagination items, total, counts all consistent."""
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app), httpx.AsyncClient(**client_args(app)) as c:
        s = app.state.store
        for p in ('whatsapp', 'telegram'):
            s.upsert_groups(p, [{'id': '1', 'title': 'ops'}])
            s.select(p, '1', True)

        # Add 3 normal + 2 mention per platform
        for p in ('whatsapp', 'telegram'):
            for i in range(3):
                s.ingest(dict(platform=p, chat_id='1', message_id=f'n{p}{i}',
                              timestamp=time.time(), text=f'normal {i}', sender='ops'))
            for i in range(2):
                s.ingest(dict(platform=p, chat_id='1', message_id=f'm{p}{i}',
                              timestamp=time.time(), text=f'@mention {i}',
                              sender='ops', mentioned=True))

        for p in ('whatsapp', 'telegram'):
            r = (await c.get(f'/api/learning/samples?platform={p}')).json()
            assert r['total'] == 3, f'{p} total should be 3'
            assert len(r['items']) == 3
            assert r['counts'][p] == 3


@pytest.mark.asyncio
async def test_direct_post_to_excluded_mention_rejected(tmp_path):
    """POST review to mention message → 404, label not written."""
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app), httpx.AsyncClient(**client_args(app)) as c:
        s = app.state.store
        s.upsert_groups('whatsapp', [{'id': '1', 'title': 'ops'}])
        s.select('whatsapp', '1', True)

        mid = s.ingest(dict(
            platform='whatsapp', chat_id='1', message_id='m1',
            timestamp=time.time(), text='@你', sender='同事',
            mentioned=True,
        ))

        # New learning endpoint
        r = await c.post(f'/api/learning/samples/{mid}', json={'label': 'action'})
        assert r.status_code == 404

        # Legacy label endpoint
        r = await c.post(f'/api/samples/{mid}/label', json={'label': 'action'})
        assert r.status_code == 404

        # Verify label was not written
        msg = s.rows('SELECT label FROM messages WHERE id=?', (mid,))[0]
        assert msg['label'] is None


@pytest.mark.asyncio
async def test_old_reviewed_mention_cleaned_by_migration(tmp_path):
    """Migration removes reviews and labels from mention messages, preserves message and alerts."""
    from scripts.migrate_mention_exclusion import run

    app = make_app(tmp_path)
    async with app.router.lifespan_context(app), httpx.AsyncClient(**client_args(app)) as c:
        s = app.state.store
        s.upsert_groups('whatsapp', [{'id': '1', 'title': 'ops'}])
        s.select('whatsapp', '1', True)

        # Create a mention message with review
        mid = s.ingest(dict(
            platform='whatsapp', chat_id='1', message_id='m1',
            timestamp=time.time(), text='@你 看看', sender='同事',
            mentioned=True,
        ))
        # Manually add review (as if done before the rule change)
        s.db.execute('UPDATE messages SET label=?, urgent=1 WHERE id=?', ('action', mid))
        s.db.execute('INSERT INTO sample_reviews VALUES (?,?,?,?,?,?)',
                     (mid, 'db', 'owner', 'reference', '[]', time.time()))
        s.db.commit()

        # Also create a normal message with review
        normal_mid = s.ingest(dict(
            platform='whatsapp', chat_id='1', message_id='n1',
            timestamp=time.time(), text='正常消息', sender='同事',
        ))
        s.db.execute('UPDATE messages SET label=? WHERE id=?', ('inform', normal_mid))
        s.db.execute('INSERT INTO sample_reviews VALUES (?,?,?,?,?,?)',
                     (normal_mid, '', '', 'reference', '[]', time.time()))
        s.db.commit()

        alert_count_before = len(s.alerts())
        msg_count_before = len(s.rows('SELECT * FROM messages'))

    # Run migration
    db_path = str(tmp_path / 'alerts.sqlite3')
    assert run(db_path, execute=True)

    # Verify
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    # Mention message: label cleared, review deleted, message preserved
    msg = dict(db.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone())
    assert msg['label'] is None
    assert msg['urgent'] == 0
    assert msg['text'] == '@你 看看'  # Original text preserved
    assert msg['reason'] == 'mention'  # Reason preserved

    reviews = db.execute('SELECT * FROM sample_reviews WHERE message_id=?', (mid,)).fetchall()
    assert len(reviews) == 0

    # Normal message: review preserved
    normal = dict(db.execute('SELECT * FROM messages WHERE id=?', (normal_mid,)).fetchone())
    assert normal['label'] == 'inform'
    normal_reviews = db.execute('SELECT * FROM sample_reviews WHERE message_id=?',
                                (normal_mid,)).fetchall()
    assert len(normal_reviews) == 1

    # Message count and alerts preserved
    assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == msg_count_before
    assert db.execute('SELECT count(*) FROM alerts').fetchone()[0] == alert_count_before

    db.close()


@pytest.mark.asyncio
async def test_publish_export_overview_exclude_mention(tmp_path):
    """publish, export, overview all exclude mention samples."""
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app), httpx.AsyncClient(**client_args(app)) as c:
        s = app.state.store
        s.upsert_groups('telegram', [{'id': '1', 'title': 'ops'}])
        s.select('telegram', '1', True)

        # Normal message: review it
        normal = s.ingest(dict(
            platform='telegram', chat_id='1', message_id='n1',
            timestamp=time.time(), text='正常', sender='同事',
        ))
        await c.post(f'/api/learning/samples/{normal}',
                     json={'label': 'action', 'rationale': 'test'})

        # Mention message: forcefully label it (simulating pre-migration)
        mention = s.ingest(dict(
            platform='telegram', chat_id='1', message_id='m1',
            timestamp=time.time(), text='@你', sender='同事',
            mentioned=True,
        ))
        s.db.execute('UPDATE messages SET label=? WHERE id=?', ('action', mention))
        s.db.commit()

        # Overview should only count the normal one
        overview = (await c.get('/api/learning')).json()
        assert overview['reviewed'] == 1

        # Export should only include the normal one
        export_lines = [json.loads(line) for line in (await c.get('/api/export')).text.strip().split('\n') if line.strip()]
        export_ids = [r['id'] for r in export_lines]
        assert normal in export_ids
        assert mention not in export_ids

        # Publish should only freeze the normal one
        await c.post('/api/learning/rules', json={'rules': 'test'})
        pub = (await c.post('/api/learning/publish', json={})).json()
        snap = (await c.get(f'/api/learning/versions/{pub["id"]}')).json()
        assert len(snap['samples']) == 1
        assert snap['samples'][0]['id'] == normal


@pytest.mark.asyncio
async def test_other_sample_context_retains_mention_messages(tmp_path):
    """Context of non-mention samples may include mention messages."""
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app), httpx.AsyncClient(**client_args(app)) as c:
        s = app.state.store
        s.upsert_groups('telegram', [{'id': '1', 'title': 'ops'}])
        s.select('telegram', '1', True)

        now = time.time()
        mention_mid = s.ingest(dict(
            platform='telegram', chat_id='1', message_id='m1',
            timestamp=now - 10, text='@你 看看', sender='同事',
            mentioned=True,
        ))
        normal_mid = s.ingest(dict(
            platform='telegram', chat_id='1', message_id='n1',
            timestamp=now, text='数据库故障', sender='同事',
        ))

        context = (await c.get(f'/api/samples/{normal_mid}/context')).json()
        context_ids = [r['id'] for r in context]
        assert mention_mid in context_ids


@pytest.mark.asyncio
async def test_contaminated_version_creates_replacement_and_blocks_old(tmp_path):
    """Migration creates replacement version for contaminated ones, old one blocked from AI."""
    from scripts.migrate_mention_exclusion import run

    app = make_app(tmp_path)
    async with app.router.lifespan_context(app), httpx.AsyncClient(**client_args(app)) as c:
        s = app.state.store
        s.upsert_groups('telegram', [{'id': '1', 'title': 'ops'}])
        s.select('telegram', '1', True)

        # Create 2 messages: 1 normal, 1 mention
        now = time.time()
        normal = s.ingest(dict(
            platform='telegram', chat_id='1', message_id='n1',
            timestamp=now, text='正常', sender='同事',
        ))
        mention = s.ingest(dict(
            platform='telegram', chat_id='1', message_id='m1',
            timestamp=now, text='@你', sender='同事', mentioned=True,
        ))

        # Review both and publish
        for mid in (normal, mention):
            s.db.execute('UPDATE messages SET label=? WHERE id=?', ('action', mid))
            s.db.execute('INSERT INTO sample_reviews VALUES (?,?,?,?,?,?)',
                         (mid, '', '', 'reference', '[]', time.time()))
        s.db.commit()

        await c.post('/api/learning/rules', json={'rules': 'test'})
        # Manually publish with both (simulating pre-exclusion behavior)
        samples_data = []
        for mid in (normal, mention):
            msg = s.rows('SELECT * FROM messages WHERE id=?', (mid,))[0]
            samples_data.append({
                'id': mid,
                'platform': msg['platform'],
                'chat_id': msg['chat_id'],
                'external_id': msg['external_id'],
                'text': msg['text'],
                'reason': msg['reason'],
                'label': msg['label'],
                'urgent': msg['urgent'],
                'split': 'reference',
                'title': 'ops',
                'context': [],
                'service': '',
                'rationale': '',
            })
        s.db.execute(
            'INSERT INTO dataset_versions(created, rules, note, samples) VALUES (?,?,?,?)',
            (time.time(), 'test', 'original', json.dumps(samples_data)))
        s.db.commit()
        old_vid = s.rows('SELECT max(id) as id FROM dataset_versions')[0]['id']
        s.set('active_dataset', old_vid)

    # Run migration
    db_path = str(tmp_path / 'alerts.sqlite3')
    assert run(db_path, execute=True)

    # Verify with fresh app
    app2 = make_app(tmp_path)
    async with app2.router.lifespan_context(app2), httpx.AsyncClient(**client_args(app2)) as c:
        overview = (await c.get('/api/learning')).json()

        # Old version should be superseded
        old_v = next(v for v in overview['versions'] if v['id'] == old_vid)
        assert 'superseded_by' in old_v

        new_vid = old_v['superseded_by']

        # Active should point to new version
        assert overview['active'] == new_vid

        # Old version should not be activatable
        r = await c.post(f'/api/learning/versions/{old_vid}/activate', json={})
        assert r.status_code == 409

        # New version should be activatable
        r = await c.post(f'/api/learning/versions/{new_vid}/activate', json={})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_version_cleaned_to_empty_retires_without_replacement(tmp_path):
    """Version where all samples are mention → retired, no empty replacement."""
    from scripts.migrate_mention_exclusion import run

    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        s = app.state.store
        s.upsert_groups('telegram', [{'id': '1', 'title': 'ops'}])
        s.select('telegram', '1', True)

        mid = s.ingest(dict(
            platform='telegram', chat_id='1', message_id='m1',
            timestamp=time.time(), text='@你', sender='同事', mentioned=True,
        ))
        s.db.execute('UPDATE messages SET label=? WHERE id=?', ('action', mid))
        s.db.execute('INSERT INTO sample_reviews VALUES (?,?,?,?,?,?)',
                     (mid, '', '', 'reference', '[]', time.time()))
        s.db.commit()

        samples_data = [{
            'id': mid, 'platform': 'telegram', 'chat_id': '1',
            'text': '@你', 'reason': 'mention', 'label': 'action',
            'urgent': 0, 'split': 'reference', 'title': 'ops',
            'context': [], 'service': '', 'rationale': '',
        }]
        s.db.execute(
            'INSERT INTO dataset_versions(created, rules, note, samples) VALUES (?,?,?,?)',
            (time.time(), 'test', '', json.dumps(samples_data)))
        s.db.commit()
        vid = s.rows('SELECT max(id) as id FROM dataset_versions')[0]['id']
        s.set('active_dataset', vid)

    db_path = str(tmp_path / 'alerts.sqlite3')
    assert run(db_path, execute=True)

    app2 = make_app(tmp_path)
    async with app2.router.lifespan_context(app2), httpx.AsyncClient(**client_args(app2)) as c:
        overview = (await c.get('/api/learning')).json()
        old_v = next(v for v in overview['versions'] if v['id'] == vid)
        assert old_v.get('retired') is True
        assert overview['active'] is None  # Cleared, not random

        # Count of versions should not include an empty replacement
        version_count_after = len(overview['versions'])
        assert version_count_after == 1  # Only the original retired one


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path):
    """Running migration twice: no duplicate versions, no extra side effects."""
    from scripts.migrate_mention_exclusion import run

    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        s = app.state.store
        s.upsert_groups('telegram', [{'id': '1', 'title': 'ops'}])
        s.select('telegram', '1', True)

        normal = s.ingest(dict(
            platform='telegram', chat_id='1', message_id='n1',
            timestamp=time.time(), text='正常', sender='同事',
        ))
        mention = s.ingest(dict(
            platform='telegram', chat_id='1', message_id='m1',
            timestamp=time.time(), text='@你', sender='同事', mentioned=True,
        ))

        for mid in (normal, mention):
            s.db.execute('UPDATE messages SET label=? WHERE id=?', ('action', mid))
            s.db.execute('INSERT INTO sample_reviews VALUES (?,?,?,?,?,?)',
                         (mid, '', '', 'reference', '[]', time.time()))
        s.db.commit()

        samples_data = []
        for mid in (normal, mention):
            msg = s.rows('SELECT * FROM messages WHERE id=?', (mid,))[0]
            samples_data.append({
                'id': mid, 'platform': msg['platform'], 'chat_id': msg['chat_id'],
                'text': msg['text'], 'reason': msg['reason'],
                'label': msg['label'], 'urgent': msg['urgent'],
                'split': 'reference', 'title': 'ops', 'context': [],
                'service': '', 'rationale': '',
            })
        s.db.execute(
            'INSERT INTO dataset_versions(created, rules, note, samples) VALUES (?,?,?,?)',
            (time.time(), 'test', '', json.dumps(samples_data)))
        s.db.commit()

    db_path = str(tmp_path / 'alerts.sqlite3')

    # First run
    assert run(db_path, execute=True)
    db = sqlite3.connect(db_path)
    version_count_1 = db.execute('SELECT count(*) FROM dataset_versions').fetchone()[0]
    db.close()

    # Second run
    assert run(db_path, execute=True)
    db = sqlite3.connect(db_path)
    version_count_2 = db.execute('SELECT count(*) FROM dataset_versions').fetchone()[0]
    db.close()

    assert version_count_1 == version_count_2, 'Idempotent run should not create more versions'


@pytest.mark.asyncio
async def test_legacy_samples_and_export_exclude_mention(tmp_path):
    """Legacy /api/samples and /api/export exclude mention messages."""
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app), httpx.AsyncClient(**client_args(app)) as c:
        s = app.state.store
        s.upsert_groups('whatsapp', [{'id': '1', 'title': 'ops'}])
        s.select('whatsapp', '1', True)

        normal = s.ingest(dict(
            platform='whatsapp', chat_id='1', message_id='n1',
            timestamp=time.time(), text='正常消息', sender='同事',
        ))
        mention = s.ingest(dict(
            platform='whatsapp', chat_id='1', message_id='m1',
            timestamp=time.time(), text='@你', sender='同事', mentioned=True,
        ))

        # Manually label both (simulating pre-migration)
        for mid in (normal, mention):
            s.db.execute('UPDATE messages SET label=? WHERE id=?', ('action', mid))
        s.db.commit()

        # Legacy samples endpoint
        legacy = (await c.get('/api/samples')).json()
        legacy_ids = [r['id'] for r in legacy]
        assert normal in legacy_ids
        assert mention not in legacy_ids

        # Export endpoint
        export_lines = [json.loads(line) for line in (await c.get('/api/export')).text.strip().split('\n') if line.strip()]
        export_ids = [r['id'] for r in export_lines]
        assert normal in export_ids
        assert mention not in export_ids


@pytest.mark.asyncio
async def test_service_restart_preserves_migration_and_api_counts(tmp_path):
    """After service restart, migration markers persist and API counts are consistent."""
    from scripts.migrate_mention_exclusion import run

    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        s = app.state.store
        s.upsert_groups('telegram', [{'id': '1', 'title': 'ops'}])
        s.select('telegram', '1', True)
        s.ingest(dict(
            platform='telegram', chat_id='1', message_id='m1',
            timestamp=time.time(), text='@你', sender='同事', mentioned=True,
        ))
        s.ingest(dict(
            platform='telegram', chat_id='1', message_id='n1',
            timestamp=time.time(), text='正常', sender='同事',
        ))

    db_path = str(tmp_path / 'alerts.sqlite3')
    run(db_path, execute=True)

    # "Restart" — create fresh app instance
    app2 = make_app(tmp_path)
    async with app2.router.lifespan_context(app2), httpx.AsyncClient(**client_args(app2)) as c:
        result = (await c.get('/api/learning/samples?platform=telegram')).json()
        assert result['total'] == 1
        assert result['counts']['telegram'] == 1
