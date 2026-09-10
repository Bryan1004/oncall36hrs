import test from 'node:test';
import assert from 'node:assert/strict';
import {normalize} from './normalize.mjs';
import {normalizeMessageContent, jidNormalizedUser} from '@whiskeysockets/baileys';
const self = new Set(['601234@s.whatsapp.net', '9876@lid']);
function message(body, overrides={}){return {key:{id:'A',remoteJid:'group@g.us',fromMe:false},message:body,messageTimestamp:1000,...overrides};}
const parse=m=>normalize(m,self,normalizeMessageContent,jidNormalizedUser);
test('detects PN and LID mentions, including media captions',()=>{
 for(const jid of self){const e=parse(message({imageMessage:{caption:'看看',contextInfo:{mentionedJid:[jid]}}}));assert.equal(e.mentioned,true);assert.equal(e.text,'看看');}
});
test('normalizes device suffix and ephemeral quoted reply',()=>{
 const e=parse(message({ephemeralMessage:{message:{extendedTextMessage:{text:'回复',contextInfo:{stanzaId:'original',participant:'601234:2@s.whatsapp.net'}}}}}));
 assert.equal(e.reply_to_me,true);
});
test('plain @text and replies to others do not match',()=>{
 assert.equal(parse(message({conversation:'@601234'})).mentioned,false);
 assert.equal(parse(message({extendedTextMessage:{contextInfo:{stanzaId:'a',participant:'other@lid'}}})).reply_to_me,false);
});
test('ignores private chats and empty protocol messages',()=>{
 assert.equal(parse(message({conversation:'hello'},{key:{remoteJid:'other@s.whatsapp.net',id:'a'}})),null);
 assert.equal(parse({...message({}),message:null}),null);
});
