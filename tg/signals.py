# [context: discord-selfbot-monitor, os: linux, arch: x86_64]
import asyncio
import logging
import time
from pathlib import Path
from urllib.parse import unquote_plus

from tg.presence_commands import handle_presence_command
from tg.state import POST_RESUME_DELAY

log = logging.getLogger("signals")
SIG_DIR_NAME = ".watchdog_signals"


class SignalManager:
    def __init__(self, captcha_queue, telegram_bot=None, presence=None):
        self.captcha_queue = captcha_queue
        self.telegram_bot = telegram_bot
        self.presence = presence
        self.sig_dir = (
            Path(__file__).resolve().parent.parent / SIG_DIR_NAME
        )
        self.sig_dir.mkdir(exist_ok=True)
        self._clean_stale_cmds()

    def _clean_stale_cmds(self):
        for p in self.sig_dir.glob("cmd_*"):
            try:
                p.unlink()
            except OSError:
                pass

    async def _reply(self, text):
        if self.telegram_bot is not None:
            await self.telegram_bot._send_message(text)
        else:
            log.info("no telegram bot attached, dropping reply: %s", text)

    async def watch_loop(self):
        log.info("signal manager watching %s", self.sig_dir)
        while True:
            await self._process_commands()
            await asyncio.sleep(1)

    async def _process_commands(self):
        for path in sorted(self.sig_dir.glob("cmd_*")):
            name = path.name
            try:
                path.unlink()
            except OSError:
                continue
            try:
                await self._handle_command(name)
            except Exception as exc:
                log.error(
                    "command %s failed: %s: %s",
                    name,
                    type(exc).__name__,
                    exc,
                )

    async def _handle_command(self, name):
        if name == "cmd_resume":
            waiter = self.captcha_queue.pop_next()
            if waiter is not None:
                waiter.resume()
                await self._reply(
                    f"resumed {waiter.username} — retrying part "
                    f"{waiter.part_index} after {POST_RESUME_DELAY}s"
                )
            else:
                await self._reply("captcha queue empty")
        elif name.startswith("cmd_skip_"):
            try:
                pos = int(name.rsplit("_", 1)[1])
            except ValueError:
                return
            waiter = self.captcha_queue.remove(pos - 1)
            if waiter is not None:
                waiter.abort()
                await self._reply(f"aborted #{pos} ({waiter.username})")
            else:
                await self._reply(f"no waiter at position {pos}")
        elif name == "cmd_queue":
            waiters = self.captcha_queue.pending()
            if not waiters:
                await self._reply("captcha queue empty")
                return
            lines = ["CAPTCHA QUEUE"]
            for i, w in enumerate(waiters, start=1):
                lines.append(
                    f"#{i} user={w.username} ({w.user_id}) "
                    f"part={w.part_index} wait={int(w.wait_time)}s"
                )
            await self._reply("\n".join(lines))
        elif name == "cmd_logs_on":
            if self.telegram_bot is not None:
                self.telegram_bot.log_forwarding = True
                await self._reply("log forwarding is on")
            else:
                await self._reply("log forwarding unavailable — no TG token")
        elif name == "cmd_logs_off":
            if self.telegram_bot is not None:
                self.telegram_bot.log_forwarding = False
                await self._reply("log forwarding is off")
            else:
                await self._reply("log forwarding unavailable — no TG token")
        elif name == "cmd_logs_status":
            if self.telegram_bot is not None:
                state = "on" if self.telegram_bot.log_forwarding else "off"
                await self._reply(f"log forwarding is {state}")
            else:
                await self._reply("log forwarding unavailable — no TG token")
        elif name.startswith("cmd_presence__"):
            payload = unquote_plus(name[len("cmd_presence__") :])
            cmd_text = "/" + payload if not payload.startswith("/") else payload
            if self.presence is None:
                await self._reply("presence manager not attached")
                return
            handled = await handle_presence_command(
                cmd_text, self.presence, self._reply
            )
            if not handled:
                await self._reply(f"unknown presence command: {cmd_text}")
        else:
            log.warning("unknown signal command: %s", name)
