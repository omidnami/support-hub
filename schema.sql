CREATE TABLE IF NOT EXISTS clients (
    tenant VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    endpoint VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    peers JSON NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (tenant, endpoint),
    UNIQUE KEY unique_token (token_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS channels (
    id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    endpoint VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    owner_endpoint VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    platform VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    credentials BLOB NOT NULL,
    webhook_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (id),
    UNIQUE KEY unique_endpoint (tenant, endpoint),
    FOREIGN KEY (tenant, owner_endpoint) REFERENCES clients(tenant, endpoint)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS options (
    name VARCHAR(100) NOT NULL PRIMARY KEY,
    value JSON NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE channels ADD COLUMN IF NOT EXISTS provider_account_id VARCHAR(64)
    CHARACTER SET ascii COLLATE ascii_bin NULL;
CREATE UNIQUE INDEX IF NOT EXISTS unique_provider_account ON channels(platform, provider_account_id);
