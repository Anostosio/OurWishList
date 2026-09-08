import json
import secrets
from datetime import datetime, timezone, timedelta

from psycopg import connect
from psycopg.rows import dict_row

from .catalog import CATEGORIES, MATCH_MODES, KINDS, money


class _CompatConnection:
    def __init__(self, connection):
        self._connection = connection

    def cursor(self):
        return self._connection.cursor()

    def execute(self, query, args=()):
        return self._connection.execute(query.replace('?', '%s'), args)

    def close(self):
        return self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class PostgresStore:
    def __init__(self, url):
        connection = connect(url, autocommit=True, row_factory=dict_row)
        self.db = _CompatConnection(connection)
        with self.db.cursor() as cur:
            cur.execute('CREATE TABLE IF NOT EXISTS users(id BIGINT PRIMARY KEY,name TEXT NOT NULL,pair TEXT,state TEXT)')
            cur.execute('CREATE TABLE IF NOT EXISTS invites(token TEXT PRIMARY KEY,owner BIGINT NOT NULL,created TEXT NOT NULL)')
            cur.execute('''CREATE TABLE IF NOT EXISTS wishes(
                id BIGSERIAL PRIMARY KEY,
                owner BIGINT NOT NULL REFERENCES users(id),
                scope TEXT NOT NULL DEFAULT 'mine',
                title TEXT NOT NULL,
                url TEXT DEFAULT '', image TEXT DEFAULT '', price TEXT DEFAULT '', note TEXT DEFAULT '',
                priority INTEGER DEFAULT 1, archived INTEGER DEFAULT 0, claimed_by BIGINT,
                created TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'other', match_mode TEXT NOT NULL DEFAULT 'unspecified',
                size TEXT NOT NULL DEFAULT '', color TEXT NOT NULL DEFAULT '', price_checked TEXT NOT NULL DEFAULT ''
            )''')
            cur.execute("ALTER TABLE wishes ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT ''")
            cur.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)')
            cur.execute('CREATE TABLE IF NOT EXISTS webapp_sessions(token TEXT PRIMARY KEY,uid BIGINT NOT NULL,created TEXT NOT NULL)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_webapp_sessions_created ON webapp_sessions(created)')
            cur.execute('DELETE FROM webapp_sessions WHERE created < %s', ((datetime.now(timezone.utc)-timedelta(hours=24)).isoformat(),))

    def _one(self, query, args=()):
        with self.db.cursor() as cur:
            cur.execute(query, args)
            return cur.fetchone()

    def _all(self, query, args=()):
        with self.db.cursor() as cur:
            cur.execute(query, args)
            return cur.fetchall()

    def user(self, uid, name=None):
        if name is not None:
            with self.db.cursor() as cur:
                cur.execute('INSERT INTO users(id,name) VALUES(%s,%s) ON CONFLICT(id) DO UPDATE SET name=excluded.name', (uid, name))
        return self._one('SELECT * FROM users WHERE id=%s', (uid,))

    def state(self, uid, value):
        with self.db.cursor() as cur:
            cur.execute('UPDATE users SET state=%s WHERE id=%s', (value, uid))

    def invite(self, uid):
        if self.user(uid)['pair']:
            raise ValueError('Вы уже связаны с партнёром.')
        token = secrets.token_urlsafe(18)
        with self.db.cursor() as cur:
            cur.execute('DELETE FROM invites WHERE owner=%s', (uid,))
            cur.execute('INSERT INTO invites(token,owner,created) VALUES(%s,%s,%s)', (token, uid, datetime.now(timezone.utc).isoformat()))
        return token

    def join(self, uid, token):
        invite = self._one('SELECT * FROM invites WHERE token=%s', (token,))
        if not invite or invite['owner'] == uid:
            raise ValueError('Приглашение недействительно. Попросите партнёра создать новое.')
        age = datetime.now(timezone.utc) - datetime.fromisoformat(invite['created'])
        if age.total_seconds() > 86400:
            raise ValueError('Приглашение истекло. Попросите новое.')
        owner = self.user(invite['owner'])
        if self.user(uid)['pair'] or owner['pair']:
            raise ValueError('Один из участников уже состоит в паре.')
        pair = secrets.token_urlsafe(18)
        with self.db.cursor() as cur:
            cur.execute('UPDATE users SET pair=%s WHERE id IN (%s,%s)', (pair, uid, owner['id']))
            cur.execute('DELETE FROM invites WHERE owner IN (%s,%s)', (uid, owner['id']))

    def partner(self, uid):
        me = self.user(uid)
        if not me or not me['pair']:
            return None
        return self._one('SELECT * FROM users WHERE pair=%s AND id<>%s', (me['pair'], uid))

    def wish(self, uid, wid):
        row = self._one('SELECT * FROM wishes WHERE id=%s', (wid,))
        if row:
            partner = self.partner(uid)
            if row['owner'] == uid or (partner and row['owner'] == partner['id']):
                return row
        raise ValueError('Желание недоступно.')

    def create_webapp_session(self, uid):
        token = secrets.token_urlsafe(24)
        created = datetime.now(timezone.utc).isoformat()
        cutoff = (datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()
        with self.db.cursor() as cur:
            cur.execute('DELETE FROM webapp_sessions WHERE uid=%s OR created < %s', (uid, cutoff))
            cur.execute('INSERT INTO webapp_sessions(token,uid,created) VALUES(%s,%s,%s)', (token, uid, created))
        return token

    def webapp_session_uid(self, token):
        row = self._one('SELECT uid,created FROM webapp_sessions WHERE token=%s', (token,))
        if not row:
            return None
        created = datetime.fromisoformat(row['created'])
        if datetime.now(timezone.utc)-created > timedelta(hours=24):
            with self.db.cursor() as cur:
                cur.execute('DELETE FROM webapp_sessions WHERE token=%s', (token,))
            return None
        return row['uid']

    def add(self, uid, title, url='', image='', price='', note='', source=''):
        if url:
            duplicate = self._one('SELECT id FROM wishes WHERE owner=%s AND url=%s AND archived=0', (uid, url))
            if duplicate:
                return duplicate['id'], False
        with self.db.cursor() as cur:
            created = datetime.now(timezone.utc).isoformat()
            cur.execute('''INSERT INTO wishes(owner,title,url,image,price,note,created,price_checked,source)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
                        (uid, title[:200], url, image, price[:100], note[:1500], created, created if price else '', source[:255]))
            wid = cur.fetchone()['id']
        return wid, True

    def change(self, uid, wid, field, value):
        row = self.wish(uid, wid)
        if row['owner'] != uid:
            raise ValueError('Изменять желание может только его автор.')
        allowed = {'title','note','price','scope','priority','archived','image','category','match_mode','size','color'}
        if field not in allowed:
            raise ValueError('Неизвестное поле.')
        if field == 'scope' and value not in ('mine','shared'):
            raise ValueError('Неизвестный список.')
        if field == 'category' and value not in CATEGORIES:
            raise ValueError('Неизвестная категория.')
        if field == 'match_mode' and value not in MATCH_MODES:
            raise ValueError('Неизвестный вариант.')
        if field == 'priority' and value not in (0,1,2):
            raise ValueError('Неизвестный приоритет.')
        with self.db.cursor() as cur:
            cur.execute(f'UPDATE wishes SET {field}=%s WHERE id=%s', (value, wid))
            if field == 'price':
                cur.execute('UPDATE wishes SET price_checked=%s WHERE id=%s', (datetime.now(timezone.utc).isoformat() if value else '', wid))
            if field == 'scope':
                cur.execute('UPDATE wishes SET claimed_by=NULL WHERE id=%s', (wid,))

    def filters(self, uid, updates=None):
        key = f'filters:{uid}'
        result = {'query':'','category':'','budget':'','sort':'priority'}
        result.update(json.loads(self.setting(key) or '{}'))
        if updates is not None:
            result.update(updates)
            self.setting(key, json.dumps(result, ensure_ascii=False))
        return result

    def browse(self, uid, kind):
        if kind not in KINDS:
            raise ValueError('Неизвестный список.')
        partner = self.partner(uid)
        ids = [uid] + ([partner['id']] if partner else [])
        if kind == 'partner':
            ids = [partner['id']] if partner else []
        elif kind == 'mine':
            ids = [uid]
        if not ids:
            return []
        if kind == 'archive':
            rows = self._all('SELECT * FROM wishes WHERE owner = ANY(%s) AND archived=1 ORDER BY priority DESC,id DESC', (ids,))
        else:
            scope = 'shared' if kind == 'shared' else 'mine'
            rows = self._all('SELECT * FROM wishes WHERE owner = ANY(%s) AND archived=0 AND scope=%s ORDER BY priority DESC,id DESC', (ids, scope))
        filters = self.filters(uid)
        term = filters['query'].casefold()
        if term:
            rows = [r for r in rows if term in ' '.join(str(r.get(k) or '') for k in ('title','note','size','color')).casefold()]
        if filters['category']:
            rows = [r for r in rows if r['category'] == filters['category']]
        if filters['budget']:
            maximum = money(filters['budget'])
            if maximum is None:
                return []
            rows = [r for r in rows if (price := money(r['price'])) and price[1] == maximum[1] and price[0] <= maximum[0]]
        if filters['sort'] == 'newest':
            rows.sort(key=lambda r: r['id'], reverse=True)
        return rows

    def claim(self, uid, wid):
        row = self.wish(uid, wid)
        if row['owner'] == uid or row['scope'] != 'mine' or row['archived']:
            raise ValueError('Бронь доступна для активного личного желания партнёра.')
        with self.db.cursor() as cur:
            cur.execute('UPDATE wishes SET claimed_by=%s WHERE id=%s', (None if row['claimed_by'] == uid else uid, wid))

    def listing(self, uid, kind, page=0):
        rows = self.browse(uid, kind)
        start = max(0, page)*5
        return rows[start:start+6]

    def setting(self, key, value=None):
        if value is not None:
            with self.db.cursor() as cur:
                cur.execute('INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))
        row = self._one('SELECT value FROM settings WHERE key=%s', (key,))
        return row['value'] if row else None

    def export(self, uid):
        return self._all('''SELECT id,title,url,price,note,scope,priority,archived,created,category,match_mode,size,color,price_checked,source
                            FROM wishes WHERE owner=%s ORDER BY id''', (uid,))

    def update_metadata(self, wid, title, image, source=''):
        with self.db.cursor() as cur:
            cur.execute('UPDATE wishes SET title=%s,image=%s,source=%s WHERE id=%s', (title[:200], image, source[:255], wid))
