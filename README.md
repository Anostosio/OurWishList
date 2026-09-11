# OurWishList 💛

Telegram-бот и Mini App для совместного списка желаний пары. Пользователи объединяются по одноразовой ссылке, сохраняют идеи из интернет-магазинов и ведут личные и общие списки в одном приложении.

[Открыть демо](https://ourwishlist-silk.vercel.app/) · [Описание MVP](MVP.md) · [Совместимость с маркетплейсами](MARKETPLACE_COMPATIBILITY.md)

> Демо предназначено для запуска внутри Telegram. В обычном браузере можно посмотреть интерфейс, но авторизация и совместные списки требуют Telegram.

## Задача проекта

Сделать приватный вишлист для двух человек, в котором удобно:

- добавлять желание ссылкой или вручную;
- автоматически получать название, изображение и цену товара;
- разделять списки на «Моё», «Партнёра», «Наше» и «Сбылось»;
- указывать приоритет, размер, цвет, категорию и допустимость аналога;
- приглашать партнёра безопасной одноразовой ссылкой;
- пользоваться одним и тем же списком с разных устройств.

## Что реализовано

- Telegram-бот и адаптивный Telegram Mini App.
- Авторизация через подписанные Telegram `initData`.
- Приватная пара максимум из двух участников.
- Создание, редактирование, архивирование, восстановление и резервирование желаний.
- Получение метаданных товара из Open Graph и JSON-LD с безопасным ручным вводом при недоступности магазина.
- Облачная база PostgreSQL в production и SQLite для локальной разработки.
- Serverless API и развертывание на Vercel.
- Автоматические тесты основных API-, авторизационных и пользовательских сценариев.

## Стек

- Python 3.12+
- PostgreSQL / SQLite
- HTML, CSS и JavaScript без frontend-фреймворка
- Telegram Bot API и Telegram Mini Apps
- Vercel Functions

## Архитектура

```text
Telegram
├── бот ─────────────── wishlist/bot.py
└── Mini App ────────── wishlist/miniapp/index.html
                         │
                         ▼
                   Vercel API ───── api/
                         │
                         ▼
               PostgreSQL / SQLite ─ wishlist/*_store.py
```

## Структура проекта

```text
api/                         serverless API для Vercel
scripts/                     миграция БД, webhook и диагностика
tests/                       автоматические тесты
wishlist/
  bot.py                     сценарии Telegram-бота
  metadata.py                получение данных о товарах
  postgres_store.py          хранилище PostgreSQL
  store.py                   хранилище SQLite
  web_auth.py                проверка Telegram-авторизации
  miniapp/index.html         интерфейс Mini App
vercel.json                  production-маршруты и сборка
```

## Локальный запуск

1. Установите Python 3.12 или новее и создайте виртуальное окружение.
2. Установите зависимости:

   ```bash
   python -m pip install -r requirements.txt
   ```

3. Скопируйте `.env.example` в `.env` и заполните как минимум:

   ```dotenv
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_BOT_USERNAME=...
   TELEGRAM_WEBHOOK_SECRET=...
   WEBAPP_URL=https://your-app.example
   DATABASE_PATH=data/wishlist.sqlite3
   ```

4. Запустите бота:

   ```bash
   python -m wishlist.bot
   ```

5. Для локального просмотра Mini App запустите сервер:

   ```bash
   python -m wishlist.miniapp_server
   ```

Переменная `DATABASE_URL` переключает приложение с SQLite на PostgreSQL. Настоящие токены и строки подключения нельзя добавлять в Git — локальные `.env` уже исключены через `.gitignore`.

## Тесты

```bash
python -m unittest discover -s tests -v
```

Тесты также запускаются автоматически в GitHub Actions при каждом push и pull request.

## Статус

MVP версии 0.2 реализован и развернут. Следующий логичный этап — расширить поддержку маркетплейсов, добавить наблюдаемость production-ошибок и вынести интерфейс Mini App в отдельные модули.
