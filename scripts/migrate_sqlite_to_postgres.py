import os
import sqlite3
from pathlib import Path

from wishlist.postgres_store import PostgresStore


def main():
    database_url = os.environ.get('DATABASE_URL', '').strip()
    if not database_url:
        raise SystemExit('DATABASE_URL is not set')

    sqlite_path = Path(os.environ.get('DATABASE_PATH', 'data/wishlist.sqlite3'))
    if not sqlite_path.exists():
        raise SystemExit(f'SQLite database not found: {sqlite_path}')

    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row
    target = PostgresStore(database_url)

    tables = ['users', 'invites', 'wishes', 'settings', 'webapp_sessions']
    counts = {}

    with target.db.cursor() as cur:
        for row in source.execute('SELECT * FROM users'):
            cur.execute(
                '''INSERT INTO users(id,name,pair,state) VALUES(%s,%s,%s,%s)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,pair=excluded.pair,state=excluded.state''',
                (row['id'], row['name'], row['pair'], row['state'])
            )

        for row in source.execute('SELECT * FROM invites'):
            cur.execute(
                '''INSERT INTO invites(token,owner,created) VALUES(%s,%s,%s)
                   ON CONFLICT(token) DO UPDATE SET owner=excluded.owner,created=excluded.created''',
                (row['token'], row['owner'], row['created'])
            )

        wish_columns = [r['name'] for r in source.execute('PRAGMA table_info(wishes)')]
        defaults = {
            'category': 'other', 'match_mode': 'unspecified', 'size': '',
            'color': '', 'price_checked': ''
        }
        for row in source.execute('SELECT * FROM wishes'):
            data = dict(row)
            for key, value in defaults.items():
                data.setdefault(key, value)
            cur.execute(
                '''INSERT INTO wishes(
                       id,owner,scope,title,url,image,price,note,priority,archived,claimed_by,created,
                       category,match_mode,size,color,price_checked
                   ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(id) DO UPDATE SET
                       owner=excluded.owner,scope=excluded.scope,title=excluded.title,url=excluded.url,
                       image=excluded.image,price=excluded.price,note=excluded.note,priority=excluded.priority,
                       archived=excluded.archived,claimed_by=excluded.claimed_by,created=excluded.created,
                       category=excluded.category,match_mode=excluded.match_mode,size=excluded.size,
                       color=excluded.color,price_checked=excluded.price_checked''',
                (
                    data['id'], data['owner'], data.get('scope', 'mine'), data['title'], data.get('url', ''),
                    data.get('image', ''), data.get('price', ''), data.get('note', ''), data.get('priority', 1),
                    data.get('archived', 0), data.get('claimed_by'), data['created'], data['category'],
                    data['match_mode'], data['size'], data['color'], data['price_checked']
                )
            )

        for row in source.execute('SELECT * FROM settings'):
            cur.execute(
                '''INSERT INTO settings(key,value) VALUES(%s,%s)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value''',
                (row['key'], row['value'])
            )

        for row in source.execute('SELECT * FROM webapp_sessions'):
            cur.execute(
                '''INSERT INTO webapp_sessions(token,uid,created) VALUES(%s,%s,%s)
                   ON CONFLICT(token) DO UPDATE SET uid=excluded.uid,created=excluded.created''',
                (row['token'], row['uid'], row['created'])
            )

        cur.execute("SELECT setval(pg_get_serial_sequence('wishes','id'), COALESCE((SELECT MAX(id) FROM wishes), 1), true)")

        for table in tables:
            sqlite_count = source.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            cur.execute(f'SELECT COUNT(*) AS count FROM {table}')
            postgres_count = cur.fetchone()['count']
            counts[table] = (sqlite_count, postgres_count)

    source.close()
    target.db.close()

    print('migration=ok')
    for table, (sqlite_count, postgres_count) in counts.items():
        print(f'{table}: sqlite={sqlite_count} postgres={postgres_count}')

    mismatched = [name for name, pair in counts.items() if pair[0] != pair[1]]
    if mismatched:
        raise SystemExit('count mismatch: ' + ', '.join(mismatched))


if __name__ == '__main__':
    main()
