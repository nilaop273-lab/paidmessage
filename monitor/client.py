# [context: discord-selfbot-monitor, os: linux, arch: x86_64]
import logging

import discord

from monitor.handlers import handle_message

log = logging.getLogger("monitor")


class MonitorClient(discord.Client):
    def __init__(self, settings, storage, dm_sender=None):
        super().__init__()
        self.cfg = settings
        self._storage = storage
        self._dm_sender = dm_sender

    async def on_ready(self):
        user = self.user
        name = user.name if user else "unknown"
        uid = user.id if user else "?"

        banner = (
            "┌─ SELF BOT STARTED ─\n"
            f"│ account={name} ({uid})\n"
            f"│ channel={self.cfg.paid_request_channel_id}\n"
            f"│ trigger_author={self.cfg.paid_request_trigger_author}\n"
            f"│ cooldown={self.cfg.dm_cooldown_seconds}s\n"
            f"│ initial_delay={self.cfg.dm_delay_min_seconds}-"
            f"{self.cfg.dm_delay_max_seconds}s\n"
            f"│ part_delay={self.cfg.part_delay_min_seconds}-"
            f"{self.cfg.part_delay_max_seconds}s\n"
            f"│ pool_size={len(self.cfg.dm_messages)}\n"
            "└─ READY"
        )
        log.info("[PAID] %s", banner)

    async def on_message(self, message):
        if self.user is None or message.author.id == self.user.id:
            return

        if message.channel.id != self.cfg.paid_request_channel_id:
            return

        # Log what the gateway delivered
        log.info(
            "[PAID] saw message in channel "
            "message_id=%s author_name=%r display_name=%r "
            "global_name=%r bot=%s content=%r embeds=%d",
            message.id,
            getattr(message.author, "name", None),
            getattr(message.author, "display_name", None),
            getattr(message.author, "global_name", None),
            getattr(message.author, "bot", False),
            message.content,
            len(message.embeds),
        )

        # If the gateway stripped content, fetch the full message via HTTP
        if not message.content and not message.embeds:
            try:
                fetched = await message.channel.fetch_message(message.id)
                if fetched:
                    log.info(
                        "[PAID] fetched full message "
                        "message_id=%s content=%r embeds=%d",
                        fetched.id,
                        fetched.content,
                        len(fetched.embeds),
                    )
                    message = fetched
            except Exception as exc:
                log.error(
                    "[PAID] fetch_message failed message_id=%s "
                    "error=%s: %s",
                    message.id,
                    type(exc).__name__,
                    exc,
                )

        if self._dm_sender is None:
            log.error("dm_sender not set — ignoring message")
            return

        await handle_message(
            message, self.cfg, self._storage, self._dm_sender, self
        )
