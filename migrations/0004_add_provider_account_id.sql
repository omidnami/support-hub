ALTER TABLE channels ADD COLUMN IF NOT EXISTS provider_account_id VARCHAR(64)
    CHARACTER SET ascii COLLATE ascii_bin NULL;
