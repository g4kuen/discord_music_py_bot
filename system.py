import os
import re
import asyncio
import json
import tempfile

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
queues = {}
locked_tracks = {}
greetings = ["Приветик", "Хай", "Йоу", "Салют", "Хэллоу", "Чё как", "Здарова", "Йо, здарова", "ку"]
hates = ["Хуйло","Пидагог","Огузок","Хуета","Еблан","Лабуба","Чепушило"]
emojis = ["🤖", "👾", "🛸", "🪐", "🦿", "🤯", "👽", "🛰️", ""]

MAX_HISTORY = 10


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

    if locked_tracks.get(channel_id):
        if history:
            song = history[-1]
        else:
            await ctx.send("⚠️ История пуста, нечего повторять!")
            return
    else:
        if not queues.get(channel_id):
            await ctx.send("Очередь закончилась!")
            voice = ctx.guild.voice_client
            if voice:
                await voice.disconnect()
            return
        song = queues[channel_id].pop(0)

    track = await asyncio.to_thread(download_audio, song['title'])

    if not track or not track.get('path'):
        await ctx.send(f"❌ Не удалось скачать аудио для {song['title']}")
        await play_next(ctx, bot, channel_id, depth + 1)
        return

    voice = ctx.guild.voice_client
    if not voice:
        await ctx.send("⚠️ Бот не подключён к голосовому каналу!")
        return

    source = discord.FFmpegPCMAudio(track['path'], **ffmpeg_options)
    voice.current = track
    if not locked_tracks.get(channel_id):
        add_to_history(track)

    def after_play(err):
        if err:
            print(f"Ошибка при проигрывании: {err}")
        asyncio.run_coroutine_threadsafe(play_next(ctx, bot, channel_id), bot.loop)

    try:
        voice.play(source, after=after_play)
        await ctx.send(f"▶️ Играет: {track['title']}")
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