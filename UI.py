import discord
import asyncio


from system import (
    _parse_time,
    _fmt_duration,
    queues,
    locked_tracks,
    get_position,
    mark_resumed,
    mark_paused,
    refresh_np,
    get_volume,
    set_volume,
    DEFAULT_VOLUME,
    MAX_VOLUME,
    cancel_prefetch,
)


# ─── интерактивное NP ──────────────────────────────────────────

class SeekModal(discord.ui.Modal, title="Перейти к позиции"):
    time_input = discord.ui.TextInput(
        label="Время",
        placeholder="1:30 или 90 или 1:02:30",
        required=True,
        max_length=12,
    )

    def __init__(self, voice):
        super().__init__()
        self.voice = voice

    async def on_submit(self, interaction: discord.Interaction):
        sec = _parse_time(self.time_input.value)
        if sec is None or sec < 0:
            await interaction.response.send_message(
                "❌ Формат: `1:30`, `90`, `1:02:30`.", ephemeral=True
            )
            return

        current = getattr(self.voice, 'current', None)
        dur = (current or {}).get('duration') or 0
        if dur and sec >= dur:
            await interaction.response.send_message(
                f"❌ Трек длится `{_fmt_duration(dur)}`.", ephemeral=True
            )
            return

        self.voice.seek_to = sec
        self.voice.stop()  # → after_play → play_next (seek-ветка)

        label = "начало" if sec == 0 else _fmt_duration(sec)
        await interaction.response.send_message(
            f"⏩ Перемотка на `{label}`", ephemeral=True
        )

class QueueSelect(discord.ui.Select):
    """Выпадашка с треками очереди. До 25 штук, дальше — пока не влезет."""

    def __init__(self, voice, channel_id):
        self.voice = voice
        self.channel_id = channel_id

        queue = queues.get(channel_id, [])
        options = []
        for i, t in enumerate(queue[:25], start=1):
            title = t.get('title', 'Неизвестно')
            label = f"{i}. {title}"[:100]  # Discord лимит на label — 100 символов
            dur = _fmt_duration(t.get('duration'))
            options.append(
                discord.SelectOption(label=label, description=f"⏱ {dur}", value=str(i))
            )

        super().__init__(
            placeholder="Куда прыгнуть?",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        idx = int(self.values[0])
        v = self.voice
        ch = self.channel_id

        # защита: очередь могла измениться между открытием и выбором
        queue = queues.get(ch, [])
        if not queue or idx > len(queue):
            await interaction.response.send_message(
                "❌ Трек уже пропал из очереди (кто-то удалил или переставил).",
                ephemeral=True,
            )
            return

        chosen = queue.pop(idx - 1)

        # возвращаем текущий трек на место выбранного (свап)
        current = getattr(v, 'current', None)
        if current and current.get('path'):
            queue.insert(idx - 1, {
                'title': current['title'],
                'url': current['path'],
                'duration': current.get('duration', 0),
            })

        # выбранный — в начало
        queue.insert(0, chosen)
        queues[ch] = queue

        # снимаем loop, иначе play_next вернётся к истории
        locked_tracks[ch] = False

        if v.is_playing() or v.is_paused():
            v.stop()  # → after_play → play_next → сыграет queue[0]
        else:
            # не запускаем play_next вручную — нам тут неоткуда взять ctx/bot.
            # но в этой ветке мы и не окажемся: кнопки живут на NP-сообщении,
            # которое существует только когда что-то играет.
            pass

        dur = _fmt_duration(chosen.get('duration'))
        await interaction.response.send_message(
            f"⏭️ Прыжок на **{chosen['title']}** `{dur}`",
            ephemeral=True,
        )


class QueueView(discord.ui.View):
    """Контейнер для одного селекта. Живёт 60 сек, потом сам гаснет."""

    def __init__(self, voice, channel_id):
        super().__init__(timeout=60)
        self.add_item(QueueSelect(voice, channel_id))

class PlayerControls(discord.ui.View):
    """Ряд кнопок под NP-сообщением. Работает на текущем voice."""

    def __init__(self, voice):
        super().__init__(timeout=None)
        self.voice = voice

    def _ok(self, interaction: discord.Interaction) -> bool:
        v = self.voice
        if not v or not v.is_connected():
            return False
        mv = getattr(interaction.user, 'voice', None)
        if not mv or mv.channel != v.channel:
            return False
        return True

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if not self._ok(interaction):
            await interaction.response.send_message(
                "🔇 Зайди в тот же голосовой канал, что и бот.", ephemeral=True
            )
            return False
        return True

    async def _rel_seek(self, interaction: discord.Interaction, delta: int):
        if not await self._guard(interaction):
            return
        v = self.voice
        current = getattr(v, 'current', None)
        if not current:
            await interaction.response.send_message(
                "⚠️ Нет активного трека.", ephemeral=True
            )
            return

        pos = get_position(v)
        dur = current.get('duration') or 0
        target = pos + delta
        if target < 0:
            target = 0
        if dur and target >= dur:
            target = max(0, dur - 2)

        v.seek_to = target
        v.stop()
        await interaction.response.defer()

    # ряд 1
    @discord.ui.button(emoji="⏪", label="30", style=discord.ButtonStyle.secondary, row=0)
    async def back30(self, interaction, button):
        await self._rel_seek(interaction, -30)

    @discord.ui.button(emoji="⏪", label="10", style=discord.ButtonStyle.secondary, row=0)
    async def back10(self, interaction, button):
        await self._rel_seek(interaction, -10)

    @discord.ui.button(emoji="⏯", style=discord.ButtonStyle.primary, row=0)
    async def toggle(self, interaction, button):
        if not await self._guard(interaction):
            return
        v = self.voice
        if v.is_paused():
            v.resume()
            mark_resumed(v)
        elif v.is_playing():
            v.pause()
            mark_paused(v)
        else:
            await interaction.response.send_message(
                "⚠️ Ничего не играет.", ephemeral=True
            )
            return
        await refresh_np(v)
        await interaction.response.defer()

    @discord.ui.button(emoji="⏩", label="10", style=discord.ButtonStyle.secondary, row=0)
    async def fwd10(self, interaction, button):
        await self._rel_seek(interaction, +10)

    @discord.ui.button(emoji="⏩", label="30", style=discord.ButtonStyle.secondary, row=0)
    async def fwd30(self, interaction, button):
        await self._rel_seek(interaction, +30)

    # ряд 2
    @discord.ui.button(emoji="📍", label="Перейти", style=discord.ButtonStyle.success, row=1)
    async def jump(self, interaction, button):
        if not await self._guard(interaction):
            return
        await interaction.response.send_modal(SeekModal(self.voice))

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, row=1)
    async def loop_toggle(self, interaction, button):
        if not await self._guard(interaction):
            return
        ch = self.voice.channel.id
        locked_tracks[ch] = not locked_tracks.get(ch, False)
        state = "вкл" if locked_tracks[ch] else "выкл"
        await interaction.response.send_message(
            f"🔁 Повтор: **{state}**", ephemeral=True
        )

    @discord.ui.button(emoji="⏭", label="Скип", style=discord.ButtonStyle.danger, row=1)
    async def skip_btn(self, interaction, button):
        if not await self._guard(interaction):
            return

        v = self.voice
        ch = v.channel.id
        locked_tracks[ch] = False
        cancel_prefetch(v)  # ← новое

        if v.is_playing() or v.is_paused():
            v.stop()
            await interaction.response.send_message("⏭️ Скип.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "⚠️ Ничего не играет.", ephemeral=True
            )

    @discord.ui.button(emoji="📃", label="Очередь", style=discord.ButtonStyle.secondary, row=1)
    async def queue_btn(self, interaction, button):
        if not await self._guard(interaction):
            return

        v = self.voice
        ch = v.channel.id
        queue = queues.get(ch, [])

        if not queue:
            await interaction.response.send_message(
                "📭 Очередь пуста — некуда прыгать.", ephemeral=True
            )
            return

        view = QueueView(v, ch)
        extra = "" if len(queue) <= 25 else f"\n_(показаны первые 25 из {len(queue)})_"
        await interaction.response.send_message(
            "📃 Куда прыгнуть?" + extra,
            view=view,
            ephemeral=True,
        )

    async def _change_volume(self, interaction: discord.Interaction, delta: float):
        if not await self._guard(interaction):
            return

        v = self.voice
        ch = v.channel.id

        cur = get_volume(ch)
        new = max(0.0, min(MAX_VOLUME, cur + delta))
        vol = set_volume(ch, new)

        # применяем к текущему источнику
        src = getattr(v, "source", None)
        if isinstance(src, discord.PCMVolumeTransformer):
            src.volume = vol

        # запоминаем на voice — на случай, если source пересоздадут
        v._vol = vol

        await interaction.response.send_message(
            f"🔊 Громкость: **{int(vol * 100)}%**",
            ephemeral=True,
        )

    @discord.ui.button(emoji="🔉", label="10", style=discord.ButtonStyle.secondary, row=2)
    async def vol_down(self, interaction, button):
        await self._change_volume(interaction, -0.1)

    @discord.ui.button(emoji="🔊", label="10", style=discord.ButtonStyle.secondary, row=2)
    async def vol_up(self, interaction, button):
        await self._change_volume(interaction, +0.1)

    @discord.ui.button(emoji="🔇", label="Mute", style=discord.ButtonStyle.secondary, row=2)
    async def mute_toggle(self, interaction, button):
        if not await self._guard(interaction):
            return

        v = self.voice
        ch = v.channel.id

        cur = get_volume(ch)

        if cur > 0.0:
            # уходим в мут — запоминаем текущую громкость
            v._vol_before_mute = cur
            vol = set_volume(ch, 0.0)
            label = "🔇 Мут"
        else:
            # возвращаемся из мута
            back = getattr(v, '_vol_before_mute', DEFAULT_VOLUME) or DEFAULT_VOLUME
            vol = set_volume(ch, back)
            label = f"🔊 {int(vol * 100)}%"

        src = getattr(v, "source", None)
        if isinstance(src, discord.PCMVolumeTransformer):
            src.volume = vol

        v._vol = vol

        await interaction.response.send_message(label, ephemeral=True)

    @discord.ui.button(emoji="🔊", label="100%", style=discord.ButtonStyle.success, row=2)
    async def vol_reset(self, interaction, button):
        if not await self._guard(interaction):
            return

        v = self.voice
        ch = v.channel.id

        vol = set_volume(ch, DEFAULT_VOLUME)

        src = getattr(v, "source", None)
        if isinstance(src, discord.PCMVolumeTransformer):
            src.volume = vol

        v._vol = vol

        await interaction.response.send_message(
            f"🔊 Громкость: **{int(vol * 100)}%**", ephemeral=True
        )



class RenamePlaylistModal(discord.ui.Modal, title="Переименовать плейлист"):
    new_name = discord.ui.TextInput(
        label="Новое имя",
        placeholder="Мой плейлист 2.0",
        required=True,
        max_length=50,
    )

    def __init__(self, playlist, on_done):
        super().__init__()
        self.playlist = playlist
        self.on_done = on_done
        self.new_name.default = playlist["name"]

    async def on_submit(self, interaction: discord.Interaction):
        await self.on_done(interaction, self.new_name.value)


class PlaylistTrackSelect(discord.ui.Select):
    """Селект для удаления/перемещения трека."""

    def __init__(self, playlist, on_pick):
        self.playlist = playlist
        self.on_pick = on_pick

        tracks = playlist.get("tracks", [])
        options = [
            discord.SelectOption(
                label=f"{i}. {t['title']}"[:100],
                description=f"⏱ {_fmt_duration(t.get('duration'))}",
                value=str(i - 1),
            )
            for i, t in enumerate(tracks[:25], start=1)
        ]

        super().__init__(
            placeholder="Выбери трек",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.on_pick(interaction, int(self.values[0]))


class PlaylistEditorView(discord.ui.View):
    def __init__(self, playlist, author_id, refresh_cb, selected_index=None):
        super().__init__(timeout=180)
        self.playlist = playlist
        self.author_id = author_id
        self.refresh_cb = refresh_cb

        # запоминаем выбранный индекс
        if selected_index is not None and 0 <= selected_index < len(playlist.get("tracks", [])):
            self.selected_index = selected_index
        elif hasattr(self, "selected_index"):
            # если old selected_index был — не сбрасываем
            pass

        if playlist.get("tracks"):
            self.add_item(PlaylistTrackSelect(
                playlist,
                self._on_pick,
                default_index=selected_index,
            ))

    async def interaction_check(self, interaction):
        if interaction.user.id == self.author_id:
            return True
        if interaction.guild and interaction.user.guild_permissions.manage_guild:
            return True
        await interaction.response.send_message(
            "❌ Только автор или админ сервера.", ephemeral=True
        )
        return False

    async def _on_pick(self, interaction, index):
        """Запомнили выбранный трек — кнопки удаления/перемещения теперь знают, что брать."""
        self.selected_index = index
        title = self.playlist["tracks"][index]["title"]
        await interaction.response.send_message(
            f"Выбран: **{title}**", ephemeral=True
        )

    # ─── кнопки ───────────────────────────────────────
    @discord.ui.button(label="Удалить", emoji="🗑", style=discord.ButtonStyle.danger, row=1)
    async def delete_btn(self, interaction, button):
        if not hasattr(self, "selected_index"):
            await interaction.response.send_message(
                "Сначала выбери трек в селекте.", ephemeral=True
            )
            return

        import playlists as pl
        ok, removed = pl.remove_track_at(self.playlist["name"], self.author_id, self.selected_index)

        if not ok:
            await interaction.response.send_message("❌ Не удалось удалить.", ephemeral=True)
            return

        self.playlist["tracks"].pop(self.selected_index)
        del self.selected_index

        await interaction.response.send_message(
            f"🗑 Удалён: **{removed['title']}**", ephemeral=True
        )
        await self.refresh_cb(interaction, self.playlist, None)

    @discord.ui.button(label="Переименовать", emoji="✏️", style=discord.ButtonStyle.primary, row=1)
    async def rename_btn(self, interaction, button):
        async def on_done(inter, new_name):
            import playlists as pl
            ok, err = pl.rename(self.playlist["name"], new_name, self.author_id)
            if not ok:
                await inter.response.send_message(f"❌ {err}", ephemeral=True)
                return
            self.playlist["name"] = new_name.strip()
            await inter.response.send_message(f"✏️ Переименован в **{new_name}**.", ephemeral=True)
            await self.refresh_cb(inter)

        await interaction.response.send_modal(RenamePlaylistModal(self.playlist, on_done))

    @discord.ui.button(label="Убрать дубли", emoji="🔁", style=discord.ButtonStyle.secondary, row=1)
    async def dedup_btn(self, interaction, button):
        import playlists as pl
        ok, removed = pl.dedup(self.playlist["name"], self.author_id)

        if not ok:
            await interaction.response.send_message("❌ Ошибка.", ephemeral=True)
            return

        if removed == 0:
            await interaction.response.send_message(
                "Дубликатов нет.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"🔁 Удалено дубликатов: **{removed}**", ephemeral=True
        )
        # перезагружаем плейлист
        pl_data = pl.load(self.playlist["name"])
        if pl_data:
            self.playlist["tracks"] = pl_data["tracks"]
        await self.refresh_cb(interaction, self.playlist)

    @discord.ui.button(label="Удалить плейлист", emoji="❌", style=discord.ButtonStyle.danger, row=3)
    async def delete_all_btn(self, interaction, button):
        view = _DeleteConfirmView(interaction.user.id, self._on_delete_confirm)
        await interaction.response.send_message(
            f"Удалить плейлист **{self.playlist['name']}** целиком?",
            view=view,
            ephemeral=True,
        )

    async def _on_delete_confirm(self, confirmation):
        import playlists as pl
        ok = pl.delete(self.playlist["name"], self.author_id)
        if ok:
            await confirmation.response.send_message(
                f"🗑 Плейлист **{self.playlist['name']}** удалён.", ephemeral=True
            )
            self.stop()
        else:
            await confirmation.response.send_message("❌ Не удалось.", ephemeral=True)

    @discord.ui.button(label="Шафл", emoji="🔀", style=discord.ButtonStyle.secondary, row=2)
    async def shuffle_btn(self, interaction, button):
        import playlists as pl
        ok, total = pl.shuffle(self.playlist["name"], self.author_id)

        if not ok:
            await interaction.response.send_message("❌ Ошибка.", ephemeral=True)
            return

        if total < 2:
            await interaction.response.send_message(
                "Меньше двух треков — нечего мешать.", ephemeral=True
            )
            return

        pl_data = pl.load(self.playlist["name"])
        if pl_data:
            self.playlist["tracks"] = pl_data["tracks"]

        await interaction.response.send_message(
            f"🔀 Перемешано **{total}** трек(ов).", ephemeral=True
        )
        await self.refresh_cb(interaction, self.playlist, None)  # ← сброс

    @discord.ui.button(label="Вверх", emoji="⬆️", style=discord.ButtonStyle.primary, row=2)
    async def move_up_btn(self, interaction, button):
        await self._move(interaction, -1)

    @discord.ui.button(label="Вниз", emoji="⬇️", style=discord.ButtonStyle.primary, row=2)
    async def move_down_btn(self, interaction, button):
        await self._move(interaction, +1)

    async def _move(self, interaction, direction):
        if not hasattr(self, "selected_index"):
            await interaction.response.send_message(
                "Сначала выбери трек в селекте.", ephemeral=True
            )
            return

        import playlists as pl
        ok, new_index, err = pl.move_track(
            self.playlist["name"], self.author_id, self.selected_index, direction
        )

        if not ok:
            await interaction.response.send_message(
                f"❌ {err or 'не удалось'}", ephemeral=True
            )
            return

        pl_data = pl.load(self.playlist["name"])
        if pl_data:
            self.playlist["tracks"] = pl_data["tracks"]

        # запоминаем новый индекс
        self.selected_index = new_index

        arrow = "⬆️" if direction < 0 else "⬇️"
        title = self.playlist["tracks"][new_index]["title"]
        await interaction.response.send_message(
            f"{arrow} **{title}** теперь на позиции `{new_index + 1}`.",
            ephemeral=True,
        )
        await self.refresh_cb(interaction, self.playlist, new_index)

    def _rebuild_select(self):
        """Пересоздаёт селект с обновлёнными треками."""
        for child in list(self.children):
            if isinstance(child, PlaylistTrackSelect):
                self.remove_item(child)
        if self.playlist.get("tracks"):
            self.add_item(PlaylistTrackSelect(self.playlist, self._on_pick))


class PlaylistPickerView(discord.ui.View):
    """Первое окно: выбор плейлиста из списка."""

    def __init__(self, playlists, user_id, is_admin, on_pick):
        super().__init__(timeout=120)
        self.playlists = playlists
        self.user_id = user_id
        self.is_admin = is_admin
        self.on_pick = on_pick

        options = [
            discord.SelectOption(
                label=p["name"][:100],
                description=f"{len(p.get('tracks', []))} трек(ов) • {p.get('author_name', '?')}",
                value=str(i),
            )
            for i, p in enumerate(playlists[:25])
        ]

        select = discord.ui.Select(
            placeholder="Выбери плейлист для редактирования",
            options=options,
            min_values=1,
            max_values=1,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.user_id:
            return True
        if self.is_admin and interaction.user.guild_permissions.manage_guild:
            return True
        await interaction.response.send_message("Не твоё.", ephemeral=True)
        return False

    async def _on_select(self, interaction):
        idx = int(interaction.data["values"][0])
        chosen = self.playlists[idx]
        await self.on_pick(interaction, chosen)
        self.stop()

class _DeleteConfirmView(discord.ui.View):
    """Подтверждение удаления плейлиста."""

    def __init__(self, author_id, on_confirm):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.on_confirm = on_confirm

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Это не твоё подтверждение.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Да, удалить", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await self.on_confirm(interaction)
        self.stop()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.send_message("Отменено.", ephemeral=True)
        self.stop()

class PlaylistTrackSelect(discord.ui.Select):
    def __init__(self, playlist, on_pick, default_index=None):
        self.playlist = playlist
        self.on_pick = on_pick

        tracks = playlist.get("tracks", [])
        options = []
        for i, t in enumerate(tracks[:25], start=1):
            options.append(
                discord.SelectOption(
                    label=f"{i}. {t['title']}"[:100],
                    description=f"⏱ {_fmt_duration(t.get('duration'))}",
                    value=str(i - 1),
                    default=(default_index == i - 1),
                )
            )

        super().__init__(
            placeholder="Выбери трек",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.on_pick(interaction, int(self.values[0]))