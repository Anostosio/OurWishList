"""Manual marketplace smoke test. Network-dependent; not part of the test suite."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wishlist.metadata import extract


STORES = {
    'Wildberries': 'https://www.wildberries.ru/catalog/18557215/detail.aspx?size=50238318',
    'Яндекс Маркет': 'https://market.yandex.ru/cc/AzhWTW',
    'Ozon': 'https://ozon.ru/t/n6N6ixZ',
    'Poizon': 'https://pz7.me/EsiW89',
    'DNS': 'https://www.dns-shop.ru/product/b798dfc702d1d9cb/igrovaa-konsol-playstation-5/',
    'М.Видео': 'https://www.mvideo.ru/products/400498866',
    'Lamoda': 'https://www.lamoda.ru/p/mp002xw1ecky/clothes-tvoe-longsliv/',
    'ЛЭТУАЛЬ': 'https://www.letu.ru/product/grinco-gel-kontsentrat-dlya-stirki-belogo-belya/161900101',
    'Спортмастер': 'https://www.sportmaster.ru/product/33529090299/',
}


def check(item):
    name, url = item
    try:
        value = extract(url)
        return name, {'title': value.get('title', ''), 'image': bool(value.get('image')),
                      'price': value.get('price', ''), 'partial': value.get('partial', False)}
    except Exception as error:
        return name, {'error': str(error)}


if __name__ == '__main__':
    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(check, item) for item in STORES.items()]
        for future in as_completed(futures):
            name, result = future.result()
            results[name] = result
    print(json.dumps(results, ensure_ascii=False, indent=2))
