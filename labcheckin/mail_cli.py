"""在仪器电脑命令行查看 / 测试 / 人工重试邮件。运行：python -m labcheckin.mail_cli ..."""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from labcheckin.config import load_config
from labcheckin.db import Store
from labcheckin.mail import DeliveryUncertain, SendError, deliver, valid_address


def main() -> int:
    parser = argparse.ArgumentParser(description="实验设备邮件管理工具")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("check", help="检查邮件配置，不发送邮件")
    tester = sub.add_parser("test", help="发送一封包含测试附件的真实邮件")
    tester.add_argument("--to", required=True, help="测试收件邮箱")
    report = sub.add_parser("status", help="查看某个预约的邮件任务状态")
    report.add_argument("booking_id")
    retry = sub.add_parser("retry", help="人工重新排队一个失败的邮件任务")
    retry.add_argument("booking_id")
    retry.add_argument("--confirm-uncertain", action="store_true", help="已确认不怕重复投递后才可重发不确定状态")
    args = parser.parse_args()
    cfg = load_config()

    if args.action == "check":
        print(f"SMTP 已配置: {'是' if cfg.smtp_configured else '否'}")
        print(f"主机/端口: {cfg.smtp_host or '(未设置)'}:{cfg.smtp_port}")
        print(f"安全连接: {cfg.smtp_security}")
        print(f"发件地址: {cfg.smtp_from or '(未设置)'}")
        print(f"登录账号: {cfg.smtp_user or '(未设置)'}")
        if cfg.smtp_password:
            password_status = "已加载（不显示密码）"
        elif (cfg.db_path.parent / ".smtp-password.dpapi").is_file():
            password_status = "DPAPI 加密文件存在，但当前命令未解密；请运行 scripts/test-mail.ps1"
        else:
            password_status = "未设置"
        print(f"SMTP 授权码: {password_status}")
        print(f"附件总大小上限: {cfg.max_attachment_bytes / 1024 / 1024:.1f} MB")
        print(f"文件稳定等待: {cfg.file_stable_seconds} 秒")
        return 0

    if args.action == "test":
        if not valid_address(args.to):
            parser.error("--to 请填写有效的邮箱地址")
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            file = folder / "email-test.txt"
            file.write_text("这是一封测试邮件，不包含真实实验数据。\n", encoding="utf-8")
            session = {
                "folder_path": str(folder), "email": args.to, "username": "邮件配置测试",
                "check_in_at": "--", "check_out_at": "--", "booking_id": "smtp-test",
            }
            try:
                deliver(replace(cfg, device_name=f"{cfg.device_name}（邮件测试）"), session, [file])
            except (SendError, DeliveryUncertain) as exc:
                print(f"测试失败：{exc}", file=sys.stderr)
                return 1
        print(f"SMTP 已接受测试邮件，收件邮箱：{args.to}；请检查收件箱/垃圾箱。")
        return 0

    store = Store(cfg.db_path)
    row = store.get_booking_delivery(args.booking_id)
    if row is None:
        print("本机未记录这个预约的邮件任务", file=sys.stderr)
        return 1
    if args.action == "status":
        print(f"预约: {row['booking_id']}")
        print(f"状态: {row['status']}")
        print(f"收件人: {row['email']}")
        print(f"尝试次数: {row['attempts']}")
        print(f"已回报服务器: {'是' if row['reported'] else '否'}")
        print(f"错误: {row['error'] or '(无)'}")
        return 0
    if args.action == "retry":
        try:
            store.retry_booking(args.booking_id, time.time(), allow_uncertain=args.confirm_uncertain)
        except RuntimeError as exc:
            print(f"重试失败：{exc}", file=sys.stderr)
            return 1
        print(f"已重新排队 {args.booking_id}。Agent 运行时会自动执行并回报预约系统。")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
