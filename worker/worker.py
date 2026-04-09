import logging
import os
import signal
import sys
import json
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import Column, DateTime, Integer, SmallInteger, String, create_engine, inspect, select, text
from sqlalchemy.orm import Session, declarative_base

from .garmin_client import NodeGarminClient

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
CREDENTIALS_FILE = os.path.join(_PROJECT_ROOT, "backend", "data", "credentials.json")


def load_credentials():
    if os.path.exists(CREDENTIALS_FILE):
        with open(CREDENTIALS_FILE, "r") as f:
            return json.load(f)
    return None



Base = declarative_base()


def _ensure_column(engine, col_name, col_type_sql):
    inspector = inspect(engine)
    if not inspector.has_table("body_battery_logs"):
        return
    columns = [c["name"] for c in inspector.get_columns("body_battery_logs")]
    if col_name not in columns:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE body_battery_logs ADD COLUMN {col_name} {col_type_sql}"))


class BodyBatteryLog(Base):
    __tablename__ = "body_battery_logs"
    id = Column(Integer, primary_key=True, index=True)
    measured_at = Column(DateTime(timezone=True), unique=True, index=True, nullable=False)
    level = Column(SmallInteger, nullable=False)
    battery_level = Column(SmallInteger, nullable=True)
    fetched_at = Column(DateTime(timezone=True), nullable=False)
    profile_name = Column(String, nullable=True)


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


def upsert_entries(db: Session, entries, profile_name=None) -> int:
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
                    battery_level=item.get("battery_level"),
                    fetched_at=now,
                    profile_name=profile_name,
                )
            )
            inserted += 1
        else:
            # Обновляем battery_level если появилось новое значение
            if item.get("battery_level") is not None and existing.battery_level != item["battery_level"]:
                existing.battery_level = item["battery_level"]
            if profile_name and existing.profile_name != profile_name:
                existing.profile_name = profile_name
    db.commit()
    return inserted


def run_job():
    logger.info("=== Job started (using Node.js helper) ===")

    creds = load_credentials()
    if not creds:
        logger.warning("No credentials found. Please login via the web interface.")
        return

    if not creds.get("username") or not creds.get("password"):
        logger.warning("Invalid credentials: username and password must be provided.")
        return

    username = creds["username"]
    password = creds["password"]

    client = NodeGarminClient(username=username, password=password)

    try:
        client.login()

        engine = get_engine()
        Base.metadata.create_all(bind=engine)
        _ensure_column(engine, "profile_name", "TEXT")
        _ensure_column(engine, "battery_level", "SMALLINT")

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
            payload = client.get_heart_rate(start, now)
            profile_name = payload.get("profile_name")
            entries = payload.get("entries", [])
            inserted = upsert_entries(db, entries, profile_name=profile_name)
            # Update profile_name on the latest record even if no new entries were inserted
            if profile_name:
                latest = db.scalars(
                    select(BodyBatteryLog).order_by(BodyBatteryLog.measured_at.desc()).limit(1)
                ).first()
                if latest and latest.profile_name != profile_name:
                    latest.profile_name = profile_name
                    db.commit()
            logger.info("Job completed: fetched=%d, inserted=%d", len(entries), inserted)
    except Exception as exc:
        logger.exception("Job failed: %s", exc)
        raise


def main():
    logger.info("Worker starting. Poll interval: %d minutes", POLL_MINUTES)

    # Запускаем сразу один раз при старте (если есть кредентайлы)
    creds = load_credentials()
    if not creds:
        logger.warning("No credentials found on startup. Waiting for user to login via web interface.")
    else:
        logger.info(f"Found credentials for user '{creds.get('username')}'. Starting initial job run.")
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
