from __future__ import annotations

import logging
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from labcheckin.agent import Agent, collect_files, make_session_folder, total_bytes
from labcheckin.booking import BookingClient, BookingUnavailable
from labcheckin.config import ROOT, load_config
from labcheckin.db import Occupied, Store
from labcheckin.tunnel import QuickTunnel

log = logging.getLogger("labcheckin")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ILLEGAL = set('\\/:*?"<>|')

cfg = load_config()
store = Store(cfg.db_path)
agent = Agent(cfg, store)
booking = BookingClient(cfg)
tunnel = QuickTunnel(cfg.port)
templates = Jinja2Templates(directory=str(ROOT / "templates"))


def session_secret() -> str:
    if cfg.secret_path.is_file():
        return cfg.secret_path.read_text(encoding="utf-8").strip()
    secret = secrets.token_hex(32)
    cfg.secret_path.write_text(secret, encoding="utf-8")
    return secret


def clean_username(raw: str) -> str:
    name = "".join(ch for ch in raw.strip() if ch not in ILLEGAL and ord(ch) >= 32)
    name = name.strip(" .")
    if not name or len(name) > 40:
        raise ValueError("请填写有效用户名")
    return name


def show_time(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def file_rows(folder: Path) -> list[dict]:
    rows = []
    for path in collect_files(folder):
        size = path.stat().st_size
        rows.append({"name": path.name, "size": format_size(size), "bytes": size})
    return rows


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def username_of(request: Request) -> str | None:
    value = request.session.get("username")
    return value if isinstance(value, str) and value else None


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def render(request: Request, name: str, context: dict, status_code: int = 200):
    payload = {"device_name": cfg.device_name, "device_id": cfg.device_id, "refresh": False, **context}
    try:
        return templates.TemplateResponse(request, name, payload, status_code=status_code)
    except TypeError:
        payload["request"] = request
        return templates.TemplateResponse(name, payload, status_code=status_code)


def device_matches(request: Request) -> bool:
    given = request.query_params.get("device")
    return given is None or given == "" or given == cfg.device_id


def instrument_matches(request: Request) -> bool:
    given = request.query_params.get("instrument")
    if given is None or given == "":
        return True
    return given == cfg.instrument_id


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    agent.start()
    tunnel.start()
    log.info("open %s", agent.refresh_qr())
    yield
    tunnel.stop()
    agent.stop()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
app.add_middleware(SessionMiddleware, secret_key=session_secret(), same_site="lax", https_only=False, max_age=60 * 60 * 12)


@app.get("/qr.png")
def qr_image():
    path = cfg.db_path.parent / "qr.png"
    agent.refresh_qr()
    return FileResponse(path, media_type="image/png")


# 流程：打开设备页先登录，再按这台仪器的当前预约决定能不能点「上机」。
# 下机暂不要求邮箱和文件。
def checkin_gate(username: str) -> dict:
    if not (cfg.require_booking and booking.enabled):
        return {"can_check_in": True, "booking": None, "block_reason": ""}
    try:
        reservation = booking.active_booking(username, datetime.now().astimezone())
    except BookingUnavailable as exc:
        return {"can_check_in": False, "booking": None, "block_reason": str(exc)}
    if reservation is None:
        return {
            "can_check_in": False,
            "booking": None,
            "block_reason": "当前时间没有这台仪器的有效预约，不能上机。",
        }
    return {"can_check_in": True, "booking": reservation, "block_reason": ""}


@app.get("/")
def home(request: Request):
    if not device_matches(request):
        return render(
            request,
            "message.html",
            {"title": "设备不匹配", "message": "这个二维码不属于本机这台演示设备。"},
            status_code=404,
        )
    if not instrument_matches(request):
        return render(
            request,
            "message.html",
            {"title": "仪器不匹配", "message": "这个二维码对应的仪器不是本机正在管理的仪器。"},
            status_code=404,
        )
    active = store.active_session(cfg.device_id)
    if active is not None:
        return render(request, "checkout.html", checkout_context(active))
    username = username_of(request)
    if not username:
        return render(request, "login.html", {"title": "登录", "error": ""})
    latest = store.latest_for_user(cfg.device_id, username)
    notice = None
    if latest is not None and latest["send_status"] in {"pending", "sending", "failed", "sent"}:
        notice = {
            "id": latest["id"],
            "status": latest["send_status"],
            "error": latest["send_error"] or "",
            "email": latest["email"] or "",
        }
    return render(
        request,
        "checkin.html",
        {"title": "上机", "username": username, "error": "", "notice": notice, **checkin_gate(username)},
    )


@app.get("/checked-in")
def checked_in(request: Request):
    username = username_of(request)
    active = store.active_session(cfg.device_id)
    if username is None or active is None or active["username"] != username:
        return redirect(f"/?device={quote(cfg.device_id)}")
    return render(
        request,
        "checked_in.html",
        {
            "title": "已上机",
            "username": username,
            "folder": active["folder_path"],
            "checked_in": show_time(active["check_in_at"]),
        },
    )


def checkout_context(session, error: str = "") -> dict:
    folder = Path(session["folder_path"])
    rows = file_rows(folder)
    total = sum(item["bytes"] for item in rows)
    return {
        "title": "下机并发送",
        "username": session["username"],
        "folder": str(folder),
        "files": rows,
        "total": format_size(total),
        "oversize": total > cfg.max_attachment_bytes,
        "error": error,
        "checked_in": show_time(session["check_in_at"]),
    }


@app.post("/login")
def login(request: Request, username: Annotated[str, Form()], password: Annotated[str, Form()] = ""):
    try:
        name = clean_username(username)
    except ValueError as exc:
        return render(request, "login.html", {"title": "登录", "error": str(exc)}, status_code=400)
    if booking.enabled:
        try:
            result = booking.verify_user(name, password)
        except BookingUnavailable as exc:
            return render(request, "login.html", {"title": "登录", "error": str(exc)}, status_code=503)
        if not result["ok"]:
            return render(request, "login.html", {"title": "登录", "error": result["message"]}, status_code=403)
    request.session["username"] = name
    return redirect(f"/?device={quote(cfg.device_id)}")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/")


@app.post("/check-in")
def check_in(request: Request):
    username = username_of(request)
    if not username:
        return redirect("/")
    when = datetime.now().astimezone()
    active = store.active_session(cfg.device_id)
    if active is not None and active["username"] != username:
        return render(
            request,
            "occupied.html",
            {
                "title": "设备使用中",
                "username": username,
                "holder": active["username"],
                "started": show_time(active["check_in_at"]),
            },
            status_code=409,
        )
    if active is not None:
        return redirect("/checked-in")
    gate = checkin_gate(username)
    reservation = gate["booking"]
    if not gate["can_check_in"]:
        latest = store.latest_for_user(cfg.device_id, username)
        notice = None
        if latest is not None and latest["send_status"] in {"pending", "sending", "failed", "sent"}:
            notice = {
                "id": latest["id"],
                "status": latest["send_status"],
                "error": latest["send_error"] or "",
                "email": latest["email"] or "",
            }
        return render(
            request,
            "checkin.html",
            {"title": "上机", "username": username, "error": "", "notice": notice, **gate},
            status_code=403,
        )
    folder = make_session_folder(cfg.data_root, username, when)
    try:
        store.check_in(cfg.device_id, username, str(folder), when, reservation["id"] if reservation else None)
    except Occupied as exc:
        try:
            folder.rmdir()
        except OSError:
            pass
        return render(
            request,
            "occupied.html",
            {
                "title": "设备使用中",
                "username": username,
                "holder": exc.username,
                "started": show_time(exc.check_in_at),
            },
            status_code=409,
        )
    if booking.enabled and reservation is not None:
        try:
            booking.report_scan(reservation["id"], "check-in", when)
        except BookingUnavailable:
            log.warning("上机状态回写预约系统失败：%s", reservation["id"])
    return redirect("/checked-in")


@app.post("/check-out")
def check_out(request: Request, email: Annotated[str, Form()] = ""):
    active = store.active_session(cfg.device_id)
    if active is None:
        return redirect(f"/?device={quote(cfg.device_id)}")
    address = email.strip()
    request.session["username"] = active["username"]
    when = datetime.now().astimezone()
    store.check_out(active["id"], address, when)
    if booking.enabled and active["booking_id"]:
        try:
            booking.report_scan(active["booking_id"], "check-out", when)
        except BookingUnavailable:
            log.warning("下机状态回写预约系统失败：%s", active["booking_id"])
    return redirect(f"/session/{active['id']}")


@app.get("/session/{session_id}")
def session_result(request: Request, session_id: str):
    username = username_of(request)
    if not username:
        return redirect("/")
    session = store.get_session(session_id)
    if session is None or session["username"] != username or session["device_id"] != cfg.device_id:
        return render(request, "message.html", {"title": "找不到这次上机", "message": "这条记录不属于当前登录用户。"}, status_code=404)
    if not session["check_out_at"]:
        return redirect("/")
    status = session["send_status"] or "pending"
    return render(
        request,
        "result.html",
        {
            "title": "已下机",
            "username": username,
            "email": session["email"] or "",
            "status": status,
            "error": session["send_error"] or "",
            "folder": session["folder_path"],
            "refresh": status in {"pending", "sending"},
            "session_id": session["id"],
        },
    )


@app.post("/session/{session_id}/retry")
def retry_session(request: Request, session_id: str):
    username = username_of(request)
    if not username:
        return redirect("/")
    session = store.get_session(session_id)
    if session is None or session["username"] != username:
        return redirect("/")
    folder = Path(session["folder_path"])
    rows = file_rows(folder)
    total = sum(item["bytes"] for item in rows)
    if not session["check_in_at"] or not session["check_out_at"]:
        return render(request, "message.html", {"title": "还不能发送", "message": "需要先完成上机和下机两次扫码。"}, status_code=400)
    if not rows:
        return render(request, "message.html", {"title": "没有数据文件", "message": f"请把文件放回 {folder} 后再重试。"}, status_code=400)
    if total > cfg.max_attachment_bytes:
        return render(request, "message.html", {"title": "文件太大", "message": "文件合计超过 20MB，无法发送。"}, status_code=400)
    try:
        store.retry_send(session_id, datetime.now().astimezone())
    except RuntimeError as exc:
        return render(request, "message.html", {"title": "不能重试", "message": str(exc)}, status_code=400)
    return redirect(f"/session/{session_id}")
