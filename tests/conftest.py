"""Shared fixtures for all tests."""

import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Force in-memory DB before importing any app modules
os.environ["DATABASE_URL"] = "sqlite://"

from backend.app.database import Base
from backend.app.models import BodyBatteryLog


@pytest.fixture()
def db_engine():
    """Create a fresh in-memory SQLite engine for each test."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    """Provide a transactional DB session that rolls back after each test."""
    TestSession = sessionmaker(bind=db_engine)
    session = TestSession()
    yield session
    session.close()


@pytest.fixture()
def tmp_credentials(tmp_path):
    """Provide a temporary credentials file path."""
    return tmp_path / "credentials.json"


@pytest.fixture()
def tmp_token_dir(tmp_path):
    """Provide a temporary token directory."""
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    return token_dir
