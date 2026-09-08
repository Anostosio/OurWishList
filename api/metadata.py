import json
import os
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

from wishlist.metadata import (BRIGHTDATA_COLLECTORS, brightdata_poll,
                               brightdata_trigger, clean_url, extract)
from wishlist.storage import open_store
from wishlist.web_auth import authorized_uid


class handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return

    def _send(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split('?', 1)[0] != '/api/metadata':
            return self._send({'ok': False, 'error': 'Not found'}, 404)
        if not os.environ.get('DATABASE_URL', '').strip():
            return self._send({'ok': False, 'error': 'Cloud database is not configured'}, 503)
        try:
            length = int(self.headers.get('Content-Length', '0') or 0)
            if length > 20_000:
                return self._send({'ok': False, 'error': 'Слишком большой запрос'}, 413)
            payload = json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            return self._send({'ok': False, 'error': 'Bad JSON'}, 400)

        url = str(payload.get('url') or '').strip()
        try:
            store = open_store()
            if not authorized_uid(store, payload.get('session'), payload.get('initData')):
                return self._send({'ok': False, 'error': 'Сессия истекла. Откройте приложение заново через бота.'}, 401)
            url = clean_url(url)
            action = str(payload.get('action') or '')
            if action == 'poll':
                ticket = str(payload.get('ticket') or '')
                try:
                    job_id, source, signature = ticket.split('~', 2)
                except ValueError:
                    return self._send({'ok': False, 'error': 'Некорректная задача'}, 400)
                key = str(os.environ.get('BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN') or '').encode()
                expected = hmac.new(key, f'{job_id}.{source}'.encode(), hashlib.sha256).hexdigest()
                if not key or not hmac.compare_digest(signature, expected):
                    return self._send({'ok': False, 'error': 'Некорректная задача'}, 400)
                ready, data = brightdata_poll(job_id, source)
                if not ready:
                    return self._send({'ok': True, 'pending': True}, 202)
                if data:
                    return self._send({'ok': True, 'url': url, **data})
                return self._send({'ok': False, 'error': 'Заполните карточку вручную.'}, 422)

            host = (urlsplit(url).hostname or '').lower()
            async_domains = ('letu.ru', 'goldapple.ru')
            domain = next((d for d in async_domains if host == d or host.endswith('.' + d)), '')
            if domain:
                job_id = brightdata_trigger(url, BRIGHTDATA_COLLECTORS.get(domain, ''))
                if job_id:
                    key = str(os.environ.get('BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN') or '').encode()
                    signature = hmac.new(key, f'{job_id}.{host}'.encode(), hashlib.sha256).hexdigest()
                    return self._send({'ok': True, 'pending': True,
                                       'ticket': f'{job_id}~{host}~{signature}', 'url': url}, 202)
            data = extract(url)
            return self._send({'ok': True, 'url': url, **data})
        except ValueError:
            return self._send({
                'ok': False,
                'error': 'Не получилось прочитать магазин. Заполните карточку вручную.'
            }, 422)
        except Exception:
            return self._send({
                'ok': False,
                'error': 'Магазин временно недоступен. Заполните карточку вручную.'
            }, 502)
