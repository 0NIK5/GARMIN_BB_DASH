import os
import json
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from .database import get_db
from .crud import get_latest_log, get_history
from .schemas import BatteryCurrent, BatteryHistory, ConfigResponse, LoginRequest, LoginResponse, LogoutResponse

router = APIRouter(prefix="/api/v1")

# Порог устаревания данных (heart rate опрашивается каждые 5 минут)
STALE_THRESHOLD_MINUTES = 15

CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "credentials.json")


def load_credentials():
    if os.path.exists(CREDENTIALS_FILE):
        with open(CREDENTIALS_FILE, "r") as f:
            return json.load(f)
    return None


def save_credentials(username, password):
    os.makedirs(os.path.dirname(CREDENTIALS_FILE), exist_ok=True)
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump({"username": username, "password": password}, f)


def delete_credentials():
    if os.path.exists(CREDENTIALS_FILE):
        os.remove(CREDENTIALS_FILE)


router = APIRouter(prefix="/api/v1")

# Порог устаревания данных (heart rate опрашивается каждые 5 минут)
STALE_THRESHOLD_MINUTES = 15



def compute_status(records):
    if len(records) < 2:
        return "unknown"

    last_three = records[-3:]
    levels = [record.level for record in last_three]
    if levels[-1] < levels[0]:
        return "decreasing"
    if levels[-1] > levels[0]:
        return "increasing"
    return "stable"


def _ensure_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@router.get("/battery/current", response_model=BatteryCurrent)
def get_current(db: Session = Depends(get_db)):
    current = get_latest_log(db)
    if current is None:
        raise HTTPException(status_code=404, detail="No heart rate data available")

    history = get_history(db, hours=3)
    status = compute_status(history)
    measured_at_utc = _ensure_utc(current.measured_at)
    minutes_since_update = int((datetime.now(timezone.utc) - measured_at_utc).total_seconds() // 60)
    is_stale = minutes_since_update > STALE_THRESHOLD_MINUTES

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


@router.get("/config", response_model=ConfigResponse)
def get_config():
    """Return application configuration including username"""
    creds = load_credentials()
    username = creds["username"] if creds else "Not logged in"
    return {"username": username}


@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest):
    """Login with Garmin credentials"""
    save_credentials(request.username, request.password)
    return {"success": True, "message": "Logged in successfully"}


@router.post("/logout", response_model=LogoutResponse)
def logout():
    """Logout and clear credentials"""
    delete_credentials()
    return {"success": True, "message": "Logged out successfully"}


