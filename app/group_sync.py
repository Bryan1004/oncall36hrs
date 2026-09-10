"""Synchronize WhatsApp groups after connection, with retries and shared request locking."""
import asyncio
import time


class WhatsAppGroupSync:
    def __init__(self, store, fetch):
        self.store, self.fetch = store, fetch
        self.lock = asyncio.Lock()
        self.connection = None
        self.next_attempt = 0
        self.baseline = store.get("wa_groups_synced_connection")
        if self.baseline is None:
            self.baseline = self.connected(time.time())

    def connected(self, now):
        rows = self.store.rows("SELECT * FROM connectors WHERE name='whatsapp'")
        if not rows or rows[0]['state'] != 'connected' or now - rows[0]['updated'] > 45 or self.store.get('whatsapp_disconnected'):
            return None
        return rows[0]['changed']

    async def sync(self, force=False, now=None):
        now = time.time() if now is None else now
        async with self.lock:
            connection = self.connected(now)
            if connection is None:
                self.connection = None
                return False
            if not force and connection == self.connection and now < self.next_attempt:
                return False
            self.connection = connection
            self.next_attempt = now + 15
            groups = await self.fetch()
            if self.connected(time.time()) != connection:
                return False
            self.store.sync_groups('whatsapp', groups, notify_all=connection != self.baseline)
            self.baseline = connection
            self.store.set("wa_groups_synced_connection", connection)
            self.next_attempt = time.time() + 300
            return True
