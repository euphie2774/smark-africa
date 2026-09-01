"""Smoke check for private (authenticated) Cloudinary storage of paid files.

Run with: python tools/private_storage_smoke.py

Paid digital files used to have only one home: local disk, chosen because an
ordinary Cloudinary URL is readable by anyone who has it. That choice is also what
pins the platform to a single web instance, since a download served by the machine
that did not receive the upload is a 404. Authenticated delivery removes the pin
without removing the protection, and these checks are about the protection:

  * a reference round-trips, and cannot be confused with a local path
  * no unsigned URL is ever produced for a private asset
  * the purchase gate still runs in full, and a signed URL exists only past it
  * a stranger and a non-buyer get nothing, cloud-stored or not
  * paid files are the only thing routed this way - KYC documents stay local
  * a failed upload falls back to disk rather than losing the seller's file

What is NOT covered, and cannot be from here: the network round-trip. Uploading to
Cloudinary and fetching a signed URL back needs real credentials, so an operator
has to upload one file and download it once before trusting the switch. Everything
below is signature arithmetic and routing, which is where the leaks would be.
"""

import contextlib
import io
import os
import sys

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.datastructures import FileStorage

import main as app_module
from main import (CLOUDINARY_PRIVATE_FOLDERS, CLOUDINARY_PUBLIC_FOLDERS, app, db,
                  is_cloudinary_reference, make_cloudinary_reference,
                  parse_cloudinary_reference, product_file_extension,
                  save_uploaded_file, signed_private_download_url)
from models import Order, OrderItem, Product, User

FAILURES = []
TAG = 'privstore'

# Enough to sign with and nothing else. The secret is what the signature is
# derived from, so a made-up one exercises the real arithmetic; what it cannot do
# is prove Cloudinary accepts the result, which is the operator's one manual step.
FAKE_CREDS = {'cloud_name': 'smoke-cloud', 'api_key': '123456789012345',
              'api_secret': 'abcdefghijklmnopqrstuvwxyz12'}
NO_CREDS = {'cloud_name': '', 'api_key': '', 'api_secret': ''}


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + str(detail)) if detail else ""}')


@contextlib.contextmanager
def cloudinary(creds, private_uploads=True):
    """Force the credentials and the switch, rather than reading the real ones.

    cloudinary_credentials() prefers a row in the settings table over config, so on
    an install that has real keys the "unconfigured" checks below would silently
    become "configured" ones and pass for the wrong reason. Patching the one
    function that answers "what are the keys" keeps every check here reading the
    same on a bare checkout and on production.
    """
    original = app_module.cloudinary_credentials
    saved_switch = os.environ.get('CLOUDINARY_PRIVATE_UPLOADS')
    app_module.cloudinary_credentials = lambda: dict(creds)
    os.environ['CLOUDINARY_PRIVATE_UPLOADS'] = '1' if private_uploads else '0'
    try:
        yield
    finally:
        app_module.cloudinary_credentials = original
        if saved_switch is None:
            os.environ.pop('CLOUDINARY_PRIVATE_UPLOADS', None)
        else:
            os.environ['CLOUDINARY_PRIVATE_UPLOADS'] = saved_switch


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
            with client.session_transaction() as session:
                session['_user_id'] = str(user_id)
                session['_fresh'] = True
            yield client
    finally:
        db.session.remove()
        ctx.pop()


@contextlib.contextmanager
def rate_limits_off():
    """Downloads are capped at ten an hour, which is a limit on the test, not the code.

    The cap is per client address and, when REDIS_URL is set, it outlives the
    process - so a third run of this script inside an hour would start reading 429s
    as failures. The cap is pre-existing behaviour and not what is under test here.
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


def teardown():
    db.session.rollback()
    user_ids = [row[0] for row in db.session.query(User.id)
                .filter(User.username.like(f'{TAG}%')).all()] or [0]
    product_ids = [row[0] for row in db.session.query(Product.id)
                   .filter(Product.name.like(f'{TAG}%')).all()] or [0]
    order_ids = [row[0] for row in db.session.query(Order.id)
                 .filter(Order.user_id.in_(user_ids)).all()] or [0]
    OrderItem.query.filter(OrderItem.order_id.in_(order_ids)).delete(synchronize_session=False)
    Order.query.filter(Order.id.in_(order_ids)).delete(synchronize_session=False)
    Product.query.filter(Product.id.in_(product_ids)).delete(synchronize_session=False)
    User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)
    db.session.commit()


def make_user(suffix):
    user = User(username=f'{TAG}_{suffix}', email=f'{TAG}_{suffix}@example.invalid')
    user.set_password('x')
    db.session.add(user)
    db.session.commit()
    return user


def check_references():
    print('a stored reference round-trips and is never mistaken for a path')
    reference = make_cloudinary_reference({
        'public_id': 'smarkafrica/digital/ab12cd34_nursing_paper',
        'resource_type': 'raw', 'format': 'pdf'})
    check('a reference is built from the API response', reference, reference)
    parsed = parse_cloudinary_reference(reference)
    check('and parses back to the same three values',
          parsed == ('raw', 'pdf', 'smarkafrica/digital/ab12cd34_nursing_paper'), parsed)
    check('a local static path is not a reference',
          not is_cloudinary_reference('/static/uploads/digital/ab12_paper.pdf'))
    check('nor is an ordinary Cloudinary image URL',
          not is_cloudinary_reference(
              'https://res.cloudinary.com/x/image/upload/v1/smarkafrica/products/a.jpg'))
    check('nor is empty or None',
          not is_cloudinary_reference('') and not is_cloudinary_reference(None))
    check('a malformed reference parses to nothing rather than half a value',
          parse_cloudinary_reference('cloudinary:raw:pdf') is None
          and parse_cloudinary_reference('cloudinary:') is None)
    check('a response with no public_id yields no reference',
          make_cloudinary_reference({'resource_type': 'raw', 'format': 'pdf'}) == ''
          and make_cloudinary_reference(None) == '')

    # Raw uploads can carry the extension in the public_id instead of a format
    # field, and the signing call needs a format either way.
    derived = parse_cloudinary_reference(make_cloudinary_reference({
        'public_id': 'smarkafrica/digital/ab12_notes.docx', 'resource_type': 'raw'}))
    check('a format missing from the response is recovered from the public_id',
          derived and derived[1] == 'docx', derived)
    # A folder with a dot in it must not be read as an extension.
    plain = parse_cloudinary_reference(make_cloudinary_reference({
        'public_id': 'smarkafrica/v1.2/ab12_notes', 'resource_type': 'raw'}))
    check('and an extensionless public_id yields no format instead of a guess',
          plain and plain[1] == '', plain)
    return reference


def check_folder_sets():
    print('the two folder sets stay separate')
    check('paid files are private', 'digital' in CLOUDINARY_PRIVATE_FOLDERS)
    check('and are not also public', 'digital' not in CLOUDINARY_PUBLIC_FOLDERS)
    check('product photos are public', 'products' in CLOUDINARY_PUBLIC_FOLDERS)
    check('and are not also private', 'products' not in CLOUDINARY_PRIVATE_FOLDERS)
    overlap = CLOUDINARY_PUBLIC_FOLDERS & CLOUDINARY_PRIVATE_FOLDERS
    check('no folder is in both sets', not overlap, overlap)
    # Identity documents and selfies are read back off disk by the face matching,
    # so they must not be routed to object storage by either path.
    for folder in ('seller_docs', 'kyc'):
        check(f'{folder} is in neither set',
              folder not in CLOUDINARY_PUBLIC_FOLDERS
              and folder not in CLOUDINARY_PRIVATE_FOLDERS)

    with cloudinary(FAKE_CREDS):
        check('with the switch on, paid files route privately',
              app_module.cloudinary_private_allowed_for('digital'))
        check('and product photos still do not',
              not app_module.cloudinary_private_allowed_for('products'))
        check('nor do identity documents',
              not app_module.cloudinary_private_allowed_for('seller_docs'))
    with cloudinary(FAKE_CREDS, private_uploads=False):
        check('with the switch off, nothing routes privately',
              not app_module.private_uploads_enabled()
              and not app_module.cloudinary_private_allowed_for('digital'))
    with cloudinary(NO_CREDS):
        check('and the switch alone does nothing without credentials',
              not app_module.private_uploads_enabled())


def check_signing(reference):
    print('signing produces an expiring URL and never a bare one')
    with cloudinary(NO_CREDS):
        check('nothing is signed while Cloudinary is unconfigured',
              signed_private_download_url(reference) is None)
    with cloudinary(FAKE_CREDS):
        signed = signed_private_download_url(reference, download_name='paper.pdf',
                                             ttl_seconds=120)
        check('a configured install mints a URL', bool(signed), (signed or '')[:60])
        if signed:
            check('it carries an expiry', 'expires_at=' in signed)
            check('it carries a signature', 'signature=' in signed)
            check('it names the download', 'attachment=paper.pdf' in signed)
            check('the api secret is absent from it',
                  FAKE_CREDS['api_secret'] not in signed)
            check('it is not a public delivery URL', '/upload/' not in signed,
                  signed[:64])
        check('a local path still signs to nothing',
              signed_private_download_url('/static/uploads/digital/x.pdf') is None)
        check('and so does an empty column',
              signed_private_download_url(None) is None
              and signed_private_download_url('') is None)


def check_upload_routing():
    """Where save_uploaded_file sends a file, without sending one anywhere.

    The upload call itself is stubbed: what matters here is that the private branch
    is taken only for paid files, that its reference is what gets stored, and that a
    refusal falls back to disk instead of costing the seller the upload.
    """
    print('uploads are routed by folder, and a failure still keeps the file')
    original = app_module.upload_private_to_cloudinary
    calls = []

    def stub(source, subfolder='digital', filename=None):
        calls.append(subfolder)
        return stub.result

    def upload(subfolder, name='notes.pdf'):
        source = FileStorage(stream=io.BytesIO(b'%PDF-1.4 smoke'), filename=name)
        with app.test_request_context('/'):
            return save_uploaded_file(source, subfolder=subfolder)

    app_module.upload_private_to_cloudinary = stub
    try:
        with cloudinary(FAKE_CREDS):
            stub.result = 'cloudinary:raw:pdf:smarkafrica/digital/ab12_notes'
            del calls[:]
            stored = upload('digital')
            check('a paid file is stored as a reference',
                  is_cloudinary_reference(stored), stored)
            check('and the private upload was the thing that ran', calls == ['digital'],
                  calls)

            stub.result = None
            del calls[:]
            stored = upload('digital', name='fallback.pdf')
            check('a refused upload falls back to a local path',
                  stored.startswith('/static/uploads/digital/'), stored)
            local = os.path.join(app.config['UPLOAD_FOLDER'], 'digital',
                                 os.path.basename(stored))
            check('and the bytes are really on disk',
                  os.path.exists(local) and os.path.getsize(local) > 0, local)
            if os.path.exists(local):
                os.remove(local)
    finally:
        app_module.upload_private_to_cloudinary = original


def check_download_gate(reference):
    print('the purchase gate is unchanged, and delivery happens only past it')
    seller = make_user('seller')
    buyer = make_user('buyer')
    stranger = make_user('stranger')
    cloud = Product(
        name=f'{TAG} Nursing Paper', slug=f'{TAG}-nursing-paper', seller_id=seller.id,
        selling_price=250.0, buying_price=0.0, description='A cloud-stored past paper.',
        short_description='Past paper', stock=0, is_active=True, is_digital=True,
        review_status='approved', commission_percent=15.0, file_path=reference)
    # The same page and the same gate, for a file that never moved. Cloud storage is
    # opt-in per file, so both shapes exist side by side for as long as the operator
    # has old listings - and the old shape has to keep working unchanged.
    local = Product(
        name=f'{TAG} Local Notes', slug=f'{TAG}-local-notes', seller_id=seller.id,
        selling_price=100.0, buying_price=0.0, description='A disk-stored note set.',
        short_description='Notes', stock=0, is_active=True, is_digital=True,
        review_status='approved', commission_percent=15.0,
        file_path='/static/uploads/digital/privstore_local_notes.pdf')
    db.session.add_all([cloud, local])
    db.session.commit()
    check('a cloud-stored product reports its extension',
          product_file_extension(cloud) == 'pdf', product_file_extension(cloud))
    check('and so does a disk-stored one',
          product_file_extension(local) == 'pdf', product_file_extension(local))

    paid = Order(user_id=buyer.id, amount_paid=350.0, payment_status='completed',
                 status='completed')
    unpaid = Order(user_id=buyer.id, amount_paid=0.0, payment_status='pending',
                   status='pending')
    db.session.add_all([paid, unpaid])
    db.session.flush()
    for order in (paid, unpaid):
        for product in (cloud, local):
            db.session.add(OrderItem(order_id=order.id, product_id=product.id,
                                     product_name=product.name,
                                     price=product.selling_price, quantity=1,
                                     is_digital=True))
    db.session.commit()
    buyer_id, stranger_id = buyer.id, stranger.id
    paid_id, unpaid_id = paid.id, unpaid.id
    cloud_id, local_id, cloud_slug = cloud.id, local.id, cloud.slug

    with cloudinary(FAKE_CREDS), rate_limits_off():
        with as_user(buyer_id) as client:
            response = client.get(f'/api/download/{paid_id}/{cloud_id}')
            check('the buyer of a paid order is redirected to a signed URL',
                  response.status_code == 302, response.status_code)
            target = response.headers.get('Location', '')
            check('and the target is signed and expiring',
                  'signature=' in target and 'expires_at=' in target, target[:80])
            check('and it is Cloudinary serving the bytes, not this app',
                  'cloudinary.com' in target, target[:60])

            # The disk-stored file is missing on purpose: what is being checked is
            # that its request never reaches the signing code, and a 404 from the
            # local branch proves it took the local branch.
            response = client.get(f'/api/download/{paid_id}/{local_id}')
            check('a disk-stored file is not sent to Cloudinary',
                  response.status_code == 404, response.status_code)

            response = client.get(f'/api/download/{unpaid_id}/{cloud_id}')
            check('an unpaid order is turned away', response.status_code == 302,
                  response.status_code)
            check('and turned back into the app, not out to the file',
                  'cloudinary' not in response.headers.get('Location', ''),
                  response.headers.get('Location'))

        with as_user(stranger_id) as client:
            response = client.get(f'/api/download/{paid_id}/{cloud_id}')
            check("someone else's order is refused outright",
                  response.status_code == 403, response.status_code)

        ctx = app.app_context()
        ctx.push()
        try:
            with app.test_client() as anon:
                response = anon.get(f'/api/download/{paid_id}/{cloud_id}')
                check('an anonymous request is sent to log in',
                      response.status_code in (301, 302)
                      and 'login' in response.headers.get('Location', ''),
                      response.headers.get('Location'))
                preview = anon.get(f'/api/product/{cloud_id}/preview')
                check('a cloud-stored file offers no preview to leak through',
                      preview.status_code == 404, preview.status_code)
                page = anon.get(f'/product/{cloud_slug}')
                check('and its page renders without a preview control',
                      page.status_code == 200
                      and f'/api/product/{cloud_id}/preview'.encode() not in page.data,
                      page.status_code)
        finally:
            db.session.remove()
            ctx.pop()


def check_static_upload_guard():
    """Who can fetch an upload folder straight off /static/, by identity.

    tools/wiring_smoke.py already asserts the anonymous half of this, but it signs
    nobody in, so the *admin* half - the one that decides whether a KYC reviewer can
    see the document they are reviewing - has no coverage there at all. That half fails
    silently: an over-tight guard shows up as an empty <img> on one admin page and
    nowhere else, and the reviewer has no way to tell a blocked file from a missing
    upload.

    Real files, written here and removed in the finally, so the answer does not depend
    on what happens to be on disk. A 404 on a file that was never there proves nothing.
    """
    print('the static route serves an upload folder only to who should have it')

    admin = make_user('guardadmin')
    admin.is_admin = True
    plain = make_user('guardplain')
    db.session.commit()
    admin_id, plain_id = admin.id, plain.id

    root = app.config['UPLOAD_FOLDER']
    written = []
    try:
        for folder in ('digital', 'seller_docs', 'kyc', 'phone_docs', 'products'):
            target = os.path.join(root, folder)
            os.makedirs(target, exist_ok=True)
            path = os.path.join(target, f'{TAG}_guard_probe.bin')
            io.open(path, 'wb').write(b'probe')
            written.append((folder, f'{TAG}_guard_probe.bin', path))

        def url(folder, name):
            return f'/static/uploads/{folder}/{name}'

        # Anonymous: everything private is refused, and refused as a 404. A 403 would
        # confirm the file exists, and for an ID document that is the fact worth
        # withholding.
        with app.test_client() as anon:
            for folder, name, _ in written:
                got = anon.get(url(folder, name))
                want = 200 if folder == 'products' else 404
                check(f'anonymous gets {want} for {folder}',
                      got.status_code == want, got.status_code)

        # A signed-in customer is not an admin. Worth asserting separately: the guard
        # reads current_user, and "logged in" is the easiest thing to mistake for
        # "allowed".
        with as_user(plain_id) as client:
            for folder in ('seller_docs', 'kyc', 'phone_docs'):
                got = client.get(url(folder, f'{TAG}_guard_probe.bin'))
                check(f'a signed-in customer still gets 404 for {folder}',
                      got.status_code == 404, got.status_code)

        with as_user(admin_id) as client:
            for folder in ('seller_docs', 'kyc', 'phone_docs'):
                got = client.get(url(folder, f'{TAG}_guard_probe.bin'))
                check(f'an admin can read {folder} for review',
                      got.status_code == 200, got.status_code)
            # Not even an admin, and not an oversight: the paid-download route reads
            # the folder off disk itself, so nothing legitimate needs this URL, and an
            # admin session on a shared machine is the likeliest way the link escapes.
            got = client.get(url('digital', f'{TAG}_guard_probe.bin'))
            check('and digital is closed even to an admin',
                  got.status_code == 404, got.status_code)
    finally:
        for _, _, path in written:
            try:
                os.remove(path)
            except OSError:
                pass


def run():
    reference = check_references()
    check_folder_sets()
    check_signing(reference)
    check_upload_routing()
    check_download_gate(reference)
    check_static_upload_guard()


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
