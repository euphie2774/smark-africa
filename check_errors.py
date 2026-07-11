"""
Error Checker for SMARKAFRICA
Checks for common issues and reports them
"""

import sys
import os

# Fix Windows console encoding
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

print("="*80)
print("SMARKAFRICA - Error Checker")
print("="*80)
print()

errors = []
warnings = []

# 1. Check if running in virtual environment
print("[1/10] Checking virtual environment...")
if sys.prefix == sys.base_prefix:
    errors.append("Not running in virtual environment! Use: venv\\Scripts\\python.exe")
else:
    print("  ✓ Running in virtual environment")

# 2. Check required modules
print("\n[2/10] Checking required modules...")
required_modules = [
    'flask',
    'flask_sqlalchemy',
    'flask_login',
    'flask_wtf',
    'flask_limiter',
    'flask_talisman',
    'marshmallow',
    'werkzeug',
    'sqlalchemy',
    'requests',
    'pillow'
]

missing_modules = []
for module in required_modules:
    try:
        __import__(module)
        print(f"  ✓ {module}")
    except ImportError:
        missing_modules.append(module)
        errors.append(f"Missing module: {module}")
        print(f"  ✗ {module} - NOT FOUND")

# 3. Check custom modules
print("\n[3/10] Checking custom modules...")
custom_modules = ['validators', 'audit_log', 'security_utils', 'chatbot_ai']
for module in custom_modules:
    if os.path.exists(f"{module}.py"):
        print(f"  ✓ {module}.py exists")
        try:
            __import__(module)
            print(f"    ✓ {module} imports successfully")
        except Exception as e:
            errors.append(f"Error importing {module}: {e}")
            print(f"    ✗ Import error: {e}")
    else:
        errors.append(f"Missing file: {module}.py")
        print(f"  ✗ {module}.py - NOT FOUND")

# 4. Check config file
print("\n[4/10] Checking configuration...")
try:
    from config import Config
    print("  ✓ config.py loads successfully")

    if not Config.SECRET_KEY or Config.SECRET_KEY == 'change-this-secret-key-before-launch':
        warnings.append("SECRET_KEY not set or using default")
        print("  ⚠ SECRET_KEY not configured")
    else:
        print("  ✓ SECRET_KEY configured")

except Exception as e:
    errors.append(f"Config error: {e}")
    print(f"  ✗ Config error: {e}")

# 5. Check models
print("\n[5/10] Checking database models...")
try:
    from models import db, User, Product, Order, AuditLog
    print("  ✓ All models import successfully")
except ImportError as e:
    errors.append(f"Model import error: {e}")
    print(f"  ✗ Model import error: {e}")

# 6. Check templates
print("\n[6/10] Checking critical templates...")
critical_templates = [
    'templates/base.html',
    'templates/login.html',
    'templates/register.html',
    'templates/shop.html',
    'templates/support.html',
    'templates/support_enhanced.html'
]

for template in critical_templates:
    if os.path.exists(template):
        print(f"  ✓ {template}")
    else:
        warnings.append(f"Template not found: {template}")
        print(f"  ⚠ {template} - NOT FOUND")

# 7. Check static files
print("\n[7/10] Checking static directories...")
static_dirs = ['static', 'static/uploads', 'static/uploads/products', 'static/uploads/digital']
for dir_path in static_dirs:
    if os.path.exists(dir_path):
        print(f"  ✓ {dir_path}/")
    else:
        warnings.append(f"Directory missing: {dir_path}")
        print(f"  ⚠ {dir_path}/ - NOT FOUND")

# 8. Check database file
print("\n[8/10] Checking database...")
if os.path.exists('smarkafrica.db'):
    print("  ✓ Database file exists")
    print(f"    Size: {os.path.getsize('smarkafrica.db')} bytes")
else:
    warnings.append("Database file not found - will be created on first run")
    print("  ⚠ Database not found (will be created)")

# 9. Check logs directory
print("\n[9/10] Checking logs directory...")
if os.path.exists('logs'):
    print("  ✓ logs/ directory exists")
else:
    warnings.append("logs/ directory missing")
    print("  ⚠ logs/ directory not found")

# 10. Check .env file
print("\n[10/10] Checking environment configuration...")
if os.path.exists('.env'):
    print("  ✓ .env file exists")
else:
    warnings.append(".env file not found - run setup_security.py")
    print("  ⚠ .env file not found")

# Summary
print("\n" + "="*80)
print("SUMMARY")
print("="*80)

if errors:
    print(f"\n❌ ERRORS FOUND: {len(errors)}")
    for i, error in enumerate(errors, 1):
        print(f"  {i}. {error}")
    print("\nFIX THESE ERRORS BEFORE RUNNING THE APPLICATION!")
else:
    print("\n✅ NO CRITICAL ERRORS FOUND")

if warnings:
    print(f"\n⚠️  WARNINGS: {len(warnings)}")
    for i, warning in enumerate(warnings, 1):
        print(f"  {i}. {warning}")
    print("\nThese are not critical but should be addressed.")
else:
    print("\n✅ NO WARNINGS")

print("\n" + "="*80)

# Exit code
if errors:
    print("\nStatus: ❌ FAILED - Fix errors above")
    sys.exit(1)
elif warnings:
    print("\nStatus: ⚠️  PASSED WITH WARNINGS")
    sys.exit(0)
else:
    print("\nStatus: ✅ ALL CHECKS PASSED")
    sys.exit(0)
