# [context: discord-selfbot-monitor, os: linux, arch: x86_64]
import logging
import re
import time

log = logging.getLogger("handlers")

REQUEST_PATTERN = re.compile(r"request by:\s*<@!?(\d+)>", re.IGNORECASE)


def _strip_markdown(text):
    for marker in ("***", "__", "~~", "**", "*", "_"):
        text = text.replace(marker, "")
    return text


def _author_matches(message, trigger_author):
    author = message.author
    for attr in ("name", "display_name", "global_name"):
        value = getattr(author, attr, None)
        if value is not None and value == trigger_author:
            return True
    return False


def _extract_target_user_id(content):
    clean = _strip_markdown(content)
    match = REQUEST_PATTERN.search(clean)
    if match:
        return int(match.group(1))
    return None


async def handle_message(message, settings, storage, dm_sender, client):
    if message.channel.id != settings.paid_request_channel_id:
        return
    if not _author_matches(message, settings.paid_request_trigger_author):
        return

    target_user_id = _extract_target_user_id(message.content)
    if target_user_id is None:
        log.info(
            "[PAID] trigger author matched but request pattern missing "
            "message_id=%s author=%s",
            message.id,
            message.author.name,
        )
        return

    if not storage.mark_processed(message.id):
        log.debug("[PAID] duplicate message_id=%s", message.id)
        return

    user = client.get_user(target_user_id)
    if user is None:
        try:
            user = await client.fetch_user(target_user_id)
        except Exception as exc:
            log.error(
                "[PAID] failed to resolve target user_id=%s exc=%s",
                target_user_id,
                exc,
            )
            return

    log.info(
        "[PAID] ┌─ request detected\n"
        "[PAID] │ timestamp=%s\n"
        "[PAID] │ trigger_author=%s\n"
        "[PAID] │ target_user=%s (%s)\n"
        "[PAID] │ channel_id=%s message_id=%s",
        time.strftime("%Y-%m-%d %H:%M:%S"),
        message.author.name,
        user.name,
        user.id,
        message.channel.id,
        message.id,
    )

    if not storage.check_cooldown(user.id, settings.dm_cooldown_seconds):
        log.info("[PAID] └─ skipped target_user=%s reason=cooldown", user.name)
        return

    session_key = f"user-{user.id}"
    if not storage.claim_dm_slot(user.id, session_key):
        log.info(
            "[PAID] └─ skipped target_user=%s reason=duplicate_slot", user.name
        )
        return

    ok = await dm_sender.send_dm_sequence(user, message.author.name, session_key)
    if not ok:
        log.info("[PAID] └─ DM not delivered target_user=%s", user.name)