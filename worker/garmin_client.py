import json
import logging
import os
import subprocess
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

NODE_HELPER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "worker_node"))
NODE_HELPER_SCRIPT = os.path.join(NODE_HELPER_DIR, "fetch_heart_rate.js")


class NodeGarminClient:
    """
    Гибридный клиент: вызывает Node.js скрипт `worker_node/fetch_heart_rate.js`
    через subprocess. Node.js библиотека `garmin-connect` использует другой
    TLS fingerprint и проходит Cloudflare там, где Python падает с 429.

    Креды читаются Node-скриптом из env GARMIN_USERNAME / GARMIN_PASSWORD.
    Токены кэшируются Node-скриптом в worker_node/tokens/.
    """

    def __init__(self, username: str = "", password: str = ""):
        # username/password передаются через env, тут только для совместимого интерфейса
        self.username = username
        self.password = password
        if not os.path.exists(NODE_HELPER_SCRIPT):
            raise FileNotFoundError(f"Node helper not found: {NODE_HELPER_SCRIPT}")

    def login(self) -> None:
        # Node.js скрипт логинится сам при первом вызове get_heart_rate
        logger.info("NodeGarminClient: login deferred to Node helper")

    def get_heart_rate(self, start: datetime, end: datetime) -> list[dict]:
        env = os.environ.copy()
        if self.username:
            env["GARMIN_USERNAME"] = self.username
        if self.password:
            env["GARMIN_PASSWORD"] = self.password

        cmd = ["node", NODE_HELPER_SCRIPT, start.isoformat(), end.isoformat()]
        logger.info("Spawning node helper: %s", " ".join(cmd[:2]))
        try:
            result = subprocess.run(
                cmd,
                cwd=NODE_HELPER_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            logger.error("Node helper timed out")
            raise

        if result.stderr:
            for line in result.stderr.strip().splitlines():
                logger.info("node: %s", line)

        if result.returncode != 0:
            logger.error("Node helper failed with code %d", result.returncode)
            raise RuntimeError(f"Node helper exited with code {result.returncode}")

        try:
            raw = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse node helper output: %s", exc)
            raise

        entries: list[dict] = []
        for item in raw:
            measured_at = datetime.fromisoformat(item["measured_at"].replace("Z", "+00:00"))
            entries.append({"measured_at": measured_at, "level": int(item["level"])})

        logger.info("NodeGarminClient: got %d heart rate points", len(entries))
        return entries
