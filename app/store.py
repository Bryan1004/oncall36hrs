"""Single-process SQLite state. All writes run on the application's event loop."""
import json
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.create_function("has_review_text", 1, lambda text: int(bool((text or "").strip()) and (text or "").strip() != "[非文字消息]"), deterministic=True)
        self.db.executescript("""
          PRAGMA journal_mode=WAL;
          PRAGMA foreign_keys=ON;
          CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS groups (
            platform TEXT, id TEXT, title TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(platform,id));
          CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY, platform TEXT, chat_id TEXT, external_id TEXT,
            sender TEXT, text TEXT, created REAL, received REAL, reason TEXT,
            label TEXT, urgent INTEGER DEFAULT 0,
            UNIQUE(platform,chat_id,external_id));
          CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY, message_id INTEGER UNIQUE REFERENCES messages(id),
            status TEXT NOT NULL DEFAULT 'pending', created REAL, acknowledged REAL);
          CREATE TABLE IF NOT EXISTS deliveries (
            id INTEGER PRIMARY KEY, created REAL, channel TEXT, alert_ids TEXT,
            status TEXT, detail TEXT);
          CREATE TABLE IF NOT EXISTS connectors (
            name TEXT PRIMARY KEY, state TEXT, updated REAL, changed REAL);
          CREATE INDEX IF NOT EXISTS messages_chat ON messages(platform,chat_id,created);
        """)
        if 'from_me' not in {r[1] for r in self.db.execute('PRAGMA table_info(messages)')}:
            self.db.execute('ALTER TABLE messages ADD COLUMN from_me INTEGER')
        if 'available' not in {r[1] for r in self.db.execute('PRAGMA table_info(groups)')}:
            self.db.execute('ALTER TABLE groups ADD COLUMN available INTEGER NOT NULL DEFAULT 1')
        if 'mentions_only' not in {r[1] for r in self.db.execute('PRAGMA table_info(groups)')}:
            self.db.execute('ALTER TABLE groups ADD COLUMN mentions_only INTEGER NOT NULL DEFAULT 0')
        for key, value in {"mode": "paused", "call_interval": 120, "push_interval": 60,
                           "next_delivery": 0, "last_delivery": 0}.items():
            self.db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (key, json.dumps(value)))
        self.db.commit()

    def rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def get(self, key):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))
        self.db.commit()

    def groups(self, platform=None):
        return self.rows("SELECT * FROM groups WHERE available=1" + (" AND platform=?" if platform else "") +
                         " ORDER BY title", (platform,) if platform else ())

    def upsert_groups(self, platform, groups):
        self.db.executemany("""INSERT INTO groups(platform,id,title) VALUES (?,?,?)
          ON CONFLICT(platform,id) DO UPDATE SET title=excluded.title""",
                            [(platform, str(g["id"]), g["title"]) for g in groups])
        self.db.commit()

    def sync_groups(self, platform, groups, notify_all=False):
        before = {g['id'] for g in self.groups(platform)}
        incoming = {str(g['id']): g for g in groups}
        added = [{'id': k, 'title': g['title']} for k, g in incoming.items() if k not in before]
        with self.db:
            if before != incoming.keys():
                revision_key = 'group_revision_' + platform
                self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (revision_key, json.dumps((self.get(revision_key) or 0) + 1)))
            for chat in before - incoming.keys():
                self.db.execute('UPDATE groups SET available=0,enabled=0 WHERE platform=? AND id=?', (platform, chat))
                self.db.execute("UPDATE alerts SET status='cancelled' WHERE status='pending' AND message_id IN (SELECT id FROM messages WHERE platform=? AND chat_id=?)", (platform, chat))
            for chat, group in incoming.items():
                self.db.execute('INSERT INTO groups(platform,id,title,available) VALUES (?,?,?,1) ON CONFLICT(platform,id) DO UPDATE SET title=excluded.title,available=1', (platform, chat, group['title']))
            noticed = [{'id': k, 'title': g['title']} for k, g in incoming.items()] if notify_all else added
            if noticed:
                notices = self.get('group_sync_notices') or []
                sequence = (self.get('group_sync_sequence') or 0) + 1
                notices.append({'id': sequence, 'platform': platform, 'groups': noticed, 'kind': 'connection' if notify_all else 'new'})
                for key, value in [('group_sync_sequence', sequence), ('group_sync_notices', notices[-20:])]:
                    self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))
        return added

    def pending_group_notices(self):
        seen = self.get('group_sync_seen') or 0
        current = {(g['platform'], g['id']): g for g in self.groups()}
        notices = []
        for notice in self.get('group_sync_notices') or []:
            if notice['id'] <= seen:
                continue
            groups = [current[(notice['platform'], g['id'])] for g in notice['groups']
                      if (notice['platform'], g['id']) in current]
            if groups:
                notices.append(dict(notice, groups=groups))
        return notices

    def selected(self, platform, chat_id):
        row = self.db.execute("SELECT enabled FROM groups WHERE platform=? AND id=? AND available=1",
                              (platform, str(chat_id))).fetchone()
        return bool(row and row[0])

    def select(self, platform, chat_id, enabled):
        cur = self.db.execute("UPDATE groups SET enabled=? WHERE platform=? AND id=?",
                              (int(enabled), platform, chat_id))
        if not enabled:
            self.db.execute("""UPDATE alerts SET status='cancelled' WHERE status='pending'
                AND message_id IN (SELECT id FROM messages WHERE platform=? AND chat_id=?)""",
                            (platform, chat_id))
        self.db.commit()
        return cur.rowcount > 0

    def ingest(self, event, now=None):
        now = time.time() if now is None else now
        platform, chat = event["platform"], str(event["chat_id"])
        if not self.selected(platform, chat):
            return None
        group = self.db.execute("SELECT mentions_only FROM groups WHERE platform=? AND id=?", (platform, chat)).fetchone()
        if group['mentions_only'] and (not event.get('mentioned') or event.get('from_me')):
            return None
        reason = "mention" if event.get("mentioned") else "reply" if event.get("reply_to_me") else ""
        if event.get("from_me"):
            reason = ""
        # Backfilled or delayed messages remain reviewable, never trigger a stale call.
        created = event["timestamp"]
        live = not event.get("historical") and 0 <= now - created <= 300
        with self.db:
            cur = self.db.execute("""INSERT OR IGNORE INTO messages
              (platform,chat_id,external_id,sender,text,created,received,reason,from_me)
              VALUES (?,?,?,?,?,?,?,?,?)""", (platform, chat, event["message_id"],
                event.get("sender", ""), event.get("text", "")[:8000], created, now, reason, int(bool(event.get("from_me")))))
            if cur.rowcount == 0:
                return None
            mid = cur.lastrowid
            if reason and live:
                self.db.execute("INSERT INTO alerts(message_id,created) VALUES (?,?)", (mid, now))
        return mid

    def alerts(self, pending=False):
        return self.rows("""SELECT a.*,m.platform,m.chat_id,m.sender,m.text,m.reason,g.title
          FROM alerts a JOIN messages m ON m.id=a.message_id
          JOIN groups g ON g.platform=m.platform AND g.id=m.chat_id
        """ + (" WHERE a.status='pending'" if pending else " WHERE a.status='pending' OR a.id IN (SELECT id FROM alerts WHERE status!='pending' ORDER BY id DESC LIMIT 500)") + " ORDER BY a.id DESC")

    def acknowledge(self, ids):
        with self.db:
            self.db.executemany("UPDATE alerts SET status='acknowledged',acknowledged=? WHERE id=? AND status='pending'",
                                [(time.time(), i) for i in ids])

    def delivery(self, channel, ids, status, detail=""):
        self.db.execute("INSERT INTO deliveries(created,channel,alert_ids,status,detail) VALUES (?,?,?,?,?)",
                        (time.time(), channel, json.dumps(ids), status, detail[:300]))
        self.db.commit()

    def connector(self, name, state):
        now = time.time()
        self.db.execute("""INSERT INTO connectors VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET
          state=excluded.state, updated=excluded.updated,
          changed=CASE WHEN connectors.state=excluded.state THEN connectors.changed ELSE excluded.changed END""",
                        (name, state, now, now))
        self.db.commit()

    def prune(self, days):
        cutoff = time.time() - max(1, days) * 86400
        with self.db:
            self.db.execute("""DELETE FROM alerts WHERE status!='pending' AND message_id IN
              (SELECT id FROM messages WHERE created<? AND label IS NULL)""", (cutoff,))
            self.db.execute("""DELETE FROM messages WHERE created<? AND label IS NULL
                AND id NOT IN (SELECT message_id FROM alerts)""", (cutoff,))
            self.db.execute("DELETE FROM deliveries WHERE created<?", (cutoff,))
