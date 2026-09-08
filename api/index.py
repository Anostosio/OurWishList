import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from wishlist.catalog import CATEGORIES, KINDS, money
from wishlist.storage import open_store


def serial(row, owner_name, is_owner, viewer_id=None):
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
        'priceChecked': row['price_checked'] or '',
        'source': row['source'] if 'source' in row.keys() else '',
        'ownerName': owner_name,
        'isOwner': is_owner,
        'archived': bool(row['archived']),
        'claimedByMe': bool(not is_owner and viewer_id and row['claimed_by'] == viewer_id),
        'scopeLabel': 'Для нас' if row['scope'] == 'shared' else ('Личное' if is_owner else 'Партнёру')
    }


def filtered_rows(store, uid, kind, query, category, budget, sort):
    rows = list(store.browse(uid, kind))
    if query:
        term = query.casefold()
        rows = [row for row in rows if term in ' '.join((
            row['title'] or '', row['note'] or '', row['size'] or '', row['color'] or ''
        )).casefold()]
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


class handler(BaseHTTPRequestHandler):
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

    def _session_uid(self, store, values):
        token = values.get('session', [''])[0].strip()
        return store.webapp_session_uid(token) if token else None

    def do_GET(self):
        parsed = urlparse(self.path)
        values = parse_qs(parsed.query)

        if parsed.path == '/api/health':
            configured = bool(os.environ.get('DATABASE_URL', '').strip())
            return self._send({'ok': configured, 'database': 'postgres' if configured else 'missing'}, 200 if configured else 503)

        try:
            store = self._store()
        except Exception:
            return self._send({'ok': False, 'error': 'Cloud database is not configured'}, 503)

        uid = self._session_uid(store, values)
        if not uid:
            return self._send({'ok': False, 'error': 'Session expired'}, 401)

        if parsed.path == '/api/wishlist':
            kind = values.get('kind', ['mine'])[0]
            if kind not in KINDS:
                return self._send({'ok': False, 'error': 'Unknown kind'}, 400)
            query = (values.get('query', [''])[0] or '').strip()
            category = (values.get('category', [''])[0] or '').strip()
            if category and category not in CATEGORIES:
                return self._send({'ok': False, 'error': 'Unknown category'}, 400)
            budget = (values.get('budget', [''])[0] or '').strip()
            sort = values.get('sort', ['priority'])[0]
            try:
                page = max(0, int(values.get('page', ['0'])[0] or 0))
                limit = min(30, max(6, int(values.get('limit', ['12'])[0] or 12)))
            except ValueError:
                return self._send({'ok': False, 'error': 'Bad pagination values'}, 400)

            rows = filtered_rows(store, uid, kind, query, category, budget, sort)
            if rows is None:
                return self._send({'ok': False, 'error': 'Budget parse error. Use one currency, e.g. 2000 UAH.'}, 400)
            start = page * limit
            stop = start + limit
            cards = []
            for row in rows[start:stop]:
                owner = store.user(row['owner'])
                cards.append(serial(row, owner['name'] if owner else '—', row['owner'] == uid, uid))
            return self._send({
                'ok': True,
                'kind': kind,
                'page': page,
                'limit': limit,
                'count': len(rows),
                'hasMore': stop < len(rows),
                'cards': cards
            })

        if parsed.path.startswith('/api/wish/'):
            wid = parsed.path.removeprefix('/api/wish/')
            if not wid.isdigit():
                return self._send({'ok': False, 'error': 'Bad wish id'}, 400)
            try:
                row = store.wish(uid, int(wid))
            except ValueError:
                return self._send({'ok': False, 'error': 'Wish not found'}, 404)
            owner = store.user(row['owner'])
            return self._send({
                'ok': True,
                'card': {
                    **serial(row, owner['name'] if owner else '—', row['owner'] == uid, uid),
                    'note': row['note'],
                    'matchModeLabel': {
                        'unspecified': 'Вариант не уточнён',
                        'exact': 'Именно это',
                        'alternative': 'Можно аналог'
                    }.get(row['match_mode'], '')
                }
            })

        return self._send({'ok': False, 'error': 'Not found'}, 404)
