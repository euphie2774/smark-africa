#!/usr/bin/env python3
"""
Security Setup Script for SMARKAFRICA
Helps configure security settings and create necessary database tables
"""

import os
import secrets
import sys


def generate_secret_key():
    """Generate a secure random secret key"""
    return secrets.token_hex(32)


def create_env_file():
    """Create a .env file with secure defaults"""
    if os.path.exists('.env'):
        response = input('.env file already exists. Overwrite? (y/n): ')
        if response.lower() != 'y':
            print('Skipping .env file creation.')
            return

    secret_key = generate_secret_key()

    env_content = f"""# SMARKAFRICA Security Configuration
# Generated on: {__import__('datetime').datetime.now().isoformat()}

# CRITICAL: Change these before deploying to production!
SECRET_KEY={secret_key}

# Admin Credentials - CHANGE THESE!
ADMIN_USERNAME=admin
ADMIN_PASSWORD=ChangeThisPassword123!
ADMIN_EMAIL=admin@smarkafrica.com

# Flask Environment
FLASK_ENV=development
# Set to 'production' when deploying

# Database
DATABASE_URL=sqlite:///smarkafrica.db
# For production, use PostgreSQL:
# DATABASE_URL=postgresql://user:password@localhost/smarkafrica

# Redis (for rate limiting and caching)
REDIS_URL=redis://localhost:6379/0
# Use 'memory://' for development without Redis

# M-Pesa / Daraja API
DARAJA_CONSUMER_KEY=your_consumer_key_here
DARAJA_CONSUMER_SECRET=your_consumer_secret_here
DARAJA_PASSKEY=your_passkey_here
DARAJA_SHORTCODE=174379
DARAJA_ENV=sandbox
DARAJA_CALLBACK_SECRET={generate_secret_key()[:32]}

# Email Configuration
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-app-password
MAIL_DEFAULT_SENDER=noreply@smarkafrica.com

# Caching
CACHE_TYPE=RedisCache
CACHE_DEFAULT_TIMEOUT=300

# Logging
LOG_LEVEL=INFO
LOG_DIR=logs
"""

    with open('.env', 'w') as f:
        f.write(env_content)

    print('✓ Created .env file with secure defaults')
    print('⚠ IMPORTANT: Edit .env and change the default passwords!')
    print(f'  SECRET_KEY generated: {secret_key[:20]}...')


def create_database_tables():
    """Create database tables including AuditLog"""
    try:
        from main import app, db
        with app.app_context():
            db.create_all()
            print('✓ Database tables created successfully')
            print('  Including new AuditLog table for security logging')
    except Exception as e:
        print(f'✗ Failed to create database tables: {e}')
        print('  Make sure your database is properly configured')


def check_dependencies():
    """Check if all required packages are installed"""
    required_packages = [
        'flask',
        'flask_sqlalchemy',
        'flask_login',
        'flask_wtf',
        'flask_limiter',
        'flask_talisman',
        'marshmallow',
        'werkzeug',
    ]

    missing = []
    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            missing.append(package)

    if missing:
        print('✗ Missing required packages:')
        for pkg in missing:
            print(f'  - {pkg}')
        print('\nRun: pip install -r requirements.txt')
        return False
    else:
        print('✓ All required packages are installed')
        return True


def create_log_directory():
    """Create logs directory"""
    os.makedirs('logs', exist_ok=True)
    print('✓ Created logs directory')


def display_security_checklist():
    """Display post-setup security checklist"""
    print('\n' + '='*60)
    print('SECURITY SETUP CHECKLIST')
    print('='*60)
    print('''
Before deploying to production:

1. Environment Configuration
   [ ] Change SECRET_KEY in .env
   [ ] Change ADMIN_PASSWORD in .env
   [ ] Set FLASK_ENV=production in .env
   [ ] Configure production database (PostgreSQL)
   [ ] Set up Redis for rate limiting

2. M-Pesa Configuration
   [ ] Add valid Daraja API credentials
   [ ] Configure callback URL in Daraja portal
   [ ] Test STK push in sandbox first

3. Email Configuration
   [ ] Add SMTP credentials
   [ ] Test email sending

4. Web Server
   [ ] Enable HTTPS (SSL certificate)
   [ ] Configure gunicorn or uwsgi
   [ ] Set up nginx/Apache reverse proxy
   [ ] Enable firewall

5. Template Updates
   [ ] Add {{ csrf_token() }} to all forms
   [ ] Test all forms work with CSRF protection

6. Testing
   [ ] Test rate limiting on login page
   [ ] Test strong password validation
   [ ] Try uploading .exe file (should fail)
   [ ] Check audit logs are being created
   [ ] Test M-Pesa callbacks

7. Monitoring
   [ ] Set up log monitoring
   [ ] Review audit logs regularly
   [ ] Monitor rate limit violations
   [ ] Set up alerts for security events

For detailed instructions, see SECURITY_IMPROVEMENTS.md
''')


def main():
    """Main setup function"""
    print('='*60)
    print('SMARKAFRICA Security Setup Wizard')
    print('='*60)
    print()

    # Step 1: Check dependencies
    print('[1/5] Checking dependencies...')
    if not check_dependencies():
        print('\n✗ Setup cannot continue without required packages')
        print('Run: pip install -r requirements.txt')
        sys.exit(1)
    print()

    # Step 2: Create .env file
    print('[2/5] Creating .env configuration file...')
    create_env_file()
    print()

    # Step 3: Create log directory
    print('[3/5] Creating log directory...')
    create_log_directory()
    print()

    # Step 4: Create database tables
    print('[4/5] Creating database tables...')
    try:
        create_database_tables()
    except Exception as e:
        print(f'⚠ Warning: Could not create database tables: {e}')
        print('  You may need to run this manually after configuring your database')
    print()

    # Step 5: Display checklist
    print('[5/5] Setup complete!')
    display_security_checklist()

    print('\n' + '='*60)
    print('Next Steps:')
    print('='*60)
    print('1. Edit .env and change all passwords')
    print('2. Review SECURITY_IMPROVEMENTS.md')
    print('3. Add {{ csrf_token() }} to all forms in templates/')
    print('4. Run: python main.py (or gunicorn main:app)')
    print('5. Test all security features')
    print()


if __name__ == '__main__':
    main()
