import os
import sys
import json
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from .database import get_db
from .crud import get_latest_log, get_history
from .schemas import BatteryCurrent, BatteryHistory, ConfigResponse, LoginResponse, LogoutResponse

logger = logging.getLogger(__name__)

# Allow importing the worker package from the repository root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

def _get_run_job():
    try:
        from worker.worker import run_job
        return run_job
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to import refresh worker: {exc}")

router = APIRouter(prefix="/api/v1")

# Порог устаревания данных (heart rate опрашивается каждые 5 минут)
STALE_THRESHOLD_MINUTES = 15

CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "credentials.json")
TOKEN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "worker_node", "tokens"))


def load_credentials():
    if os.path.exists(CREDENTIALS_FILE):
        with open(CREDENTIALS_FILE, "r") as f:
            return json.load(f)
    return None


def clear_saved_tokens():
    """Remove cached OAuth tokens so the next refresh does a fresh login."""
    if os.path.isdir(TOKEN_DIR):
        for fname in os.listdir(TOKEN_DIR):
            fpath = os.path.join(TOKEN_DIR, fname)
            if os.path.isfile(fpath):
                os.remove(fpath)
                logger.info("Removed stale token file: %s", fname)


def save_credentials(username, password):
    os.makedirs(os.path.dirname(CREDENTIALS_FILE), exist_ok=True)
    clear_saved_tokens()
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump({"username": username, "password": password}, f)


def delete_credentials():
    if os.path.exists(CREDENTIALS_FILE):
        os.remove(CREDENTIALS_FILE)
    clear_saved_tokens()



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


@router.get("/battery/current")
def get_current(db: Session = Depends(get_db)):
    current = get_latest_log(db)
    if current is None:
        raise HTTPException(status_code=404, detail="No heart rate data available")

    history = get_history(db, hours=3)
    status = compute_status(history)
    measured_at_utc = _ensure_utc(current.measured_at)
    minutes_since_update = int((datetime.now(timezone.utc) - measured_at_utc).total_seconds() // 60)
    is_stale = minutes_since_update > STALE_THRESHOLD_MINUTES

    return jsonable_encoder({
        "timestamp": current.measured_at,
        "level": current.level,
        "status": status,
        "minutes_since_update": minutes_since_update,
        "is_stale": is_stale,
        "profile_name": getattr(current, "profile_name", None),
    })


@router.get("/battery/history")
def get_history_endpoint(request: Request, db: Session = Depends(get_db)):
    hours_param = request.query_params.get("hours", "24")
    try:
        hours = int(hours_param)
    except ValueError:
        raise HTTPException(status_code=400, detail="hours must be an integer")
    if hours < 1 or hours > 168:
        raise HTTPException(status_code=400, detail="hours must be between 1 and 168")

    rows = get_history(db, hours)
    return jsonable_encoder({
        "period_hours": hours,
        "data": [{"time": row.measured_at, "level": row.level} for row in rows],
    })


@router.get("/config")
def get_config():
    """Return application configuration including username"""
    creds = load_credentials()
    username = creds["username"] if creds else "Not logged in"
    return {"username": username}


@router.post("/login")
async def login(request: Request):
    """Login with Garmin credentials"""
    payload = await request.json()
    username = payload.get("username")
    password = payload.get("password")
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required")
    save_credentials(username, password)
    logger.info(f"User '{username}' logged in successfully. Credentials saved to file.")
    return {"success": True, "message": "Logged in successfully"}


@router.post("/logout")
def logout():
    """Logout and clear credentials"""
    delete_credentials()
    logger.info("User logged out. Credentials cleared.")
    return {"success": True, "message": "Logged out successfully"}


@router.post("/refresh")
def refresh_data():
    """Run an immediate heart rate refresh and return when it completes."""
    # Проверяем, что кредентайлы сохранены перед запуском refresh
    creds = load_credentials()
    if not creds:
        logger.warning("Refresh requested but no credentials found")
        raise HTTPException(status_code=401, detail="Not logged in. Please login first.")

    try:
        logger.info(f"Starting immediate refresh for user '{creds.get('username')}'")
        run_job = _get_run_job()
        run_job()
        logger.info("Refresh completed successfully")
        return {"success": True, "message": "Refresh completed"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Refresh failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


