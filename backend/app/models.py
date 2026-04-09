from sqlalchemy import Column, Integer, DateTime, SmallInteger, String, func
from .database import Base


class BodyBatteryLog(Base):
    __tablename__ = "body_battery_logs"

    id = Column(Integer, primary_key=True, index=True)
    measured_at = Column(DateTime(timezone=True), unique=True, index=True, nullable=False)
    level = Column(SmallInteger, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    profile_name = Column(String, nullable=True)
