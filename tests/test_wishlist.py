import json
import unittest
import tempfile
import base64
from pathlib import Path
from unittest.mock import patch

from wishlist.bot import Bot, card
from wishlist.metadata import clean_url, parse, public_address, fetch, extract, marketplace_fallback, _brightdata_ozon
from wishlist.store import Store


class WishlistTests(unittest.TestCase):
    def setUp(self):
        self.s = Store(':memory:')
        for uid in (1, 2, 3):
            self.s.user(uid, f'User {uid}')
        self.s.join(2, self.s.invite(1))

    def tearDown(self):
        self.s.db.close()

    def test_pair_is_limited_to_two_and_invite_is_single_use(self):
        with self.assertRaises(ValueError):
            self.s.invite(1)
        with self.assertRaises(ValueError):
            self.s.join(3, 'missing')
        self.assertEqual(self.s.partner(1)['id'], 2)

    def test_private_claim_does_not_change_recipient_card_or_export_fields(self):
        wid, _ = self.s.add(1, 'Gift')
        before = card(self.s, 1, self.s.wish(1, wid))
        self.s.claim(2, wid)
        self.assertEqual(before, card(self.s, 1, self.s.wish(1, wid)))
        self.assertIn('Вы планируете', card(self.s, 2, self.s.wish(2, wid))[0])
        self.s.claim(2, wid)
        self.assertIsNone(self.s.wish(1, wid)['claimed_by'])

    def test_unauthorized_read_and_edit(self):
        wid, _ = self.s.add(1, 'Gift')
        with self.assertRaises(ValueError):
            self.s.wish(3, wid)
        with self.assertRaises(ValueError):
            self.s.change(2, wid, 'title', 'Changed')
        with self.assertRaises(ValueError):
            self.s.claim(3, wid)

    def test_lists_archive_and_duplicates(self):
        wid, fresh = self.s.add(1, 'Gift', url='https://example.com/?size=M')
        self.assertTrue(fresh)
        self.assertEqual((wid, False), self.s.add(1, 'Again', url='https://example.com/?size=M'))
        self.assertTrue(self.s.add(1, 'Other size', url='https://example.com/?size=L')[1])
        self.s.change(1, wid, 'scope', 'shared')
        self.assertEqual(self.s.listing(2, 'shared')[0]['id'], wid)
        self.s.change(1, wid, 'archived', 1)
        self.assertEqual(len(self.s.listing(2, 'shared')), 0)
        self.assertEqual(self.s.listing(2, 'archive')[0]['id'], wid)

    def test_failed_fetch_preserves_wish_and_note(self):
        bot = Bot('unused', self.s)
        with patch.object(bot, 'api', return_value={}), patch('wishlist.bot.extract', side_effect=ValueError('blocked')):
            bot.message({'chat': {'id': 1, 'type': 'private'}, 'from': {'id': 1}, 'text': 'https://example.com/item Размер M'})
        row = self.s.listing(1, 'mine')[0]
        self.assertEqual(row['url'], 'https://example.com/item')
        self.assertEqual(row['note'], 'Размер M')

    def test_product_and_og_metadata(self):
        data = {'@graph': [{'@type': 'Product', 'name': '  Nice   gift ', 'image': ['/image.jpg'], 'offers': {'price': '99', 'priceCurrency': 'UAH'}}]}
        result = parse('<script type="application/ld+json">' + json.dumps(data) + '</script>', 'https://example.com/item')
        self.assertEqual(result, {'title': 'Nice gift', 'image': 'https://example.com/image.jpg', 'price': '99 UAH'})
        self.assertEqual(parse('<meta property="og:title" content="A &amp; B">', 'https://example.com')['title'], 'A & B')

    def test_microdata_and_extended_offer_metadata(self):
        html = ('<meta itemprop="name" content="Coffee machine">'
                '<meta itemprop="image" content="/coffee.jpg">'
                '<meta property="og:price:amount" content="12990">'
                '<meta property="og:price:currency" content="RUB">')
        self.assertEqual(parse(html, 'https://shop.example/item'), {
            'title': 'Coffee machine', 'image': 'https://shop.example/coffee.jpg',
            'price': '12990 RUB'})

        data = {'@type': 'Product', 'name': 'Chair', 'offers': {
            'lowPrice': '4999', 'priceCurrency': 'RUB'}}
        result = parse('<script type="application/ld+json">' + json.dumps(data) + '</script>',
                       'https://shop.example/chair')
        self.assertEqual(result['price'], '4999 RUB')

    def test_extract_records_final_store_domain(self):
        with patch('wishlist.metadata.fetch', return_value=('<title>Gift</title>', 'https://shop.example/product')):
            result = extract('https://start.example/item')
        self.assertEqual(result['source'], 'shop.example')

    def test_marketplace_fallbacks_when_pages_block_automated_requests(self):
        cases = {
            'https://www.ozon.ru/product/kubik-rubik-3x3-220937393/': 'Kubik rubik 3x3',
            'https://market.yandex.ru/product--umnye-chasy/123456': 'Умные часы',
            'https://www.poizon.com/product/teenmix-sneakers-627855963': 'Teenmix sneakers',
        }
        for url, title in cases.items():
            with self.subTest(url=url), patch('wishlist.metadata.fetch', side_effect=ValueError('blocked')):
                result = extract(url)
                self.assertEqual(result['title'], title)
                self.assertTrue(result['partial'])

    def test_wildberries_fallback_uses_public_product_assets(self):
        url = 'https://www.wildberries.ru/catalog/86035817/detail.aspx'
        with patch('wishlist.metadata._json', side_effect=[
                {'imt_name': 'Настольный вентилятор'},
                [{'price': {'RUB': 249900}}]]):
            result = marketplace_fallback(url)
        self.assertEqual(result['title'], 'Настольный вентилятор')
        self.assertEqual(result['image'], 'https://basket-05.wbbasket.ru/vol860/part86035/86035817/images/big/1.webp')
        self.assertEqual(result['price'], '2499.00 RUB')
        self.assertFalse(result['partial'])

    def test_short_marketplace_links_still_create_editable_drafts(self):
        cases = {
            'https://www.ozon.ru/t/abc123': 'Товар Ozon',
            'https://market.yandex.ru/cc/abc123': 'Товар с Яндекс Маркета',
        }
        for url, title in cases.items():
            with self.subTest(url=url), patch('wishlist.metadata.fetch', side_effect=ValueError('blocked')):
                result = extract(url)
                self.assertEqual(result['title'], title)
                self.assertTrue(result['partial'])

    def test_brightdata_ozon_maps_complete_product(self):
        response = unittest.mock.MagicMock()
        response.read.return_value = json.dumps([{
            'name': 'Платье BIDRESS', 'image': 'https://img.example/dress.jpg',
            'final_price': 4867, 'currency': 'RUB'
        }]).encode()
        response.__enter__.return_value = response
        with patch.dict('os.environ', {'BRIGHTDATA_API_TOKEN': 'secret'}), \
             patch('wishlist.metadata.build_opener') as opener:
            opener.return_value.open.return_value = response
            result = _brightdata_ozon('https://ozon.ru/t/n6N6ixZ')
        self.assertEqual(result['title'], 'Платье BIDRESS')
        self.assertEqual(result['image'], 'https://img.example/dress.jpg')
        self.assertEqual(result['price'], '4867 RUB')
        self.assertFalse(result['partial'])
        request = opener.return_value.open.call_args.args[0]
        self.assertNotIn('secret', request.full_url)

    def test_ozon_provider_failure_keeps_editable_fallback(self):
        with patch.dict('os.environ', {'BRIGHTDATA_API_TOKEN': 'secret'}), \
             patch('wishlist.metadata.fetch', side_effect=ValueError('blocked')), \
             patch('wishlist.metadata.build_opener', side_effect=OSError('offline')):
            result = extract('https://ozon.ru/t/n6N6ixZ')
        self.assertEqual(result['title'], 'Товар Ozon')
        self.assertTrue(result['partial'])

    def test_yandex_captcha_retains_product_title(self):
        target = 'https://market.yandex.ru/card/shvabra-s-otzhimom-i-vedrom/103760449703'
        encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip('=')
        result = marketplace_fallback(f'https://market.yandex.ru/showcaptcha?retpath={encoded}_deadbeef')
        self.assertEqual(result['title'], 'Швабра с отжимом и ведром')

    def test_initial_price_records_check_date_and_source(self):
        wid, _ = self.s.add(1, 'Gift', price='99 UAH', source='shop.example')
        row = self.s.wish(1, wid)
        self.assertEqual(row['source'], 'shop.example')
        self.assertTrue(row['price_checked'])

    def test_hostile_metadata_and_html_escape(self):
        self.assertEqual(parse('<script type="application/ld+json">bad json</script><title>Fallback</title>', 'https://example.com')['title'], 'Fallback')
        wid, _ = self.s.add(1, '<b>not markup</b>')
        self.assertIn('&lt;b&gt;', card(self.s, 1, self.s.wish(1, wid))[0])

    def test_internal_network_and_credentials_are_blocked(self):
        for url in ('file:///etc/passwd', 'http://user:pass@example.com', 'http://example.com:8080'):
            with self.assertRaises(ValueError):
                clean_url(url)
        for addr in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '::1'):
            with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', (addr, 0))]):
                with self.assertRaises(ValueError):
                    public_address('example.com')

    def test_pagination(self):
        for number in range(12):
            self.s.add(1, str(number))
        self.assertEqual(len(self.s.listing(1, 'mine', 0)), 6)
        self.assertEqual(len(self.s.listing(1, 'mine', 2)), 2)

    def test_invite_expiry_and_consumption(self):
        self.s.user(4, 'Fourth')
        token = self.s.invite(3)
        self.s.db.execute("UPDATE invites SET created='2020-01-01T00:00:00+00:00' WHERE token=?", (token,))
        with self.assertRaises(ValueError):
            self.s.join(4, token)
        token = self.s.invite(3)
        self.s.join(4, token)
        self.assertIsNone(self.s.db.execute('SELECT * FROM invites WHERE token=?', (token,)).fetchone())

    def test_persistence_after_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.sqlite3'
            first = Store(path)
            first.user(10, 'Name')
            wid, _ = first.add(10, 'Keep this')
            first.db.close()
            second = Store(path)
            self.assertEqual(second.wish(10, wid)['title'], 'Keep this')
            second.db.close()

    def test_redirect_to_internal_address_is_blocked(self):
        with patch('wishlist.metadata.public_address', side_effect=['93.184.216.34', ValueError('private')]), \
             patch('wishlist.metadata.socket.create_connection'), \
             patch('wishlist.metadata.http.client.HTTPConnection') as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 302
            response.getheader.return_value = 'http://127.0.0.1/secrets'
            with self.assertRaises(ValueError):
                fetch('http://example.com/item')
            self.assertEqual(connection.call_count, 1)

    def test_bot_edit_flow_and_export_do_not_leak_claim(self):
        wid, _ = self.s.add(1, 'Original')
        self.s.claim(2, wid)
        bot = Bot('unused', self.s)
        with patch.object(bot, 'api', return_value={}) as api:
            bot.callback({'id': 'q1', 'from': {'id': 1}, 'message': {'chat': {'type': 'private'}}, 'data': f'edit:note:{wid}'})
            bot.message({'chat': {'id': 1, 'type': 'private'}, 'from': {'id': 1}, 'text': 'Размер M, зелёный'})
            self.assertEqual(self.s.wish(1, wid)['note'], 'Размер M, зелёный')
            api.reset_mock()
            bot.message({'chat': {'id': 1, 'type': 'private'}, 'from': {'id': 1}, 'text': '/export'})
            output = str(api.call_args_list)
            self.assertNotIn('claimed_by', output)
            self.assertNotIn('Вы планируете', output)

    def test_bot_app_button_uses_fresh_session_and_production_url(self):
        bot = Bot('unused', self.s)
        bot.username = 'OurWishListbot_bot'
        with patch.dict('os.environ', {'WEBAPP_URL': 'https://ourwishlist-silk.vercel.app'}, clear=False), \
             patch.object(bot, 'api', return_value={}) as api:
            bot.webapp_url = 'https://ourwishlist-silk.vercel.app'
            bot.open_web_app(1)
        payload = api.call_args.kwargs
        url = payload['reply_markup']['inline_keyboard'][0][0]['web_app']['url']
        self.assertTrue(url.startswith('https://ourwishlist-silk.vercel.app?session='))
        token = url.split('session=', 1)[1].split('&', 1)[0]
        self.assertEqual(self.s.webapp_session_uid(token), 1)

    def test_opening_app_on_another_device_keeps_previous_session(self):
        first = self.s.create_webapp_session(1)
        second = self.s.create_webapp_session(1)
        self.assertNotEqual(first, second)
        self.assertEqual(self.s.webapp_session_uid(first), 1)
        self.assertEqual(self.s.webapp_session_uid(second), 1)

    def test_partner_can_join_through_start_link_once(self):
        owner_store = Store(':memory:')
        owner_store.user(10, 'Owner')
        owner_store.user(20, 'Partner')
        token = owner_store.invite(10)
        bot = Bot('unused', owner_store)
        with patch.object(bot, 'api', return_value={}) as api:
            bot.message({'chat': {'id': 20, 'type': 'private'}, 'from': {'id': 20, 'first_name': 'Partner'}, 'text': f'/start join_{token}'})
        self.assertEqual(owner_store.partner(10)['id'], 20)
        self.assertIn('Вы теперь пара', api.call_args.kwargs['text'])
        owner_store.db.close()


if __name__ == '__main__':
    unittest.main()
