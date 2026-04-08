import json
import os
from datetime import datetime, timedelta

SESSION_FILE = os.path.join(os.path.dirname(__file__), "session.json")


class GarminClient:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.session = self._load_session()

    def _load_session(self):
        if os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, "r", encoding="utf-8") as handle:
                return json.load(handle)
        return None

    def _save_session(self, data):
        with open(SESSION_FILE, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def login(self):
        # TODO: реализовать реальный Garmin Connect login
        self.session = {"logged_in_at": datetime.utcnow().isoformat()}
        self._save_session(self.session)

    def get_body_battery(self, start: datetime, end: datetime):
        # TODO: заменить заглушку на вызов Garmin API
        points = []
        current = start
        while current <= end:
            points.append({
                "measured_at": current,
                "level": 50 + (current.hour % 20),
            })
            current += timedelta(hours=1)
        return points
