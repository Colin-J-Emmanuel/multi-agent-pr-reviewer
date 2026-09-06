import logging
from app.queue import REDIS_SETTINGS
from app.github_client import GitHubClient, GitHubError
from app.graph import review_graph, PRState
from app.db import get_pool, close_pool, save_review

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pr-reviewer.worker")


async def review_pr(ctx, job: dict):
    repo = job["repo"]
    pr_number = job["pr_number"]
    logger.info("Reviewing PR %s#%s", repo, pr_number)

    try:
        context = await GitHubClient().fetch_pr(repo, pr_number)
    except GitHubError as e:
        logger.error("Failed to fetch PR %s#%s: %s", repo, pr_number, e)
        raise                       # let arq mark the job failed (see note below)

    logger.info(
        "Fetched '%s' by %s — %d files to review",
        context["title"], context["author"], context["files_returned"],
    )
    # Phase 4: hand `context` to the agent graph.
    # inside review_pr, after you have `context`:
    initial_state: PRState = {"context": context, "findings": []}
    result = await review_graph.ainvoke({
        "context": context, "findings": [],
        "high_findings": [], "low_findings": [], "summary": "",
    })
    high = result["high_findings"]
    low = result["low_findings"]

    logger.info("Review of %s#%s — %d high, %d demoted", repo, pr_number, len(high), len(low))
    logger.info("Summary: %s", result["summary"])
    for f in high:
        logger.info("  [%s/%s] %s (%.2f) — %s", f.category, f.severity, f.file, f.confidence, f.message)
    
    await save_review(
        repo=repo,
        pr_number=pr_number,
        delivery_id=job["delivery_id"],
        head_sha=job.get("head_sha"),
        title=context.get("title"),
        author=context.get("author"),
        summary=result["summary"],
        high=high,
        low=low,
    )

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