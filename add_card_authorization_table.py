"""
Migration: Add card_authorization_requests table for mobile authorization
"""

from main import app, db
from models import CardAuthorizationRequest

def add_card_authorization_table():
    with app.app_context():
        print("Creating card_authorization_requests table...")
        db.create_all()
        print("✓ Table created successfully!")

if __name__ == '__main__':
    add_card_authorization_table()
