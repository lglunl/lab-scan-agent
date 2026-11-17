"""实验文件邮件发送：只发送单次实验专属文件夹中的附件。"""
from __future__ import annotations

import logging
import mimetypes
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

from labcheckin.config import Config

log = logging.getLogger("labcheckin.mail")
EMAIL_RE = re.compile(r"^[^\s@<>\x00-\x1f\x7f]+@[^\s@<>\x00-\x1f\x7f]+\.[^\s@<>\x00-\x1f\x7f]+$")


class SendError(Exception):
    """明确没有投递成功；可在本地设置 retryable 表示允许自动重试。"""
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class DeliveryUncertain(Exception):
    """SMTP DATA 阶段异常，服务端可能已接收，不可盲目自动重发。"""


def valid_address(address: str) -> bool:
    if not address or len(address) > 254 or not EMAIL_RE.fullmatch(address):
        return False
    parsed_name, parsed_addr = parseaddr(address)
    return not parsed_name and parsed_addr == address


def collect_files(folder: Path) -> list[Path]:
    root = folder.resolve()
    if not folder.is_dir() or folder.is_symlink():
        return []
    result = []
    for p in sorted(root.rglob("*")):
        # 不允许软链接绕过本次会话目录；也不附加子目录外的文件。
        if p.is_symlink() or not p.is_file():
            continue
        resolved = p.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        result.append(p)
    return result


def total_bytes(files: list[Path]) -> int:
    return sum(p.stat().st_size for p in files)


def attachment_name(folder: Path, path: Path, index: int) -> str:
    name = path.relative_to(folder).as_posix().replace("/", "_")
    return f"{index:03d}_{name}"


def make_message(cfg: Config, session: dict, files: list[Path]) -> EmailMessage:
    if not cfg.smtp_configured:
        raise SendError("未配置发信 SMTP 主机或发信邮箱")
    if not valid_address(cfg.smtp_from):
        raise SendError("SMTP 发件邮箱格式不正确")
    email = str(session.get("email") or "").strip()
    if not valid_address(email):
        raise SendError("预约收件邮箱格式不正确")
    if not files:
        raise SendError("本次实验文件夹内没有数据文件")
    if total_bytes(files) > cfg.max_attachment_bytes:
        raise SendError(f"附件合计超过 {cfg.max_attachment_bytes // (1024 * 1024)}MB 限制，请管理员改用文件下载链接")

    message = EmailMessage()
    message["Subject"] = f"【实验数据】{cfg.device_name} - {session.get('check_in_at', '')}"
    message["From"] = cfg.smtp_from
    message["To"] = email
    booking_id = str(session.get("booking_id") or "")
    if booking_id and all(32 <= ord(ch) < 127 for ch in booking_id) and len(booking_id) < 128:
        message["X-Lab-Booking-ID"] = booking_id
    folder = Path(session["folder_path"])
    names = [attachment_name(folder, p, i) for i, p in enumerate(files, start=1)]
    lines = [
        "您好，以下是本次实验的自动归档数据。",
        "",
        f"实验设备：{cfg.device_name}",
        f"预约用户：{session.get('username', '')}",
        f"开始时间：{session.get('check_in_at', '')}",
        f"结束时间：{session.get('check_out_at', '')}",
        f"预约编号：{booking_id}",
        "",
        f"附件共 {len(files)} 个：",
        *[f"- {name}" for name in names],
        "",
        "此邮件由实验室仪器客户端自动发送，请勿直接回复。",
    ]
    message.set_content("\n".join(lines))
    try:
        for p, filename in zip(files, names):
            content_type, _ = mimetypes.guess_type(p.name)
            maintype, subtype = content_type.split("/", 1) if content_type and "/" in content_type else ("application", "octet-stream")
            message.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=filename)
    except (OSError, ValueError) as exc:
        raise SendError(f"读取实验文件失败：{exc}") from exc
    return message


def deliver(cfg: Config, session: dict, files: list[Path]) -> None:
    """SMTP 同步发送。调用方负责幂等、状态持久化、文件稳定性检查。"""
    msg = make_message(cfg, session, files)
    security = cfg.smtp_security
    if security not in {"ssl", "starttls", "none"}:
        raise SendError("smtp_security 只支持 ssl、starttls 或 none")
    # 不允许账号密码以明文 SMTP 方式发送。
    if security == "none" and cfg.smtp_user:
        raise SendError("配置了 SMTP 登录账号时必须启用 SSL 或 STARTTLS")

    smtp = None
    try:
        context = ssl.create_default_context()
        if security == "ssl":
            smtp = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=cfg.smtp_timeout, context=context)
        else:
            smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.smtp_timeout)
            smtp.ehlo()
            if security == "starttls":
                smtp.starttls(context=context)
                smtp.ehlo()
        if cfg.smtp_user:
            smtp.login(cfg.smtp_user, cfg.smtp_password)
    except smtplib.SMTPAuthenticationError as exc:
        raise SendError("SMTP 登录失败，请检查账号和授权码") from exc
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        raise SendError(f"连接 SMTP 邮件服务器失败：{exc}", retryable=True) from exc
    else:
        try:
            # 此调用过程中若连接中断，无法确认 SMTP 服务器是否已经接收数据。
            smtp.send_message(msg)
        except smtplib.SMTPRecipientsRefused as exc:
            raise SendError("SMTP 服务器拒绝收件地址") from exc
        except smtplib.SMTPDataError as exc:
            raise SendError(f"SMTP 服务器拒绝邮件内容（{exc.smtp_code}）") from exc
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            raise DeliveryUncertain("SMTP 发送过程中断，无法确认对方是否已收到；请人工核对后重发") from exc
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except (smtplib.SMTPException, OSError):
                # SMTP.send_message 返回成功就认为已交给邮件服务器；QUIT 失败不重发。
                log.debug("SMTP QUIT failed after delivery attempt", exc_info=True)
