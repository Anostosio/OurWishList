"""Small explicit catalogue vocabulary and conservative money parsing."""
import re
from decimal import Decimal, InvalidOperation

CATEGORIES = {
    'other': 'Без категории', 'home': '🏡 Дом', 'clothes': '👕 Одежда',
    'tech': '🎧 Техника', 'beauty': '🧴 Красота', 'books': '📚 Книги',
    'hobby': '🎨 Хобби', 'experience': '🎟 Впечатления', 'travel': '🧳 Путешествия',
}
MATCH_MODES = {'unspecified': 'Вариант не уточнён', 'exact': '🎯 Именно это', 'alternative': '🔄 Можно аналог'}
KINDS = {'mine': '💛 Моё', 'partner': '🎁 Партнёра', 'shared': '🏡 Наше', 'archive': '✨ Сбылось'}
CURRENCIES = {'UAH': ('UAH', 'ГРН', '₴'), 'USD': ('USD', '$'), 'EUR': ('EUR', '€'),
              'RUB': ('RUB', 'РУБ', '₽'), 'GBP': ('GBP', '£'), 'PLN': ('PLN', 'ZŁ')}


def money(text):
    """Return an unambiguous amount/currency, never guess or convert currency."""
    value = str(text).strip().upper().replace('\u00a0', ' ').replace('\u202f', ' ')
    currencies = [(code, alias) for code, aliases in CURRENCIES.items() for alias in aliases if alias in value]
    if len(currencies) != 1:
        return None
    code, alias = currencies[0]
    value = value.replace(alias, '').strip()
    if not re.fullmatch(r'\d+(?:[ .,]\d+)*', value):
        return None
    value = value.replace(' ', '')
    if ',' in value and '.' in value:
        decimal = ',' if value.rfind(',') > value.rfind('.') else '.'
        thousands = '.' if decimal == ',' else ','
        value = value.replace(thousands, '').replace(decimal, '.')
    elif ',' in value or '.' in value:
        separator = ',' if ',' in value else '.'
        pieces = value.split(separator)
        if len(pieces) == 2 and len(pieces[1]) in (1, 2):
            value = value.replace(separator, '.')
        elif all(len(piece) == 3 for piece in pieces[1:]):
            value = ''.join(pieces)
        else:
            return None
    try:
        amount = Decimal(value)
        if amount < 0 or amount > Decimal('1000000000000'):
            return None
        return amount, code
    except InvalidOperation:
        return None
