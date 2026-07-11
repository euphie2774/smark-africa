"""
Security utilities for SMARKAFRICA
Includes path sanitization, signature verification, and other security helpers
"""
import os
import hmac
import hashlib
from pathlib import Path
from werkzeug.security import safe_join


def sanitize_filepath(filename, allowed_extensions=None):
    """
    Sanitize a file path to prevent directory traversal attacks

    Args:
        filename: The filename to sanitize
        allowed_extensions: Set of allowed file extensions (e.g., {'pdf', 'jpg'})

    Returns:
        Sanitized filename or None if invalid
    """
    if not filename:
        return None

    # Remove any directory components
    filename = os.path.basename(filename)

    # Remove null bytes
    filename = filename.replace('\x00', '')

    # Remove leading dots and spaces
    filename = filename.lstrip('. ')

    # Check for empty filename after sanitization
    if not filename or filename in ('.', '..'):
        return None

    # Check extension if specified
    if allowed_extensions:
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if ext not in allowed_extensions:
            return None

    return filename


def safe_path_join(base_path, *paths):
    """
    Safely join paths, ensuring the result is within base_path

    Args:
        base_path: The base directory
        *paths: Path components to join

    Returns:
        Safe joined path or None if outside base_path
    """
    try:
        # Use werkzeug's safe_join which prevents directory traversal
        result = safe_join(base_path, *paths)
        if result is None:
            return None

        # Double-check the result is within base_path
        result_resolved = Path(result).resolve()
        base_resolved = Path(base_path).resolve()

        if not str(result_resolved).startswith(str(base_resolved)):
            return None

        return str(result_resolved)
    except Exception:
        return None


def verify_mpesa_signature(payload, signature, secret):
    """
    Verify M-Pesa callback signature

    Args:
        payload: The callback payload (dict or JSON string)
        signature: The signature to verify
        secret: The shared secret key

    Returns:
        True if signature is valid, False otherwise
    """
    if not signature or not secret:
        return False

    try:
        import json
        if isinstance(payload, dict):
            payload = json.dumps(payload, sort_keys=True)

        expected_signature = hmac.new(
            secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        # Use constant-time comparison
        return hmac.compare_digest(signature, expected_signature)
    except Exception:
        return False


def constant_time_compare(val1, val2):
    """
    Compare two values in constant time to prevent timing attacks

    Args:
        val1: First value
        val2: Second value

    Returns:
        True if values are equal, False otherwise
    """
    if val1 is None or val2 is None:
        return False
    return hmac.compare_digest(str(val1), str(val2))


def generate_csrf_token():
    """Generate a CSRF token"""
    import secrets
    return secrets.token_hex(32)


# Safe file extensions - no executables
SAFE_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
SAFE_DIGITAL_EXTENSIONS = {'pdf', 'zip', 'epub', 'mobi', 'mp3', 'mp4', 'jpg', 'jpeg', 'png'}
DANGEROUS_EXTENSIONS = {'exe', 'msi', 'apk', 'bat', 'cmd', 'com', 'sh', 'bash', 'dll', 'so', 'dylib'}


def is_safe_file(filename, file_type='image'):
    """
    Check if a file is safe to upload

    Args:
        filename: The filename to check
        file_type: Type of file ('image' or 'digital')

    Returns:
        True if file is safe, False otherwise
    """
    if not filename or '.' not in filename:
        return False

    ext = filename.rsplit('.', 1)[1].lower()

    # Block dangerous extensions
    if ext in DANGEROUS_EXTENSIONS:
        return False

    # Check against allowed extensions
    if file_type == 'image':
        return ext in SAFE_IMAGE_EXTENSIONS
    elif file_type == 'digital':
        return ext in SAFE_DIGITAL_EXTENSIONS

    return False


def validate_admin_permission(user, required_level='admin'):
    """
    Validate admin permission level

    Args:
        user: User object
        required_level: Required permission level ('admin' or 'mvp')

    Returns:
        True if user has required permission, False otherwise
    """
    if not user or not hasattr(user, 'is_authenticated') or not user.is_authenticated:
        return False

    if not hasattr(user, 'is_admin') or not user.is_admin:
        return False

    if required_level == 'mvp':
        return hasattr(user, 'admin_level') and user.admin_level == 'mvp'

    return True
