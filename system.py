import yt_dlp
import random
import discord
import asyncio


ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}
url_cache = {}
history = []
queues = {}
locked_tracks = {}
greetings = ["Приветик", "Хай", "Йоу", "Салют", "Хэллоу", "Чё как", "Здарова", "Йо, здарова", "ку"]
hates = ["Хуйло","Пидагог","Огузок","Хуета","Еблан","Лабуба","Чепушило"]
emojis = ["🤖", "👾", "🛸", "🪐", "🦿", "🤯", "👽", "🛰️", ""]

playlists = {
    1: [
        "【Ado】ギラギラ",
        "TVアニメ『ダンダダン』HAYASii「Hunting Soul」【lyric video】",
        "米津玄師 Kenshi Yonezu - KICKBACK",
        "米津玄師  Kenshi Yonezu - IRIS OUT",
    ]
}


def get_audio_url(title):
    for track in history:
        if track['title'].lower() == title.lower():
            return track


    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'default_search': 'ytsearch',
        'quiet': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(title, download=False)
            url = info['entries'][0]['url'] if 'entries' in info else info['url']
            yt_title = info['entries'][0]['title'] if 'entries' in info else info['title']

            track = {'title': yt_title, 'url': url}

            return track
    except Exception as e:
        print(f"Ошибка при получении трека {title}: {e}")
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


async def play_next(ctx, bot, channel_id):
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

    track = get_audio_url(song['title'])

    if not track or 'url' not in track:
        await ctx.send(f"❌ Не удалось получить аудио для {song['title']}")
        await play_next(ctx, bot, channel_id)
        return

    voice = ctx.guild.voice_client
    if not voice:
        await ctx.send("⚠️ Бот не подключён к голосовому каналу!")
        return

    source = discord.FFmpegPCMAudio(track['url'], **ffmpeg_options)
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
        await play_next(ctx, bot, channel_id)


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

    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "default_search": "ytsearch",
        "quiet": True,
    }

    added_tracks = []

    async def fetch_track(track):
        try:
            def _extract():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(track, download=False)

            info = await asyncio.to_thread(_extract)
            url = info["entries"][0]["url"] if "entries" in info else info["url"]
            title = info["entries"][0]["title"] if "entries" in info else info["title"]

            queues[channel_id].append({"title": title, "url": url})
            added_tracks.append(title)

            print(f"✅ Добавлен трек: {title}")

        except asyncio.TimeoutError:
            print(f"⏳ Превышено время ожидания для трека: {track}")
        except Exception as e:
            print(f"❌ Ошибка загрузки трека '{track}': {e}")

    for track in playlists[number]:
        try:
            await asyncio.wait_for(fetch_track(track), timeout=20)
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