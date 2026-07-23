#!/usr/bin/env python3
"""
Reset or create the configured SMARKAFRICA admin account.

Usage:
    python reset_admin_password.py --password "NewStrongPassword123!"

If --password is omitted, the script uses ADMIN_PASSWORD from the environment.
Run this on the deployed server with the same environment variables as the app.
"""

import argparse
import getpass
import os
import sys


def parse_args():
    parser = argparse.ArgumentParser(description='Reset the SMARKAFRICA admin password.')
    parser.add_argument('--username', help='Admin username. Defaults to ADMIN_USERNAME or admin.')
    parser.add_argument('--email', help='Admin email. Defaults to ADMIN_EMAIL or admin@smarkafrica.com.')
    parser.add_argument('--password', help='New admin password. Defaults to ADMIN_PASSWORD or secure prompt.')
    return parser.parse_args()


def password_from_args(args):
    password = args.password or os.environ.get('ADMIN_PASSWORD')
    if password:
        return password

    first = getpass.getpass('New admin password: ')
    second = getpass.getpass('Confirm admin password: ')
    if first != second:
        raise ValueError('Passwords do not match.')
    return first


def main():
    args = parse_args()
    password = password_from_args(args)
    if len(password) < 8:
        raise ValueError('Password must be at least 8 characters.')

    from main import app, db
    from models import User

    with app.app_context():
        username = args.username or app.config.get('ADMIN_USERNAME') or os.environ.get('ADMIN_USERNAME') or 'admin'
        email = args.email or app.config.get('ADMIN_EMAIL') or os.environ.get('ADMIN_EMAIL') or 'admin@smarkafrica.com'

        admin = User.query.filter_by(username=username).first()
        if not admin:
            admin = User(
                username=username,
                email=email,
                phone='254700000000',
                is_admin=True,
                admin_level='mvp',
                is_active=True,
            )
            db.session.add(admin)
            action = 'created'
        else:
            admin.email = admin.email or email
            admin.is_admin = True
            admin.admin_level = 'mvp'
            admin.is_active = True
            action = 'updated'

        admin.set_password(password)
        db.session.commit()

        print(f'Admin account {action}: {admin.username}')
        print('Password reset complete. Remove ADMIN_RESET_PASSWORD if you used it elsewhere, then restart the app.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
