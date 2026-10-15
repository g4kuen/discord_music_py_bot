# help.py
"""Красивая -help для музыкального бота."""

import discord
from discord.ext import commands


# ── категории: имя → список названий команд ──────────────────
CATEGORIES = {
    "🎵 Воспроизведение": [
        "play", "pause", "resume", "skip", "previous", "jump",
        "seek", "volume", "repeat", "shuffle", "np",
    ],
    "📃 Очередь и история": [
        "list", "info", "remove", "clear", "story", "remember",
        "playlist", "cache",
    ],
    "🔧 Утилиты": [
        "join", "echo", "привет",
    ],
}


def _fmt_command(cmd: commands.Command) -> str:
    """`-name <args>` — короткое описание из docstring."""
    sig = []
    for name, p in cmd.clean_params.items():
        if p.default is p.empty:
            sig.append(f"<{name}>")
        else:
            sig.append(f"[{name}]")
    args = " " + " ".join(sig) if sig else ""

    # первая строка docstring — короткое описание
    doc = (cmd.help or "").strip().split("\n")[0] or "—"
    return f"`-{cmd.name}{args}` — {doc}"


class MusicHelp(commands.HelpCommand):
    """-help — обзор, -help <команда> — подробно про одну."""

    def get_command_signature(self, command: commands.Command) -> str:
        return f"-{command.qualified_name} {command.signature}".rstrip()

    async def send_bot_help(self, mapping):
        embed = discord.Embed(
            title="🎧 Музыкальный бот",
            description=(
                f"Префикс: `{self.context.clean_prefix}`\n"
                f"Всего команд: **{len(self.context.bot.commands)}**\n\n"
                f"Подробнее про команду: `-help <название>`"
            ),
            color=discord.Color.blurple(),
        )

        # собираем все известные имена
        all_names = set()
        for names in CATEGORIES.values():
            all_names.update(names)

        # категории
        for cat_name, cmd_names in CATEGORIES.items():
            lines = []
            for name in cmd_names:
                cmd = self.context.bot.get_command(name)
                if cmd:
                    lines.append(_fmt_command(cmd))
            if lines:
                embed.add_field(
                    name=cat_name,
                    value="\n".join(lines),
                    inline=False,
                )

        # всё, что не попало ни в одну категорию — отдельный блок
        leftovers = [
            _fmt_command(c)
            for c in self.context.bot.commands
            if c.name not in all_names and not c.hidden
        ]
        if leftovers:
            embed.add_field(
                name="📦 Прочее",
                value="\n".join(leftovers),
                inline=False,
            )

        embed.set_footer(text="venom_bot")
        await self.get_destination().send(embed=embed)

    async def send_command_help(self, command: commands.Command):
        embed = discord.Embed(
            title=f"`-{command.qualified_name}`",
            description=command.help or "Описание отсутствует.",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Использование",
            value=f"`{self.get_command_signature(command)}`",
            inline=False,
        )
        if command.aliases:
            embed.add_field(
                name="Алиасы",
                value=", ".join(f"`-{a}`" for a in command.aliases),
                inline=False,
            )
        await self.get_destination().send(embed=embed)

    async def send_group_help(self, group):
        # если однажды появятся группы (например, -queue clear)
        await self.send_command_help(group)

    async def send_cog_help(self, cog):
        await self.send_bot_help(None)

    async def send_error_message(self, error):
        embed = discord.Embed(
            title="❌ Ошибка",
            description=error,
            color=discord.Color.red(),
        )
        await self.get_destination().send(embed=embed)