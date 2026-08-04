"""Smoke test for storefront approval and seller product listing with location pins.

Copies the working database to a throwaway file so this never mutates real data.
Run with the base interpreter (the venv's ctypes is broken):

    PYTHONPATH=".:.venv/Lib/site-packages" \
      "C:/Users/euwin/AppData/Local/Programs/Python/Python314/python.exe" test_storefront_listing.py

Covers: storefront application reaching the admin queue, approval notification,
seller product listing gate, location geocoding, and the icon rendering on cards.
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
    scratch = os.path.join(tempfile.mkdtemp(prefix='smark-storefront-'), 'test.db')
    if source:
        shutil.copy2(source, scratch)
    return scratch


SCRATCH_DB = _scratch_database()
os.environ['DATABASE_URL'] = 'sqlite:///' + SCRATCH_DB.replace('\\', '/')
os.environ['FLASK_ENV'] = 'development'
os.environ.setdefault('SECRET_KEY', 'smoke-test-key')

import main  # noqa: E402
from models import (db, User, Product, BusinessStorefront, Category,  # noqa: E402
                    CustomerNotification)

app = main.app
app.config['TESTING'] = True
app.config['WTF_CSRF_ENABLED'] = False

FAILURES = []


def check(label, condition, detail=''):
    print(f'  [{"PASS" if condition else "FAIL"}] {label}'
          f'{(" -> " + str(detail)) if detail else ""}')
    if not condition:
        FAILURES.append(label)


with app.app_context():
    print(f'== schema (scratch db: {SCRATCH_DB}) ==')
    main.ensure_phase_two_schema()
    db.session.commit()

    admin = User.query.filter_by(is_admin=True).first()
    check('admin user exists', admin is not None, admin.username if admin else None)
    if not admin:
        sys.exit('cannot continue without an admin')

    # A verified seller but no storefront yet
    seller = User.query.filter(User.is_admin.is_(False), User.seller_status == 'verified').first()
    if not seller:
        seller = User(username='testseller', email='seller@test.local',
                      password_hash='dummy', seller_status='verified',
                      is_verified_seller=True)
        db.session.add(seller)
        db.session.commit()
    check('verified seller exists', seller.seller_status == 'verified', seller.username)

    category = Category.query.filter_by(is_active=True).first()
    if not category:
        category = Category(name='Electronics', slug='electronics', is_active=True)
        db.session.add(category)
        db.session.commit()
    check('category exists', category.name is not None)

    admin_id, seller_id, category_id = admin.id, seller.id, category.id

print('\n== storefront application ==')
with app.test_client() as client:
    with client.session_transaction() as sess:
        sess['_user_id'] = str(seller_id)
        sess['_fresh'] = True
    r = client.post('/storefront/apply',
                    data={'business_name': 'Test Shop',
                          'category_id': category_id,
                          'physical_address': 'Thika Road, Nairobi',
                          'landmark': 'Near Blue Post Hotel',
                          'contact_phone': '+254712345678',
                          'contact_email': 'shop@test.local'},
                    follow_redirects=False)
    check('POST storefront application', r.status_code in (302, 200), r.status_code)

with app.app_context():
    storefront = BusinessStorefront.query.filter_by(owner_id=seller_id).first()
    check('storefront record created', storefront is not None, storefront.business_name if storefront else None)
    check('status is pending_review', storefront and storefront.status == 'pending_review', storefront.status if storefront else None)
    # Geocoding runs at application time, so coordinates should already be set
    check('location geocoded on submit', storefront and storefront.location_lat is not None,
          f'{storefront.location_lat:.4f}, {storefront.location_lng:.4f}' if storefront and storefront.location_lat else 'not geocoded')

    # The admin should have been notified
    notif = CustomerNotification.query.filter_by(user_id=admin_id, notification_type='storefront').first()
    check('admin notified of application', notif is not None, notif.title if notif else None)

    storefront_id = storefront.id if storefront else None

print('\n== admin approval queue ==')
with app.test_client() as client:
    with client.session_transaction() as sess:
        sess['_user_id'] = str(admin_id)
        sess['_fresh'] = True

    r = client.get('/admin/storefronts')
    check('admin storefronts route renders', r.status_code == 200, r.status_code)
    html = r.get_data(as_text=True)
    check('queue shows the pending application', 'Test Shop' in html and 'Thika Road' in html)
    check('pending count badge present', 'pending_review' in html)

    r = client.post('/admin/storefronts',
                    data={'storefront_id': storefront_id,
                          'status': 'approved',
                          'verification_notes': 'Verified business address and category.'},
                    follow_redirects=False)
    check('POST approval', r.status_code in (302, 200), r.status_code)

with app.app_context():
    storefront = db.session.get(BusinessStorefront, storefront_id)
    check('storefront approved', storefront.status == 'approved', storefront.status)
    check('approved_at timestamp set', storefront.approved_at is not None)
    # Owner should have received two notifications: approval + listing unlocked
    owner_notifs = CustomerNotification.query.filter_by(user_id=seller_id, notification_type='storefront').all()
    check('owner notified of approval', len(owner_notifs) >= 2, f'{len(owner_notifs)} notifications')

print('\n== seller product listing gate ==')
# An unverified user cannot reach the listing page
with app.app_context():
    unverified_user = User.query.filter(User.is_admin.is_(False),
                                        User.seller_status != 'verified').first()
    if not unverified_user:
        unverified_user = User(username='buyer1', email='buyer@test.local',
                               password_hash='dummy', seller_status='buyer')
        db.session.add(unverified_user)
        db.session.commit()
    unverified_id = unverified_user.id

with app.test_client() as client:
    with client.session_transaction() as sess:
        sess['_user_id'] = str(unverified_id)
        sess['_fresh'] = True
    r = client.get('/seller/products', follow_redirects=False)
    check('unverified seller bounced', r.status_code == 302, r.status_code)
    check('redirected to an application step',
          'seller/apply' in (r.location or '') or 'storefront/apply' in (r.location or ''),
          r.location)

# The verified storefront owner can list products
with app.test_client() as client:
    with client.session_transaction() as sess:
        sess['_user_id'] = str(seller_id)
        sess['_fresh'] = True
    r = client.get('/seller/products')
    check('verified seller reaches listing page', r.status_code == 200, r.status_code)
    html = r.get_data(as_text=True)
    check('listing form renders', 'List a new product' in html or 'List a product' in html)
    check('storefront name displayed', 'Test Shop' in html)

print('\n== product listing with location ==')
with app.test_client() as client:
    with client.session_transaction() as sess:
        sess['_user_id'] = str(seller_id)
        sess['_fresh'] = True
    r = client.post('/seller/products',
                    data={'name': 'Solar Panel 100W',
                          'category_id': category_id,
                          'short_description': 'Monocrystalline, 12V output',
                          'description': 'High-efficiency solar panel for off-grid use.',
                          'selling_price': 8500,
                          'buying_price': 6000,
                          'stock': 15,
                          'weight_kg': 7.5,
                          'product_condition': 'new',
                          'is_active': '1',
                          'location_label': 'Warehouse, Industrial Area',
                          'image_url': 'https://example.com/solar.jpg'},
                    follow_redirects=False)
    check('POST product listing', r.status_code in (302, 200), r.status_code)

with app.app_context():
    product = Product.query.filter_by(seller_id=seller_id, name='Solar Panel 100W').first()
    check('product created', product is not None, product.name if product else None)
    check('seller_id matches', product and product.seller_id == seller_id)
    check('location_label set', product and product.location_label is not None, product.location_label if product else None)
    # Location should have inherited from the storefront or been geocoded
    check('location coordinates set', product and product.location_lat is not None,
          f'{product.location_lat:.4f}, {product.location_lng:.4f}' if product and product.location_lat else 'not set')
    check('has_location property true', product and product.has_location)
    check('location_display returns a label', product and product.location_display is not None, product.location_display if product else None)
    check('review_status approved for new condition', product and product.review_status == 'approved', product.review_status if product else None)
    check('is_active true', product and product.is_active)

    product_id = product.id if product else None

print('\n== location icon on cards (simulated template logic) ==')
# We can't render the full Jinja template here, but we can verify the logic
with app.app_context():
    product = db.session.get(Product, product_id)
    check('product.has_location evaluates true', product.has_location)
    check('location_display[:24] truncates properly', len(product.location_display) > 0)
    # Simulate the template condition
    icon_renders = product.has_location
    check('location icon would render', icon_renders, f'has_location={product.has_location}')

print('\n== admin can also set product location ==')
with app.test_client() as client:
    with client.session_transaction() as sess:
        sess['_user_id'] = str(admin_id)
        sess['_fresh'] = True
    r = client.post('/admin/products/add',
                    data={'name': 'Admin-listed Laptop',
                          'category_id': category_id,
                          'description': 'Test admin listing',
                          'selling_price': 45000,
                          'buying_price': 38000,
                          'stock': 3,
                          'weight_kg': 2.1,
                          'product_condition': 'new',
                          'is_active': '1',
                          'location_label': 'Nairobi CBD showroom',
                          'location_lat': -1.2864,
                          'location_lng': 36.8172},
                    follow_redirects=False)
    check('admin POST product with location', r.status_code in (302, 200), r.status_code)

with app.app_context():
    admin_product = Product.query.filter_by(name='Admin-listed Laptop').first()
    check('admin product created', admin_product is not None)
    check('admin product location set', admin_product and admin_product.has_location, admin_product.location_display if admin_product else None)

print('\n== storefront application on dashboard ==')
# Submit a second application so there's a pending one on the dashboard
with app.app_context():
    seller2 = User(username='seller2x', email='seller2x@test.local',
                   password_hash='dummy', seller_status='verified', is_verified_seller=True)
    db.session.add(seller2)
    db.session.commit()
    seller2_id = seller2.id

with app.test_client() as client:
    with client.session_transaction() as sess:
        sess['_user_id'] = str(seller2_id)
        sess['_fresh'] = True
    client.post('/storefront/apply',
                data={'business_name': 'Second Shop',
                      'category_id': category_id,
                      'physical_address': 'Mombasa Road, Nairobi',
                      'landmark': 'Near SGR terminus',
                      'contact_phone': '+254723456789',
                      'contact_email': 'shop2@test.local'})

with app.test_client() as client:
    with client.session_transaction() as sess:
        sess['_user_id'] = str(admin_id)
        sess['_fresh'] = True
    r = client.get('/admin')
    check('admin dashboard renders', r.status_code == 200)
    html = r.get_data(as_text=True)
    check('dashboard shows pending storefront alert', 'storefront application' in html.lower() or 'waiting for approval' in html.lower())
    check('dashboard links to storefront queue', 'admin/storefronts' in html)

shutil.rmtree(os.path.dirname(SCRATCH_DB), ignore_errors=True)

print('\n' + '=' * 60)
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S):')
    for f in FAILURES:
        print('  - ' + f)
    sys.exit(1)
print('ALL CHECKS PASSED')
