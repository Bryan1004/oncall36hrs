import http from 'node:http';
import {mkdir, readdir, readFile, writeFile, rename, unlink, rm} from 'node:fs/promises';
import {createHash, randomUUID, timingSafeEqual} from 'node:crypto';
import path from 'node:path';
import makeWASocket, {useMultiFileAuthState, normalizeMessageContent, jidNormalizedUser, fetchLatestBaileysVersion} from '@whiskeysockets/baileys';
import pino from 'pino';
import {normalize} from './normalize.mjs';
import {closePolicy, restartAfterAuth} from './connection-policy.mjs';

process.umask(0o077);
const dir = process.env.DATA_DIR || './data';
const token = process.env.BRIDGE_TOKEN || '';
if (token.length < 24) throw new Error('BRIDGE_TOKEN must have at least 24 characters');
const appUrl = process.env.APP_URL || 'http://localhost:8000';
await mkdir(path.join(dir, 'spool'), {recursive: true});
let socket, qr = null, lastQr = null, state = 'connecting', selected = new Set(), selfIds = new Set();
let reconnectTimer, controlBusy = false, authWrites = Promise.resolve(), waVersion = null, reconnectAttempts = 0, authSaveFailed = false;
const pauseFile = path.join(dir, 'paused');
let paused = false;
try { await readFile(pauseFile); paused = true; state = 'disconnected'; } catch {}

try {
  const latest = await fetchLatestBaileysVersion();
  if (Array.isArray(latest?.version)) waVersion = latest.version;
} catch {}

// Keep the last confirmed allowlist across app outages and bridge restarts.
const selectionFile = path.join(dir, 'selected.json');
let savedSelection = '[]';
try {
  const prior = JSON.parse(await readFile(selectionFile, 'utf8'));
  if (Array.isArray(prior) && prior.every(id => typeof id === 'string')) {
    savedSelection = JSON.stringify(prior);
    selected = new Set(prior);
  }
} catch { /* First startup has no selected groups. */ }
const logger = pino({level: 'silent'});
const safeEqual = value => {
  const a = Buffer.from(value || ''), b = Buffer.from(token);
  return a.length === b.length && timingSafeEqual(a, b);
};
async function backend(route, options = {}) {
  const response = await fetch(`${appUrl}/internal/wa${route}`, {
    ...options, headers: {'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json'},
    signal: AbortSignal.timeout(10000)
  });
  if (!response.ok) throw new Error('backend request failed');
  return response.json();
}

async function start() {
  if (paused) return;
  const auth = await useMultiFileAuthState(path.join(dir, 'auth'));
  if (paused) return;
  const socketOpts = {
    auth: auth.state, logger, markOnlineOnConnect: false,
    syncFullHistory: false, shouldSyncHistoryMessage: () => false,
    browser: ['Mac OS', 'Chrome', '125.0.0.0']
  };
  if (waVersion) socketOpts.version = waVersion;
  socket = makeWASocket(socketOpts);
  const activeSocket = socket;
  let paired = false, hadQr = false, closed = false;
  socket.ev.on('creds.update', () => {
    if(!paused && socket === activeSocket) authWrites = authWrites.then(auth.saveCreds).catch(() => {authSaveFailed=true;state='error';});
  });
  socket.ev.on('connection.update', update => {
    if(paused || socket !== activeSocket) return;
    if (update.qr) { hadQr = true; qr = update.qr; lastQr = update.qr; state = 'needs_scan'; }
    if (update.isNewLogin) { paired = true; qr = null; lastQr = null; state = 'connecting'; }
    if (update.connection === 'open') {
      state = 'connected'; qr = null; lastQr = null; reconnectAttempts = 0;
      selfIds = new Set([socket.user?.id, socket.user?.lid].filter(Boolean).map(jidNormalizedUser));
    }
    if (update.connection === 'close' && !closed) {
      closed = true;
      qr = null;
      clearTimeout(reconnectTimer);
      const error = update.lastDisconnect?.error;
      const decision = closePolicy({error,
        authenticated: paired || !!auth.state.creds.me || !!auth.state.creds.registered,
        hadQr, attempts: reconnectAttempts});
      state = authSaveFailed ? 'error' : decision.state;
      if (state !== 'qr_expired') lastQr = null;
      // Record only a status code and decision; never credentials, QR or messages.
      console.info(JSON.stringify({event: 'wa_connection_closed',
        code: error?.output?.statusCode ?? null, state, attempts: reconnectAttempts}));
      if (!authSaveFailed && decision.delay !== undefined) {
        reconnectAttempts += 1;
        reconnectTimer = setTimeout(() => {
          restartAfterAuth(authWrites,
            () => !paused && socket === activeSocket && !authSaveFailed,
            start).catch(() => { state = 'error'; });
        }, decision.delay);
      }
    }
  });
  socket.ev.on('messages.upsert', async ({messages, type}) => {
    try {
      if(paused || socket !== activeSocket) return;
      // Keep spooling selected messages during backend downtime; recheck before delivery.
      for (const msg of messages) {
        if (!selected.has(msg.key?.remoteJid)) continue;
        // Refresh both PN and LID identities; either may appear in a mention or quote.
        for (const jid of [socket.user?.id, socket.user?.lid]) if (jid) selfIds.add(jidNormalizedUser(jid));
        const event = normalize(msg, selfIds, normalizeMessageContent, jidNormalizedUser, type !== 'notify');
        if (!event || !Number.isFinite(event.timestamp)) continue;
        const key = createHash('sha256').update(event.chat_id + ':' + event.message_id).digest('hex');
        const target = path.join(dir, 'spool', key + '.json');
        const temp = target + '.' + randomUUID() + '.tmp';
        await writeFile(temp, JSON.stringify(event));
        await rename(temp, target);
      }
    } catch { state = 'message_error'; }
  });
}

let busy = false;
async function flush() {
  if (busy) return;
  busy = true;
  try {
    const config = await backend('/config');
    selected = new Set(config.selected);
    const serialized = JSON.stringify(config.selected);
    if (serialized !== savedSelection) {
      await writeFile(selectionFile + '.tmp', serialized);
      await rename(selectionFile + '.tmp', selectionFile);
      savedSelection = serialized;
    }
    await backend('/heartbeat', {method: 'POST', body: JSON.stringify({state})});
    if (paused) return;
    for (const file of (await readdir(path.join(dir, 'spool'))).filter(f => f.endsWith('.json')).slice(0, 200)) {
      const target = path.join(dir, 'spool', file);
      const event = JSON.parse(await readFile(target, 'utf8'));
      if (selected.has(event.chat_id)) await backend('/events', {method: 'POST', body: JSON.stringify(event)});
      await unlink(target);
    }
  } catch { /* No credentials or chat contents in logs; app detects expired heartbeat. */ }
  finally { busy = false; }
}

http.createServer(async (req, res) => {
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Cache-Control', 'no-store');
  if (!safeEqual(req.headers.authorization?.replace(/^Bearer /, ''))) {
    res.writeHead(401); res.end('{}'); return;
  }
  try {
    if (req.method === 'POST' && ['/pause','/resume'].includes(req.url)) {
      if(controlBusy){res.writeHead(409);res.end('{}');return;}
      controlBusy=true;
      try {
        let remoteLogout=true;
        if(req.url==='/pause'){
          await writeFile(pauseFile,'1');paused=true;clearTimeout(reconnectTimer);qr=null;lastQr=null;state='disconnected';
          const prior=socket;socket=null;
          try {
            if(prior?.user){
              let timeout;
              try{await Promise.race([prior.logout(),new Promise((_,reject)=>{timeout=setTimeout(()=>reject(new Error('Logout timeout')),10000);})]);}
              finally{clearTimeout(timeout);}
            }else{remoteLogout=false;}
          }catch{remoteLogout=false;}
          finally{prior?.end(new Error('Logged out by user'));}
          await authWrites;
          await rm(path.join(dir,'auth'),{recursive:true,force:true});
          await rm(path.join(dir,'spool'),{recursive:true,force:true});
          await mkdir(path.join(dir,'spool'),{recursive:true});
        }else{
          clearTimeout(reconnectTimer);
          qr=null;lastQr=null;
          const prior=socket;socket=null;
          prior?.end(new Error('Restarting connection'));
          await authWrites;
          await rm(path.join(dir,'auth'),{recursive:true,force:true});
          await rm(path.join(dir,'spool'),{recursive:true,force:true});
          await mkdir(path.join(dir,'spool'),{recursive:true});
          try{await unlink(pauseFile);}catch{}
          paused=false;authSaveFailed=false;reconnectAttempts=0;state='connecting';await start();
        }
        res.end(JSON.stringify({ok:true,remote_logout:remoteLogout}));
      } finally {controlBusy=false;}
    } else if (req.method === 'GET' && req.url === '/status') {
      res.end(JSON.stringify({state, qr: qr || (state === 'qr_expired' ? lastQr : null)}));
    } else if (req.method === 'GET' && req.url === '/groups') {
      if (state !== 'connected') { res.writeHead(409); res.end('{}'); return; }
      const all = await socket.groupFetchAllParticipating();
      res.end(JSON.stringify(Object.values(all).map(g => ({id: g.id, title: g.subject}))));
    } else { res.writeHead(404); res.end('{}'); }
  } catch { res.writeHead(503); res.end(JSON.stringify({error: 'WhatsApp暂不可用'})); }
}).listen(3001, '0.0.0.0');
await flush();
await start();
setInterval(flush, 5000);
