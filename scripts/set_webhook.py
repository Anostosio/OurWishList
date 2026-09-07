import json
import os
from urllib.request import Request, urlopen

from wishlist.bot import load_env


def main():
    load_env()
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    base = os.environ.get('WEBAPP_URL', '').strip().rstrip('/')
    secret = os.environ.get('TELEGRAM_WEBHOOK_SECRET', '').strip()
    if not token or not base:
        raise SystemExit('Set TELEGRAM_BOT_TOKEN and WEBAPP_URL first.')
    if not secret:
        raise SystemExit('Set TELEGRAM_WEBHOOK_SECRET first.')

    payload = json.dumps({
        'url': base + '/telegram/webhook',
        'secret_token': secret,
        'allowed_updates': ['message', 'callback_query'],
        'drop_pending_updates': False
    }).encode('utf-8')
    request = Request(
        f'https://api.telegram.org/bot{token}/setWebhook',
        data=payload,
        headers={'Content-Type': 'application/json'}
    )
    with urlopen(request, timeout=30) as response:
        result = json.load(response)
    if not result.get('ok'):
        raise SystemExit('Telegram rejected webhook setup.')
    print('Webhook configured:', base + '/telegram/webhook')


if __name__ == '__main__':
    main()
