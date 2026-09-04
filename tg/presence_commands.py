# [context: discord-selfbot-monitor, os: linux, arch: x86_64]
"""
Telegram command handlers for Discord presence control.
"""

from __future__ import annotations

import logging
import re

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
    "/presence clear     clear activity + stop rotation\n"
    "/presence status <online|idle|dnd|invisible>\n"
    "/presence rotate <spec>\n"
    "  example:\n"
    "  /presence rotate playing:paid requests:10, watching:the board:5, listening:rain:8, streaming:live:12\n"
    "  format: type:text:seconds, type:text:seconds, ..."
)

_ROTATE_SPLIT = re.compile(r"\s*,\s*")


def _parse_rotate_spec(spec: str):
    """
    Parse: playing:hello world:10, watching:board:5, listening:x:8
    Returns list[(type, text, duration)] or raises ValueError.
    """
    spec = (spec or "").strip()
    if not spec:
        raise ValueError("empty rotate spec")
    steps = []
    for chunk in _ROTATE_SPLIT.split(spec):
        chunk = chunk.strip()
        if not chunk:
            continue
        # split from the right so text can contain colons rarely; expect type:text:seconds
        parts = chunk.rsplit(":", 2)
        if len(parts) != 3:
            raise ValueError(
                f"bad step {chunk!r} — use type:text:seconds "
                "(example playing:paid requests:10)"
            )
        atype, text, dur_s = parts[0].strip().lower(), parts[1].strip(), parts[2].strip()
        if atype in ("stream",):
            atype = "streaming"
        if atype not in ("playing", "watching", "listening", "competing", "streaming"):
            raise ValueError(f"unknown type {atype!r}")
        if not text:
            raise ValueError("empty text in step")
        try:
            dur = float(dur_s)
        except ValueError:
            raise ValueError(f"bad duration {dur_s!r}")
        if dur < 1:
            dur = 1.0
        steps.append((atype, text, dur))
    if not steps:
        raise ValueError("no steps parsed")
    return steps


async def handle_presence_command(text: str, presence, reply_coro) -> bool:
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
        await reply_coro("presence cleared (rotation stopped)")
        return True

    if sub == "status":
        if not rest:
            await reply_coro("usage: /presence status <online|idle|dnd|invisible>")
            return True
        await presence.set_status(rest.split()[0])
        await reply_coro(f"status set — {presence.describe()}")
        return True

    if sub in ("rotate", "cycle", "shuffle"):
        if not rest:
            await reply_coro(
                "usage:\n"
                "/presence rotate playing:paid requests:10, watching:the board:5, "
                "listening:rain:8, streaming:live:12"
            )
            return True
        try:
            steps = _parse_rotate_spec(rest)
        except ValueError as exc:
            await reply_coro(f"bad rotate spec: {exc}")
            return True
        await presence.start_rotation(steps)
        await reply_coro(f"rotation started — {presence.describe()}")
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
