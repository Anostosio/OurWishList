import os
from pathlib import Path

from .store import Store


def open_store():
    database_url = os.environ.get('DATABASE_URL', '').strip()
    if database_url:
        from .postgres_store import PostgresStore
        return PostgresStore(database_url)

    path = Path(os.environ.get('DATABASE_PATH', 'data/wishlist.sqlite3'))
    path.parent.mkdir(parents=True, exist_ok=True)
    return Store(path)
