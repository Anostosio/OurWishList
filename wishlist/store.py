import os
import secrets
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from .catalog import CATEGORIES, MATCH_MODES, KINDS, money


class Store:
    def __new__(cls, path):
        database_url = os.environ.get('DATABASE_URL', '').strip()
        if cls is Store and database_url:
            from .postgres_store import PostgresStore
            return PostgresStore(database_url)
        return super().__new__(cls)

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, pair TEXT, state TEXT);
        CREATE TABLE IF NOT EXISTS invites(token TEXT PRIMARY KEY, owner INTEGER NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS wishes(
          id INTEGER PRIMARY KEY, owner INTEGER NOT NULL REFERENCES users(id), scope TEXT NOT NULL DEFAULT 'mine',
          title TEXT NOT NULL, url TEXT DEFAULT '', image TEXT DEFAULT '', price TEXT DEFAULT '',
          note TEXT DEFAULT '', priority INTEGER DEFAULT 1, archived INTEGER DEFAULT 0,
          claimed_by INTEGER, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS webapp_sessions(token TEXT PRIMARY KEY, uid INTEGER NOT NULL, created TEXT NOT NULL);
        ''')
        # Additive, repeatable migration: existing IDs, invitations and claims survive.
        columns = {row['name'] for row in self.db.execute('PRAGMA table_info(wishes)')}
        with self.db:
            for name, definition in {
                'category': "TEXT NOT NULL DEFAULT 'other'",
                'match_mode': "TEXT NOT NULL DEFAULT 'unspecified'",
                'size': "TEXT NOT NULL DEFAULT ''", 'color': "TEXT NOT NULL DEFAULT ''",
                'price_checked': "TEXT NOT NULL DEFAULT ''",
                'source': "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in columns:
                    self.db.execute(f'ALTER TABLE wishes ADD COLUMN {name} {definition}')
            self.db.execute("UPDATE wishes SET price_checked=created WHERE price<>'' AND price_checked=''")
            self.db.execute('CREATE INDEX IF NOT EXISTS idx_webapp_sessions_created ON webapp_sessions(created)')
            self.db.execute('DELETE FROM webapp_sessions WHERE created < ?', (
                (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),))

    def user(self, uid, name=None):
        if name is not None:
            with self.db:
                self.db.execute('INSERT INTO users(id,name) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name', (uid, name))
        return self.db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()

    def state(self, uid, value):
        with self.db:
            self.db.execute('UPDATE users SET state=? WHERE id=?', (value, uid))

    def invite(self, uid):
        if self.user(uid)['pair']:
            raise ValueError('Вы уже связаны с партнёром.')
        token = secrets.token_urlsafe(18)
        with self.db:
            self.db.execute('DELETE FROM invites WHERE owner=?', (uid,))
            self.db.execute('INSERT INTO invites VALUES(?,?,?)', (token, uid, datetime.now(timezone.utc).isoformat()))
        return token

    def join(self, uid, token):
        with self.db:
            invite = self.db.execute('SELECT * FROM invites WHERE token=?', (token,)).fetchone()
            if not invite or invite['owner'] == uid:
                raise ValueError('Приглашение недействительно. Попросите партнёра создать новое.')
            age = datetime.now(timezone.utc) - datetime.fromisoformat(invite['created'])
            if age.total_seconds() > 86400:
                raise ValueError('Приглашение истекло. Попросите новое.')
            owner = self.user(invite['owner'])
            if self.user(uid)['pair'] or owner['pair']:
                raise ValueError('Один из участников уже состоит в паре.')
            pair = secrets.token_urlsafe(18)
            self.db.execute('UPDATE users SET pair=? WHERE id IN (?,?)', (pair, uid, owner['id']))
            self.db.execute('DELETE FROM invites WHERE owner IN (?,?)', (uid, owner['id']))

    def partner(self, uid):
        return self.db.execute('SELECT * FROM users WHERE pair=? AND id<>?', (self.user(uid)['pair'], uid)).fetchone()

    def wish(self, uid, wid):
        row = self.db.execute('SELECT * FROM wishes WHERE id=?', (wid,)).fetchone()
        if row:
            partner = self.partner(uid)
            if row['owner'] == uid or (partner and row['owner'] == partner['id']):
                return row
        raise ValueError('Желание недоступно.')

    def create_webapp_session(self, uid):
        token = secrets.token_urlsafe(24)
        created = datetime.now(timezone.utc).isoformat()
        with self.db:
            self.db.execute('DELETE FROM webapp_sessions WHERE uid=? OR created < ?', (uid, (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()))
            self.db.execute('INSERT INTO webapp_sessions(token,uid,created) VALUES(?,?,?)', (token, uid, created))
        return token

    def webapp_session_uid(self, token):
        row = self.db.execute('SELECT uid,created FROM webapp_sessions WHERE token=?', (token,)).fetchone()
        if not row:
            return None
        created = datetime.fromisoformat(row['created'])
        if datetime.now(timezone.utc) - created > timedelta(hours=24):
            with self.db:
                self.db.execute('DELETE FROM webapp_sessions WHERE token=?', (token,))
            return None
        return row['uid']

    def add(self, uid, title, url='', image='', price='', note='', source=''):
        if url:
            duplicate = self.db.execute('SELECT id FROM wishes WHERE owner=? AND url=? AND archived=0', (uid, url)).fetchone()
            if duplicate:
                return duplicate['id'], False
        with self.db:
            created = datetime.now(timezone.utc).isoformat()
            cur = self.db.execute('INSERT INTO wishes(owner,title,url,image,price,note,created,price_checked,source) VALUES(?,?,?,?,?,?,?,?,?)',
                                  (uid, title[:200], url, image, price[:100], note[:1500], created, created if price else '', source[:255]))
        return cur.lastrowid, True

    def change(self, uid, wid, field, value):
        row = self.wish(uid, wid)
        if row['owner'] != uid:
            raise ValueError('Изменять желание может только его автор.')
        if field not in {'title', 'note', 'price', 'scope', 'priority', 'archived', 'image', 'category', 'match_mode', 'size', 'color'}:
            raise ValueError('Неизвестное поле.')
        if field == 'scope' and value not in ('mine', 'shared'):
            raise ValueError('Неизвестный список.')
        if field == 'category' and value not in CATEGORIES:
            raise ValueError('Неизвестная категория.')
        if field == 'match_mode' and value not in MATCH_MODES:
            raise ValueError('Неизвестный вариант.')
        if field == 'priority' and value not in (0, 1, 2):
            raise ValueError('Неизвестный приоритет.')
        with self.db:
            self.db.execute(f'UPDATE wishes SET {field}=? WHERE id=?', (value, wid))
            if field == 'price':
                self.db.execute('UPDATE wishes SET price_checked=? WHERE id=?', (datetime.now(timezone.utc).isoformat() if value else '', wid))
            if field == 'scope':
                self.db.execute('UPDATE wishes SET claimed_by=NULL WHERE id=?', (wid,))

    def filters(self, uid, updates=None):
        key = f'filters:{uid}'
        result = {'query': '', 'category': '', 'budget': '', 'sort': 'priority'}
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
        filters = self.filters(uid)
        query = f'SELECT * FROM wishes WHERE owner IN ({",".join("?" for _ in ids)}) AND archived=?'
        args = ids + [int(kind == 'archive')]
        if kind != 'archive':
            query += ' AND scope=?'
            args.append('shared' if kind == 'shared' else 'mine')
        rows = list(self.db.execute(query + ' ORDER BY priority DESC,id DESC', args))
        term = filters['query'].casefold()
        if term:
            rows = [r for r in rows if term in ' '.join(str(r[k]) for k in ('title', 'note', 'size', 'color')).casefold()]
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
        with self.db:
            self.db.execute('UPDATE wishes SET claimed_by=? WHERE id=?', (None if row['claimed_by'] == uid else uid, wid))

    def listing(self, uid, kind, page=0):
        partner = self.partner(uid)
        ids = [uid] + ([partner['id']] if partner else [])
        if kind == 'partner':
            ids = [partner['id']] if partner else []
        elif kind == 'mine':
            ids = [uid]
        if not ids:
            return []
        marks = ','.join('?' for _ in ids)
        scope = 'shared' if kind == 'shared' else 'mine'
        query = f'SELECT * FROM wishes WHERE owner IN ({marks}) AND archived=?'
        args = ids + [int(kind == 'archive')]
        if kind != 'archive':
            query += ' AND scope=?'
            args.append(scope)
        query += ' ORDER BY priority DESC,id DESC LIMIT 6 OFFSET ?'
        return self.db.execute(query, args + [max(0, page) * 5]).fetchall()

    def setting(self, key, value=None):
        if value is not None:
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, str(value)))
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row['value'] if row else None

    def export(self, uid):
        return self.db.execute('''SELECT id,title,url,price,note,scope,priority,archived,created,category,match_mode,size,color,price_checked,source
                                  FROM wishes WHERE owner=? ORDER BY id''', (uid,)).fetchall()

    def update_metadata(self, wid, title, image, source=''):
        with self.db:
            self.db.execute('UPDATE wishes SET title=?,image=?,source=? WHERE id=?',
                            (title[:200], image, source[:255], wid))
