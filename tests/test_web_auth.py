import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import urlencode

from wishlist.store import Store
from wishlist.web_auth import authorized_uid, telegram_user


def signed_data(token, user):
    values = {'auth_date': str(int(time.time())), 'query_id': 'test', 'user': json.dumps(user, separators=(',', ':'))}
    check = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    values['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


class WebAuthTests(unittest.TestCase):
    def test_signed_telegram_user_authenticates_without_browser_session(self):
        token = 'test-token'
        data = signed_data(token, {'id': 123, 'first_name': 'Анна', 'last_name': 'Тест'})
        store = Store(':memory:')
        try:
            self.assertEqual(authorized_uid(store, '', data, token), 123)
            self.assertEqual(store.user(123)['name'], 'Анна Тест')
        finally:
            store.db.close()

    def test_tampered_or_expired_telegram_data_is_rejected(self):
        token = 'test-token'
        data = signed_data(token, {'id': 123, 'first_name': 'Анна'})
        data = data[:-1] + ('0' if data[-1] != '0' else '1')
        self.assertIsNone(telegram_user(data, token))
        old = signed_data(token, {'id': 123, 'first_name': 'Анна'}).replace(str(int(time.time())), '1')
        self.assertIsNone(telegram_user(old, token))


if __name__ == '__main__':
    unittest.main()
