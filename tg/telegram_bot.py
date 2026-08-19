# [context: discord-selfbot-monitor, os: linux, arch: x86_64]
import asyncio
import logging
import time

import aiohttp

from tg.state import POST_RESUME_DELAY

log = logging.getLogger("tg.telegram_bot")

API_URL = "https://api.telegram.org/bot{token}/{method}"


class TelegramLogHandler(logging.Handler):
    def __init__(self, queue):
        super().__init__()
        self.queue = queue

    def emit(self, record):
        name = record.name
        if name.startswith("tg.") or name.startswith("signals"):
            return
        try:
            self.queue.put_nowait(self.format(record))
        except Exception:
            pass


class TelegramBot:
    def __init__(self, token, chat_id, captcha_queue,
                 discord_client=None, polling=True):
        self.token = token
        self.chat_id = chat_id
        self.captcha_queue = captcha_queue
        self.discord_client = discord_client
        self.polling = polling
        self.log_queue = asyncio.Queue()
        self.log_forwarding = True
        self._session = None
        self._offset = 0
        self._stop = False
        self._attached = False
        self._started_at = time.time()

    def attach_logging(self):
        if self._attached:
            return
        self._attached = True
        handler = TelegramLogHandler(self.log_queue)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(handler)
        log.info("telegram log handler attached")

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    async def _api(self, method, http_timeout=15, **params):
        await self._ensure_session()
        url = API_URL.format(token=self.token, method=method)
        async with self._session.post(
            url,
            json=params,
            timeout=aiohttp.ClientTimeout(total=http_timeout),
        ) as resp:
            return await resp.json()

    @staticmethod
    def _chunks(text):
        for i in range(0, len(text), 4000):
            yield text[i : i + 4000]

    async def _send_message(self, text):
        for chunk in self._chunks(text):
            for attempt in range(3):
                try:
                    data = await self._api(
                        "sendMessage", http_timeout=15,
                        chat_id=self.chat_id, text=chunk,
                    )
                    if data.get("ok"):
                        return
                    log.error("sendMessage not ok: %s", data)
                except Exception as exc:
                    log.error(
                        "telegram sendMessage failed: %s: %s",
                        type(exc).__name__, exc,
                    )
                await asyncio.sleep(1)

    async def notify_captcha(self, waiter):
        text = (
            "[CAPTCHA]\n"
            f"user={waiter.username} ({waiter.user_id})\n"
            f"part={waiter.part_index}\n"
            f"queue_size={len(self.captcha_queue)}\n"
            f"time={time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await self._send_message(text)

    async def _drain_stale_updates(self):
        while True:
            try:
                data = await self._api(
                    "getUpdates", http_timeout=10, timeout=0, offset=-1,
                )
            except Exception as exc:
                log.error(
                    "drain getUpdates failed: %s: %s",
                    type(exc).__name__, exc,
                )
                return
            result = data.get("result", [])
            if not result:
                return
            self._offset = max(u["update_id"] for u in result) + 1
            log.info(
                "drained %d stale updates, offset=%s",
                len(result), self._offset,
            )

    async def _poll_loop(self):
        await self._drain_stale_updates()
        log.info("telegram poll loop started offset=%s", self._offset)
        while not self._stop:
            try:
                data = await self._api(
                    "getUpdates", http_timeout=50,
                    timeout=30, offset=self._offset,
                )
            except Exception as exc:
                log.error(
                    "getUpdates failed: %s: %s",
                    type(exc).__name__, exc,
                )
                await asyncio.sleep(2)
                continue
            if not data.get("ok"):
                log.error("getUpdates API error: %s", data)
                await asyncio.sleep(2)
                continue
            for update in data.get("result", []):
                self._offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue
                chat = message.get("chat", {})
                if str(chat.get("id")) != str(self.chat_id):
                    continue
                await self._handle_command(message.get("text", ""))
            await asyncio.sleep(0.2)

    async def _handle_command(self, text):
        if not text or not text.startswith("/"):
            return
        parts = text.split()
        cmd = parts[0].lower().lstrip("/")

        if cmd == "resume":
            waiter = self.captcha_queue.pop_next()
            if waiter is None:
                await self._send_message("no pending captcha waiters")
                return
            waiter.resume()
            await self._send_message(
                f"resumed {waiter.username} — retrying part "
                f"{waiter.part_index} after {POST_RESUME_DELAY}s"
            )

        elif cmd == "skip":
            if len(parts) < 2:
                await self._send_message("usage: /skip <n>")
                return
            try:
                n = int(parts[1])
            except ValueError:
                await self._send_message("invalid position")
                return
            waiter = self.captcha_queue.remove(n - 1)
            if waiter is None:
                await self._send_message(f"no waiter at position {n}")
                return
            waiter.abort()
            await self._send_message(
                f"aborted waiter at position {n} ({waiter.username})"
            )

        elif cmd == "queue":
            await self._send_queue()

        elif cmd == "logs":
            if len(parts) > 1:
                if parts[1].lower() in ("on", "off"):
                    self.log_forwarding = parts[1].lower() == "on"
                else:
                    await self._send_message("usage: /logs on|off")
                    return
            state = "on" if self.log_forwarding else "off"
            await self._send_message(f"log forwarding is {state}")

        elif cmd == "status":
            await self._send_status()

        elif cmd == "help":
            await self._send_message(
                "/resume — unblock next captcha waiter\n"
                "/skip <n> — abort waiter at queue position n\n"
                "/queue — show pending captcha waiters\n"
                "/logs on|off — toggle log forwarding\n"
                "/logs — show log forwarding state\n"
                "/status — show account and uptime\n"
                "/help — this list"
            )

        else:
            await self._send_message(f"unknown command: /{cmd}")

    async def _send_queue(self):
        waiters = self.captcha_queue.pending()
        if not waiters:
            await self._send_message("captcha queue empty")
            return
        lines = ["CAPTCHA QUEUE"]
        for i, w in enumerate(waiters, start=1):
            lines.append(
                f"#{i} user={w.username} ({w.user_id}) "
                f"part={w.part_index} wait={int(w.wait_time)}s"
            )
        await self._send_message("\n".join(lines))

    async def _send_status(self):
        if self.discord_client is None:
            await self._send_message("discord client not attached")
            return
        user = self.discord_client.user
        if user is None:
            await self._send_message("discord not logged in yet")
            return
        ready = self.discord_client.is_ready()
        uptime = int(time.time() - self._started_at)
        h, rem = divmod(uptime, 3600)
        m, s = divmod(rem, 60)
        uptime_str = f"{h}h {m}m {s}s" if h else f"{m}m {s}s"
        await self._send_message(
            f"🤖 STATUS\n"
            f"account: {user.name} ({user.id})\n"
            f"discord_ready: {ready}\n"
            f"bot_uptime: {uptime_str}\n"
            f"captcha_queue: {len(self.captcha_queue)} pending"
        )

    async def _log_forwarder_loop(self):
        lines = []
        batch_start = None
        while not self._stop:
            try:
                line = await asyncio.wait_for(self.log_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                line = None
            if line is not None:
                if batch_start is None:
                    batch_start = time.monotonic()
                lines.append(line)
            if lines and (
                len(lines) >= 10
                or (
                    batch_start is not None
                    and time.monotonic() - batch_start >= 2.0
                )
            ):
                if self.log_forwarding:
                    await self._send_message("\n".join(lines[:10]))
                lines = []
                batch_start = None

    def stop(self):
        self._stop = True

    async def run(self):
        self.attach_logging()
        try:
            if self.polling:
                await asyncio.gather(
                    self._poll_loop(), self._log_forwarder_loop()
                )
            else:
                await self._log_forwarder_loop()
        finally:
            if self._session and not self._session.closed:
                await self._session.close()