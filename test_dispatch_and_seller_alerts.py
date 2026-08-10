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
SENT_SMS = []


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
# Same for Africa's Talking. Recorded rather than discarded so the test can prove
# the driver link went out on the fallback channel too.
main.send_sms_notification = lambda phone, message: SENT_SMS.append((phone, message)) or True

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
    # Somebody who has just opened their link: reporting a position, carrying no
    # delivery yet. Watching this first walk is how an admin confirms the link
    # they sent actually works, so the map has to show it.
    solo_user = User(username='sololrider', email='sololrider@test.local',
                     password_hash='dummy')
    db.session.add(solo_user)
    db.session.commit()
    solo = DriverProfile(user_id=solo_user.id, display_name='Solo Rider',
                         phone='0722000002', vehicle_type='motorbike',
                         vehicle_registration='KDB 111S', is_active=True,
                         tracking_token='solo-token-smoke')
    db.session.add_all([driver, solo])
    db.session.commit()
    driver_id = driver.id
    solo_id = solo.id

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
        # Solo Rider: two points, no order on either.
        DriverLocationPing(driver_id=solo_id, order_id=None, lat=-1.3100,
                           lng=36.8400, speed_kph=12.0, created_at=now - timedelta(minutes=6)),
        DriverLocationPing(driver_id=solo_id, order_id=None, lat=-1.3050,
                           lng=36.8450, speed_kph=15.0, created_at=now - timedelta(minutes=2)),
    ])
    solo.last_lat, solo.last_lng = -1.3050, 36.8450
    solo.last_ping_at = now
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

    # The whole point of sending a link: the first walk shows on the map before
    # anyone has been given a delivery.
    unassigned = next((d for d in payload.get('drivers', []) if d['id'] == solo_id), None)
    check('the just-linked driver is in the payload', unassigned is not None)
    if unassigned:
        check('no delivery yet', not unassigned.get('assignment'), unassigned.get('assignment'))
        check('but the movement is still sent',
              len(unassigned.get('trail') or []) == 2, len(unassigned.get('trail') or []))
        check('with the latest speed', unassigned.get('speed_kph') == 15.0,
              unassigned.get('speed_kph'))

    nofix = next((d for d in payload.get('drivers', []) if d.get('lat') is None), None)
    if nofix is not None:
        check('a driver with no fix carries no trail', 'trail' not in nofix, list(nofix.keys()))
    else:
        print('  [skip] every driver in this database has a fix')

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
    check('trail no longer gated on being assigned',
          'if (driver.trail && driver.trail.length) updateTrail(driver);' in html)
    refresh_body = html[html.index('async function refreshDrivers()'):
                        html.index('function copyLink(')]
    check('the clip detector re-runs once the live cells have widened the table',
          'markClippedTables();' in refresh_body)

print('\n== active deliveries table is no longer boxed into a narrow column ==')
with app.test_client() as client:
    login(client, admin_id)
    html = client.get('/admin/dispatch').get_data(as_text=True)
    row_open = html.index('<div class="row g-4">')
    card_at = html.index('<!-- Active deliveries')
    check('deliveries card sits after the two-column row closes', card_at > row_open)
    check('only the map and driver list share the row',
          html.count('col-xl-8', row_open, card_at) == 1
          and html.count('col-xl-4', row_open, card_at) == 1,
          (html.count('col-xl-8', row_open, card_at),
           html.count('col-xl-4', row_open, card_at)))
    check('the deliveries card itself is in no column',
          # From the card element, not the comment above it - that comment
          # explains the old col-xl-8 and would match on its own words.
          'col-xl' not in html[html.index('<div class="card', card_at):
                               html.index('dispatch-table', card_at)])
    check('overflow is announced rather than silent',
          'dispatch-scroll' in html and 'data-scroll-hint' in html)
    check('the hint says which way to scroll', 'Scroll sideways for the last columns' in html)

print('\n== the driver is handed their tracking link ==')
with app.app_context():
    SENT_EMAILS.clear()
    SENT_SMS.clear()
    rider = db.session.get(DriverProfile, driver_id)

    check('number normalised to international digits',
          main.whatsapp_number('0722000002') == '254722000002',
          main.whatsapp_number('0722000002'))
    share = main.whatsapp_share_url(rider.phone, 'hello there')
    check('share link points at the driver\'s own number',
          share.startswith('https://wa.me/254700000001?text='), share)
    check('the message rides in the link', 'hello%20there' in share, share)
    check('no phone means no share link', main.whatsapp_share_url('', 'hi') == '')

    # Unconfigured is the default state of a fresh install: it must decline
    # honestly so the caller falls back instead of claiming a send.
    check('unconfigured WhatsApp refuses rather than pretends',
          main.send_whatsapp_message(rider.phone, 'hi') is False)

print('\n== the Cloud API request itself ==')
with app.app_context():
    # Configure it and intercept the HTTP call, so the payload Meta would receive
    # is checked without a single packet leaving the machine.
    from models import Setting
    Setting.set('whatsapp_access_token', 'test-token-abc')
    Setting.set('whatsapp_phone_number_id', '111222333')
    db.session.commit()

    CALLS = []

    class FakeResponse:
        status_code = 200
        content = b'{"messages":[{"id":"wamid.TEST"}]}'
        ok = True

        def json(self):
            return {'messages': [{'id': 'wamid.TEST'}]}

    real_post = main.requests.post
    main.requests.post = lambda url, **kw: (CALLS.append((url, kw)), FakeResponse())[1]
    try:
        sent = main.send_whatsapp_message('0722000002', 'ping the rider')
        check('a configured send reports success', sent is True)
        check('exactly one HTTP call', len(CALLS) == 1, len(CALLS))
        url, kw = CALLS[0]
        check('posts to the graph messages endpoint',
              url == 'https://graph.facebook.com/v21.0/111222333/messages', url)
        check('bearer token attached',
              kw['headers']['Authorization'] == 'Bearer test-token-abc')
        body = kw['json']
        check('addressed to the normalised number', body['to'] == '254722000002', body['to'])
        check('free-form text when no template is set', body['type'] == 'text', body['type'])
        check('the link previews in the chat', body['text']['preview_url'] is True)
        check('the message body is carried', body['text']['body'] == 'ping the rider')

        # Cold contacts outside the 24h service window need an approved template,
        # or Meta rejects the send with 131047.
        Setting.set('whatsapp_template_name', 'driver_link')
        db.session.commit()
        CALLS.clear()
        main.send_whatsapp_message('0722000002', 'ping the rider')
        body = CALLS[0][1]['json']
        check('a configured template switches the payload', body['type'] == 'template', body['type'])
        check('template named', body['template']['name'] == 'driver_link')
        check('language defaults to en', body['template']['language']['code'] == 'en')
        check('the message rides in the body params',
              body['template']['components'][0]['parameters'][0]['text'] == 'ping the rider')

        # A refusal must not be reported as a send.
        class Refused(FakeResponse):
            status_code = 400
            ok = False
            content = b'{"error":{"code":131047}}'

            def json(self):
                return {'error': {'code': 131047}}

        main.requests.post = lambda url, **kw: Refused()
        check('a refused send returns False',
              main.send_whatsapp_message('0722000002', 'nope') is False)

        def explode(url, **kw):
            raise RuntimeError('network down')

        main.requests.post = explode
        check('a network failure returns False rather than raising',
              main.send_whatsapp_message('0722000002', 'nope') is False)
    finally:
        main.requests.post = real_post
        Setting.set('whatsapp_access_token', '')
        Setting.set('whatsapp_phone_number_id', '')
        Setting.set('whatsapp_template_name', '')
        db.session.commit()

with app.app_context():
    rider = db.session.get(DriverProfile, driver_id)

    message = main.driver_link_message(rider, 'created')
    check('the message carries the console link',
          rider.tracking_token in message, message[:80])
    # A link texted to a handset has to be reachable from that handset, so it is
    # built on the configured public base URL, not on whatever host the admin
    # happened to be browsing.
    check('the link is absolute and not localhost',
          'http' in message and 'localhost' not in message,
          [ln for ln in message.splitlines() if 'http' in ln])
    check('the link is built off the public base URL',
          main.public_base_url() in main.driver_tracking_url(rider),
          main.driver_tracking_url(rider))
    check('the message greets the driver by name', 'Trail Rider' in message)
    check('the message says to keep it private', 'Keep the link to yourself' in message)
    check('a driver with no token gets no message',
          main.driver_link_message(DriverProfile(display_name='Tokenless')) == '')

    result = main.notify_driver_tracking_link(rider, 'created')
    check('a share link always comes back', result['share_url'].startswith('https://wa.me/'))
    check('the in-app note was written', result['in_app'] is True)
    check('the email went out', result['email'] is True)
    check('SMS is the fallback that always fires', result['sms'] is True, SENT_SMS)
    check('WhatsApp honestly reports not-configured', result['whatsapp'] is False)

    note = CustomerNotification.query.filter_by(
        user_id=rider.user_id, notification_type='driver_link').first()
    check('the note is on the driver\'s account', note is not None,
          note.title if note else None)
    check('one email, to the driver',
          len(SENT_EMAILS) == 1 and SENT_EMAILS[0][0] == 'trailrider@test.local',
          SENT_EMAILS[0][0] if SENT_EMAILS else None)
    check('the email links the console', SENT_EMAILS and rider.tracking_token in SENT_EMAILS[0][2])
    check('the SMS went to the normalised number',
          SENT_SMS and SENT_SMS[0][0] == '254700000001', SENT_SMS[0][0] if SENT_SMS else None)

    rotated = main.driver_link_message(rider, 'rotated')
    check('a regenerated link says so', 'regenerated' in rotated)

print('\n== creating a driver sends the link straight away ==')
with app.app_context():
    # The save route attaches a profile to an existing account, so the account
    # has to be there first.
    db.session.add(User(username='freshrider', email='freshrider@test.local',
                        password_hash='dummy'))
    db.session.commit()

with app.test_client() as client:
    login(client, admin_id)
    SENT_EMAILS.clear()
    SENT_SMS.clear()
    r = client.post('/admin/dispatch/drivers/save', data={
        'username': 'freshrider', 'display_name': 'Brand New Rider',
        'phone': '0733000003', 'vehicle_type': 'motorbike',
        'vehicle_registration': 'KDC 222N', 'is_active': '1',
    }, follow_redirects=False)
    check('saving redirects back to dispatch', r.status_code == 302, r.status_code)
    check('the new driver is flagged for the share hand-off',
          'share=' in (r.headers.get('Location') or ''), r.headers.get('Location'))
    check('the link was texted to the number just entered',
          any(s[0] == '254733000003' for s in SENT_SMS), SENT_SMS)
    check('and emailed to the account',
          any(m[0] == 'freshrider@test.local' for m in SENT_EMAILS), SENT_EMAILS)

with app.app_context():
    made = DriverProfile.query.filter_by(display_name='Brand New Rider').first()
    check('the driver was created with a token', made is not None and bool(made.tracking_token))
    check('the SMS carried that very token',
          made and SENT_SMS and made.tracking_token in SENT_SMS[0][1])

    # An edit that changes nothing about reachability must not re-spam.
    SENT_SMS.clear()

with app.test_client() as client:
    login(client, admin_id)
    r = client.post('/admin/dispatch/drivers/save', data={
        'driver_id': str(made.id), 'username': 'freshrider',
        'display_name': 'Brand New Rider', 'phone': '0733000003',
        'vehicle_type': 'pickup', 'vehicle_registration': 'KDC 222N', 'is_active': '1',
    }, follow_redirects=False)
    check('editing an unchanged phone sends nothing again', len(SENT_SMS) == 0, SENT_SMS)

    # A phone arriving late means the link was minted with nowhere to send it.
    r = client.post('/admin/dispatch/drivers/save', data={
        'driver_id': str(made.id), 'username': 'freshrider',
        'display_name': 'Brand New Rider', 'phone': '0744000004',
        'vehicle_type': 'pickup', 'vehicle_registration': 'KDC 222N', 'is_active': '1',
    }, follow_redirects=False)
    check('a changed phone gets the link',
          any(s[0] == '254744000004' for s in SENT_SMS), SENT_SMS)

print('\n== resend and regenerate both reach the driver ==')
with app.test_client() as client:
    login(client, admin_id)
    SENT_SMS.clear()
    r = client.post(f'/admin/dispatch/drivers/{driver_id}/send-link')
    check('resend redirects to the driver\'s card', r.status_code == 302
          and f'share={driver_id}' in (r.headers.get('Location') or ''),
          r.headers.get('Location'))
    check('resend actually sent something', len(SENT_SMS) == 1, SENT_SMS)

    with app.app_context():
        old_token = db.session.get(DriverProfile, driver_id).tracking_token
    SENT_SMS.clear()
    r = client.post(f'/admin/dispatch/drivers/{driver_id}/rotate-token')
    check('regenerate redirects to the card too', r.status_code == 302)
    with app.app_context():
        new_token = db.session.get(DriverProfile, driver_id).tracking_token
    check('the token really changed', new_token != old_token)
    check('the new link was sent, not left stranded',
          SENT_SMS and new_token in SENT_SMS[0][1], len(SENT_SMS))

print('\n== the dispatch page offers the one-tap hand-off ==')
with app.test_client() as client:
    login(client, admin_id)
    html = client.get(f'/admin/dispatch?share={driver_id}&wa=1').get_data(as_text=True)
    check('a WhatsApp button per driver', f'data-whatsapp="{driver_id}"' in html)
    check('it uses the brand icon', 'fab fa-whatsapp' in html)
    check('it opens a wa.me link for that number', 'https://wa.me/254700000001' in html)
    check('resend and regenerate are both offered',
          f'/admin/dispatch/drivers/{driver_id}/send-link' in html
          and f'/admin/dispatch/drivers/{driver_id}/rotate-token' in html)
    check('the card just linked is highlighted',
          'data-just-linked="1"' in html and 'driver-highlight' in html)
    check('the browser is told to offer the hand-off', 'const SHARE_PROMPT = true' in html)

    html = client.get('/admin/dispatch').get_data(as_text=True)
    check('no hand-off prompt on a plain visit', 'const SHARE_PROMPT = false' in html)
    # The selector itself lives in the page script, so look for the attribute
    # actually stamped on a card.
    check('and no card is highlighted', 'data-just-linked="1"' not in html)

print('\n== the map is actually allowed to render ==')
with app.test_client() as client:
    login(client, admin_id)
    r = client.get('/admin/dispatch')
    csp = dict(
        (part.strip().split(' ')[0], part.strip())
        for part in (r.headers.get('Content-Security-Policy') or '').split(';')
        if part.strip()
    )
    check('a CSP is being sent at all', bool(csp), r.headers.get('Content-Security-Policy'))
    # maplibre-gl.css is what absolutely-positions the canvas, markers and
    # popups. Blocked, the map still "loads" but its children fall into normal
    # flow and land on top of the tables below.
    check('the map stylesheet host is allowed',
          'unpkg.com' in csp.get('style-src', ''), csp.get('style-src'))
    check('the map script host is allowed',
          'unpkg.com' in csp.get('script-src', ''), csp.get('script-src'))
    # MapLibre decodes tiles in a worker built from a blob: URL.
    check('blob: workers permitted', 'blob:' in csp.get('worker-src', ''), csp.get('worker-src'))
    # Tiles are fetched over XHR, so img-src alone is not enough.
    check('the tile host is reachable over XHR',
          'tile.openstreetmap.org' in csp.get('connect-src', ''), csp.get('connect-src'))
    check('same-origin XHR still allowed', "'self'" in csp.get('connect-src', ''))
    # Regression guard: these were dropped once while reworking the policy.
    check('fonts still allowed', 'fonts.gstatic.com' in csp.get('font-src', ''), csp.get('font-src'))
    check('product images still allowed', 'https:' in csp.get('img-src', ''), csp.get('img-src'))
    check('framing still denied', "'none'" in csp.get('frame-ancestors', ''))

    html = r.get_data(as_text=True)
    check('the map box clips its own children',
          '#map {' in html and 'overflow: hidden;' in html)
    check('following a driver resizes before flying',
          'map.resize();' in html and 'map.flyTo(' in html)
    check('the scroll target clears the sticky navbar',
          'navbar.sticky-top' in html and 'scrollIntoView' not in html.split('function trackDriver')[1].split('async function')[0])

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
check('table stacks below the xl breakpoint, where 7 columns stop fitting',
      '@media (max-width: 1199.98px)' in dispatch_html)
check('stacked cells print their column name', 'content: attr(data-label)' in dispatch_html)
check('action cell allowed to wrap', 'td.dispatch-actions { white-space: normal; }' in dispatch_html)
check('a clipped table shades its edge instead of hiding silently',
      '.dispatch-scroll.is-clipped::after' in dispatch_html)

print('\n== WhatsApp settings are admin-editable ==')
with app.test_client() as client:
    login(client, admin_id)
    html = client.get('/admin/settings').get_data(as_text=True)
    check('settings page renders', 'whatsapp_access_token' in html)
    for field in ('whatsapp_phone_number_id', 'whatsapp_template_name',
                  'whatsapp_template_language'):
        check(f'{field} field present', field in html)
    check('the token is masked', 'name="whatsapp_access_token"' in html
          and 'type="password"' in html)
    check('the page explains the wa.me fallback', 'wa.me' in html or 'one-tap' in html)
check('the access token is redacted from the audit log',
      'whatsapp_access_token' in main.SENSITIVE_AUDIT_FIELDS)

shutil.rmtree(os.path.dirname(SCRATCH_DB), ignore_errors=True)

print('\n' + '=' * 60)
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S):')
    for f in FAILURES:
        print('  - ' + f)
    sys.exit(1)
print('ALL CHECKS PASSED')
