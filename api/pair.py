import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from wishlist.storage import open_store
from wishlist.web_auth import authorized_uid


class handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return

    def _send(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _store(self):
        if not os.environ.get('DATABASE_URL', '').strip():
            raise RuntimeError('DATABASE_URL is not configured')
        return open_store()

    def _uid(self, store, session, init_data=''):
        return authorized_uid(store, session, init_data)

    def do_GET(self):
        if urlparse(self.path).path != '/api/pair':
            return self._send({'ok': False, 'error': 'Not found'}, 404)
        try:
            values = parse_qs(urlparse(self.path).query)
            store = self._store()
            uid = self._uid(store, self.headers.get('X-Wishlist-Session', '') or values.get('session', [''])[0], self.headers.get('X-Telegram-Init-Data', ''))
            if not uid:
                return self._send({'ok': False, 'error': 'Сессия истекла. Откройте приложение заново через бота.'}, 401)
            partner = store.partner(uid)
            return self._send({
                'ok': True,
                'paired': bool(partner),
                'partnerName': partner['name'] if partner else '',
            })
        except Exception:
            return self._send({'ok': False, 'error': 'Не удалось проверить пару'}, 500)

    def do_POST(self):
        if self.path.split('?', 1)[0] != '/api/pair':
            return self._send({'ok': False, 'error': 'Not found'}, 404)
        try:
            length = int(self.headers.get('Content-Length', '0') or 0)
            if length > 20_000:
                return self._send({'ok': False, 'error': 'Слишком большой запрос'}, 413)
            payload = json.loads(self.rfile.read(length) or b'{}')
            store = self._store()
            uid = self._uid(store, payload.get('session'), payload.get('initData'))
            if not uid:
                return self._send({'ok': False, 'error': 'Сессия истекла. Откройте приложение заново через бота.'}, 401)
            if payload.get('action') != 'invite':
                return self._send({'ok': False, 'error': 'Неизвестное действие'}, 400)
            username = os.environ.get('TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')
            if not username:
                return self._send({'ok': False, 'error': 'Бот не настроен'}, 503)
            token = store.invite(uid)
            return self._send({
                'ok': True,
                'link': f'https://t.me/{username}?start=join_{token}',
                'expiresInHours': 24,
            })
        except ValueError as error:
            return self._send({'ok': False, 'error': str(error)}, 400)
        except Exception:
            return self._send({'ok': False, 'error': 'Не удалось создать приглашение'}, 500)
