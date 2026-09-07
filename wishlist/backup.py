"""Consistent SQLite backup, also safe while the bot is running."""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .bot import load_env


def main():
    load_env()
    path = Path(os.environ.get('DATABASE_PATH', 'data/wishlist.sqlite3')).resolve()
    if not path.is_file():
        raise SystemExit('Database not found; backup skipped.')
    directory = path.parent / 'backups'
    directory.mkdir(parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime('wishlist-%Y%m%d-%H%M%S-%f.sqlite3')
    target = directory / name
    source = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
        if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Backup integrity check failed.')
    finally:
        source.close()
        destination.close()
    print(f'Backup ready: {target}')


if __name__ == '__main__':
    main()
