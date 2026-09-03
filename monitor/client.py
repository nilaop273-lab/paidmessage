# [context: discord-selfbot-monitor, os: linux, arch: x86_64]
import logging
import discord
from monitor.handlers import handle_message

log = logging.getLogger("monitor")


def _extract_from_components(components):
    """Recursively pull readable text out of Components V2 (and legacy)."""
    texts = []
    if not components:
        return texts

    def walk(comp):
        if comp is None:
            return

        # dict form (raw API payload)
        if isinstance(comp, dict):
            t = comp.get("type")
            # type 10 = Text Display (Components V2)
            if t == 10:
                c = comp.get("content")
                if c and isinstance(c, str) and c.strip():
                    texts.append(c)
            for key in ("components", "children", "items"):
                for child in comp.get(key) or []:
                    walk(child)
            acc = comp.get("accessory")
            if acc:
                walk(acc)
            for key in ("label", "placeholder", "description", "title", "content"):
                val = comp.get(key)
                if isinstance(val, str) and val.strip() and val not in texts:
                    texts.append(val)
            return

        # object form (discord.py-self MessageComponent subclasses)
        content = getattr(comp, "content", None)
        if content and isinstance(content, str) and content.strip():
            texts.append(content)

        for child in (
            getattr(comp, "children", None)
            or getattr(comp, "components", None)
            or []
        ):
            walk(child)

        acc = getattr(comp, "accessory", None)
        if acc is not None:
            walk(acc)

        for attr in ("label", "placeholder", "description", "title"):
            val = getattr(comp, attr, None)
            if isinstance(val, str) and val.strip() and val not in texts:
                texts.append(val)

    for c in components:
        walk(c)
    return texts


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

        content = getattr(message, "content", "") or ""
        embeds = getattr(message, "embeds", []) or []

        log.info(
            "[PAID] saw message in channel "
            "message_id=%s author_name=%r display_name=%r "
            "global_name=%r bot=%s content=%r embeds=%d components=%d",
            message.id,
            getattr(message.author, "name", None),
            getattr(message.author, "display_name", None),
            getattr(message.author, "global_name", None),
            getattr(message.author, "bot", False),
            content,
            len(embeds),
            len(getattr(message, "components", None) or []),
        )

        # If the author is a bot, the gateway often omits content.
        # Force a full HTTP fetch to obtain it (and components).
        if getattr(message.author, "bot", False) and not content and not embeds:
            try:
                fetched = await message.channel.fetch_message(message.id)
                if fetched:
                    log.info(
                        "[PAID] fetched bot message via HTTP "
                        "message_id=%s content=%r embeds=%d components=%d",
                        fetched.id,
                        getattr(fetched, "content", "") or "",
                        len(getattr(fetched, "embeds", []) or []),
                        len(getattr(fetched, "components", None) or []),
                    )
                    message = fetched
                    content = getattr(message, "content", "") or ""
                    embeds = getattr(message, "embeds", []) or []
            except Exception as exc:
                log.error(
                    "[PAID] HTTP fetch for bot message failed: %s: %s",
                    type(exc).__name__,
                    exc,
                )

        # Components V2: text lives in TextDisplay (type 10) etc.
        components = getattr(message, "components", None) or []
        comp_texts = _extract_from_components(components)
        if comp_texts:
            comp_joined = "\n".join(comp_texts)
            log.info(
                "[PAID] extracted %d component text pieces (len=%d)",
                len(comp_texts),
                len(comp_joined),
            )
            if not content:
                content = comp_joined
            else:
                content = content + "\n" + comp_joined

        # Fallback: raw to_dict if library objects gave nothing
        if not content and not embeds and hasattr(message, "to_dict"):
            try:
                raw = message.to_dict()
                raw_comps = raw.get("components") or []
                raw_texts = _extract_from_components(raw_comps)
                if raw_texts:
                    content = "\n".join(raw_texts)
                    log.info(
                        "[PAID] extracted from raw to_dict components len=%d",
                        len(content),
                    )
            except Exception as exc:
                log.debug("[PAID] to_dict component extract failed: %s", exc)

        # Fallback for any still-empty message (reference / reply)
        if not content and not embeds:
            referenced = None
            ref = getattr(message, "reference", None)
            if ref is not None:
                ref_id = getattr(ref, "message_id", None)
                if ref_id:
                    try:
                        referenced = await message.channel.fetch_message(ref_id)
                    except Exception as exc:
                        log.error(
                            "[PAID] fetch referenced failed: %s: %s",
                            type(exc).__name__,
                            exc,
                        )
            if referenced is not None:
                ref_content = getattr(referenced, "content", "") or ""
                ref_embeds = getattr(referenced, "embeds", []) or []
                ref_comps = getattr(referenced, "components", None) or []
                ref_comp_texts = _extract_from_components(ref_comps)
                log.info(
                    "[PAID] referenced message found ref_id=%s content=%r "
                    "embeds=%d component_texts=%d",
                    referenced.id,
                    ref_content,
                    len(ref_embeds),
                    len(ref_comp_texts),
                )
                combined_content = ref_content
                if not combined_content and ref_comp_texts:
                    combined_content = "\n".join(ref_comp_texts)
                if not combined_content:
                    buf = []
                    for e in ref_embeds:
                        t = getattr(e, "title", None)
                        d = getattr(e, "description", None)
                        if t:
                            buf.append(t)
                        if d:
                            buf.append(d)
                        for f in getattr(e, "fields", []) or []:
                            buf.append(
                                f"{getattr(f, 'name', '')} {getattr(f, 'value', '')}"
                            )
                    combined_content = "\n".join(buf)
                content = combined_content
                embeds = ref_embeds
            else:
                log.warning(
                    "[PAID] message empty and no referenced text / no components. "
                    "Raw dump: %s",
                    getattr(message, "to_dict", lambda: None)(),
                )

        if self._dm_sender is None:
            log.error("dm_sender not set — ignoring message")
            return

        await handle_message(
            message,
            self.cfg,
            self._storage,
            self._dm_sender,
            self,
            content_override=content,
        )
