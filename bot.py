import os
import random
import re
import asyncio
import unicodedata

import discord
from discord.ext import commands
from dotenv import load_dotenv

from system import (
    random_greeting,
    connect_to_voice,
    download_audio,
    add_to_queue,
    get_queue,
    clear_queue,
    play_next,
    add_playlist_to_queue,
    history,
    queues,
    locked_tracks,
    ffmpeg_options,
    matches,
    normalize,
    add_to_history,
    cleanup_temp_files,
    get_cache_info,
    _cleanup_file,
    _fmt_duration,
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
    cleanup_temp_files()
    print("🧹 Кэш /tmp очищен")


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
    """Добавить трек в очередь -list <название видео на ютубе>"""
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    await ctx.send(f"🔎 Скачиваю: **{query}**...")

    track = await asyncio.to_thread(download_audio, query)

    if not track or not track.get('path'):
        await ctx.send(f"❌ Не удалось скачать: **{query}**")
        return

    channel_id = ctx.author.voice.channel.id
    add_to_queue(channel_id, track['title'], track['path'],track.get('duration', 0))
    dur = _fmt_duration(track.get('duration'))
    await ctx.send(f"✅ Добавлено в очередь: {track['title']} `{dur}`")


@bot.command()
async def play(ctx, *, query: str = None):
    """Запустить очередь. С аргументом — дополнительно добавить в очередь"""
    # если передан аргумент — сначала находим и добавляем в очередь
    if query:
        if ctx.author.voice is None:
            await ctx.send("Ты должен быть в голосовом канале!")
            return

        await ctx.send(f"🔎 Ищу: **{query}**...")

        track = await asyncio.to_thread(download_audio, query)

        if not track or not track.get('path'):
            await ctx.send(f"❌ Не удалось найти или скачать: **{query}**")
            return

        channel_id = ctx.author.voice.channel.id
        add_to_queue(channel_id, track['title'], track['path'],track.get('duration', 0))
        dur = _fmt_duration(track.get('duration'))
        await ctx.send(f"✅ Добавлено в очередь: {track['title']} `{dur}`")

    # дальше стандартная логика запуска
    voice = await connect_to_voice(ctx)
    if voice is None:
        return

    if voice.is_paused():
        voice.resume()
        await ctx.send("▶️ Воспроизведение возобновлено.")
        return

    channel_id = voice.channel.id
    queue = get_queue(channel_id)

    if not queue:
        await ctx.send("Очередь пуста! Добавь треки командой `-list <название>` или `-play <название>`.")
        return

    if not voice.is_playing():
        song = queue.pop(0)

        track = await asyncio.to_thread(download_audio, song['title'])
        if not track or not track.get('path'):
            await ctx.send(f"❌ Не удалось скачать **{song['title']}**, пропускаю.")
            await play_next(ctx, bot, channel_id)
            return

        voice.current = track
        add_to_history(track)

        source = discord.FFmpegPCMAudio(track['path'], **ffmpeg_options)
        voice.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next(ctx, bot, channel_id),
                bot.loop
            )
        )
        await ctx.send(f"▶️ Играет: {track['title']}")
    else:
        await ctx.send("🎵 Музыка уже играет, трек добавлен в очередь.")


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
    """Очистить очередь, остановить трек и удалить все связанные файлы из кэша"""
    voice = ctx.voice_client
    if not voice:
        await ctx.send("Бот не в голосовом канале.")
        return

    channel_id = voice.channel.id
    queue = get_queue(channel_id)

    # собираем пути к файлам: текущий играющий + все из очереди
    paths_to_delete = set()

    current = getattr(voice, 'current', None)
    if current and current.get('path'):
        paths_to_delete.add(current['path'])

    for s in queue:
        p = s.get('url')  # в очереди path хранится под ключом 'url'
        if p:
            paths_to_delete.add(p)

    # останавливаем и отключаемся
    voice.stop()
    clear_queue(channel_id)
    await voice.disconnect()

    # чистим файлы
    deleted = 0
    for p in paths_to_delete:
        if os.path.exists(p):
            try:
                os.remove(p)
                deleted += 1
            except OSError as e:
                print(f"⚠️ Не удалось удалить {p}: {e}")

    await ctx.send(
        f"⏹️ Остановлено, очередь очищена.\n"
        f"🧹 Удалено файлов из кэша: **{deleted}**"
    )


@bot.command()
async def remove(ctx, *, query):
    """Удалить треки из очереди по подстроке/номеру из -info"""
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    channel_id = ctx.author.voice.channel.id
    queue = get_queue(channel_id)

    if not queue:
        await ctx.send("Очередь пуста.")
        return

    # защищённые пути — те, что есть в истории (их юзает -remember, -repeat)
    protected_paths = {t.get('path') for t in history if t.get('path')}

    def purge_files(tracks):
        """Удаляет файлы удаляемых треков, если они не в истории."""
        for t in tracks:
            path = t.get('url')  # в очереди path хранится под ключом 'url'
            if path and path not in protected_paths:
                _cleanup_file(path)

    # удаление по номеру
    q = normalize(query)
    if q.isdigit():
        idx = int(q) - 1
        if 0 <= idx < len(queue):
            removed_track = queue.pop(idx)
            purge_files([removed_track])
            await ctx.send(f"🗑️ Удалён трек #{idx + 1}: **{removed_track['title']}**")
        else:
            await ctx.send(f"❌ В очереди нет трека с номером {q}.")
        return

    # поиск (подстрока + fuzzy)
    removed = [s for s in queue if matches(query, s['title'])]

    if not removed:
        await ctx.send(f"❌ Треки по запросу **{query}** не найдены.")
        return

    queues[channel_id] = [s for s in queue if not matches(query, s['title'])]
    purge_files(removed)

    preview = "\n".join(f"• {t['title']}" for t in removed[:10])
    if len(removed) > 10:
        preview += f"\n… и ещё {len(removed) - 10}"

    await ctx.send(f"🗑️ Удалено {len(removed)} трек(ов):\n{preview}")


@bot.command()
async def info(ctx):
    """Показать текущую очередь и текущий играющий трек"""
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    channel_id = ctx.author.voice.channel.id
    voice = ctx.voice_client
    msg = ""

    if voice and voice.is_playing():
        now = getattr(voice, "current", None)
        if now:
            dur = _fmt_duration(now.get('duration'))
            title = now.get('title', 'Неизвестно')
            msg += f"▶️ Сейчас играет: **{title}** `{dur}`\n"
        else:
            msg += "▶️ Сейчас играет: **Неизвестно**\n"
    else:
        msg += "⏹️ Сейчас ничего не играет.\n"

    queue = get_queue(channel_id)
    if queue:
        total = sum(s.get('duration', 0) for s in queue)
        lines = [
            f"`{i:2d}.` {song['title']} `{_fmt_duration(song.get('duration'))}`"
            for i, song in enumerate(queue, start=1)
        ]
        msg += "\n📃 Очередь:\n" + "\n".join(lines)
        msg += f"\n\n⌛ Всего в очереди: **{_fmt_duration(total)}**"
    else:
        msg += "\nОчередь пуста."

    await ctx.send(msg)

@bot.command()
async def playlist(ctx, number: int):
    """Добавить в очередь треки из текстовика -playlist <1..2..3>"""
    if ctx.author.voice is None:
        await ctx.send("🎧 Ты должен быть в голосовом канале!")
        return

    channel_id = ctx.author.voice.channel.id
    await ctx.send(f"⏳ Загружаю плейлист #{number}...")

    added_tracks = await add_playlist_to_queue(channel_id, number)

    if not added_tracks:
        await ctx.send(f"❌ Плейлист #{number} не найден или пуст.")
        return

    lines = [
        f"`{i:2d}.` {t['title']} `{_fmt_duration(t.get('duration'))}`"
        for i, t in enumerate(added_tracks, start=1)
    ]
    await ctx.send(
        f"✅ Добавлено в очередь {len(added_tracks)} трек(ов) из плейлиста #{number}:\n"
        + "\n".join(lines)
    )


@bot.command()
async def story(ctx):
    """Показать последние 10 треков из истории"""
    if not history:
        await ctx.send("История пуста.")
        return

    # берём последние 10 и разворачиваем — свежие сверху
    last = history[-10:][::-1]

    # что играет сейчас — для пометки
    voice = ctx.voice_client
    current = getattr(voice, 'current', None) if voice else None
    current_path = current.get('path') if current else None

    lines = []
    for i, t in enumerate(last, start=1):
        dur = _fmt_duration(t.get('duration'))
        title = t.get('title', 'Неизвестно')
        marker = " ▶" if current_path and t.get('path') == current_path else ""
        lines.append(f"`{i:2d}.` {title} `{dur}`{marker}")

    total = sum(t.get('duration', 0) for t in last)

    text = "🕘 История последних треков:\n" + "\n".join(lines)
    text += f"\n\n⌛ Общая длительность: **{_fmt_duration(total)}**"

    await ctx.send(text)

@bot.command()
async def remember(ctx):
    """Добавить в очередь последние 10 треков из -story"""
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    channel_id = ctx.author.voice.channel.id

    if not history:
        await ctx.send("История пуста.")
        return

    added = []
    for t in history[-10:]:
        add_to_queue(channel_id, t['title'], t.get('path', ''), t.get('duration', 0))
        added.append(t)

    lines = [
        f"`{i:2d}.` {t['title']} `{_fmt_duration(t.get('duration'))}`"
        for i, t in enumerate(added, start=1)
    ]

    await ctx.send(
        f"✅ Добавлено {len(added)} трек(ов) из истории:\n" + "\n".join(lines)
    )

@bot.command()
async def repeat(ctx):
    """Поставить на повтор текущий играющий трек"""
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

@bot.command()
async def cache(ctx):
    """Показать, что сейчас лежит в кэше /tmp"""

    files = get_cache_info()

    if not files:
        await ctx.send("📦 Кэш пуст.")
        return

    total_mb = sum(f[1] for f in files)

    lines = []
    for name, size_mb, _ in files[:20]:
        lines.append(f"`{size_mb:5.2f} MB` — {name}")

    text = f"📦 Кэш: **{len(files)}** файл(ов), всего **{total_mb:.2f} MB**\n"
    text += "\n".join(lines)

    if len(files) > 20:
        text += f"\n… и ещё {len(files) - 20}"

    await ctx.send(text)


@bot.command()
async def shuffle(ctx):
    """Перемешать треки в очереди"""
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    channel_id = ctx.author.voice.channel.id
    queue = get_queue(channel_id)

    if not queue:
        await ctx.send("Очередь пуста, нечего перемешивать.")
        return

    if len(queue) < 2:
        await ctx.send("В очереди меньше двух треков, перемешивать нечего.")
        return

    random.shuffle(queue)
    # queues[channel_id] и queue — один и тот же список, shuffle меняет in-place,
    # так что отдельно перезаписывать не надо, но для явности можно:
    queues[channel_id] = queue

    await ctx.send(f"🔀 Перемешано **{len(queue)}** трек(ов) в очереди.")

@bot.command()
async def jump(ctx, index: int):
    """Перепрыгнуть на трек из очереди по номеру. Текущий трек скипается (-info показывает номера)."""
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    voice = ctx.voice_client
    if not voice:
        await ctx.send("Бот не в голосовом канале.")
        return

    channel_id = voice.channel.id
    queue = get_queue(channel_id)

    if not queue:
        await ctx.send("Очередь пуста.")
        return

    if index < 1 or index > len(queue):
        await ctx.send(f"❌ Укажи номер от 1 до {len(queue)} (см. `-info`).")
        return

    # забираем выбранный трек и ставим его в начало очереди
    chosen = queue.pop(index - 1)
    queue.insert(0, chosen)
    queues[channel_id] = queue

    # снимаем lock, как при skip — иначе play_next снова запустит историю
    locked_tracks[channel_id] = False

    if voice.is_playing() or voice.is_paused():
        # stop() дёрнет after_play → play_next → сыграет queue[0]
        voice.stop()
    else:
        # ничего не играет — запускаем воспроизведение вручную
        await play_next(ctx, bot, channel_id)

    dur = _fmt_duration(chosen.get('duration'))
    await ctx.send(f"⏭️ Прыжок на #{index}: **{chosen['title']}** `{dur}`")


@bot.command()
async def previous(ctx):
    """Вернуться к предыдущему треку. Текущий уходит в очередь первым."""
    if ctx.author.voice is None:
        await ctx.send("Ты должен быть в голосовом канале!")
        return

    voice = ctx.voice_client
    if not voice:
        await ctx.send("Бот не в голосовом канале.")
        return

    if not history:
        await ctx.send("История пуста.")
        return

    channel_id = voice.channel.id
    current = getattr(voice, 'current', None)
    current_key = (current.get('path') or current.get('title')) if current else None

    # идём с конца истории, пропуская всё, что совпадает с текущим
    # (актуально для -repeat, когда трек лежит в истории несколько раз)
    prev_track = None
    for t in reversed(history):
        key = t.get('path') or t.get('title')
        if key and key != current_key:
            prev_track = t
            break

    if not prev_track:
        await ctx.send("❌ В истории нет предыдущего трека.")
        return

    # собираем очередь: [prev, current, ...остальное]
    queue = get_queue(channel_id)
    new_queue = [prev_track]
    if current:
        new_queue.append(current)
    new_queue.extend(queue)
    queues[channel_id] = new_queue

    # снимаем lock — иначе play_next вернётся к истории вместо очереди
    locked_tracks[channel_id] = False

    if voice.is_playing() or voice.is_paused():
        voice.stop()
    else:
        await play_next(ctx, bot, channel_id)

    dur = _fmt_duration(prev_track.get('duration'))
    await ctx.send(f"⏮️ Возврат к: **{prev_track['title']}** `{dur}`")

bot.run(token)
