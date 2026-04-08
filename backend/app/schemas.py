from datetime import datetime
from typing import List
from pydantic import BaseModel


class BatteryPoint(BaseModel):
    time: datetime
    level: int

    class Config:
        orm_mode = True


class BatteryCurrent(BaseModel):
    timestamp: datetime
    level: int
    status: str
    minutes_since_update: int
    is_stale: bool


class BatteryHistory(BaseModel):
    period_hours: int
    data: List[BatteryPoint]
