from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from labcheckin.agent import Agent
from labcheckin.config import load_config
from labcheckin.db import Store
from labcheckin.demo_data import generate_demo_files
from labcheckin.mail import make_message


class DeskBackend:
    enabled = True
    def __init__(self, phase="checked-in", folder_path=""):
        self.phase = phase
        self.booking = {
            "id": "booking-101", "applicant": "演示用户", "checkInAt": "2026-11-11T14:30:00+08:00",
            "checkOutAt": "", "email": "tester@example.org", "folderPath": folder_path,
        }
        self.reported = []
    def desk(self):
        return {"phase": self.phase, "booking": self.booking}
    def report_desk(self, booking_id, **kwargs):
        self.reported.append((booking_id, kwargs))


@pytest.fixture
def configured(tmp_path):
    cfg = replace(load_config(), data_root=tmp_path / "desktop" / "Experiment Data",
                  db_path=tmp_path / "db" / "lab.sqlite", instrument_id="instrument-1",
                  demo_seed_on_checkin=True, smtp_host="localhost", smtp_from="bot@example.org")
    return cfg, Store(cfg.db_path)


def test_checked_in_creates_folder_and_seed_when_server_folder_path_stale(configured):
    cfg, store = configured
    agent = Agent(cfg, store)
    backend = DeskBackend(folder_path="C:\\AnotherMachine\\old-folder")
    agent.booking = backend
    agent._sync_desk()
    path = Path(store.folder_for_booking("booking-101"))
    assert path.is_dir() and path.parent == cfg.data_root
    names = sorted(p.name for p in path.iterdir())
    assert names == sorted(["DEMO_README_模拟数据_不可用于科研.txt", "DEMO_experiment_info.json", "DEMO_pressure_temperature.csv"])
    assert backend.reported == [("booking-101", {"folder_path": str(path)})]
    assert "YES_FAKE_DATA" in (path / "DEMO_pressure_temperature.csv").read_text(encoding="utf-8-sig")
    assert "SYNTHETIC" in (path / "DEMO_experiment_info.json").read_text(encoding="utf-8")
    # User can delete a demo file and continue the same booking without it regenerating.
    (path / "DEMO_pressure_temperature.csv").unlink()
    agent._sync_desk()
    assert not (path / "DEMO_pressure_temperature.csv").exists()
    assert len(backend.reported) == 2  # Server fake still returns the stale folderPath.
    assert len(list(cfg.data_root.iterdir())) == 1


def test_existing_server_folder_path_adopted_and_not_overwritten(configured):
    cfg, store = configured
    folder = cfg.data_root / "existing-booking"
    folder.mkdir(parents=True)
    existing = folder / "real-measured.csv"
    existing.write_text("original measurement", encoding="utf-8")
    backend = DeskBackend(folder_path=str(folder))
    agent = Agent(cfg, store)
    agent.booking = backend
    agent._sync_desk()
    assert store.folder_for_booking("booking-101") == str(folder)
    assert existing.read_text(encoding="utf-8") == "original measurement"
    assert not backend.reported


def test_need_send_uses_local_folder_even_if_server_has_no_path(configured):
    cfg, store = configured
    agent = Agent(cfg, store)
    backend = DeskBackend()
    agent.booking = backend
    agent._sync_desk()
    expected = store.folder_for_booking("booking-101")
    backend.phase = "need-send"
    backend.booking["checkOutAt"] = "2026-11-11T15:00:00+08:00"
    backend.booking["folderPath"] = ""
    agent._sync_desk()
    row = store.get_booking_delivery("booking-101")
    assert row["folder_path"] == expected
    assert row["status"] == "queued"
    fake_files = list(Path(expected).glob("DEMO_*"))
    mail = make_message(cfg, {
        "email": "tester@example.org", "folder_path": expected, "username": "演示用户",
        "booking_id": "booking-101", "check_in_at": "2026", "check_out_at": "2026",
    }, fake_files)
    assert "模拟数据测试" in mail["Subject"]
    assert "不是真实实验测量数据" in mail.get_body(preferencelist=("plain",)).get_content()
    assert len(list(mail.iter_attachments())) == 3


def test_preview_created_on_start_but_does_not_count_as_booking_folder(configured):
    cfg, store = configured
    agent = Agent(cfg, store)
    backend = DeskBackend(phase="idle")
    agent.booking = backend
    agent.start()
    agent.stop()
    preview = cfg.data_root / "__DEMO_PREVIEW_DO_NOT_EMAIL__"
    assert preview.is_dir()
    assert len(list(preview.iterdir())) == 3
    assert agent._safe_folder(str(preview)) is None
    assert store.folder_for_booking("booking-101") is None


def test_demo_disabled_does_not_seed(configured):
    cfg, store = configured
    cfg = replace(cfg, demo_seed_on_checkin=False)
    backend = DeskBackend()
    agent = Agent(cfg, store)
    agent.booking = backend
    agent._sync_desk()
    folder = Path(store.folder_for_booking("booking-101"))
    assert folder.is_dir()
    assert list(folder.iterdir()) == []


def test_generator_is_idempotent(tmp_path):
    folder = tmp_path / "f"
    first = generate_demo_files(folder, "B", "I")
    second = generate_demo_files(folder, "B", "I")
    assert len(first) == 3 and second == []


def test_simulated_booking_flows_to_mail_and_reports_success(configured, monkeypatch):
    """End-to-end within Agent with fake reservation desk and fake mail transport."""
    import os
    import time
    cfg, store = configured
    agent = Agent(cfg, store)
    desk = DeskBackend()
    agent.booking = desk
    agent._sync_desk()
    folder = Path(store.folder_for_booking("booking-101"))
    now = time.time()
    for file in folder.iterdir():
        os.utime(file, (now - 120, now - 120))
    desk.phase = "need-send"
    desk.booking["checkOutAt"] = "2026-11-11T16:00:00+08:00"
    agent._sync_desk()
    sent = []
    monkeypatch.setattr("labcheckin.agent.deliver", lambda config, session, files: sent.append((session, files)))
    monkeypatch.setattr("labcheckin.agent.time.time", lambda: now + 40)
    agent._process_booking()  # snapshot files, not yet send
    monkeypatch.setattr("labcheckin.agent.time.time", lambda: now + 65)
    agent._process_booking()  # stable -> one mail
    agent._report_booking()
    assert len(sent) == 1
    assert len(sent[0][1]) == 3
    assert sent[0][0]["email"] == "tester@example.org"
    assert store.get_booking_delivery("booking-101")["status"] == "sent"
    assert desk.reported[-1] == ("booking-101", {"send_status": "sent", "send_error": ""})
