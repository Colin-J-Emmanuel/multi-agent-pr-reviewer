-- db/migrations/002_add_posted.sql
ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS comment_id BIGINT;
ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS posted_at TIMESTAMPTZ;