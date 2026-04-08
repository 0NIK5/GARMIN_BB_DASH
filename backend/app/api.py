from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from .database import get_db
from .crud import get_latest_log, get_history
from .schemas import BatteryCurrent, BatteryHistory

router = APIRouter(prefix="/api/v1")


def compute_status(records):
    if len(records) < 2:
        return "unknown"

    last_three = records[-3:]
    levels = [record.level for record in last_three]
    if levels[-1] < levels[0]:
        return "draining"
    if levels[-1] > levels[0]:
        return "recharging"
    return "stable"


@router.get("/battery/current", response_model=BatteryCurrent)
def get_current(db: Session = Depends(get_db)):
    current = get_latest_log(db)
    if current is None:
        raise HTTPException(status_code=404, detail="No battery data available")

    history = get_history(db, hours=3)
    status = compute_status(history)
    minutes_since_update = int((datetime.utcnow() - current.measured_at.replace(tzinfo=None)).total_seconds() // 60)
    is_stale = minutes_since_update > 90

    return {
        "timestamp": current.measured_at,
        "level": current.level,
        "status": status,
        "minutes_since_update": minutes_since_update,
        "is_stale": is_stale,
    }


@router.get("/battery/history", response_model=BatteryHistory)
def get_history_endpoint(hours: int = Query(24, ge=1, le=168), db: Session = Depends(get_db)):
    rows = get_history(db, hours)
    return {
        "period_hours": hours,
        "data": [{"time": row.measured_at, "level": row.level} for row in rows],
    }
