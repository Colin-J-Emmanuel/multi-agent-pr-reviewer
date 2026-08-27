import logging
from app.queue import REDIS_SETTINGS
from app.github_client import GitHubClient, GitHubError

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
    return {"pr_number": pr_number, "files_reviewed": context["files_returned"]}


class WorkerSettings:
    functions = [review_pr]
    redis_settings = REDIS_SETTINGS