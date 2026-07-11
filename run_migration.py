"""
Simple migration script to add card_authorization_requests table
This avoids Flask app initialization issues
"""
import sqlite3
import os
import sys

# Fix unicode encoding for Windows console
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Find the database file
db_path = os.path.join('instance', 'smarkafrica.db')

if not os.path.exists(db_path):
    print(f"Database not found at {db_path}")
    print("Trying alternative paths...")
    # Try other common locations
    alternatives = [
        'smarkafrica.db',
        'instance/database.db',
        'database.db'
    ]
    for alt in alternatives:
        if os.path.exists(alt):
            db_path = alt
            print(f"Found database at {alt}")
            break
    else:
        print("ERROR: Could not find database file!")
        print("Please run this script from the project root directory")
        exit(1)

print(f"Using database: {db_path}")

# Connect to database
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("Creating card_authorization_requests table...")

# Create table
create_table_sql = """
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
"""

try:
    cursor.execute(create_table_sql)
    print("[OK] Table created successfully!")
except sqlite3.Error as e:
    print(f"[ERROR] creating table: {e}")
    conn.close()
    exit(1)

# Create indexes
print("Creating indexes...")
indexes = [
    "CREATE INDEX IF NOT EXISTS ix_card_auth_card_created ON card_authorization_requests(card_id, created_at);",
    "CREATE INDEX IF NOT EXISTS ix_card_auth_status_created ON card_authorization_requests(status, created_at);",
    "CREATE INDEX IF NOT EXISTS ix_card_auth_token ON card_authorization_requests(authorization_token);"
]

for idx_sql in indexes:
    try:
        cursor.execute(idx_sql)
        print(f"[OK] Index created")
    except sqlite3.Error as e:
        print(f"[WARN] creating index: {e}")

# Add missing columns to shopping_cards if not present
print("\nAdding missing columns to shopping_cards...")
alter_columns = [
    ("pin_set_at", "DATETIME"),
    ("pin_set_token", "VARCHAR(64)"),
]
for col_name, col_type in alter_columns:
    try:
        cursor.execute(f"ALTER TABLE shopping_cards ADD COLUMN {col_name} {col_type}")
        print(f"[OK] Added shopping_cards.{col_name}")
    except sqlite3.Error as e:
        if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
            print(f"[SKIP] shopping_cards.{col_name} already exists")
        else:
            print(f"[WARN] shopping_cards.{col_name}: {e}")

# Commit changes
conn.commit()
print("\n[SUCCESS] Migration completed successfully!")

# Verify table exists
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='card_authorization_requests';")
result = cursor.fetchone()
if result:
    print(f"[OK] Verified: card_authorization_requests table exists")
else:
    print("[WARN] Table verification failed")

# Show table structure
print("\nTable structure:")
cursor.execute("PRAGMA table_info(card_authorization_requests);")
columns = cursor.fetchall()
for col in columns:
    print(f"  - {col[1]} ({col[2]})")

conn.close()
print("\n[DONE]")
