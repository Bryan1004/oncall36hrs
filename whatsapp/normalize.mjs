// Pure transformation kept separate so mention/reply edge cases can be tested.
export function normalize(msg, selfIds, normalizeContent, jidNormalize, historical = false) {
  const body = normalizeContent(msg.message);
  if (!body || !msg.key?.remoteJid?.endsWith('@g.us') || !msg.key.id) return null;
  const isSelf = jid => !!jid && selfIds.has(jidNormalize(jid));
  const nodes = Object.values(body).filter(v => v && typeof v === 'object');
  const contexts = nodes.map(v => v.contextInfo).filter(Boolean);
  const text = body.conversation || nodes.find(v => v.text)?.text || nodes.find(v => v.caption)?.caption || '[非文字消息]';
  return {
    platform: 'whatsapp', chat_id: msg.key.remoteJid, message_id: msg.key.id,
    sender: msg.pushName || msg.key.participant || '', text,
    timestamp: Number(msg.messageTimestamp), from_me: !!msg.key.fromMe, historical,
    mentioned: contexts.some(c => (c.mentionedJid || []).some(isSelf)),
    reply_to_me: contexts.some(c => !!c.stanzaId && isSelf(c.participant))
  };
}
