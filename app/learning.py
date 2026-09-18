"""Immutable reviewed datasets. Publishing never invokes or trains a model."""
import json
import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal


class Draft(BaseModel):
    rules: str = Field(max_length=20000)


class Review(BaseModel):
    label: Literal['action','inform','irrelevant','unclear']
    urgent: bool = False
    service: str = Field(default='', max_length=200)
    rationale: str = Field(default='', max_length=2000)
    split: Literal['reference','evaluation'] | None = None


class Publish(BaseModel):
    note: str = Field(default='', max_length=1000)


def install_learning(app, store):
    store.db.executescript('''
      CREATE TABLE IF NOT EXISTS sample_reviews (
        message_id INTEGER PRIMARY KEY, service TEXT, rationale TEXT, split TEXT,
        context TEXT NOT NULL, updated REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS dataset_versions (
        id INTEGER PRIMARY KEY, created REAL NOT NULL, rules TEXT NOT NULL,
        note TEXT NOT NULL, samples TEXT NOT NULL);
    ''')
    store.db.execute('''CREATE TABLE IF NOT EXISTS sample_group_splits (
        platform TEXT NOT NULL, chat_id TEXT NOT NULL, split TEXT NOT NULL,
        PRIMARY KEY (platform, chat_id))''')
    # Preserve historical holdout groups without changing frozen snapshots.
    with store.db:
        for version in store.rows('SELECT samples FROM dataset_versions ORDER BY id'):
            for sample in json.loads(version['samples']):
                store.db.execute('''INSERT INTO sample_group_splits VALUES (?,?,?)
                    ON CONFLICT(platform,chat_id) DO UPDATE SET split=
                    CASE WHEN excluded.split='evaluation' THEN 'evaluation'
                         ELSE sample_group_splits.split END''',
                    (sample['platform'], sample['chat_id'], sample.get('split', 'reference')))
    router=APIRouter(prefix='/api/learning')

    def assign_splits(rows):
        assigned={(r['platform'],r['chat_id']):r['split']
                  for r in store.rows('SELECT * FROM sample_group_splits')}
        for row in rows:
            key=(row['platform'],row['chat_id'])
            if key not in assigned:
                evaluations=sum(value=='evaluation' for value in assigned.values())
                split='evaluation' if assigned and evaluations < (len(assigned)+5)//5 else 'reference'
                store.db.execute('INSERT INTO sample_group_splits VALUES (?,?,?)',(*key,split))
                assigned[key]=split
            row['split']=assigned[key]


    SAMPLE_FILTER = "m.platform IN ('whatsapp','telegram') AND COALESCE(m.from_me,0)=0 AND has_review_text(m.text)=1 AND COALESCE(m.reason,'') <> 'mention' AND NOT EXISTS (SELECT 1 FROM groups mg WHERE mg.platform=m.platform AND mg.id=m.chat_id AND mg.mentions_only=1)"

    def samples():
        rows=store.rows('''SELECT m.*,g.title,r.service,r.rationale,r.split,r.context
            FROM messages m JOIN groups g ON m.platform=g.platform AND m.chat_id=g.id
            LEFT JOIN sample_reviews r ON r.message_id=m.id
            WHERE '''+SAMPLE_FILTER+''' AND m.label IS NOT NULL ORDER BY m.id''')
        for row in rows:
            row['context']=json.loads(row['context']) if row['context'] else context(row)
            row['split']=row['split'] or 'reference'
            row['service']=row['service'] or ''
            row['rationale']=row['rationale'] or ''
        return rows

    def context(row):
        return store.rows('''SELECT id,sender,text,created FROM messages
            WHERE platform=? AND chat_id=? AND (created<? OR (created=? AND id<=?))
            ORDER BY created DESC,id DESC LIMIT 6''',
            (row['platform'],row['chat_id'],row['created'],row['created'],row['id']))[::-1]

    @router.get('')
    async def overview():
        versions=store.rows('SELECT * FROM dataset_versions ORDER BY id DESC')
        migration_map = store.get('mention_migration_map') or {}
        retired = store.get('mention_migration_retired') or []
        previous={}
        for version in reversed(versions):
            rows=json.loads(version.pop('samples'))
            current={r['id']:r for r in rows}
            version['count']=len(rows)
            version['evaluation_count']=sum(r['split']=='evaluation' for r in rows)
            version['added']=len(current.keys()-previous.keys())
            version['removed']=len(previous.keys()-current.keys())
            version['changed']=sum(current[k]!=previous[k] for k in current.keys()&previous.keys())
            previous=current
            vid=version['id']
            if str(vid) in migration_map:
                version['superseded_by']=migration_map[str(vid)]
            if vid in retired:
                version['retired']=True
        rows=samples()
        return {'rules':store.get('responsibility_rules') or '', 'versions':versions,
                'active':store.get('active_dataset'), 'reviewed':len(rows),
                'evaluation_count':sum(r['split']=='evaluation' for r in rows)}

    @router.get('/samples')
    async def list_samples(offset:int=0, platform:Literal['whatsapp','telegram']='whatsapp'):
        if offset<0: raise HTTPException(422)
        rows=store.rows('''SELECT m.*,g.title,r.service,r.rationale,r.split FROM messages m
          JOIN groups g ON m.platform=g.platform AND m.chat_id=g.id
          LEFT JOIN sample_reviews r ON r.message_id=m.id
          WHERE m.platform=? AND COALESCE(m.from_me,0)=0 AND has_review_text(m.text)=1 AND COALESCE(m.reason,'') <> 'mention' AND NOT EXISTS (SELECT 1 FROM groups mg WHERE mg.platform=m.platform AND mg.id=m.chat_id AND mg.mentions_only=1) ORDER BY m.id DESC LIMIT 50 OFFSET ?''',(platform,offset))
        counts={p:store.rows("SELECT count(*) AS n FROM messages WHERE platform=? AND COALESCE(from_me,0)=0 AND has_review_text(text)=1 AND COALESCE(reason,'') <> 'mention' AND NOT EXISTS (SELECT 1 FROM groups mg WHERE mg.platform=messages.platform AND mg.id=messages.chat_id AND mg.mentions_only=1)",(p,))[0]['n'] for p in ('whatsapp','telegram')}
        return {'items':rows,'total':counts[platform],'counts':counts}

    @router.post('/rules')
    async def rules(body:Draft):
        store.set('responsibility_rules',body.rules)
        return {'ok':True}

    @router.post('/samples/{mid}')
    async def review(mid:int, body:Review):
        rows=store.rows("SELECT * FROM messages WHERE id=? AND COALESCE(from_me,0)=0 AND has_review_text(text)=1 AND COALESCE(reason,'') <> 'mention' AND NOT EXISTS (SELECT 1 FROM groups mg WHERE mg.platform=messages.platform AND mg.id=messages.chat_id AND mg.mentions_only=1) AND platform IN ('whatsapp','telegram')",(mid,))
        if not rows: raise HTTPException(404)
        snapshot=context(rows[0])
        prior=store.rows('SELECT split FROM sample_reviews WHERE message_id=?',(mid,))
        split=body.split or (prior[0]['split'] if prior else 'reference')
        with store.db:
            store.db.execute('UPDATE messages SET label=?,urgent=? WHERE id=?',(body.label,body.urgent,mid))
            store.db.execute('INSERT OR REPLACE INTO sample_reviews VALUES (?,?,?,?,?,?)',
                (mid,body.service,body.rationale,split,json.dumps(snapshot,ensure_ascii=False),time.time()))
        return {'ok':True}

    @router.post('/samples/{mid}/own')
    async def mark_own(mid:int):
        with store.db:
            cur=store.db.execute("UPDATE messages SET from_me=1,label=NULL,urgent=0 WHERE id=? AND platform IN ('whatsapp','telegram')",(mid,))
            if not cur.rowcount:raise HTTPException(404)
            store.db.execute('DELETE FROM sample_reviews WHERE message_id=?',(mid,))
            store.db.execute("UPDATE alerts SET status='cancelled' WHERE message_id=? AND status='pending'",(mid,))
        return {'ok':True}

    @router.post('/samples/{mid}/unreview')
    async def unreview(mid:int):
        with store.db:
            cur=store.db.execute('UPDATE messages SET label=NULL,urgent=0 WHERE id=?',(mid,))
            store.db.execute('DELETE FROM sample_reviews WHERE message_id=?',(mid,))
        if not cur.rowcount:raise HTTPException(404)
        return {'ok':True}

    @router.post('/publish')
    async def publish(body:Publish):
        rows=samples()
        if not rows: raise HTTPException(409,'请先审核至少一条样本')
        rules=store.get('responsibility_rules') or ''
        if not rules.strip(): raise HTTPException(409,'请先保存职责说明')
        # Freeze contexts and labels: subsequent editing/pruning cannot mutate a version.
        with store.db:
            assign_splits(rows)
            cur=store.db.execute('INSERT INTO dataset_versions(created,rules,note,samples) VALUES (?,?,?,?)',
                (time.time(),rules,body.note,json.dumps(rows,ensure_ascii=False)))
        return {'id':cur.lastrowid}

    @router.get('/versions/{vid}')
    async def version(vid:int):
        rows=store.rows('SELECT * FROM dataset_versions WHERE id=?',(vid,))
        if not rows: raise HTTPException(404)
        row=rows[0];row['samples']=json.loads(row['samples']);return row

    @router.post('/versions/{vid}/activate')
    async def activate(vid:int):
        v = await version(vid)
        migration_map = store.get('mention_migration_map') or {}
        retired = store.get('mention_migration_retired') or []
        if str(vid) in migration_map or vid in retired:
            raise HTTPException(409, '该版本已被清理替代或已停用，请选择其他版本')
        store.set('active_dataset',vid)
        return {'ok':True}

    app.include_router(router)
