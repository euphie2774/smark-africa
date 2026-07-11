"""
Audit Logging System for SMARKAFRICA
Tracks all admin actions and sensitive operations
"""
import logging
from datetime import datetime
from functools import wraps
from flask import request
from flask_login import current_user

# Configure audit logger
audit_logger = logging.getLogger('audit')
audit_logger.setLevel(logging.INFO)


def log_admin_action(action, resource_type=None, resource_id=None, details=None, user=None):
    """
    Log an admin action to the audit trail

    Args:
        action: Action performed (e.g., 'create', 'update', 'delete')
        resource_type: Type of resource (e.g., 'product', 'user', 'order')
        resource_id: ID of the resource
        details: Additional details dict
        user: User performing action (defaults to current_user)
    """
    if user is None and hasattr(current_user, 'id'):
        user = current_user

    log_entry = {
        'timestamp': datetime.utcnow().isoformat(),
        'action': action,
        'resource_type': resource_type,
        'resource_id': resource_id,
        'user_id': user.id if user and hasattr(user, 'id') else None,
        'username': user.username if user and hasattr(user, 'username') else 'anonymous',
        'ip_address': request.remote_addr if request else None,
        'user_agent': request.headers.get('User-Agent') if request else None,
        'details': details or {}
    }

    # Log to file
    audit_logger.info(f"AUDIT: {log_entry}")

    # Also persist to database
    try:
        from models import db, AuditLog
        import json

        audit_record = AuditLog(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=log_entry['user_id'],
            username=log_entry['username'],
            ip_address=log_entry['ip_address'],
            user_agent=log_entry['user_agent'],
            details=json.dumps(details) if details else None
        )
        db.session.add(audit_record)
        db.session.commit()
    except Exception as e:
        # Don't fail the request if audit logging fails
        audit_logger.error(f"Failed to persist audit log to database: {e}")

    return log_entry


def audit_log(action, resource_type=None):
    """
    Decorator to automatically log admin actions

    Usage:
        @audit_log('delete', 'product')
        def delete_product(pid):
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Extract resource_id from kwargs or args
            resource_id = kwargs.get('pid') or kwargs.get('uid') or kwargs.get('oid') or kwargs.get('id')
            if not resource_id and args:
                resource_id = args[0] if isinstance(args[0], (int, str)) else None

            # Execute the function
            result = f(*args, **kwargs)

            # Log after successful execution
            log_admin_action(
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details={'function': f.__name__}
            )

            return result
        return decorated_function
    return decorator


class AuditLogMixin:
    """Mixin for database models to add audit logging"""

    def log_create(self):
        """Log creation of this model instance"""
        log_admin_action(
            action='create',
            resource_type=self.__tablename__,
            resource_id=self.id if hasattr(self, 'id') else None,
            details={'model': self.__class__.__name__}
        )

    def log_update(self, changes=None):
        """Log update of this model instance"""
        log_admin_action(
            action='update',
            resource_type=self.__tablename__,
            resource_id=self.id if hasattr(self, 'id') else None,
            details={'model': self.__class__.__name__, 'changes': changes}
        )

    def log_delete(self):
        """Log deletion of this model instance"""
        log_admin_action(
            action='delete',
            resource_type=self.__tablename__,
            resource_id=self.id if hasattr(self, 'id') else None,
            details={'model': self.__class__.__name__}
        )
