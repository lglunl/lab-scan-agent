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
            CREATE TABLE IF NOT EXISTS booking_folders (
                booking_id TEXT PRIMARY KEY,
                folder_path TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS booking_deliveries (
                booking_id TEXT PRIMARY KEY,
                folder_path TEXT NOT NULL,
                email TEXT NOT NULL,
                username TEXT NOT NULL,
                check_in_at TEXT NOT NULL,
                check_out_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                reported INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                next_attempt REAL NOT NULL DEFAULT 0,
                file_signature TEXT NOT NULL DEFAULT '',
                signature_since REAL NOT NULL DEFAULT 0
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


    # 以下表及方法专供预约服务器 desk 协议的自动邮件流程使用。
    def recover_unfinished_bookings(self) -> None:
        """仅在 Agent 启动时调用，不在 CLI 查询数据库时改写正在发送的任务。"""
        with self._lock:
            self._conn.execute(
                "UPDATE booking_deliveries SET status='uncertain', reported=0, "
                "error='发送过程意外中断：请检查收件箱后决定是否手动重发' "
                "WHERE status='sending'"
            )
            self._conn.commit()

    def remember_folder(self, booking_id: str, folder: Path) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO booking_folders (booking_id, folder_path) VALUES (?, ?)",
                (booking_id, str(folder)),
            )
            self._conn.commit()

    def folder_for_booking(self, booking_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT folder_path FROM booking_folders WHERE booking_id=?", (booking_id,)).fetchone()
            return row["folder_path"] if row else None

    def enqueue_booking(self, booking: dict, now: float) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR IGNORE INTO booking_deliveries
                (booking_id, folder_path, email, username, check_in_at, check_out_at, created_at, next_attempt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(booking["id"]), str(booking.get("folderPath") or ""),
                    str(booking.get("email") or "").strip(),
                    str(booking.get("applicant") or ""),
                    str(booking.get("checkInAt") or ""),
                    str(booking.get("checkOutAt") or ""), now, now,
                ),
            )
            self._conn.commit()

    def get_booking_delivery(self, booking_id: str):
        with self._lock:
            return self._conn.execute("SELECT * FROM booking_deliveries WHERE booking_id=?", (booking_id,)).fetchone()

    def ready_booking(self, now: float):
        with self._lock:
            return self._conn.execute(
                """SELECT * FROM booking_deliveries WHERE status='queued' AND next_attempt<=?
                ORDER BY created_at LIMIT 1""", (now,),
            ).fetchone()

    def snapshot_booking(self, booking_id: str, signature: str, now: float) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE booking_deliveries SET file_signature=?, signature_since=?
                WHERE booking_id=? AND status='queued'""", (signature, now, booking_id),
            )
            self._conn.commit()

    def mark_booking_sending(self, booking_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE booking_deliveries SET status='sending', attempts=attempts+1 WHERE booking_id=? AND status='queued'",
                (booking_id,),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    def postpone_booking(self, booking_id: str, until: float, error: str) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE booking_deliveries SET status='queued', next_attempt=?, error=?,
                file_signature='', signature_since=0 WHERE booking_id=? AND status='sending'""",
                (until, error, booking_id),
            )
            self._conn.commit()

    def finish_booking(self, booking_id: str, status: str, error: str = "") -> None:
        if status not in {"sent", "failed", "skipped", "uncertain"}:
            raise ValueError("invalid mail delivery state")
        with self._lock:
            self._conn.execute(
                "UPDATE booking_deliveries SET status=?, error=?, reported=0 WHERE booking_id=?",
                (status, error, booking_id),
            )
            self._conn.commit()

    def unreported_booking(self):
        with self._lock:
            return self._conn.execute(
                """SELECT * FROM booking_deliveries
                WHERE reported=0 AND status IN ('sent', 'failed', 'skipped', 'uncertain')
                ORDER BY created_at LIMIT 1"""
            ).fetchone()

    def confirm_booking_report(self, booking_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE booking_deliveries SET reported=1 WHERE booking_id=?", (booking_id,))
            self._conn.commit()

    def retry_booking(self, booking_id: str, now: float, *, allow_uncertain: bool = False) -> None:
        with self._lock:
            status = ("'failed','uncertain'" if allow_uncertain else "'failed'")
            cursor = self._conn.execute(
                f"""UPDATE booking_deliveries SET status='queued', error='', reported=0,
                attempts=0, next_attempt=?, file_signature='', signature_since=0
                WHERE booking_id=? AND status IN ({status})""",
                (now, booking_id),
            )
            self._conn.commit()
            if cursor.rowcount != 1:
                raise RuntimeError("只有发送失败的订单可重试；发送结果不确定时需显式确认")
