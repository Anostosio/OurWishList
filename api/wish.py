import json
import os
from http.server import BaseHTTPRequestHandler

from wishlist.catalog import CATEGORIES, MATCH_MODES
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

    def _payload(self):
        length = int(self.headers.get('Content-Length', '0') or 0)
        if length > 50_000:
            raise ValueError('Слишком большой запрос')
        return json.loads(self.rfile.read(length) or b'{}')

    def _authorized(self, payload):
        if not os.environ.get('DATABASE_URL', '').strip():
            return None, None, (503, 'Cloud database is not configured')
        session = str(payload.get('session') or '').strip()
        if not session:
            return None, None, (401, 'Session expired')
        store = open_store()
        uid = store.webapp_session_uid(session)
        if not uid:
            return None, None, (401, 'Session expired')
        return store, uid, None

    def do_POST(self):
        if self.path.split('?', 1)[0] != '/api/wish':
            return self._send({'ok': False, 'error': 'Not found'}, 404)
        try:
            payload = self._payload()
        except Exception:
            return self._send({'ok': False, 'error': 'Bad JSON'}, 400)

        title = str(payload.get('title') or '').strip()
        url = str(payload.get('url') or '').strip()
        image = str(payload.get('image') or '').strip()
        source = str(payload.get('source') or '').strip()
        price = str(payload.get('price') or '').strip()
        note = str(payload.get('note') or '').strip()
        if not title:
            return self._send({'ok': False, 'error': 'Введите название желания'}, 400)

        try:
            store, uid, error = self._authorized(payload)
            if error:
                return self._send({'ok': False, 'error': error[1]}, error[0])
            wish_id, created = store.add(uid, title, url=url, image=image, price=price, note=note, source=source)
            if created:
                updates = {
                    'scope': str(payload.get('scope') or 'mine'),
                    'priority': int(payload.get('priority', 1)),
                    'category': str(payload.get('category') or 'other'),
                    'match_mode': str(payload.get('matchMode') or 'unspecified'),
                    'size': str(payload.get('size') or '')[:100],
                    'color': str(payload.get('color') or '')[:100],
                }
                for field, value in updates.items():
                    store.change(uid, wish_id, field, value)
            return self._send({'ok': True, 'id': wish_id, 'created': bool(created)})
        except (ValueError, TypeError):
            return self._send({'ok': False, 'error': 'Проверьте заполненные поля'}, 400)
        except Exception:
            return self._send({'ok': False, 'error': 'Не удалось сохранить желание'}, 500)

    def do_PATCH(self):
        if self.path.split('?', 1)[0] != '/api/wish':
            return self._send({'ok': False, 'error': 'Not found'}, 404)
        try:
            payload = self._payload()
            store, uid, error = self._authorized(payload)
            if error:
                return self._send({'ok': False, 'error': error[1]}, error[0])
            wish_id = int(payload.get('id'))
            action = str(payload.get('action') or 'update')
            if action == 'claim':
                store.claim(uid, wish_id)
            elif action == 'archive':
                row = store.wish(uid, wish_id)
                store.change(uid, wish_id, 'archived', 0 if row['archived'] else 1)
            elif action == 'update':
                values = {
                    'title': str(payload.get('title') or '').strip(),
                    'price': str(payload.get('price') or '').strip(),
                    'note': str(payload.get('note') or '').strip(),
                    'scope': str(payload.get('scope') or 'mine'),
                    'priority': int(payload.get('priority', 1)),
                    'category': str(payload.get('category') or 'other'),
                    'match_mode': str(payload.get('matchMode') or 'unspecified'),
                    'size': str(payload.get('size') or '')[:100],
                    'color': str(payload.get('color') or '')[:100],
                }
                if not values['title'] or values['category'] not in CATEGORIES or values['match_mode'] not in MATCH_MODES:
                    raise ValueError('Некорректные поля')
                for field, value in values.items():
                    store.change(uid, wish_id, field, value)
            else:
                return self._send({'ok': False, 'error': 'Unknown action'}, 400)
            return self._send({'ok': True})
        except (ValueError, TypeError):
            return self._send({'ok': False, 'error': 'Действие недоступно'}, 400)
        except Exception:
            return self._send({'ok': False, 'error': 'Не удалось обновить желание'}, 500)
