"""Bounded public HTTP fetches; metadata is untrusted and optional."""
import http.client
import ipaddress
import json
import socket
import re
import ssl
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

LIMIT = 2_000_000


def clean_url(url):
    parts = urlsplit(url.strip())
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('Нужна обычная ссылка http или https.')
    if parts.port not in (None, 80, 443):
        raise ValueError('Этот порт не поддерживается.')
    # Preserve query and fragment: they can encode the selected product variant.
    return urlunsplit(parts)


def public_address(host):
    addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Разрешены только публичные адреса магазинов.')
    return addresses[0][4][0]


def fetch(url):
    for _ in range(4):
        url = clean_url(url)
        parts = urlsplit(url)
        host = parts.hostname.encode('idna').decode('ascii')
        address = public_address(host)
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        conn = http.client.HTTPConnection(host, port, timeout=6)
        try:
            # Pin the validated address, including across redirects, to avoid DNS rebinding.
            conn.sock = socket.create_connection((address, port), timeout=6)
            if parts.scheme == 'https':
                conn.sock = ssl.create_default_context().wrap_socket(conn.sock, server_hostname=host)
            path = parts.path or '/'
            if parts.query:
                path += '?' + parts.query
            conn.request('GET', path, headers={'User-Agent': 'OurWishList/0.1', 'Accept': 'text/html', 'Accept-Encoding': 'identity'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    raise ValueError('Пустой редирект.')
                url = urljoin(url, location)
                continue
            if response.status != 200 or 'html' not in response.getheader('Content-Type', '').lower():
                raise ValueError('Магазин не вернул страницу товара.')
            body = response.read(LIMIT + 1)
            if len(body) > LIMIT:
                raise ValueError('Страница слишком большая.')
            return body.decode('utf-8', errors='replace'), url
        finally:
            conn.close()
    raise ValueError('Слишком много перенаправлений.')


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta, self.blocks, self.title = {}, [], ''
        self.in_title = self.in_json = False
        self.block = ''

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            self.meta[attrs.get('property', attrs.get('name', ''))] = attrs.get('content', '')
        if tag == 'title':
            self.in_title = True
        if tag == 'script' and attrs.get('type', '').lower() == 'application/ld+json':
            self.in_json, self.block = True, ''

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.in_json:
            self.block += data

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False
        if tag == 'script' and self.in_json:
            self.blocks.append(self.block)
            self.in_json = False


def products(value):
    if isinstance(value, list):
        for item in value:
            yield from products(item)
    elif isinstance(value, dict):
        types = value.get('@type', [])
        if types == 'Product' or isinstance(types, list) and 'Product' in types:
            yield value
        if '@graph' in value:
            yield from products(value['@graph'])


def parse(html, url):
    parser = MetadataParser()
    parser.feed(html)
    product = {}
    for block in parser.blocks:
        try:
            found = list(products(json.loads(block)))
            if found:
                product = found[0]
                break
        except (ValueError, RecursionError):
            continue
    title = product.get('name') or parser.meta.get('og:title') or parser.title or urlsplit(url).hostname
    picture = product.get('image') or parser.meta.get('og:image', '')
    if isinstance(picture, list):
        picture = picture[0] if picture else ''
    if isinstance(picture, dict):
        picture = picture.get('url', '')
    offer = product.get('offers', {})
    if isinstance(offer, list):
        offer = offer[0] if offer else {}
    if not isinstance(offer, dict):
        offer = {}
    price = offer.get('price', parser.meta.get('product:price:amount', ''))
    currency = offer.get('priceCurrency', parser.meta.get('product:price:currency', ''))
    return {'title': ' '.join(str(title).split())[:200],
            'image': urljoin(url, picture) if isinstance(picture, str) and picture else '',
            'price': f'{price} {currency}'.strip() if price != '' and price is not None else ''}


def _wb_basket(volume):
    limits = (143, 287, 431, 719, 1007, 1061, 1115, 1169, 1313, 1601, 1655,
              1919, 2045, 2189, 2405, 2621, 2837, 3053, 3269, 3485, 3701, 3917,
              4133, 4349)
    for number, maximum in enumerate(limits, 1):
        if volume <= maximum:
            return number
    return 25


def _json(url):
    parts = urlsplit(clean_url(url))
    host = parts.hostname.encode('idna').decode('ascii')
    address = public_address(host)
    port = parts.port or 443
    conn = http.client.HTTPConnection(host, port, timeout=6)
    try:
        conn.sock = socket.create_connection((address, port), timeout=6)
        conn.sock = ssl.create_default_context().wrap_socket(conn.sock, server_hostname=host)
        path = parts.path + (('?' + parts.query) if parts.query else '')
        conn.request('GET', path, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Accept-Encoding': 'identity'})
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError('Источник не вернул данные.')
        body = response.read(500_001)
        if len(body) > 500_000:
            raise ValueError('Ответ источника слишком большой.')
        return json.loads(body.decode('utf-8'))
    finally:
        conn.close()


def _slug_title(value):
    value = unquote(value).replace('-', ' ').replace('_', ' ')
    value = re.sub(r'\s+', ' ', value).strip(' /')
    return value[:1].upper() + value[1:200] if value else ''


def marketplace_fallback(url):
    """Return safe best-effort fields when a marketplace blocks page scraping."""
    parts = urlsplit(clean_url(url))
    host, path = parts.hostname.lower(), parts.path
    result = {'title': '', 'image': '', 'price': '', 'source': host, 'partial': True}
    if host.endswith('wildberries.ru'):
        match = re.search(r'/catalog/(\d+)', path)
        if not match:
            return None
        item = int(match.group(1)); volume, part = item // 100000, item // 1000
        basket = _wb_basket(volume)
        base = f'https://basket-{basket:02d}.wbbasket.ru/vol{volume}/part{part}/{item}'
        try:
            card = _json(base + '/info/ru/card.json')
            result['title'] = str(card.get('imt_name') or card.get('subj_name') or '').strip()[:200]
        except Exception:
            pass
        result['title'] = result['title'] or f'Товар Wildberries №{item}'
        result['image'] = base + '/images/big/1.webp'
        return result
    patterns = []
    if host.endswith('ozon.ru'):
        patterns = [r'/product/(.+?)-\d+/?$']
    elif host.endswith('market.yandex.ru'):
        patterns = [r'/product--([^/]+)/\d+', r'/card/([^/]+)/\d+']
    elif host.endswith('poizon.com'):
        patterns = [r'/product/(.+?)-\d+/?$', r'/product/([^/]+)/?$']
    else:
        return None
    for pattern in patterns:
        match = re.search(pattern, path)
        if match:
            result['title'] = _slug_title(match.group(1))
            break
    return result if result['title'] else None


def extract(url):
    fallback = marketplace_fallback(url)
    try:
        html, final = fetch(url)
        result = parse(html, final)
        result['source'] = urlsplit(final).hostname or ''
        redirected = urlsplit(final).path != urlsplit(url).path
        if fallback:
            if redirected or not result.get('title') or (not result.get('image') and not result.get('price')):
                result['title'] = fallback.get('title') or result.get('title', '')
            if not result.get('image'):
                result['image'] = fallback.get('image', '')
            result['partial'] = not bool(result.get('price'))
            result['source'] = fallback.get('source') or result['source']
        return result
    except ValueError:
        if fallback:
            return fallback
        raise
