ALTER TABLE findings ADD COLUMN IF NOT EXISTS embedding vector(384);

-- HNSW index for fast approximate nearest-neighbour search under cosine distance.
CREATE INDEX IF NOT EXISTS idx_findings_embedding
    ON findings USING hnsw (embedding vector_cosine_ops);