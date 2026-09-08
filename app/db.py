import json
import logging

import asyncpg

from app.config import get_settings
from app.embeddings import embed_texts, finding_text
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

async def mark_in_progress(
    *,
    repo: str,
    pr_number: int,
    delivery_id: str,
    head_sha: str | None,
) -> int:
    """Record that a review has started. Returns the delivery row id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            pr_id = await conn.fetchval(
                """
                INSERT INTO pull_requests (repo, pr_number)
                VALUES ($1, $2)
                ON CONFLICT (repo, pr_number) DO UPDATE SET repo = EXCLUDED.repo
                RETURNING id
                """,
                repo, pr_number,
            )
            delivery_pk = await conn.fetchval(
                """
                INSERT INTO deliveries
                    (delivery_id, pull_request_id, head_sha, status)
                VALUES ($1, $2, $3, 'in_progress')
                ON CONFLICT (delivery_id) DO UPDATE
                    SET status = 'in_progress', updated_at = now()
                RETURNING id
                """,
                delivery_id, pr_id, head_sha,
            )
    logger.info("Delivery %s marked in_progress (id=%s)", delivery_id, delivery_pk)
    return delivery_pk

async def save_review(
    *,
    delivery_id: str,
    title: str | None,
    author: str | None,
    summary: str,
    high: list[Finding],
    low: list[Finding],
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    usage_detail: list[dict],
) -> None:
    """Complete a review: update the delivery to done and attach findings."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE deliveries
                   SET status = 'done', summary = $2, updated_at = now(),
                        input_tokens = $3, output_tokens = $4,
                        cost_usd = $5, usage_detail = $6
                 WHERE delivery_id = $1
                """,
                delivery_id, summary, input_tokens, output_tokens,
                cost_usd, json.dumps(usage_detail)
            )
            row = await conn.fetchrow(
                "SELECT id, pull_request_id FROM deliveries WHERE delivery_id = $1",
                delivery_id,
            )
            delivery_pk, pr_id = row["id"], row["pull_request_id"]

            await conn.execute(
                "UPDATE pull_requests SET title = $2, author = $3 WHERE id = $1",
                pr_id, title, author,
            )

            pairs = [(tier, f)
                     for tier, findings in (("high", high), ("low", low))
                     for f in findings]

            vectors = embed_texts(
                [finding_text(f.category, f.file, f.message) for _, f in pairs]
            )

            rows = [
                (delivery_pk, f.file, f.line, f.category, f.severity,
                 f.confidence, f.message, tier, str(vec))
                for (tier, f), vec in zip(pairs, vectors)
            ]
            if rows:
                await conn.executemany(
                    """
                    INSERT INTO findings
                        (delivery_id, file, line, category, severity,
                         confidence, message, tier, embedding)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    rows,
                )
    logger.info("Delivery %s done: %d high, %d low", delivery_id, len(high), len(low))

async def search_similar_findings(query: str, limit: int = 5) -> list[dict]:
    """Find past findings semantically similar to a query string."""
    vector = embed_texts([query])[0]
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT f.category, f.severity, f.file, f.message, f.tier,
                   pr.repo, pr.pr_number,
                   f.embedding <=> $1 AS distance
              FROM findings f
              JOIN deliveries d ON f.delivery_id = d.id
              JOIN pull_requests pr ON d.pull_request_id = pr.id
             WHERE f.embedding IS NOT NULL
             ORDER BY f.embedding <=> $1
             LIMIT $2
            """,
            str(vector), limit,
        )
    return [dict(r) for r in rows]

async def mark_posted(delivery_id: str, comment_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE deliveries
                SET comment_id = $2, posted_at = now(), updated_at = now()
            WHERE delivery_id = $1
            """,
            delivery_id, comment_id,
        )
    logger.info("Delivery %s posted as comment %s", delivery_id, comment_id)

async def record_failure(delivery_id: str, error: str, kind: str) -> int:
    """Record a failure, increment the attempt counter, return the new count."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """
            UPDATE deliveries
               SET status = 'failed',
                   retry_count = retry_count + 1,
                   last_error = $2,
                   failure_kind = $3,
                   updated_at = now()
             WHERE delivery_id = $1
            RETURNING retry_count
            """,
            delivery_id, error[:2000], kind,
        )
    logger.warning("Delivery %s failed (%s, attempt %s): %s",
                   delivery_id, kind, count, error)
    return count

async def spend_since(hours: int = 24) -> float:
    """Total cost_usd over a recent window."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            """
            SELECT COALESCE(SUM(cost_usd), 0)
              FROM deliveries
             WHERE created_at > now() - ($1 || ' hours')::interval
            """,
            str(hours),
        )
    return float(total)