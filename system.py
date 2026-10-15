import os
import re
import asyncio
import json
import tempfile
import time
from datetime import datetime

import subprocess
import unicodedata

import random
import discord
import yt_dlp

from difflib import SequenceMatcher

ffmpeg_options = {
    'options': '-vn'
}
url_cache = {}
history = []
PLAYLISTS_DIR = "playlists"
queues = {}
locked_tracks = {}
greetings = ["Приветик", "Хай", "Йоу", "Салют", "Хэллоу", "Чё как", "Здарова", "Йо, здарова", "ку"]
hates = ["Хуйло","Пидагог","Огузок","Хуета","Еблан","Лабуба","Чепушило"]
emojis = ["🤖", "👾", "🛸", "🪐", "🦿", "🤯", "👽", "🛰️", ""]

MAX_HISTORY = 10

volumes = {}

MIN_VOLUME = 0.0
MAX_VOLUME = 2.0   # 200%
DEFAULT_VOLUME = 1.0


def _cleanup_file(path):
    """Удаляет файл, если он существует."""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError as e:
            print(f"⚠️ Не удалось удалить {path}: {e}")


def add_to_history(track):
    """Добавляет трек в историю и чистит файлы за пределами MAX_HISTORY."""
    history.append(track)
    while len(history) > MAX_HISTORY:
        old = history.pop(0)
        _cleanup_file(old.get('path'))


def cleanup_temp_files():
    """Удаляет все временные аудиофайлы — вызывается при старте бота."""
    import tempfile
    tmp = tempfile.gettempdir()
    for name in os.listdir(tmp):
        if name.endswith(('.opus', '.webm', '.m4a', '.mp3')):
            _cleanup_file(os.path.join(tmp, name))

def download_audio(title):
    # ищем в кэше истории
    for track in history:
        if track['title'].lower() == title.lower() and track.get('path'):
            if os.path.exists(track['path']):
                return track

    tmpdir = tempfile.gettempdir()
    outtmpl = os.path.join(tmpdir, '%(id)s.%(ext)s')

    cmd = [
        'yt-dlp',
        '--js-runtimes', 'node:/usr/bin/node',
        '--remote-components', 'ejs:github',
        '--extractor-args', 'youtube:player_client=mweb',
        '--extractor-args', 'youtubepot-bgutilhttp:base_url=http://bgutil:4416',
        '-f', 'bestaudio/best',
        '-x', '--audio-format', 'opus',
        '-o', outtmpl,
        '--no-playlist',
        '--default-search', 'ytsearch1',
        '--no-update',
        '--print-json',
        title,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )

        if result.returncode != 0:
            print(f"❌ yt-dlp вернул ошибку: {result.stderr.strip()}")
            return None

        # --print-json печатает JSON-инфо на последней строке stdout
        lines = [line for line in result.stdout.strip().split('\n') if line.strip()]
        if not lines:
            print(f"❌ Пустой вывод yt-dlp для: {title}")
            return None

        info = json.loads(lines[-1])
        video_id = info.get('id')
        yt_title = info.get('title') or title
        duration = info.get('duration') or 0

        if not video_id:
            print(f"❌ Нет id видео для: {title}")
            return None

        # ищем скачанный .opus, fallback на исходные форматы
        opus_path = os.path.join(tmpdir, f"{video_id}.opus")
        if not os.path.exists(opus_path):
            for ext in ('webm', 'm4a', 'mp3'):
                p = os.path.join(tmpdir, f"{video_id}.{ext}")
                if os.path.exists(p):
                    opus_path = p
                    break
            else:
                print(f"❌ Скачанный файл не найден для: {title}")
                return None

        return {
            'title': yt_title,
            'path': opus_path,
            'id': video_id,
            'duration': duration,
        }

    except subprocess.TimeoutExpired:
        print(f"⏳ Таймаут скачивания: {title}")
        return None
    except Exception as e:
        print(f"Ошибка при скачивании трека {title}: {e}")
        return None


async def connect_to_voice(ctx):
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return None

    channel = ctx.author.voice.channel
    voice_client = ctx.voice_client

    if voice_client is None:
        voice_client = await channel.connect()
        await ctx.send(f"✅ Подключился к каналу: **{channel.name}**")
    elif voice_client.channel != channel:
        await voice_client.move_to(channel)
        await ctx.send(f"🔁 Переместился в канал: **{channel.name}**")
    else:
        await ctx.send(f"🎧 Уже подключён к каналу: **{channel.name}**")

    return voice_client


async def play_next(ctx, bot, channel_id, depth=0):
    if depth > 5:
        await ctx.send("⚠️ Слишком много ошибок подряд, останавливаюсь.")
        return

    voice = ctx.guild.voice_client
    if not voice:
        await ctx.send("⚠️ Бот не подключён к голосовому каналу!")
        return

    # ── seek-режим ────────────────────────────────────────────
    seek_to = getattr(voice, 'seek_to', None)
    is_seek = seek_to is not None

    if is_seek:
        voice.seek_to = None
        track = getattr(voice, 'current', None)
        if not track or not track.get('path'):
            await ctx.send("⚠️ Не удалось перемотать трек.")
            return
        song = None  # не используется в seek-ветке
    # ──────────────────────────────────────────────────────────
    elif locked_tracks.get(channel_id):
        if history:
            song = history[-1]
        else:
            await ctx.send("⚠️ История пуста, нечего повторять!")
            return
    else:
        if not queues.get(channel_id):
            await ctx.send("Очередь закончилась!")
            if voice:
                await stop_np(voice)
                await voice.disconnect()
            return
        song = queues[channel_id].pop(0)

    # в обычном режиме трек качаем, в seek — уже есть
    if not is_seek:
        track = await asyncio.to_thread(download_audio, song['title'])
        if not track or not track.get('path'):
            await ctx.send(f"❌ Не удалось скачать аудио для {song['title']}")
            await play_next(ctx, bot, channel_id, depth + 1)
            return

    # источник: с -ss при seek, обычный иначе
    if is_seek:
        raw = discord.FFmpegPCMAudio(
            track['path'],
            before_options=f"-ss {seek_to}",
            **ffmpeg_options,
        )
    else:
        raw = discord.FFmpegPCMAudio(track['path'], **ffmpeg_options)

    source = discord.PCMVolumeTransformer(raw, volume=get_volume(channel_id))

    voice.current = track
    # в историю не пишем при seek — иначе трек задвоится
    if not is_seek and not locked_tracks.get(channel_id):
        add_to_history(track)

    def after_play(err):
        if err:
            print(f"Ошибка при проигрывании: {err}")
        asyncio.run_coroutine_threadsafe(play_next(ctx, bot, channel_id), bot.loop)

    try:
        voice.play(source, after=after_play)
        init_tracking(voice, offset=seek_to if is_seek else 0.0)

        if is_seek:
            # тот же трек — обновляем существующее сообщение
            await refresh_np(voice)
        else:
            # новый трек — новое сообщение + новый цикл
            await start_np(ctx, voice, track)
    except Exception as e:
        print(f"Ошибка запуска ffmpeg: {e}")
        await ctx.send(f"❌ Ошибка при воспроизведении: {track['title']}")
        await play_next(ctx, bot, channel_id, depth + 1)

def random_greeting():
    return f"{random.choice(greetings)} {random.choice(hates)} {random.choice(emojis)}"


def add_to_queue(channel_id, title, url, duration):
    if channel_id not in queues:
        queues[channel_id] = []
    queues[channel_id].append({'title': title, 'url': url, 'duration': duration})


def get_queue(channel_id):
    return queues.get(channel_id, [])


def clear_queue(channel_id):
    queues[channel_id] = []


async def add_playlist_to_queue(channel_id, number, filepath="playlists.txt"):
    playlists = load_playlists_from_file(filepath)
    if number not in playlists:
        print(f"⚠️ Плейлист #{number} не найден.")
        return []

    if channel_id not in queues:
        queues[channel_id] = []

    added_tracks = []

    async def fetch_track(track):
        try:
            result = await asyncio.to_thread(download_audio, track)

            if not result or not result.get('path'):
                print(f"❌ Не удалось загрузить трек: {track}")
                return

            queues[channel_id].append({
                "title": result["title"],
                "url": result["path"],
                "duration": result.get("duration", 0),
            })
            added_tracks.append({
                "title": result["title"],
                "duration": result.get("duration", 0),
            })
            print(f"✅ Добавлен трек: {result['title']}")

        except asyncio.TimeoutError:
            print(f"⏳ Превышено время ожидания для трека: {track}")
        except Exception as e:
            print(f"❌ Ошибка загрузки трека '{track}': {e}")

    for track in playlists[number]:
        try:
            # таймаут больше, потому что теперь трек реально скачивается целиком
            await asyncio.wait_for(fetch_track(track), timeout=180)
        except asyncio.TimeoutError:
            print(f"⚠️ Таймаут загрузки трека: {track}")

    return added_tracks


def load_playlists_from_file(filepath="playlists.txt"):
    playlists = {}
    current_id = None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    current_id = int(line[1:])
                    playlists[current_id] = []
                elif current_id is not None:
                    playlists[current_id].append(line)
    except FileNotFoundError:
        print(f"⚠️ Файл {filepath} не найден. Создай playlists.txt рядом с ботом.")
    return playlists

def normalize(s):
    # NFKC
    s = unicodedata.normalize('NFKC', s)
    # zero-width
    s = re.sub(r'[\u200b-\u200f\ufeff]', '', s)
    # brackets with suffixes like [MV], (Official Video), 【...】, 「...」
    # first, remove specific known suffixes
    suffix_pattern = re.compile(
        r'[\(\[\{【「『].*?(official|mv|m/?v|lyric|audio|video|hd|hq|4k|remaster|topic|feat|ft\.?|prod\.?|music video).*?[\)\]\}】」』]',
        re.IGNORECASE,
    )
    s = suffix_pattern.sub(' ', s)
    # remove feat./ft./prod. outside brackets
    s = re.sub(r'\b(feat\.?|ft\.?|featuring|prod\.?|produced by)\b.*', ' ', s, flags=re.IGNORECASE)
    # remove "- Topic" at end
    s = re.sub(r'\s*-\s*topic\s*$', ' ', s, flags=re.IGNORECASE)
    # punctuation → space (keep alphanumeric, spaces, and unicode letters)
    s = re.sub(r'[^\w\s]', ' ', s, flags=re.UNICODE)
    # but \w in Python 3 with re.UNICODE includes letters, digits, underscore
    # collapse spaces
    s = re.sub(r'\s+', ' ', s).strip()
    # lower
    s = s.lower()
    return s

def matches(query: str, title: str, threshold: float = 0.72) -> bool:
    """True, если query «похоже» на title.

    1. подстрочное совпадение после normalize (быстро, точно)
    2. fuzzy через SequenceMatcher (медленнее, ловит опечатки и пропуски)
    """
    q = normalize(query)
    t = normalize(title)

    if not q or not t:
        return False

    # короткие запросы — только точное вхождение, без fuzzy
    if len(q) < 4:
        return q in t

    # быстрый путь — подстрока
    if q in t:
        return True

    # fuzzy: скользящее окно по длине запроса,
    # чтобы длинный title не «размывал» сходство
    if len(q) <= len(t):
        best = 0.0
        window = len(q)
        step = max(1, window // 4)
        for i in range(0, len(t) - window + 1, step):
            r = SequenceMatcher(None, q, t[i:i + window]).ratio()
            if r > best:
                best = r
                if best >= threshold:
                    return True
        return best >= threshold

    # запрос длиннее названия — сравниваем целиком
    return SequenceMatcher(None, q, t).ratio() >= threshold

def get_cache_info():
    """Возвращает список файлов кэша: (имя, размер_в_МБ, mtime)."""
    import tempfile
    import time

    tmp = tempfile.gettempdir()
    files = []
    for name in os.listdir(tmp):
        if name.endswith(('.opus', '.webm', '.m4a', '.mp3')):
            p = os.path.join(tmp, name)
            try:
                size_mb = os.path.getsize(p) / (1024 * 1024)
                mtime = os.path.getmtime(p)
                files.append((name, size_mb, mtime))
            except OSError:
                pass

    # сортируем по времени изменения — свежие сверху
    files.sort(key=lambda x: x[2], reverse=True)
    return files

def _fmt_duration(seconds):
    """Секунды → 'M:SS' или 'H:MM:SS'."""
    if not seconds:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def get_volume(channel_id: int) -> float:
    return volumes.get(channel_id, DEFAULT_VOLUME)


def set_volume(channel_id: int, value: float) -> float:
    value = max(MIN_VOLUME, min(MAX_VOLUME, value))
    volumes[channel_id] = value
    return value

def _parse_time(s: str):
    """'1:30' | '90' | '1:02:30' → секунды (int) или None при ошибке."""
    s = s.strip()
    if not s:
        return None

    s = s.replace('.', ':')
    try:
        parts = [int(p) for p in s.split(':')]
    except ValueError:
        return None
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None

# ─── живое NP-сообщение ────────────────────────────────────────

# ─── трекинг позиции ──────────────────────────────────────────

def init_tracking(voice, offset: float = 0.0):
    """Инициализирует состояние трекинга при старте воспроизведения."""
    voice.started_at = time.time()
    voice.start_offset = offset or 0.0
    voice.total_paused = 0.0
    voice.paused_at = None


def get_position(voice) -> float:
    """Текущая позиция в треке в секундах. 0 если трекинг не инициализирован."""
    if not voice or getattr(voice, 'started_at', None) is None:
        return 0.0
    now = voice.paused_at if voice.paused_at else time.time()
    elapsed = now - voice.started_at - (voice.total_paused or 0.0)
    pos = (voice.start_offset or 0.0) + elapsed
    return max(0.0, pos)


def mark_paused(voice):
    """Зафиксировать начало паузы."""
    if voice and getattr(voice, 'started_at', None) and not getattr(voice, 'paused_at', None):
        voice.paused_at = time.time()


def mark_resumed(voice):
    """Учесть паузу в total_paused и продолжить трекинг."""
    if voice and getattr(voice, 'paused_at', None):
        voice.total_paused = (getattr(voice, 'total_paused', 0.0) or 0.0) \
                             + (time.time() - voice.paused_at)
        voice.paused_at = None


def _progress_bar(pos: float, total: float, length: int = 16) -> str:
    """[▬▬▬🔘▬▬▬▬▬▬] — визуальный прогресс."""
    if not total or total <= 0:
        return "─" * length
    ratio = min(1.0, max(0.0, pos / total))
    filled = int(ratio * (length - 1))
    return "─" * filled + "🔘" + "─" * (length - 1 - filled)

def _build_np_text(track, pos, dur, paused=False):
    """Собирает текст: иконка, название, прогресс-бар."""
    title = track.get('title', 'Неизвестно')
    icon = "⏸️" if paused else "▶️"
    if dur:
        bar = _progress_bar(pos, dur)
        body = f"`{_fmt_duration(pos)}` {bar} `{_fmt_duration(dur)}`"
    else:
        body = f"`{_fmt_duration(pos)}`"
    return f"{icon} 🎵 **{title}**\n{body}"


async def _np_loop(voice):
    """Фоновой цикл: раз в 5 сек правит voice.np_message."""
    try:
        while True:
            await asyncio.sleep(5)
            if not voice or not voice.is_connected():
                return
            current = getattr(voice, 'current', None)
            msg = getattr(voice, 'np_message', None)
            if not current or not msg:
                return

            pos = get_position(voice)
            dur = current.get('duration') or 0
            paused = bool(getattr(voice, 'paused_at', None))
            text = _build_np_text(current, pos, dur, paused)

            # не дёргаем Discord, если ничего не изменилось
            if text == getattr(voice, '_np_last_text', None):
                continue

            try:
                await msg.edit(content=text)
                voice._np_last_text = text
            except discord.NotFound:
                return  # сообщение удалили — выходим
            except discord.HTTPException as e:
                print(f"⚠️ np edit: {e}")
                return
    except asyncio.CancelledError:
        return


async def start_np(ctx, voice, track):
    """Отправляет новое NP-сообщение с кнопками и запускает авто-обновление."""

    # удаляем старое сообщение с мёртвыми кнопками, чтобы не засорять чат
    old = getattr(voice, 'np_message', None)
    if old:
        try:
            await old.delete()
        except Exception:
            pass

    await stop_np(voice)

    dur = track.get('duration') or 0
    text = _build_np_text(track, 0.0, dur, False)

    # создаём View. Если класс по какой-то причине не готов — без кнопок.
    try:
        view = _get_player_controls()(voice)
    except Exception as e:
        print(f"⚠️ start_np: не удалось создать View: {e}")
        view = None

    msg = await ctx.send(text, view=view)

    voice.np_message = msg
    voice._np_last_text = text
    voice.np_task = asyncio.create_task(_np_loop(voice))
    return msg



async def stop_np(voice):
    """Отменяет авто-обновление и забывает сообщение."""
    task = getattr(voice, 'np_task', None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    voice.np_task = None
    voice.np_message = None
    voice._np_last_text = None


async def refresh_np(voice):
    """Принудительно обновить NP-сообщение прямо сейчас (для seek / pause)."""
    msg = getattr(voice, 'np_message', None)
    current = getattr(voice, 'current', None)
    if not msg or not current:
        return
    pos = get_position(voice)
    dur = current.get('duration') or 0
    paused = bool(getattr(voice, 'paused_at', None))
    text = _build_np_text(current, pos, dur, paused)
    try:
        await msg.edit(content=text)
        voice._np_last_text = text
    except Exception as e:
        print(f"⚠️ np refresh: {e}")

async def repost_np(ctx, voice):
    """Пересоздать NP-сообщение внизу чата (для -np).
    Удаляет старое, шлёт новое с текущей позицией, перезапускает автообновление."""
    current = getattr(voice, 'current', None)
    if not current:
        return None

    # запоминаем старое, потом stop_np обнуляет все атрибуты
    old = getattr(voice, 'np_message', None)
    await stop_np(voice)

    if old:
        try:
            await old.delete()
        except Exception:
            pass

    pos = get_position(voice)
    dur = current.get('duration') or 0
    paused = bool(getattr(voice, 'paused_at', None))
    text = _build_np_text(current, pos, dur, paused)

    try:
        view = _get_player_controls()(voice)
    except Exception as e:
        print(f"⚠️ repost_np: не удалось создать View: {e}")
        view = None

    msg = await ctx.send(text, view=view)

    voice.np_message = msg
    voice._np_last_text = text
    voice.np_task = asyncio.create_task(_np_loop(voice))
    return msg

def _get_player_controls():
    """Импортирует PlayerControls из ui.py по требованию.
    Так избегаем циклического импорта system ↔ ui."""
    from UI import PlayerControls
    return PlayerControls

