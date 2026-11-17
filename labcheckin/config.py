from __future__ import annotations

import json
import os
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
    smtp_security: str
    smtp_timeout: int
    file_stable_seconds: int
    file_wait_seconds: int
    email_max_attempts: int
    max_attachment_bytes: int
    db_path: Path
    secret_path: Path
    booking_api_base: str
    booking_api_token: str
    instrument_id: str
    require_booking: bool
    demo_seed_on_checkin: bool = True

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host.strip() and self.smtp_from.strip())


def desktop() -> Path:
    """Use the Windows Known Folder API to support OneDrive / relocated Desktops."""
    if os.name == "nt":
        try:
            import ctypes
            import uuid

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8),
                ]

            known_desktop = GUID.from_buffer_copy(uuid.UUID("B4BFCC3A-DB2C-424C-B029-7FE99A87C641").bytes_le)
            pointer = ctypes.c_wchar_p()
            getter = ctypes.windll.shell32.SHGetKnownFolderPath
            getter.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
            getter.restype = ctypes.c_long
            result = getter(ctypes.byref(known_desktop), 0, None, ctypes.byref(pointer))
            if result == 0 and pointer.value:
                try:
                    return Path(pointer.value)
                finally:
                    free_pointer = ctypes.windll.ole32.CoTaskMemFree
                    free_pointer.argtypes = [ctypes.c_void_p]
                    free_pointer.restype = None
                    free_pointer(ctypes.cast(pointer, ctypes.c_void_p))
        except (AttributeError, OSError, ValueError):
            pass
    home = Path.home()
    candidates = [home / "Desktop", home / "桌面"]
    for env_name in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        value = os.environ.get(env_name)
        if value:
            candidates += [Path(value) / "Desktop", Path(value) / "桌面"]
    for path in candidates:
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
        smtp_host=(os.environ.get("LAB_SMTP_HOST") or str(raw.get("smtp_host") or "")).strip(),
        smtp_port=int(os.environ.get("LAB_SMTP_PORT") or raw.get("smtp_port") or 587),
        smtp_user=os.environ.get("LAB_SMTP_USER") or str(raw.get("smtp_user") or ""),
        smtp_password=os.environ.get("LAB_SMTP_PASSWORD") or str(raw.get("smtp_password") or ""),
        smtp_from=(os.environ.get("LAB_SMTP_FROM") or str(raw.get("smtp_from") or "")).strip(),
        smtp_tls=bool(raw.get("smtp_tls", True)),
        # smtp_security: ssl(465) / starttls(587) / none(仅受信内网中继)
        smtp_security=(os.environ.get("LAB_SMTP_SECURITY") or str(raw.get("smtp_security") or
                       ("starttls" if raw.get("smtp_tls", True) else "none"))).strip().lower(),
        smtp_timeout=max(5, int(raw.get("smtp_timeout") or 30)),
        file_stable_seconds=max(1, int(raw.get("file_stable_seconds") or 10)),
        file_wait_seconds=max(5, int(raw.get("file_wait_seconds") or 120)),
        email_max_attempts=max(1, int(raw.get("email_max_attempts") or 3)),
        max_attachment_bytes=int(raw.get("max_attachment_bytes") or 20 * 1024 * 1024),
        db_path=DATA_DIR / "lab.sqlite",
        secret_path=DATA_DIR / "session.secret",
        booking_api_base=str(raw.get("booking_api_base") or "").strip().rstrip("/"),
        # 密钥优先取环境变量 LAB_API_TOKEN，避免真实密钥写进 config.json 被提交到 git。
        booking_api_token=os.environ.get("LAB_API_TOKEN") or str(raw.get("booking_api_token") or "").strip(),
        instrument_id=str(raw.get("instrument_id") or "").strip(),
        require_booking=bool(raw.get("require_booking", False)),
        demo_seed_on_checkin=bool(raw.get("demo_seed_on_checkin", True)),
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
