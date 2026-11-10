from __future__ import annotations

import logging
import smtplib
import threading
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import qrcode

from labcheckin.config import Config, page_url
from labcheckin.db import Store

log = logging.getLogger("labcheckin.agent")


class SendError(Exception):
    pass


def make_session_folder(root: Path, username: str, when: datetime) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    base = f"{username}_{when.strftime('%Y%m%d_%H%M%S')}"
    path = root / base
    suffix = 2
    while path.exists():
        path = root / f"{base}_{suffix}"
        suffix += 1
    path.mkdir()
    return path


def collect_files(folder: Path) -> list[Path]:
    root = folder.resolve()
    if not root.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        found.append(resolved)
    return found


def total_bytes(files: list[Path]) -> int:
    return sum(path.stat().st_size for path in files)


def attachment_name(folder: Path, path: Path) -> str:
    relative = path.resolve().relative_to(folder.resolve()).as_posix()
    return relative.replace("/", "_")


def deliver(cfg: Config, session, files: list[Path]) -> None:
    if not cfg.smtp_configured:
        raise SendError("发信邮箱未配置")
    if not files:
        raise SendError("未找到数据文件")
    size = total_bytes(files)
    if size > cfg.max_attachment_bytes:
        raise SendError("文件合计超过 20MB，无法发送")

    folder = Path(session["folder_path"])
    message = EmailMessage()
    message["Subject"] = f"实验数据 {session['username']} {session['check_in_at']}"
    message["From"] = cfg.smtp_from
    message["To"] = session["email"]
    lines = [
        f"设备：{cfg.device_name}",
        f"用户：{session['username']}",
        f"上机：{session['check_in_at']}",
        f"下机：{session['check_out_at']}",
        "文件：",
        *[f"- {attachment_name(folder, path)}" for path in files],
    ]
    message.set_content("\n".join(lines))
    for path in files:
        message.add_attachment(
            path.read_bytes(),
            maintype="application",
            subtype="octet-stream",
            filename=attachment_name(folder, path),
        )

    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
        if cfg.smtp_tls:
            smtp.starttls()
        if cfg.smtp_user:
            smtp.login(cfg.smtp_user, cfg.smtp_password)
        smtp.send_message(message)


def write_qr(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    image = qrcode.make(url)
    image.save(dest)


class Agent:
    def __init__(self, cfg: Config, store: Store) -> None:
        self.cfg = cfg
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._qr_url = ""

    def start(self) -> None:
        self.refresh_qr()
        self._thread = threading.Thread(target=self._loop, name="lab-agent", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def refresh_qr(self) -> str:
        url = page_url(self.cfg)
        if url != self._qr_url:
            write_qr(url, self.cfg.qr_path)
            write_qr(url, self.cfg.db_path.parent / "qr.png")
            self._qr_url = url
            log.info("QR -> %s saved %s", url, self.cfg.qr_path)
        return url

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh_qr()
                self._process_one()
            except Exception:
                log.exception("agent loop failed")
            self._stop.wait(1.0)

    def _process_one(self) -> None:
        job = self.store.claim_job()
        if job is None:
            return
        session = self.store.get_session(job["session_id"])
        if session is None:
            self.store.finish_job(job["id"], job["session_id"], "failed", "会话不存在")
            return
        try:
            files = collect_files(Path(session["folder_path"]))
            deliver(self.cfg, session, files)
        except SendError as exc:
            self.store.finish_job(job["id"], session["id"], "failed", str(exc))
            log.info("send failed for %s: %s", session["username"], exc)
            return
        except Exception as exc:
            self.store.finish_job(job["id"], session["id"], "failed", f"发送失败：{exc}")
            log.exception("send crashed")
            return
        self.store.finish_job(job["id"], session["id"], "sent", None)
        log.info("sent files for %s to %s", session["username"], session["email"])
