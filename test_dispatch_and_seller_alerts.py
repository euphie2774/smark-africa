"""Smoke test for the dispatch tracking, seller alerts and layout fixes.

Copies the working database to a throwaway file so this never mutates real data.
Run with the base interpreter (the venv's ctypes is broken):

    PYTHONPATH=".:.venv/Lib/site-packages" \
      "C:/Users/euwin/AppData/Local/Programs/Python/Python314/python.exe" \
      test_dispatch_and_seller_alerts.py

Covers: driver breadcrumb trails on the dispatch map, the un-clipped active
deliveries table, seller listing/sale notifications, the admin users role
filter, and the CSS rules behind the badge and mobile-grid fixes.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.abspath(__file__))


def _scratch_database():
    """Clone the dev database so the test can write freely."""
    candidates = [
        os.path.join(REPO, 'instance', 'smarkafrica.db'),
        os.path.join(REPO, 'smarkafrica.db'),
    ]
    source = next((p for p in candidates if os.path.exists(p)), None)
    scratch = os.path.join(tempfile.mkdtemp(prefix='smark-dispatch-'), 'test.db')
    if source:
        shutil.copy2(source, scratch)
    return scratch


SCRATCH_DB = _scratch_database()
os.environ['DATABASE_URL'] = 'sqlite:///' + SCRATCH_DB.replace('\\', '/')
os.environ['FLASK_ENV'] = 'development'
os.environ.setdefault('SECRET_KEY', 'smoke-test-key')

import main  # noqa: E402
from models import (db, User, Product, Category, Order, OrderItem,  # noqa: E402
                    DriverProfile, DriverLocationPing, DeliveryAssignment,
                    CustomerNotification)

app = main.app
app.config['TESTING'] = True
app.config['WTF_CSRF_ENABLED'] = False

FAILURES = []
SENT_EMAILS = []


def check(label, condition, detail=''):
    print(f'  [{"PASS" if condition else "FAIL"}] {label}'
          f'{(" -> " + str(detail)) if detail else ""}')
    if not condition:
        FAILURES.append(label)


def login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def read(path):
    with open(os.path.join(REPO, path), encoding='utf-8') as handle:
        return handle.read()


# Nothing in a smoke test has any business reaching Resend or an SMTP host.
main.send_email = lambda to, subject, html: SENT_EMAILS.append((to, subject, html)) or True

with app.app_context():
    print(f'== fixtures (scratch db: {SCRATCH_DB}) ==')
    main.ensure_phase_two_schema()
    db.session.commit()

    category = Category.query.filter_by(is_active=True).first()
    if not category:
        category = Category(name='Electronics', slug='electronics', is_active=True)
        db.session.add(category)
        db.session.commit()
    category_id = category.id

    admin = User.query.filter_by(is_admin=True).first()
    if not admin:
        admin = User(username='dispatchadmin', email='dispatchadmin@test.local',
                     password_hash='dummy', is_admin=True, admin_level='mvp')
        db.session.add(admin)
        db.session.commit()
    admin_id = admin.id

    seller = User(username='trailseller', email='trailseller@test.local',
                  password_hash='dummy', seller_status='verified',
                  is_verified_seller=True)
    buyer = User(username='trailbuyer', email='trailbuyer@test.local',
                 password_hash='dummy')
    driver_user = User(username='trailrider', email='trailrider@test.local',
                       password_hash='dummy')
    db.session.add_all([seller, buyer, driver_user])
    db.session.commit()
    seller_id, buyer_id = seller.id, buyer.id

    product = Product(name='Trail Test Speaker', slug='trail-test-speaker',
                      description='Portable speaker.', selling_price=3500,
                      buying_price=2200, stock=6, category_id=category_id,
                      seller_id=seller_id, is_active=True, review_status='approved',
                      location_label='Kimathi Street')
    db.session.add(product)
    db.session.commit()
    product_id = product.id

    driver = DriverProfile(user_id=driver_user.id, display_name='Trail Rider',
                           phone='+254700000001', vehicle_type='motorbike',
                           vehicle_registration='KDA 999T', is_active=True,
                           tracking_token='trail-token-smoke')
    db.session.add(driver)
    db.session.commit()
    driver_id = driver.id

    order = Order(order_number='TRAIL-0001', user_id=buyer_id, amount_paid=3500,
                  payment_status='completed', shipping_city='Nairobi',
                  shipping_country='Kenya', shipping_status='processing')
    db.session.add(order)
    db.session.commit()
    db.session.add(OrderItem(order_id=order.id, product_id=product_id,
                             product_name=product.name, price=3500, quantity=1))
    db.session.commit()
    order_id = order.id

    assignment = DeliveryAssignment(order_id=order_id, driver_id=driver_id,
                                    status='in_transit', destination_lat=-1.2921,
                                    destination_lng=36.8219,
                                    destination_label='Nairobi CBD')
    db.session.add(assignment)

    # Three breadcrumbs on this delivery, one stale one from an old shift, and
    # one belonging to no order at all.
    now = datetime.utcnow()
    driver.last_lat, driver.last_lng = -1.2700, 36.8000
    driver.last_ping_at = now
    db.session.add_all([
        DriverLocationPing(driver_id=driver_id, order_id=order_id, lat=-1.2500,
                           lng=36.7800, speed_kph=31.0, created_at=now - timedelta(minutes=8)),
        DriverLocationPing(driver_id=driver_id, order_id=order_id, lat=-1.2600,
                           lng=36.7900, speed_kph=27.0, created_at=now - timedelta(minutes=4)),
        DriverLocationPing(driver_id=driver_id, order_id=order_id, lat=-1.2700,
                           lng=36.8000, speed_kph=22.0, created_at=now - timedelta(minutes=1)),
        DriverLocationPing(driver_id=driver_id, order_id=order_id, lat=-9.9,
                           lng=9.9, created_at=now - timedelta(hours=9)),
        DriverLocationPing(driver_id=driver_id, order_id=None, lat=-8.8,
                           lng=8.8, created_at=now - timedelta(minutes=2)),
    ])
    db.session.commit()

print('\n== driver_trail() ==')
with app.app_context():
    trail = main.driver_trail(driver_id, order_id)
    check('three in-window points for this order', len(trail) == 3, len(trail))
    check('oldest first', trail == sorted(trail, key=lambda p: p['at']),
          [p['at'] for p in trail])
    check('ends on the latest fix', trail and trail[-1]['lat'] == -1.2700, trail[-1] if trail else None)
    check('carries speed for the movement readout', trail and trail[-1]['speed_kph'] == 22.0)
    check('the 9-hour-old ping is outside the window',
          all(p['lat'] != -9.9 for p in trail))
    check('another order\'s ping is excluded', all(p['lat'] != -8.8 for p in trail))

    unscoped = main.driver_trail(driver_id)
    check('unscoped trail also picks up the orderless ping',
          any(p['lat'] == -8.8 for p in unscoped), len(unscoped))

    windowed = main.driver_trail(driver_id, order_id, minutes=2)
    check('minutes window narrows the trail', len(windowed) == 1, len(windowed))

    capped = main.driver_trail(driver_id, order_id, limit=2)
    check('limit keeps the newest points', [p['lat'] for p in capped] == [-1.2600, -1.2700],
          [p['lat'] for p in capped])

print('\n== /api/dispatch/drivers carries the trail ==')
with app.test_client() as client:
    login(client, admin_id)
    r = client.get('/api/dispatch/drivers')
    check('endpoint renders', r.status_code == 200, r.status_code)
    payload = r.get_json() or {}
    entry = next((d for d in payload.get('drivers', []) if d['id'] == driver_id), None)
    check('the live driver is in the payload', entry is not None)
    if entry:
        check('assignment attached', (entry.get('assignment') or {}).get('order_number') == 'TRAIL-0001',
              entry.get('assignment'))
        check('trail attached', len(entry.get('trail') or []) == 3, len(entry.get('trail') or []))
        check('latest speed surfaced', entry.get('speed_kph') == 22.0, entry.get('speed_kph'))
        check('position still present', entry.get('lat') == -1.2700)

    idle = next((d for d in payload.get('drivers', [])
                 if d['id'] != driver_id and not d.get('assignment')), None)
    if idle is not None:
        check('idle drivers carry no trail', 'trail' not in idle, list(idle.keys()))
    else:
        print('  [skip] no idle driver in this database to compare against')

print('\n== dispatch page markup ==')
with app.test_client() as client:
    login(client, admin_id)
    r = client.get('/admin/dispatch')
    check('dispatch page renders', r.status_code == 200, r.status_code)
    html = r.get_data(as_text=True)
    check('order number shown', 'TRAIL-0001' in html)
    check('follow-on-map button rendered', f'data-track="{driver_id}"' in html)
    check('live position slot rendered', f'data-live="{driver_id}"' in html)
    check('cells carry stacking labels', 'data-label="Destination"' in html
          and 'data-label="Move to"' in html)
    check('action column no longer nowrap-inline',
          'text-end text-nowrap' not in html)
    check('status buttons wrap instead of overflowing',
          'dispatch-actions' in html and 'd-flex flex-wrap gap-1' in html)
    check('trail layer code shipped', 'updateTrail' in html and 'line-cap' in html)
    check('map waits for style load before adding sources', "map.on('load'" in html)
    check('legend explains the path', 'Path the phone has travelled' in html)

print('\n== seller is told their listing went live ==')
with app.app_context():
    SENT_EMAILS.clear()
    fresh = Product(name='Alert Test Lamp', slug='alert-test-lamp',
                    description='Desk lamp.', selling_price=1200, buying_price=700,
                    stock=4, category_id=category_id, seller_id=seller_id,
                    is_active=True, review_status='approved',
                    location_label='Kimathi Street')
    db.session.add(fresh)
    db.session.commit()

    check('first call notifies', main.notify_seller_listing_live(fresh) is True)
    note = CustomerNotification.query.filter_by(
        user_id=seller_id, notification_type='seller_listing').first()
    check('in-app note written', note is not None, note.title if note else None)
    check('note names the product', note and 'Alert Test Lamp' in note.title)
    check('one email sent', len(SENT_EMAILS) == 1, len(SENT_EMAILS))
    check('email addressed to the seller', SENT_EMAILS and SENT_EMAILS[0][0] == 'trailseller@test.local')
    check('email names the product', SENT_EMAILS and 'Alert Test Lamp' in SENT_EMAILS[0][1])

    # A double submit must not mail the seller twice.
    check('repeat call is a no-op', main.notify_seller_listing_live(fresh) is False)
    check('still only one email', len(SENT_EMAILS) == 1, len(SENT_EMAILS))

    pending = Product(name='Pending Review Fan', slug='pending-review-fan',
                      description='Fan.', selling_price=900, buying_price=500,
                      stock=2, category_id=category_id, seller_id=seller_id,
                      is_active=True, review_status='pending')
    db.session.add(pending)
    db.session.commit()
    main.notify_seller_listing_live(pending)
    check('pending listing says it awaits review',
          any('Awaiting admin review' in mail[2] for mail in SENT_EMAILS))

print('\n== seller is told when the item sells ==')
with app.app_context():
    SENT_EMAILS.clear()
    sold_order = db.session.get(Order, order_id)
    notified = main.notify_sellers_of_sale(sold_order)
    check('one seller notified', notified == 1, notified)
    check('sale email sent', len(SENT_EMAILS) == 1, len(SENT_EMAILS))
    if SENT_EMAILS:
        to, subject, body = SENT_EMAILS[0]
        check('sale email goes to the seller', to == 'trailseller@test.local', to)
        check('subject names the order', 'TRAIL-0001' in subject, subject)
        check('body shows the earning after commission', 'after commission' in body)
        check('body names the product', 'Trail Test Speaker' in body)

    sale_note = CustomerNotification.query.filter_by(
        user_id=seller_id, notification_type='seller_sale').first()
    check('in-app sale note written', sale_note is not None,
          sale_note.title if sale_note else None)

    # A callback and a status poll both land here; the second must stay quiet.
    repeat = main.notify_sellers_of_sale(sold_order)
    check('repeat callback notifies nobody', repeat == 0, repeat)
    check('no duplicate email', len(SENT_EMAILS) == 1, len(SENT_EMAILS))

print('\n== admin users page splits sellers from customers ==')
with app.test_client() as client:
    login(client, admin_id)
    r = client.get('/admin/users?role=sellers')
    check('sellers tab renders', r.status_code == 200, r.status_code)
    html = r.get_data(as_text=True)
    check('seller listed', 'trailseller' in html)
    check('plain buyer excluded', 'trailbuyer' not in html)

    html = client.get('/admin/users?role=customers').get_data(as_text=True)
    check('customers tab lists the buyer', 'trailbuyer' in html)
    check('customers tab excludes the seller', 'trailseller' not in html)

    html = client.get('/admin/users').get_data(as_text=True)
    check('everyone tab lists both', 'trailseller' in html and 'trailbuyer' in html)
    check('brand toggle posts to the brand route', 'admin_toggle_brand_seller' in html
          or '/admin/users/brand/' in html)
    check('brand button is a real submit', 'Unbrand' in html or '>Brand' in html)
    check('actions are a wrapping flex, not a btn-group',
          'btn-group btn-group-sm' not in html)
    check('role column present', '>Role<' in html and 'Listings' in html)

    html = client.get('/admin/users?role=admins').get_data(as_text=True)
    check('admins tab renders', 'nav-pills' in html)

print('\n== css: badges, grid and rating block ==')
css = read(os.path.join('static', 'style.css'))
seal = css[css.index('.authenticity-seal {'):]
seal = seal[:seal.index('}')]
check('seal pinned bottom-left', 'bottom: 6px' in seal and 'left: 6px' in seal, seal.strip())
check('seal never stretches down the image', 'top: auto' in seal)
check('seal is small', 'font-size: 0.58rem' in seal)
check('the two seals stack instead of colliding',
      '.authenticity-seal:not(.verified-seller-seal) ~ .verified-seller-seal' in css)

phone = css[css.index('@media (max-width: 640px)'):]
check('two products across on a phone',
      'grid-template-columns: repeat(2, minmax(0, 1fr))' in phone)
check('shop keeps a left category rail', 'grid-template-columns: 88px minmax(0, 1fr)' in phone)
check('rating block trimmed on a phone', '.feedback-strip .star-rating span' in phone)
check('rating blurb hidden rather than wrapping',
      '.feedback-strip p {\n        display: none;' in phone)

tablet = css[css.index('@media (max-width: 992px)'):css.index('@media (max-width: 640px)')]
check('shop rail survives the tablet breakpoint too',
      'grid-template-columns: 132px minmax(0, 1fr)' in tablet)

print('\n== dispatch stylesheet ==')
dispatch_html = read(os.path.join('templates', 'admin', 'dispatch.html'))
check('table stacks below the xl breakpoint', '@media (max-width: 991.98px)' in dispatch_html)
check('stacked cells print their column name', 'content: attr(data-label)' in dispatch_html)
check('action cell allowed to wrap', 'td.dispatch-actions { white-space: normal; }' in dispatch_html)

shutil.rmtree(os.path.dirname(SCRATCH_DB), ignore_errors=True)

print('\n' + '=' * 60)
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S):')
    for f in FAILURES:
        print('  - ' + f)
    sys.exit(1)
print('ALL CHECKS PASSED')
