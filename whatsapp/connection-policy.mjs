import {DisconnectReason} from '@whiskeysockets/baileys';

// A 408 is also connectionLost: only Baileys' QR exhaustion error means expired.
// No close reason starts another socket without an explicit user action.
export function closePolicy({error, authenticated, hadQr}) {
  const code = error?.output?.statusCode;
  if (code === DisconnectReason.loggedOut) return {state: 'logged_out'};
  if (!authenticated && hadQr && code === DisconnectReason.timedOut &&
      error?.message === 'QR refs attempts ended') return {state: 'qr_expired'};
  return {state: 'error'};
}
