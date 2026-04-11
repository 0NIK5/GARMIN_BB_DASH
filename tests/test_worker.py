"""Tests for worker/worker.py — multi-slot job logic, upsert, credentials."""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from worker.worker import (
    BodyBatteryLog,
    Base,
    get_last_timestamp,
    upsert_entries,
    load_credentials,
    run_job,
    run_all_slots,
    credentials_file,
    token_dir_for,
    SLOTS,
)


@pytest.fixture()
def worker_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# Slot path helpers
# ---------------------------------------------------------------------------

class TestSlotPaths:
    def test_credentials_file_left(self):
        assert credentials_file("left").endswith("credentials_left.json")

    def test_credentials_file_right(self):
        assert credentials_file("right").endswith("credentials_right.json")

    def test_token_dir_left(self):
        assert token_dir_for("left").endswith(os.path.join("tokens", "left"))

    def test_token_dir_right(self):
        assert token_dir_for("right").endswith(os.path.join("tokens", "right"))

    def test_slots_constant(self):
        assert "left" in SLOTS
        assert "right" in SLOTS


# ---------------------------------------------------------------------------
# load_credentials
# ---------------------------------------------------------------------------

class TestWorkerLoadCredentials:
    def test_returns_none_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("worker.worker.CREDENTIALS_DIR", str(tmp_path))
        assert load_credentials("left") is None

    def test_loads_left_credentials(self, tmp_path, monkeypatch):
        monkeypatch.setattr("worker.worker.CREDENTIALS_DIR", str(tmp_path))
        (tmp_path / "credentials_left.json").write_text(
            json.dumps({"username": "left@test.com", "password": "pw1"})
        )
        creds = load_credentials("left")
        assert creds["username"] == "left@test.com"

    def test_loads_right_credentials(self, tmp_path, monkeypatch):
        monkeypatch.setattr("worker.worker.CREDENTIALS_DIR", str(tmp_path))
        (tmp_path / "credentials_right.json").write_text(
            json.dumps({"username": "right@test.com", "password": "pw2"})
        )
        creds = load_credentials("right")
        assert creds["username"] == "right@test.com"

    def test_slots_are_independent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("worker.worker.CREDENTIALS_DIR", str(tmp_path))
        (tmp_path / "credentials_left.json").write_text(
            json.dumps({"username": "left@test.com", "password": "p1"})
        )
        (tmp_path / "credentials_right.json").write_text(
            json.dumps({"username": "right@test.com", "password": "p2"})
        )
        assert load_credentials("left")["username"] == "left@test.com"
        assert load_credentials("right")["username"] == "right@test.com"


# ---------------------------------------------------------------------------
# get_last_timestamp
# ---------------------------------------------------------------------------

class TestGetLastTimestamp:
    def test_returns_none_when_empty(self, worker_db):
        assert get_last_timestamp(worker_db, "user@test.com") is None

    def test_returns_latest_for_user(self, worker_db):
        now = datetime.now(timezone.utc)
        earlier = now - timedelta(hours=1)
        worker_db.add(BodyBatteryLog(username="u", measured_at=earlier, level=70, fetched_at=now))
        worker_db.add(BodyBatteryLog(username="u", measured_at=now, level=80, fetched_at=now))
        worker_db.commit()
        result = get_last_timestamp(worker_db, "u")
        assert result.replace(tzinfo=None) == now.replace(tzinfo=None)

    def test_isolates_by_username(self, worker_db):
        now = datetime.now(timezone.utc)
        worker_db.add(BodyBatteryLog(username="a", measured_at=now, level=70, fetched_at=now))
        worker_db.commit()
        assert get_last_timestamp(worker_db, "b") is None


# ---------------------------------------------------------------------------
# upsert_entries
# ---------------------------------------------------------------------------

class TestUpsertEntries:
    def test_inserts_new_entries(self, worker_db):
        now = datetime.now(timezone.utc)
        entries = [
            {"measured_at": now - timedelta(minutes=10), "level": 70},
            {"measured_at": now - timedelta(minutes=5), "level": 75},
            {"measured_at": now, "level": 80},
        ]
        inserted = upsert_entries(worker_db, entries, username="u@test.com", profile_name="TestUser")
        assert inserted == 3
        rows = worker_db.scalars(select(BodyBatteryLog)).all()
        assert len(rows) == 3
        assert all(r.profile_name == "TestUser" for r in rows)

    def test_skips_duplicates(self, worker_db):
        now = datetime.now(timezone.utc)
        entries = [{"measured_at": now, "level": 70}]
        upsert_entries(worker_db, entries, username="u", profile_name="User1")
        inserted = upsert_entries(worker_db, entries, username="u", profile_name="User1")
        assert inserted == 0
        assert len(worker_db.scalars(select(BodyBatteryLog)).all()) == 1

    def test_updates_profile_name_on_duplicate(self, worker_db):
        now = datetime.now(timezone.utc)
        entries = [{"measured_at": now, "level": 70}]
        upsert_entries(worker_db, entries, username="u", profile_name="OldName")
        upsert_entries(worker_db, entries, username="u", profile_name="NewName")
        row = worker_db.scalars(select(BodyBatteryLog)).first()
        assert row.profile_name == "NewName"

    def test_users_do_not_share_records(self, worker_db):
        now = datetime.now(timezone.utc)
        entries = [{"measured_at": now, "level": 70}]
        upsert_entries(worker_db, entries, username="user_a")
        upsert_entries(worker_db, entries, username="user_b")
        rows = worker_db.scalars(select(BodyBatteryLog)).all()
        assert len(rows) == 2
        usernames = {r.username for r in rows}
        assert usernames == {"user_a", "user_b"}

    def test_empty_entries(self, worker_db):
        assert upsert_entries(worker_db, [], username="u") == 0

    def test_no_profile_name(self, worker_db):
        now = datetime.now(timezone.utc)
        upsert_entries(worker_db, [{"measured_at": now, "level": 70}], username="u", profile_name=None)
        row = worker_db.scalars(select(BodyBatteryLog)).first()
        assert row.profile_name is None


# ---------------------------------------------------------------------------
# run_job (mocked) — slot-aware
# ---------------------------------------------------------------------------

def _make_mock_client(entries=None, profile_name="TestUser"):
    mock_client = MagicMock()
    mock_client.get_heart_rate.return_value = {
        "profile_name": profile_name,
        "entries": entries or [],
    }
    return mock_client


class TestRunJob:
    def _patch_creds(self, monkeypatch, slot, creds):
        monkeypatch.setattr(
            "worker.worker.load_credentials",
            lambda s: creds if s == slot else None,
        )

    def test_no_credentials_returns_early(self, monkeypatch):
        monkeypatch.setattr("worker.worker.load_credentials", lambda slot: None)
        run_job("left")  # must not raise

    def test_empty_username_returns_early(self, monkeypatch):
        monkeypatch.setattr(
            "worker.worker.load_credentials",
            lambda slot: {"username": "", "password": "pass"},
        )
        run_job("left")  # must not raise

    def test_unknown_slot_returns_early(self, monkeypatch):
        monkeypatch.setattr("worker.worker.load_credentials", lambda slot: None)
        run_job("center")  # must not raise

    def test_successful_job_left(self, monkeypatch, tmp_path):
        now = datetime.now(timezone.utc)
        self._patch_creds(monkeypatch, "left", {"username": "left@test.com", "password": "pw"})

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        monkeypatch.setattr("worker.worker.get_engine", lambda: engine)
        monkeypatch.setattr("worker.worker.token_dir_for", lambda slot: str(tmp_path / slot))

        mock_client = _make_mock_client(
            entries=[
                {"measured_at": now - timedelta(minutes=5), "level": 72},
                {"measured_at": now, "level": 75},
            ]
        )
        monkeypatch.setattr("worker.worker.NodeGarminClient", MagicMock(return_value=mock_client))

        run_job("left")

        session = sessionmaker(bind=engine)()
        rows = session.scalars(select(BodyBatteryLog).where(BodyBatteryLog.username == "left@test.com")).all()
        assert len(rows) == 2
        assert all(r.profile_name == "TestUser" for r in rows)
        session.close()

    def test_successful_job_right(self, monkeypatch, tmp_path):
        now = datetime.now(timezone.utc)
        self._patch_creds(monkeypatch, "right", {"username": "right@test.com", "password": "pw"})

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        monkeypatch.setattr("worker.worker.get_engine", lambda: engine)
        monkeypatch.setattr("worker.worker.token_dir_for", lambda slot: str(tmp_path / slot))

        mock_client = _make_mock_client(
            entries=[{"measured_at": now, "level": 88}],
            profile_name="RightUser",
        )
        monkeypatch.setattr("worker.worker.NodeGarminClient", MagicMock(return_value=mock_client))

        run_job("right")

        session = sessionmaker(bind=engine)()
        rows = session.scalars(select(BodyBatteryLog).where(BodyBatteryLog.username == "right@test.com")).all()
        assert len(rows) == 1
        assert rows[0].level == 88
        session.close()

    def test_job_passes_slot_token_dir_to_client(self, monkeypatch, tmp_path):
        now = datetime.now(timezone.utc)
        self._patch_creds(monkeypatch, "left", {"username": "u@test.com", "password": "pw"})

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        monkeypatch.setattr("worker.worker.get_engine", lambda: engine)

        expected_token_dir = str(tmp_path / "left")
        monkeypatch.setattr("worker.worker.token_dir_for", lambda slot: str(tmp_path / slot))

        mock_class = MagicMock()
        mock_class.return_value = _make_mock_client()
        monkeypatch.setattr("worker.worker.NodeGarminClient", mock_class)

        run_job("left")

        call_kwargs = mock_class.call_args
        assert call_kwargs.kwargs.get("token_dir") == expected_token_dir

    def test_job_failure_raises(self, monkeypatch, tmp_path):
        self._patch_creds(monkeypatch, "left", {"username": "u@test.com", "password": "pw"})
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        monkeypatch.setattr("worker.worker.get_engine", lambda: engine)
        monkeypatch.setattr("worker.worker.token_dir_for", lambda slot: str(tmp_path / slot))

        mock_client = MagicMock()
        mock_client.get_heart_rate.side_effect = RuntimeError("Node helper exited with code 3")
        monkeypatch.setattr("worker.worker.NodeGarminClient", MagicMock(return_value=mock_client))

        with pytest.raises(RuntimeError, match="Node helper"):
            run_job("left")

    def test_slots_write_independent_records(self, monkeypatch, tmp_path):
        """Left and right slots write to same DB but different usernames."""
        now = datetime.now(timezone.utc)
        creds_map = {
            "left": {"username": "left@test.com", "password": "pw"},
            "right": {"username": "right@test.com", "password": "pw"},
        }
        monkeypatch.setattr("worker.worker.load_credentials", lambda slot: creds_map.get(slot))

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        monkeypatch.setattr("worker.worker.get_engine", lambda: engine)
        monkeypatch.setattr("worker.worker.token_dir_for", lambda slot: str(tmp_path / slot))

        def fake_client_class(username, password, token_dir):
            mc = MagicMock()
            mc.get_heart_rate.return_value = {
                "profile_name": username,
                "entries": [{"measured_at": now, "level": 50 if "left" in username else 90}],
            }
            return mc

        monkeypatch.setattr("worker.worker.NodeGarminClient", fake_client_class)

        run_job("left")
        run_job("right")

        session = sessionmaker(bind=engine)()
        left_rows = session.scalars(select(BodyBatteryLog).where(BodyBatteryLog.username == "left@test.com")).all()
        right_rows = session.scalars(select(BodyBatteryLog).where(BodyBatteryLog.username == "right@test.com")).all()
        assert len(left_rows) == 1 and left_rows[0].level == 50
        assert len(right_rows) == 1 and right_rows[0].level == 90
        session.close()


# ---------------------------------------------------------------------------
# run_all_slots
# ---------------------------------------------------------------------------

class TestRunAllSlots:
    def test_runs_both_slots(self, monkeypatch):
        called = []
        monkeypatch.setattr("worker.worker.run_job", lambda slot: called.append(slot))
        run_all_slots()
        assert "left" in called
        assert "right" in called

    def test_continues_if_one_slot_fails(self, monkeypatch):
        called = []

        def fake_run_job(slot):
            if slot == "left":
                raise RuntimeError("left failed")
            called.append(slot)

        monkeypatch.setattr("worker.worker.run_job", fake_run_job)
        run_all_slots()  # must not raise
        assert "right" in called
