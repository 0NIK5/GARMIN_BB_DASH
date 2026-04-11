import os
import sys
import json
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from .database import get_db
from .crud import get_latest_log, get_history

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

STALE_THRESHOLD_MINUTES = 15

SLOTS = ("left", "right")
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
TOKENS_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "worker_node", "tokens"))


def _slot_from_request(request: Request) -> str:
    slot = request.query_params.get("slot", "left")
    if slot not in SLOTS:
        raise HTTPException(status_code=400, detail=f"slot must be one of {SLOTS}")
    return slot


def _credentials_file(slot: str) -> str:
    return os.path.join(DATA_DIR, f"credentials_{slot}.json")


def _token_dir(slot: str) -> str:
    return os.path.join(TOKENS_ROOT, slot)


def load_credentials(slot: str):
    path = _credentials_file(slot)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return None


def clear_saved_tokens(slot: str):
    """Remove cached OAuth tokens for a slot so the next refresh does a fresh login."""
    tdir = _token_dir(slot)
    if os.path.isdir(tdir):
        for fname in os.listdir(tdir):
            fpath = os.path.join(tdir, fname)
            if os.path.isfile(fpath):
                os.remove(fpath)
                logger.info("Removed token file for slot %s: %s", slot, fname)


def save_credentials(slot: str, username: str, password: str):
    os.makedirs(DATA_DIR, exist_ok=True)
    clear_saved_tokens(slot)
    with open(_credentials_file(slot), "w") as f:
        json.dump({"username": username, "password": password}, f)


def delete_credentials(slot: str):
    path = _credentials_file(slot)
    if os.path.exists(path):
        os.remove(path)
    clear_saved_tokens(slot)


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
def get_current(request: Request, db: Session = Depends(get_db)):
    slot = _slot_from_request(request)
    creds = load_credentials(slot)
    if not creds:
        raise HTTPException(status_code=401, detail="Not logged in. Please login first.")

    username = creds["username"]
    current = get_latest_log(db, username)
    if current is None:
        raise HTTPException(status_code=404, detail="No heart rate data available")

    history = get_history(db, hours=3, username=username)
    status = compute_status(history)
    measured_at_utc = _ensure_utc(current.measured_at)
    minutes_since_update = int((datetime.now(timezone.utc) - measured_at_utc).total_seconds() // 60)
    is_stale = minutes_since_update > STALE_THRESHOLD_MINUTES

    return jsonable_encoder({
        "timestamp": current.measured_at,
        "level": current.level,
        "battery_level": getattr(current, "battery_level", None),
        "status": status,
        "minutes_since_update": minutes_since_update,
        "is_stale": is_stale,
        "profile_name": getattr(current, "profile_name", None),
    })


@router.get("/battery/history")
def get_history_endpoint(request: Request, db: Session = Depends(get_db)):
    slot = _slot_from_request(request)
    creds = load_credentials(slot)
    if not creds:
        raise HTTPException(status_code=401, detail="Not logged in. Please login first.")

    username = creds["username"]
    hours_param = request.query_params.get("hours", "24")
    try:
        hours = int(hours_param)
    except ValueError:
        raise HTTPException(status_code=400, detail="hours must be an integer")
    if hours < 1 or hours > 168:
        raise HTTPException(status_code=400, detail="hours must be between 1 and 168")

    rows = get_history(db, hours, username)
    return jsonable_encoder({
        "period_hours": hours,
        "data": [{"time": row.measured_at, "level": row.level, "battery_level": getattr(row, "battery_level", None)} for row in rows],
    })


@router.get("/config")
def get_config(request: Request):
    """Return application configuration including username for the given slot"""
    slot = _slot_from_request(request)
    creds = load_credentials(slot)
    username = creds["username"] if creds else "Not logged in"
    return {"slot": slot, "username": username}


@router.post("/login")
async def login(request: Request):
    """Login with Garmin credentials for the given slot"""
    slot = _slot_from_request(request)
    payload = await request.json()
    username = payload.get("username")
    password = payload.get("password")
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required")
    save_credentials(slot, username, password)
    logger.info("User '%s' logged in to slot '%s'.", username, slot)
    return {"success": True, "message": "Logged in successfully"}


@router.post("/logout")
def logout(request: Request):
    """Logout and clear credentials for the given slot"""
    slot = _slot_from_request(request)
    delete_credentials(slot)
    logger.info("Slot '%s' logged out.", slot)
    return {"success": True, "message": "Logged out successfully"}


@router.post("/refresh")
def refresh_data(request: Request):
    """Run an immediate heart rate refresh for the given slot."""
    slot = _slot_from_request(request)
    creds = load_credentials(slot)
    if not creds:
        logger.warning("Refresh requested for slot '%s' but no credentials found", slot)
        raise HTTPException(status_code=401, detail="Not logged in. Please login first.")

    try:
        logger.info("Starting immediate refresh for slot '%s' (user '%s')", slot, creds.get("username"))
        run_job = _get_run_job()
        run_job(slot)
        logger.info("Refresh for slot '%s' completed", slot)
        return {"success": True, "message": "Refresh completed"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Refresh for slot '%s' failed: %s", slot, exc)
        raise HTTPException(status_code=500, detail=str(exc))
