"""
Add AuditLog model to models.py
Run this script to update your models.py file with audit logging support
"""

# Add this class to your models.py file:

AUDIT_LOG_MODEL = '''

class AuditLog(db.Model):
    """
    Audit log for tracking admin actions and security events
    """
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    action = db.Column(db.String(100), nullable=False, index=True)
    resource_type = db.Column(db.String(50), index=True)
    resource_id = db.Column(db.Integer, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    username = db.Column(db.String(80))
    ip_address = db.Column(db.String(45))  # IPv6 compatible
    user_agent = db.Column(db.String(500))
    details = db.Column(db.Text)  # JSON string for additional details
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship('User', lazy=True, foreign_keys=[user_id])

    def __repr__(self):
        return f'<AuditLog {self.action} on {self.resource_type}:{self.resource_id} by {self.username}>'
'''

print("Add the following class to your models.py file:")
print(AUDIT_LOG_MODEL)

print("\nThen run the following commands to create the table:")
print("python")
print(">>> from main import app, db")
print(">>> with app.app_context():")
print(">>>     db.create_all()")
print(">>>     print('Audit log table created!')")
