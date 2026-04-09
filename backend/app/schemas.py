from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class BatteryPoint(BaseModel):
    time: datetime
    level: int
    battery_level: Optional[int] = None

    model_config = {"from_attributes": True}


class BatteryCurrent(BaseModel):
    timestamp: datetime
    level: int
    battery_level: Optional[int] = None
    status: str
    minutes_since_update: int
    is_stale: bool
    profile_name: Optional[str] = None


class BatteryHistory(BaseModel):
    period_hours: int
    data: List[BatteryPoint]


class ConfigResponse(BaseModel):
    username: str


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    success: bool
    message: str


class LogoutResponse(BaseModel):
    success: bool
    message: str

