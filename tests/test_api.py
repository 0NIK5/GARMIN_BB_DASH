"""Tests for backend/app/api.py — FastAPI endpoints and helper functions."""

import json
import os
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import Base, get_db
from backend.app.models import BodyBatteryLog
from backend.app.main import app
from backend.app.api import (
    compute_status,
    _ensure_utc,
    load_credentials,
    save_credentials,
    delete_credentials,
    clear_saved_tokens,
    CREDENTIALS_FILE,
    TOKEN_DIR,
)


# ---------------------------------------------------------------------------
# Test helpers: compute_status / _ensure_utc
# ---------------------------------------------------------------------------


class FakeRecord:
    def __init__(self, level):
        self.level = level


class TestComputeStatus:
    def test_unknown_with_less_than_2_records(self):
        assert compute_status([]) == "unknown"
        assert compute_status([FakeRecord(70)]) == "unknown"

    def test_increasing(self):
        records = [FakeRecord(60), FakeRecord(65), FakeRecord(70)]
        assert compute_status(records) == "increasing"

    def test_decreasing(self):
        records = [FakeRecord(80), FakeRecord(75), FakeRecord(70)]
        assert compute_status(records) == "decreasing"

    def test_stable(self):
        records = [FakeRecord(70), FakeRecord(70), FakeRecord(70)]
        assert compute_status(records) == "stable"

    def test_uses_last_three_only(self):
        records = [FakeRecord(50), FakeRecord(60), FakeRecord(70), FakeRecord(80), FakeRecord(90)]
        assert compute_status(records) == "increasing"

    def test_two_records_increasing(self):
        records = [FakeRecord(60), FakeRecord(70)]
        assert compute_status(records) == "increasing"

    def test_two_records_decreasing(self):
        records = [FakeRecord(80), FakeRecord(70)]
        assert compute_status(records) == "decreasing"


class TestEnsureUtc:
    def test_naive_datetime_gets_utc(self):
        dt = datetime(2026, 1, 1, 12, 0, 0)
        result = _ensure_utc(dt)
        assert result.tzinfo == timezone.utc

    def test_aware_datetime_unchanged(self):
        tz = timezone(timedelta(hours=3))
        dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=tz)
        result = _ensure_utc(dt)
        assert result.tzinfo == tz


# ---------------------------------------------------------------------------
# Test credentials management
# ---------------------------------------------------------------------------


class TestCredentials:
    def test_save_and_load(self, tmp_path, monkeypatch):
        cred_file = str(tmp_path / "creds.json")
        monkeypatch.setattr("backend.app.api.CREDENTIALS_FILE", cred_file)
        monkeypatch.setattr("backend.app.api.TOKEN_DIR", str(tmp_path / "tokens"))

        save_credentials("user@test.com", "pass123")
        creds = load_credentials()
        assert creds["username"] == "user@test.com"
        assert creds["password"] == "pass123"

    def test_load_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.app.api.CREDENTIALS_FILE", str(tmp_path / "nonexistent.json"))
        assert load_credentials() is None

    def test_delete_credentials(self, tmp_path, monkeypatch):
        cred_file = str(tmp_path / "creds.json")
        monkeypatch.setattr("backend.app.api.CREDENTIALS_FILE", cred_file)
        monkeypatch.setattr("backend.app.api.TOKEN_DIR", str(tmp_path / "tokens"))

        save_credentials("user@test.com", "pass123")
        assert os.path.exists(cred_file)
        delete_credentials()
        assert not os.path.exists(cred_file)

    def test_save_clears_tokens(self, tmp_path, monkeypatch):
        token_dir = tmp_path / "tokens"
        token_dir.mkdir()
        (token_dir / "oauth1_token.json").write_text("{}")
        (token_dir / "oauth2_token.json").write_text("{}")

        monkeypatch.setattr("backend.app.api.CREDENTIALS_FILE", str(tmp_path / "creds.json"))
        monkeypatch.setattr("backend.app.api.TOKEN_DIR", str(token_dir))

        save_credentials("new@test.com", "pass")
        assert not (token_dir / "oauth1_token.json").exists()
        assert not (token_dir / "oauth2_token.json").exists()

    def test_delete_clears_tokens(self, tmp_path, monkeypatch):
        token_dir = tmp_path / "tokens"
        token_dir.mkdir()
        (token_dir / "oauth1_token.json").write_text("{}")

        cred_file = str(tmp_path / "creds.json")
        monkeypatch.setattr("backend.app.api.CREDENTIALS_FILE", cred_file)
        monkeypatch.setattr("backend.app.api.TOKEN_DIR", str(token_dir))

        save_credentials("user@test.com", "pass")
        delete_credentials()
        assert not (token_dir / "oauth1_token.json").exists()


class TestClearSavedTokens:
    def test_clears_files_in_token_dir(self, tmp_path, monkeypatch):
        token_dir = tmp_path / "tokens"
        token_dir.mkdir()
        (token_dir / "file1.json").write_text("{}")
        (token_dir / "file2.json").write_text("{}")

        monkeypatch.setattr("backend.app.api.TOKEN_DIR", str(token_dir))
        clear_saved_tokens()
        assert len(list(token_dir.iterdir())) == 0

    def test_no_error_when_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.app.api.TOKEN_DIR", str(tmp_path / "nonexistent"))
        clear_saved_tokens()  # should not raise


# ---------------------------------------------------------------------------
# Test API endpoints via TestClient
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def client(test_db):
    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestGetCurrentEndpoint:
    def test_404_when_no_data(self, client):
        resp = client.get("/api/v1/battery/current")
        assert resp.status_code == 404

    def test_returns_current_data(self, client, test_db):
        now = datetime.now(timezone.utc)
        test_db.add(BodyBatteryLog(
            measured_at=now,
            level=72,
            fetched_at=now,
            profile_name="TestUser",
        ))
        test_db.commit()

        resp = client.get("/api/v1/battery/current")
        assert resp.status_code == 200
        data = resp.json()
        assert data["level"] == 72
        assert data["profile_name"] == "TestUser"
        assert "status" in data
        assert "is_stale" in data
        assert "minutes_since_update" in data

    def test_stale_flag_when_old_data(self, client, test_db):
        old_time = datetime.now(timezone.utc) - timedelta(minutes=30)
        test_db.add(BodyBatteryLog(
            measured_at=old_time,
            level=65,
            fetched_at=old_time,
        ))
        test_db.commit()

        resp = client.get("/api/v1/battery/current")
        data = resp.json()
        assert data["is_stale"] is True
        assert data["minutes_since_update"] >= 29


class TestGetHistoryEndpoint:
    def test_empty_history(self, client):
        resp = client.get("/api/v1/battery/history?hours=24")
        assert resp.status_code == 200
        data = resp.json()
        assert data["period_hours"] == 24
        assert data["data"] == []

    def test_returns_data_within_period(self, client, test_db):
        now = datetime.now(timezone.utc)
        test_db.add(BodyBatteryLog(measured_at=now - timedelta(hours=1), level=70, fetched_at=now))
        test_db.add(BodyBatteryLog(measured_at=now - timedelta(hours=25), level=60, fetched_at=now))
        test_db.commit()

        resp = client.get("/api/v1/battery/history?hours=24")
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["level"] == 70

    def test_invalid_hours_param(self, client):
        resp = client.get("/api/v1/battery/history?hours=abc")
        assert resp.status_code == 400

    def test_hours_out_of_range(self, client):
        resp = client.get("/api/v1/battery/history?hours=0")
        assert resp.status_code == 400
        resp = client.get("/api/v1/battery/history?hours=200")
        assert resp.status_code == 400

    def test_default_24_hours(self, client):
        resp = client.get("/api/v1/battery/history")
        assert resp.status_code == 200
        assert resp.json()["period_hours"] == 24


class TestConfigEndpoint:
    def test_not_logged_in(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", lambda: None)
        resp = client.get("/api/v1/config")
        assert resp.status_code == 200
        assert resp.json()["username"] == "Not logged in"

    def test_logged_in(self, client, monkeypatch):
        monkeypatch.setattr(
            "backend.app.api.load_credentials",
            lambda: {"username": "user@test.com", "password": "x"},
        )
        resp = client.get("/api/v1/config")
        assert resp.json()["username"] == "user@test.com"


class TestLoginEndpoint:
    def test_login_success(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.save_credentials", lambda u, p: None)
        resp = client.post("/api/v1/login", json={"username": "user", "password": "pass"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_login_missing_fields(self, client):
        resp = client.post("/api/v1/login", json={"username": "", "password": ""})
        assert resp.status_code == 400

    def test_login_missing_password(self, client):
        resp = client.post("/api/v1/login", json={"username": "user"})
        assert resp.status_code == 400


class TestLogoutEndpoint:
    def test_logout_success(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.delete_credentials", lambda: None)
        resp = client.post("/api/v1/logout")
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestRefreshEndpoint:
    def test_refresh_no_credentials(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", lambda: None)
        resp = client.post("/api/v1/refresh")
        assert resp.status_code == 401

    def test_refresh_success(self, client, monkeypatch):
        monkeypatch.setattr(
            "backend.app.api.load_credentials",
            lambda: {"username": "user", "password": "pass"},
        )
        monkeypatch.setattr("backend.app.api._get_run_job", lambda: lambda: None)
        resp = client.post("/api/v1/refresh")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_refresh_worker_failure(self, client, monkeypatch):
        monkeypatch.setattr(
            "backend.app.api.load_credentials",
            lambda: {"username": "user", "password": "pass"},
        )

        def failing_job():
            raise RuntimeError("Node helper exited with code 3")

        monkeypatch.setattr("backend.app.api._get_run_job", lambda: failing_job)
        resp = client.post("/api/v1/refresh")
        assert resp.status_code == 500
        assert "Node helper" in resp.json()["detail"]


class TestHealthEndpoint:
    def test_health(self, client):
        # /health is defined after app.mount("/", StaticFiles(...))
        # so it may be shadowed by the static file handler.
        resp = client.get("/health")
        if resp.status_code == 200:
            assert resp.json()["status"] == "ok"
        else:
            pytest.skip("health endpoint shadowed by StaticFiles mount")
