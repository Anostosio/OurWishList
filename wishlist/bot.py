import html
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit, quote
from urllib.request import Request, urlopen

from .metadata import clean_url, extract, public_address
from .store import Store
from .catalog import CATEGORIES, MATCH_MODES, KINDS, money

LOG = logging.getLogger('wishlist')
PRIORITIES = ['🌱 Когда-нибудь', '💛 Хочу', '❤️ Очень хочу']


def button(text, data):
    return {'text': text, 'callback_data': data}


def menu():
    return {'inline_keyboard': [
        [button('💛 Моё', 'list:mine:0'), button('🎁 Партнёра', 'list:partner:0')],
        [button('🏡 Наше', 'list:shared:0'), button('✨ Сбылось', 'list:archive:0')],
        [button('🖼 Галерея', 'gallery'), button('🔎 Поиск', 'search:mine')],
        [button('➕ Без ссылки', 'new'), button('💌 Пригласить', 'invite')],
        [button('📱 Mini App', 'app')]]}


def card(store, uid, row):
    owner = store.user(row['owner'])
    mine = row['owner'] == uid
    scope = '🏡 Для нас' if row['scope'] == 'shared' else f'Для {html.escape(owner["name"])}'
    text = f'<b>{html.escape(row["title"])}</b>\n{scope} · {PRIORITIES[row["priority"]]}\n\n'
    text += f'<b>{html.escape(row["price"])}</b>' if row['price'] else 'Цена не указана'
    if row['price']:
        text += f' · на {(row["price_checked"] or row["created"])[:10]}'
    if row['category'] != 'other':
        text += '\n' + CATEGORIES[row['category']]
    variants = [v for v in [f'Размер: {row["size"]}' if row['size'] else '', row['color']] if v]
    if variants:
        text += '\n' + html.escape(' · '.join(variants))
    if row['match_mode'] != 'unspecified':
        text += '\n' + MATCH_MODES[row['match_mode']]
    if row['note']:
        text += '\n\n' + html.escape(row['note'][:180] + ('…' if len(row['note']) > 180 else ''))
    if row['archived']:
        text += '\n\n✨ Сбылось'
    # Do not expose any claim information to the recipient, even indirectly.
    if not mine and row['claimed_by'] == uid:
        text += '\n\n🤫 Вы планируете подарить это. Автор не видит бронь.'
    keys = []
    wid = row['id']
    if row['url']:
        keys.append([{'text': '↗ Открыть магазин', 'url': row['url']}])
    if mine:
        keys += [[button('✏️ Изменить', f'editor:{wid}'), button('📷 Фото', f'edit:image:{wid}')],
                 [button(PRIORITIES[row['priority']], f'priority:{wid}'), button('В «Моё»' if row['scope'] == 'shared' else 'Для нас', f'scope:{wid}')],
                 [button('↩ Вернуть в желания' if row['archived'] else '✨ Сбылось', f'archive:{wid}')]]
    elif row['scope'] == 'mine' and not row['archived']:
        keys.append([button('Отменить мою бронь' if row['claimed_by'] == uid else 'Подарю 🤫', f'claim:{wid}')])
    if row['note']:
        keys.append([button('💬 Читать детали', f'details:{wid}')])
    keys.append([button('☰ Списки', 'menu')])
    return text, {'inline_keyboard': keys}


class Bot:
    def __init__(self, token, store):
        self.token, self.store = token, store
        self.webapp_url = os.environ.get('WEBAPP_URL', '').strip()
        self.username = ''

    def api(self, method, **payload):
        request = Request(f'https://api.telegram.org/bot{self.token}/{method}',
                          data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=40) as response:
                result = json.load(response)
        except HTTPError as error:
            # Avoid logging the request URL: it contains the secret bot token.
            raise RuntimeError(f'Telegram HTTP {error.code}') from None
        if not result.get('ok'):
            raise RuntimeError('Telegram отклонил запрос.')
        return result['result']

    def say(self, uid, text, keys=None):
        return self.api('sendMessage', chat_id=uid, text=text, parse_mode='HTML',
                        reply_markup=keys or menu(), link_preview_options={'is_disabled': True})

    def render(self, uid, text, keys, image='', message=None):
        photo = ''
        if image and len(text) <= 1000:
            try:
                if image.startswith('tg:'):
                    photo = image[3:]
                else:
                    photo = clean_url(image)
                    public_address(urlsplit(photo).hostname)
            except Exception:
                photo = ''
        if photo:
            try:
                if message:
                    return self.api('editMessageMedia', chat_id=uid, message_id=message['message_id'],
                                    media={'type': 'photo', 'media': photo, 'caption': text, 'parse_mode': 'HTML'}, reply_markup=keys)
                return self.api('sendPhoto', chat_id=uid, photo=photo, caption=text, parse_mode='HTML', reply_markup=keys)
            except RuntimeError:
                # A shop may block Telegram's image fetch. The wish remains usable.
                if message:
                    try:
                        return self.api('sendPhoto', chat_id=uid, photo=photo, caption=text, parse_mode='HTML', reply_markup=keys)
                    except RuntimeError:
                        pass
        if message and not message.get('photo'):
            try:
                return self.api('editMessageText', chat_id=uid, message_id=message['message_id'],
                                text=text, parse_mode='HTML', reply_markup=keys, link_preview_options={'is_disabled': True})
            except RuntimeError:
                pass
        # Never put a new wish's caption underneath the previous wish's photograph.
        return self.say(uid, text, keys)

    def show(self, uid, wid, message=None):
        row = self.store.wish(uid, wid)
        text, keys = card(self.store, uid, row)
        return self.render(uid, text, keys, row['image'], message)

    def editor(self, uid, wid):
        row = self.store.wish(uid, wid)
        if row['owner'] != uid:
            raise ValueError('Изменять желание может только его автор.')
        keys = [[button('Название', f'edit:title:{wid}'), button('Цена', f'edit:price:{wid}')],
                [button('Размер', f'edit:size:{wid}'), button('Цвет', f'edit:color:{wid}')],
                [button('Комментарий', f'edit:note:{wid}'), button('Фото', f'edit:image:{wid}')],
                [button('Категория', f'category:{wid}'), button('Можно аналог?', f'match:{wid}')]]
        if row['url']:
            keys.append([button('↻ Проверить цену в магазине', f'refresh:{wid}')])
        keys.append([button('← К карточке', f'view:{wid}')])
        return self.say(uid, '<b>Что уточнить?</b>\n' + html.escape(row['title']), {'inline_keyboard': keys})

    def filter_label(self, uid):
        filters = self.store.filters(uid)
        parts = []
        if filters['query']:
            parts.append('«' + filters['query'] + '»')
        if filters['category']:
            parts.append(CATEGORIES[filters['category']])
        if filters['budget']:
            parts.append('до ' + filters['budget'])
        if filters['sort'] == 'newest':
            parts.append('сначала новые')
        return html.escape(' · '.join(parts))

    def gallery(self, uid, kind, index=0, message=None):
        rows = self.store.browse(uid, kind)
        if not rows:
            return self.say(uid, KINDS[kind] + '\n\nНичего не найдено. Попробуйте изменить фильтры или добавить желание.',
                            {'inline_keyboard': [[button('⚙️ Фильтры', f'filters:{kind}'), button('Сбросить', f'clear:{kind}')], [button('☰ Списки', 'menu')]]})
        index = max(0, min(index, len(rows) - 1))
        row = rows[index]
        text, _ = card(self.store, uid, row)
        label = self.filter_label(uid)
        text = f'{KINDS[kind]} · {index + 1} из {len(rows)}\n' + (label + '\n' if label else '') + '\n' + text
        wid = row['id']
        keys = [[button('Открыть карточку', f'view:{wid}')]]
        if row['url']:
            keys[0].append({'text': '↗ Магазин', 'url': row['url']})
        nav = []
        if index:
            nav.append(button('←', f'gal:{kind}:{index-1}'))
        nav.append(button(f'{index+1} / {len(rows)}', 'noop'))
        if index < len(rows)-1:
            nav.append(button('→', f'gal:{kind}:{index+1}'))
        keys += [nav, [button('🔎 Поиск', f'search:{kind}'), button('⚙️ Фильтры', f'filters:{kind}')],
                 [button('☰ Списки', 'menu')]]
        return self.render(uid, text, {'inline_keyboard': keys}, row['image'], message)

    def filter_menu(self, uid, kind):
        return self.say(uid, '<b>Подобрать подарок</b>\n' + (self.filter_label(uid) or 'Все желания, сначала самые желанные.') +
                        '\n\nБюджет сравнивается только в указанной валюте. Желания без точной цены в такой подборке не появятся.',
                        {'inline_keyboard': [[button('🔎 Поиск', f'search:{kind}'), button('Категория', f'filtercat:{kind}')],
                                             [button('💰 Бюджет', f'budget:{kind}'), button('⇅ Порядок', f'sort:{kind}')],
                                             [button('Сбросить всё', f'clear:{kind}'), button('Показать', f'gal:{kind}:0')]]})

    def invite(self, uid):
        token = self.store.invite(uid)
        link = f'https://t.me/{self.username}?start=join_{token}'
        self.say(uid, 'Передайте эту ссылку партнёру. Она действует 24 часа и используется один раз.\n'
                      'После подключения вы увидите личные и общие желания друг друга.\n\n' + link)

    def open_web_app(self, uid):
        if not self.webapp_url:
            raise ValueError('Mini App не настроен: добавьте WEBAPP_URL в .env и запустите мини-приложение.')
        token = self.store.create_webapp_session(uid)
        sep = '&' if '?' in self.webapp_url else '?'
        app_url = self.webapp_url + sep + 'session=' + quote(token)
        if self.username:
            app_url += '&bot=' + quote(f'https://t.me/{self.username}')
        api_base = os.environ.get('WEBAPP_API_BASE', '').strip()
        if api_base:
            app_url += '&api=' + quote(api_base.rstrip('/'))
        return self.say(uid, 'Откройте удобную карточную витрину:', {
            'inline_keyboard': [[{'text': '💛 Открыть Mini App', 'web_app': {'url': app_url}}]]
        })

    def listing(self, uid, kind, page):
        if kind not in ('mine', 'partner', 'shared', 'archive'):
            raise ValueError('Неизвестный список.')
        all_rows = self.store.browse(uid, kind)
        rows = all_rows[max(0, page)*5:max(0, page)*5+6]
        names = {'mine': '💛 Моё', 'partner': '🎁 Партнёра', 'shared': '🏡 Наше', 'archive': '✨ Сбылось'}
        if not rows:
            return self.gallery(uid, kind)
        keys = [[button(f'{PRIORITIES[r["priority"]].split()[0]} {r["title"][:45]}', f'view:{r["id"]}')] for r in rows[:5]]
        nav = []
        if page:
            nav.append(button('← Назад', f'list:{kind}:{page-1}'))
        if len(rows) > 5:
            nav.append(button('Дальше →', f'list:{kind}:{page+1}'))
        if nav:
            keys.append(nav)
        keys.append([button('🖼 Смотреть фото', f'gal:{kind}:0'), button('⚙️ Фильтры', f'filters:{kind}')])
        keys.append([button('☰ Списки', 'menu')])
        self.say(uid, names[kind] + f' · {len(all_rows)}\n' + self.filter_label(uid) + '\nВыберите желание:', {'inline_keyboard': keys})

    def callback(self, query):
        uid = query['from']['id']
        if query.get('message', {}).get('chat', {}).get('type') != 'private':
            return self.api('answerCallbackQuery', callback_query_id=query['id'], text='Откройте личный чат с ботом.')
        self.api('answerCallbackQuery', callback_query_id=query['id'])
        args = query['data'].split(':')
        action = args[0]
        if action == 'noop':
            return
        if action == 'menu':
            self.store.state(uid, None)
            return self.say(uid, 'Ваши желания 💛\nОтправьте ссылку, чтобы добавить новое.')
        if action == 'invite':
            return self.invite(uid)
        if action == 'app':
            return self.open_web_app(uid)
        if action == 'new':
            self.store.state(uid, 'new')
            return self.say(uid, 'Напишите, чего хочется: например, «Мастер-класс по керамике вдвоём».\n/cancel — отменить')
        if action == 'gallery':
            self.store.state(uid, None)
            return self.say(uid, 'Чьи желания посмотрим?', {'inline_keyboard': [
                [button(name, f'gal:{kind}:0')] for kind, name in KINDS.items()]})
        if action in ('gal', 'filters', 'filtercat', 'search', 'budget', 'sort', 'clear', 'setfiltercat'):
            kind = args[1]
            if kind not in KINDS:
                raise ValueError('Неизвестный список.')
            self.store.state(uid, None)
            if action == 'gal':
                return self.gallery(uid, kind, int(args[2]), query.get('message'))
            if action == 'filters':
                return self.filter_menu(uid, kind)
            if action in ('search', 'budget'):
                self.store.state(uid, f'{action}:{kind}')
                prompt = 'Что ищем? Напишите название, размер или слово из комментария.' if action == 'search' else 'Напишите максимальную сумму и валюту, например: 2000 UAH, 50 EUR или 100 USD.'
                return self.say(uid, prompt + '\n«-» — убрать этот фильтр. /cancel — отменить.')
            if action == 'filtercat':
                keys = [[button(name, f'setfiltercat:{kind}:{key}')] for key, name in CATEGORIES.items()]
                keys.append([button('Все категории', f'setfiltercat:{kind}:all')])
                return self.say(uid, 'Выберите категорию:', {'inline_keyboard': keys})
            if action == 'setfiltercat':
                category = args[2]
                if category != 'all' and category not in CATEGORIES:
                    raise ValueError('Неизвестная категория.')
                self.store.filters(uid, {'category': '' if category == 'all' else category})
            if action == 'sort':
                current = self.store.filters(uid)['sort']
                self.store.filters(uid, {'sort': 'newest' if current == 'priority' else 'priority'})
                return self.filter_menu(uid, kind)
            if action == 'clear':
                self.store.filters(uid, {'query': '', 'category': '', 'budget': '', 'sort': 'priority'})
            return self.gallery(uid, kind)
        if action == 'list':
            self.store.state(uid, None)
            return self.listing(uid, args[1], int(args[2]))
        if action in ('category', 'match', 'setcategory', 'setmatch', 'editor', 'refresh'):
            wid = int(args[1])
            row = self.store.wish(uid, wid)
            if row['owner'] != uid:
                raise ValueError('Изменять желание может только его автор.')
            self.store.state(uid, None)
            if action == 'editor':
                return self.editor(uid, wid)
            if action == 'refresh':
                return self.refresh(uid, wid)
            if action in ('category', 'match'):
                choices = CATEGORIES if action == 'category' else MATCH_MODES
                prefix = 'setcategory' if action == 'category' else 'setmatch'
                keys = [[button(label, f'{prefix}:{wid}:{key}')] for key, label in choices.items()]
                keys.append([button('← Назад', f'editor:{wid}')])
                return self.say(uid, 'Выберите категорию:' if action == 'category' else 'Важна конкретная вещь или подойдёт похожая?', {'inline_keyboard': keys})
            self.store.change(uid, wid, 'category' if action == 'setcategory' else 'match_mode', args[2])
            return self.show(uid, wid)
        if action == 'edit':
            row = self.store.wish(uid, int(args[2]))
            if row['owner'] != uid or args[1] not in ('title', 'note', 'price', 'size', 'color', 'image'):
                raise ValueError('Редактирование недоступно.')
            self.store.state(uid, ':'.join(args))
            if args[1] == 'image':
                return self.say(uid, 'Пришлите фотографию для этой хотелки — как фото, а не файл.\n«-» — убрать фото. /cancel — отменить.')
            labels = {'title': 'новое название', 'note': 'комментарий', 'price': 'цену вместе с валютой', 'size': 'нужный размер', 'color': 'нужный цвет'}
            return self.say(uid, f'Напишите {labels[args[1]]}.\n/cancel — отменить; «-» — очистить детали или цену.')
        wid = int(args[1])
        row = self.store.wish(uid, wid)
        if action == 'details':
            # Split raw text before escaping, keeping even pathological input in API limits.
            note = row['note'] or 'Комментарий пока не добавлен.'
            for start in range(0, len(note), 600):
                self.say(uid, html.escape(note[start:start+600]), {'inline_keyboard': [[button('← К карточке', f'view:{wid}')]]})
            return
        if action == 'priority':
            self.store.change(uid, wid, 'priority', (row['priority'] + 1) % 3)
        elif action == 'scope':
            self.store.change(uid, wid, 'scope', 'mine' if row['scope'] == 'shared' else 'shared')
        elif action == 'archive':
            self.store.change(uid, wid, 'archived', 1 - row['archived'])
        elif action == 'claim':
            self.store.claim(uid, wid)
        elif action != 'view':
            raise ValueError('Неизвестная кнопка.')
        self.store.state(uid, None)
        return self.show(uid, wid, None if action == 'view' else query.get('message'))

    def refresh(self, uid, wid):
        row = self.store.wish(uid, wid)
        if not row['url']:
            raise ValueError('У этого желания нет ссылки на магазин.')
        self.say(uid, 'Проверяю цену в магазине…', {'inline_keyboard': []})
        try:
            result = extract(row['url'])
            if not result.get('price'):
                raise ValueError('Цена недоступна.')
        except Exception:
            return self.say(uid, 'Магазин не отдал цену. Сохранённая цена осталась прежней.', {'inline_keyboard': [[button('← К карточке', f'view:{wid}')]]})
        self.store.change(uid, wid, 'price', result['price'][:100])
        return self.show(uid, wid)

    def photo(self, uid, message):
        state = self.store.user(uid)['state'] or ''
        photo = max(message['photo'], key=lambda p: p.get('width', 0) * p.get('height', 0))
        image = 'tg:' + photo['file_id']
        if state.startswith('edit:image:'):
            wid = int(state.split(':')[2])
            self.store.change(uid, wid, 'image', image)
        elif state and state != 'new':
            return self.say(uid, 'Сейчас жду текст. Отправьте /cancel, чтобы добавить новую хотелку с фото.')
        else:
            caption = message.get('caption', '').strip()
            links = re.findall(r'https?://[^\s<>]+', caption)
            if len(links) > 1:
                raise ValueError('Добавьте к фото не больше одной ссылки.')
            url = clean_url(links[0]) if links else ''
            title = caption.replace(links[0], '', 1).strip() if links else caption
            title = title or 'Хотелка с фото'
            wid, fresh = self.store.add(uid, title[:200], url=url, image=image, note=title if len(title) > 200 else '')
            if not fresh:
                self.say(uid, 'Эта ссылка уже есть. Чтобы заменить фото, нажмите «Фото» в её карточке.')
        self.store.state(uid, None)
        return self.show(uid, wid)

    def message(self, message):
        if message['chat']['type'] != 'private':
            return
        uid = message['from']['id']
        self.store.user(uid, message['from'].get('first_name', 'Участник'))
        if message.get('photo'):
            return self.photo(uid, message)
        text = message.get('text', '').strip()
        if text.startswith('/start'):
            self.store.state(uid, None)
            parts = text.split(maxsplit=1)
            if len(parts) > 1 and parts[1].startswith('join_'):
                self.store.join(uid, parts[1][5:])
                return self.say(uid, 'Вы теперь пара 💛\nОткройте список партнёра или добавьте своё желание.')
            return self.say(uid, 'Привет! Здесь живут ваши подарки и хотелки 💛\n\n'
                                 '1. Пригласите партнёра.\n2. Пришлите ссылку на товар.\n3. Уточните размер и цвет кнопкой «Детали».\n\n'
                                 'Личные желания видны партнёру. Бронь подарка видна только дарителю.\n'
                                 'Можно добавить желание без ссылки — просто напишите его.')
        if text in ('/cancel', '/menu'):
            self.store.state(uid, None)
            return self.say(uid, 'Отправьте ссылку или выберите список.')
        if text == '/invite':
            return self.invite(uid)
        if text == '/gallery':
            self.store.state(uid, None)
            return self.say(uid, 'Чьи желания посмотрим?', {'inline_keyboard': [[button(name, f'gal:{kind}:0')] for kind, name in KINDS.items()]})
        if text == '/app':
            return self.open_web_app(uid)
        if text == '/search':
            self.store.state(uid, 'search:mine')
            return self.say(uid, 'Что найти в ваших желаниях? Напишите слово. /cancel — отменить.')
        if text == '/help':
            return self.say(uid, 'Пришлите одну ссылку и, по желанию, комментарий в том же сообщении.\n'
                                 'Любой текст без ссылки сохранится как желание.\n'
                                 '«Для нас» переносит карточку в общий список.\n'
                                 '«Подарю» тайно бронирует желание партнёра.\n'
                                 '«Сбылось» переносит желание в архив; его можно вернуть.\n'
                                 'Пришлите фото с подписью — получится новая карточка.\n'
                                 'В «Изменить» можно уточнить размер, цвет, категорию и допустимость аналога.\n'
                                 '/gallery — фото с перелистыванием; фильтры по категории и бюджету.\n'
                                 '/app — открыть красивую карточную витрину.\n'
                                 '/cancel — отменить редактирование. /export — выгрузить свои желания.')
        if text == '/export':
            rows = self.store.db.execute('SELECT id,title,url,price,note,scope,priority,archived,created,category,match_mode,size,color,price_checked FROM wishes WHERE owner=?', (uid,)).fetchall()
            if not rows:
                return self.say(uid, 'Пока нечего выгружать.')
            for row in rows:
                # Export recipient-owned fields only; never leak a partner's claim.
                raw = json.dumps(dict(row), ensure_ascii=False, indent=2)
                for start in range(0, len(raw), 600):
                    self.say(uid, '<pre>' + html.escape(raw[start:start+600]) + '</pre>')
            return
        if not text or text.startswith('/'):
            return self.say(uid, 'Пришлите ссылку, текст или фотографию с подписью. Список команд: /help')
        state = self.store.user(uid)['state']
        if state and state.split(':')[0] in ('search', 'budget'):
            action, kind = state.split(':')
            if len(text) > 100:
                raise ValueError('Сократите запрос до 100 символов.')
            value = '' if text == '-' else text
            if action == 'budget' and value:
                parsed = money(value)
                if not parsed:
                    raise ValueError('Укажите сумму и валюту, например 2000 UAH или 50 EUR.')
                value = f'{parsed[0]} {parsed[1]}'
            self.store.filters(uid, {'query' if action == 'search' else 'budget': value})
            self.store.state(uid, None)
            return self.gallery(uid, kind)
        if state and state.startswith('edit:'):
            _, field, wid = state.split(':')
            if field == 'image':
                if text != '-':
                    return self.say(uid, 'Пришлите фото или «-», чтобы убрать фотографию. /cancel — отменить.')
                self.store.change(uid, int(wid), 'image', '')
                self.store.state(uid, None)
                return self.show(uid, int(wid))
            maximum = {'title': 200, 'price': 100, 'note': 1500, 'size': 80, 'color': 80}[field]
            if len(text) > maximum:
                raise ValueError(f'Сократите текст до {maximum} символов.')
            value = '' if text == '-' and field != 'title' else text
            self.store.change(uid, int(wid), field, value)
            self.store.state(uid, None)
            return self.show(uid, int(wid))
        self.store.state(uid, None)
        links = re.findall(r'https?://[^\s<>]+', text)
        if len(links) > 1:
            raise ValueError('Пришлите одну ссылку за раз, чтобы не перепутать товары.')
        if not links:
            wid, _ = self.store.add(uid, text[:200], note=text if len(text) > 200 else '')
            return self.show(uid, wid)
        url = clean_url(links[0])
        note = text.replace(links[0], '', 1).strip()
        # Save before remote fetching: failures must not lose the user's wish.
        wid, fresh = self.store.add(uid, urlsplit(url).hostname, url=url, note=note)
        if not fresh:
            self.say(uid, 'Эта ссылка уже есть в вашем списке.')
            return self.show(uid, wid)
        self.say(uid, 'Ссылка сохранена. Загружаю название, фото и цену…', {'inline_keyboard': []})
        try:
            data = extract(url)
            self.store.update_metadata(wid, data['title'], data['image'], data.get('source', ''))
            self.store.change(uid, wid, 'price', data['price'][:100])
        except Exception:
            self.say(uid, 'Магазин не отдал данные. Ссылка сохранена — название, цену и детали можно заполнить кнопками.')
        return self.show(uid, wid)

    def run(self):
        self.username = self.api('getMe')['username']
        self.api('setMyCommands', commands=[{'command': c, 'description': d} for c, d in [
            ('menu', 'Открыть списки'), ('gallery', 'Галерея желаний'), ('search', 'Найти желание'),
            ('app', 'Открыть Mini App'), ('invite', 'Пригласить партнёра'), ('cancel', 'Отменить ввод'),
            ('help', 'Как пользоваться'), ('export', 'Выгрузить свои желания')]])
        LOG.info('Bot started: @%s', self.username)
        offset = int(self.store.setting('offset') or 0)
        while True:
            try:
                updates = self.api('getUpdates', offset=offset, timeout=25, allowed_updates=['message', 'callback_query'])
                for update in updates:
                    uid = None
                    try:
                        if 'callback_query' in update:
                            query = update['callback_query']
                            uid = query['from']['id']
                            self.store.user(uid, query['from'].get('first_name', 'Участник'))
                            self.callback(query)
                        elif 'message' in update:
                            uid = update['message']['chat']['id']
                            self.message(update['message'])
                    except ValueError as error:
                        if uid:
                            self.say(uid, html.escape(str(error)))
                    except Exception as error:
                        LOG.warning('Update failed (%s); private payload omitted', type(error).__name__)
                        if uid:
                            try:
                                self.say(uid, 'Не удалось завершить действие. Откройте /menu и попробуйте снова.')
                            except Exception:
                                pass
                    offset = update['update_id'] + 1
                    self.store.setting('offset', offset)
            except KeyboardInterrupt:
                break
            except Exception as error:
                LOG.warning('Connection failed (%s), retrying', type(error).__name__)
                time.sleep(3)


def load_env():
    path = Path('.env')
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


def main():
    load_env()
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        raise SystemExit('Заполните TELEGRAM_BOT_TOKEN в файле .env (см. README.md).')
    path = Path(os.environ.get('DATABASE_PATH', 'data/wishlist.sqlite3'))
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    Bot(token, Store(path)).run()


if __name__ == '__main__':
    main()
