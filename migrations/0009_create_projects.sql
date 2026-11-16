CREATE TABLE IF NOT EXISTS projects (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id BIGINT UNSIGNED NOT NULL,
    name VARCHAR(190) NOT NULL,
    apikey CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    domain VARCHAR(253) CHARACTER SET ascii COLLATE ascii_bin NULL,
    PRIMARY KEY (id),
    UNIQUE KEY unique_projects_apikey (apikey),
    KEY index_projects_user_id (user_id),
    FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
