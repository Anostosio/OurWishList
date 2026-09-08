import unittest
from unittest.mock import patch

from api.index import serial
from api.wish import create_values


class ApiSerializationTests(unittest.TestCase):
    def row(self, owner=10, claimed_by=None):
        return {
            'id': 1, 'title': 'Подарок', 'url': '', 'image': '', 'price': '',
            'note': '', 'priority': 1, 'category': 'other',
            'match_mode': 'unspecified', 'size': '', 'color': '',
            'scope': 'mine', 'created': '2026-09-08T00:00:00+00:00',
            'owner': owner, 'archived': 0, 'claimed_by': claimed_by,
            'price_checked': '', 'source': '',
        }

    def test_claim_is_visible_only_to_the_partner_who_made_it(self):
        row = self.row(claimed_by=20)
        self.assertFalse(serial(row, 'Автор', True, 10)['claimedByMe'])
        self.assertTrue(serial(row, 'Автор', False, 20)['claimedByMe'])
        self.assertFalse(serial(row, 'Автор', False, 30)['claimedByMe'])

    def test_manual_wish_payload_is_validated_before_insert(self):
        with patch('api.wish.clean_url', return_value='https://shop.example/gift'):
            values = create_values({'title': ' Gift ', 'url': 'https://shop.example/gift', 'scope': 'shared', 'category': 'home', 'matchMode': 'exact', 'priority': 2})
        self.assertEqual(values['title'], 'Gift')
        self.assertEqual(values['source'], 'shop.example')
        with self.assertRaises(ValueError):
            create_values({'title': 'Gift', 'category': 'unknown'})
        with self.assertRaises(ValueError):
            create_values({'title': 'Gift', 'priority': 9})


if __name__ == '__main__':
    unittest.main()
