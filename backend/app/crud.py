from datetime import datetime, timedelta
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import select
from .models import BodyBatteryLog


def get_latest_log(db: Session):
    stmt = select(BodyBatteryLog).order_by(BodyBatteryLog.measured_at.desc()).limit(1)
    return db.scalars(stmt).first()


def get_history(db: Session, hours: int):
    threshold = datetime.utcnow() - timedelta(hours=hours)
    stmt = select(BodyBatteryLog).where(BodyBatteryLog.measured_at >= threshold).order_by(BodyBatteryLog.measured_at.asc())
    return db.scalars(stmt).all()


def upsert_logs(db: Session, entries: List[dict]):
    for entry in entries:
        stmt = select(BodyBatteryLog).where(BodyBatteryLog.measured_at == entry["measured_at"])
        existing = db.scalars(stmt).first()
        if existing is None:
            db.add(BodyBatteryLog(**entry))
    db.commit()
