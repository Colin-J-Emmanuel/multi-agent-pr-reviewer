import hashlib
import hmac
import logging
from contextlib import asynccontextmanager

from arq import create_pool

# import os
from fastapi import FastAPI, Header, HTTPException, Request, status

from app.config import get_settings
from app.queue import REDIS_SETTINGS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pr-reviewer")

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.arq = await create_pool(REDIS_SETTINGS)
    yield
    await app.state.arq.close()

app = FastAPI(title="Multi-Agent PR Reviewer", lifespan=lifespan)

# WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"].encode()
settings = get_settings()
WEBHOOK_SECRET = settings.github_webhook_secret.encode()
RELEVANT_ACTIONS = {"opened", "synchronize", "reopened"}


def verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    if signature_header is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing signature")
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET, raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid signature")


@app.post("/webhook", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    x_github_event: str = Header(None),
    x_github_delivery: str = Header(None),
    x_hub_signature_256: str = Header(None),
):
    raw_body = await request.body()          # raw bytes BEFORE json parsing — required for HMAC
    verify_signature(raw_body, x_hub_signature_256)

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"event={x_github_event}"}

    payload = await request.json()
    action = payload.get("action")
    if action not in RELEVANT_ACTIONS:
        return {"status": "ignored", "reason": f"action={action}"}

    pr = payload["pull_request"]
    job = {
        "delivery_id": x_github_delivery,
        "repo": payload["repository"]["full_name"],
        "pr_number": pr["number"],
        "head_sha": pr["head"]["sha"],
    }
    # Phase 2: enqueue this to Redis; a worker does the slow LLM work off the request path.
    redis = request.app.state.arq
    # SET NX returns True only the first time we see this delivery id → dedupe.
    is_new = await redis.set(f"delivery:{x_github_delivery}", "1", ex=86_400, nx=True)
    if not is_new:
        return {"status": "duplicate", "delivery_id": x_github_delivery}

    await redis.enqueue_job("review_pr", job)
    logger.info("Enqueued PR job: %s", job)
    return {"status": "accepted", "delivery_id": x_github_delivery}
