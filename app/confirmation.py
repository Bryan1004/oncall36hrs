"""Short-lived capabilities scoped to the alerts in one notification."""
import base64
import hashlib
import hmac
import json
import time

TTL = 24 * 60 * 60


def sign(config, ids, now=None):
    payload = json.dumps({'ids': ids, 'exp': int(time.time() if now is None else now) + TTL}, separators=(',', ':')).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip('=')
    signature = hmac.new(config.bridge_token.encode(), b'notification-ack-v1:' + encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + '.' + signature


def verify(config, token):
    try:
        encoded, signature = token.split('.')
        expected = hmac.new(config.bridge_token.encode(), b'notification-ack-v1:' + encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        if data['exp'] <= time.time():
            raise ValueError()
        ids = data['ids']
        if not isinstance(ids, list) or not ids or any(type(i) is not int or i <= 0 for i in ids):
            raise ValueError()
        return ids
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise ValueError('确认链接无效或已过期，请打开最新通知或在面板手动确认。') from None
