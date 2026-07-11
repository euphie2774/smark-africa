-- Migration: Add card_authorization_requests table
-- Run this SQL script in your database

CREATE TABLE IF NOT EXISTS card_authorization_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    pos_sale_id INTEGER,
    authorization_token VARCHAR(64) UNIQUE NOT NULL,
    amount FLOAT NOT NULL,
    merchant_name VARCHAR(160),
    pos_terminal_id VARCHAR(80),
    phone_number VARCHAR(20),
    status VARCHAR(30) DEFAULT 'pending',
    user_response VARCHAR(30),
    response_at DATETIME,
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (card_id) REFERENCES shopping_cards(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (pos_sale_id) REFERENCES point_of_sale_sales(id)
);

CREATE INDEX IF NOT EXISTS ix_card_auth_card_created ON card_authorization_requests(card_id, created_at);
CREATE INDEX IF NOT EXISTS ix_card_auth_status_created ON card_authorization_requests(status, created_at);
CREATE INDEX IF NOT EXISTS ix_card_auth_token ON card_authorization_requests(authorization_token);

-- Verify table was created
SELECT 'Table created successfully' as status;
