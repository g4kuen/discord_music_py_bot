import os
import random

import discord
from discord.ext import commands

import yt_dlp
import asyncio

from dotenv import load_dotenv

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="-", intents=intents)

load_dotenv()
token = os.getenv("BOT_TOKEN")


ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

greetings = ["Приветик", "Хай", "Йоу", "Салют", "Хэллоу", "Чё как", "Здарова", "Йо, здарова", "ку"]
hates = ["Хуйло","Пидагог","Огузок","Хуета","Еблан","Лабуба","Чепушило"]
emojis = ["🤖", "👾", "🛸", "🪐", "🦿", "🤯", "👽", "🛰️", ""]




playlists = {
    1: [
        "TVアニメ『ダンダダン』HAYASii「Hunting Soul」【lyric video】",
        "米津玄師 Kenshi Yonezu - KICKBACK",
        "米津玄師  Kenshi Yonezu - IRIS OUT",
        "Unravel tk tokyo ghoul 1 opening"
    ],
    # 2: [
    #     "Song A",
    #     "Song B",
    #     "Song C"
    # ],
    # 3: [
    #     "Track X",
    #     "Track Y"
    # ],
    # 4: [
    #     "Some Other Track 1",
    #     "Some Other Track 2"
    # ]
}
history = []
queues = {}

@bot.event
async def on_ready():
    print(f"Бот {bot.user} запущен!")

def get_audio_url(query):
    ydl_opts = {'format': 'bestaudio/best', 'noplaylist': True, 'default_search': 'ytsearch', 'quiet': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(query, download=False)
        url = info['entries'][0]['url'] if 'entries' in info else info['url']
        title = info['entries'][0]['title'] if 'entries' in info else info['title']
        return {'title': title, 'url': url}

async def play_next(ctx, channel_id):
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
        await play_next(ctx, channel_id)
        return

    voice = ctx.guild.voice_client
    if not voice:
        await ctx.send("⚠️ Бот не подключён к голосовому каналу!")
        return



    source = discord.FFmpegPCMAudio(track['url'], **ffmpeg_options)
    voice.current = track
    history.append(track)

    def after_play(err):
        if err:
            print(f"Ошибка при проигрывании: {err}")
        asyncio.run_coroutine_threadsafe(play_next(ctx, channel_id), bot.loop)

    try:
        voice.play(source, after=after_play)
        await ctx.send(f"▶️ Играет: {track['title']}")
    except Exception as e:
        print(f"Ошибка запуска ffmpeg: {e}")
        await ctx.send(f"❌ Ошибка при воспроизведении: {track['title']}")
        await play_next(ctx, channel_id)

@bot.command()
async def echo(ctx):
    await ctx.send(f"я живой!!")


@bot.command()
async def привет(ctx):
    greeting = random.choice(greetings)
    hate_speech = random.choice(hates)
    emoji = random.choice(emojis)
    await ctx.send(f"{greeting} {hate_speech} {emoji}")

@bot.command()
async def list(ctx, *, query):
    ydl_opts = {'format': 'bestaudio/best', 'noplaylist': True, 'default_search': 'ytsearch', 'quiet': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(query, download=False)
        url = info['entries'][0]['url'] if 'entries' in info else info['url']
        title = info['entries'][0]['title'] if 'entries' in info else info['title']

    channel_id = ctx.author.voice.channel.id if ctx.author.voice else None
    if channel_id is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    if channel_id not in queues:
        queues[channel_id] = []

    queues[channel_id].append({'title': title, 'url': url})
    await ctx.send(f"✅ Добавлено в очередь: {title}")

@bot.command()
async def play(ctx):
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        await channel.connect()
    elif ctx.voice_client.channel != channel:
        await ctx.voice_client.move_to(channel)

    channel_id = ctx.voice_client.channel.id
    if channel_id not in queues or not queues[channel_id]:
        await ctx.send("Очередь пустая! Добавь треки командой !list")
        return

    voice = ctx.voice_client
    if not voice.is_playing():
        song = queues[channel_id].pop(0)
        voice.current = song
        if all(song['title'] != s['title'] for s in history):
            history.append(song)
        voice.play(discord.FFmpegPCMAudio(song['url']), after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx, channel_id), bot.loop))
        await ctx.send(f"▶️ Играет: {song['title']}")
    else:
        await ctx.send("Музыка уже играет!")

@bot.command()
async def skip(ctx):
    voice = ctx.voice_client
    if voice and voice.is_playing():
        voice.stop()
        await ctx.send("⏭️ Пропускаем песню!")
    else:
        await ctx.send("Сейчас ничего не играет.")

@bot.command()
async def stop(ctx):
    voice = ctx.voice_client
    if voice:
        voice.stop()
        queues[voice.channel.id] = []
        await voice.disconnect()
        await ctx.send("⏹️ Остановлено и очередь очищена.")
    else:
        await ctx.send("Бот не в голосовом канале.")

@bot.command()
async def remove(ctx, *, query):
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    channel_id = ctx.author.voice.channel.id
    if channel_id not in queues or not queues[channel_id]:
        await ctx.send("Очередь пуста!")
        return

    removed = [song for song in queues[channel_id] if query.lower() in song['title'].lower()]
    if not removed:
        await ctx.send(f"Треки с названием '{query}' не найдены в очереди.")
        return

    queues[channel_id] = [song for song in queues[channel_id] if query.lower() not in song['title'].lower()]
    await ctx.send(f"❌ Удалено {len(removed)} трек(ов) из очереди с названием '{query}'.")

@bot.command()
async def info(ctx):
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    channel_id = ctx.author.voice.channel.id
    voice = ctx.voice_client

    if voice and voice.is_playing():
        now_playing = getattr(voice, 'current', None)
        current_song = now_playing['title'] if now_playing else "Неизвестно"
        message = f"▶️ Сейчас играет: **{current_song}**\n"
    else:
        message = "Сейчас ничего не играет.\n"

    if channel_id in queues and queues[channel_id]:
        message += "📃 Очередь:\n"
        for i, song in enumerate(queues[channel_id], start=1):
            message += f"{i}. {song['title']}\n"
    else:
        message += "Очередь пуста."

    await ctx.send(message)

@bot.command()
async def playlist(ctx, number: int):
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    if number not in playlists:
        await ctx.send("Плейлист с таким номером не найден!")
        return

    channel_id = ctx.author.voice.channel.id
    if channel_id not in queues:
        queues[channel_id] = []

    ydl_opts = {'format': 'bestaudio/best', 'noplaylist': True, 'default_search': 'ytsearch', 'quiet': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        added_tracks = []
        for track in playlists[number]:
            info = ydl.extract_info(track, download=False)
            url = info['entries'][0]['url'] if 'entries' in info else info['url']
            title = info['entries'][0]['title'] if 'entries' in info else info['title']
            queues[channel_id].append({'title': title, 'url': url})
            added_tracks.append(title)

    await ctx.send(f"✅ Добавлено в очередь {len(added_tracks)} трек(ов) из плейлиста #{number}:\n" + "\n".join(added_tracks))

@bot.command()
async def story(ctx):
    if not history:
        await ctx.send("История пуста.")
        return

    last_tracks = history[-10:]
    text = "\n".join([f"{i+1}. {song['title']}" for i, song in enumerate(last_tracks)])

    await ctx.send("🕘 История последних треков:\n" + text)


@bot.command()
async def repeat(ctx):
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    channel_id = ctx.author.voice.channel.id
    if channel_id not in queues:
        queues[channel_id] = []

    added_tracks = []
    for track in history[-10:]:
        queues[channel_id].append({'title': track['title'], 'url': track['url']})
        added_tracks.append(track['title'])

    if added_tracks:
        await ctx.send(f"✅ Добавлено в очередь {len(added_tracks)} трек(ов) из истории:\n" + "\n".join(added_tracks))
    else:
        await ctx.send("История пуста!")

bot.run(token)



