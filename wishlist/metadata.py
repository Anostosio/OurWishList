"""Bounded public HTTP fetches; metadata is untrusted and optional."""
import http.client
import ipaddress
import json
import base64
import binascii
import socket
import re
import ssl
import os
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

LIMIT = 2_000_000
BRIGHTDATA_OZON_DATASET = 'gd_lutq85sl13rlndbzai'
BRIGHTDATA_COLLECTORS = {
    'market.yandex.ru': 'c_mtsxfbf82lhz6y1jjo',
    'mvideo.ru': 'c_mtsxpv772ly5ie73jp',
}


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
            conn.request('GET', path, headers={'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36', 'Accept': 'text/html', 'Accept-Encoding': 'identity'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    raise ValueError('Пустой редирект.')
                url = urljoin(url, location)
                continue
            if response.status != 200 or 'html' not in response.getheader('Content-Type', '').lower():
                raise ValueError('Магазин не вернул страницу товара.')
            # Product metadata normally lives in <head>. Read a bounded prefix
            # instead of rejecting modern storefront pages that are several MB.
            body = response.read(LIMIT)
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
            key = attrs.get('property') or attrs.get('name') or attrs.get('itemprop') or ''
            if key and attrs.get('content') and key not in self.meta:
                self.meta[key] = attrs['content']
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
    title = (product.get('name') or parser.meta.get('og:title') or
             parser.meta.get('twitter:title') or parser.meta.get('name') or
             parser.title or urlsplit(url).hostname)
    picture = (product.get('image') or parser.meta.get('og:image') or
               parser.meta.get('og:image:url') or parser.meta.get('twitter:image') or
               parser.meta.get('image', ''))
    if isinstance(picture, list):
        picture = picture[0] if picture else ''
    if isinstance(picture, dict):
        picture = picture.get('url', '')
    offer = product.get('offers', {})
    if isinstance(offer, list):
        offer = offer[0] if offer else {}
    if not isinstance(offer, dict):
        offer = {}
    specification = offer.get('priceSpecification', {})
    if isinstance(specification, list):
        specification = specification[0] if specification else {}
    if not isinstance(specification, dict):
        specification = {}
    price = (offer.get('price') or offer.get('lowPrice') or
             specification.get('price') or parser.meta.get('product:price:amount') or
             parser.meta.get('og:price:amount') or parser.meta.get('price') or '')
    currency = (offer.get('priceCurrency') or specification.get('priceCurrency') or
                parser.meta.get('product:price:currency') or
                parser.meta.get('og:price:currency') or parser.meta.get('priceCurrency') or '')
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


def _russian_slug_title(value):
    text = _slug_title(value).lower()
    for latin, cyrillic in (
        ('shch', 'щ'), ('yo', 'ё'), ('zh', 'ж'), ('kh', 'х'), ('ts', 'ц'),
        ('ch', 'ч'), ('sh', 'ш'), ('yu', 'ю'), ('ya', 'я'),
        ('yy', 'ый'), ('iy', 'ий')
    ):
        text = text.replace(latin, cyrillic)
    table = str.maketrans({
        'a': 'а', 'b': 'б', 'v': 'в', 'g': 'г', 'd': 'д', 'e': 'е',
        'z': 'з', 'i': 'и', 'j': 'й', 'k': 'к', 'l': 'л', 'm': 'м',
        'n': 'н', 'o': 'о', 'p': 'п', 'r': 'р', 's': 'с', 't': 'т',
        'u': 'у', 'f': 'ф', 'h': 'х', 'c': 'к', 'y': 'ы', 'x': 'кс',
        'q': 'к', 'w': 'в'
    })
    text = text.translate(table)
    return text[:1].upper() + text[1:200] if text else ''


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(clean_url(newurl))
        public_address(parts.hostname)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _known_short_target(url):
    parts = urlsplit(url)
    if not (parts.hostname or '').endswith('market.yandex.ru') or not parts.path.startswith('/cc/'):
        return ''
    try:
        request = Request(url, method='HEAD', headers={'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36'})
        with build_opener(_SafeRedirect()).open(request, timeout=6) as response:
            return response.geturl()
    except Exception:
        return ''


def marketplace_fallback(url):
    """Return safe best-effort fields when a marketplace blocks page scraping."""
    parts = urlsplit(clean_url(url))
    host, path = parts.hostname.lower(), parts.path
    result = {'title': '', 'image': '', 'price': '', 'source': host, 'partial': True}
    if host.endswith('market.yandex.ru') and path == '/showcaptcha':
        encoded = parse_qs(parts.query).get('retpath', [''])[0]
        if encoded:
            try:
                raw = encoded.rsplit('_', 1)[0]
                target = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)).decode('utf-8')
                nested = marketplace_fallback(target)
                if nested:
                    return nested
            except (ValueError, UnicodeDecodeError, binascii.Error):
                pass
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
        try:
            history = _json(base + '/info/price-history.json')
            current = history[-1].get('price', {}).get('RUB') if isinstance(history, list) and history else None
            if isinstance(current, (int, float)):
                result['price'] = f'{current / 100:.2f} RUB'
        except Exception:
            pass
        result['title'] = result['title'] or f'Товар Wildberries №{item}'
        result['image'] = base + '/images/big/1.webp'
        result['partial'] = not bool(result['price'])
        return result
    patterns = []
    if host.endswith('ozon.ru'):
        patterns = [r'/product/(.+?)-\d+/?$']
        generic = 'Товар Ozon'
    elif host.endswith('market.yandex.ru'):
        patterns = [r'/product--([^/]+)/\d+', r'/card/([^/]+)/\d+']
        generic = 'Товар с Яндекс Маркета'
    elif host.endswith('poizon.com'):
        patterns = [r'/product/(.+?)-\d+/?$', r'/product/([^/]+)/?$']
        generic = 'Товар Poizon'
    elif host.endswith('lamoda.ru'):
        patterns = [r'/p/[^/]+/([^/]+)/?$']
        generic = 'Товар Lamoda'
    elif host.endswith('eldorado.ru'):
        patterns = [r'/cat/detail/([^/]+)/?$']
        generic = 'Товар Эльдорадо'
    elif host.endswith('dns-shop.ru'):
        patterns = [r'/product/[^/]+/([^/]+)/?$']
        generic = 'Товар DNS'
    elif host.endswith('hoff.ru'):
        patterns = [r'/catalog/[^/]+/([^/]+)/?$']
        generic = 'Товар Hoff'
    elif host.endswith('letu.ru'):
        patterns = [r'/product/([^/]+)/\d+/?$']
        generic = 'Товар ЛЭТУАЛЬ'
    elif host.endswith('goldapple.ru'):
        patterns = [r'/[^/]+/([^/]+)-\d+/?$']
        generic = 'Товар Золотого Яблока'
    elif host.endswith('sportmaster.ru'):
        patterns = [r'/product/(\d+)/?$']
        generic = 'Товар Спортмастер'
    elif host.endswith('mvideo.ru'):
        patterns = [r'/products/(.+?)-\d+/?$']
        generic = 'Товар М.Видео'
    elif host.endswith('aliexpress.ru') or host.endswith('aliexpress.com'):
        patterns = []
        generic = 'Товар AliExpress'
    else:
        return None
    for pattern in patterns:
        match = re.search(pattern, path)
        if match:
            value = match.group(1)
            if not value.isdigit():
                result['title'] = (_russian_slug_title(value)
                                   if host.endswith('market.yandex.ru') or host.endswith('dns-shop.ru')
                                   else _slug_title(value))
            break
    result['title'] = result['title'] or generic
    return result


def _brightdata_result(item, source):
    if not isinstance(item, dict):
        return None
    title = str(item.get('name') or item.get('title') or '').strip()[:200]
    image = item.get('image') or item.get('image_url') or ''
    if not image and isinstance(item.get('images_url'), list) and item['images_url']:
        image = item['images_url'][0]
    price = item.get('final_price')
    if price in ('', None):
        price = item.get('price')
    currency = str(item.get('currency') or 'RUB').strip()
    if not title:
        return None
    if isinstance(image, str) and image:
        image = urljoin(f'https://{source}/', image)
    return {
        'title': title,
        'image': str(image or ''),
        'price': f'{price} {currency}'.strip() if price not in ('', None) else '',
        'source': source,
        'partial': not bool(image and price not in ('', None)),
    }


def _brightdata_ozon(url):
    """Use the monthly free scraper allowance only for otherwise blocked Ozon pages."""
    token = os.environ.get('BRIGHTDATA_API_TOKEN', '').strip()
    if not token:
        return None
    request = Request(
        ('https://api.brightdata.com/datasets/v3/scrape?dataset_id='
         f'{BRIGHTDATA_OZON_DATASET}&notify=false&include_errors=true'),
        data=json.dumps({'input': [{'url': url, 'country': ''}], 'limit_per_input': 1}).encode(),
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='POST')
    try:
        with build_opener().open(request, timeout=25) as response:
            payload = json.loads(response.read(500_001).decode('utf-8'))
        item = payload[0] if isinstance(payload, list) and payload else payload
        return _brightdata_result(item, 'ozon.ru')
    except Exception:
        # Free allowance exhaustion or provider downtime must never block manual entry.
        return None


def _brightdata_collector(url, collector):
    """Trigger a custom collector and briefly poll it for one product result."""
    token = os.environ.get('BRIGHTDATA_API_TOKEN', '').strip()
    if not token or not collector:
        return None
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    trigger = Request(
        f'https://api.brightdata.com/dca/trigger?collector={collector}&queue_next=1',
        data=json.dumps([{'url': url}]).encode(), headers=headers, method='POST')
    try:
        with build_opener().open(trigger, timeout=8) as response:
            job = json.loads(response.read(50_001).decode('utf-8'))
        job_id = job if isinstance(job, str) else job.get('id') or job.get('collection_id')
        if not job_id or not re.fullmatch(r'[A-Za-z0-9_-]+', str(job_id)):
            return None
        deadline = time.monotonic() + 28
        result_url = f'https://api.brightdata.com/dca/dataset?id={job_id}'
        while time.monotonic() < deadline:
            time.sleep(2)
            try:
                with build_opener().open(Request(result_url, headers=headers), timeout=6) as response:
                    if response.status == 202:
                        continue
                    payload = json.loads(response.read(500_001).decode('utf-8'))
            except Exception:
                continue
            item = payload[0] if isinstance(payload, list) and payload else payload
            return _brightdata_result(item, urlsplit(url).hostname.lower())
    except Exception:
        pass
    return None


def _collector_for(url):
    host = (urlsplit(url).hostname or '').lower()
    for domain, collector in BRIGHTDATA_COLLECTORS.items():
        if host == domain or host.endswith('.' + domain):
            return collector
    return ''


def extract(url):
    fallback = marketplace_fallback(url)
    collector = _collector_for(url)
    # Yandex consistently returns a CAPTCHA to server requests, so avoid that
    # redundant request and spend exactly one external record.
    if collector and (urlsplit(url).hostname or '').lower().endswith('market.yandex.ru'):
        enriched = _brightdata_collector(url, collector)
        if enriched:
            return enriched
    resolved = _known_short_target(url)
    if resolved:
        fallback = marketplace_fallback(resolved) or fallback
    try:
        html, final = fetch(url)
        result = parse(html, final)
        result['source'] = urlsplit(final).hostname or ''
        fallback = marketplace_fallback(final) or fallback
        redirected = urlsplit(final).path != urlsplit(url).path
        if fallback:
            shell_titles = {'l\'etoile', 'lamoda', 'ozon', 'wildberries', 'яндекс маркет',
                            'м.видео', 'dns', 'hoff', 'спортмастер'}
            weak_title = str(result.get('title', '')).strip().lower() in shell_titles
            if redirected or weak_title or not result.get('title') or (not result.get('image') and not result.get('price')):
                result['title'] = fallback.get('title') or result.get('title', '')
            if not result.get('image'):
                result['image'] = fallback.get('image', '')
            result['partial'] = not bool(result.get('price'))
            result['source'] = fallback.get('source') or result['source']
        if urlsplit(url).hostname.lower().endswith('ozon.ru') and result.get('partial'):
            result = _brightdata_ozon(url) or result
        elif collector and result.get('partial'):
            result = _brightdata_collector(url, collector) or result
        return result
    except (ValueError, OSError, http.client.HTTPException):
        if urlsplit(url).hostname.lower().endswith('ozon.ru'):
            enriched = _brightdata_ozon(url)
            if enriched:
                return enriched
        elif collector:
            enriched = _brightdata_collector(url, collector)
            if enriched:
                return enriched
        if fallback:
            return fallback
        raise
