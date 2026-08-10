"""Smoke test for the verified-seller hub, storefront listing page, and unread badge.

Copies the working database to a throwaway file so this never mutates real data.
Run with the base interpreter (the venv's ctypes is broken):

    PYTHONPATH=".:.venv/Lib/site-packages" \
      "C:/Users/euwin/AppData/Local/Programs/Python/Python314/python.exe" \
      test_seller_hub_notifications.py

Covers: /seller/apply becoming a listing hub once verified, /storefront/apply
becoming a shop listing page once approved, the shop-details save round trip,
the navbar unread-notification badge, and the raffles copy removal.
"""
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.abspath(__file__))


def _scratch_database():
    """Clone the dev database so the test can write freely."""
    candidates = [
        os.path.join(REPO, 'instance', 'smarkafrica.db'),
        os.path.join(REPO, 'smarkafrica.db'),
    ]
    source = next((p for p in candidates if os.path.exists(p)), None)
    scratch = os.path.join(tempfile.mkdtemp(prefix='smark-sellerhub-'), 'test.db')
    if source:
        shutil.copy2(source, scratch)
    return scratch


SCRATCH_DB = _scratch_database()
os.environ['DATABASE_URL'] = 'sqlite:///' + SCRATCH_DB.replace('\\', '/')
os.environ['FLASK_ENV'] = 'development'
# The app caches the settings dict for 60s in a FileSystemCache under .cache,
# which outlives the process - so a previous run could hand this one a stale
# seller_signup_enabled and blank the navbar. Tests get no cache at all.
os.environ['CACHE_TYPE'] = 'NullCache'
os.environ.setdefault('SECRET_KEY', 'smoke-test-key')

import main  # noqa: E402
from models import (db, User, Product, BusinessStorefront, Category,  # noqa: E402
                    CustomerNotification, Setting)

app = main.app
app.config['TESTING'] = True
app.config['WTF_CSRF_ENABLED'] = False

FAILURES = []


def check(label, condition, detail=''):
    print(f'  [{"PASS" if condition else "FAIL"}] {label}'
          f'{(" -> " + str(detail)) if detail else ""}')
    if not condition:
        FAILURES.append(label)


def login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


with app.app_context():
    print(f'== schema (scratch db: {SCRATCH_DB}) ==')
    main.ensure_phase_two_schema()
    db.session.commit()

    # The new shop-description columns must survive the migration path.
    cols = {c['name'] for c in db.inspect(db.engine).get_columns('business_storefronts')}
    for column in ('about', 'specialties', 'opening_hours'):
        check(f'business_storefronts.{column} migrated', column in cols)

    # /seller/apply redirects home unless seller signup is switched on.
    Setting.set('seller_signup_enabled', '1')
    db.session.commit()

    category = Category.query.filter_by(is_active=True).first()
    if not category:
        category = Category(name='Electronics', slug='electronics', is_active=True)
        db.session.add(category)
        db.session.commit()

    seller = User(username='hubseller', email='hubseller@test.local',
                  password_hash='dummy', seller_status='verified',
                  is_verified_seller=True)
    db.session.add(seller)
    db.session.commit()
    seller_id, category_id = seller.id, category.id
    check('verified seller created', main.user_can_sell(seller), seller.username)

print('\n== verified seller gets the hub, not the KYC form ==')
with app.test_client() as client:
    login(client, seller_id)
    r = client.get('/seller/apply')
    check('GET /seller/apply renders', r.status_code == 200, r.status_code)
    html = r.get_data(as_text=True)
    check('shows verified badge', 'Verified seller' in html)
    check('no KYC application form', 'id_number' not in html and 'id_front' not in html)
    check('prompts to open a storefront', 'Open your storefront' in html)

print('\n== navbar reflects seller state ==')
with app.test_client() as client:
    login(client, seller_id)
    html = client.get('/').get_data(as_text=True)
    check('nav says "Seller" not "Become a Seller"', 'Become a Seller' not in html)
    check('nav links to seller hub', 'seller/apply' in html)

print('\n== unread notification badge ==')
with app.app_context():
    for i in range(3):
        db.session.add(CustomerNotification(user_id=seller_id, title=f'Notice {i}',
                                            body='body', notification_type='storefront',
                                            is_read=False))
    db.session.add(CustomerNotification(user_id=seller_id, title='Already read',
                                        body='body', notification_type='storefront',
                                        is_read=True))
    db.session.commit()
    check('helper counts only unread', main.unread_notification_count(
        db.session.get(User, seller_id)) == 3)

with app.test_client() as client:
    login(client, seller_id)
    html = client.get('/').get_data(as_text=True)
    check('badge count rendered in navbar', 'unread notification' in html)
    check('badge shows 3', '>3' in html.replace(' ', '').replace('\n', ''))

with app.app_context():
    anon_count = main.unread_notification_count(None)
    check('anonymous user counts zero', anon_count == 0, anon_count)

print('\n== storefront pending review ==')
with app.test_client() as client:
    login(client, seller_id)
    r = client.post('/storefront/apply',
                    data={'business_name': 'Hub Electronics',
                          'category_id': category_id,
                          'physical_address': 'Kimathi Street, Nairobi',
                          'landmark': 'Near Hilton',
                          'contact_phone': '+254712345678',
                          'contact_email': 'hub@test.local'},
                    follow_redirects=False)
    check('POST application accepted', r.status_code in (200, 302), r.status_code)

with app.app_context():
    storefront = BusinessStorefront.query.filter_by(owner_id=seller_id).first()
    check('storefront created pending', storefront and storefront.status == 'pending_review',
          storefront.status if storefront else None)
    storefront_id = storefront.id if storefront else None

with app.test_client() as client:
    login(client, seller_id)
    html = client.get('/seller/apply').get_data(as_text=True)
    check('hub shows "under review" state', 'Storefront under review' in html)
    check('hub names the pending business', 'Hub Electronics' in html)

print('\n== approved storefront gets the listing page ==')
with app.app_context():
    storefront = db.session.get(BusinessStorefront, storefront_id)
    storefront.status = 'approved'
    db.session.commit()

with app.test_client() as client:
    login(client, seller_id)
    r = client.get('/storefront/apply')
    check('GET /storefront/apply renders', r.status_code == 200, r.status_code)
    html = r.get_data(as_text=True)
    check('shop details form replaces the application', 'What my shop entails' in html)
    check('no business_name application field', 'name="business_name"' not in html)
    check('approved badge shown', 'Approved storefront' in html)

    r = client.post('/storefront/apply',
                    data={'about': 'We stock phones, laptops and do repairs.',
                          'specialties': 'Phones, Laptops, Repairs',
                          'contact_phone': '+254712345678',
                          'contact_email': 'hub@test.local',
                          'physical_address': 'Kimathi Street, Nairobi',
                          'landmark': 'Near Hilton'},
                    follow_redirects=True)
    check('POST shop details accepted', r.status_code == 200, r.status_code)
    check('save confirmation flashed', 'Shop details updated' in r.get_data(as_text=True))

with app.app_context():
    storefront = db.session.get(BusinessStorefront, storefront_id)
    check('about persisted', storefront.about == 'We stock phones, laptops and do repairs.',
          storefront.about)
    check('specialties persisted', storefront.specialties == 'Phones, Laptops, Repairs',
          storefront.specialties)
    # Opening hours are no longer asked for on this form - the field was dropped
    # from the shop-details page, so there is nothing to round-trip. The column
    # stays on the model for the older storefronts that already filled it in.

with app.test_client() as client:
    login(client, seller_id)
    html = client.get('/storefront/apply').get_data(as_text=True)
    check('saved description shown back', 'We stock phones, laptops and do repairs.' in html)
    html = client.get('/').get_data(as_text=True)
    check('nav switches to "My Shop"', 'My Shop' in html)

print('\n== hub lists products once the shop is live ==')
with app.test_client() as client:
    login(client, seller_id)
    client.post('/seller/products',
                data={'name': 'Bluetooth Speaker', 'category_id': category_id,
                      'short_description': 'Portable, 12h battery',
                      'description': 'Rugged portable speaker.',
                      'selling_price': 3500, 'buying_price': 2200, 'stock': 8,
                      'weight_kg': 1.2, 'product_condition': 'new', 'is_active': '1',
                      'location_label': 'Kimathi Street'})

    html = client.get('/seller/apply').get_data(as_text=True)
    check('hub shows the listings table', 'My listings' in html)
    check('hub lists the product', 'Bluetooth Speaker' in html)
    check('hub no longer prompts for a storefront', 'Open your storefront' not in html)

    html = client.get('/storefront/apply').get_data(as_text=True)
    check('storefront page lists the product', 'Bluetooth Speaker' in html)

print('\n== raffles copy removed ==')
with app.test_client() as client:
    login(client, seller_id)
    r = client.get('/raffles')
    check('GET /raffles renders', r.status_code == 200, r.status_code)
    html = r.get_data(as_text=True)
    check('end-conditions sentence gone', 'minimum number of different buyers' not in html)
    check('remaining copy intact', 'buyer holding the most tickets wins' in html)

print('\n== daraja settings no longer discard credentials ==')
with app.app_context():
    # The old setting_value() silently swapped these two real-looking strings
    # for defaults, so nothing ever reached Safaricom.
    formerly_discarded = '2UA9gRP6n9dejGWJDwinJekxAJYZ8ZYgyKm0bf4o7ytSnw6J'
    Setting.set('daraja_consumer_key', formerly_discarded)
    db.session.commit()
    check('real-looking key survives', main.setting_value('daraja_consumer_key') == formerly_discarded)

    Setting.set('daraja_consumer_secret', '  padded-secret\n')
    db.session.commit()
    check('whitespace stripped', main.setting_value('daraja_consumer_secret') == 'padded-secret')

    Setting.set('daraja_passkey', 'YOUR_PASSKEY_HERE')
    db.session.commit()
    check('placeholder still ignored', main.setting_value('daraja_passkey', 'fallback') == 'fallback')

    check('sandbox host', main.daraja_base_url('sandbox') == 'https://sandbox.safaricom.co.ke')
    check('production host', main.daraja_base_url('production') == 'https://api.safaricom.co.ke')
    check('sandbox till in production flagged',
          bool(main.daraja_shortcode_pairing_error('production', '174379')))
    check('sandbox till in sandbox fine',
          not main.daraja_shortcode_pairing_error('sandbox', '174379'))

print('\n== admin settings keep unsubmitted credentials ==')
with app.app_context():
    admin = User.query.filter_by(is_admin=True).first()
    admin_id = admin.id if admin else None
    Setting.set('daraja_consumer_key', 'keep-me-please')
    db.session.commit()
check('admin user exists', admin_id is not None)

if admin_id:
    with app.test_client() as client:
        login(client, admin_id)
        r = client.get('/admin/settings')
        check('settings page renders', r.status_code == 200, r.status_code)
        check('diagnostics panel present', 'Test Daraja connection' in r.get_data(as_text=True))

        # Submit a partial form that omits the Daraja fields entirely.
        client.post('/admin/settings', data={'site_name': 'SMARKAFRICA'},
                    follow_redirects=True)

    with app.app_context():
        check('credential not wiped by unrelated save',
              main.setting_value('daraja_consumer_key') == 'keep-me-please',
              main.setting_value('daraja_consumer_key'))

print('\n== stk push fails loudly instead of silently ==')
with app.app_context():
    Setting.set('daraja_env', 'sandbox')
    Setting.set('daraja_consumer_key', 'k')
    Setting.set('daraja_consumer_secret', 's')
    Setting.set('daraja_shortcode', '174379')
    Setting.set('daraja_passkey', 'p')
    Setting.set('app_base_url', 'http://localhost:5000')
    db.session.commit()

    # Stub the OAuth call: these guards sit downstream of it, and a smoke test
    # has no business calling Safaricom.
    original_token = main.daraja_token_result
    main.daraja_token_result = lambda: {'token': 'stub-token', 'error': ''}
    try:
        # http:// base URL: Safaricom drops non-HTTPS callbacks, and the old
        # code sent them anyway so the buyer just never saw a prompt.
        result = main.stk_push('254712345678', 100, 'TEST-1')
        check('http callback rejected', result.get('success') is False)
        check('error names the https requirement', 'HTTPS' in (result.get('error') or ''),
              result.get('error'))

        # https but still localhost: reachable-looking, but not from Safaricom.
        Setting.set('app_base_url', 'https://localhost:5000')
        db.session.commit()
        result = main.stk_push('254712345678', 100, 'TEST-2')
        check('https localhost callback rejected', result.get('success') is False)
        check('error names localhost', 'localhost' in (result.get('error') or '').lower(),
              result.get('error'))

        Setting.set('app_base_url', 'https://example.com')
        db.session.commit()
        result = main.stk_push('254712345678', 0, 'TEST-3')
        check('sub-KES-1 amount rejected', result.get('success') is False)
        check('error explains the amount', 'KES 1' in (result.get('error') or ''),
              result.get('error'))

        # A sandbox till configured against production must not go out.
        Setting.set('daraja_env', 'production')
        db.session.commit()
        result = main.stk_push('254712345678', 100, 'TEST-4')
        check('sandbox till on production rejected', result.get('success') is False)
        check('error names the shortcode', '174379' in (result.get('error') or ''),
              result.get('error'))
    finally:
        main.daraja_token_result = original_token

shutil.rmtree(os.path.dirname(SCRATCH_DB), ignore_errors=True)

print('\n' + '=' * 60)
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S):')
    for f in FAILURES:
        print('  - ' + f)
    sys.exit(1)
print('ALL CHECKS PASSED')
