import json
import os
from http.server import BaseHTTPRequestHandler

from wishlist.metadata import clean_url, extract
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
        if self.path.split('?', 1)[0] != '/api/metadata':
            return self._send({'ok': False, 'error': 'Not found'}, 404)
        if not os.environ.get('DATABASE_URL', '').strip():
            return self._send({'ok': False, 'error': 'Cloud database is not configured'}, 503)
        try:
            length = int(self.headers.get('Content-Length', '0') or 0)
            if length > 20_000:
                return self._send({'ok': False, 'error': 'Слишком большой запрос'}, 413)
            payload = json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            return self._send({'ok': False, 'error': 'Bad JSON'}, 400)

        session = str(payload.get('session') or '').strip()
        url = str(payload.get('url') or '').strip()
        if not session:
            return self._send({'ok': False, 'error': 'Session expired'}, 401)
        try:
            store = open_store()
            if not store.webapp_session_uid(session):
                return self._send({'ok': False, 'error': 'Session expired'}, 401)
            url = clean_url(url)
            data = extract(url)
            return self._send({'ok': True, 'url': url, **data})
        except ValueError:
            return self._send({
                'ok': False,
                'error': 'Не получилось прочитать магазин. Заполните карточку вручную.'
            }, 422)
        except Exception:
            return self._send({
                'ok': False,
                'error': 'Магазин временно недоступен. Заполните карточку вручную.'
            }, 502)
