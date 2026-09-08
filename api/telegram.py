import json
import os
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from wishlist.bot import Bot
from wishlist.store import Store


class handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return

    def _send(self, payload, status=200):
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        return self._send({
            'ok': True,
            'telegram': bool(os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()),
            'database': bool(os.environ.get('DATABASE_URL', '').strip())
        })

    def do_POST(self):
        secret = os.environ.get('TELEGRAM_WEBHOOK_SECRET', '').strip()
        if secret:
            received = self.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
            if received != secret:
                return self._send({'ok': False, 'error': 'Forbidden'}, 403)

        token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
        database_url = os.environ.get('DATABASE_URL', '').strip()
        if not token or not database_url:
            return self._send({'ok': False, 'error': 'Server is not configured'}, 503)

        try:
            length = int(self.headers.get('Content-Length', '0') or 0)
            update = json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            return self._send({'ok': False, 'error': 'Bad JSON'}, 400)

        store = Store(Path('/tmp/wishlist.sqlite3'))
        bot = Bot(token, store)
        bot.username = os.environ.get('TELEGRAM_BOT_USERNAME', '').strip()
        if not bot.username:
            try:
                bot.username = bot.api('getMe')['username']
            except Exception:
                bot.username = ''

        uid = None
        try:
            if 'callback_query' in update:
                query = update['callback_query']
                uid = query['from']['id']
                store.user(uid, query['from'].get('first_name', 'Участник'))
                bot.callback(query)
            elif 'message' in update:
                uid = update['message']['chat']['id']
                bot.message(update['message'])
        except ValueError as error:
            if uid:
                try:
                    bot.say(uid, str(error))
                except Exception:
                    pass
        except Exception:
            if uid:
                try:
                    bot.say(uid, 'Не удалось завершить действие. Откройте /menu и попробуйте снова.')
                except Exception:
                    pass

        return self._send({'ok': True})
