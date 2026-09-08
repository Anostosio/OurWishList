import unittest

from api.index import serial


class ApiSerializationTests(unittest.TestCase):
    def row(self, owner=10, claimed_by=None):
        return {
            'id': 1, 'title': 'Подарок', 'url': '', 'image': '', 'price': '',
            'note': '', 'priority': 1, 'category': 'other',
            'match_mode': 'unspecified', 'size': '', 'color': '',
            'scope': 'mine', 'created': '2026-09-08T00:00:00+00:00',
            'owner': owner, 'archived': 0, 'claimed_by': claimed_by,
        }

    def test_claim_is_visible_only_to_the_partner_who_made_it(self):
        row = self.row(claimed_by=20)
        self.assertFalse(serial(row, 'Автор', True, 10)['claimedByMe'])
        self.assertTrue(serial(row, 'Автор', False, 20)['claimedByMe'])
        self.assertFalse(serial(row, 'Автор', False, 30)['claimedByMe'])


if __name__ == '__main__':
    unittest.main()
