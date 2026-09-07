import os
import sqlite3
from pathlib import Path

from psycopg import connect
from psycopg.rows import dict_row

from .postgres_store import PostgresStore


def migrate(sqlite_path, database_url):
    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row

    # Creates/updates the cloud schema before copying data.
    cloud = PostgresStore(database_url)
    target = cloud.db._connection

    with target.cursor() as cur:
        for row in source.execute('SELECT id,name,pair,state FROM users'):
            cur.execute('''
                INSERT INTO users(id,name,pair,state) VALUES(%s,%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name,pair=excluded.pair,state=excluded.state
            ''', tuple(row))

        for row in source.execute('SELECT token,owner,created FROM invites'):
            cur.execute('''
                INSERT INTO invites(token,owner,created) VALUES(%s,%s,%s)
                ON CONFLICT(token) DO UPDATE SET owner=excluded.owner,created=excluded.created
            ''', tuple(row))

        columns = {r['name'] for r in source.execute('PRAGMA table_info(wishes)')}
        wanted = [
            'id','owner','scope','title','url','image','price','note','priority','archived',
            'claimed_by','created','category','match_mode','size','color','price_checked'
        ]
        defaults = {
            'category': 'other', 'match_mode': 'unspecified', 'size': '',
            'color': '', 'price_checked': ''
        }
        rows = source.execute('SELECT * FROM wishes').fetchall()
        for raw in rows:
            values = []
            for name in wanted:
                if name in columns:
                    value = raw[name]
                else:
                    value = defaults.get(name)
                if name == 'price_checked' and not value and raw['price']:
                    value = raw['created']
                values.append(value)
            cur.execute('''
                INSERT INTO wishes(
                  id,owner,scope,title,url,image,price,note,priority,archived,
                  claimed_by,created,category,match_mode,size,color,price_checked
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET
                  owner=excluded.owner,scope=excluded.scope,title=excluded.title,url=excluded.url,
                  image=excluded.image,price=excluded.price,note=excluded.note,priority=excluded.priority,
                  archived=excluded.archived,claimed_by=excluded.claimed_by,created=excluded.created,
                  category=excluded.category,match_mode=excluded.match_mode,size=excluded.size,
                  color=excluded.color,price_checked=excluded.price_checked
            ''', values)

        for row in source.execute('SELECT key,value FROM settings'):
            cur.execute('''
                INSERT INTO settings(key,value) VALUES(%s,%s)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            ''', tuple(row))

        if 'webapp_sessions' in {r['name'] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            for row in source.execute('SELECT token,uid,created FROM webapp_sessions'):
                cur.execute('''
                    INSERT INTO webapp_sessions(token,uid,created) VALUES(%s,%s,%s)
                    ON CONFLICT(token) DO UPDATE SET uid=excluded.uid,created=excluded.created
                ''', tuple(row))

        cur.execute("SELECT setval(pg_get_serial_sequence('wishes','id'), COALESCE((SELECT MAX(id) FROM wishes), 1), true)")

    source.close()
    print('Migration complete: SQLite -> Postgres')


def main():
    database_url = os.environ.get('DATABASE_URL', '').strip()
    if not database_url:
        raise SystemExit('Set DATABASE_URL first.')
    path = Path(os.environ.get('DATABASE_PATH', 'data/wishlist.sqlite3'))
    if not path.exists():
        raise SystemExit(f'SQLite database not found: {path}')
    migrate(path, database_url)


if __name__ == '__main__':
    main()
