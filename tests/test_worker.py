"""Tests for worker/worker.py — job logic, upsert, credentials loading."""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

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
    CREDENTIALS_FILE,
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
# get_last_timestamp
# ---------------------------------------------------------------------------


class TestGetLastTimestamp:
    def test_returns_none_when_empty(self, worker_db):
        assert get_last_timestamp(worker_db) is None

    def test_returns_latest_timestamp(self, worker_db):
        now = datetime.now(timezone.utc)
        earlier = now - timedelta(hours=1)
        worker_db.add(BodyBatteryLog(measured_at=earlier, level=70, fetched_at=now))
        worker_db.add(BodyBatteryLog(measured_at=now, level=80, fetched_at=now))
        worker_db.commit()

        result = get_last_timestamp(worker_db)
        # SQLite may return naive datetime — compare without tz
        assert result.replace(tzinfo=None) == now.replace(tzinfo=None)


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
        inserted = upsert_entries(worker_db, entries, profile_name="TestUser")
        assert inserted == 3

        all_rows = worker_db.scalars(select(BodyBatteryLog)).all()
        assert len(all_rows) == 3
        assert all(r.profile_name == "TestUser" for r in all_rows)

    def test_skips_duplicates(self, worker_db):
        now = datetime.now(timezone.utc)
        entries = [{"measured_at": now, "level": 70}]

        upsert_entries(worker_db, entries, profile_name="User1")
        inserted = upsert_entries(worker_db, entries, profile_name="User1")
        assert inserted == 0

        all_rows = worker_db.scalars(select(BodyBatteryLog)).all()
        assert len(all_rows) == 1

    def test_updates_profile_name_on_duplicate(self, worker_db):
        now = datetime.now(timezone.utc)
        entries = [{"measured_at": now, "level": 70}]

        upsert_entries(worker_db, entries, profile_name="OldUser")
        upsert_entries(worker_db, entries, profile_name="NewUser")

        row = worker_db.scalars(select(BodyBatteryLog)).first()
        assert row.profile_name == "NewUser"

    def test_no_profile_name(self, worker_db):
        now = datetime.now(timezone.utc)
        entries = [{"measured_at": now, "level": 70}]

        inserted = upsert_entries(worker_db, entries, profile_name=None)
        assert inserted == 1
        row = worker_db.scalars(select(BodyBatteryLog)).first()
        assert row.profile_name is None

    def test_empty_entries(self, worker_db):
        inserted = upsert_entries(worker_db, [], profile_name="User")
        assert inserted == 0


# ---------------------------------------------------------------------------
# load_credentials
# ---------------------------------------------------------------------------


class TestWorkerLoadCredentials:
    def test_returns_none_when_file_missing(self, monkeypatch):
        monkeypatch.setattr("worker.worker.CREDENTIALS_FILE", "/nonexistent/creds.json")
        assert load_credentials() is None

    def test_loads_credentials(self, tmp_path, monkeypatch):
        cred_file = tmp_path / "creds.json"
        cred_file.write_text(json.dumps({"username": "user", "password": "pass"}))
        monkeypatch.setattr("worker.worker.CREDENTIALS_FILE", str(cred_file))

        creds = load_credentials()
        assert creds["username"] == "user"
        assert creds["password"] == "pass"


# ---------------------------------------------------------------------------
# run_job (mocked)
# ---------------------------------------------------------------------------


class TestRunJob:
    def test_no_credentials_returns_early(self, monkeypatch):
        monkeypatch.setattr("worker.worker.load_credentials", lambda: None)
        # Should not raise
        run_job()

    def test_empty_username_returns_early(self, monkeypatch):
        monkeypatch.setattr(
            "worker.worker.load_credentials",
            lambda: {"username": "", "password": "pass"},
        )
        run_job()

    def test_successful_job(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "worker.worker.load_credentials",
            lambda: {"username": "user@test.com", "password": "pass"},
        )

        now = datetime.now(timezone.utc)
        mock_client = MagicMock()
        mock_client.get_heart_rate.return_value = {
            "profile_name": "TestUser",
            "entries": [
                {"measured_at": now - timedelta(minutes=5), "level": 72},
                {"measured_at": now, "level": 75},
            ],
        }

        mock_client_class = MagicMock(return_value=mock_client)
        monkeypatch.setattr("worker.worker.NodeGarminClient", mock_client_class)

        # Use in-memory DB
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        monkeypatch.setattr("worker.worker.get_engine", lambda: engine)

        run_job()

        # Verify data was inserted
        session = sessionmaker(bind=engine)()
        rows = session.scalars(select(BodyBatteryLog)).all()
        assert len(rows) == 2
        assert rows[0].profile_name == "TestUser"
        session.close()

    def test_job_failure_raises(self, monkeypatch):
        monkeypatch.setattr(
            "worker.worker.load_credentials",
            lambda: {"username": "user@test.com", "password": "pass"},
        )

        mock_client = MagicMock()
        mock_client.get_heart_rate.side_effect = RuntimeError("Node helper exited with code 3")

        mock_client_class = MagicMock(return_value=mock_client)
        monkeypatch.setattr("worker.worker.NodeGarminClient", mock_client_class)

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        monkeypatch.setattr("worker.worker.get_engine", lambda: engine)

        with pytest.raises(RuntimeError, match="Node helper"):
            run_job()
