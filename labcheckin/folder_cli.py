"""Folder path / desk handshake diagnostics."""
from __future__ import annotations

import argparse
from pathlib import Path

from labcheckin.booking import BookingClient, BookingUnavailable
from labcheckin.config import desktop, load_config
from labcheckin.db import Store


def main() -> int:
    parser = argparse.ArgumentParser(description="诊断扫码后文件夹创建")
    parser.add_argument("action", choices=["diagnose"], help="diagnose: 检查路径/预约状态")
    args = parser.parse_args()
    cfg = load_config()
    print("系统实际桌面:", desktop())
    print("配置的数据根目录:", cfg.data_root)
    print("绝对路径:", cfg.data_root.resolve())
    print("根目录是否存在:", cfg.data_root.is_dir())
    print("仪器 ID:", cfg.instrument_id or "(未配置)")
    print("预约服务:", cfg.booking_api_base or "(未配置)")
    client = BookingClient(cfg)
    if not client.enabled or not cfg.instrument_id:
        print("诊断: 预约接口或 instrument_id 未配置，无法通过后台状态自动创建会话目录。")
        return 1
    try:
        desk_info = client.desk()
    except BookingUnavailable as exc:
        print("诊断: 无法获取预约仪器状态:", exc)
        return 2
    booking = desk_info.get("booking") or {}
    booking_id = str(booking.get("id") or "")
    print("服务器返回 phase:", desk_info.get("phase", "(缺失)"))
    print("服务器预约 ID:", booking_id or "(无)")
    print("服务器 folderPath:", booking.get("folderPath") or "(空)")
    if booking_id:
        store = Store(cfg.db_path)
        print("本地记录 folderPath:", store.folder_for_booking(booking_id) or "(无)")
    if desk_info.get("phase") != "checked-in":
        print("诊断: 服务器当前未确认 checked-in；必须先完成有效的预约扫码上机。")
    else:
        print("诊断: 服务器已确认 checked-in；启动 Agent 后会自动建目录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
