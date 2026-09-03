# [context: discord-selfbot-monitor, os: linux, arch: x86_64]
import logging
import re
import time

log = logging.getLogger("handlers")

REQUEST_PATTERN = re.compile(r"request by:\s*<@!?(\d+)>", re.IGNORECASE)
# tolerates both "@ProVfx" and "ProVfx" and the blue-mention display form
REQUEST_PATTERN_NAME = re.compile(
    r"request by:\s*@?([^\s<>@]+)", re.IGNORECASE
)


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


def _combined_content(message, content_override=None):
    if content_override is not None:
        parts = [content_override]
    else:
        parts = [message.content or ""]
    for embed in message.embeds:
        if embed.title:
            parts.append(embed.title)
        if embed.description:
            parts.append(embed.description)
        if embed.author and embed.author.name:
            parts.append(embed.author.name)
        if embed.footer and embed.footer.text:
            parts.append(embed.footer.text)
        for field in embed.fields:
            if field.name:
                parts.append(field.name)
            if field.value:
                parts.append(field.value)
    return "\n".join(parts)


def _extract_target_user_id(message, content_override=None):
    combined = _combined_content(message, content_override)
    clean = _strip_markdown(combined)
    match = REQUEST_PATTERN.search(clean)
    if match:
        return int(match.group(1))
    match = REQUEST_PATTERN_NAME.search(clean)
    if match:
        return match.group(1)
    return None


def _find_user_by_name(client, name):
    for guild in client.guilds:
        for member in guild.members:
            for attr in ("name", "display_name", "global_name"):
                value = getattr(member, attr, None)
                if value is not None and value == name:
                    return member
    return None


async def handle_message(
    message,
    settings,
    storage,
    dm_sender,
    client,
    content_override=None,
):
    if message.channel.id != settings.paid_request_channel_id:
        return
    if not _author_matches(message, settings.paid_request_trigger_author):
        log.info(
            "[PAID] author mismatch — got name=%r display=%r global=%r "
            "want=%r message_id=%s",
            getattr(message.author, "name", None),
            getattr(message.author, "display_name", None),
            getattr(message.author, "global_name", None),
            settings.paid_request_trigger_author,
            message.id,
        )
        return

    target = _extract_target_user_id(message, content_override)
    if target is None:
        log.info(
            "[PAID] author matched but request pattern missing "
            "message_id=%s raw_content=%r embeds=%d override=%r",
            message.id,
            message.content,
            len(message.embeds),
            content_override,
        )
        return

    if isinstance(target, int):
        user = client.get_user(target)
        if user is None:
            try:
                user = await client.fetch_user(target)
            except Exception as exc:
                log.error(
                    "[PAID] failed to resolve target user_id=%s exc=%s",
                    target,
                    exc,
                )
                return
    else:
        user = _find_user_by_name(client, target)
        if user is None:
            log.info(
                "[PAID] could not resolve target name=%r via guilds", target
            )
            return

    if not storage.mark_processed(message.id):
        log.debug("[PAID] duplicate message_id=%s", message.id)
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

    ok = await dm_sender.send_dm_sequence(
        user, message.author.name, session_key
    )
    if not ok:
        log.info("[PAID] └─ DM not delivered target_user=%s", user.name)
