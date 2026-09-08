"""Authenticate Telegram Mini Apps without relying on one browser session."""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl


def telegram_user(init_data, bot_token=None, max_age=86400):
    values = dict(parse_qsl(str(init_data or ''), keep_blank_values=True))
    supplied_hash = values.pop('hash', '')
    token = str(bot_token or os.environ.get('BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN') or '')
    if not supplied_hash or not token:
        return None
    check = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied_hash, expected):
        return None
    try:
        auth_date = int(values.get('auth_date', '0'))
        user = json.loads(values['user'])
        uid = int(user['id'])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if auth_date <= 0 or abs(int(time.time()) - auth_date) > max_age:
        return None
    name = ' '.join(str(user.get(key) or '').strip() for key in ('first_name', 'last_name')).strip()
    return uid, name or str(user.get('username') or 'Пользователь')


def authorized_uid(store, session='', init_data='', bot_token=None):
    telegram = telegram_user(init_data, bot_token)
    if telegram:
        uid, name = telegram
        store.user(uid, name)
        return uid
    token = str(session or '').strip()
    return store.webapp_session_uid(token) if token else None
