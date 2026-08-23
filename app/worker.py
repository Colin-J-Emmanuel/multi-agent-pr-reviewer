import logging
from app.queue import REDIS_SETTINGS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pr-reviewer.worker")


async def review_pr(ctx, job: dict):
    logger.info("Reviewing PR: %s", job)
    # Phase 3: fetch diff + changed files.  Phase 4: run the agent graph.
    return {"reviewed": job["pr_number"]}


class WorkerSettings:
    functions = [review_pr]
    redis_settings = REDIS_SETTINGS