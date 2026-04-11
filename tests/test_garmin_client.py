"""Tests for worker/garmin_client.py — NodeGarminClient with mocked subprocess."""

import json
import os
import subprocess
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from worker.garmin_client import NodeGarminClient, NODE_HELPER_SCRIPT, NODE_HELPER_DIR


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def skip_if_no_script():
    if not os.path.exists(NODE_HELPER_SCRIPT):
        pytest.skip("Node helper script not found (expected in worker_node/)")


def _make_result_file(data, token_dir=None):
    """Write _result.json where the client expects it."""
    if token_dir is None:
        token_dir = os.path.join(NODE_HELPER_DIR, "tokens")
    os.makedirs(token_dir, exist_ok=True)
    result_path = os.path.join(token_dir, "_result.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return result_path


def _ok_run():
    return MagicMock(returncode=0, stdout="", stderr="[node] done")


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestNodeGarminClientInit:
    def test_raises_when_script_missing(self, monkeypatch):
        monkeypatch.setattr("worker.garmin_client.NODE_HELPER_SCRIPT", "/nonexistent/script.js")
        with pytest.raises(FileNotFoundError, match="Node helper not found"):
            NodeGarminClient(username="user", password="pass")

    def test_init_stores_credentials(self):
        client = NodeGarminClient(username="user@test.com", password="secret")
        assert client.username == "user@test.com"
        assert client.password == "secret"

    def test_init_stores_token_dir(self, tmp_path):
        client = NodeGarminClient(username="u", password="p", token_dir=str(tmp_path))
        assert client.token_dir == str(tmp_path)

    def test_init_default_token_dir_is_empty(self):
        client = NodeGarminClient(username="u", password="p")
        assert client.token_dir == ""


# ---------------------------------------------------------------------------
# login (no-op)
# ---------------------------------------------------------------------------

class TestNodeGarminClientLogin:
    def test_login_is_noop(self):
        client = NodeGarminClient(username="user", password="pass")
        client.login()  # must not raise


# ---------------------------------------------------------------------------
# get_heart_rate — default token dir
# ---------------------------------------------------------------------------

class TestGetHeartRateDefault:
    @patch("worker.garmin_client.subprocess.run")
    def test_successful_fetch(self, mock_run):
        now = datetime.now(timezone.utc)
        _make_result_file({"profile_name": "TestUser", "entries": [
            {"measured_at": now.isoformat(), "level": 72},
        ]})
        mock_run.return_value = _ok_run()
        client = NodeGarminClient(username="user", password="pass")
        result = client.get_heart_rate(now - timedelta(hours=1), now)
        assert result["profile_name"] == "TestUser"
        assert len(result["entries"]) == 1
        assert result["entries"][0]["level"] == 72

    @patch("worker.garmin_client.subprocess.run")
    def test_nonzero_exit_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=3, stdout="", stderr="LOGIN_FAILED")
        client = NodeGarminClient(username="user", password="pass")
        now = datetime.now(timezone.utc)
        with pytest.raises(RuntimeError, match="Node helper exited with code 3"):
            client.get_heart_rate(now - timedelta(hours=1), now)

    @patch("worker.garmin_client.subprocess.run")
    def test_timeout_raises(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="node", timeout=120)
        client = NodeGarminClient(username="user", password="pass")
        now = datetime.now(timezone.utc)
        with pytest.raises(subprocess.TimeoutExpired):
            client.get_heart_rate(now - timedelta(hours=1), now)

    @patch("worker.garmin_client.subprocess.run")
    def test_missing_result_file_raises(self, mock_run):
        default_result = os.path.join(NODE_HELPER_DIR, "tokens", "_result.json")
        if os.path.exists(default_result):
            os.remove(default_result)
        mock_run.return_value = _ok_run()
        client = NodeGarminClient(username="user", password="pass")
        now = datetime.now(timezone.utc)
        with pytest.raises(FileNotFoundError):
            client.get_heart_rate(now - timedelta(hours=1), now)

    @patch("worker.garmin_client.subprocess.run")
    def test_result_file_cleaned_up_after_read(self, mock_run):
        now = datetime.now(timezone.utc)
        result_path = _make_result_file({"profile_name": None, "entries": []})
        mock_run.return_value = _ok_run()
        NodeGarminClient(username="user", password="pass").get_heart_rate(now - timedelta(hours=1), now)
        assert not os.path.exists(result_path)

    @patch("worker.garmin_client.subprocess.run")
    def test_credentials_passed_via_env(self, mock_run):
        now = datetime.now(timezone.utc)
        _make_result_file({"profile_name": None, "entries": []})
        mock_run.return_value = _ok_run()
        NodeGarminClient(username="testuser@gmail.com", password="secret123").get_heart_rate(
            now - timedelta(hours=1), now
        )
        env = mock_run.call_args.kwargs.get("env") or mock_run.call_args[1].get("env")
        assert env["GARMIN_USERNAME"] == "testuser@gmail.com"
        assert env["GARMIN_PASSWORD"] == "secret123"

    @patch("worker.garmin_client.subprocess.run")
    def test_parses_z_suffix_dates(self, mock_run):
        now = datetime.now(timezone.utc)
        _make_result_file({"profile_name": "U", "entries": [
            {"measured_at": "2026-04-09T10:00:00Z", "level": 65},
        ]})
        mock_run.return_value = _ok_run()
        result = NodeGarminClient(username="u", password="p").get_heart_rate(now - timedelta(hours=1), now)
        entry = result["entries"][0]
        assert entry["measured_at"].tzinfo is not None
        assert entry["level"] == 65

    @patch("worker.garmin_client.subprocess.run")
    def test_battery_level_parsed(self, mock_run):
        now = datetime.now(timezone.utc)
        _make_result_file({"profile_name": "U", "entries": [
            {"measured_at": now.isoformat(), "level": 70, "battery_level": 55},
        ]})
        mock_run.return_value = _ok_run()
        result = NodeGarminClient(username="u", password="p").get_heart_rate(now - timedelta(hours=1), now)
        assert result["entries"][0]["battery_level"] == 55

    @patch("worker.garmin_client.subprocess.run")
    def test_null_battery_level_allowed(self, mock_run):
        now = datetime.now(timezone.utc)
        _make_result_file({"profile_name": "U", "entries": [
            {"measured_at": now.isoformat(), "level": 70, "battery_level": None},
        ]})
        mock_run.return_value = _ok_run()
        result = NodeGarminClient(username="u", password="p").get_heart_rate(now - timedelta(hours=1), now)
        assert result["entries"][0]["battery_level"] is None


# ---------------------------------------------------------------------------
# get_heart_rate — custom token_dir (slot isolation)
# ---------------------------------------------------------------------------

class TestGetHeartRateWithTokenDir:
    @patch("worker.garmin_client.subprocess.run")
    def test_token_dir_env_set(self, mock_run, tmp_path):
        now = datetime.now(timezone.utc)
        custom_dir = str(tmp_path / "left")
        _make_result_file({"profile_name": None, "entries": []}, token_dir=custom_dir)
        mock_run.return_value = _ok_run()
        NodeGarminClient(username="u", password="p", token_dir=custom_dir).get_heart_rate(
            now - timedelta(hours=1), now
        )
        env = mock_run.call_args.kwargs.get("env") or mock_run.call_args[1].get("env")
        assert env["GARMIN_TOKEN_DIR"] == custom_dir

    @patch("worker.garmin_client.subprocess.run")
    def test_result_read_from_custom_dir(self, mock_run, tmp_path):
        now = datetime.now(timezone.utc)
        custom_dir = str(tmp_path / "right")
        _make_result_file(
            {"profile_name": "RightUser", "entries": [{"measured_at": now.isoformat(), "level": 88}]},
            token_dir=custom_dir,
        )
        mock_run.return_value = _ok_run()
        result = NodeGarminClient(username="u", password="p", token_dir=custom_dir).get_heart_rate(
            now - timedelta(hours=1), now
        )
        assert result["profile_name"] == "RightUser"
        assert result["entries"][0]["level"] == 88

    @patch("worker.garmin_client.subprocess.run")
    def test_result_file_cleaned_up_from_custom_dir(self, mock_run, tmp_path):
        now = datetime.now(timezone.utc)
        custom_dir = str(tmp_path / "left")
        result_path = _make_result_file({"profile_name": None, "entries": []}, token_dir=custom_dir)
        mock_run.return_value = _ok_run()
        NodeGarminClient(username="u", password="p", token_dir=custom_dir).get_heart_rate(
            now - timedelta(hours=1), now
        )
        assert not os.path.exists(result_path)

    @patch("worker.garmin_client.subprocess.run")
    def test_no_token_dir_env_when_not_set(self, mock_run):
        now = datetime.now(timezone.utc)
        _make_result_file({"profile_name": None, "entries": []})
        mock_run.return_value = _ok_run()
        NodeGarminClient(username="u", password="p").get_heart_rate(now - timedelta(hours=1), now)
        env = mock_run.call_args.kwargs.get("env") or mock_run.call_args[1].get("env")
        assert "GARMIN_TOKEN_DIR" not in env
