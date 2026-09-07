import json
import logging
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .store import Store
from .catalog import CATEGORIES, money, KINDS


LOG = logging.getLogger('wishlist.miniapp')
INDEX = Path(__file__).with_name('miniapp').joinpath('index.html')


def _json_response(handler, payload, status=200):
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
    handler.send_header('Access-Control-Allow-Headers', 'Content-Type')
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    handler.wfile.write(body)


def _read_index():
    if not INDEX.exists():
        return None
    return INDEX.read_text(encoding='utf-8')


def _serial(row, owner_name, is_owner):
    short = row['note']
    if short and len(short) > 180:
        short = short[:177] + '...'
    return {
        'id': row['id'],
        'title': row['title'],
        'url': row['url'],
        'image': row['image'],
        'price': row['price'],
        'note': short,
        'priority': row['priority'],
        'category': row['category'],
        'matchMode': row['match_mode'],
        'size': row['size'],
        'color': row['color'],
        'scope': row['scope'],
        'created': row['created'],
        'ownerName': owner_name,
        'isOwner': is_owner,
        'archived': bool(row['archived']),
        'scopeLabel': 'Для нас' if row['scope'] == 'shared' else ('Личное' if is_owner else 'Партнёру')
    }


def _filtered_rows(store, uid, kind, query, category, budget, sort):
    rows = list(store.browse(uid, kind))
    if query:
        term = query.casefold()
        rows = [row for row in rows if term in (' '.join((
            row['title'], row['note'], row['size'], row['color'])).casefold())]
    if category:
        rows = [row for row in rows if row['category'] == category]
    if budget:
        maximum = money(budget)
        if maximum is None:
            return None
        rows = [row for row in rows if (price := money(row['price'])) and price[1] == maximum[1] and price[0] <= maximum[0]]
    if sort == 'newest':
        rows.sort(key=lambda row: row['id'], reverse=True)
    return rows


def _create_handler(store_path):
    class MiniAppHandler(BaseHTTPRequestHandler):
        server_version = 'wishlist-miniapp/0.1'

        def _session_uid(self):
            parsed = urlparse(self.path)
            values = parse_qs(parsed.query)
            token = values.get('session', [''])[0].strip()
            if not token:
                return None
            store = Store(store_path)
            return store.webapp_session_uid(token)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path == '/':
                body = _read_index()
                if body is None:
                    return _json_response(self, {'ok': False, 'error': 'Mini App not found.'}, 500)
                raw = body.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            if path == '/api/health':
                return _json_response(self, {'ok': True})

            if path == '/api/wishlist':
                values = parse_qs(parsed.query)
                uid = self._session_uid()
                if not uid:
                    return _json_response(self, {'ok': False, 'error': 'Session expired'}, 401)

                kind = values.get('kind', ['mine'])[0]
                if kind not in KINDS:
                    return _json_response(self, {'ok': False, 'error': 'Unknown kind'}, 400)

                query = (values.get('query', [''])[0] or '').strip()
                category = (values.get('category', [''])[0] or '').strip()
                if category and category not in CATEGORIES:
                    return _json_response(self, {'ok': False, 'error': 'Unknown category'}, 400)

                budget = (values.get('budget', [''])[0] or '').strip()
                sort = values.get('sort', ['priority'])[0]
                try:
                    page = max(0, int(values.get('page', ['0'])[0] or 0))
                    limit = min(30, max(6, int(values.get('limit', ['12'])[0] or 12)))
                except ValueError:
                    return _json_response(self, {'ok': False, 'error': 'Bad pagination values'}, 400)

                store = Store(store_path)
                filtered = _filtered_rows(store, uid, kind, query, category, budget, sort)
                if filtered is None:
                    return _json_response(self, {'ok': False, 'error': 'Budget parse error. Use one currency, e.g. 2000 UAH.'}, 400)

                start = page * limit
                stop = start + limit
                slice_rows = filtered[start:stop]
                cards = []
                for row in slice_rows:
                    owner_name = store.user(row['owner'])['name'] if row['owner'] else '—'
                    cards.append(_serial(row, owner_name, row['owner'] == uid))

                return _json_response(self, {
                    'ok': True,
                    'kind': kind,
                    'page': page,
                    'limit': limit,
                    'count': len(filtered),
                    'hasMore': stop < len(filtered),
                    'cards': cards
                })

            if path.startswith('/api/wish/'):
                _, _, wid_part = path.partition('/api/wish/')
                uid = self._session_uid()
                if not uid:
                    return _json_response(self, {'ok': False, 'error': 'Session expired'}, 401)
                if not wid_part.isdigit():
                    return _json_response(self, {'ok': False, 'error': 'Bad wish id'}, 400)

                store = Store(store_path)
                row = store.wish(uid, int(wid_part))
                if not row:
                    return _json_response(self, {'ok': False, 'error': 'Wish not found'}, 404)
                owner = store.user(row['owner'])
                return _json_response(self, {
                    'ok': True,
                    'card': {
                        **_serial(row, owner['name'] if owner else '—', row['owner'] == uid),
                        'note': row['note'],
                        'priceChecked': row['price_checked'] or '',
                        'matchModeLabel': {
                            'unspecified': 'Вариант не уточнён',
                            'exact': 'Именно это',
                            'alternative': 'Можно аналог'
                        }.get(row['match_mode'], '')
                    }
                })

            return _json_response(self, {'ok': False, 'error': 'Not found'}, 404)

        def log_message(self, *_):
            return

    return MiniAppHandler


def run_server(db_path, host='127.0.0.1', port=8765):
    handler = _create_handler(db_path)
    server = ThreadingHTTPServer((host, port), handler)
    LOG.info('Mini App server start at http://%s:%s', host, port)
    server.serve_forever()


def main():
    import os
    path = Path(os.environ.get('DATABASE_PATH', 'data/wishlist.sqlite3'))
    host = os.environ.get('WEBAPP_BIND', '127.0.0.1')
    port = int(os.environ.get('WEBAPP_PORT', '8765'))
    path.parent.mkdir(parents=True, exist_ok=True)
    run_server(path, host=host, port=port)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    main()
