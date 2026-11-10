from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path


class Occupied(Exception):
    def __init__(self, username: str, check_in_at: str) -> None:
        self.username = username
        self.check_in_at = check_in_at


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                username TEXT NOT NULL,
                folder_path TEXT NOT NULL,
                check_in_at TEXT NOT NULL,
                check_out_at TEXT,
                email TEXT,
                send_status TEXT,
                send_error TEXT,
                booking_id TEXT
            );
            CREATE TABLE IF NOT EXISTS device_lock (
                device_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(sessions)")}
        if "booking_id" not in columns:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN booking_id TEXT")
        self._conn.commit()

    def active_session(self, device_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                """
                SELECT s.* FROM sessions s
                JOIN device_lock l ON l.session_id = s.id
                WHERE l.device_id = ?
                """,
                (device_id,),
            ).fetchone()

    def get_session(self, session_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()

    def latest_for_user(self, device_id: str, username: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                """
                SELECT * FROM sessions
                WHERE device_id = ? AND username = ?
                ORDER BY check_in_at DESC
                LIMIT 1
                """,
                (device_id, username),
            ).fetchone()

    def check_in(self, device_id: str, username: str, folder_path: str, when: datetime, booking_id: str | None = None) -> str:
        session_id = uuid.uuid4().hex
        stamp = when.isoformat(timespec="seconds")
        with self._lock:
            current = self._conn.execute(
                """
                SELECT s.username, s.check_in_at FROM sessions s
                JOIN device_lock l ON l.session_id = s.id
                WHERE l.device_id = ?
                """,
                (device_id,),
            ).fetchone()
            if current is not None:
                raise Occupied(current["username"], current["check_in_at"])
            self._conn.execute(
                """
                INSERT INTO sessions (id, device_id, username, folder_path, check_in_at, booking_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, device_id, username, folder_path, stamp, booking_id),
            )
            self._conn.execute(
                "INSERT INTO device_lock (device_id, session_id) VALUES (?, ?)",
                (device_id, session_id),
            )
            self._conn.commit()
        return session_id

    def check_out(self, session_id: str, email: str, when: datetime) -> None:
        stamp = when.isoformat(timespec="seconds")
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None or row["check_out_at"]:
                raise RuntimeError("这次上机还不能下机")
            self._conn.execute(
                """
                UPDATE sessions
                SET check_out_at = ?, email = ?, send_status = 'pending', send_error = NULL
                WHERE id = ?
                """,
                (stamp, email, session_id),
            )
            self._conn.execute("DELETE FROM device_lock WHERE session_id = ?", (session_id,))
            self._conn.execute(
                "INSERT INTO jobs (session_id, status, created_at) VALUES (?, 'pending', ?)",
                (session_id, stamp),
            )
            self._conn.commit()

    def claim_job(self) -> sqlite3.Row | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, session_id FROM jobs WHERE status = 'pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            updated = self._conn.execute(
                "UPDATE jobs SET status = 'running' WHERE id = ? AND status = 'pending'",
                (row["id"],),
            )
            if updated.rowcount != 1:
                self._conn.commit()
                return None
            self._conn.execute(
                "UPDATE sessions SET send_status = 'sending', send_error = NULL WHERE id = ?",
                (row["session_id"],),
            )
            self._conn.commit()
            return row

    def finish_job(self, job_id: int, session_id: str, status: str, error: str | None) -> None:
        with self._lock:
            self._conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
            self._conn.execute(
                "UPDATE sessions SET send_status = ?, send_error = ? WHERE id = ?",
                (status, error, session_id),
            )
            self._conn.commit()

    def retry_send(self, session_id: str, when: datetime) -> None:
        stamp = when.isoformat(timespec="seconds")
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None or not row["check_in_at"] or not row["check_out_at"]:
                raise RuntimeError("还没有完成上机和下机")
            if row["send_status"] not in {"failed"}:
                raise RuntimeError("这次发送不能重试")
            running = self._conn.execute(
                "SELECT id FROM jobs WHERE session_id = ? AND status IN ('pending', 'running')",
                (session_id,),
            ).fetchone()
            if running is not None:
                return
            self._conn.execute(
                "UPDATE sessions SET send_status = 'pending', send_error = NULL WHERE id = ?",
                (session_id,),
            )
            self._conn.execute(
                "INSERT INTO jobs (session_id, status, created_at) VALUES (?, 'pending', ?)",
                (session_id, stamp),
            )
            self._conn.commit()
