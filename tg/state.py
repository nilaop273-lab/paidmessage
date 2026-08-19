# [context: discord-selfbot-monitor, os: linux, arch: x86_64]
import asyncio
import time

POST_RESUME_DELAY = 3.0


class Waiter:
    def __init__(self, user_id, username, part_index, trigger_author):
        self.user_id = user_id
        self.username = username
        self.part_index = part_index
        self.trigger_author = trigger_author
        self.created_at = time.time()
        self.skipped = False
        self._event = asyncio.Event()

    def resume(self):
        self.skipped = False
        self._event.set()

    def abort(self):
        self.skipped = True
        self._event.set()

    async def wait(self):
        await self._event.wait()

    @property
    def wait_time(self):
        return max(0.0, time.time() - self.created_at)


class CaptchaQueue:
    def __init__(self):
        self._items = []

    def push(self, waiter):
        self._items.append(waiter)

    def pop_next(self):
        if self._items:
            return self._items.pop(0)
        return None

    def get(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def remove(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def pending(self):
        return list(self._items)

    def __len__(self):
        return len(self._items)