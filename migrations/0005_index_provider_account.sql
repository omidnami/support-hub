CREATE UNIQUE INDEX IF NOT EXISTS unique_provider_account ON channels(platform, provider_account_id);
