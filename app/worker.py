import logging
from app.queue import REDIS_SETTINGS
from app.github_client import GitHubClient, GitHubError
from app.graph import review_graph, PRState
from app.db import get_pool, close_pool, save_review, mark_in_progress, mark_failed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pr-reviewer.worker")


async def review_pr(ctx, job: dict):
    repo = job["repo"]
    pr_number = job["pr_number"]
    delivery_id = job["delivery_id"]
    logger.info("Reviewing PR %s#%s", repo, pr_number)

    # Record that we've started — BEFORE any slow work.
    await mark_in_progress(
        repo=repo, pr_number=pr_number,
        delivery_id=delivery_id, head_sha=job.get("head_sha"),
    )

    try:
        context = await GitHubClient().fetch_pr(repo, pr_number)

        logger.info(
            "Fetched '%s' by %s — %d files to review",
            context["title"], context["author"], context["files_returned"],
        )

        result = await review_graph.ainvoke({
            "context": context, "findings": [],
            "high_findings": [], "low_findings": [], "summary": "",
        })
        high = result["high_findings"]
        low = result["low_findings"]

        logger.info("Review of %s#%s — %d high, %d demoted",
                    repo, pr_number, len(high), len(low))
        for f in high:
            logger.info("  [%s/%s] %s (%.2f) — %s",
                        f.category, f.severity, f.file, f.confidence, f.message)

        await save_review(
            delivery_id=delivery_id,
            title=context.get("title"),
            author=context.get("author"),
            summary=result["summary"],
            high=high, low=low,
        )

    except Exception as e:
        await mark_failed(delivery_id, str(e))
        logger.error("Review of %s#%s failed: %s", repo, pr_number, e)
        raise                       # let arq record the job as failed too

    return {"pr_number": pr_number, "high": len(high), "low": len(low)}

async def startup(ctx):
    ctx["db"] = await get_pool()

async def shutdown(ctx):
    await close_pool()

class WorkerSettings:
    functions = [review_pr]
    redis_settings = REDIS_SETTINGS
    on_startup = startup          # open the pool once when the worker starts
    on_shutdown = shutdown        # close it cleanly on exit