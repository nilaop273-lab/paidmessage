# [context: discord-selfbot-monitor, os: linux, arch: x86_64]
import asyncio
import logging
import random
import time

import discord

from tg.state import POST_RESUME_DELAY, Waiter

log = logging.getLogger("dm_sender")


class DMSender:
    def __init__(self, settings, storage, captcha_queue, notify_captcha_cb=None):
        self.settings = settings
        self.storage = storage
        self.captcha_queue = captcha_queue
        self.notify_captcha_cb = notify_captcha_cb
        self._seq_index = 0

    def pick_message(self):
        messages = self.settings.dm_messages
        if not messages:
            return None, -1
        if self.settings.dm_rotation == "sequential":
            idx = self._seq_index % len(messages)
            self._seq_index += 1
            return messages[idx], idx
        idx = random.randrange(len(messages))
        return messages[idx], idx

    def _split_parts(self, raw):
        text = raw.replace("\\n", "\n").replace("\r\n", "\n")
        parts = [p.strip() for p in text.split("\n")]
        return [p for p in parts if p]

    async def send_dm_sequence(self, user, trigger_author, session_key):
        message, pool_index = self.pick_message()
        if message is None:
            log.warning("[DM] no DM messages configured")
            return False
        parts = self._split_parts(message)
        if not parts:
            log.warning("[DM] message #%d contained no parts", pool_index)
            return False

        initial_delay = random.uniform(
            self.settings.dm_delay_min_seconds,
            self.settings.dm_delay_max_seconds,
        )
        log.info(
            "[DM] ┌─ DM sequence start\n"
            "[DM] │ timestamp=%s\n"
            "[DM] │ author=%s\n"
            "[DM] │ user=%s (%s)\n"
            "[DM] │ parts=%d pool_index=%d",
            time.strftime("%Y-%m-%d %H:%M:%S"),
            trigger_author,
            user.name,
            user.id,
            len(parts),
            pool_index,
        )
        await asyncio.sleep(initial_delay)

        try:
            for i, part in enumerate(parts):
                ok = await self._send_part(user, part, i, trigger_author)
                if not ok:
                    self.storage.complete_dm(user.id, session_key, False)
                    log.info("[DM] └─ DM sequence aborted user=%s", user.name)
                    return False
                if i < len(parts) - 1:
                    delay = random.uniform(
                        self.settings.part_delay_min_seconds,
                        self.settings.part_delay_max_seconds,
                    )
                    await asyncio.sleep(delay)
        except Exception as exc:
            self.storage.complete_dm(user.id, session_key, False)
            log.error("[DM] └─ DM sequence failed user=%s exc=%s", user.name, exc)
            return False

        self.storage.complete_dm(user.id, session_key, True)
        self.storage.record_dm(user.id)
        self.storage.log_sent(user.id, user.name, trigger_author, pool_index)
        log.info("[DM] └─ DM sequence complete user=%s", user.name)
        return True

    async def _send_part(self, user, content, part_index, trigger_author):
        while True:
            try:
                await user.send(content)
                log.info(
                    "[DM] part %d sent user=%s content_len=%d",
                    part_index + 1,
                    user.name,
                    len(content),
                )
                return True
            except discord.HTTPException as exc:
                text = str(exc).lower()
                if "captcha" in text or getattr(exc, "code", None) == -1:
                    waiter = Waiter(
                        user.id, user.name, part_index + 1, trigger_author
                    )
                    self.captcha_queue.push(waiter)
                    log.warning(
                        "[DM] captcha hit user=%s part=%d queue_size=%d",
                        user.name,
                        part_index + 1,
                        len(self.captcha_queue),
                    )
                    if self.notify_captcha_cb is not None:
                        try:
                            await self.notify_captcha_cb(waiter)
                        except Exception as exc_notify:
                            log.error(
                                "[DM] telegram captcha notify failed: %s",
                                exc_notify,
                            )
                    await waiter.wait()
                    if waiter.skipped:
                        return False
                    await asyncio.sleep(POST_RESUME_DELAY)
                    continue
                log.error(
                    "[DM] HTTPException user=%s part=%d code=%s text=%s",
                    user.name,
                    part_index + 1,
                    getattr(exc, "code", None),
                    exc,
                )
                return False