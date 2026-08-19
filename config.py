# [context: discord-selfbot-monitor, os: linux, arch: x86_64]
import os
from dataclasses import dataclass


@dataclass
class Settings:
    discord_token: str
    paid_request_channel_id: int
    paid_request_trigger_author: str
    dm_messages: list
    dm_rotation: str
    dm_cooldown_seconds: float
    dm_delay_min_seconds: float
    dm_delay_max_seconds: float
    part_delay_min_seconds: float
    part_delay_max_seconds: float
    tg_bot_token: str
    tg_chat_id: int


def load_env_file(path=".env"):
    env = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                env[key] = val
    return env


def load_settings(env=None):
    if env is None:
        env = load_env_file()
    data = {**os.environ, **env}

    def get(key, default=None, required=False):
        val = data.get(key, default)
        if required and (val is None or val == ""):
            raise ValueError(f"Missing required env var: {key}")
        return val

    rotation = get("DM_ROTATION", "random").strip().lower()
    if rotation not in ("random", "sequential"):
        rotation = "random"

    raw_messages = get("DM_MESSAGES", "")
    dm_messages = [m for m in raw_messages.split("|||") if m.strip()]

    return Settings(
        discord_token=get("DISCORD_TOKEN", required=True),
        paid_request_channel_id=int(get("PAID_REQUEST_CHANNEL_ID", "0")),
        paid_request_trigger_author=get(
            "PAID_REQUEST_TRIGGER_AUTHOR", required=True
        ),
        dm_messages=dm_messages,
        dm_rotation=rotation,
        dm_cooldown_seconds=float(get("DM_COOLDOWN_SECONDS", "86400")),
        dm_delay_min_seconds=float(get("DM_DELAY_MIN_SECONDS", "5")),
        dm_delay_max_seconds=float(get("DM_DELAY_MAX_SECONDS", "15")),
        part_delay_min_seconds=float(get("PART_DELAY_MIN_SECONDS", "4")),
        part_delay_max_seconds=float(get("PART_DELAY_MAX_SECONDS", "10")),
        tg_bot_token=get("TG_BOT_TOKEN", ""),
        tg_chat_id=int(get("TG_CHAT_ID", "0")),
    )