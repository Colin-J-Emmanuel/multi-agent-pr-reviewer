CREATE EXTENSION IF NOT EXISTS vector;

-- One row per pull request we've ever seen.
CREATE TABLE IF NOT EXISTS pull_requests (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    repo        TEXT NOT NULL,
    pr_number   INTEGER NOT NULL,
    title       TEXT,
    author      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (repo, pr_number)
);

-- One row per webhook delivery = one review run.
CREATE TABLE IF NOT EXISTS deliveries (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    delivery_id     TEXT NOT NULL UNIQUE,          -- GitHub's X-GitHub-Delivery
    pull_request_id BIGINT NOT NULL REFERENCES pull_requests(id),
    head_sha        TEXT,
    status          TEXT NOT NULL DEFAULT 'accepted',   -- accepted | in_progress | done | failed
    summary         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per finding, belonging to a specific delivery (review run).
CREATE TABLE IF NOT EXISTS findings (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    delivery_id  BIGINT NOT NULL REFERENCES deliveries(id) ON DELETE CASCADE,
    file         TEXT NOT NULL,
    line         INTEGER,
    category     TEXT NOT NULL,
    severity     TEXT NOT NULL,
    confidence   REAL NOT NULL,
    message      TEXT NOT NULL,
    tier         TEXT NOT NULL,                    -- high | low  (from the aggregator)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_deliveries_pr ON deliveries(pull_request_id);
CREATE INDEX IF NOT EXISTS idx_findings_delivery ON findings(delivery_id);