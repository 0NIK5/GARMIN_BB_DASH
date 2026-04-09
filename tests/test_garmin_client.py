"""Tests for worker/garmin_client.py — NodeGarminClient with mocked subprocess."""

import json
import os
import subprocess
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from worker.garmin_client import NodeGarminClient, NODE_HELPER_SCRIPT, NODE_HELPER_DIR


# ---------------------------------------------------------------------------
# NodeGarminClient.__init__
# ---------------------------------------------------------------------------


class TestNodeGarminClientInit:
    def test_raises_when_script_missing(self, monkeypatch):
        monkeypatch.setattr("worker.garmin_client.NODE_HELPER_SCRIPT", "/nonexistent/script.js")
        with pytest.raises(FileNotFoundError, match="Node helper not found"):
            NodeGarminClient(username="user", password="pass")

    def test_init_success(self):
        # NODE_HELPER_SCRIPT should exist in the repo
        if not os.path.exists(NODE_HELPER_SCRIPT):
            pytest.skip("Node helper script not found (expected in worker_node/)")
        client = NodeGarminClient(username="user", password="pass")
        assert client.username == "user"
        assert client.password == "pass"


# ---------------------------------------------------------------------------
# NodeGarminClient.login
# ---------------------------------------------------------------------------


class TestNodeGarminClientLogin:
    def test_login_is_noop(self):
        if not os.path.exists(NODE_HELPER_SCRIPT):
            pytest.skip("Node helper script not found")
        client = NodeGarminClient(username="user", password="pass")
        # login() should not raise and is a no-op
        client.login()


# ---------------------------------------------------------------------------
# NodeGarminClient.get_heart_rate
# ---------------------------------------------------------------------------


class TestNodeGarminClientGetHeartRate:
    @pytest.fixture(autouse=True)
    def _skip_if_no_script(self):
        if not os.path.exists(NODE_HELPER_SCRIPT):
            pytest.skip("Node helper script not found")

    def _make_result_file(self, data):
        """Write a _result.json file that the client will read."""
        result_path = os.path.join(NODE_HELPER_DIR, "tokens", "_result.json")
        os.makedirs(os.path.dirname(result_path), exist_ok=True)
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    @patch("worker.garmin_client.subprocess.run")
    def test_successful_fetch(self, mock_run):
        now = datetime.now(timezone.utc)
        result_data = {
            "profile_name": "TestUser",
            "entries": [
                {"measured_at": now.isoformat(), "level": 72},
            ],
        }
        self._make_result_file(result_data)

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="[node] Fetched 1 heart rate points",
        )

        client = NodeGarminClient(username="user", password="pass")
        result = client.get_heart_rate(now - timedelta(hours=1), now)

        assert result["profile_name"] == "TestUser"
        assert len(result["entries"]) == 1
        assert result["entries"][0]["level"] == 72
        mock_run.assert_called_once()

    @patch("worker.garmin_client.subprocess.run")
    def test_nonzero_exit_code_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=3,
            stdout="",
            stderr="LOGIN_FAILED: some error",
        )

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
        # Don't create _result.json
        result_path = os.path.join(NODE_HELPER_DIR, "tokens", "_result.json")
        if os.path.exists(result_path):
            os.remove(result_path)

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )

        client = NodeGarminClient(username="user", password="pass")
        now = datetime.now(timezone.utc)

        with pytest.raises(FileNotFoundError):
            client.get_heart_rate(now - timedelta(hours=1), now)

    @patch("worker.garmin_client.subprocess.run")
    def test_result_file_cleaned_up_after_read(self, mock_run):
        now = datetime.now(timezone.utc)
        self._make_result_file({"profile_name": None, "entries": []})

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        client = NodeGarminClient(username="user", password="pass")
        client.get_heart_rate(now - timedelta(hours=1), now)

        result_path = os.path.join(NODE_HELPER_DIR, "tokens", "_result.json")
        assert not os.path.exists(result_path)

    @patch("worker.garmin_client.subprocess.run")
    def test_env_credentials_passed(self, mock_run):
        now = datetime.now(timezone.utc)
        self._make_result_file({"profile_name": None, "entries": []})

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        client = NodeGarminClient(username="testuser@gmail.com", password="secret123")
        client.get_heart_rate(now - timedelta(hours=1), now)

        call_kwargs = mock_run.call_args
        env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env")
        assert env["GARMIN_USERNAME"] == "testuser@gmail.com"
        assert env["GARMIN_PASSWORD"] == "secret123"

    @patch("worker.garmin_client.subprocess.run")
    def test_parses_z_suffix_dates(self, mock_run):
        now = datetime.now(timezone.utc)
        self._make_result_file({
            "profile_name": "User",
            "entries": [
                {"measured_at": "2026-04-09T10:00:00Z", "level": 65},
            ],
        })

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        client = NodeGarminClient(username="user", password="pass")
        result = client.get_heart_rate(now - timedelta(hours=1), now)

        assert result["entries"][0]["measured_at"].tzinfo is not None
        assert result["entries"][0]["level"] == 65
