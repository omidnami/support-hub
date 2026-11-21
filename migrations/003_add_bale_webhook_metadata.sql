ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS webhook_secret VARCHAR(64) NULL;

ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS last_update_id BIGINT UNSIGNED NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_sources_webhook_secret
    ON sources (webhook_secret);
