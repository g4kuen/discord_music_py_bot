import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

from system import (
    random_greeting,
    connect_to_voice,
    get_audio_url,
    add_to_queue,
    get_queue,
    clear_queue,
    play_next,
    add_playlist_to_queue,
    history,
    queues,
    locked_tracks
)

# init
load_dotenv()
token = os.getenv("BOT_TOKEN")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="-", intents=intents)


# events
@bot.event
async def on_ready():
    print(f"✅ Бот {bot.user} успешно запущен!")


# commands
@bot.command()
async def echo(ctx):
    """Просто проверяет, жив ли бот"""
    await ctx.send("я живой!!!")


@bot.command()
async def привет(ctx):
    """Рандомное приветствие"""
    await ctx.send(random_greeting())


@bot.command()
async def join(ctx):
    """Присоединить бота в канал"""
    await connect_to_voice(ctx)


@bot.command()
async def list(ctx, *, query):
    """Добавить трек в очередь"""
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    track = get_audio_url(query)
    channel_id = ctx.author.voice.channel.id
    add_to_queue(channel_id, track['title'], track['url'])
    await ctx.send(f"✅ Добавлено в очередь: {track['title']}")


@bot.command()
async def play(ctx):
    """Начать проигрывать очередь"""
    voice = await connect_to_voice(ctx)
    if voice is None:
        return

    if voice.is_paused():
        voice.resume()
        await ctx.send("▶️ Воспроизведение возобновлено.")

    channel_id = voice.channel.id
    queue = get_queue(channel_id)

    if not queue:
        await ctx.send("Очередь пуста! Добавь треки командой `-list <название>`,а я попробую найти это на ютубе.")
        return

    if not voice.is_playing():
        song = queue.pop(0)
        voice.current = song
        #history.append(song)

        source = discord.FFmpegPCMAudio(song['url'])
        voice.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next(ctx, bot, channel_id),
                bot.loop
            )
        )
        await ctx.send(f"▶️ Играет: {song['title']}")
    else:
        await ctx.send("🎵 Музыка уже играет!")


@bot.command()
async def skip(ctx):
    """Скипнуть/анлокнуть текущий трек """
    voice = ctx.voice_client
    if voice and voice.is_playing():
        channel_id = voice.channel.id
        locked_tracks[channel_id] = False
        voice.stop()
        await ctx.send("⏭️ Пропускаем песню!")
    else:
        await ctx.send("Сейчас ничего не играет.")


@bot.command()
async def stop(ctx):
    """Стоп трека (-play чтобы продолжить воспроизведение) """
    voice = ctx.voice_client
    if not voice:
        await ctx.send("⚠️ Бот не в голосовом канале.")
        return

    if voice.is_playing():
        voice.pause()
        await ctx.send("⏸️ Воспроизведение приостановлено.")
    else:
        await ctx.send("Сейчас ничего не играет.")



@bot.command()
async def clear(ctx):
    """Очистить очередь(полностью) и выключить текущий трек """
    voice = ctx.voice_client
    if voice:
        voice.stop()
        clear_queue(voice.channel.id)
        await voice.disconnect()
        await ctx.send("⏹️ Остановлено и очередь очищена.")
    else:
        await ctx.send("Бот не в голосовом канале.")


@bot.command()
async def remove(ctx, *, query):
    """Удалить трек по названию"""
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    channel_id = ctx.author.voice.channel.id
    queue = get_queue(channel_id)
    removed = [s for s in queue if query.lower() in s['title'].lower()]

    if not removed:
        await ctx.send(f"❌ Треки с названием '{query}' не найдены.")
        return

    queues[channel_id] = [s for s in queue if query.lower() not in s['title'].lower()]
    await ctx.send(f"🗑️ Удалено {len(removed)} трек(ов) из очереди.")


@bot.command()
async def info(ctx):
    """Показать текущую очередь/текущий играющий трек"""
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    channel_id = ctx.author.voice.channel.id
    voice = ctx.voice_client
    msg = ""

    if voice and voice.is_playing():
        now = getattr(voice, "current", None)
        title = now['title'] if now else "Неизвестно"
        msg += f"▶️ Сейчас играет: **{title}**\n"
    else:
        msg += "⏹️ Сейчас ничего не играет.\n"

    queue = get_queue(channel_id)
    if queue:
        msg += "\n📃 Очередь:\n" + "\n".join(
            [f"{i+1}. {song['title']}" for i, song in enumerate(queue)]
        )
    else:
        msg += "\nОчередь пуста."

    await ctx.send(msg)

@bot.command()
async def playlist(ctx, number: int):
    """Добавить в очередь треки из плейлистов (хардкод в текстовике)"""
    if ctx.author.voice is None:
        await ctx.send("🎧 Ты должен быть в голосовом канале!")
        return

    channel_id = ctx.author.voice.channel.id
    await ctx.send(f"⏳ Загружаю плейлист #{number}...")

    added_tracks = await add_playlist_to_queue(channel_id, number)

    if not added_tracks:
        await ctx.send(f"❌ Плейлист #{number} не найден или пуст.")
        return

    await ctx.send(
        f"✅ Добавлено в очередь {len(added_tracks)} трек(ов) из плейлиста #{number}:\n" +
        "\n".join(f"- {t}" for t in added_tracks)
    )


@bot.command()
async def story(ctx):
    """Показать последние 10 треков которые играли"""
    if not history:
        await ctx.send("История пуста.")
        return

    last = history[-10:]
    text = "\n".join([f"{i+1}. {t['title']}" for i, t in enumerate(last)])
    await ctx.send("🕘 История последних треков:\n" + text)


@bot.command()
async def repeat(ctx):
    """Добавить в очередь последние 10 треков из истории"""
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    channel_id = ctx.author.voice.channel.id

    if not history:
        await ctx.send("История пуста.")
        return

    added = []
    for t in history[-10:]:
        add_to_queue(channel_id, t['title'], t['url'])
        added.append(t['title'])

    await ctx.send(
        f"✅ Добавлено {len(added)} трек(ов) из истории:\n" + "\n".join(added)
    )


@bot.command()
async def lock(ctx):
    """Поставить на repeat текущий играющий трек"""
    voice = ctx.voice_client
    if voice is None or not voice.is_playing():
        await ctx.send("⚠️ Сейчас ничего не играет!")
        return

    channel_id = voice.channel.id
    current_track = getattr(voice, 'current', None)

    if not current_track:
        await ctx.send("⚠️ Не удалось определить текущий трек!")
        return

    locked_tracks[channel_id] = True

    await ctx.send(f"🔒 Трек **{current_track['title']}** закреплён и будет играть в цикле до команды `-skip`.")



bot.run(token)
