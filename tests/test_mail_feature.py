from __future__ import annotations

from dataclasses import replace
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest

from labcheckin.agent import Agent
from labcheckin.booking import BookingUnavailable
from labcheckin.config import load_config
from labcheckin.db import Store
from labcheckin.mail import DeliveryUncertain, SendError, collect_files, deliver, make_message, valid_address


def make_test_config(tmp_path: Path):
    cfg = load_config()
    return replace(
        cfg,
        db_path=tmp_path / "lab.sqlite",
        data_root=tmp_path / "experiments",
        qr_path=tmp_path / "qr.png",
        booking_api_base="https://reservation.test.invalid",
        instrument_id="inst-test",
        smtp_host="smtp.test.invalid",
        smtp_port=587,
        smtp_user="bot@example.org",
        smtp_password="mock-password",
        smtp_from="bot@example.org",
        smtp_security="starttls",
        file_stable_seconds=10,
        file_wait_seconds=120,
    )


def booking_payload(folder: Path | str, bid="book-001"):
    return {
        "id": bid,
        "folderPath": str(folder),
        "applicant": "科研用户",
        "email": "user@example.org",
        "checkInAt": "2026-11-11T14:00:00+08:00",
        "checkOutAt": "2026-11-11T15:20:00+08:00",
    }


class FakeBooking:
    enabled = True

    def __init__(self):
        self.phase = "need-send"
        self.booking = {}
        self.reported = []
        self.fail_once = False

    def desk(self):
        return {"phase": self.phase, "booking": self.booking}

    def report_desk(self, booking_id, **kwargs):
        if self.fail_once:
            self.fail_once = False
            raise BookingUnavailable("simulated network outage")
        self.reported.append((booking_id, kwargs))


def test_smtp_starttls_and_attachment(tmp_path, monkeypatch):
    cfg = make_test_config(tmp_path)
    folder = cfg.data_root / "exp1"
    folder.mkdir(parents=True)
    file = folder / "曲线.csv"
    file.write_bytes(b"x,y\n1,2\n")
    session = booking_payload(folder)
    session.update({"folder_path": str(folder), "username": session["applicant"],
                    "email": session["email"], "booking_id": session["id"],
                    "check_in_at": session["checkInAt"], "check_out_at": session["checkOutAt"]})
    class SMTP:
        def __init__(self): self.calls = []; self.message = None
        def ehlo(self): self.calls.append("ehlo")
        def starttls(self, context=None): self.calls.append("starttls")
        def login(self, user, pw): self.calls.append(("login", user, pw))
        def send_message(self, msg): self.calls.append("send"); self.message = msg
        def quit(self): self.calls.append("quit")
    smtp = SMTP()
    monkeypatch.setattr("labcheckin.mail.smtplib.SMTP", lambda *a, **k: smtp)
    deliver(cfg, session, [file])
    assert smtp.calls[:3] == ["ehlo", "starttls", "ehlo"]
    assert "send" in smtp.calls
    assert smtp.message["To"] == "user@example.org"
    assert smtp.message["X-Lab-Booking-ID"] == "book-001"
    attachments = list(smtp.message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_payload(decode=True) == file.read_bytes()
    assert "曲线.csv" in attachments[0].get_filename()


def test_invalid_email_and_size(tmp_path):
    cfg = make_test_config(tmp_path)
    folder = cfg.data_root / "exp1"
    folder.mkdir(parents=True)
    file = folder / "data.txt"
    file.write_bytes(b"abc")
    assert not valid_address("victim@example.org\nBcc:other@example.com")
    session = {"folder_path": str(folder), "email": "bad\nBcc:x@y.z"}
    with pytest.raises(SendError, match="收件邮箱"):
        make_message(cfg, session, [file])
    with pytest.raises(SendError, match="附件合计"):
        make_message(replace(cfg, max_attachment_bytes=1), {**session, "email": "user@example.org"}, [file])


def test_file_symlink_escape(tmp_path):
    root = tmp_path / "exp"
    root.mkdir()
    inside = root / "included.txt"
    inside.write_text("inside")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    link = root / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("this filesystem does not support symlinks")
    assert collect_files(root) == [inside]


def test_booking_workflow_exactly_once_after_reporting_failure(tmp_path, monkeypatch):
    cfg = make_test_config(tmp_path)
    store = Store(cfg.db_path)
    folder = cfg.data_root / "exp01"
    folder.mkdir(parents=True)
    file = folder / "result.csv"
    file.write_text("data")
    file.touch()
    import os
    os.utime(file, (900, 900))
    fake = FakeBooking()
    fake.booking = booking_payload(folder)
    agent = Agent(cfg, store)
    agent.booking = fake
    times = iter([1000, 1015, 1030, 1040, 1050])
    monkeypatch.setattr("labcheckin.agent.time.time", lambda: next(times))
    mailed = []
    monkeypatch.setattr("labcheckin.agent.deliver", lambda cfg, session, files: mailed.append((session, files)))
    agent._sync_desk()
    agent._process_booking()  # first observation, not stable yet
    assert not mailed
    agent._process_booking()  # stable; send once
    assert len(mailed) == 1
    fake.fail_once = True
    agent._report_booking()  # reporting error, not sending error
    assert store.get_booking_delivery("book-001")["status"] == "sent"
    assert store.get_booking_delivery("book-001")["reported"] == 0
    agent._sync_desk()
    agent._process_booking()  # must NOT send a second email
    agent._report_booking()  # recover reporting
    assert len(mailed) == 1
    assert fake.reported == [("book-001", {"send_status": "sent", "send_error": ""})]
    assert store.get_booking_delivery("book-001")["reported"] == 1


def test_persisted_sending_marked_uncertain(tmp_path):
    cfg = make_test_config(tmp_path)
    store = Store(cfg.db_path)
    folder = cfg.data_root / "exp01"
    folder.mkdir(parents=True)
    store.enqueue_booking(booking_payload(folder), 1000)
    assert store.mark_booking_sending("book-001")
    another_process = Store(cfg.db_path)
    # CLI 读取不能修改正在执行的任务；只有真正的 Agent 重启才恢复状态。
    assert another_process.get_booking_delivery("book-001")["status"] == "sending"
    another_process.recover_unfinished_bookings()
    row = another_process.get_booking_delivery("book-001")
    assert row["status"] == "uncertain"
    assert another_process.ready_booking(2000) is None
    with pytest.raises(RuntimeError):
        another_process.retry_booking("book-001", 2000)
    another_process.retry_booking("book-001", 2000, allow_uncertain=True)
    assert another_process.get_booking_delivery("book-001")["status"] == "queued"


def test_malformed_folder_rejected(tmp_path, monkeypatch):
    cfg = make_test_config(tmp_path)
    store = Store(cfg.db_path)
    folder = tmp_path / "outside"
    folder.mkdir()
    (folder / "file.txt").write_text("sensitive")
    fake = FakeBooking()
    fake.booking = booking_payload(folder)
    agent = Agent(cfg, store)
    agent.booking = fake
    monkeypatch.setattr("labcheckin.agent.deliver", lambda *a: pytest.fail("must not send files outside data_root"))
    agent._sync_desk()
    agent._process_booking()
    assert store.get_booking_delivery("book-001")["status"] == "failed"


def test_no_data_file_wait_timeout(tmp_path, monkeypatch):
    cfg = make_test_config(tmp_path)
    store = Store(cfg.db_path)
    folder = cfg.data_root / "empty"
    folder.mkdir(parents=True)
    store.enqueue_booking(booking_payload(folder), 1000)
    agent = Agent(cfg, store)
    monkeypatch.setattr("labcheckin.agent.time.time", lambda: 1121)
    agent._process_booking()
    assert store.get_booking_delivery("book-001")["status"] == "failed"
    assert "未发现实验数据" in store.get_booking_delivery("book-001")["error"]


def test_booking_folder_is_stable_across_restarts(tmp_path):
    cfg = make_test_config(tmp_path)
    store = Store(cfg.db_path)
    fake = FakeBooking()
    fake.phase = "checked-in"
    fake.booking = booking_payload("")
    agent = Agent(cfg, store)
    agent.booking = fake
    agent._sync_desk()
    folder1 = store.folder_for_booking("book-001")
    assert Path(folder1).is_dir()
    again = Agent(cfg, Store(cfg.db_path))
    again.booking = fake
    again._sync_desk()
    assert again.store.folder_for_booking("book-001") == folder1
    assert len(list(cfg.data_root.iterdir())) == 1


def test_smtp_environment_variables_override_config(monkeypatch):
    monkeypatch.setenv("LAB_SMTP_HOST", "smtp.portable.test")
    monkeypatch.setenv("LAB_SMTP_PORT", "2465")
    monkeypatch.setenv("LAB_SMTP_SECURITY", "ssl")
    monkeypatch.setenv("LAB_SMTP_USER", "portable@example.org")
    monkeypatch.setenv("LAB_SMTP_FROM", "portable@example.org")
    monkeypatch.setenv("LAB_SMTP_PASSWORD", "portable-test-secret")
    cfg = load_config()
    assert cfg.smtp_host == "smtp.portable.test"
    assert cfg.smtp_port == 2465
    assert cfg.smtp_security == "ssl"
    assert cfg.smtp_user == "portable@example.org"
    assert cfg.smtp_from == "portable@example.org"
    assert cfg.smtp_password == "portable-test-secret"
