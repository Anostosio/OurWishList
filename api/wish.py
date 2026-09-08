import json
import os
from http.server import BaseHTTPRequestHandler

from wishlist.storage import open_store


class handler(BaseHTTPRequestHandler):
    def _send(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split('?', 1)[0] != '/api/wish':
            return self._send({'ok': False, 'error': 'Not found'}, 404)
        if not os.environ.get('DATABASE_URL', '').strip():
            return self._send({'ok': False, 'error': 'Cloud database is not configured'}, 503)
        try:
            length = int(self.headers.get('Content-Length', '0') or 0)
            payload = json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            return self._send({'ok': False, 'error': 'Bad JSON'}, 400)

        session = str(payload.get('session') or '').strip()
        title = str(payload.get('title') or '').strip()
        url = str(payload.get('url') or '').strip()
        price = str(payload.get('price') or '').strip()
        note = str(payload.get('note') or '').strip()
        if not session:
            return self._send({'ok': False, 'error': 'Session expired'}, 401)
        if not title:
            return self._send({'ok': False, 'error': 'Введите название желания'}, 400)

        try:
            store = open_store()
            uid = store.webapp_session_uid(session)
            if not uid:
                return self._send({'ok': False, 'error': 'Session expired'}, 401)
            wish_id, created = store.add(uid, title, url=url, price=price, note=note)
            return self._send({'ok': True, 'id': wish_id, 'created': bool(created)})
        except Exception:
            return self._send({'ok': False, 'error': 'Не удалось сохранить желание'}, 500)
