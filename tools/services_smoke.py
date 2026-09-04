"""Smoke check for the services category: fulfilment profiles, linking, tickets.

Run with: python tools/services_smoke.py

A service is not sold the way a product is, and the eighteen services are not sold
the same way as each other. Each category carries a fulfilment profile - ticket,
dropoff, errand, visit, session, tenancy - and the profile decides which questions a
provider is asked, which facts a client is shown, and where the money goes. A ticket
is bought outright at a tier price; a laundry drop-off is paid on the platform; a
barber is paid in the chair.

The model is only as good as its guards, so these are asserted rather than trusted
to review:

  * provider_phone reaches admins and the provider themselves, and nobody else -
    not the client, not an anonymous visitor, not the listing page
  * a field a profile does not have does not appear as a negative. A ticket page
    contains no occurrence of the word "pickup" at all - not "no pickup offered",
    which is the exact complaint the profiles were built to answer
  * the client's answer to "contact admin" is byte-identical with an admin on duty
    and with the desk empty, so nobody can tell from the reply whether anyone was
    working. That is the whole of "a customer will not know"
  * an empty desk still records the request, still hands it to the support WhatsApp
    line, and still notifies the provider - with no button pressed and nothing to
    monitor - and notifying twice sends once
  * a seller may only list services the admin enabled; an admin may list any
  * a blank address falls back to the device's coordinates; a typed address wins
  * money: a ticket purchase creates a pending order with a Daraja checkout id and
    no revenue, the callback is the only thing that marks it paid, a replayed
    callback changes nothing, and a directly-paid profile creates no order at all
  * the chatbot's provider list matches the format the MVP wrote out, and claims
    "near you" only when a county is actually held
  * the menu it offers holds services somebody is listed under, so a button cannot
    hand the client back the same "nobody is listed under that yet" reply
  * the listing fee is charged only while direct contact is switched on

What is NOT covered from here: the live STK push, which needs a public HTTPS
callback and real Daraja credentials - stk_push is faked and the callback is driven
directly, and this file says so rather than implying otherwise. Nor the WhatsApp
hand-off itself, which is one link to a third party.

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
from datetime import datetime, timedelta

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from flask import session

import main as app_module
from main import (SERVICE_CLIENT_WELCOME_MESSAGE,
                  SERVICE_PROVIDER_INTEREST_MESSAGE, app, cached_service_ids, db,
                  invalidate_service_caches, match_service_key,
                  match_service_key_scored, notify_service_provider,
                  paginate_cached_ids, pick_service_admin, seed_profile_for,
                  service_catalogue, service_chatbot_reply, service_duty_active,
                  service_listing_fee, service_order_for_checkout_id,
                  service_price_display, service_provider_line,
                  support_whatsapp_url)
from models import (SERVICE_FULFILMENT_PROFILES, SERVICE_PROFILE_BY_KEY,
                    BusinessStorefront, CustomerNotification, PlatformRevenue,
                    ServiceCatalogueItem, ServiceLinkMessage, ServiceLinkRequest,
                    ServiceListing, ServiceOrder, ServicePriceTier, Setting, User,
                    service_profile_spec)

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
# One key per profile whose shape is asserted. Ours rather than the seeded
# events_tickets / barber_beauty rows for the same reason as the others: those are
# the operator's configuration, and a test that lists providers under them leaves
# the real services page holding smoke-test listings if a teardown is ever missed.
TICKET_KEY = f'{TAG}_tickets'
VISIT_KEY = f'{TAG}_visit'
SESSION_KEY = f'{TAG}_session'
TENANCY_KEY = f'{TAG}_tenancy'
CREATED_CATALOGUE_KEYS = []

# A fake Daraja response, so the money checks can run with no credentials and no
# network. stk_push is replaced for the duration; the callback is then driven
# directly against this id, which is exactly how far a local run can honestly go.
FAKE_CHECKOUT_ID = f'ws_CO_{TAG}_0001'


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
    # Before the listings, and by service_id rather than through the cascade: the
    # listing delete below is a bulk query, and a bulk delete does not run
    # delete-orphan.
    ServicePriceTier.query.filter(
        ServicePriceTier.service_id.in_(service_ids)).delete(synchronize_session=False)
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


def make_catalogue(key, label, seller_listable=True, sort_order=100,
                   profile='dropoff'):
    row = ServiceCatalogueItem.query.filter_by(key=key).first()
    if row:
        return row
    row = ServiceCatalogueItem(key=key, label=label, emoji='',
                              seller_listable=seller_listable, is_active=True,
                              sort_order=sort_order, fulfilment_profile=profile)
    db.session.add(row)
    db.session.commit()
    CREATED_CATALOGUE_KEYS.append(key)
    invalidate_service_caches()
    return row


def make_listing(provider_id, title, price, key, label, orders, **fields):
    # The profile is copied onto the listing rather than read through the catalogue,
    # which is what the app itself does - so a fixture that set only the catalogue
    # row would be testing a shape no real listing has.
    fields.setdefault('fulfilment_profile',
                      (ServiceCatalogueItem.query.filter_by(key=key).first()
                       or ServiceCatalogueItem()).fulfilment_profile or 'dropoff')
    # create_service writes pay_to/pay_when from the profile spec at insert time
    # (main.py:20834). Leaving them on the column defaults here would give a
    # barber's listing pay_to='platform' and make the direct-pay checks below
    # assert against a shape the real route never produces.
    spec = service_profile_spec(fields['fulfilment_profile'])
    fields.setdefault('pay_to', spec['pay_to'])
    fields.setdefault('pay_when', spec['pay_when'])
    service = ServiceListing(
        provider_id=provider_id, title=title, price=price, category=label,
        service_key=key, description=f'{TAG} listing', is_active=True,
        orders_completed=orders, provider_phone=PROVIDER_PHONE,
        platform_commission=15.0, **fields)
    db.session.add(service)
    db.session.commit()
    invalidate_service_caches()
    return service


def make_tier(service, name, price, total=0, sold=0, max_per_order=5,
              sort_order=100):
    """One price band, and the listing's denormalised 'from' price kept true.

    ServiceListing.price is maintained as the lowest active tier so the grid can
    print "from KES x" without a query per card. A fixture that skipped that would
    make the price checks pass against a number no route ever writes.

    `sold` is settable because quantity_total=0 means unlimited, by design
    (models.py:2211) - so the only way to build a band with nothing left is to sell
    out a band that had a capacity, which is also the only way it happens in life.
    """
    tier = ServicePriceTier(service_id=service.id, name=name, price=price,
                            quantity_total=total, quantity_sold=sold,
                            max_per_order=max_per_order, is_active=True,
                            sort_order=sort_order)
    db.session.add(tier)
    if not service.price or price < service.price:
        service.price = price
    db.session.commit()
    invalidate_service_caches()
    return tier


@contextlib.contextmanager
def fake_stk_push():
    """Answer every STK push with one fixed checkout id, and record the calls.

    Nothing else in the payment path is stubbed: the order row, the checkout id it
    stores, the callback lookup and finalize_paid_service_order are all the real
    code. Only Safaricom is absent.
    """
    calls = []
    original = app_module.stk_push

    def stub(phone, amount, reference, *args, **kwargs):
        calls.append({'phone': phone, 'amount': amount, 'reference': reference})
        return {'success': True, 'checkout_request_id': FAKE_CHECKOUT_ID,
                'merchant_request_id': f'{TAG}-merchant'}

    app_module.stk_push = stub
    try:
        yield calls
    finally:
        app_module.stk_push = original


@contextlib.contextmanager
def record_outbound():
    """Capture WhatsApp and SMS sends instead of making them.

    Both helpers return False when their keys are absent, so on a machine with no
    WhatsApp token every delivery assertion would pass for the wrong reason - "we
    never even tried" is indistinguishable from "it was refused". Recording the
    arguments makes the check real without configuring anything.
    """
    sent = {'whatsapp': [], 'sms': []}
    original_whatsapp = app_module.send_whatsapp_message
    original_sms = app_module.send_sms_notification

    def whatsapp(number, body, *args, **kwargs):
        sent['whatsapp'].append({'to': number, 'body': body})
        return True

    def sms(number, body, *args, **kwargs):
        sent['sms'].append({'to': number, 'body': body})
        return True

    app_module.send_whatsapp_message = whatsapp
    app_module.send_sms_notification = sms
    try:
        yield sent
    finally:
        app_module.send_whatsapp_message = original_whatsapp
        app_module.send_sms_notification = original_sms


def mpesa_callback_payload(checkout_id, result_code=0, receipt='SMK1TEST01',
                           amount=0):
    """The Daraja callback body, in the shape mpesa_callback actually parses."""
    body = {'Body': {'stkCallback': {
        'MerchantRequestID': f'{TAG}-merchant',
        'CheckoutRequestID': checkout_id,
        'ResultCode': result_code,
        'ResultDesc': 'The service request is processed successfully.'
        if result_code == 0 else 'Request cancelled by user',
    }}}
    if result_code == 0:
        body['Body']['stkCallback']['CallbackMetadata'] = {'Item': [
            {'Name': 'Amount', 'Value': amount},
            {'Name': 'MpesaReceiptNumber', 'Value': receipt},
        ]}
    return body


def create_post(client, **overrides):
    data = {'title': f'{TAG} New Listing', 'service_key': OPEN_KEY,
            'provider_phone': PROVIDER_PHONE, 'price': '450',
            'delivery_days': '2', 'description': 'Written by services_smoke.'}
    data.update(overrides)
    return client.post('/services/create', data=data)


# --- the checks ------------------------------------------------------------

def source_files():
    """main.py and every template, as text, for the checks that read the source.

    Read from disk rather than asserted through a rendered page: the sentence has to
    be gone from the places it could be rendered *from*, and a page that happens not
    to hit the branch today would let it sit there waiting for the branch that does.
    """
    paths = [os.path.join(ROOT, 'main.py')]
    for folder, _dirs, names in os.walk(os.path.join(ROOT, 'templates')):
        paths.extend(os.path.join(folder, name) for name in names
                     if name.endswith('.html'))
    for path in paths:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            yield os.path.relpath(path, ROOT).replace('\\', '/'), handle.read()


def check_client_never_sees_a_busy_message():
    print('the client is never told about our staffing, and the two messages are exact')
    # The sentence this replaced. Asserting the *absence* of a specific string is the
    # only honest form of this check: the old check compared the constant to its own
    # wording, which stayed green whether or not a client ever saw it, and a check
    # that cannot fail is worse than one that was deleted.
    gone = ('All our agents are currently busy, please reach out via whatsapp '
            'for immediate action.')
    offenders = [name for name, text in source_files()
                 if gone in text or 'SERVICE_BUSY_MESSAGE' in text
                 or 'linking desk is quiet' in text]
    check('the busy sentence is not in main.py or any template', not offenders,
          ', '.join(offenders))

    welcome = ('Thank you for showing interest in our services. Please wait as we '
               'link you with the provider. To avoid fraudulent activities, never '
               'pay directly to the provider before receiving the service.')
    check('the client welcome message matches character for character',
          SERVICE_CLIENT_WELCOME_MESSAGE == welcome, SERVICE_CLIENT_WELCOME_MESSAGE)
    interest = ('A client is interested in the service you listed. Quality service '
                'increases your rating. Thank you for making SMARKAFRICA a '
                'trustworthy platform.')
    check('the provider interest message matches character for character',
          SERVICE_PROVIDER_INTEREST_MESSAGE == interest,
          SERVICE_PROVIDER_INTEREST_MESSAGE)
    # Still asserted, because an empty desk hands the request to this number and the
    # client is promised a reply on the strength of it.
    url = support_whatsapp_url('Hi')
    check('the support number resolves to a wa.me link in international form',
          url.startswith('https://wa.me/254'), url)
    check('and a prefilled message is carried on it', 'text=' in url, url)


def check_profile_map():
    print('every seeded service has exactly one shape, and it is the right one')
    expected_by_profile = {
        'ticket': {'events_tickets'},
        'dropoff': {'laundry', 'printing', 'device_repair', 'cyber_services',
                    'books_stationery'},
        'errand': {'food_delivery', 'grocery', 'parcel_courier', 'campus_errands'},
        'visit': {'barber_beauty', 'fitness', 'health_wellness', 'cleaning'},
        'session': {'tutoring', 'career', 'student_gigs'},
        'tenancy': {'accommodation'},
    }
    # Written out here rather than derived from SERVICE_PROFILE_BY_KEY: deriving it
    # would compare the mapping to itself and pass whatever it said. This is the
    # table from the specification, and the mapping has to match it.
    check('the six profiles are the six that exist',
          set(SERVICE_FULFILMENT_PROFILES) == set(expected_by_profile),
          sorted(set(SERVICE_FULFILMENT_PROFILES) ^ set(expected_by_profile)))
    grouped = {}
    for key, profile in SERVICE_PROFILE_BY_KEY.items():
        grouped.setdefault(profile, set()).add(key)
    check('all eighteen seeded services are mapped',
          len(SERVICE_PROFILE_BY_KEY) == 18, len(SERVICE_PROFILE_BY_KEY))
    for profile, keys in expected_by_profile.items():
        check(f'{profile} holds exactly the services it should',
              grouped.get(profile) == keys,
              sorted((grouped.get(profile) or set()) ^ keys))
    for profile, spec in SERVICE_FULFILMENT_PROFILES.items():
        # A profile that says "paid on the platform after the work" and offers no
        # flow to reach the provider would be a shape nobody can buy.
        check(f'{profile} declares a flow, a payee and a moment',
              spec.get('flow') in ('buy', 'request')
              and spec.get('pay_to') in ('platform', 'provider')
              and spec.get('pay_when') in ('upfront', 'after'),
              f"{spec.get('flow')}/{spec.get('pay_to')}/{spec.get('pay_when')}")
    # The complaint that started this, stated as a property of the table rather than
    # of one page: pickup is a field on exactly the two profiles that have the idea.
    with_pickup = {name for name, spec in SERVICE_FULFILMENT_PROFILES.items()
                   if 'pickup' in spec['fields']}
    check('pickup is a field on drop-off and errands, and on nothing else',
          with_pickup == {'dropoff', 'errand'}, sorted(with_pickup))
    check('a ticket is bought, not requested',
          SERVICE_FULFILMENT_PROFILES['ticket']['flow'] == 'buy')
    check('and it is the only profile that is',
          [name for name, spec in SERVICE_FULFILMENT_PROFILES.items()
           if spec['flow'] == 'buy'] == ['ticket'])
    check('the two directly-paid profiles are the visit and the tenancy',
          {name for name, spec in SERVICE_FULFILMENT_PROFILES.items()
           if spec['pay_to'] == 'provider'} == {'visit', 'tenancy'})
    check('seed_profile_for reads the table for a known key',
          seed_profile_for('accommodation') == 'tenancy',
          seed_profile_for('accommodation'))
    check('and falls back rather than raising for one it has never seen',
          seed_profile_for(f'{TAG}_not_a_service') == 'dropoff',
          seed_profile_for(f'{TAG}_not_a_service'))


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
    print('contact admin: the same answer whether or not anyone is at the desk')
    with rate_limits_off(), settings(service_requests_enabled='1',
                                     service_direct_contact_enabled='0'):
        # The empty-desk press is made as the stranger, not the customer. Both presses
        # have to be a fresh create for the comparison to mean anything, and an open
        # row from the first would send the second down the "you already have a
        # thread" branch - which would compare two different code paths and call the
        # difference a pass.
        with only_on_duty(None):
            check('duty state reads as nobody at the desk',
                  not service_duty_active() and pick_service_admin() is None)
            with as_user(stranger_id) as client:
                response = client.post(f'/services/{service_id}/contact-admin',
                                       data={'note': 'Anybody there?',
                                             'phone': CLIENT_PHONE})
                empty_desk = response.get_json() or {}
                check('the request is answered, not refused',
                      response.status_code == 200, response.status_code)
                empty_desk_status = response.status_code
                check('and the reply is the client welcome message, word for word',
                      empty_desk.get('reply') == SERVICE_CLIENT_WELCOME_MESSAGE,
                      empty_desk.get('reply'))
                check('no provider phone anywhere in the payload',
                      PROVIDER_PHONE not in response.get_data(as_text=True))
            # The row is still written. Not as a refusal and not as a hand-off state:
            # 'open' with channel 'whatsapp', so the desk still lists it and "how
            # often was nobody there" stays answerable from the table.
            row = ServiceLinkRequest.query.filter_by(
                service_id=service_id, client_id=stranger_id).first()
            check('a desk row is written even with nobody to claim it',
                  row is not None and row.status == 'open',
                  row.status if row else None)
            check('and the channel records that it went to WhatsApp',
                  row is not None and row.channel == 'whatsapp',
                  row.channel if row else None)
            check('with no admin assigned, rather than one who is not working',
                  row is not None and row.assigned_admin_id is None,
                  row.assigned_admin_id if row else None)

        with only_on_duty(admin_id):
            check('duty state reads as somebody at the desk', service_duty_active())
            check('and the request is routed to that admin',
                  pick_service_admin() == admin_id, pick_service_admin())
            with as_user(customer_id) as client:
                response = client.post(f'/services/{service_id}/contact-admin',
                                       data={'note': 'Need this today',
                                             'phone': CLIENT_PHONE})
                staffed = response.get_json() or {}
                request_id = staffed.get('request_id')
                check('and a request id comes back', bool(request_id), request_id)
                check('no provider phone in this payload either',
                      PROVIDER_PHONE not in response.get_data(as_text=True))

                # The whole of "a customer will not know if there was an active admin
                # or not". Everything but the row id, which is a counter and says
                # nothing about staffing - and the id is compared for shape instead,
                # because a payload that dropped it on one path would differ in a way
                # the client could read.
                check('the empty desk and the staffed desk return the same status',
                      empty_desk_status == response.status_code,
                      f'{empty_desk_status} vs {response.status_code}')
                check('the same fields, with none added or missing',
                      set(empty_desk) == set(staffed),
                      sorted(set(empty_desk) ^ set(staffed)))
                differing = sorted(key for key in set(empty_desk) | set(staffed)
                                   if key != 'request_id'
                                   and empty_desk.get(key) != staffed.get(key))
                check('and the same value in every one of them except the row id',
                      not differing, differing)
                check('the row id is a real id on both, not zero on one of them',
                      isinstance(empty_desk.get('request_id'), int)
                      and empty_desk.get('request_id') > 0 and bool(request_id),
                      f'{empty_desk.get("request_id")} vs {request_id}')

                again = client.post(f'/services/{service_id}/contact-admin',
                                    data={'note': 'Still need this'})
                repeat = again.get_json() or {}
                check('pressing it twice reuses the open request',
                      repeat.get('existing') is True
                      and repeat.get('request_id') == request_id, repeat)
                check('and says the same words again',
                      repeat.get('reply') == SERVICE_CLIENT_WELCOME_MESSAGE,
                      repeat.get('reply'))

                thread = client.get(f'/services/requests/{request_id}/thread')
                body = thread.get_json() or {}
                check('the client can read the thread',
                      thread.status_code == 200 and body.get('success') is True,
                      thread.status_code)
                check('and their own note is the first message in it',
                      any('Need this today' in (msg.get('body') or '')
                          for msg in body.get('messages') or []),
                      body.get('messages'))
                # Rendered from the constant rather than stored as a row: with nobody
                # on duty there is no admin to attribute it to, and a stored copy
                # would double on re-read.
                check('the welcome message is carried on the thread itself',
                      body.get('welcome') == SERVICE_CLIENT_WELCOME_MESSAGE,
                      body.get('welcome'))
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

        # The kill switch is "nobody is answering", not "no". A client who presses the
        # button while it is off is not told the platform has switched something off -
        # they get the same words, a row, and a provider who hears about it.
        with only_on_duty(admin_id), settings(service_requests_enabled='0'):
            with as_user(stranger_id) as client:
                ServiceLinkRequest.query.filter_by(
                    service_id=service_id, client_id=stranger_id).update(
                        {'status': 'closed'}, synchronize_session=False)
                db.session.commit()
                response = client.post(f'/services/{service_id}/contact-admin',
                                       data={'note': 'Switched off?'})
                payload = response.get_json() or {}
                check('the kill switch reads as an empty desk, not as a refusal',
                      response.status_code == 200
                      and payload.get('reply') == SERVICE_CLIENT_WELCOME_MESSAGE,
                      f'{response.status_code} {payload.get("reply")}')
                check('and it still returns the same fields as a staffed desk',
                      set(payload) == set(staffed), sorted(set(payload) ^ set(staffed)))
                fresh = ServiceLinkRequest.query.filter_by(
                    service_id=service_id, client_id=stranger_id,
                    status='open').count()
                check('with a row written for the desk to find later', fresh == 1,
                      fresh)


def check_no_negative_fields(customer_id, provider_id, admin_id, listings):
    """A field a profile does not have does not appear as a negative.

    Asserted as the absence of the word "pickup" from the whole rendered page, not
    the absence of one sentence. The complaint was that a concert ticket announced
    "No pickup offered" - and any wording of that idea is the same bug, including the
    pickup-truck icon class that used to be printed on everything but a ticket.
    """
    print('a profile without pickup has no pickup on it, in any wording')
    with settings(service_direct_contact_enabled='0'):
        for profile, service in listings.items():
            expect_pickup = profile in ('dropoff', 'errand')
            # Three viewers, because the word crept back in through the owner's own
            # card once already: it promised every provider we show clients their
            # "pickup terms".
            viewers = {'an anonymous visitor': as_anonymous(),
                       'a signed-in client': as_user(customer_id),
                       'the provider themselves': as_user(provider_id)}
            for who, ctx in viewers.items():
                with ctx as client:
                    response = client.get(f'/services/{service.id}')
                    page = response.get_data(as_text=True).lower()
                    check(f'the {profile} page renders for {who}',
                          response.status_code == 200, response.status_code)
                    if expect_pickup:
                        check(f'and {profile} does talk about pickup, so the check '
                              f'above can fail', 'pickup' in page)
                    else:
                        where = [line.strip()[:90] for line in page.split('\n')
                                 if 'pickup' in line][:2]
                        check(f'{profile} says nothing about pickup to {who}',
                              'pickup' not in page, where)
            # The grid, filtered to this key so a neighbouring drop-off card cannot
            # supply the word and make a passing page look like a passing grid.
            with as_anonymous() as client:
                grid = client.get(f'/services?service={service.service_key}')
                page = grid.get_data(as_text=True).lower()
                check(f'the {profile} card renders on the grid',
                      grid.status_code == 200 and service.title.lower() in page,
                      grid.status_code)
                if not expect_pickup:
                    check(f'and the {profile} card says nothing about pickup either',
                          'pickup' not in page,
                          [line.strip()[:90] for line in page.split('\n')
                           if 'pickup' in line][:2])

    ticket = listings['ticket']
    check('pickup_display is None on a profile with no pickup concept, not a sentence',
          ticket.pickup_display is None, ticket.pickup_display)
    check('and offer_display is what the page prints in its place',
          bool(ticket.offer_display), ticket.offer_display)
    dropoff = listings['dropoff']
    check('a drop-off does carry a pickup sentence',
          bool(dropoff.pickup_display), dropoff.pickup_display)
    check('has_field reads the whitelist, so a ticket has no pickup field at all',
          not ticket.has_field('pickup') and dropoff.has_field('pickup'))
    check('and a ticket carries the fields it does have',
          ticket.has_field('event_venue') and ticket.has_field('tiers'))


def check_empty_desk_notifies_the_provider(client_id, provider_id, admin_id,
                                           service_id):
    """Nobody at the desk: the provider hears about it with no button pressed.

    Takes a client of its own rather than reusing check_contact_admin's. A client
    who already has an open request gets the existing one handed back - which is
    correct behaviour, and would make every assertion below check a send that had
    already happened before record_outbound started listening.
    """
    print('an unattended request notifies the provider by itself')
    with rate_limits_off(), settings(service_requests_enabled='1',
                                     service_direct_contact_enabled='0'):
        with only_on_duty(None), record_outbound() as sent:
            with as_user(client_id) as client:
                response = client.post(f'/services/{service_id}/contact-admin',
                                       data={'note': 'Tomorrow morning please',
                                             'phone': CLIENT_PHONE})
                payload = response.get_json() or {}
            check('the client still gets the welcome message',
                  payload.get('reply') == SERVICE_CLIENT_WELCOME_MESSAGE,
                  payload.get('reply'))
            check('and this is a fresh request, not one handed back',
                  not payload.get('existing'), payload)
            row = db.session.get(ServiceLinkRequest, payload.get('request_id') or 0)
            check('the request is on the desk for whoever comes in next',
                  row is not None and row.status == 'open',
                  row.status if row else None)
            check('the provider was notified without anyone pressing anything',
                  row is not None and row.provider_notified_at is not None,
                  row.provider_notified_at if row else None)

            provider_texts = [item['body'] for item in sent['whatsapp']
                              if item['to'] == PROVIDER_PHONE]
            check('the interest message went to the provider on WhatsApp',
                  len(provider_texts) == 1, len(provider_texts))
            check('and it opens with the exact words the MVP wrote',
                  bool(provider_texts)
                  and provider_texts[0].startswith(SERVICE_PROVIDER_INTEREST_MESSAGE),
                  provider_texts[0][:60] if provider_texts else '')
            check('and carries the service description with it, not just the sentence',
                  bool(provider_texts) and len(provider_texts[0]) >
                  len(SERVICE_PROVIDER_INTEREST_MESSAGE),
                  len(provider_texts[0]) if provider_texts else 0)
            check('an SMS went out as well, which lands without WhatsApp installed',
                  any(item['body'].startswith(SERVICE_PROVIDER_INTEREST_MESSAGE)
                      for item in sent['sms']),
                  [item['to'] for item in sent['sms']])
            inbox = CustomerNotification.query.filter_by(
                user_id=provider_id, notification_type='service').count()
            check('and it is in the provider\'s platform inbox too', inbox >= 1, inbox)
            # The other half of "the message lands to whatsapp immediately": the
            # request itself goes to the support line, so an admin coming back to a
            # phone rather than to the desk still sees it.
            support = [item for item in sent['whatsapp']
                       if item['to'] != PROVIDER_PHONE]
            check('the request was pushed to the support WhatsApp line as well',
                  len(support) == 1, len(support))
            check('and that message says nobody was on the desk, to us and not to the client',
                  bool(support) and 'nobody was on the linking desk' in support[0]['body'],
                  support[0]['body'][:70] if support else '')

            # Idempotent. The automatic send and the desk button can both run on one
            # request, and a provider who gets the same sentence twice reads it as the
            # platform being broken.
            before = len(sent['whatsapp'])
            first_stamp = row.provider_notified_at
            again = notify_service_provider(row)
            check('notifying a second time sends nothing',
                  len(sent['whatsapp']) == before, len(sent['whatsapp']) - before)
            check('and says so rather than reporting a success it did not have',
                  again.get('already') is True and not again.get('sms'), again)
            check('but the wa.me link is still handed back, so the desk button works '
                  'on an already-notified request',
                  (again.get('share_url') or '').startswith('https://wa.me/'),
                  again.get('share_url'))
            db.session.refresh(row)
            check('and the first timestamp is not overwritten',
                  row.provider_notified_at == first_stamp,
                  f'{first_stamp} -> {row.provider_notified_at}')
    return row.id if row else 0


def check_provider_whatsapp_button(customer_id, admin_id, service_id, request_id):
    """The desk's two provider buttons, and who can see the number behind them."""
    print('the desk can open a WhatsApp conversation; a client cannot')
    with rate_limits_off():
        with as_user(admin_id) as client:
            desk = client.get('/admin/services/requests')
            page = desk.get_data(as_text=True)
            check('the linking desk renders', desk.status_code == 200,
                  desk.status_code)
            # Deliberately not asserting a wa.me link in the markup: there is none,
            # and there should not be. A bare deep link would open the conversation
            # without ever sending the interest message, so both buttons POST to
            # admin_service_notify_provider and only the redirect carries wa.me -
            # which is asserted below, on the redirect itself.
            check('the desk shows the number to the admin, as a link they can press',
                  f'tel:{PROVIDER_PHONE}' in page, PROVIDER_PHONE in page)
            check('and both provider buttons post to the notify-provider route',
                  page.count(f'/admin/services/requests/{request_id}/notify-provider')
                  == 2,
                  page.count(f'/admin/services/requests/{request_id}/notify-provider'))
            check('the WhatsApp one distinguished by open_whatsapp, so one button can '
                  'serve both',
                  'name="open_whatsapp"' in page)

            # The button, exercised. open_whatsapp is what separates the two: both
            # send, and only this one hands the browser the deep link.
            with record_outbound():
                opened = client.post(
                    f'/admin/services/requests/{request_id}/notify-provider',
                    data={'open_whatsapp': '1'})
            check('pressing "WhatsApp provider" redirects to the conversation itself',
                  opened.status_code == 302
                  and (opened.headers.get('Location') or '').startswith(
                      'https://wa.me/'),
                  opened.headers.get('Location'))
            check('and the link carries the interest message prefilled',
                  'text=' in (opened.headers.get('Location') or ''),
                  opened.headers.get('Location'))
            with record_outbound():
                stayed = client.post(
                    f'/admin/services/requests/{request_id}/notify-provider')
            check('while "Notify provider" stays on the desk',
                  stayed.status_code == 302
                  and 'wa.me' not in (stayed.headers.get('Location') or ''),
                  stayed.headers.get('Location'))

        with as_user(customer_id) as client:
            refused = client.post(
                f'/admin/services/requests/{request_id}/notify-provider')
            check('a client cannot press it', refused.status_code in (302, 403, 404),
                  refused.status_code)
            with settings(service_direct_contact_enabled='0'):
                detail = client.get(f'/services/{service_id}')
                page = detail.get_data(as_text=True)
            check("and the client's own page carries neither the link nor the number",
                  f'wa.me/254{PROVIDER_PHONE[1:]}' not in page
                  and PROVIDER_PHONE not in page)


def check_ticket_money(customer_id, provider_id, service):
    """A ticket is bought, and nothing is earned until Safaricom says so."""
    print('tickets: pending until paid, paid once, and never oversold')
    service_id = service.id
    regular = make_tier(service, 'Regular', 500.0, total=2, max_per_order=2,
                        sort_order=10)
    vip = make_tier(service, 'VIP', 2500.0, total=1, max_per_order=1, sort_order=20)
    # Sold out by having been sold, not by being created empty: quantity_total=0 is
    # unlimited on purpose, so a band created with no capacity is the opposite of
    # this one. Both are asserted, because getting them the wrong way round is how a
    # free-entry event would refuse everyone.
    exhausted = make_tier(service, 'VVIP Table', 9000.0, total=1, sold=1,
                          sort_order=30)
    unlimited = make_tier(service, 'Standing', 3000.0, total=0, sort_order=40)
    check('the listing prints the lowest band, so the grid needs no tier query',
          service.price == 500.0, service.price)
    check('and the price display says "from" rather than one flat number',
          service_price_display(service).lower().startswith('from'),
          service_price_display(service))
    check('a band with nothing left knows it', exhausted.seats_left == 0,
          exhausted.seats_left)
    check('and a band nobody gave a capacity is unlimited, not sold out',
          unlimited.seats_left is None and not unlimited.sold_out,
          f'{unlimited.seats_left} / {unlimited.sold_out}')

    revenue_before = PlatformRevenue.query.filter_by(
        reference_id=str(service_id)).count()
    completed_before = service.orders_completed or 0

    with rate_limits_off(), fake_stk_push() as pushes:
        with as_user(customer_id) as client:
            # The linking desk is not on offer for a ticket at all, at any hour.
            refused = client.post(f'/services/{service_id}/contact-admin')
            check('a ticket cannot be requested through the desk',
                  refused.status_code == 400, refused.status_code)
            check('and the reason is the service, not our staffing',
                  'bought straight from this page'
                  in (refused.get_json() or {}).get('error', ''),
                  (refused.get_json() or {}).get('error'))

            bought = client.post(f'/services/{service_id}/buy',
                                 data={'tier_id': regular.id, 'quantity': '2',
                                       'phone': CLIENT_PHONE})
            check('buying redirects back to the listing', bought.status_code == 302,
                  bought.status_code)

    order = ServiceOrder.query.filter_by(service_id=service_id,
                                         client_id=customer_id).first()
    check('an order exists', order is not None)
    check('and it is pending, not paid', order is not None
          and order.payment_status == 'pending', order.payment_status if order else None)
    check('priced at the band times the quantity, not the listing price',
          order is not None and order.amount == 1000.0,
          order.amount if order else None)
    check('with the Daraja checkout id stored, which is all the callback arrives with',
          order is not None and order.checkout_request_id == FAKE_CHECKOUT_ID,
          order.checkout_request_id if order else None)
    check('one STK push was fired, for that amount',
          len(pushes) == 1 and pushes[0]['amount'] == 1000.0, pushes)
    check('no ticket code is minted before payment', order is not None
          and not order.ticket_code, order.ticket_code if order else None)
    check('no seat is taken off the band by an unpaid prompt',
          regular.quantity_sold == 0, regular.quantity_sold)
    check('no revenue is recorded for an unpaid order',
          PlatformRevenue.query.filter_by(reference_id=str(service_id)).count()
          == revenue_before,
          PlatformRevenue.query.filter_by(reference_id=str(service_id)).count())
    check('and the job count has not moved either',
          (service.orders_completed or 0) == completed_before,
          service.orders_completed)

    check('the callback can find the order by checkout id alone',
          service_order_for_checkout_id(FAKE_CHECKOUT_ID) is not None)
    check('and that resolver is separate from the product one, which still finds nothing',
          app_module.order_for_checkout_id(FAKE_CHECKOUT_ID) is None)

    with as_anonymous() as client:
        callback = client.post('/mpesa/callback',
                               json=mpesa_callback_payload(FAKE_CHECKOUT_ID,
                                                           amount=1000.0))
        check('Safaricom gets an acknowledgement', callback.status_code == 200,
              callback.status_code)
    db.session.expire_all()
    order = db.session.get(ServiceOrder, order.id)
    regular = db.session.get(ServicePriceTier, regular.id)
    service = db.session.get(ServiceListing, service_id)
    check('the order is paid', order.payment_status == 'paid', order.payment_status)
    check('the receipt is stored', order.mpesa_receipt == 'SMK1TEST01',
          order.mpesa_receipt)
    check('a ticket code is minted', bool(order.ticket_code), order.ticket_code)
    check('both seats come off the band', regular.quantity_sold == 2,
          regular.quantity_sold)
    check('the job count moves now, and only now',
          (service.orders_completed or 0) == completed_before + 1,
          service.orders_completed)
    paid_revenue = PlatformRevenue.query.filter_by(
        reference_id=str(service_id)).count()
    check('and the commission is recorded exactly once',
          paid_revenue == revenue_before + 1, paid_revenue)

    code = order.ticket_code
    with as_anonymous() as client:
        replay = client.post('/mpesa/callback',
                             json=mpesa_callback_payload(FAKE_CHECKOUT_ID,
                                                         amount=1000.0))
        check('a replayed callback is accepted', replay.status_code == 200,
              replay.status_code)
    db.session.expire_all()
    order = db.session.get(ServiceOrder, order.id)
    regular = db.session.get(ServicePriceTier, regular.id)
    check('and changes nothing: no second seat',
          db.session.get(ServicePriceTier, regular.id).quantity_sold == 2,
          regular.quantity_sold)
    check('no second commission',
          PlatformRevenue.query.filter_by(reference_id=str(service_id)).count()
          == paid_revenue)
    check('and the same ticket code', order.ticket_code == code, order.ticket_code)

    check('the band is now sold out', regular.seats_left == 0, regular.seats_left)
    with rate_limits_off(), fake_stk_push() as pushes:
        with as_user(customer_id) as client:
            client.post(f'/services/{service_id}/buy',
                        data={'tier_id': regular.id, 'quantity': '1',
                              'phone': CLIENT_PHONE})
            check('a sold-out band is refused at the till, with no push fired',
                  not pushes, pushes)
            client.post(f'/services/{service_id}/buy',
                        data={'tier_id': exhausted.id, 'quantity': '1'})
            check('and so is one that was already sold out when we arrived',
                  not pushes, pushes)
            client.post(f'/services/{service_id}/buy',
                        data={'tier_id': vip.id, 'quantity': '4'})
            check('asking for more than the per-order cap is refused too', not pushes,
                  pushes)
    orders_now = ServiceOrder.query.filter_by(service_id=service_id).count()
    check('none of the three refusals left an order behind', orders_now == 1,
          orders_now)

    # The other side of the same rule: an uncapped band sells. Asserted because
    # 0-means-unlimited is a decision, and a later "fix" reading it as zero seats
    # would silently close the till on every free-entry event.
    with rate_limits_off(), fake_stk_push() as pushes:
        with as_user(customer_id) as client:
            client.post(f'/services/{service_id}/buy',
                        data={'tier_id': unlimited.id, 'quantity': '3',
                              'phone': CLIENT_PHONE})
    check('an uncapped band still sells, at its own price',
          len(pushes) == 1 and pushes[0]['amount'] == 9000.0, pushes)
    check('and that is the second order, still pending',
          ServiceOrder.query.filter_by(service_id=service_id,
                                       payment_status='pending').count() == 1,
          ServiceOrder.query.filter_by(service_id=service_id).count())

    with as_user(provider_id) as client:
        own = client.post(f'/services/{service_id}/buy',
                          data={'tier_id': vip.id, 'quantity': '1'})
        check('a provider cannot buy their own tickets', own.status_code == 302,
              own.status_code)
    check('and that left no order of its own', ServiceOrder.query.filter_by(
        service_id=service_id).count() == 2)


def check_direct_pay_creates_no_order(customer_id, service):
    """visit and tenancy: paid in the chair, so there is nothing to charge for."""
    print('a directly-paid profile takes no money on the platform')
    revenue_before = PlatformRevenue.query.filter_by(
        reference_id=str(service.id)).count()
    check('the profile says the provider is paid directly',
          service.pays_provider_direct, service.pay_to)
    with rate_limits_off():
        with as_user(customer_id) as client:
            response = client.post(f'/services/{service.id}/order',
                                   data={'requirements': 'Saturday if you can'})
            check('the order button redirects rather than charging',
                  response.status_code == 302, response.status_code)
    orders = ServiceOrder.query.filter_by(service_id=service.id).count()
    check('no order row is created at all', orders == 0, orders)
    check('and no revenue row either',
          PlatformRevenue.query.filter_by(reference_id=str(service.id)).count()
          == revenue_before)


def check_thread_after_linking(customer_id, provider_id, stranger_id, admin_id,
                               service_id):
    """The provider joins the conversation when an admin links it, and not before."""
    print('the provider is on the thread after linking, and the admin always is')
    with rate_limits_off(), settings(service_requests_enabled='1'):
        with only_on_duty(admin_id):
            with as_user(customer_id) as client:
                created = client.post(f'/services/{service_id}/contact-admin',
                                      data={'note': 'Is Thursday possible?'})
                request_id = (created.get_json() or {}).get('request_id')
            check('a request to work with exists', bool(request_id), request_id)

            with as_user(provider_id) as client:
                early = client.get(f'/services/requests/{request_id}/thread')
                check('the provider cannot read it before it is linked',
                      early.status_code == 403, early.status_code)
                posted = client.post(f'/services/requests/{request_id}/thread',
                                     data={'body': 'I can do Thursday.'})
                check('nor write to it', posted.status_code == 403,
                      posted.status_code)

            with as_user(admin_id) as client:
                reading = client.get(f'/services/requests/{request_id}/thread')
                check('an admin can read it at this status',
                      reading.status_code == 200, reading.status_code)
                linked = client.post(
                    f'/admin/services/requests/{request_id}/link')
                check('and can mark it linked', linked.status_code == 302,
                      linked.status_code)
            row = db.session.get(ServiceLinkRequest, request_id)
            check('which is what the row now says', row.status == 'linked',
                  row.status)

            with as_user(provider_id) as client:
                now = client.post(f'/services/requests/{request_id}/thread',
                                  data={'body': 'Thursday at ten works.'})
                body = now.get_json() or {}
                check('now the provider can post', now.status_code == 200,
                      now.status_code)
                check('and the message is marked as theirs, not the desk\'s',
                      any(msg.get('from_provider') and not msg.get('from_admin')
                          for msg in body.get('messages') or []),
                      [(msg.get('from_admin'), msg.get('from_provider'))
                       for msg in body.get('messages') or []])

            with as_user(admin_id) as client:
                watching = client.get(f'/services/requests/{request_id}/thread')
                body = watching.get_json() or {}
                # "The admin can see the conversation in real time as they are
                # texting" - the desk polls this same endpoint, so what matters is
                # that a linked thread is still fully readable by an admin.
                check('the admin can still read every message on a linked thread',
                      watching.status_code == 200
                      and any('Thursday at ten' in (msg.get('body') or '')
                              for msg in body.get('messages') or []),
                      len(body.get('messages') or []))
                check('and is told the provider is on it',
                      body.get('provider_on_thread') is True,
                      body.get('provider_on_thread'))

            with as_user(stranger_id) as client:
                nosy = client.get(f'/services/requests/{request_id}/thread')
                check('an unrelated client is still refused', nosy.status_code == 403,
                      nosy.status_code)


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
            follow_up = payload.get('follow_up')
            check('and the follow-up tells them what to press, nothing about staffing',
                  follow_up == ('Open the one you want and press Contact admin '
                                f'{DASH} we will connect you with the provider.'),
                  follow_up)

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
                session.pop('delivery_location', None)
                empty_desk = service_chatbot_reply('I need laundry') or {}
            # The chatbot no longer consults duty at all, so this is not "the off-duty
            # shape is correct" - it is "there is no off-duty shape". Compared field by
            # field against the on-duty payload above, because a single asserted key
            # would let a new one be added later that gives the game away.
            with only_on_duty(admin_id):
                with app.test_request_context('/support'):
                    session.pop('delivery_location', None)
                    staffed = service_chatbot_reply('I need laundry') or {}
            check('the chatbot answers identically with the desk empty',
                  empty_desk == staffed,
                  sorted(key for key in set(empty_desk) | set(staffed)
                         if empty_desk.get(key) != staffed.get(key)))
            check('which means the action is still contact admin, not a hand-off',
                  empty_desk.get('action') == 'contact_admin',
                  empty_desk.get('action'))
            check('and the provider list is still given, not withheld',
                  len(empty_desk.get('providers') or []) == 3,
                  len(empty_desk.get('providers') or []))

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
    check_client_never_sees_a_busy_message()
    check_profile_map()
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

    # One listing per profile whose shape is asserted, on catalogue keys of our own.
    # The seeded events_tickets and barber_beauty rows are the operator's
    # configuration; listing under them would leave the real services page holding
    # smoke-test rows if a teardown were ever missed.
    make_catalogue(TICKET_KEY, 'Smoketest Tickets', sort_order=903, profile='ticket')
    make_catalogue(VISIT_KEY, 'Smoketest Visit', sort_order=904, profile='visit')
    make_catalogue(SESSION_KEY, 'Smoketest Session', sort_order=905,
                   profile='session')
    make_catalogue(TENANCY_KEY, 'Smoketest Tenancy', sort_order=906,
                   profile='tenancy')
    starts = datetime.utcnow() + timedelta(days=21)
    listings = {
        # The drop-off is the listing every other check already uses, and it is the
        # positive control: if the word "pickup" is missing from this one too, the
        # absence checks below are passing for the wrong reason.
        'dropoff': service,
        'ticket': make_listing(provider_id, f'{TAG} Gala Night', 500.0, TICKET_KEY,
                               'Smoketest Tickets', 0, event_starts_at=starts,
                               event_venue='KICC Grounds'),
        'visit': make_listing(provider_id, f'{TAG} Home Barber', 700.0, VISIT_KEY,
                              'Smoketest Visit', 0, serves_at_client=True,
                              appointment_required=True,
                              location_label='Kilimani', location_county='Nairobi',
                              opening_hours='Mon-Sat 9am-7pm'),
        'session': make_listing(provider_id, f'{TAG} Maths Tuition', 800.0,
                                SESSION_KEY, 'Smoketest Session', 0,
                                rate_unit='hour', serves_at_client=True,
                                turnaround_note='Two sessions a week'),
        'tenancy': make_listing(provider_id, f'{TAG} Bedsitter', 9000.0, TENANCY_KEY,
                                'Smoketest Tenancy', 0, rate_unit='month',
                                deposit_amount=9000.0, appointment_required=True,
                                available_from=starts,
                                location_label='Juja', location_county='Kiambu'),
    }
    check_no_negative_fields(customer_id, provider_id, admin_id, listings)

    check_contact_admin(customer_id, stranger_id, provider_id, admin_id, service_id)
    # Its own client, because contact-admin hands an existing open request back
    # rather than opening a second one - correct behaviour that would have made the
    # empty-desk assertions inspect a send that happened before we were listening.
    lonely = make_user('lonely', phone=CLIENT_PHONE)
    notified_id = check_empty_desk_notifies_the_provider(lonely.id, provider_id,
                                                         admin_id, service_id)
    # Reuses the request that check above created, because the desk's two buttons are
    # meant to be safe on a request the automatic path already notified.
    check_provider_whatsapp_button(customer_id, admin_id, service_id, notified_id)
    check_thread_after_linking(customer_id, provider_id, stranger_id, admin_id,
                               listings['session'].id)
    check_ticket_money(customer_id, provider_id, listings['ticket'])
    check_direct_pay_creates_no_order(customer_id, listings['visit'])

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
