import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wishlist.bot import Bot, card
from wishlist.catalog import money
from wishlist.store import Store


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        for uid in (1, 2, 3):
            self.store.user(uid, f'User {uid}')
        self.store.join(2, self.store.invite(1))
        self.bot = Bot('not-a-real-token', self.store)

    def tearDown(self):
        self.store.db.close()

    def message(self, text='', uid=1, **extra):
        return {'chat': {'id': uid, 'type': 'private'}, 'from': {'id': uid}, 'text': text, **extra}

    def callback(self, data, uid=1, photo=False):
        message = {'chat': {'id': uid, 'type': 'private'}, 'message_id': 44}
        if photo:
            message['photo'] = [{'file_id': 'old-photo'}]
        return {'id': 'query', 'from': {'id': uid}, 'message': message, 'data': data}

    def test_currency_parsing_and_ranges(self):
        for value, expected in [('1 890 ₴', ('1890', 'UAH')), ('€49,95', ('49.95', 'EUR')),
                                ('1,299.00 USD', ('1299.00', 'USD')), ('1.299,99 EUR', ('1299.99', 'EUR'))]:
            result = money(value)
            self.assertEqual((str(result[0]), result[1]), expected)
        for value in ('2000', 'от 500 UAH', '500–1000 UAH', '99 USD / 90 EUR', '-20 USD', 'нет цены'):
            self.assertIsNone(money(value))

    def test_budget_never_compares_different_currencies_or_unknown_prices(self):
        for title, price in [('UAH', '100 UAH'), ('USD', '100 USD'), ('none', ''), ('range', 'от 20 UAH'), ('expensive', '500 UAH')]:
            self.store.add(1, title, price=price)
        self.store.filters(2, {'budget': '200 UAH'})
        self.assertEqual([r['title'] for r in self.store.browse(2, 'partner')], ['UAH'])

    def test_cyrillic_search_and_independent_user_filters(self):
        wid, _ = self.store.add(1, 'Зелёная рубашка')
        self.store.change(1, wid, 'size', 'M')
        self.store.change(1, wid, 'category', 'clothes')
        self.store.add(1, 'Книга')
        self.store.filters(2, {'query': 'РУБАШКА', 'category': 'clothes'})
        self.assertEqual([r['id'] for r in self.store.browse(2, 'partner')], [wid])
        self.assertEqual(len(self.store.browse(1, 'mine')), 2)
        self.assertEqual(self.store.browse(3, 'partner'), [])

    def test_gallery_does_not_change_when_partner_reserves(self):
        wid, _ = self.store.add(1, 'Book')
        with patch.object(self.bot, 'api', return_value={}) as api:
            self.bot.gallery(1, 'mine')
            before = api.call_args
            self.store.claim(2, wid)
            self.bot.gallery(1, 'mine')
            self.assertEqual(before, api.call_args)

    def test_upload_and_replace_photo(self):
        with patch.object(self.bot, 'api', return_value={}) as api:
            self.bot.message(self.message(photo=[{'file_id': 'small', 'width': 50, 'height': 50}, {'file_id': 'large', 'width': 800, 'height': 800}], caption='Красная сумка'))
            row = self.store.browse(1, 'mine')[0]
            self.assertEqual(row['image'], 'tg:large')
            self.assertEqual(api.call_args.kwargs['photo'], 'large')
            self.bot.callback(self.callback(f'edit:image:{row["id"]}'))
            self.bot.message(self.message(photo=[{'file_id': 'replacement'}]))
            self.assertEqual(self.store.wish(1, row['id'])['image'], 'tg:replacement')
            self.assertEqual(len(self.store.browse(1, 'mine')), 1)

    def test_foreign_user_cannot_edit_new_fields_or_photos(self):
        wid, _ = self.store.add(1, 'Gift')
        with patch.object(self.bot, 'api', return_value={}):
            for data in (f'edit:image:{wid}', f'setcategory:{wid}:home', f'setmatch:{wid}:alternative', f'refresh:{wid}'):
                with self.assertRaises(ValueError):
                    self.bot.callback(self.callback(data, uid=2))

    def test_gallery_replaces_photo_instead_of_only_caption(self):
        self.store.add(1, 'Gift', image='tg:new-photo')
        with patch.object(self.bot, 'api', return_value={}) as api:
            self.bot.callback(self.callback('gal:mine:0', photo=True))
            self.assertEqual(api.call_args.args[0], 'editMessageMedia')
            self.assertEqual(api.call_args.kwargs['media']['media'], 'new-photo')

    def test_photo_to_text_transition_does_not_keep_wrong_image(self):
        self.store.add(1, 'Wish without photo')
        with patch.object(self.bot, 'api', return_value={}) as api:
            self.bot.callback(self.callback('gal:mine:0', photo=True))
            self.assertEqual(api.call_args.args[0], 'sendMessage')

    def test_search_and_budget_messages_are_not_saved_as_wishes(self):
        self.store.add(1, 'Book', price='10 EUR')
        with patch.object(self.bot, 'api', return_value={}):
            self.bot.callback(self.callback('search:mine'))
            self.bot.message(self.message('book'))
            self.bot.callback(self.callback('budget:mine'))
            with self.assertRaises(ValueError):
                self.bot.message(self.message('100'))
            self.assertEqual(self.store.user(1)['state'], 'budget:mine')
            self.bot.message(self.message('50 EUR'))
        self.assertEqual(len(self.store.browse(1, 'mine')), 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM wishes').fetchone()[0], 1)

    def test_refresh_failure_preserves_price_and_success_preserves_user_details(self):
        wid, _ = self.store.add(1, 'My title', url='https://example.com', price='50 EUR', image='tg:custom', note='Keep note')
        with patch.object(self.bot, 'api', return_value={}):
            with patch('wishlist.bot.extract', side_effect=ValueError('offline')):
                self.bot.refresh(1, wid)
            self.assertEqual(self.store.wish(1, wid)['price'], '50 EUR')
            with patch('wishlist.bot.extract', return_value={'price': '40 EUR', 'title': 'Store title', 'image': 'other'}):
                self.bot.refresh(1, wid)
        row = self.store.wish(1, wid)
        self.assertEqual((row['title'], row['image'], row['note'], row['price']), ('My title', 'tg:custom', 'Keep note', '40 EUR'))
        self.assertTrue(row['price_checked'])

    def test_legacy_database_migrates_without_losing_claims(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'legacy.sqlite3'
            db = sqlite3.connect(path)
            db.executescript('''
                CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, pair TEXT, state TEXT);
                CREATE TABLE wishes(id INTEGER PRIMARY KEY, owner INTEGER NOT NULL, scope TEXT NOT NULL DEFAULT 'mine',
                    title TEXT NOT NULL, url TEXT DEFAULT '', image TEXT DEFAULT '', price TEXT DEFAULT '',
                    note TEXT DEFAULT '', priority INTEGER DEFAULT 1, archived INTEGER DEFAULT 0, claimed_by INTEGER, created TEXT NOT NULL);
                INSERT INTO users VALUES(1,'One','pair',NULL),(2,'Two','pair',NULL);
                INSERT INTO wishes(id,owner,title,price,claimed_by,created) VALUES(42,1,'Existing','50 EUR',2,'2026-09-06');
            ''')
            db.close()
            for _ in range(2):
                migrated = Store(path)
                row = migrated.wish(1, 42)
                self.assertEqual((row['title'], row['claimed_by'], row['category']), ('Existing', 2, 'other'))
                self.assertEqual(row['price_checked'], '2026-09-06')
                migrated.db.close()


if __name__ == '__main__':
    unittest.main()
