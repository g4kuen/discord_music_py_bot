# Venom Music Bot

Discord-бот для воспроизведения музыки с YouTube в голосовых каналах.
Скачивает треки локально через `yt-dlp`, обходит bot-check YouTube
через PO Token (bgutil) и играет файл в голосовом канале.

## Возможности

- 🎵 Поиск и воспроизведение треков с YouTube по названию
- 📃 Очередь с управлением (`-list`, `-play`, `-skip`, `-remove`, `-info`)
- 🔒 Зацикливание текущего трека (`-lock`)
- 🕘 История последних треков (`-story`, `-repeat`)
- 📦 Плейлисты из текстового файла (`-playlist <номер>`)
- 🎧 Голосовые каналы Discord (join / move / disconnect)
- 🐳 Полностью докеризовано: бот + сервер PO-токенов

## Стек

| Компонент | Назначение |
|---|---|
| `python:3.12-slim` | База для бота |
| `discord.py[voice]` + `davey` | Discord API + голос |
| `yt-dlp` | Скачивание аудио с YouTube |
| `ffmpeg` | Конвертация и воспроизведение |
| `nodejs 22` | JS-рантайм для yt-dlp (EJS-челленджи) |
| `bgutil-ytdlp-pot-provider` | Генерация PO-токена (v2.0.2) |

## Требования

- Docker Desktop (Linux containers)
- Docker Compose v2 (`docker compose`)
- Discord-бот с включёнными **Privileged Gateway Intents**:
  `SERVER MEMBERS INTENT`, `MESSAGE CONTENT INTENT`
- Свободный порт `4416` (для bgutil) либо маппинг на другой

> ⚠️ **Windows/WSL2**: голосовой трафик Discord идёт по UDP и в дефолтном
> WSL2 NAT может ломаться. Помогает `networkingMode=mirrored` в `~/.wslconfig`
> или запуск на VPS / нативном Linux.

## Структура проекта

```
discord_music_py_bot/
├── bot.py               # Точка входа, команды Discord
├── system.py            # Логика: yt-dlp, очередь, плеер, PO Token
├── requirements.txt     # Python-зависимости
├── playlists.txt        # Плейлисты (формат — см. ниже)
├── Dockerfile           # Образ бота
├── docker-compose.yml   # Оркестрация: bot + bgutil
├── .env                 # BOT_TOKEN (не коммитить!)
└── README.md
```

## Установка и запуск

### 1. Клонируй репозиторий

```bash
git clone <твой-репозиторий> discord_music_py_bot
cd discord_music_py_bot
```

### 2. Создай `.env` рядом с `docker-compose.yml`

```env
BOT_TOKEN=твой_токен_дискорд_бота
```

Токен берётся в [Discord Developer Portal](https://discord.com/developers/applications)
→ Application → Bot → Reset Token.

### 3. Пригласи бота на сервер

В Developer Portal → OAuth2 → URL Generator:
- Scopes: `bot`
- Bot Permissions: `Connect`, `Speak`, `Send Messages`, `Read Message History`

### 4. Запусти через Docker Compose

```bash
docker compose up -d --build
```

Первый запуск соберёт образ (~2–3 минуты). Дальше:

```bash
docker compose logs -f bot       # логи бота
docker compose logs -f bgutil    # логи сервера PO-токенов
```

В логах бота:

```
✅ Бот <имя>#<тег> успешно запущен!
```

В логах bgutil:

```
Started POT server (v2.0.2) on address 0.0.0.0:4416
```

### 5. Проверь, что бот живой

В дискорде на сервере, где есть бот:

```
-echo
```

Бот должен ответить `я живой!!!`.

## Команды

Все команды начинаются с префикса `-`.

| Команда | Описание |
|---|---|
| `-echo` | Проверка, жив ли бот |
| `-привет` | Рандомное приветствие |
| `-join` | Присоединить бота в твой голосовой канал |
| `-list <название>` | Найти трек на YouTube и добавить в очередь |
| `-play` | Начать воспроизведение очереди / продолжить после `-stop` |
| `-skip` | Пропустить текущий трек, снять `-lock` |
| `-stop` | Пауза (без disconnect) |
| `-clear` | Остановить, очистить очередь, disconnect |
| `-remove <название>` | Удалить треки из очереди по подстроке |
| `-info` | Показать текущий трек и очередь |
| `-playlist <номер>` | Добавить плейлист из `playlists.txt` |
| `-story` | Последние 10 треков из истории |
| `-repeat` | Добавить последние 10 треков из истории в очередь |
| `-lock` | Зациклить текущий трек |

**Пример:**

```
-list ado gira gira
-play
-lock
-skip
```

## Плейлисты (`playlists.txt`)

Формат: строки с `#<номер>` открывают плейлист, дальше — треки:

```
#1
Ado - ギラギラ
米津玄師 - KICKBACK
YOASOBI - 夜に駆ける

#2
Eve - 廻廻奇譚
Aimer - 残響散歌
```

Добавить в очередь:

```
-playlist 1
```

## Переменные окружения

Задаются в `docker-compose.yml` для сервиса `bot`:

| Переменная | Значение | Назначение |
|---|---|---|
| `BOT_TOKEN` | из `.env` | Токен Discord-бота |
| `PYTHONUNBUFFERED` | `1` | Логи в реальном времени |
| `BGUTIL_PROVIDER_URL` | `http://bgutil:4416` | Адрес сервера PO-токенов |

## Как это работает

1. Пользователь пишет `-list <запрос>`.
2. Бот через `yt-dlp` ищет видео на YouTube (`ytsearch1`).
3. Для обхода bot-check запрашивает PO-токен у сервиса `bgutil`
   (внутри compose — контейнер `bgutil_provider`).
4. Скачивает аудио в `/tmp/<video_id>.opus` (внутри контейнера бота).
5. При `-play` передаёт путь к файлу в `ffmpeg`, тот стримит в Discord.

Прямые ссылки YouTube **не используются** — они протухают за минуту и
начинают отдавать 403. Поэтому аудио скачивается локально.

## Известные ограничения

- Поиск только через YouTube (`ytsearch1`); SoundCloud и другие источники
  не подключены.
- Один трек на канал — параллельное воспроизведение в нескольких каналах
  не поддерживается (очередь глобальная по `channel_id`).
- Файлы в `/tmp` внутри контейнера бота не чистятся автоматически —
  периодически нужно чистить вручную.
- Cookies/авторизация YouTube не поддерживается — играем «как гость».