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
# The settings dict is cached for 60s in a FileSystemCache under .cache, which
# outlives the process - a previous run could otherwise hand this one stale
# toggles. Tests get no cache at all.
os.environ['CACHE_TYPE'] = 'NullCache'
os.environ.setdefault('SECRET_KEY', 'smoke-test-key')

import main  # noqa: E402
from models import (db, User, Product, Category, Order, OrderItem,  # noqa: E402
                    DriverProfile, DriverLocationPing, DeliveryAssignment,
                    CustomerNotification, BusinessStorefront)

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

    # The hand-off copy has to agree with how tracking now works: the driver
    # opens the link once and carries on using the phone.
    handoff = SENT_SMS[-1][1]
    check('the text no longer tells the driver to sit on the page',
          'page open' not in handoff, handoff)
    check('the text says they can keep using the phone',
          'using your phone' in handoff, handoff)

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

print('\n== social ads only leave on paid, eligible requests ==')
with app.app_context():
    # Three would-be advertisers: a verified seller, a plain customer, and a
    # customer whose only credential is an approved storefront.
    storefront_user = User(username='frontonly', email='frontonly@test.local',
                           password_hash='dummy')
    db.session.add(storefront_user)
    db.session.commit()
    db.session.add(BusinessStorefront(owner_id=storefront_user.id,
                                      business_name='Front Only Traders',
                                      slug='front-only-traders', status='approved'))
    db.session.commit()

    seller_obj = db.session.get(User, seller_id)
    buyer_obj = db.session.get(User, buyer_id)
    check('a verified seller may ask', main.social_ad_request_error(seller_obj) == '',
          main.social_ad_request_error(seller_obj))
    check('an approved storefront may ask too',
          main.social_ad_request_error(storefront_user) == '',
          main.social_ad_request_error(storefront_user))
    check('a plain customer may not', main.social_ad_request_error(buyer_obj) != '')
    check('and is told why', 'verified' in main.social_ad_request_error(buyer_obj).lower())

    base = main.public_base_url()
    product_obj = db.session.get(Product, product_id)
    link = main.website_ad_link(product_obj)
    check('the ad link is absolute, not localhost',
          link.startswith(base) and 'localhost' not in link, link)
    check('and lands on the product page here', product_obj.slug in link, link)

    paid = main.AdCampaign(seller_id=seller_id, product_id=product_id,
                           platform='Facebook / Instagram', budget=500,
                           total_charged=500, placement='social',
                           destination_url=link, ad_copy='Loud and portable.',
                           status='active')
    unpaid = main.AdCampaign(seller_id=seller_id, product_id=product_id,
                             platform='TikTok', budget=500, total_charged=500,
                             placement='social', destination_url=link,
                             ad_copy='Still unpaid.', status='pending_payment')
    ineligible = main.AdCampaign(seller_id=buyer_id, product_id=product_id,
                                 platform='TikTok', budget=500, total_charged=500,
                                 placement='social', destination_url=link,
                                 ad_copy='Not a seller.', status='active')
    db.session.add_all([paid, unpaid, ineligible])
    db.session.commit()
    paid_id, unpaid_id, ineligible_id = paid.id, unpaid.id, ineligible.id

    check('a paid request from a verified seller is clear',
          main.social_ad_blocker(paid) == '', main.social_ad_blocker(paid))
    check('an unpaid request is blocked', 'paid' in main.social_ad_blocker(unpaid).lower(),
          main.social_ad_blocker(unpaid))
    check('a paid request from a non-seller is still blocked',
          main.social_ad_blocker(ineligible) != '', main.social_ad_blocker(ineligible))
    # A zero-charge row on a seller account must not read as "free, go ahead".
    unpaid.total_charged = 0
    check('a zero total does not make a seller request free',
          main.social_ad_campaign_is_paid(unpaid) is False)
    unpaid.total_charged = 500
    db.session.commit()

with app.test_client() as client:
    login(client, admin_id)
    html = client.get('/admin/social-ads').get_data(as_text=True)
    check('the queue renders', 'Paid requests waiting' in html)
    check('the paid request is offered for composing',
          f'campaign_id={paid_id}' in html, paid_id)
    check('the unpaid one is not', f'campaign_id={unpaid_id}' not in html, unpaid_id)
    check('nor is the non-seller one', f'campaign_id={ineligible_id}' not in html)
    check('both sit in the ineligible list instead', 'Not yet eligible' in html)

    # The gate has to hold on the route itself, not only in the markup: a
    # hand-typed URL is exactly how an unpaid ad would otherwise slip out.
    r = client.get(f'/admin/social-ads/new?campaign_id={unpaid_id}')
    check('composing an unpaid campaign is refused', r.status_code == 302, r.status_code)
    r = client.get(f'/admin/social-ads/new?campaign_id={ineligible_id}')
    check('composing a non-seller campaign is refused', r.status_code == 302, r.status_code)
    r = client.get(f'/admin/social-ads/new?campaign_id={paid_id}')
    check('composing a paid seller campaign is allowed', r.status_code == 200, r.status_code)
    compose = r.get_data(as_text=True)
    check('the caption is seeded with the website link',
          main.public_base_url() in compose, None)
    check('the destination is shown read-only, not typed',
          'id="adLink"' in compose and 'readonly' in compose)
    check('no free-text destination field survives',
          'name="destination_url"' not in compose)

with app.app_context():
    # Saving must append the link even when an admin trims it out of the caption.
    with app.test_request_context('/admin/social-ads/new', method='POST', data={
        'platform': 'instagram', 'caption': 'No link in here at all.',
        'product_id': str(product_id), 'status': 'draft',
    }):
        post = main.SocialAdPost(campaign_id=paid_id)
        error = main.apply_social_ad_form(post, db.session.get(main.AdCampaign, paid_id))
        check('the draft saves', error is None, error)
        check('the website link is appended for the admin',
              main.public_base_url() in (post.caption or ''), post.caption)

with app.test_client() as client:
    login(client, admin_id)
    with app.app_context():
        blocked_post = main.SocialAdPost(campaign_id=unpaid_id, platform='tiktok',
                                         caption='Unpaid draft.', status='draft')
        db.session.add(blocked_post)
        db.session.commit()
        blocked_post_id = blocked_post.id
    r = client.post(f'/admin/social-ads/{blocked_post_id}/mark-posted',
                    data={'posted_url': 'https://tiktok.com/@smarkafrica/1'})
    check('an unpaid draft cannot be recorded as live', r.status_code == 302, r.status_code)
    with app.app_context():
        again = db.session.get(main.SocialAdPost, blocked_post_id)
        check('and its status is untouched', again.status == 'draft', again.status)
        check('with no live URL stored', not again.posted_url, again.posted_url)

print('\n== the seller ads page ==')
with app.app_context():
    main.Setting.set('seller_ads_enabled', '1')
with app.test_client() as client:
    login(client, seller_id)
    r = client.get('/seller/ads')
    check('a verified seller reaches the page', r.status_code == 200, r.status_code)
    html = r.get_data(as_text=True)
    check('the product choice is mandatory',
          'name="product_id"' in html and 'required' in html)
    check('the destination is shown, not asked for',
          'name="destination_url"' not in html and main.public_base_url() in html)
    check('social placement is offered', 'SMARKAFRICA social accounts' in html)

    login(client, buyer_id)
    r = client.get('/seller/ads')
    check('a plain customer is turned away', r.status_code == 302, r.status_code)
    check('and sent to apply', '/seller/apply' in (r.headers.get('Location') or ''),
          r.headers.get('Location'))

print('\n== the driver keeps tracking off the console page ==')
with app.app_context():
    # The rotate-token check above changed it, so read the live one.
    driver_token = db.session.get(DriverProfile, driver_id).tracking_token
with app.test_client() as client:
    r = client.get(f'/driver/{driver_token}')
    check('the console renders', r.status_code == 200, r.status_code)
    console = r.get_data(as_text=True)

    # A blanket geolocation=() would kill watchPosition without so much as a
    # permission prompt, so the header has to open up on exactly these pages.
    policy = r.headers.get('Permissions-Policy') or ''
    check('the console is allowed to read the device position',
          'geolocation=(self)' in policy, policy)
    home_policy = client.get('/').headers.get('Permissions-Policy') or ''
    check('but the rest of the site still is not',
          'geolocation=()' in home_policy, home_policy)
    check('and the microphone stays shut everywhere',
          'microphone=()' in policy and 'microphone=()' in home_policy)

    # What the user asked to be gone, and nothing put in its place.
    check('no instruction to stay on the page',
          'Keep this page open' not in console and 'Leave sharing on' not in console)
    check('no statusLine element left behind', 'id="statusLine"' not in console)
    check('no replacement sentence under the button',
          console.split('id="shareBtn"')[1].split('</button>')[1].strip().startswith('<div class="row'))

    # Tracking survives the driver switching apps.
    check('sharing state is remembered between visits', 'localStorage' in console
          and 'SHARE_KEY' in console)
    check('and resumes by itself on load', 'function resumeSharing' in console
          and 'resumeSharing()' in console)
    check('nothing tears sharing down on unload', 'beforeunload' not in console)
    check('the screen wake lock is requested', "navigator.wakeLock.request('screen')" in console)
    check('and re-taken when the page comes back',
          'visibilitychange' in console and 'armWatch();' in console)
    check('fixes are buffered rather than dropped', 'BUFFER_KEY' in console)
    check('and flushed with a beacon when the tab is hidden',
          'navigator.sendBeacon' in console and 'pagehide' in console)
    check('a lost fix does not stop sharing, only a refusal does',
          'err.code === 1' in console)

    # The travelled line.
    check('the trail is seeded server-side', 'const TRAIL' in console
          and '-1.27' in console.split('const TRAIL')[1].split(';')[0])
    check('drawn as a line layer', "id: 'trail', type: 'line'" in console)
    check('and extended as new fixes arrive', 'function pushTrail' in console)
    check('jitter while parked is ignored', 'TRAIL_MIN_M' in console)

print('\n== batched pings rebuild an unbroken line ==')
with app.test_client() as client:
    with app.app_context():
        before = DriverLocationPing.query.filter_by(driver_id=driver_id).count()
    # Stamped after the newest fixture breadcrumb (now - 1 min) so these three
    # are unambiguously the tail of the line.
    stamp = datetime.utcnow() - timedelta(seconds=45)
    r = client.post(f'/api/driver/{driver_token}/ping', json={'points': [
        {'lat': -1.2710, 'lng': 36.8010, 'accuracy_m': 12, 'speed_kph': 20.0,
         'at': (stamp).isoformat() + 'Z'},
        {'lat': -1.2720, 'lng': 36.8020, 'accuracy_m': 9, 'speed_kph': 24.0,
         'at': (stamp + timedelta(seconds=15)).isoformat() + 'Z'},
        {'lat': -1.2730, 'lng': 36.8030, 'accuracy_m': 8, 'speed_kph': 26.0,
         'at': (stamp + timedelta(seconds=30)).isoformat() + 'Z'},
    ]})
    check('the batch is accepted', r.status_code == 200, r.status_code)
    payload = r.get_json()
    check('all three fixes stored', payload.get('accepted') == 3, payload.get('accepted'))
    with app.app_context():
        after = DriverLocationPing.query.filter_by(driver_id=driver_id).count()
        check('three new breadcrumbs on record', after - before == 3, after - before)
        moved = db.session.get(DriverProfile, driver_id)
        # The dispatch map reads the denormalised position, so it must follow the
        # newest fix in the batch and not the first one written.
        check('the profile follows the newest fix in the batch',
              round(moved.last_lat, 4) == -1.2730, moved.last_lat)
        replayed = main.driver_trail(driver_id, order_id)
        check('the trail replays in travel order, not arrival order',
              [round(p['lat'], 4) for p in replayed][-3:] == [-1.2710, -1.2720, -1.2730],
              [round(p['lat'], 4) for p in replayed][-3:])

    # A single fix, the shape an older client sends, still works.
    r = client.post(f'/api/driver/{driver_token}/ping',
                    json={'lat': -1.2740, 'lng': 36.8040})
    check('a lone fix is still accepted', r.status_code == 200
          and r.get_json().get('accepted') == 1, r.get_json())
    # Junk must not land in the trail as a point in the Gulf of Guinea.
    r = client.post(f'/api/driver/{driver_token}/ping',
                    json={'points': [{'lat': 'x', 'lng': None}]})
    check('unusable coordinates are rejected', r.status_code == 400, r.status_code)
    r = client.post('/api/driver/unknown-token-xyz/ping', json={'lat': -1.0, 'lng': 36.0})
    check('an unknown token gets nothing', r.status_code == 404, r.status_code)

with app.app_context():
    now_utc = datetime.utcnow()
    check('a phone clock an hour behind is trusted',
          main.parse_client_timestamp((now_utc - timedelta(hours=1)).isoformat()) is not None)
    check('but next week is not', main.parse_client_timestamp(
        (now_utc + timedelta(days=7)).isoformat()) is None)
    check('nor is last year', main.parse_client_timestamp(
        (now_utc - timedelta(days=365)).isoformat()) is None)
    check('nor is nonsense', main.parse_client_timestamp('yesterday afternoon') is None)
    check('a missing timestamp falls back to server time',
          main.parse_client_timestamp(None) is None)
    check('the batch is bounded', main.DRIVER_PING_BATCH_LIMIT <= 200,
          main.DRIVER_PING_BATCH_LIMIT)

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
