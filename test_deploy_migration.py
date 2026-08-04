"""Reproduce the post-redeploy 500 and prove the schema fingerprint fixes it.

The bug: init_database only ran ensure_phase_two_schema when a hand-written
version string changed. The storefront/location work added columns to products
and business_storefronts but left that string alone, so a database that had
already recorded the old version skipped the migration entirely. The models
still selected the new columns, so every product query failed and the whole
site returned 500.

This test rebuilds that exact state - old version recorded, new columns absent -
then boots the app and checks the columns arrive and the pages render.

Run with the base interpreter (the venv's ctypes is broken):

    PYTHONPATH=".:.venv/Lib/site-packages" \
      "C:/Users/euwin/AppData/Local/Programs/Python/Python314/python.exe" test_deploy_migration.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile

REPO = os.path.dirname(os.path.abspath(__file__))

LOCATION_COLUMNS = {
    'products': ['location_label', 'location_county', 'location_lat', 'location_lng'],
    'business_storefronts': ['location_lat', 'location_lng', 'location_county'],
}

# The literal that shipped in init_database before this fix.
STALE_VERSION = '2026-07-30-pos-vat-raffle-ledger'

FAILURES = []


def check(label, condition, detail=''):
    print(f'  [{"PASS" if condition else "FAIL"}] {label}'
          f'{(" -> " + str(detail)) if detail else ""}')
    if not condition:
        FAILURES.append(label)


def columns_of(conn, table):
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
    except sqlite3.Error:
        return set()


def _scratch_database():
    candidates = [
        os.path.join(REPO, 'instance', 'smarkafrica.db'),
        os.path.join(REPO, 'smarkafrica.db'),
    ]
    source = next((p for p in candidates if os.path.exists(p)), None)
    scratch = os.path.join(tempfile.mkdtemp(prefix='smark-deploy-'), 'predeploy.db')
    if source:
        shutil.copy2(source, scratch)
    return scratch, source


SCRATCH_DB, SOURCE_DB = _scratch_database()
print(f'== building a pre-deploy database ==')
check('found a source database to clone', SOURCE_DB is not None, SOURCE_DB)
if not SOURCE_DB:
    sys.exit('cannot reproduce a deploy without an existing database')

# Roll the clone back to the state a live server would have been in: the old
# schema version recorded, and the location columns not yet added.
conn = sqlite3.connect(SCRATCH_DB)
if conn.execute("SELECT sqlite_version()").fetchone()[0] < '3.35':
    sys.exit('need SQLite 3.35+ for ALTER TABLE DROP COLUMN')

dropped = []
for table, cols in LOCATION_COLUMNS.items():
    present = columns_of(conn, table)
    for col in cols:
        if col in present:
            try:
                conn.execute(f'ALTER TABLE {table} DROP COLUMN {col}')
                dropped.append(f'{table}.{col}')
            except sqlite3.Error as exc:
                print(f'    (could not drop {table}.{col}: {exc})')
conn.commit()

# Record the stale version, exactly as a previously-deployed server would have.
row = conn.execute("SELECT COUNT(*) FROM settings WHERE key = 'phase_two_schema_version'").fetchone()
if row and row[0]:
    conn.execute("UPDATE settings SET value = ? WHERE key = 'phase_two_schema_version'", (STALE_VERSION,))
else:
    conn.execute("INSERT INTO settings (key, value) VALUES ('phase_two_schema_version', ?)", (STALE_VERSION,))
conn.commit()

check('location columns absent before boot',
      all(col not in columns_of(conn, table)
          for table, cols in LOCATION_COLUMNS.items() for col in cols),
      f'{len(dropped)} dropped this run' if dropped else 'already absent in the clone')
recorded = conn.execute("SELECT value FROM settings WHERE key = 'phase_two_schema_version'").fetchone()
check('stale schema version recorded', recorded and recorded[0] == STALE_VERSION, recorded[0] if recorded else None)

missing_before = {t: sorted(set(c) - columns_of(conn, t)) for t, c in LOCATION_COLUMNS.items()}
check('products is missing location columns', len(missing_before['products']) == 4, missing_before['products'])
check('business_storefronts is missing location columns',
      len(missing_before['business_storefronts']) == 3, missing_before['business_storefronts'])
conn.close()

# Now boot the app against that database, which is what a redeploy does.
os.environ['DATABASE_URL'] = 'sqlite:///' + SCRATCH_DB.replace('\\', '/')
os.environ['FLASK_ENV'] = 'development'
os.environ.setdefault('SECRET_KEY', 'smoke-test-key')

print('\n== booting the app (runs init_database at import) ==')
import main  # noqa: E402
from models import db, Product, Setting  # noqa: E402

app = main.app
app.config['TESTING'] = True
app.config['WTF_CSRF_ENABLED'] = False
check('app imported without raising', app is not None)

print('\n== the migration ran despite the stale version ==')
conn = sqlite3.connect(SCRATCH_DB)
for table, cols in LOCATION_COLUMNS.items():
    present = columns_of(conn, table)
    for col in cols:
        check(f'{table}.{col} exists', col in present)
conn.close()

with app.app_context():
    fingerprint = main.phase_two_schema_fingerprint()
    stored = Setting.get('phase_two_schema_version', '')
    check('fingerprint is a stable hash', len(fingerprint) == 16, fingerprint)
    check('fingerprint replaced the stale literal', stored == fingerprint, stored)
    check('fingerprint is deterministic across calls',
          main.phase_two_schema_fingerprint() == fingerprint)

    # The query that was failing in production.
    try:
        Product.query.order_by(Product.created_at.desc()).limit(5).all()
        selected = True
    except Exception as exc:  # noqa: BLE001
        selected = False
        print(f'    query error: {exc}')
    check('product query selecting location columns works', selected)

print('\n== a second boot skips the migration (no needless DDL) ==')
with app.app_context():
    before = Setting.get('phase_two_schema_version', '')
    ran = {'called': False}
    original = main.ensure_phase_two_schema

    def spy():
        ran['called'] = True
        return original()

    main.ensure_phase_two_schema = spy
    try:
        main.init_database()
    finally:
        main.ensure_phase_two_schema = original
    check('migration skipped when fingerprint matches', not ran['called'])
    check('version unchanged', Setting.get('phase_two_schema_version', '') == before)

print('\n== pages that were returning 500 now render ==')
for path in ['/', '/shop']:
    with app.test_client() as client:
        r = client.get(path)
        check(f'GET {path}', r.status_code == 200, r.status_code)

# Second bug found while verifying the first: the context processor that runs on
# every page cached live ORM objects. The first request populated the cache and
# succeeded; every later request within the 60s TTL got detached instances, and
# base.html lazy-loads ad.product, so the entire site 500ed. Exercising a path
# twice as a logged-in non-admin is what reproduces it - the ad block is gated
# on auth_user.is_authenticated and not is_admin.
print('\n== cached ORM objects do not detach across requests ==')
from models import AdCampaign, User  # noqa: E402

with app.app_context():
    seller = User.query.filter(User.is_admin.is_(False)).first()
    if not seller:
        seller = User(username='cachetest', email='cachetest@test.local',
                      password_hash='dummy')
        db.session.add(seller)
        db.session.commit()
    shopper_id = seller.id

    product = Product.query.filter_by(is_active=True).first()
    campaign = AdCampaign.query.filter(AdCampaign.status == 'active').first()
    if not campaign:
        campaign = AdCampaign(status='active', placement='platform',
                              product_id=product.id if product else None,
                              ad_copy='Cache regression probe',
                              objective='Sponsored')
        db.session.add(campaign)
        db.session.commit()
    # A hot sale product exercises the other cached helper on the same path.
    if product and not Product.query.filter_by(is_active=True, is_hot_sale=True).first():
        product.is_hot_sale = True
        db.session.commit()
    check('active platform ad present for the probe', campaign is not None)

if main.cache:
    main.cache.delete('platform_ads_ids')
    main.cache.delete('hot_sale_pop_product_id')

statuses = []
for attempt in range(1, 4):
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_user_id'] = str(shopper_id)
            sess['_fresh'] = True
        r = client.get('/shop')
        statuses.append(r.status_code)
        check(f'logged-in GET /shop attempt {attempt}', r.status_code == 200, r.status_code)

check('cached ads survive a warm cache', statuses[1:] == [200, 200],
      f'first={statuses[0]}, warm={statuses[1:]}')

with app.app_context():
    if main.cache:
        main.cache.delete('platform_ads_ids')
    first_ad, first_list = main._get_cached_platform_ads()
    second_ad, second_list = main._get_cached_platform_ads()
    check('helper returns the same ad from a warm cache',
          (first_ad.id if first_ad else None) == (second_ad.id if second_ad else None),
          first_ad.id if first_ad else None)
    check('warm-cache ads are session-bound (product readable)',
          all(ad.product is None or ad.product.name is not None for ad in second_list),
          f'{len(second_list)} ad(s)')

shutil.rmtree(os.path.dirname(SCRATCH_DB), ignore_errors=True)

print('\n' + '=' * 60)
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S):')
    for f in FAILURES:
        print('  - ' + f)
    sys.exit(1)
print('ALL CHECKS PASSED')
