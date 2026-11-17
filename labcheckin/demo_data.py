"""Explicitly fake, reproducible small files for check-in/email workflow tests."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path


README_NAME = "DEMO_README_模拟数据_不可用于科研.txt"


def generate_demo_files(folder: Path, booking_id: str, instrument_id: str) -> list[Path]:
    """Never overwrite existing files; all data is synthetic and marked DEMO."""
    folder.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    digest = hashlib.sha256(booking_id.encode("utf-8")).digest()
    file_path = folder / "DEMO_pressure_temperature.csv"
    if not file_path.exists():
        with file_path.open("x", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(["elapsed_seconds", "pressure_pa_simulated", "temperature_c_simulated", "simulation_only"])
            for i in range(31):
                elapsed = i * 10
                pressure = round(90 + ((i * 37 + digest[0]) % 28) * 0.65, 2)
                temp = round(125 + ((i * 7 + digest[1]) % 15) * 0.2, 2)
                writer.writerow([elapsed, pressure, temp, "YES_FAKE_DATA"])
        created.append(file_path)
    file_path = folder / "DEMO_experiment_info.json"
    if not file_path.exists():
        with file_path.open("x", encoding="utf-8") as stream:
            json.dump({
                "simulation_only": True,
                "warning": "SYNTHETIC EXAMPLE ONLY; NOT MEASURED ON AN INSTRUMENT",
                "booking_id": booking_id,
                "instrument_id": instrument_id,
                "example_samples": ["DEMO-SiN-01", "DEMO-SiN-02"],
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }, stream, ensure_ascii=False, indent=2)
        created.append(file_path)
    file_path = folder / README_NAME
    if not file_path.exists():
        with file_path.open("x", encoding="utf-8") as stream:
            stream.write(
                "【仅供程序测试：这不是真实实验数据】\n"
                "此文件夹中的 DEMO_ 文件由 Agent 在扫码上机后自动生成。\n"
                "压力、温度与样品编号完全是人为模拟，用于验证预约→建文件夹→扫码下机→邮件附件流程。\n"
                "不得将其用于论文、科研测量、工艺分析或任何真实实验结果。\n"
                "正式部署时，请在 config.json 中将 demo_seed_on_checkin 改为 false。\n"
            )
        created.append(file_path)
    return created
