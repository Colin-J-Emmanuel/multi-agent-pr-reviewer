import logging
import asyncpg

from app.config import get_settings
from app.graph import Finding
from app.embeddings import embed_texts, finding_text

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


async def mark_failed(delivery_id: str, error: str) -> None:
    """Record that a review failed, with the reason."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE deliveries
               SET status = 'failed', summary = $2, updated_at = now()
             WHERE delivery_id = $1
            """,
            delivery_id, f"FAILED: {error}"[:2000],
        )
    logger.warning("Delivery %s marked failed: %s", delivery_id, error)

async def save_review(
    *,
    delivery_id: str,
    title: str | None,
    author: str | None,
    summary: str,
    high: list[Finding],
    low: list[Finding],
) -> None:
    """Complete a review: update the delivery to done and attach findings."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            delivery_pk = await conn.execute(
                """
                UPDATE deliveries
                   SET status = 'done', summary = $2, updated_at = now()
                 WHERE delivery_id = $1
                RETURNING id, pull_request_id
                """,
                delivery_id, summary,
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