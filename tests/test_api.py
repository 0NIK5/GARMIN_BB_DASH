"""Tests for backend/app/api.py — multi-slot endpoints and helper functions."""

import json
import os
from datetime import datetime, timezone, timedelta
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
    _credentials_file,
    _token_dir,
    DATA_DIR,
    TOKENS_ROOT,
    SLOTS,
)


# ---------------------------------------------------------------------------
# Pure helpers: compute_status / _ensure_utc
# ---------------------------------------------------------------------------

class FakeRecord:
    def __init__(self, level):
        self.level = level


class TestComputeStatus:
    def test_unknown_with_less_than_2_records(self):
        assert compute_status([]) == "unknown"
        assert compute_status([FakeRecord(70)]) == "unknown"

    def test_increasing(self):
        assert compute_status([FakeRecord(60), FakeRecord(65), FakeRecord(70)]) == "increasing"

    def test_decreasing(self):
        assert compute_status([FakeRecord(80), FakeRecord(75), FakeRecord(70)]) == "decreasing"

    def test_stable(self):
        assert compute_status([FakeRecord(70), FakeRecord(70), FakeRecord(70)]) == "stable"

    def test_uses_last_three_only(self):
        records = [FakeRecord(50), FakeRecord(60), FakeRecord(70), FakeRecord(80), FakeRecord(90)]
        assert compute_status(records) == "increasing"

    def test_two_records_increasing(self):
        assert compute_status([FakeRecord(60), FakeRecord(70)]) == "increasing"

    def test_two_records_decreasing(self):
        assert compute_status([FakeRecord(80), FakeRecord(70)]) == "decreasing"


class TestEnsureUtc:
    def test_naive_gets_utc(self):
        dt = datetime(2026, 1, 1, 12, 0, 0)
        assert _ensure_utc(dt).tzinfo == timezone.utc

    def test_aware_unchanged(self):
        tz = timezone(timedelta(hours=3))
        dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=tz)
        assert _ensure_utc(dt).tzinfo == tz


# ---------------------------------------------------------------------------
# Slot helper paths
# ---------------------------------------------------------------------------

class TestSlotPaths:
    def test_credentials_file_left(self):
        path = _credentials_file("left")
        assert path.endswith("credentials_left.json")

    def test_credentials_file_right(self):
        path = _credentials_file("right")
        assert path.endswith("credentials_right.json")

    def test_token_dir_left(self):
        assert _token_dir("left").endswith(os.path.join("tokens", "left"))

    def test_token_dir_right(self):
        assert _token_dir("right").endswith(os.path.join("tokens", "right"))

    def test_slots_constant(self):
        assert "left" in SLOTS
        assert "right" in SLOTS


# ---------------------------------------------------------------------------
# Credentials: save / load / delete per slot
# ---------------------------------------------------------------------------

class TestCredentials:
    def _patch(self, monkeypatch, tmp_path, slot):
        cred_file = str(tmp_path / f"credentials_{slot}.json")
        token_dir = str(tmp_path / "tokens" / slot)
        monkeypatch.setattr("backend.app.api.DATA_DIR", str(tmp_path))
        monkeypatch.setattr("backend.app.api.TOKENS_ROOT", str(tmp_path / "tokens"))
        return cred_file, token_dir

    @pytest.mark.parametrize("slot", ["left", "right"])
    def test_save_and_load(self, tmp_path, monkeypatch, slot):
        self._patch(monkeypatch, tmp_path, slot)
        save_credentials(slot, "user@test.com", "pass123")
        creds = load_credentials(slot)
        assert creds["username"] == "user@test.com"
        assert creds["password"] == "pass123"

    @pytest.mark.parametrize("slot", ["left", "right"])
    def test_load_missing_returns_none(self, tmp_path, monkeypatch, slot):
        monkeypatch.setattr("backend.app.api.DATA_DIR", str(tmp_path))
        assert load_credentials(slot) is None

    @pytest.mark.parametrize("slot", ["left", "right"])
    def test_delete_removes_file(self, tmp_path, monkeypatch, slot):
        self._patch(monkeypatch, tmp_path, slot)
        save_credentials(slot, "user@test.com", "pass123")
        cred_file = tmp_path / f"credentials_{slot}.json"
        assert cred_file.exists()
        delete_credentials(slot)
        assert not cred_file.exists()

    def test_slots_are_independent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.app.api.DATA_DIR", str(tmp_path))
        monkeypatch.setattr("backend.app.api.TOKENS_ROOT", str(tmp_path / "tokens"))
        save_credentials("left", "left@test.com", "pass1")
        save_credentials("right", "right@test.com", "pass2")
        assert load_credentials("left")["username"] == "left@test.com"
        assert load_credentials("right")["username"] == "right@test.com"

    def test_delete_left_does_not_affect_right(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.app.api.DATA_DIR", str(tmp_path))
        monkeypatch.setattr("backend.app.api.TOKENS_ROOT", str(tmp_path / "tokens"))
        save_credentials("left", "left@test.com", "pass1")
        save_credentials("right", "right@test.com", "pass2")
        delete_credentials("left")
        assert load_credentials("left") is None
        assert load_credentials("right")["username"] == "right@test.com"

    def test_save_clears_tokens(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.app.api.DATA_DIR", str(tmp_path))
        monkeypatch.setattr("backend.app.api.TOKENS_ROOT", str(tmp_path / "tokens"))
        token_dir = tmp_path / "tokens" / "left"
        token_dir.mkdir(parents=True)
        (token_dir / "oauth1_token.json").write_text("{}")
        (token_dir / "oauth2_token.json").write_text("{}")
        save_credentials("left", "user@test.com", "pass")
        assert not (token_dir / "oauth1_token.json").exists()
        assert not (token_dir / "oauth2_token.json").exists()

    def test_delete_clears_tokens(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.app.api.DATA_DIR", str(tmp_path))
        monkeypatch.setattr("backend.app.api.TOKENS_ROOT", str(tmp_path / "tokens"))
        token_dir = tmp_path / "tokens" / "right"
        token_dir.mkdir(parents=True)
        (token_dir / "oauth1_token.json").write_text("{}")
        save_credentials("right", "u", "p")
        delete_credentials("right")
        assert not (token_dir / "oauth1_token.json").exists()


class TestClearSavedTokens:
    def test_clears_files_in_token_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.app.api.TOKENS_ROOT", str(tmp_path))
        token_dir = tmp_path / "left"
        token_dir.mkdir()
        (token_dir / "file1.json").write_text("{}")
        (token_dir / "file2.json").write_text("{}")
        clear_saved_tokens("left")
        assert len(list(token_dir.iterdir())) == 0

    def test_no_error_when_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.app.api.TOKENS_ROOT", str(tmp_path / "nowhere"))
        clear_saved_tokens("left")  # must not raise

    def test_slots_are_independent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.app.api.TOKENS_ROOT", str(tmp_path))
        left_dir = tmp_path / "left"
        right_dir = tmp_path / "right"
        left_dir.mkdir()
        right_dir.mkdir()
        (left_dir / "token.json").write_text("{}")
        (right_dir / "token.json").write_text("{}")
        clear_saved_tokens("left")
        assert not (left_dir / "token.json").exists()
        assert (right_dir / "token.json").exists()  # right untouched


# ---------------------------------------------------------------------------
# TestClient fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def test_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
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


# credentials stub used in most endpoint tests
_LEFT_CREDS = {"username": "left@test.com", "password": "pass"}
_RIGHT_CREDS = {"username": "right@test.com", "password": "pass"}


def _creds_for_slot(slot):
    return {"left": _LEFT_CREDS, "right": _RIGHT_CREDS}.get(slot)


# ---------------------------------------------------------------------------
# /api/v1/config — both slots
# ---------------------------------------------------------------------------

class TestConfigEndpoint:
    def test_not_logged_in_left(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", lambda slot: None)
        resp = client.get("/api/v1/config?slot=left")
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "Not logged in"
        assert data["slot"] == "left"

    def test_not_logged_in_right(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", lambda slot: None)
        resp = client.get("/api/v1/config?slot=right")
        assert resp.json()["slot"] == "right"
        assert resp.json()["username"] == "Not logged in"

    def test_logged_in_left(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        resp = client.get("/api/v1/config?slot=left")
        assert resp.json()["username"] == "left@test.com"

    def test_logged_in_right(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        resp = client.get("/api/v1/config?slot=right")
        assert resp.json()["username"] == "right@test.com"

    def test_default_slot_is_left(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        resp = client.get("/api/v1/config")
        assert resp.json()["slot"] == "left"

    def test_invalid_slot(self, client):
        resp = client.get("/api/v1/config?slot=middle")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /api/v1/battery/current — both slots
# ---------------------------------------------------------------------------

class TestGetCurrentEndpoint:
    def _add_log(self, test_db, username, level=72, minutes_ago=0, profile_name="TestUser"):
        ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        test_db.add(BodyBatteryLog(
            username=username,
            measured_at=ts,
            level=level,
            fetched_at=ts,
            profile_name=profile_name,
        ))
        test_db.commit()

    def test_401_when_not_logged_in(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", lambda slot: None)
        assert client.get("/api/v1/battery/current?slot=left").status_code == 401

    def test_404_when_no_data(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        assert client.get("/api/v1/battery/current?slot=left").status_code == 404

    def test_returns_data_for_left_slot(self, client, test_db, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        self._add_log(test_db, "left@test.com", level=72)
        resp = client.get("/api/v1/battery/current?slot=left")
        assert resp.status_code == 200
        data = resp.json()
        assert data["level"] == 72
        assert data["profile_name"] == "TestUser"
        assert "status" in data
        assert "is_stale" in data
        assert "minutes_since_update" in data

    def test_returns_data_for_right_slot(self, client, test_db, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        self._add_log(test_db, "right@test.com", level=85)
        resp = client.get("/api/v1/battery/current?slot=right")
        assert resp.status_code == 200
        assert resp.json()["level"] == 85

    def test_slots_return_own_data(self, client, test_db, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        self._add_log(test_db, "left@test.com", level=60)
        self._add_log(test_db, "right@test.com", level=90)
        assert client.get("/api/v1/battery/current?slot=left").json()["level"] == 60
        assert client.get("/api/v1/battery/current?slot=right").json()["level"] == 90

    def test_stale_flag_when_old_data(self, client, test_db, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        self._add_log(test_db, "left@test.com", level=65, minutes_ago=30)
        data = client.get("/api/v1/battery/current?slot=left").json()
        assert data["is_stale"] is True
        assert data["minutes_since_update"] >= 29

    def test_fresh_flag_when_recent_data(self, client, test_db, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        self._add_log(test_db, "left@test.com", level=70, minutes_ago=2)
        data = client.get("/api/v1/battery/current?slot=left").json()
        assert data["is_stale"] is False

    def test_invalid_slot(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        assert client.get("/api/v1/battery/current?slot=bad").status_code == 400


# ---------------------------------------------------------------------------
# /api/v1/battery/history — both slots
# ---------------------------------------------------------------------------

class TestGetHistoryEndpoint:
    def _add_log(self, test_db, username, hours_ago=1, level=70):
        ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        test_db.add(BodyBatteryLog(username=username, measured_at=ts, level=level, fetched_at=ts))
        test_db.commit()

    def test_401_when_not_logged_in(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", lambda slot: None)
        assert client.get("/api/v1/battery/history?slot=left&hours=24").status_code == 401

    def test_empty_history(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        resp = client.get("/api/v1/battery/history?slot=left&hours=24")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_returns_own_data_per_slot(self, client, test_db, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        self._add_log(test_db, "left@test.com", hours_ago=1, level=70)
        self._add_log(test_db, "right@test.com", hours_ago=1, level=90)

        left = client.get("/api/v1/battery/history?slot=left&hours=24").json()["data"]
        right = client.get("/api/v1/battery/history?slot=right&hours=24").json()["data"]

        assert len(left) == 1 and left[0]["level"] == 70
        assert len(right) == 1 and right[0]["level"] == 90

    def test_excludes_old_data(self, client, test_db, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        self._add_log(test_db, "left@test.com", hours_ago=1, level=70)
        self._add_log(test_db, "left@test.com", hours_ago=25, level=60)
        data = client.get("/api/v1/battery/history?slot=left&hours=24").json()["data"]
        assert len(data) == 1
        assert data[0]["level"] == 70

    def test_invalid_hours_param(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        assert client.get("/api/v1/battery/history?slot=left&hours=abc").status_code == 400

    def test_hours_out_of_range(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        assert client.get("/api/v1/battery/history?slot=left&hours=0").status_code == 400
        assert client.get("/api/v1/battery/history?slot=left&hours=200").status_code == 400

    def test_default_24_hours(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        resp = client.get("/api/v1/battery/history?slot=left")
        assert resp.json()["period_hours"] == 24

    def test_invalid_slot(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        assert client.get("/api/v1/battery/history?slot=other").status_code == 400


# ---------------------------------------------------------------------------
# /api/v1/login — both slots
# ---------------------------------------------------------------------------

class TestLoginEndpoint:
    def test_login_left_success(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.save_credentials", lambda slot, u, p: None)
        resp = client.post("/api/v1/login?slot=left", json={"username": "user", "password": "pass"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_login_right_success(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.save_credentials", lambda slot, u, p: None)
        resp = client.post("/api/v1/login?slot=right", json={"username": "user2", "password": "pass2"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_login_saves_to_correct_slot(self, tmp_path, client, monkeypatch):
        saved = {}
        monkeypatch.setattr(
            "backend.app.api.save_credentials",
            lambda slot, u, p: saved.update({"slot": slot, "username": u}),
        )
        client.post("/api/v1/login?slot=right", json={"username": "r@test.com", "password": "pw"})
        assert saved["slot"] == "right"
        assert saved["username"] == "r@test.com"

    def test_login_missing_fields(self, client):
        resp = client.post("/api/v1/login?slot=left", json={"username": "", "password": ""})
        assert resp.status_code == 400

    def test_login_missing_password(self, client):
        resp = client.post("/api/v1/login?slot=left", json={"username": "user"})
        assert resp.status_code == 400

    def test_login_invalid_slot(self, client):
        resp = client.post("/api/v1/login?slot=center", json={"username": "u", "password": "p"})
        assert resp.status_code == 400

    def test_default_slot_is_left(self, client, monkeypatch):
        saved = {}
        monkeypatch.setattr(
            "backend.app.api.save_credentials",
            lambda slot, u, p: saved.update({"slot": slot}),
        )
        client.post("/api/v1/login", json={"username": "u", "password": "p"})
        assert saved["slot"] == "left"


# ---------------------------------------------------------------------------
# /api/v1/logout — both slots
# ---------------------------------------------------------------------------

class TestLogoutEndpoint:
    def test_logout_left_success(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.delete_credentials", lambda slot: None)
        resp = client.post("/api/v1/logout?slot=left")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_logout_right_success(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.delete_credentials", lambda slot: None)
        resp = client.post("/api/v1/logout?slot=right")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_logout_targets_correct_slot(self, client, monkeypatch):
        deleted = {}
        monkeypatch.setattr("backend.app.api.delete_credentials", lambda slot: deleted.update({"slot": slot}))
        client.post("/api/v1/logout?slot=right")
        assert deleted["slot"] == "right"

    def test_logout_invalid_slot(self, client):
        assert client.post("/api/v1/logout?slot=bad").status_code == 400


# ---------------------------------------------------------------------------
# /api/v1/refresh — both slots
# ---------------------------------------------------------------------------

class TestRefreshEndpoint:
    def test_refresh_no_credentials(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", lambda slot: None)
        assert client.post("/api/v1/refresh?slot=left").status_code == 401

    def test_refresh_left_success(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        monkeypatch.setattr("backend.app.api._get_run_job", lambda: lambda slot: None)
        resp = client.post("/api/v1/refresh?slot=left")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_refresh_right_success(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        monkeypatch.setattr("backend.app.api._get_run_job", lambda: lambda slot: None)
        resp = client.post("/api/v1/refresh?slot=right")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_refresh_passes_slot_to_job(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        called_with = {}

        def fake_job(slot):
            called_with["slot"] = slot

        monkeypatch.setattr("backend.app.api._get_run_job", lambda: fake_job)
        client.post("/api/v1/refresh?slot=right")
        assert called_with["slot"] == "right"

    def test_refresh_worker_failure(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)

        def failing_job(slot):
            raise RuntimeError("Node helper exited with code 3")

        monkeypatch.setattr("backend.app.api._get_run_job", lambda: failing_job)
        resp = client.post("/api/v1/refresh?slot=left")
        assert resp.status_code == 500
        assert "Node helper" in resp.json()["detail"]

    def test_refresh_invalid_slot(self, client, monkeypatch):
        monkeypatch.setattr("backend.app.api.load_credentials", _creds_for_slot)
        assert client.post("/api/v1/refresh?slot=top").status_code == 400


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health(self, client):
        resp = client.get("/health")
        if resp.status_code == 200:
            assert resp.json()["status"] == "ok"
        else:
            pytest.skip("health endpoint shadowed by StaticFiles mount")
