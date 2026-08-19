# [context: discord-selfbot-monitor, os: linux, arch: x86_64]
import logging
import sqlite3
import threading
import time

log = logging.getLogger("storage")


class Storage:
    def __init__(self, db_path="selfbot_state.db"):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()
        self.clear_stale_pending()

    def _init_tables(self):
        with self._lock:
            c = self._conn
            c.execute(
                "CREATE TABLE IF NOT EXISTS processed_messages ("
                "message_id INTEGER PRIMARY KEY)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS dm_cooldowns ("
                "user_id INTEGER PRIMARY KEY,"
                "last_dm_ts REAL)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS dm_queue ("
                "user_id INTEGER,"
                "session_key TEXT,"
                "status TEXT,"
                "created_ts REAL,"
                "UNIQUE(user_id, session_key))"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS dm_sent_log ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "user_id INTEGER,"
                "username TEXT,"
                "trigger_author TEXT,"
                "message_pool_index INTEGER,"
                "sent_ts REAL)"
            )
            c.commit()

    def clear_stale_pending(self):
        with self._lock:
            self._conn.execute("DELETE FROM dm_queue WHERE status = 'pending'")
            self._conn.commit()

    def mark_processed(self, message_id):
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO processed_messages (message_id) VALUES (?)",
                (message_id,),
            )
            self._conn.commit()
            inserted = cur.rowcount > 0
            log.debug("[DB] mark_processed message_id=%s inserted=%s", message_id, inserted)
            return inserted

    def claim_dm_slot(self, user_id, session_key):
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO dm_queue "
                "(user_id, session_key, status, created_ts) "
                "VALUES (?, ?, 'pending', ?)",
                (user_id, session_key, time.time()),
            )
            self._conn.commit()
            claimed = cur.rowcount > 0
            log.info(
                "[DB] claim_dm_slot user_id=%s session=%s claimed=%s",
                user_id,
                session_key,
                claimed,
            )
            return claimed

    def complete_dm(self, user_id, session_key, success):
        with self._lock:
            self._conn.execute(
                "DELETE FROM dm_queue WHERE user_id = ? AND session_key = ?",
                (user_id, session_key),
            )
            self._conn.commit()
        log.debug(
            "[DB] complete_dm user_id=%s session=%s success=%s",
            user_id,
            session_key,
            success,
        )

    def check_cooldown(self, user_id, cooldown_seconds):
        with self._lock:
            row = self._conn.execute(
                "SELECT last_dm_ts FROM dm_cooldowns WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return True
        elapsed = time.time() - row[0]
        return elapsed >= cooldown_seconds

    def record_dm(self, user_id):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO dm_cooldowns (user_id, last_dm_ts) "
                "VALUES (?, ?)",
                (user_id, time.time()),
            )
            self._conn.commit()

    def log_sent(self, user_id, username, trigger_author, pool_index):
        with self._lock:
            self._conn.execute(
                "INSERT INTO dm_sent_log "
                "(user_id, username, trigger_author, message_pool_index, sent_ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, username, trigger_author, pool_index, time.time()),
            )
            self._conn.commit()