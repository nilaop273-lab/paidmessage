# [context: discord-selfbot-monitor, os: linux, arch: x86_64]
import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import aiohttp

from config import load_env_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("watchdog")

BASE_DIR = Path(__file__).resolve().parent
MAIN_SCRIPT = BASE_DIR / "main.py"
PYTHON_BIN = sys.executable
SIG_DIR = BASE_DIR / ".watchdog_signals"
SIG_DIR.mkdir(exist_ok=True)

_env = load_env_file(str(BASE_DIR / ".env"))
TG_BOT_TOKEN = (_env.get("TG_BOT_TOKEN") or os.getenv("TG_BOT_TOKEN", "")).strip()
TG_CHAT_ID = int(_env.get("TG_CHAT_ID") or os.getenv("TG_CHAT_ID", "0") or "0")

API_URL = "https://api.telegram.org/bot{token}/{method}"
_MAX_CHARS = 4000
_POLL_TIMEOUT = 2

_proc = None
_started_at = None
_log_handle = None


def _is_running():
    return _proc is not None and _proc.poll() is None


def _uptime():
    if _started_at is None:
        return "unknown"
    secs = int(time.time() - _started_at)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


async def _send(session, text):
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + "\n…(truncated)"
    try:
        async with session.post(
            API_URL.format(token=TG_BOT_TOKEN, method="sendMessage"),
            json={"chat_id": TG_CHAT_ID, "text": text},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.error("sendMessage failed %d: %s", resp.status, body[:300])
    except Exception as exc:
        logger.error("sendMessage exception: %s: %s", type(exc).__name__, exc)


async def _get_updates(session, offset):
    try:
        async with session.post(
            API_URL.format(token=TG_BOT_TOKEN, method="getUpdates"),
            json={"timeout": _POLL_TIMEOUT, "offset": offset},
            timeout=aiohttp.ClientTimeout(total=_POLL_TIMEOUT + 10),
        ) as resp:
            if resp.status == 409:
                logger.error(
                    "409 Conflict — another process is polling this bot token. "
                    "Ensure main.py is only started via watchdog, not directly."
                )
                await asyncio.sleep(5)
                return []
            if resp.status != 200:
                logger.warning("getUpdates returned %d", resp.status)
                return []
            data = await resp.json()
            return data.get("result", [])
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("getUpdates exception: %s: %s", type(exc).__name__, exc)
        return []


async def _drain_stale(session):
    try:
        async with session.post(
            API_URL.format(token=TG_BOT_TOKEN, method="getUpdates"),
            json={"timeout": 0, "offset": -1},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get("result", [])
                if results:
                    off = results[-1]["update_id"] + 1
                    logger.info("drained %d stale update(s)", len(results))
                    return off
    except Exception as exc:
        logger.warning("drain failed: %s: %s", type(exc).__name__, exc)
    return 0


def _clean_signals():
    for p in SIG_DIR.glob("cmd_*"):
        try:
            p.unlink()
        except OSError:
            pass


def _start_bot():
    global _proc, _started_at, _log_handle

    if _is_running():
        return False, f"⚠️ Bot already running (pid: {_proc.pid})"
    if not MAIN_SCRIPT.exists():
        return False, f"❌ main.py not found at {MAIN_SCRIPT}"

    _clean_signals()

    env = os.environ.copy()
    env["WATCHDOG_MODE"] = "true"

    log_path = BASE_DIR / f"bot_{int(time.time())}.log"
    try:
        _log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
    except OSError as exc:
        logger.warning("could not open log file %s: %s", log_path, exc)
        _log_handle = None

    out = _log_handle if _log_handle else None
    err = _log_handle if _log_handle else None

    try:
        _proc = subprocess.Popen(
            [PYTHON_BIN, "-u", str(MAIN_SCRIPT)],
            env=env,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        _started_at = time.time()
        note = f"  log → {log_path.name}" if _log_handle else "  (terminal only)"
        logger.info("started main.py (pid: %d)%s", _proc.pid, note)
        return (
            True,
            f"✅ Bot started (pid: {_proc.pid})\n"
            f"📄 Log: {log_path.name if _log_handle else 'terminal'}\n"
            "💡 Send /status or /queue when ready",
        )
    except Exception as exc:
        return False, f"❌ Failed to start: {exc}"


async def _stop_bot(session):
    global _proc, _started_at, _log_handle

    if not _is_running():
        _proc = None
        _started_at = None
        return "⚠️ Bot is not running."

    pid = _proc.pid
    logger.info("sending SIGTERM to main.py (pid: %d)", pid)

    try:
        if sys.platform == "win32":
            _proc.terminate()
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        _proc = None
        _started_at = None
        return "✅ Bot was already dead — cleaned up."
    except Exception as exc:
        return f"❌ SIGTERM failed: {exc}"

    await _send(session, f"⏳ Stopping bot (pid: {pid}) — waiting up to 8s…")

    for _ in range(16):
        await asyncio.sleep(0.5)
        if not _is_running():
            break
    else:
        logger.warning("main.py still alive after SIGTERM — sending SIGKILL")
        try:
            if sys.platform == "win32":
                _proc.kill()
            else:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            pass
        await asyncio.sleep(1)

    _proc = None
    _started_at = None
    if _log_handle is not None:
        try:
            _log_handle.close()
        except Exception:
            pass
        _log_handle = None
    logger.info("main.py stopped (pid: %d)", pid)
    return f"🛑 Bot stopped (pid: {pid})"


def _write_cmd(name):
    path = SIG_DIR / name
    try:
        path.write_text(str(time.time()), encoding="utf-8")
        logger.info("signal written: %s", name)
    except Exception as exc:
        logger.error("failed to write signal %s: %s", name, exc)


async def _check_crash(session):
    global _proc, _started_at
    if _proc is not None and not _is_running():
        rc = _proc.returncode
        await _send(
            session,
            f"⚠️ Bot exited unexpectedly (code={rc})\nSend /start to restart",
        )
        _proc = None
        _started_at = None


async def _route(session, text):
    text = text.strip()
    lower = text.lower()

    if lower.startswith("/start"):
        ok, msg = _start_bot()
        await _send(session, msg)

    elif lower.startswith("/stop"):
        msg = await _stop_bot(session)
        await _send(session, msg)

    elif lower.startswith("/status"):
        if _is_running():
            assert _proc is not None
            await _send(
                session,
                f"🤖 Bot: RUNNING\n   pid: {_proc.pid}  •  uptime: {_uptime()}\n"
                "📋 For captcha queue: send /queue",
            )
        else:
            await _send(session, "🤖 Bot: STOPPED")

    elif lower.startswith("/resume"):
        if not _is_running():
            await _send(session, "⚠️ Bot is not running — /start it first")
            return
        _write_cmd("cmd_resume")
        await _send(session, "⏳ Resume signal sent — bot will reply shortly")

    elif lower.startswith("/skip"):
        if not _is_running():
            await _send(session, "⚠️ Bot is not running")
            return
        parts = text.split()
        if len(parts) < 2:
            _write_cmd("cmd_queue")
            await _send(
                session,
                "Usage: /skip <number>\nExample: /skip 1\n\n"
                "Requesting queue so you can see positions…",
            )
            return
        try:
            pos = int(parts[1])
        except ValueError:
            await _send(session, f"❌ Invalid position: '{parts[1]}'")
            return
        if pos < 1:
            await _send(session, "❌ Position must be 1 or higher")
            return
        _write_cmd(f"cmd_skip_{pos}")
        await _send(session, f"🗑 Skip signal sent for position {pos}")

    elif lower.startswith("/queue"):
        if not _is_running():
            await _send(session, "⚠️ Bot is not running — queue empty")
            return
        _write_cmd("cmd_queue")
        await _send(session, "📋 Queue requested — bot will reply shortly")

    elif lower.startswith("/logs"):
        if not _is_running():
            await _send(session, "⚠️ Bot is not running")
            return
        if "off" in lower:
            _write_cmd("cmd_logs_off")
            await _send(session, "🔇 Log mute signal sent")
        elif "on" in lower:
            _write_cmd("cmd_logs_on")
            await _send(session, "🔊 Log enable signal sent")
        else:
            _write_cmd("cmd_logs_status")
            await _send(session, "📋 Log status requested")

    elif lower.startswith("/help"):
        await _send(
            session,
            "📖  COMMAND LIST\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔧  PROCESS CONTROL\n"
            "/start     start the bot\n"
            "/stop      stop the bot gracefully\n"
            "/status    bot process state + uptime\n"
            "\n"
            "⚠️  CAPTCHA\n"
            "/resume      unblock next stuck DM\n"
            "/skip <n>    cancel DM at queue position n\n"
            "/queue       show full captcha queue\n"
            "\n"
            "📋  LOGS\n"
            "/logs on   enable log forwarding\n"
            "/logs off  mute log forwarding\n"
            "/logs      check current log state\n"
            "\n"
            "❓  OTHER\n"
            "/help      show this message",
        )

    else:
        await _send(
            session,
            f"❓ Unknown command: {text}\nSend /help for the full list",
        )


async def run():
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print(
            "ERROR: TG_BOT_TOKEN and TG_CHAT_ID must be set in .env",
            file=sys.stderr,
        )
        sys.exit(1)

    logger.info("watchdog started — sole Telegram poller on this token")

    async with aiohttp.ClientSession() as session:
        offset = await _drain_stale(session)
        await _send(
            session,
            "👀  Watchdog online\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "/start  — start the bot\n"
            "/stop   — stop the bot\n"
            "/status — process state\n"
            "/help   — all commands",
        )

        while True:
            updates = await _get_updates(session, offset)
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message") or update.get("edited_message")
                if not message:
                    continue
                chat_id = str(message.get("chat", {}).get("id", ""))
                if chat_id != str(TG_CHAT_ID):
                    continue
                await _route(session, message.get("text") or "")

            await _check_crash(session)


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("watchdog shutting down")
        if _is_running() and _proc is not None:
            logger.info("terminating main.py (pid: %d)", _proc.pid)
            _proc.terminate()


if __name__ == "__main__":
    main()