import logging
from typing import Any

import asyncpg

from app.config import get_settings
from app.graph import Finding

logger = logging.getLogger("pr-reviewer.db")

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Lazily create and reuse a single connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            get_settings().database_url, min_size=1, max_size=5
        )
        logger.info("Postgres pool created")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def save_review(
    *,
    repo: str,
    pr_number: int,
    delivery_id: str,
    head_sha: str | None,
    title: str | None,
    author: str | None,
    summary: str,
    high: list[Finding],
    low: list[Finding],
) -> None:
    """Persist a completed review: PR, delivery, and all findings, atomically."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():                     # all-or-nothing
            # 1. Upsert the PR — reuse the row if we've seen this PR before.
            pr_id = await conn.fetchval(
                """
                INSERT INTO pull_requests (repo, pr_number, title, author)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (repo, pr_number) DO UPDATE
                    SET title = EXCLUDED.title, author = EXCLUDED.author
                RETURNING id
                """,
                repo, pr_number, title, author,
            )

            # 2. Insert the delivery (this review run), linked to the PR.
            delivery_pk = await conn.fetchval(
                """
                INSERT INTO deliveries
                    (delivery_id, pull_request_id, head_sha, status, summary)
                VALUES ($1, $2, $3, 'done', $4)
                RETURNING id
                """,
                delivery_id, pr_id, head_sha, summary,
            )

            # 3. Insert all findings, each tagged with its tier.
            rows = [
                (delivery_pk, f.file, f.line, f.category, f.severity,
                 f.confidence, f.message, tier)
                for tier, findings in (("high", high), ("low", low))
                for f in findings
            ]
            if rows:
                await conn.executemany(
                    """
                    INSERT INTO findings
                        (delivery_id, file, line, category, severity,
                         confidence, message, tier)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    rows,
                )

    logger.info(
        "Saved review %s#%s: %d high, %d low findings",
        repo, pr_number, len(high), len(low),
    )