"""Smoke check for the services category: admin-brokered linking and the chatbot.

Run with: python tools/services_smoke.py

A service is not sold the way a product is. The client never receives the
provider's number: they press "Contact admin", an on-duty admin sees the request
together with the phone, and the admin introduces the two. When nobody is on duty
the client gets one exact sentence and a WhatsApp button.

The model is only as good as its guards, so these are asserted rather than trusted
to review:

  * provider_phone reaches admins and the provider themselves, and nobody else -
    not the client, not an anonymous visitor, not the listing page
  * an admin on duty produces an on-platform thread the client can read; no admin
    on duty produces the MVP's busy sentence, character for character
  * a seller may only list services the admin enabled; an admin may list any
  * a seller with no approved storefront is redirected, not refused
  * a blank address falls back to the device's coordinates; a typed address wins
  * the chatbot's provider list matches the format the MVP wrote out, and claims
    "near you" only when a county is actually held
  * the menu it offers holds services somebody is listed under, so a button cannot
    hand the client back the same "nobody is listed under that yet" reply
  * the listing fee is charged only while direct contact is switched on

What is NOT covered from here: the WhatsApp hand-off itself, which is one link to a
third party, and the linking desk templates beyond a status code. The invariant
worth this much scaffolding is who can see the number.

Two things are forced rather than read, because this runs against a real operator
database and both would otherwise decide the result: every feature flag it depends
on is pinned for the duration and restored, and duty is narrowed to exactly one
admin. An operator who happened to be on duty would otherwise turn the "nobody at
the desk" checks red for a reason that is not a bug.

Nothing printed below is non-ASCII, deliberately: the format under test uses an em
dash and the catalogue uses emoji, and a Windows console defaulting to cp1252 would
turn a passing check into a UnicodeEncodeError. Values are escaped on the way out.
"""

import contextlib
import os
import re
import sys

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import session

import main as app_module
from main import (SERVICE_BUSY_MESSAGE, app, cached_service_ids, db,
                  invalidate_service_caches, match_service_key,
                  match_service_key_scored, paginate_cached_ids,
                  pick_service_admin, service_catalogue, service_chatbot_reply,
                  service_duty_active, service_listing_fee, service_provider_line,
                  support_whatsapp_url)
from models import (BusinessStorefront, CustomerNotification, PlatformRevenue,
                    ServiceCatalogueItem, ServiceLinkMessage, ServiceLinkRequest,
                    ServiceListing, ServiceOrder, Setting, User)

# CSRF off for the same reason the four existing POST-exercising smoke scripts turn
# it off: the token is a browser concern, and every POST below would otherwise fail
# for a reason that has nothing to do with what is being checked.
app.config['WTF_CSRF_ENABLED'] = False

FAILURES = []
TAG = 'svcsmoke'

# The separator in the MVP's provider-line format.
DASH = '—'

PROVIDER_PHONE = '0790001234'
# Distinct from PROVIDER_PHONE and from the support number, so a leak check that
# passes cannot be passing because two numbers happen to match.
CLIENT_PHONE = '0711992288'

# Catalogue keys of our own, rather than toggling seller_listable on the MVP's real
# rows: that flag is admin configuration, and a smoke test that rewrites it leaves
# the platform differently configured than it found it.
OPEN_KEY = f'{TAG}_open'
CLOSED_KEY = f'{TAG}_closed'
# Enabled for sellers but never listed under, so it is the row that proves the chat
# menu offers services somebody is actually listed under rather than the catalogue.
EMPTY_KEY = f'{TAG}_empty'
# Labelled "Laundry" to match the MVP's worked example, and sorted ahead of the
# seeded row of the same name. match_service_key keeps the first best match, and
# service_catalogue() is ordered by sort_order, so a negative order makes the match
# deterministic - and because the key is ours, the only providers under it are the
# three created below. That is what lets the reply be asserted line for line
# instead of "contains".
LAUNDRY_KEY = f'{TAG}_laundry'
# Its own key so the paging check can seed more providers than fit on a page without
# pushing every other check's assertions onto page two.
PAGING_KEY = f'{TAG}_paging'
CREATED_CATALOGUE_KEYS = []


def ascii_safe(value):
    """Printable on a cp1252 console, whatever the value contains."""
    return str(value).encode('unicode_escape').decode('ascii')


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + ascii_safe(detail)) if detail else ""}')


@contextlib.contextmanager
def as_user(user_id):
    """A client signed in as one user, in an app context of its own.

    Flask-Login caches the loaded user on ``g``, which belongs to the app context,
    so one context reused across identities silently keeps the first one.
    """
    ctx = app.app_context()
    ctx.push()
    try:
        with app.test_client() as client:
            with client.session_transaction() as http_session:
                http_session['_user_id'] = str(user_id)
                http_session['_fresh'] = True
            yield client
    finally:
        db.session.remove()
        ctx.pop()


@contextlib.contextmanager
def as_anonymous():
    ctx = app.app_context()
    ctx.push()
    try:
        with app.test_client() as client:
            yield client
    finally:
        db.session.remove()
        ctx.pop()


@contextlib.contextmanager
def rate_limits_off():
    """Linking requests are capped at twenty an hour, which caps the test, not the code.

    With REDIS_URL set the cap outlives the process, so a second run inside an hour
    would start reading 429s as failures. The cap is pre-existing behaviour and not
    what is under test.
    """
    limiter = getattr(app_module, 'limiter', None)
    was_enabled = getattr(limiter, 'enabled', None)
    if limiter is not None:
        limiter.enabled = False
    try:
        yield
    finally:
        if limiter is not None and was_enabled is not None:
            limiter.enabled = was_enabled


@contextlib.contextmanager
def settings(**values):
    """Pin Setting rows for the duration, then put back exactly what was there.

    Captures the raw row rather than Setting.get, because "absent" and "present and
    equal to the default" are different states, and restoring the wrong one would
    leave the platform configured differently than it was found.
    """
    saved = {}
    for key in values:
        row = Setting.query.filter_by(key=key).first()
        saved[key] = row.value if row else None
    for key, value in values.items():
        Setting.set(key, value)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                Setting.delete(key)
            else:
                Setting.set(key, value)


@contextlib.contextmanager
def only_on_duty(user_id=None):
    """Narrow the linking desk to one admin, or empty it, then restore.

    Emptying it matters as much as filling it. pick_service_admin() takes the
    lowest id on duty, so a real admin left on duty on the operator's own database
    would both break the "nobody at the desk" checks and receive the notification
    the on-duty checks look for. Whoever was on duty goes back on afterwards.

    The duty state is cached for fifteen seconds, which is right for production and
    wrong for a test that flips it twice in a second, so the cache is cleared at
    both ends. Nothing else clears it: invalidate_service_caches deals with the
    catalogue and the id lists, not with who is at the desk.
    """
    previously_on = [row.id for row in
                     User.query.filter(User.service_duty_on.is_(True)).all()]
    for row_id in previously_on:
        db.session.get(User, row_id).service_duty_on = False
    target = db.session.get(User, user_id) if user_id else None
    if target is not None:
        target.service_duty_on = True
    db.session.commit()
    app_module._service_duty_cache.clear()
    try:
        yield
    finally:
        if target is not None:
            target.service_duty_on = False
        for row_id in previously_on:
            row = db.session.get(User, row_id)
            if row is not None:
                row.service_duty_on = True
        db.session.commit()
        app_module._service_duty_cache.clear()


def teardown():
    db.session.rollback()
    user_ids = [row[0] for row in db.session.query(User.id)
                .filter(User.username.like(f'{TAG}%')).all()] or [0]
    service_ids = [row[0] for row in db.session.query(ServiceListing.id)
                   .filter(ServiceListing.provider_id.in_(user_ids)).all()] or [0]
    request_ids = [row[0] for row in db.session.query(ServiceLinkRequest.id)
                   .filter(ServiceLinkRequest.service_id.in_(service_ids)).all()] or [0]
    # Children first: SQLite lets a dangling row through and PostgreSQL does not, so
    # a teardown that only works on one of them is a teardown that fails on deploy.
    ServiceLinkMessage.query.filter(
        ServiceLinkMessage.request_id.in_(request_ids)).delete(synchronize_session=False)
    ServiceLinkRequest.query.filter(
        ServiceLinkRequest.id.in_(request_ids)).delete(synchronize_session=False)
    ServiceOrder.query.filter(
        ServiceOrder.service_id.in_(service_ids)).delete(synchronize_session=False)
    ServiceListing.query.filter(
        ServiceListing.id.in_(service_ids)).delete(synchronize_session=False)
    BusinessStorefront.query.filter(
        BusinessStorefront.owner_id.in_(user_ids)).delete(synchronize_session=False)
    PlatformRevenue.query.filter(
        PlatformRevenue.payer_id.in_(user_ids)).delete(synchronize_session=False)
    CustomerNotification.query.filter(
        CustomerNotification.user_id.in_(user_ids)).delete(synchronize_session=False)
    User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)
    # Only the catalogue rows this script added. A row that was already there is the
    # admin's, and deleting it would take a service off the platform.
    if CREATED_CATALOGUE_KEYS:
        ServiceCatalogueItem.query.filter(
            ServiceCatalogueItem.key.in_(CREATED_CATALOGUE_KEYS)).delete(
                synchronize_session=False)
    db.session.commit()
    del CREATED_CATALOGUE_KEYS[:]
    invalidate_service_caches()
    app_module._service_duty_cache.clear()


def make_user(suffix, **fields):
    user = User(username=f'{TAG}_{suffix}', email=f'{TAG}_{suffix}@example.invalid',
                is_active=True, **fields)
    user.set_password('x')
    db.session.add(user)
    db.session.commit()
    return user


def make_catalogue(key, label, seller_listable=True, sort_order=100):
    row = ServiceCatalogueItem.query.filter_by(key=key).first()
    if row:
        return row
    row = ServiceCatalogueItem(key=key, label=label, emoji='',
                              seller_listable=seller_listable, is_active=True,
                              sort_order=sort_order)
    db.session.add(row)
    db.session.commit()
    CREATED_CATALOGUE_KEYS.append(key)
    invalidate_service_caches()
    return row


def make_listing(provider_id, title, price, key, label, orders, **fields):
    service = ServiceListing(
        provider_id=provider_id, title=title, price=price, category=label,
        service_key=key, description=f'{TAG} listing', is_active=True,
        orders_completed=orders, provider_phone=PROVIDER_PHONE,
        platform_commission=15.0, **fields)
    db.session.add(service)
    db.session.commit()
    invalidate_service_caches()
    return service


def create_post(client, **overrides):
    data = {'title': f'{TAG} New Listing', 'service_key': OPEN_KEY,
            'provider_phone': PROVIDER_PHONE, 'price': '450',
            'delivery_days': '2', 'description': 'Written by services_smoke.'}
    data.update(overrides)
    return client.post('/services/create', data=data)


# --- the checks ------------------------------------------------------------

def check_busy_sentence():
    print('the busy sentence is the wording the MVP specified')
    expected = ('All our agents are currently busy, please reach out via whatsapp '
                'for immediate action.')
    check('SERVICE_BUSY_MESSAGE matches character for character',
          SERVICE_BUSY_MESSAGE == expected, SERVICE_BUSY_MESSAGE)
    url = support_whatsapp_url('Hi')
    check('the support number resolves to a wa.me link in international form',
          url.startswith('https://wa.me/254'), url)
    check('and a prefilled message is carried on it', 'text=' in url, url)


def check_catalogue_filter():
    print('sellers see the services the admin enabled, admins see all of them')
    make_catalogue(OPEN_KEY, 'Smoketest Openlisting', seller_listable=True,
                   sort_order=900)
    make_catalogue(CLOSED_KEY, 'Smoketest Closedlisting', seller_listable=False,
                   sort_order=901)
    seller_keys = {row.key for row in service_catalogue(seller_only=True)}
    admin_keys = {row.key for row in service_catalogue()}
    check('an enabled service is offered to sellers', OPEN_KEY in seller_keys)
    check('a disabled one is not', CLOSED_KEY not in seller_keys)
    check('an admin is offered both',
          OPEN_KEY in admin_keys and CLOSED_KEY in admin_keys)
    check('the seller list is a subset, not a second query result',
          seller_keys <= admin_keys)


def check_phone_visibility(customer_id, provider_id, admin_id, service_id):
    print('the provider phone reaches admins and the provider, and nobody else')
    phone = PROVIDER_PHONE.encode()
    # Pinned off: direct contact is the one setting that legitimately puts the
    # number in front of a client, so leaving it to the operator's value would make
    # this either a real check or a tautology depending on the database.
    with settings(service_direct_contact_enabled='0'):
        with as_anonymous() as client:
            response = client.get(f'/services/{service_id}')
            check('the detail page renders for an anonymous visitor',
                  response.status_code == 200, response.status_code)
            check('and carries no provider phone', phone not in response.data)
            listing = client.get('/services')
            check('the services list renders', listing.status_code == 200,
                  listing.status_code)
            check('and carries no provider phone either', phone not in listing.data)

        with as_user(customer_id) as client:
            response = client.get(f'/services/{service_id}')
            check('a signed-in client gets the page', response.status_code == 200,
                  response.status_code)
            check('and still no provider phone', phone not in response.data)
            check('but is offered the contact-admin route instead',
                  b'contact-admin' in response.data)

        with as_user(provider_id) as client:
            response = client.get(f'/services/{service_id}')
            check('the provider sees their own number back',
                  response.status_code == 200 and phone in response.data,
                  response.status_code)

        with as_user(admin_id) as client:
            response = client.get(f'/services/{service_id}')
            check('an admin sees the number',
                  response.status_code == 200 and phone in response.data,
                  response.status_code)
            check('and is told not to pass it on',
                  b'Never share this number' in response.data)

    with settings(service_direct_contact_enabled='1'):
        with as_user(customer_id) as client:
            response = client.get(f'/services/{service_id}')
            check('turning direct contact on is what shows a client the number',
                  phone in response.data, response.status_code)
        with as_anonymous() as client:
            response = client.get(f'/services/{service_id}')
            check('and even then not to an anonymous visitor, who could be a scraper',
                  phone not in response.data)


def check_contact_admin(customer_id, stranger_id, provider_id, admin_id, service_id):
    print('contact admin: a thread when someone is on duty, the sentence when not')
    with rate_limits_off(), settings(service_requests_enabled='1',
                                     service_direct_contact_enabled='0'):
        with only_on_duty(None):
            check('duty state reads as nobody at the desk',
                  not service_duty_active() and pick_service_admin() is None)
            with as_user(customer_id) as client:
                response = client.post(f'/services/{service_id}/contact-admin',
                                       data={'note': 'Need this today',
                                             'phone': CLIENT_PHONE})
                payload = response.get_json() or {}
                check('the request is answered, not refused',
                      response.status_code == 200, response.status_code)
                check('the mode is the WhatsApp hand-off',
                      payload.get('mode') == 'whatsapp', payload.get('mode'))
                check('and the reply is the exact busy sentence',
                      payload.get('reply') == SERVICE_BUSY_MESSAGE,
                      payload.get('reply'))
                check('with a WhatsApp link to press',
                      (payload.get('whatsapp_url') or '').startswith('https://wa.me/'),
                      payload.get('whatsapp_url'))
                check('and no provider phone anywhere in the payload',
                      PROVIDER_PHONE not in response.get_data(as_text=True))
            recorded = ServiceLinkRequest.query.filter_by(
                service_id=service_id, status='whatsapp_redirected').count()
            check('the hand-off is still recorded so the MVP can count it',
                  recorded >= 1, recorded)

        with only_on_duty(admin_id):
            check('duty state reads as somebody at the desk', service_duty_active())
            check('and the request is routed to that admin',
                  pick_service_admin() == admin_id, pick_service_admin())
            with as_user(customer_id) as client:
                response = client.post(f'/services/{service_id}/contact-admin',
                                       data={'note': 'Need this today',
                                             'phone': CLIENT_PHONE})
                payload = response.get_json() or {}
                check('the mode is the on-platform thread',
                      payload.get('mode') == 'platform', payload.get('mode'))
                request_id = payload.get('request_id')
                check('and a request id comes back', bool(request_id), request_id)
                check('no provider phone in this payload either',
                      PROVIDER_PHONE not in response.get_data(as_text=True))

                again = client.post(f'/services/{service_id}/contact-admin',
                                    data={'note': 'Still need this'})
                repeat = again.get_json() or {}
                check('pressing it twice reuses the open request',
                      repeat.get('existing') is True
                      and repeat.get('request_id') == request_id, repeat)

                thread = client.get(f'/services/requests/{request_id}/thread')
                body = thread.get_json() or {}
                check('the client can read the thread',
                      thread.status_code == 200 and body.get('success') is True,
                      thread.status_code)
                check('and their own note is the first message in it',
                      any('Need this today' in (msg.get('body') or '')
                          for msg in body.get('messages') or []),
                      body.get('messages'))
                check('the thread carries no provider phone',
                      PROVIDER_PHONE not in thread.get_data(as_text=True))

            with as_user(stranger_id) as client:
                other = client.get(f'/services/requests/{request_id}/thread')
                check("another client's thread is refused outright",
                      other.status_code == 403, other.status_code)

            with as_user(provider_id) as client:
                response = client.post(f'/services/{service_id}/contact-admin')
                check('the provider cannot request a link to themselves',
                      response.status_code == 400, response.status_code)

            with as_user(admin_id) as client:
                reply = client.post(f'/services/requests/{request_id}/thread',
                                    data={'body': 'Calling the provider now.'})
                body = reply.get_json() or {}
                check('an admin replying claims the request',
                      reply.status_code == 200 and body.get('status') == 'claimed',
                      body.get('status'))
                check('and the reply is marked as coming from the desk',
                      any(msg.get('from_admin') for msg in body.get('messages') or []))

            notified = CustomerNotification.query.filter_by(
                user_id=admin_id, notification_type='service').count()
            check('the on-duty admin was notified of the request', notified >= 1,
                  notified)

        with only_on_duty(admin_id), settings(service_requests_enabled='0'):
            with as_user(customer_id) as client:
                response = client.post(f'/services/{service_id}/contact-admin')
                payload = response.get_json() or {}
                check('the kill switch sends everyone to WhatsApp even so',
                      payload.get('mode') == 'whatsapp'
                      and payload.get('reply') == SERVICE_BUSY_MESSAGE,
                      payload.get('mode'))


def check_listing_gate(seller_id, plain_id, admin_id):
    print('who may list, and what they may pick')
    with settings(service_direct_contact_enabled='0'):
        with as_user(plain_id) as client:
            response = client.get('/services/create')
            check('a buyer is redirected to seller verification, not refused',
                  response.status_code == 302, response.status_code)
            check('and pointed at the step they actually need next',
                  'seller' in (response.headers.get('Location') or ''),
                  response.headers.get('Location'))

        with as_user(seller_id) as client:
            response = client.get('/services/create')
            check('a seller with no approved storefront is redirected too',
                  response.status_code == 302, response.status_code)
            check('and sent to the storefront application',
                  'storefront' in (response.headers.get('Location') or ''),
                  response.headers.get('Location'))

        db.session.add(BusinessStorefront(
            owner_id=seller_id, business_name=f'{TAG} Shop', slug=f'{TAG}-shop',
            status='approved'))
        db.session.commit()

        with as_user(seller_id) as client:
            response = client.get('/services/create')
            check('an approved storefront opens the form',
                  response.status_code == 200, response.status_code)
            check('the form offers the enabled service',
                  OPEN_KEY.encode() in response.data)
            check('and does not offer the disabled one',
                  CLOSED_KEY.encode() not in response.data)

            blocked = create_post(client, service_key=CLOSED_KEY,
                                  title=f'{TAG} Should Not Exist')
            check('a seller posting a disabled key is re-shown the form',
                  blocked.status_code == 200, blocked.status_code)

            allowed = create_post(client, title=f'{TAG} Seller Listing')
            check('a seller posting an enabled key is redirected to the listing',
                  allowed.status_code == 302, allowed.status_code)

            no_phone = create_post(client, provider_phone='', title=f'{TAG} No Phone')
            check('a listing with no contact phone is refused',
                  no_phone.status_code == 200, no_phone.status_code)

        with as_user(admin_id) as client:
            response = create_post(client, service_key=CLOSED_KEY,
                                   title=f'{TAG} Admin Listing')
            check('an admin may list a service sellers cannot',
                  response.status_code == 302, response.status_code)

        check('nothing was created from the disabled key',
              ServiceListing.query.filter_by(
                  title=f'{TAG} Should Not Exist').count() == 0)
        check('nor from the listing with no number for an admin to call',
              ServiceListing.query.filter_by(title=f'{TAG} No Phone').count() == 0)
        row = ServiceListing.query.filter_by(title=f'{TAG} Admin Listing').first()
        check("and the admin's is recorded as an admin listing",
              row is not None and bool(row.is_admin_listing))


def check_location_rules(admin_id):
    print('a typed address wins; a blank one falls back to the device pin')
    with as_user(admin_id) as client:
        create_post(client, title=f'{TAG} Pin Only', location_label='',
                    location_lat='-1.29210', location_lng='36.82190')
        create_post(client, title=f'{TAG} Address', location_label='Ngong Road',
                    location_county='Nairobi', location_lat='-1.30000',
                    location_lng='36.80000')
    pin = ServiceListing.query.filter_by(title=f'{TAG} Pin Only').first()
    typed = ServiceListing.query.filter_by(title=f'{TAG} Address').first()
    check('a blank address keeps the coordinates the browser sent',
          pin is not None and pin.location_lat is not None
          and pin.location_lng is not None,
          pin and (pin.location_lat, pin.location_lng))
    check('and invents no address for them',
          pin is not None and pin.location_label is None, pin and pin.location_label)
    check('the pin still displays as a location',
          pin is not None and pin.has_location
          and pin.location_display == '-1.292, 36.822', pin and pin.location_display)
    check('a typed address is what is shown',
          typed is not None and typed.location_display == 'Ngong Road',
          typed and typed.location_display)
    check('and the county is kept for the near-you ordering',
          typed is not None and typed.location_county == 'Nairobi',
          typed and typed.location_county)


def check_listing_fee(seller_id, admin_id):
    print('the listing fee is charged only while direct contact is on')
    with settings(service_direct_contact_enabled='0', service_listing_fee_kes='500'):
        check('the rate is read from the admin-editable setting',
              service_listing_fee() == 500.0, service_listing_fee())
        with as_user(seller_id) as client:
            create_post(client, title=f'{TAG} Free Listing')
        row = ServiceListing.query.filter_by(title=f'{TAG} Free Listing').first()
        check('but with direct contact off nothing is charged',
              row is not None and not row.listing_fee_amount,
              row and row.listing_fee_amount)

    with settings(service_direct_contact_enabled='1', service_listing_fee_kes='500'):
        with as_user(seller_id) as client:
            create_post(client, title=f'{TAG} Paid Listing')
        row = ServiceListing.query.filter_by(title=f'{TAG} Paid Listing').first()
        check('with it on the seller is charged the admin rate',
              row is not None and row.listing_fee_amount == 500.0,
              row and row.listing_fee_amount)
        check('and it is outstanding, not silently marked paid',
              row is not None and not row.listing_fee_paid)
        revenue = PlatformRevenue.query.filter_by(
            revenue_stream='service_listing_fee', payer_id=seller_id).count()
        check('the fee is booked as platform revenue', revenue >= 1, revenue)

        with as_user(admin_id) as client:
            create_post(client, title=f'{TAG} Admin Free Listing')
        row = ServiceListing.query.filter_by(
            title=f'{TAG} Admin Free Listing').first()
        check('an admin listing is exempt from the fee',
              row is not None and not row.listing_fee_amount,
              row and row.listing_fee_amount)


def check_chatbot_format(admin_id, provider_id):
    print("the chatbot's provider list is the format the MVP wrote out")
    make_catalogue(LAUNDRY_KEY, 'Laundry', seller_listable=True, sort_order=-1)
    check('a laundry question resolves to the laundry catalogue row',
          match_service_key('I need laundry') == LAUNDRY_KEY,
          match_service_key('I need laundry'))

    expected = [
        f'CleanWash {DASH} KES 450 {DASH} '
        f'No pickup offered, take to the location as directed',
        f'Campus Laundry {DASH} KES 500 {DASH} pickup tomorrow',
        f'QuickWash {DASH} KES 550 {DASH} pickup today',
    ]
    # orders_completed is the ordering, so 3/2/1 reproduces the order the MVP wrote
    # the example in. All three carry the same county, so the near-you sort below
    # cannot reshuffle them and turn an ordering question into a format failure.
    make_listing(provider_id, 'CleanWash', 450.0, LAUNDRY_KEY, 'Laundry', 3,
                 location_county='Nairobi', location_label='Ngara')
    make_listing(provider_id, 'Campus Laundry', 500.0, LAUNDRY_KEY, 'Laundry', 2,
                 location_county='Nairobi', location_label='Kikuyu',
                 pickup_required=True, pickup_eta='tomorrow')
    make_listing(provider_id, 'QuickWash', 550.0, LAUNDRY_KEY, 'Laundry', 1,
                 location_county='Nairobi', location_label='Westlands',
                 pickup_required=True, pickup_eta='today')
    ids = cached_service_ids(LAUNDRY_KEY, '')
    titles = {row.id: row.title for row in ServiceListing.query.filter(
        ServiceListing.service_key == LAUNDRY_KEY).all()}
    # Two separate claims, so a failure says which one broke. That the cache holds
    # ids is the scaling claim - a cache of ORM rows would hand a later request
    # objects detached from a closed session. That they arrive in orders_completed
    # order is what the three-line reply below depends on, so it is asserted here
    # against the titles rather than inferred from the reply it feeds.
    check('the cache holds plain ids, not rows',
          len(ids) == 3 and all(isinstance(value, int) for value in ids),
          [type(value).__name__ for value in ids])
    check('ordered by orders_completed, which is the order the reply reads in',
          [titles.get(value) for value in ids]
          == ['CleanWash', 'Campus Laundry', 'QuickWash'],
          [titles.get(value) for value in ids])
    first = ServiceListing.query.filter_by(title='CleanWash').first()
    check('one provider line is built in one place',
          service_provider_line(first) == expected[0],
          service_provider_line(first))

    with settings(service_requests_enabled='1'):
        # No county held: the reply must not claim proximity it cannot compute.
        with only_on_duty(admin_id):
            with app.test_request_context('/support'):
                session.pop('delivery_location', None)
                payload = service_chatbot_reply('I need laundry') or {}
            lines = (payload.get('reply') or '').split('\n')
            check('a laundry question is answered by the services flow',
                  payload.get('service_key') == LAUNDRY_KEY,
                  payload.get('service_key'))
            check('the count line names the count and the service',
                  lines[:1] == ['I found 3 laundry providers.'],
                  lines[:1])
            check('the three provider lines are name, price, pickup, in that order',
                  lines[1:] == expected, lines[1:])
            check('each provider is also returned structured for the buttons',
                  len(payload.get('providers') or []) == 3
                  and (payload['providers'][0].get('url') or '').startswith(
                      '/services/'),
                  (payload.get('providers') or [{}])[0].get('url'))
            check('with the price and pickup as their own fields, not only in the line',
                  payload['providers'][0].get('price_display') == 'KES 450'
                  and payload['providers'][1].get('pickup') == 'pickup tomorrow',
                  payload['providers'][0].get('price_display'))
            check('no provider phone is anywhere in the chatbot payload',
                  PROVIDER_PHONE not in str(payload))
            check('on duty, the client is pointed at contact admin',
                  payload.get('action') == 'contact_admin', payload.get('action'))
            check('and the busy sentence is not used',
                  payload.get('follow_up') != SERVICE_BUSY_MESSAGE)

            # A county held: "near you" is earned.
            with app.test_request_context('/support'):
                session['delivery_location'] = {'county': 'Nairobi'}
                payload = service_chatbot_reply('I need laundry') or {}
            lines = (payload.get('reply') or '').split('\n')
            check('with a county held the reply says "near you"',
                  lines[:1] == ['I found 3 laundry providers near you.'], lines[:1])
            check('and the same three lines follow it', lines[1:] == expected,
                  lines[1:])

        with only_on_duty(None):
            with app.test_request_context('/support'):
                payload = service_chatbot_reply('I need laundry') or {}
            check('off duty, the action is the WhatsApp hand-off',
                  payload.get('action') == 'whatsapp', payload.get('action'))
            check('and the follow-up is the exact busy sentence',
                  payload.get('follow_up') == SERVICE_BUSY_MESSAGE,
                  payload.get('follow_up'))
            check('with the link beside it',
                  (payload.get('whatsapp_url') or '').startswith('https://wa.me/'),
                  payload.get('whatsapp_url'))
            check('and the provider list is still given, not withheld',
                  len(payload.get('providers') or []) == 3,
                  len(payload.get('providers') or []))

    print('and it asks before it lists')
    make_catalogue(EMPTY_KEY, 'Smoketest Emptylisting', seller_listable=True,
                   sort_order=902)
    with app.test_request_context('/support'):
        payload = service_chatbot_reply('what services do you have') or {}
    options = payload.get('options') or []
    keys = [row.get('key') for row in options]
    check('a bare services question offers the catalogue as buttons',
          payload.get('action') == 'pick_service' and bool(options),
          payload.get('action'))
    # Not a count: the seeded catalogue is the admin's and its size is their business.
    # What the reply owes the client is the services they can actually be linked to.
    check('a service with providers is among them', LAUNDRY_KEY in keys, keys[:8])
    check('one nobody is listed under is not, so pressing it cannot dead-end',
          EMPTY_KEY not in keys, keys[:8])
    check('and every button carries what the front end needs to render it',
          all(row.get('key') and row.get('label') and (row.get('url') or '')
              .startswith('/services') for row in options),
          options[:1])
    with app.test_request_context('/support'):
        check('a question about something else is left to the other intents',
              service_chatbot_reply('where is my order') is None)
        check('and an empty message is not answered at all',
              service_chatbot_reply('') is None)


def check_intent_boundaries():
    """The two questions that must never be answered by the services flow.

    Services are matched before every other intent, on both the chatbot route and
    the keyword fallback, so anything the matcher over-claims here is stolen from
    the intent that should have had it - and both victims are among the highest
    traffic questions on the site.

    Order tracking is the first. Three catalogue labels contain 'delivery' and one
    contains 'parcel', so "where is my parcel" used to score a match on the courier
    row and hand a client chasing a late order a list of couriers to hire instead.

    Buying a thing is the second. "I want to buy a phone" scores a single weak word
    against Phone & Laptop Repair. Answering the commonest product search on an
    African marketplace with a list of repair shops loses the sale.

    Asserted through service_chatbot_reply rather than match_service_key, because
    returning None is the behaviour that matters: it is what lets the message fall
    through to order_status or product_search.
    """
    print('an order question is never answered with a service')
    make_catalogue(LAUNDRY_KEY, 'Laundry', seller_listable=True, sort_order=-1)
    with app.test_request_context('/support'):
        for message in ('where is my parcel', 'track my order 12345',
                        'my delivery has not arrived', 'my package is late',
                        'what is my order status', 'my food delivery not delivered'):
            check(f'"{message}" is left to order tracking',
                  service_chatbot_reply(message) is None)
        # The counterweight, in the same context so a guard that simply swallowed
        # every sentence with "my" in it could not pass this function.
        check('but hiring a courier for a parcel still reaches services',
              (service_chatbot_reply('I want to hire a courier for my parcel')
               or {}).get('action') is not None)
        check('and a plain services question is untouched',
              service_chatbot_reply('I need laundry') is not None)

    print('a product search is never answered with a service')
    with app.test_request_context('/support'):
        for message in ('I want to buy a phone', 'buy a laptop',
                        'is the phone in stock', 'second hand phone for sale'):
            check(f'"{message}" is left to product search',
                  service_chatbot_reply(message) is None)
        check('a named service survives a purchase word',
              (service_chatbot_reply('where can I buy laundry services')
               or {}).get('service_key') == LAUNDRY_KEY)
        # Asserted on the matcher and the reply separately: nobody is listed under
        # device repair in this fixture, so the reply is the "nobody yet, here is
        # what is listed" payload, which carries no service_key by design.
        check('and phone repair is still a service, not a phone',
              match_service_key('I need phone repair') != ''
              and service_chatbot_reply('I need phone repair') is not None,
              match_service_key('I need phone repair'))

    print('a word shared by several services identifies none of them')
    # 'delivery' is in Food Delivery, Campus Delivery & Errands and Grocery &
    # Essentials Delivery. On its own it is not evidence for any of the three.
    check('"delivery" alone matches no catalogue row',
          match_service_key('delivery is slow') == '',
          match_service_key('delivery is slow'))
    check('but two words of one label are specific again',
          match_service_key('I need campus delivery') != '',
          match_service_key('I need campus delivery'))
    check('and the full label still wins outright',
          match_service_key_scored('food delivery please')[1] >= 100,
          match_service_key_scored('food delivery please'))

    print('every catalogue service is reachable by name')
    # The chatbot flow is ask -> list catalogue -> client picks -> list providers.
    # A row the matcher cannot reach is a button the client can press and a
    # sentence they can type that the chatbot then denies exists.
    #
    # Compared by label, not by key: this fixture deliberately seeds a second row
    # labelled 'Laundry' to prove the tie-break, so requiring a specific key here
    # would fail on the fixture rather than on the matcher.
    labels = {row.key: (row.label or '').strip().lower()
              for row in service_catalogue()}
    unreachable = []
    for key, label in labels.items():
        spoken = re.sub(r'[^a-z0-9\s]+', ' ', label).strip()
        got = match_service_key(f'I need {spoken}')
        if labels.get(got) != label:
            unreachable.append(f'{key} -> {got or "(nothing)"}')
    check('typing a service name reaches that service',
          not unreachable, unreachable)


def check_services_pagination(provider_id):
    print('the services page survives more providers than fit on one page')
    make_catalogue(PAGING_KEY, 'Smoketest Paging', seller_listable=True,
                   sort_order=902)
    # Thirteen against a per_page of twelve. Twelve or fewer and services.html skips
    # its whole paginator block, so a broken paginator looks perfectly healthy - the
    # thirteenth listing is the entire point of this check.
    for index in range(13):
        make_listing(provider_id, f'{TAG} Paged {index}', 100.0 + index, PAGING_KEY,
                     'Smoketest Paging', index)

    ids = cached_service_ids(PAGING_KEY, '')
    check('all thirteen listings reach the cached id list', len(ids) == 13, len(ids))
    pagination = paginate_cached_ids(ServiceListing, ids, page=1, per_page=12)
    check('which is more than one page of them', pagination.pages == 2,
          pagination.pages)
    check('twelve on the first page', len(pagination.items) == 12,
          len(pagination.items))

    # The exact call services.html makes. iter_pages first shipped taking no
    # arguments at all, so this raised TypeError inside the render and /services
    # answered 500 the moment a thirteenth provider signed up. Asserting the
    # keywords rather than just "it paginates" is what pins that down.
    pages = []
    try:
        pages = list(pagination.iter_pages(left_edge=1, right_edge=1,
                                           left_current=1, right_current=1))
        accepted, detail = True, pages
    except TypeError as exc:
        accepted, detail = False, exc
    check('iter_pages takes the keyword arguments the template passes', accepted,
          detail)
    check('and yields both page numbers', [p for p in pages if p] == [1, 2], pages)

    last = paginate_cached_ids(ServiceListing, ids, page=2, per_page=12)
    check('one on the second page', len(last.items) == 1, len(last.items))
    check('and the pages do not overlap',
          not ({row.id for row in pagination.items} & {row.id for row in last.items}))

    # rate_limits_off because the global default is 50 a minute against one shared
    # test-client address, and with REDIS_URL set the bucket outlives the process.
    # A 429 here would read as "/services is broken" on the second run of the hour.
    with rate_limits_off(), as_anonymous() as client:
        for page in (1, 2):
            response = client.get(f'/services?service={PAGING_KEY}&page={page}')
            check(f'/services renders page {page} anonymously',
                  response.status_code == 200, response.status_code)
        # Stale links and crawlers supply both of these against a public URL, so
        # neither may 500. paginate_cached_ids clamps rather than raising.
        for bad in ('99', 'abc'):
            response = client.get(f'/services?service={PAGING_KEY}&page={bad}')
            check(f'/services survives ?page={bad}', response.status_code == 200,
                  response.status_code)


def run():
    check_busy_sentence()
    check_catalogue_filter()

    provider = make_user('provider', seller_status='verified',
                         is_verified_seller=True)
    customer = make_user('customer', phone=CLIENT_PHONE)
    stranger = make_user('stranger')
    seller = make_user('seller', seller_status='verified', is_verified_seller=True)
    plain = make_user('plain')
    admin = make_user('admin', is_admin=True, admin_level='super_admin')
    service = make_listing(provider.id, f'{TAG} Ironing', 300.0, OPEN_KEY,
                           'Smoketest Openlisting', 0, location_label='Ngong Road',
                           location_county='Nairobi')
    provider_id, customer_id, stranger_id = provider.id, customer.id, stranger.id
    seller_id, plain_id, admin_id = seller.id, plain.id, admin.id
    service_id = service.id

    check_phone_visibility(customer_id, provider_id, admin_id, service_id)
    check_contact_admin(customer_id, stranger_id, provider_id, admin_id, service_id)
    check_listing_gate(seller_id, plain_id, admin_id)
    check_location_rules(admin_id)
    check_listing_fee(seller_id, admin_id)
    check_chatbot_format(admin_id, provider_id)
    # After check_chatbot_format, which is what seeds the laundry providers these
    # boundary checks need in order to prove a services question still works.
    check_intent_boundaries()
    # Last, because it adds a catalogue row and thirteen listings. Run earlier it
    # would be perturbing the very counts and line-for-line replies the checks
    # above assert.
    check_services_pagination(provider_id)


def main():
    with app.app_context():
        teardown()
        try:
            run()
        finally:
            teardown()
    print()
    if FAILURES:
        print(f'{len(FAILURES)} check(s) failed: {", ".join(FAILURES)}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
