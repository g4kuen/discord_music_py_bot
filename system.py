import yt_dlp
import random
import discord
import asyncio
import os



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


def download_audio(title):
    import subprocess
    import json
    import tempfile

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

        return {'title': yt_title, 'path': opus_path, 'id': video_id}

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
        history.append(track)

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


def add_to_queue(channel_id, title, url):
    if channel_id not in queues:
        queues[channel_id] = []
    queues[channel_id].append({'title': title, 'url': url})


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

            queues[channel_id].append({"title": result["title"], "url": result["path"]})
            added_tracks.append(result["title"])
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