import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import Column, DateTime, Integer, SmallInteger, create_engine, select
from sqlalchemy.orm import Session, declarative_base

from garmin_client import GarminClient
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("worker")

# Абсолютный путь к общей data/ в корне проекта
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_DEFAULT_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "body_battery.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")
POLL_MINUTES = int(os.getenv("POLL_MINUTES", "5"))  # для отладки heart rate — каждые 5 минут
LOOKBACK_HOURS_INITIAL = int(os.getenv("LOOKBACK_HOURS_INITIAL", "6"))

Base = declarative_base()


class BodyBatteryLog(Base):
    __tablename__ = "body_battery_logs"
    id = Column(Integer, primary_key=True, index=True)
    measured_at = Column(DateTime(timezone=True), unique=True, index=True, nullable=False)
    level = Column(SmallInteger, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False)


def get_engine():
    # Гарантируем существование папки data/
    if DATABASE_URL.startswith("sqlite:///"):
        db_path = DATABASE_URL.replace("sqlite:///", "", 1)
        db_dir = os.path.dirname(os.path.abspath(db_path))
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
    return create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def get_last_timestamp(db: Session):
    stmt = select(BodyBatteryLog).order_by(BodyBatteryLog.measured_at.desc()).limit(1)
    record = db.scalars(stmt).first()
    return record.measured_at if record else None


def upsert_entries(db: Session, entries) -> int:
    inserted = 0
    now = datetime.now(timezone.utc)
    for item in entries:
        stmt = select(BodyBatteryLog).where(BodyBatteryLog.measured_at == item["measured_at"])
        existing = db.scalars(stmt).first()
        if existing is None:
            db.add(
                BodyBatteryLog(
                    measured_at=item["measured_at"],
                    level=item["level"],
                    fetched_at=now,
                )
            )
            inserted += 1
    db.commit()
    return inserted


def fetch_with_retry(client: GarminClient, start: datetime, end: datetime):
    """
    Retry logic согласно спецификации:
      - 3 попытки с exponential backoff (5s, 15s, 30s) для network errors
      - 1 retry при 401/403 (с повторным логином)
    """
    backoffs = [5, 15, 30]
    auth_retry_used = False

    for attempt in range(len(backoffs) + 1):
        try:
            return client.get_heart_rate(start, end)
        except GarminConnectAuthenticationError as exc:
            if auth_retry_used:
                logger.error("Auth retry already used — giving up: %s", exc)
                raise
            auth_retry_used = True
            logger.warning("Auth error, attempting re-login: %s", exc)
            client.login()
        except (GarminConnectConnectionError, ConnectionError, TimeoutError) as exc:
            if attempt >= len(backoffs):
                logger.error("All retries exhausted: %s", exc)
                raise
            wait = backoffs[attempt]
            logger.warning("Network error (attempt %d), retrying in %ds: %s", attempt + 1, wait, exc)
            time.sleep(wait)

    raise RuntimeError("Unreachable")


def run_job():
    logger.info("=== Job started ===")
    username = os.getenv("GARMIN_USERNAME", "")
    password = os.getenv("GARMIN_PASSWORD", "")

    if not username or not password:
        logger.error("GARMIN_USERNAME / GARMIN_PASSWORD не заданы")
        return

    try:
        client = GarminClient(username=username, password=password)
        client.login()

        engine = get_engine()
        Base.metadata.create_all(bind=engine)

        with Session(engine) as db:
            last_ts = get_last_timestamp(db)
            now = datetime.now(timezone.utc)
            if last_ts:
                # last_ts может прийти naive из SQLite → нормализуем
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                start = last_ts - timedelta(minutes=10)
            else:
                start = now - timedelta(hours=LOOKBACK_HOURS_INITIAL)

            logger.info("Fetching range: %s → %s", start.isoformat(), now.isoformat())
            entries = fetch_with_retry(client, start, now)
            inserted = upsert_entries(db, entries)
            logger.info("Job completed: fetched=%d, inserted=%d", len(entries), inserted)
    except Exception as exc:
        logger.exception("Job failed: %s", exc)


def main():
    logger.info("Worker starting. Poll interval: %d minutes", POLL_MINUTES)

    # Запускаем сразу один раз при старте
    run_job()

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_job, "interval", minutes=POLL_MINUTES, id="fetch_heart_rate")

    def shutdown(signum, frame):
        logger.info("Shutdown signal received, stopping scheduler")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    main()
