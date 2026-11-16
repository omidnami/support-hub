CREATE TABLE IF NOT EXISTS users (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    name VARCHAR(190) NOT NULL,
    email VARCHAR(254) NULL,
    phone VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    password VARCHAR(255) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY unique_users_phone (phone),
    UNIQUE KEY unique_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
