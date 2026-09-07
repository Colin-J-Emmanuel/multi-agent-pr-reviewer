import logging
import os
from app.queue import REDIS_SETTINGS
from app.github_client import GitHubClient, GitHubError
from app.graph import review_graph, PRState, compute_cost
from app.db import get_pool, close_pool, save_review, mark_in_progress, mark_failed, mark_posted
from app.render import render_comment
from app.config import get_settings

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
            "context": context, "findings": [], "usage": [],
            "high_findings": [], "low_findings": [], "summary": "",
        })
        high = result["high_findings"]
        low = result["low_findings"]
        usage = result["usage"]

        in_tok = sum(u["input_tokens"] for u in usage)
        out_tok = sum(u["output_tokens"] for u in usage)
        cost = compute_cost(in_tok, out_tok)
        logger.info("Usage: %d in + %d out = %d tokens, $%.5f",
                    in_tok, out_tok, in_tok + out_tok, cost)

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
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            usage_detail=usage
        )

        client = GitHubClient()
        body = render_comment(result["summary"], high, len(low))
        existing = await client.find_our_comment(repo, pr_number)
        if existing:
            comment_id = await client.update_comment(repo, existing, body)
            logger.info("Updated existing review comment %s", comment_id)
        else:
            comment_id = await client.post_comment(repo, pr_number, body)
            logger.info("Posted new review comment %s", comment_id)

        await mark_posted(delivery_id, comment_id)

    except Exception as e:
        await mark_failed(delivery_id, str(e))
        logger.error("Review of %s#%s failed: %s", repo, pr_number, e)
        raise                       # let arq record the job as failed too

    return {"pr_number": pr_number, "high": len(high), "low": len(low)}

async def startup(ctx):
    s = get_settings()
    if s.langsmith_tracing and s.langsmith_api_key:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = s.langsmith_api_key
        os.environ["LANGSMITH_PROJECT"] = s.langsmith_project
        logger.info("LangSmith tracing enabled (project=%s)", s.langsmith_project)
    ctx["db"] = await get_pool()

async def shutdown(ctx):
    await close_pool()

class WorkerSettings:
    functions = [review_pr]
    redis_settings = REDIS_SETTINGS
    on_startup = startup          # open the pool once when the worker starts
    on_shutdown = shutdown        # close it cleanly on exit