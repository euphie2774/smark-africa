"""Smoke test for distance-based shipping pricing, dispatch and live tracking.

Copies the working database to a throwaway file first, so this never mutates
real orders. Run with the base interpreter - the venv's ctypes is broken:

    PYTHONPATH=".:.venv/Lib/site-packages" \
      "C:/Users/euwin/AppData/Local/Programs/Python/Python314/python.exe" test_shipping_tracking.py

Covers: zone seeding, the flat-vs-per-km fee split, the driver GPS ingest,
the public tracking payload, both admin consoles, and the delivery lifecycle.
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
    scratch = os.path.join(tempfile.mkdtemp(prefix='smark-track-'), 'probe.db')
    if source:
        shutil.copy2(source, scratch)
    return scratch


SCRATCH_DB = _scratch_database()
os.environ['DATABASE_URL'] = 'sqlite:///' + SCRATCH_DB.replace('\\', '/')
os.environ['FLASK_ENV'] = 'development'
os.environ.setdefault('SECRET_KEY', 'smoke-test-key')

import main  # noqa: E402
from models import (db, User, Order, DriverProfile, DeliveryAssignment,  # noqa: E402
                    ShippingZone, TrackingUpdate)

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
    main.seed_shipping_zones()
    db.session.commit()
    check('zones seeded', ShippingZone.query.count() >= 2,
          f'{ShippingZone.query.count()} zones')

    print('\n== fee engine: flat central region vs per-km elsewhere ==')
    # Every central county prices flat regardless of how far it actually is.
    for county in ['Nairobi', 'Kiambu', "Murang'a", 'Nyeri', 'Kirinyaga', 'Nyandarua']:
        q = main.quote_shipping(county=county, city=county, weight_kg=1)
        check(f'{county}: KSh {q["total_amount"]:.2f} over {q["distance_km"]:.0f} km',
              q['pricing_mode'] == 'flat' and q['total_amount'] == 120.0)

    for city in ['Nakuru', 'Kisumu', 'Mombasa', 'Mandera']:
        q = main.quote_shipping(county=city, city=city, weight_kg=1)
        expected = round(max(120.0, q['distance_km'] * 3), 2)
        check(f'{city}: KSh {q["total_amount"]:.2f} over {q["distance_km"]:.0f} km',
              q['pricing_mode'] == 'per_km' and abs(q['total_amount'] - expected) < 0.02,
              f'expected {expected}')

    # A short hop outside the flat zone still cannot go below the floor.
    q = main.quote_shipping(county='Machakos', city='Athi River', weight_kg=1)
    check('minimum fee floors a short out-of-zone hop',
          q['total_amount'] >= 120.0, f'KSh {q["total_amount"]:.2f}')

    # An address nothing can geocode must not produce a free delivery.
    q = main.quote_shipping(county='', city='qqzzxx nowhere', weight_kg=1)
    check('ungeocodable destination falls back to the floor',
          q['total_amount'] >= 120.0 and not q['geocoded'], f'KSh {q["total_amount"]:.2f}')

    print('\n== fixtures ==')
    admin = User.query.filter_by(is_admin=True).first()
    check('admin user exists', admin is not None, admin.username if admin else None)
    if admin is None:
        sys.exit('cannot continue without an admin account')

    driver_user = User.query.filter(User.is_admin.is_(False)).first() or admin
    profile = DriverProfile.query.filter_by(user_id=driver_user.id).first()
    if not profile:
        import secrets
        profile = DriverProfile(
            user_id=driver_user.id, display_name='Smoke Test Driver',
            phone='+254700000000', vehicle_type='motorbike',
            vehicle_registration='KDA 001A', is_active=True, status='available',
            tracking_token=secrets.token_urlsafe(32)[:64])
        db.session.add(profile)
        db.session.commit()
    check('driver profile', profile.id is not None)

    order = Order.query.first()
    if order is None:
        order = Order(user_id=driver_user.id, total_amount=2500, status='processing',
                      payment_status='completed', shipping_status='processing',
                      shipping_city='Nakuru', shipping_state='Nakuru',
                      shipping_country='Kenya', shipping_address='12 Kenyatta Ave')
        db.session.add(order)
    order.payment_status = 'completed'
    order.shipping_status = 'processing'
    order.shipping_city = order.shipping_city or 'Nakuru'
    order.shipping_state = order.shipping_state or 'Nakuru'
    order.shipping_country = 'Kenya'
    db.session.commit()
    check('order fixture', order.order_number is not None, order.order_number)

    token, order_number, order_id, admin_id = (
        profile.tracking_token, order.order_number, order.id, admin.id)
    driver_id = profile.id

print('\n== admin assigns the delivery ==')
with app.test_client() as client:
    with client.session_transaction() as sess:
        sess['_user_id'] = str(admin_id)
        sess['_fresh'] = True
    r = client.post('/admin/dispatch/assign',
                    data={'order_id': order_id, 'driver_id': driver_id},
                    follow_redirects=False)
    check('POST assign', r.status_code in (302, 200), r.status_code)

with app.app_context():
    assignment = main.active_assignment_for_order(order_id)
    check('assignment created', assignment is not None,
          assignment.destination_label if assignment else None)
    check('destination snapshotted', assignment and assignment.destination_lat is not None)
    check('driver marked on delivery',
          DriverProfile.query.get(driver_id).status == 'on_delivery')
    assignment_id = assignment.id

print('\n== driver GPS ingest (token auth, no session) ==')
with app.test_client() as client:
    r = client.post(f'/api/driver/{token}/ping',
                    json={'lat': -0.9, 'lng': 36.2, 'accuracy_m': 12.5, 'speed_kph': 48})
    body = r.get_json() or {}
    check('POST ping accepted', r.status_code == 200 and body.get('ok'), body)
    check('ping recomputes a live ETA',
          bool(body.get('assignment') and body['assignment'].get('eta_minutes') is not None),
          body.get('assignment'))
    check('rejects out-of-range coords',
          client.post(f'/api/driver/{token}/ping',
                      json={'lat': 999, 'lng': 0}).status_code == 400)
    check('rejects a missing payload',
          client.post(f'/api/driver/{token}/ping', json={}).status_code == 400)
    check('rejects an unknown token',
          client.post('/api/driver/bogus/ping',
                      json={'lat': -1, 'lng': 36}).status_code == 404)

    r = client.get(f'/driver/{token}')
    check('GET driver console', r.status_code == 200, r.status_code)
    check('console wires up the ping loop', '/ping' in r.get_data(as_text=True))
    check('unknown driver console 404s', client.get('/driver/bogus').status_code == 404)

print('\n== public tracking payload ==')
with app.test_client() as client:
    # Before pickup the driver is doing something else; their position is not
    # the customer's business and must stay hidden.
    data = (client.get(f'/api/track/{order_number}')).get_json() or {}
    check('position withheld before pickup', data.get('driver') is None, data.get('driver'))
    check('but the ETA is already shown',
          data.get('eta_minutes') is not None, data.get('eta_minutes'))

    r = client.post(f'/api/driver/{token}/status',
                    json={'assignment_id': assignment_id, 'status': 'in_transit'})
    check('driver marks in transit', r.status_code == 200, r.get_json())

    data = (client.get(f'/api/track/{order_number}')).get_json() or {}
    check('position exposed once in transit', data.get('driver') is not None, data.get('driver'))
    check('carries an ETA', data.get('eta_minutes') is not None, data.get('eta_minutes'))
    check('withholds the driver phone number',
          'phone' not in (data.get('driver') or {}))
    check('withholds the driver name', 'name' not in (data.get('driver') or {}))
    check('unknown order 404s',
          client.get('/api/track/SAF-00000000-DEADBEEF').status_code == 404)

print('\n== quote + geocode apis ==')
with app.test_client() as client:
    r = client.post('/api/shipping/quote', json={'city': 'Kisumu', 'weight_kg': 2})
    q = r.get_json() or {}
    check('POST quote returns a priced breakdown',
          r.status_code == 200 and q.get('total_amount') and q.get('explanation'),
          f'KSh {q.get("total_amount")}')
    # The legacy checkout endpoint must keep its original key.
    r = client.post('/api/shipping-cost', json={'city': 'Kisumu', 'weight': 2})
    check('legacy /api/shipping-cost still returns shipping_cost',
          r.status_code == 200 and 'shipping_cost' in (r.get_json() or {}), r.get_json())
    r = client.get('/api/geo/search?q=Eldoret')
    check('geocoder resolves a town', len((r.get_json() or {}).get('results', [])) > 0)

print('\n== admin consoles render ==')
with app.test_client() as client:
    with client.session_transaction() as sess:
        sess['_user_id'] = str(admin_id)
        sess['_fresh'] = True

    html = client.get('/admin/shipping/zones').get_data(as_text=True)
    check('zones console renders', 'shipping_flat_fee' in html and 'Kirinyaga' in html)

    html = client.get('/admin/dispatch').get_data(as_text=True)
    check('dispatch board renders', 'maplibre-gl' in html and f'/driver/{token}' in html)

    drivers = (client.get('/api/dispatch/drivers').get_json() or {}).get('drivers', [])
    check('dispatch api reports a fresh fix', any(d.get('is_fresh') for d in drivers))

    html = client.get(f'/track/{order_id}').get_data(as_text=True)
    check('customer tracking page mounts the map',
          'trackMap' in html and f'/api/track/{order_number}' in html)

print('\n== delivery lifecycle ==')
with app.test_client() as client:
    r = client.post(f'/api/driver/{token}/status',
                    json={'assignment_id': assignment_id, 'status': 'delivered'})
    check('driver marks it delivered', r.status_code == 200, r.get_json())

with app.app_context():
    a = db.session.get(DeliveryAssignment, assignment_id)
    o = db.session.get(Order, order_id)
    check('assignment closed', a.status == 'delivered' and a.delivered_at is not None)
    check('order mirrored to delivered', o.shipping_status == 'delivered', o.shipping_status)
    check('driver freed for the next job',
          db.session.get(DriverProfile, a.driver_id).status == 'available')
    check('customer-visible update written',
          TrackingUpdate.query.filter_by(order_id=order_id).count() > 0)

with app.test_client() as client:
    data = (client.get(f'/api/track/{order_number}')).get_json() or {}
    check('position hidden once delivered', data.get('driver') is None, data.get('driver'))

shutil.rmtree(os.path.dirname(SCRATCH_DB), ignore_errors=True)

print('\n' + '=' * 55)
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S):')
    for f in FAILURES:
        print('  - ' + f)
    sys.exit(1)
print('ALL CHECKS PASSED')
