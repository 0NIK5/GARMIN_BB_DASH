"""Shared fixtures for all tests."""

import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Force in-memory DB before importing any app modules
os.environ["DATABASE_URL"] = "sqlite://"

from backend.app.database import Base
from backend.app.models import BodyBatteryLog


@pytest.fixture()
def db_engine():
    """Fresh in-memory SQLite engine per test."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    """Transactional session that closes after each test."""
    session = sessionmaker(bind=db_engine)()
    yield session
    session.close()


@pytest.fixture()
def tmp_creds_dir(tmp_path):
    """Temp directory that holds per-slot credentials files."""
    return tmp_path / "creds"


@pytest.fixture()
def tmp_token_root(tmp_path):
    """Temp directory root for per-slot token subdirs."""
    root = tmp_path / "tokens"
    root.mkdir()
    return root
