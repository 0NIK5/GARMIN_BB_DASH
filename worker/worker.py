import os
import time
from datetime import datetime, timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, declarative_base
from sqlalchemy import Column, Integer, DateTime, SmallInteger
from garmin_client import GarminClient

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/body_battery.db")
Base = declarative_base()


class BodyBatteryLog(Base):
    __tablename__ = "body_battery_logs"
    id = Column(Integer, primary_key=True, index=True)
    measured_at = Column(DateTime(timezone=True), unique=True, index=True, nullable=False)
    level = Column(SmallInteger, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False)


def get_engine():
    return create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def get_last_timestamp(db: Session):
    stmt = select(BodyBatteryLog).order_by(BodyBatteryLog.measured_at.desc()).limit(1)
    record = db.scalars(stmt).first()
    return record.measured_at if record else None


def upsert_entries(db: Session, entries):
    for item in entries:
        stmt = select(BodyBatteryLog).where(BodyBatteryLog.measured_at == item["measured_at"])
        existing = db.scalars(stmt).first()
        if existing is None:
            db.add(BodyBatteryLog(
                measured_at=item["measured_at"],
                level=item["level"],
                fetched_at=datetime.utcnow(),
            ))
    db.commit()


def run_job():
    username = os.getenv("GARMIN_USERNAME", "")
    password = os.getenv("GARMIN_PASSWORD", "")
    client = GarminClient(username=username, password=password)
    if client.session is None:
        client.login()

    engine = get_engine()
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        last_ts = get_last_timestamp(db)
        start = (last_ts - timedelta(hours=1)) if last_ts else datetime.utcnow() - timedelta(hours=24)
        end = datetime.utcnow()
        entries = client.get_body_battery(start, end)
        upsert_entries(db, entries)
        print(f"Job completed: fetched {len(entries)} records")


if __name__ == "__main__":
    run_job()
