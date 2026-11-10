from __future__ import annotations

import json
import logging
from datetime import datetime
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from labcheckin.config import Config

log = logging.getLogger("labcheckin.booking")


class BookingUnavailable(Exception):
    pass


class BookingClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.booking_api_base)

    def _request(self, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        query = f"?{urlencode(params)}" if params else ""
        url = f"{self.cfg.booking_api_base}{path}{query}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.cfg.booking_api_token:
            headers["Authorization"] = f"Bearer {self.cfg.booking_api_token}"
        request = Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            log.warning("预约服务不可达: %s", exc)
            raise BookingUnavailable("预约服务暂时不可用，请稍后再试") from exc

    def verify_user(self, name: str, password: str = "") -> dict:
        payload = self._request("/api/users/verify", body={"name": name, "password": password})
        return {"ok": bool(payload.get("ok")), "message": payload.get("message") or "该用户未注册"}

    def active_booking(self, applicant: str, when: datetime) -> dict | None:
        if not self.cfg.instrument_id:
            raise BookingUnavailable("未配置本机对应的仪器")
        payload = self._request(
            "/api/bookings/active",
            params={
                "instrument": self.cfg.instrument_id,
                "user": applicant,
                "date": when.strftime("%Y-%m-%d"),
                "time": when.strftime("%H:%M"),
            },
        )
        return payload.get("booking")

    def report_scan(self, booking_id: str, action: str, when: datetime) -> None:
        self._request(
            f"/api/bookings/{booking_id}/scan",
            body={"action": action, "at": when.isoformat(timespec="seconds")},
        )

    def report_presence(self, public_url: str) -> None:
        if not self.cfg.instrument_id:
            raise BookingUnavailable("未配置本机对应的仪器")
        self._request(
            f"/api/instruments/{quote(self.cfg.instrument_id)}/presence",
            body={"publicUrl": public_url},
        )
