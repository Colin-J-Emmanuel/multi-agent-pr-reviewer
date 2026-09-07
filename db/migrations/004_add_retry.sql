ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS failure_kind TEXT;  -- transient | permanent