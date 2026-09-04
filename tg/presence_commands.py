# [context: discord-selfbot-monitor, os: linux, arch: x86_64]
"""
Telegram command handlers for Discord presence control.

Wire into TelegramBot._handle_command (and SignalManager if you use file signals).
"""

from __future__ import annotations

import logging

log = logging.getLogger("tg.presence")

PRESENCE_HELP = (
    "🎮  PRESENCE\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "/playing <text>     set Playing status\n"
    "/watching <text>    set Watching status\n"
    "/listening <text>   set Listening status\n"
    "/competing <text>   set Competing status\n"
    "/stream <text>      set Streaming status\n"
    "/presence           show current presence\n"
    "/presence clear     clear activity\n"
    "/presence status <online|idle|dnd|invisible>"
)


async def handle_presence_command(text: str, presence, reply_coro) -> bool:
    """
    Returns True if the command was handled.
    reply_coro(str) is an awaitable that sends a Telegram reply.
    """
    if not text or not text.startswith("/"):
        return False

    parts = text.split(maxsplit=2)
    cmd = parts[0].lower().lstrip("/")

    if cmd in ("playing", "watching", "listening", "competing", "stream", "streaming"):
        atype = "streaming" if cmd in ("stream", "streaming") else cmd
        body = ""
        if len(parts) > 1:
            body = parts[1]
        if len(parts) > 2:
            body = (body + " " + parts[2]).strip()
        body = body.strip()
        if not body:
            await reply_coro(f"usage: /{cmd} <text>")
            return True
        await presence.set_activity(atype, body)
        await reply_coro(f"presence set: {presence.describe()}")
        return True

    if cmd != "presence":
        return False

    if len(parts) == 1:
        await reply_coro(presence.describe())
        return True

    sub = parts[1].lower()
    rest = parts[2] if len(parts) > 2 else ""

    if sub in ("clear", "off", "stop"):
        await presence.clear()
        await reply_coro("presence cleared")
        return True

    if sub == "status":
        if not rest:
            await reply_coro("usage: /presence status <online|idle|dnd|invisible>")
            return True
        await presence.set_status(rest.split()[0])
        await reply_coro(f"status set — {presence.describe()}")
        return True

    if sub in ("playing", "watching", "listening", "competing", "stream", "streaming"):
        atype = "streaming" if sub in ("stream", "streaming") else sub
        if not rest:
            await reply_coro(f"usage: /presence {sub} <text>")
            return True
        await presence.set_activity(atype, rest)
        await reply_coro(f"presence set: {presence.describe()}")
        return True

    if sub in ("help", "?"):
        await reply_coro(PRESENCE_HELP)
        return True

    await reply_coro(PRESENCE_HELP)
    return True
