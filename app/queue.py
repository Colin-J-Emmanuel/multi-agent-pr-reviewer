import os
from arq.connections import RedisSettings

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
REDIS_SETTINGS = RedisSettings.from_dsn(REDIS_URL)