# [context: discord-selfbot-monitor, os: linux, arch: x86_64]
import asyncio
import logging
import os

from config import load_settings
from monitor.client import MonitorClient
from monitor.dm_sender import DMSender
from monitor.presence import PresenceManager
from monitor.storage import Storage
from tg.signals import SignalManager
from tg.state import CaptchaQueue
from tg.telegram_bot import TelegramBot


def setup_logging():
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.INFO)
        console = logging.StreamHandler()
        console.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(console)


async def main():
    setup_logging()
    settings = load_settings()
    storage = Storage()
    captcha_queue = CaptchaQueue()
    presence = PresenceManager()

    watchdog_mode = os.getenv("WATCHDOG_MODE", "").strip().lower() == "true"

    # Discord client first so TelegramBot / presence can reference it
    client = MonitorClient(settings, storage, None, presence=presence)

    telegram_bot = None
    signal_mgr = None
    notify_cb = None
    side_tasks = []

    if watchdog_mode:
        if settings.tg_bot_token and settings.tg_chat_id:
            telegram_bot = TelegramBot(
                settings.tg_bot_token,
                settings.tg_chat_id,
                captcha_queue,
                discord_client=client,
                polling=False,  # watchdog owns getUpdates
                presence=presence,
            )
            notify_cb = telegram_bot.notify_captcha
            side_tasks.append(asyncio.create_task(telegram_bot.run()))
        else:
            logging.getLogger("main").warning(
                "watchdog mode but no TG token — captcha alerts disabled"
            )
        signal_mgr = SignalManager(
            captcha_queue, telegram_bot, presence=presence
        )
        side_tasks.append(asyncio.create_task(signal_mgr.watch_loop()))
        logging.getLogger("main").info(
            "WATCHDOG_MODE=true — logs/replies sent via sendMessage "
            "directly; watchdog owns getUpdates"
        )
    else:
        if settings.tg_bot_token and settings.tg_chat_id:
            telegram_bot = TelegramBot(
                settings.tg_bot_token,
                settings.tg_chat_id,
                captcha_queue,
                discord_client=client,
                polling=True,
                presence=presence,
            )
            notify_cb = telegram_bot.notify_captcha
            side_tasks.append(asyncio.create_task(telegram_bot.run()))
        else:
            logging.getLogger("main").warning(
                "no telegram token configured — captcha alerts disabled"
            )

    dm_sender = DMSender(settings, storage, captcha_queue, notify_cb)
    client._dm_sender = dm_sender

    try:
        await client.start(settings.discord_token)
    finally:
        for task in side_tasks:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await client.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
