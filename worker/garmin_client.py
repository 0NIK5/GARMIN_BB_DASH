import logging
import os
from datetime import datetime, timedelta, timezone

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
)

logger = logging.getLogger(__name__)

TOKEN_DIR = os.path.join(os.path.dirname(__file__), ".garmin_tokens")


class GarminClient:
    """Обёртка над garminconnect для получения heart rate."""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self._client: Garmin | None = None

    def login(self) -> None:
        """Логин в Garmin Connect. Сначала пытается использовать сохранённые токены,
        и только если их нет — делает полноценный login с паролем.
        """
        os.makedirs(TOKEN_DIR, exist_ok=True)
        has_tokens = any(os.scandir(TOKEN_DIR))

        client = Garmin(email=self.username, password=self.password)

        if has_tokens:
            try:
                client.login(tokenstore=TOKEN_DIR)
                logger.info("Logged in via saved tokens")
                self._client = client
                return
            except GarminConnectAuthenticationError as exc:
                logger.warning("Saved tokens rejected (%s), will re-login", exc)
                client = Garmin(email=self.username, password=self.password)

        # Полноценный логин с паролем — делаем только если токенов нет или они невалидны
        logger.info("Logging in with credentials (no valid saved tokens)")
        client.login()
        client.garth.dump(TOKEN_DIR)
        logger.info("Logged in with credentials, tokens saved to %s", TOKEN_DIR)
        self._client = client

    def get_heart_rate(self, start: datetime, end: datetime) -> list[dict]:
        """
        Получить точки heart rate за период [start, end].

        Возвращает список словарей: [{"measured_at": datetime, "level": int}, ...]
        (поле называется "level" для совместимости со схемой БД).
        """
        if self._client is None:
            raise RuntimeError("Not logged in. Call login() first.")

        # garminconnect работает с датами по дням, поэтому перебираем все дни в интервале
        entries: list[dict] = []
        current_date = start.date()
        end_date = end.date()

        while current_date <= end_date:
            cdate = current_date.isoformat()
            logger.info("Fetching heart rate for %s", cdate)
            try:
                data = self._client.get_heart_rates(cdate)
            except GarminConnectConnectionError:
                logger.warning("No heart rate data for %s", cdate)
                current_date += timedelta(days=1)
                continue

            # Структура ответа: {"heartRateValues": [[timestamp_ms, bpm], ...], ...}
            values = (data or {}).get("heartRateValues") or []
            for item in values:
                if not item or len(item) < 2:
                    continue
                ts_ms, bpm = item[0], item[1]
                if bpm is None:
                    continue
                measured_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                if start <= measured_at <= end:
                    entries.append({"measured_at": measured_at, "level": int(bpm)})

            current_date += timedelta(days=1)

        logger.info("Fetched %d heart rate points", len(entries))
        return entries
