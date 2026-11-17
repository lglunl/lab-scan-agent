"""设备侧 Agent：同步预约台账、自动整理本次实验目录中的数据并发送邮件。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import qrcode

from labcheckin.booking import BookingClient, BookingUnavailable
from labcheckin.config import Config, page_url
from labcheckin.db import Store
from labcheckin.demo_data import generate_demo_files
from labcheckin.mail import DeliveryUncertain, SendError, collect_files, deliver, total_bytes

log = logging.getLogger("labcheckin.agent")


def make_session_folder(root: Path, username: str, when: datetime) -> Path:
    """保留旧本地会话兼容接口，不允许用户名引入路径穿越。"""
    root.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w-]", "_", username, flags=re.UNICODE).strip("._")[:40] or "user"
    base = f"{slug}_{when.strftime('%Y%m%d_%H%M%S')}"
    path = root / base
    suffix = 2
    while path.exists():
        path = root / f"{base}_{suffix}"
        suffix += 1
    path.mkdir()
    return path


def make_booking_folder(root: Path, booking_id: str, username: str, when: datetime) -> Path:
    """同一预约在重启后仍指向同一目录，不会凭空创建一个新目录。"""
    root.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w-]", "_", username, flags=re.UNICODE).strip("._")[:32] or "user"
    unique = hashlib.sha256(booking_id.encode("utf-8")).hexdigest()[:16]
    path = root / f"{slug}_{when.strftime('%Y%m%d_%H%M%S')}_{unique}"
    path.mkdir(exist_ok=True)
    return path


def write_qr(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    qrcode.make(url).save(dest)


class Agent:
    def __init__(self, cfg: Config, store: Store) -> None:
        self.cfg = cfg
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._qr_url = ""
        self.booking = BookingClient(cfg)

    def start(self) -> None:
        self.cfg.data_root.mkdir(parents=True, exist_ok=True)
        log.info("实验数据根目录（已确认）：%s", self.cfg.data_root.resolve())
        log.info("当前仪器 ID：%s；模拟数据：%s", self.cfg.instrument_id or "(未配置)",
                 "上机自动生成（测试模式）" if self.cfg.demo_seed_on_checkin else "关闭")
        if self.cfg.demo_seed_on_checkin:
            preview = self.cfg.data_root / "__DEMO_PREVIEW_DO_NOT_EMAIL__"
            generate_demo_files(preview, "LOCAL-PREVIEW-NO-BOOKING", self.cfg.instrument_id)
            log.warning("[TEST ONLY] 模拟数据预览已载入：%s（此目录不会作为预约数据邮件发送）", preview)
        self.store.recover_unfinished_bookings()
        self._thread = threading.Thread(target=self._loop, name="lab-agent", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def refresh_qr(self) -> str:
        url = page_url(self.cfg)
        if url != self._qr_url:
            write_qr(url, self.cfg.qr_path)
            write_qr(url, self.cfg.db_path.parent / "qr.png")
            self._qr_url = url
        return url

    def _loop(self) -> None:
        while not self._stop.is_set():
            # 三个环节相互独立：即使预约服务暂时离线，已登记的邮件也可发送。
            for action in (self._sync_desk, self._process_booking, self._report_booking, self._process_legacy_job):
                try:
                    action()
                except Exception:
                    log.exception("Agent workflow failed: %s", action.__name__)
            self._stop.wait(5.0)

    def _sync_desk(self) -> None:
        if not self.booking.enabled or not self.cfg.instrument_id:
            return
        try:
            payload = self.booking.desk()
        except BookingUnavailable:
            log.warning("预约服务暂时离线；稍后同步")
            return
        phase = payload.get("phase")
        booking = payload.get("booking") or {}
        booking_id = str(booking.get("id") or "")
        if not booking_id:
            return
        if phase == "checked-in":
            # A stale folderPath returned by the reservation service must not suppress
            # creation of the folder on *this* instrument's computer.
            self._ensure_folder(booking)
        elif phase == "need-send":
            # Local mapping wins if the reservation server did not persist its path.
            local_folder = self.store.folder_for_booking(booking_id)
            if local_folder:
                booking = {**booking, "folderPath": local_folder}
            self.store.enqueue_booking(booking, time.time())
        else:
            log.debug("desk phase=%r, booking=%s; not a confirmed check-in", phase, booking_id)

    def _seed_once(self, booking_id: str, folder: Path) -> None:
        if not self.cfg.demo_seed_on_checkin:
            return
        marker_dir = self.cfg.db_path.parent / "demo-seeded"
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker = marker_dir / (hashlib.sha256(booking_id.encode("utf-8")).hexdigest() + ".flag")
        if marker.exists():
            return
        created = generate_demo_files(folder, booking_id, self.cfg.instrument_id)
        marker.touch(exist_ok=True)
        log.warning("[SIMULATED TEST DATA] %s: generated %s demo file(s) in %s",
                    booking_id, len(created), folder)

    def _ensure_folder(self, booking: dict) -> None:
        booking_id = str(booking["id"])
        previous = self.store.folder_for_booking(booking_id)
        remote = str(booking.get("folderPath") or "")
        if previous:
            folder = Path(previous)
        else:
            # Adopt a path returned by the server only if it already exists in our
            # explicitly configured root. Never trust arbitrary remote paths.
            safe_remote = self._safe_folder(remote)
            if safe_remote is not None:
                folder = safe_remote
            else:
                raw = str(booking.get("checkInAt") or "")
                try:
                    when = datetime.fromisoformat(raw)
                except ValueError:
                    when = datetime.now().astimezone()
                folder = make_booking_folder(
                    self.cfg.data_root, booking_id, str(booking.get("applicant") or "user"), when
                )
            self.store.remember_folder(booking_id, folder)
        # Prevent silently placing / emailing data outside the configured root.
        root = self.cfg.data_root.resolve()
        if folder.is_symlink() or folder.resolve().parent != root:
            log.error("预约 %s 的本地记录指向无效目录 %s；请检查 data_root 配置", booking_id, folder)
            return
        first_seen = not folder.exists()
        folder.mkdir(parents=True, exist_ok=True)
        self._seed_once(booking_id, folder)
        if first_seen or not remote or Path(remote) != folder:
            try:
                self.booking.report_desk(booking_id, folder_path=str(folder))
            except BookingUnavailable:
                log.warning("文件夹已建立，等待与预约服务器恢复同步：%s；路径：%s", booking_id, folder)
                return
            log.info("本次实验文件夹（预约 %s）：%s", booking_id, folder)

    def _safe_folder(self, supplied: str) -> Path | None:
        if not supplied:
            return None
        folder = Path(supplied)
        if folder.name == "__DEMO_PREVIEW_DO_NOT_EMAIL__":
            return None
        if folder.is_symlink() or not folder.is_dir():
            return None
        root = self.cfg.data_root.resolve()
        path = folder.resolve()
        # 只允许数据根目录下的直接子目录，绝不按服务端传来的任意路径读取。
        if path.parent != root:
            return None
        return folder

    @staticmethod
    def _signature(folder: Path, files: list[Path]) -> str:
        entries = []
        for p in files:
            info = p.stat()
            entries.append([p.relative_to(folder).as_posix(), info.st_size, info.st_mtime_ns])
        return json.dumps(entries, ensure_ascii=False, separators=(",", ":"))

    def _process_booking(self) -> None:
        now = time.time()
        row = self.store.ready_booking(now)
        if row is None:
            return
        booking_id = row["booking_id"]
        email = row["email"]
        if not email:
            self.store.finish_booking(booking_id, "skipped", "用户未填写电子邮箱")
            return
        folder = self._safe_folder(row["folder_path"])
        if folder is None:
            self.store.finish_booking(booking_id, "failed", "实验目录未找到或不属于本设备的数据目录，禁止发送")
            return
        try:
            files = collect_files(folder)
            sig = self._signature(folder, files)
        except OSError as exc:
            log.warning("读取实验目录失败 %s: %s", booking_id, exc)
            if now - row["created_at"] >= self.cfg.file_wait_seconds:
                self.store.finish_booking(booking_id, "failed", "实验文件无法访问，请检查权限后手动重试")
            return
        if not files:
            if now - row["created_at"] >= self.cfg.file_wait_seconds:
                self.store.finish_booking(booking_id, "failed", "下机后未发现实验数据文件；未发送空邮件")
            return
        if sig != row["file_signature"]:
            self.store.snapshot_booking(booking_id, sig, now)
            return
        if now - row["signature_since"] < self.cfg.file_stable_seconds:
            return
        # 某些软件先创建空文件，再长时间持有句柄；按修改时间再次判断。
        if any(now - p.stat().st_mtime < self.cfg.file_stable_seconds for p in files):
            return
        if not self.store.mark_booking_sending(booking_id):
            return
        session = {
            "booking_id": booking_id,
            "folder_path": str(folder),
            "username": row["username"],
            "check_in_at": row["check_in_at"],
            "check_out_at": row["check_out_at"],
            "email": email,
        }
        try:
            deliver(self.cfg, session, files)
        except SendError as exc:
            updated = self.store.get_booking_delivery(booking_id)
            if exc.retryable and updated["attempts"] < self.cfg.email_max_attempts:
                delay = min(300, 15 * 2 ** (updated["attempts"] - 1))
                self.store.postpone_booking(booking_id, time.time() + delay, str(exc))
                log.warning("SMTP 尚未发送，%ss 后安全重试 %s: %s", delay, booking_id, exc)
            else:
                self.store.finish_booking(booking_id, "failed", str(exc))
                log.warning("邮件未发送 %s: %s", booking_id, exc)
        except DeliveryUncertain as exc:
            self.store.finish_booking(booking_id, "uncertain", str(exc))
            log.error("邮件发送结果不确定 %s: %s", booking_id, exc)
        except Exception:
            # 发生不可预期错误时，可能已经调用过 SMTP；不能自动重发。
            self.store.finish_booking(booking_id, "uncertain", "发送过程出现未知错误，请人工检查邮箱及日志")
            log.exception("邮件状态不确定 %s", booking_id)
        else:
            self.store.finish_booking(booking_id, "sent")
            log.info("已交付 SMTP 服务器：预约 %s，收件人 %s，附件 %s 个", booking_id, email, len(files))

    def _report_booking(self) -> None:
        if not self.booking.enabled or not self.cfg.instrument_id:
            return
        row = self.store.unreported_booking()
        if row is None:
            return
        # 预约服务器原协议支持 sent / failed / skipped；uncertain 显示为待人工处理的失败。
        status = "failed" if row["status"] == "uncertain" else row["status"]
        error = row["error"]
        if row["status"] == "uncertain":
            error = "发送结果不确定，可能已收到，请检查后再手动重发。" + error
        try:
            self.booking.report_desk(row["booking_id"], send_status=status, send_error=error)
        except BookingUnavailable:
            log.warning("邮件结果已本地持久化，等待同步预约服务器：%s", row["booking_id"])
            return
        self.store.confirm_booking_report(row["booking_id"])

    def _process_legacy_job(self) -> None:
        """保留旧 sessions/jobs 格式兼容性，旧界面中的操作目前已被禁用。"""
        job = self.store.claim_job()
        if job is None:
            return
        session = self.store.get_session(job["session_id"])
        if session is None:
            self.store.finish_job(job["id"], job["session_id"], "failed", "会话不存在")
            return
        try:
            row = dict(session)
            folder = self._safe_folder(row["folder_path"])
            if folder is None:
                raise SendError("无效的实验目录")
            deliver(self.cfg, row, collect_files(folder))
        except (SendError, DeliveryUncertain) as exc:
            self.store.finish_job(job["id"], job["session_id"], "failed", str(exc))
        except Exception:
            self.store.finish_job(job["id"], job["session_id"], "failed", "未知错误，请检查日志")
            log.exception("旧版任务发送错误")
        else:
            self.store.finish_job(job["id"], job["session_id"], "sent", None)
