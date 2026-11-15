CREATE TABLE IF NOT EXISTS clients (
    tenant VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    endpoint VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    peers JSON NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (tenant, endpoint),
    UNIQUE KEY unique_token (token_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
