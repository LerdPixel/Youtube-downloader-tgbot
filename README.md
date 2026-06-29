# YouTube Downloader Telegram Bot

Telegram-бот для скачивания аудио и видео с YouTube. Файлы отправляются прямо в чат.

## Команды

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие |
| `/help` | Список команд |
| `/audio <ссылка>` | Скачать аудио |
| `/video <ссылка>` | Скачать видео |

## Ограничения

- Максимальный размер файла — **50 МБ** (лимит Telegram).
- Видео длиннее **~16,5 часов** не скачиваются.

## Быстрый старт (Docker)

1. Создайте бота через [@BotFather](https://t.me/BotFather) и получите токен.

2. Создайте файл `.env` в корне проекта:

```env
SECRET_TOKEN=your_telegram_bot_token
```

3. Соберите и запустите:

```bash
docker compose up -d --build
```

4. Смотрите логи:

```bash
docker compose logs -f
```

5. Остановить:

```bash
docker compose down
```

## Локальный запуск

Требуется Python 3.11+ и **ffmpeg** (нужен для склейки DASH-видео).

```bash
pip install -r requirements.txt
python bot.py
```

## YouTube cookies (опционально)

Если YouTube блокирует скачивание, экспортируйте cookies в файл `yt_cookies.txt` (например, расширением [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)) и положите его в корень проекта.

Для Docker добавьте volume в `docker-compose.yml`:

```yaml
services:
  bot:
    volumes:
      - ./yt_cookies.txt:/app/yt_cookies.txt:ro
```

## Стек

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- ffmpeg
