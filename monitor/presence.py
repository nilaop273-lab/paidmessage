# [context: discord-selfbot-monitor, os: linux, arch: x86_64]
import asyncio
import logging
from typing import List, Optional, Tuple

import discord

log = logging.getLogger("presence")

ACTIVITY_TYPES = {
    "playing": discord.ActivityType.playing,
    "watching": discord.ActivityType.watching,
    "listening": discord.ActivityType.listening,
    "competing": discord.ActivityType.competing,
    "streaming": discord.ActivityType.streaming,
}


class PresenceManager:
    """
    Holds current presence state and applies it to the Discord client.
    Supports a rotating cycle of activities with per-step durations.
    """

    def __init__(self):
        self.enabled = True
        self.activity_type = "playing"
        self.activity_text = ""
        self.status = discord.Status.online
        self._client: Optional[discord.Client] = None
        self._rotate_task: Optional[asyncio.Task] = None
        # list of (activity_type, text, duration_seconds)
        self._cycle: List[Tuple[str, str, float]] = []
        self._cycle_index = 0

    def attach(self, client: discord.Client):
        self._client = client

    def describe(self) -> str:
        if self._cycle and self._rotate_task and not self._rotate_task.done():
            steps = ", ".join(
                f"{t}:{txt!r}({int(d)}s)" for t, txt, d in self._cycle
            )
            return (
                f"presence: ROTATING [{steps}] "
                f"now={self.activity_type} {self.activity_text!r} "
                f"(status={self.status})"
            )
        if not self.enabled or not self.activity_text:
            return f"presence: off (status={self.status})"
        return (
            f"presence: {self.activity_type} {self.activity_text!r} "
            f"(status={self.status})"
        )

    async def apply(self):
        if self._client is None or self._client.user is None:
            return
        try:
            if not self.enabled or not self.activity_text:
                await self._client.change_presence(
                    activity=None, status=self.status
                )
                log.info("[PRESENCE] cleared — %s", self.describe())
                return

            atype = ACTIVITY_TYPES.get(
                self.activity_type.lower(), discord.ActivityType.playing
            )
            if atype == discord.ActivityType.streaming:
                activity = discord.Streaming(
                    name=self.activity_text,
                    url="https://twitch.tv/discord",
                )
            else:
                activity = discord.Activity(type=atype, name=self.activity_text)
            await self._client.change_presence(
                activity=activity, status=self.status
            )
            log.info("[PRESENCE] applied — %s", self.describe())
        except Exception as exc:
            log.error("[PRESENCE] apply failed: %s: %s", type(exc).__name__, exc)

    async def set_activity(self, activity_type: str, text: str):
        # static presence cancels rotation
        await self.stop_rotation()
        activity_type = (activity_type or "playing").strip().lower()
        if activity_type not in ACTIVITY_TYPES:
            activity_type = "playing"
        self.activity_type = activity_type
        self.activity_text = (text or "").strip()
        self.enabled = bool(self.activity_text)
        await self.apply()

    async def set_status(self, status_name: str):
        mapping = {
            "online": discord.Status.online,
            "idle": discord.Status.idle,
            "dnd": discord.Status.dnd,
            "invisible": discord.Status.invisible,
            "offline": discord.Status.invisible,
        }
        self.status = mapping.get(
            (status_name or "online").strip().lower(), discord.Status.online
        )
        await self.apply()

    async def clear(self):
        await self.stop_rotation()
        self.enabled = False
        self.activity_text = ""
        await self.apply()

    async def stop_rotation(self):
        task = self._rotate_task
        self._rotate_task = None
        self._cycle = []
        self._cycle_index = 0
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def start_rotation(self, steps: List[Tuple[str, str, float]]):
        """
        steps: list of (activity_type, text, duration_seconds)
        Cycles forever until stop_rotation / set_activity / clear.
        """
        if not steps:
            await self.clear()
            return
        await self.stop_rotation()
        self._cycle = steps
        self._cycle_index = 0
        self.enabled = True
        self._rotate_task = asyncio.create_task(self._rotate_loop())
        log.info("[PRESENCE] rotation started (%d steps)", len(steps))

    async def _rotate_loop(self):
        try:
            while self._cycle:
                idx = self._cycle_index % len(self._cycle)
                atype, text, duration = self._cycle[idx]
                self.activity_type = atype
                self.activity_text = text
                self.enabled = True
                await self.apply()
                self._cycle_index = (idx + 1) % len(self._cycle)
                await asyncio.sleep(max(1.0, float(duration)))
        except asyncio.CancelledError:
            log.info("[PRESENCE] rotation stopped")
            raise
        except Exception as exc:
            log.error("[PRESENCE] rotation crashed: %s: %s", type(exc).__name__, exc)
