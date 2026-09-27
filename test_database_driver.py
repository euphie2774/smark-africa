"""Deployment regression: generic PostgreSQL URLs must use the installed driver.

No database connection or application startup is needed for these checks.
"""
import os
import unittest
from unittest.mock import patch

from sqlalchemy.engine import make_url

# Import configuration without inheriting deployment credentials or requiring a
# production database. This also avoids creating a local secret-key file.
with patch.dict(os.environ, {
    'SECRET_KEY': 'driver-regression-test',
    'DATABASE_URL': 'sqlite:///:memory:',
    'ALLOW_EPHEMERAL_SQLITE': '1',
}):
    from config import _database_url


class DatabaseDriverTests(unittest.TestCase):
    def test_hosted_postgres_urls_select_psycopg2(self):
        suffix = 'user:p%40ss%2Fword@db.example:5432/event?sslmode=require&connect_timeout=10'
        for prefix in ('postgres://', 'postgresql://'):
            with self.subTest(prefix=prefix), patch.dict(os.environ, {'DATABASE_URL': ' ' + prefix + suffix + ' '}):
                uri = _database_url(required=True)
                self.assertEqual(uri, 'postgresql+psycopg2://' + suffix)
                # Resolve through SQLAlchemy, not just a string comparison.
                self.assertEqual(make_url(uri).get_dialect().driver, 'psycopg2')

    def test_explicit_drivers_and_other_databases_are_preserved(self):
        for uri in (
            'postgresql+psycopg2://user:pass@db.example/event',
            'postgresql+psycopg://user:pass@db.example/event',
            'mysql+pymysql://user:pass@db.example/event',
            'sqlite:///:memory:',
        ):
            with self.subTest(uri=uri), patch.dict(os.environ, {'DATABASE_URL': uri}):
                self.assertEqual(_database_url(), uri)

    def test_production_still_requires_persistent_database(self):
        for uri in ('', 'sqlite:///:memory:'):
            with self.subTest(uri=uri), patch.dict(os.environ, {
                'DATABASE_URL': uri, 'ALLOW_EPHEMERAL_SQLITE': '0',
            }):
                with self.assertRaises(RuntimeError):
                    _database_url(required=True)


if __name__ == '__main__':
    unittest.main()
