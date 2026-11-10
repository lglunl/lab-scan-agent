from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
PUBLIC_URL_FILE = DATA_DIR / "public_base_url.txt"


@dataclass(frozen=True)
class Config:
    device_id: str
    device_name: str
    host: str
    port: int
    public_base_url: str
    data_root: Path
    qr_path: Path
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_tls: bool
    max_attachment_bytes: int
    db_path: Path
    secret_path: Path
    booking_api_base: str
    booking_api_token: str
    instrument_id: str
    require_booking: bool

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host.strip() and self.smtp_from.strip())


def desktop() -> Path:
    home = Path.home()
    for name in ("Desktop", "桌面"):
        path = home / name
        if path.is_dir():
            return path
    return home / "Desktop"


def load_config() -> Config:
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    desk = desktop()
    data_root = Path(raw["data_root"]) if raw.get("data_root") else desk / "Experiment Data"
    qr_path = Path(raw["qr_path"]) if raw.get("qr_path") else desk / "上机二维码.png"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return Config(
        device_id=str(raw.get("device_id") or "local-demo"),
        device_name=str(raw.get("device_name") or "本地演示设备"),
        host=str(raw.get("host") or "127.0.0.1"),
        port=int(raw.get("port") or 8787),
        public_base_url=str(raw.get("public_base_url") or "").strip(),
        data_root=data_root,
        qr_path=qr_path,
        smtp_host=str(raw.get("smtp_host") or "").strip(),
        smtp_port=int(raw.get("smtp_port") or 587),
        smtp_user=str(raw.get("smtp_user") or ""),
        smtp_password=str(raw.get("smtp_password") or ""),
        smtp_from=str(raw.get("smtp_from") or "").strip(),
        smtp_tls=bool(raw.get("smtp_tls", True)),
        max_attachment_bytes=int(raw.get("max_attachment_bytes") or 20 * 1024 * 1024),
        db_path=DATA_DIR / "lab.sqlite",
        secret_path=DATA_DIR / "session.secret",
        booking_api_base=str(raw.get("booking_api_base") or "").strip().rstrip("/"),
        booking_api_token=str(raw.get("booking_api_token") or "").strip(),
        instrument_id=str(raw.get("instrument_id") or "").strip(),
        require_booking=bool(raw.get("require_booking", False)),
    )


def current_base_url(cfg: Config) -> str:
    if PUBLIC_URL_FILE.is_file():
        text = PUBLIC_URL_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text.rstrip("/")
    if cfg.public_base_url:
        return cfg.public_base_url.rstrip("/")
    host = "127.0.0.1" if cfg.host in {"0.0.0.0", "::"} else cfg.host
    return f"http://{host}:{cfg.port}"


def page_url(cfg: Config) -> str:
    return f"{current_base_url(cfg)}/?device={cfg.device_id}"
