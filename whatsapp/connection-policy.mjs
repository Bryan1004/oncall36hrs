import {DisconnectReason} from '@whiskeysockets/baileys';

// A 408 is also connectionLost: only Baileys' QR exhaustion error means expired.
export function closePolicy({error, authenticated, hadQr, attempts}) {
  const code = error?.output?.statusCode;
  if (code === DisconnectReason.loggedOut) return {state: 'logged_out'};
  if ([DisconnectReason.forbidden, DisconnectReason.badSession,
       DisconnectReason.connectionReplaced, DisconnectReason.multideviceMismatch].includes(code)) {
    return {state: 'error'};
  }
  if (!authenticated && hadQr && code === DisconnectReason.timedOut &&
      error?.message === 'QR refs attempts ended') return {state: 'qr_expired'};
  if (code !== DisconnectReason.restartRequired && !authenticated) return {state: 'error'};
  if (attempts >= 5) return {state: 'error'};
  return {state: 'reconnecting', delay: Math.min(5000 * 2 ** attempts, 60000)};
}

// Recheck after the write barrier: a user may have disconnected while saving.
export async function restartAfterAuth(saved, isCurrent, restart) {
  await saved;
  if (isCurrent()) await restart();
}
