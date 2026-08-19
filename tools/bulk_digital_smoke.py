"""Smoke check for bulk digital product upload.

Run with: python tools/bulk_digital_smoke.py

Drives the real endpoints through the test client with real file bytes, because the
things most likely to break here are not in the happy path: a batch that is too big
for one request, a file whose extension lies about its contents, one bad file in a
good batch, and two files that would claim the same slug. Each of those is checked
against what the seller is actually told.

Everything created is removed at the end, including the files written to disk.
"""

import contextlib
import io
import os
import sys

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
os.environ.setdefault('CACHE_TYPE', 'NullCache')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app_module
from main import (BULK_DIGITAL_MAX_FILES, app, bulk_digital_max_file_bytes,
                  bulk_digital_request_budget, db, digital_product_name)
from models import BusinessStorefront, Order, OrderItem, Product, User

FAILURES = []
TAG = 'bulksmoke'
PDF = b'%PDF-1.4\n' + b'0' * 512  # a real signature, so the header check passes
DOCX = b'PK\x03\x04' + b'0' * 512


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + str(detail)) if detail else ""}')


@contextlib.contextmanager
def as_user(user_id):
    """A test client signed in as one user, in an app context of its own.

    Flask-Login caches the user it loaded on ``g``, and ``g`` belongs to the app
    context rather than to the request. A script that holds one app context open
    for its whole run therefore keeps whoever signed in first, silently, for every
    client opened afterwards - which is how the buyer's download checks came to be
    handing the gate a seller's identity and reading the resulting 403 as a bug in
    the gate. One context per identity is what a real server does anyway: Gunicorn
    builds a fresh app context per request.

    Takes an id rather than a User so nothing has to be read across the boundary
    between one context's session and another's.
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


def upload(client, files, **fields):
    """Post one batch. ``files`` is a list of (filename, bytes)."""
    data = {
        'selling_price': fields.pop('selling_price', '250'),
        'owns_rights': fields.pop('owns_rights', 'on'),
        'is_active': 'on',
        'first_page_preview': 'on',
    }
    data.update({k: v for k, v in fields.items() if v is not None})
    data['files'] = [(io.BytesIO(body), name) for name, body in files]
    return client.post('/seller/products/bulk/upload', data=data,
                       content_type='multipart/form-data')


def made_products():
    return Product.query.filter(Product.name.like(f'%{TAG}%')).all()


def teardown():
    db.session.rollback()
    buyer_ids = [row[0] for row in db.session.query(User.id)
                 .filter(User.username.like(f'{TAG}%')).all()]
    order_ids = [row[0] for row in db.session.query(Order.id)
                 .filter(Order.user_id.in_(buyer_ids or [0])).all()]
    OrderItem.query.filter(OrderItem.order_id.in_(order_ids or [0])).delete(
        synchronize_session=False)
    Order.query.filter(Order.id.in_(order_ids or [0])).delete(synchronize_session=False)
    db.session.commit()
    for product in made_products():
        # The file on disk outlives the row unless it is removed here.
        if product.file_path and product.file_path.startswith('/static/'):
            path = os.path.join(app.root_path, product.file_path.lstrip('/'))
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        db.session.delete(product)
    db.session.commit()
    BusinessStorefront.query.filter(
        BusinessStorefront.business_name.like(f'{TAG}%')).delete(synchronize_session=False)
    User.query.filter(User.username.like(f'{TAG}%')).delete(synchronize_session=False)
    db.session.commit()
    app_module.invalidate_nav_categories()


def make_seller():
    """A verified seller with an approved storefront - the state the gate requires.

    'verified' is what user_can_sell looks for, and 'approved' is what
    seller_storefront accepts; the two gates read different vocabularies.
    """
    user = User(username=f'{TAG}_seller', email=f'{TAG}@example.invalid',
                phone='+254798000111')
    user.set_password('x')
    user.seller_status = 'verified'
    for flag in ('is_verified_seller', 'is_seller'):
        if hasattr(user, flag):
            setattr(user, flag, True)
    db.session.add(user)
    db.session.commit()

    storefront = BusinessStorefront(
        owner_id=user.id,
        business_name=f'{TAG} Study Hub',
        slug=f'{TAG}-study-hub',
        status='approved',
        physical_address='Kimathi Street, Nairobi',
        # Pre-set so the batch never reaches for the network geocoder.
        location_lat=-1.2841,
        location_lng=36.8233,
    )
    db.session.add(storefront)
    db.session.commit()
    return user, storefront


def run():
    print('filenames become readable listing names')
    cases = [
        ('NUR-201_Final_Exam_2024.pdf', 'NUR 201 Final Exam 2024'),
        ('anatomy notes week 3.pdf', 'Anatomy Notes Week 3'),
        ('Pharmacology_Assignment (1).docx', 'Pharmacology Assignment'),
        ('  spaced   out  .pdf', 'Spaced Out'),
        ('.pdf', 'Digital download'),
    ]
    for filename, expected in cases:
        actual = digital_product_name(filename)
        check(f'{filename!r} -> {expected!r}', actual == expected, actual)

    print('the limits the browser is told about are self-consistent')
    hard_cap = app.config['MAX_CONTENT_LENGTH']
    check('a batch budget leaves room under the body cap',
          bulk_digital_request_budget() < hard_cap,
          (bulk_digital_request_budget(), hard_cap))
    check('one file can never exceed one batch',
          bulk_digital_max_file_bytes() <= bulk_digital_request_budget())

    user, storefront = make_seller()
    seller_id = user.id
    app.config['WTF_CSRF_ENABLED'] = False

    with as_user(seller_id) as client:
        print('the page loads for a verified seller')
        page = client.get('/seller/products/bulk')
        check('GET /seller/products/bulk renders', page.status_code == 200,
              page.status_code)

        print('a good batch lists one product per file')
        response = upload(client, [
            (f'{TAG}_NUR-201_Exam.pdf', PDF),
            (f'{TAG}_Anatomy_Notes.pdf', PDF),
            (f'{TAG}_Assignment.docx', DOCX),
        ], name_prefix='Nursing')
        check('the batch is accepted', response.status_code == 200, response.status_code)
        body = response.get_json()
        check('three listings created', body['summary']['created'] == 3, body['summary'])
        check('none failed', body['summary']['failed'] == 0, body.get('failed'))
        check('the prefix is on the name',
              all(item['name'].startswith('Nursing') for item in body['created']),
              [item['name'] for item in body['created']])

        saved = {p.name: p for p in made_products()}
        check('every one is marked digital', all(p.is_digital for p in saved.values()))
        check('every one has a file attached',
              all(p.file_path and p.file_size for p in saved.values()))
        check('the recorded size matches the bytes sent',
              all(p.file_size == len(PDF) or p.file_size == len(DOCX)
                  for p in saved.values()),
              [p.file_size for p in saved.values()])
        check('stock and weight are zeroed, delivery is free',
              all(p.stock == 0 and p.weight_kg == 0 and p.free_delivery
                  for p in saved.values()))
        check('the file really is on disk',
              all(os.path.exists(os.path.join(app.root_path, p.file_path.lstrip('/')))
                  for p in saved.values()))
        check('the storefront pin came along',
              all(p.location_lat is not None for p in saved.values()))
        check('the owner is the seller, not the platform',
              all(p.seller_id == seller_id for p in saved.values()))

        print('one bad file does not take the batch down with it')
        before = len(made_products())
        response = upload(client, [
            (f'{TAG}_Good_One.pdf', PDF),
            (f'{TAG}_Liar.pdf', b'this is not a pdf at all'),
            (f'{TAG}_Good_Two.pdf', PDF),
        ])
        body = response.get_json()
        check('the good files still landed', body['summary']['created'] == 2,
              body['summary'])
        check('the liar was rejected on its signature', body['summary']['failed'] == 1,
              body.get('failed'))
        check('and it is named in the failure',
              body['failed'][0]['filename'] == f'{TAG}_Liar.pdf', body['failed'])
        check('the rejection did not roll back the good ones',
              len(made_products()) == before + 2, len(made_products()))

        print('files that would collide get distinct slugs')
        response = upload(client, [
            (f'{TAG}_Same_Name.pdf', PDF),
            (f'{TAG}_Same_Name.pdf', PDF),
        ])
        body = response.get_json()
        check('both were listed', body['summary']['created'] == 2, body['summary'])
        slugs = [item['slug'] for item in body['created']]
        check('with different slugs', len(set(slugs)) == 2, slugs)

        print('an executable cannot be dressed up as a download')
        response = upload(client, [(f'{TAG}_malware.exe', b'MZ\x90\x00' + b'0' * 64)])
        body = response.get_json()
        check('the .exe is refused', body['summary']['created'] == 0, body['summary'])
        response = upload(client, [(f'{TAG}_macro.docm', DOCX)])
        body = response.get_json()
        check('so is a macro-carrying .docm', body['summary']['created'] == 0,
              body['summary'])

        print('the form is not optional')
        response = upload(client, [(f'{TAG}_x.pdf', PDF)], owns_rights=None)
        check('no rights confirmation, no upload', response.status_code == 400,
              response.status_code)
        check('and the reason says so',
              'rights' in (response.get_json().get('error') or '').lower(),
              response.get_json())
        response = upload(client, [(f'{TAG}_y.pdf', PDF)], selling_price='0')
        check('a zero price is refused', response.status_code == 400,
              response.status_code)
        response = upload(client, [])
        check('an empty batch is refused', response.status_code == 400,
              response.status_code)

        print('a file over the per-file ceiling is refused, not truncated')
        oversized = b'%PDF-1.4\n' + b'0' * (bulk_digital_max_file_bytes() + 1024)
        response = upload(client, [(f'{TAG}_huge.pdf', oversized)])
        body = response.get_json()
        check('the oversized file failed', body['summary']['created'] == 0, body['summary'])
        check('and the message names a size limit',
              'limit' in (body['failed'][0]['error'] or '').lower(), body['failed'])

        print('too many files in one request is refused')
        many = [(f'{TAG}_many_{i}.pdf', PDF) for i in range(BULK_DIGITAL_MAX_FILES + 1)]
        response = upload(client, many)
        check('the over-long batch is refused', response.status_code == 400,
              response.status_code)
        check('and the cap is in the message',
              str(BULK_DIGITAL_MAX_FILES) in (response.get_json().get('error') or ''),
              response.get_json())

        print('a batch the browser should have split is a clean failure')
        # Deliberately past MAX_CONTENT_LENGTH. Flask rejects this before the view,
        # which is exactly why the page packs batches by bytes rather than by count.
        huge = b'%PDF-1.4\n' + b'0' * (app.config['MAX_CONTENT_LENGTH'] + 1024)
        response = upload(client, [(f'{TAG}_toobig.pdf', huge)])
        check('an oversized body is rejected, not accepted in part',
              response.status_code in (400, 413), response.status_code)

        print('listings park for review when that is the configured default')
        products = made_products()
        if app_module.DIGITAL_REVIEW_REQUIRED:
            check('nothing went live unreviewed',
                  all(p.review_status == 'admin_review' and not p.is_active
                      for p in products))
        else:
            check('listings published immediately',
                  all(p.review_status == 'approved' for p in products))

        print('the seller list renders with digital rows in it')
        listing = client.get('/seller/products')
        check('GET /seller/products renders', listing.status_code == 200,
              listing.status_code)

    print('a buyer can actually download what was uploaded')
    # The point of the whole feature. A bulk-listed file has to survive the download
    # gate, which re-sanitises the stored filename against the allowed extensions -
    # so a type the upload accepts but the gate rejects would list fine and be
    # undownloadable, which is worse than refusing it at upload.
    sold = [p for p in made_products() if p.file_path]
    pdf_item = next((p for p in sold if p.file_path.lower().endswith('.pdf')), None)
    docx_item = next((p for p in sold if p.file_path.lower().endswith('.docx')), None)
    buyer = User(username=f'{TAG}_buyer', email=f'{TAG}_buyer@example.invalid',
                 phone='+254798000222')
    buyer.set_password('x')
    db.session.add(buyer)
    db.session.commit()

    order = Order(user_id=buyer.id, amount_paid=250.0, payment_status='completed')
    db.session.add(order)
    db.session.flush()
    for item in [p for p in (pdf_item, docx_item) if p]:
        db.session.add(OrderItem(order_id=order.id, product_id=item.id,
                                 product_name=item.name, price=250.0,
                                 quantity=1, is_digital=True))
    db.session.commit()

    # Plain ids, so the checks below never touch an object owned by this context's
    # session from inside another one.
    buyer_id, order_id = buyer.id, order.id
    wanted = [(label, p.id) for label, p in (('pdf', pdf_item), ('docx', docx_item)) if p]

    with as_user(buyer_id) as client:
        for label, product_id in wanted:
            got = client.get(f'/api/download/{order_id}/{product_id}')
            check(f'the buyer downloads the {label}', got.status_code == 200,
                  got.status_code)
            check(f'and gets the {label} bytes, not an error page',
                  got.data.startswith(b'%PDF') or got.data.startswith(b'PK'),
                  got.data[:8])
            check(f'served as an attachment ({label})',
                  'attachment' in (got.headers.get('Content-Disposition') or ''),
                  got.headers.get('Content-Disposition'))

    print('someone who did not buy it cannot download it')
    stranger = User(username=f'{TAG}_stranger', email=f'{TAG}_s@example.invalid',
                    phone='+254798000333')
    stranger.set_password('x')
    db.session.add(stranger)
    db.session.commit()
    with as_user(stranger.id) as client:
        blocked = client.get(f'/api/download/{order_id}/{wanted[0][1]}')
        check('a stranger is refused', blocked.status_code == 403, blocked.status_code)

    print('an unpaid order does not hand the file over')
    order.payment_status = 'pending'
    db.session.commit()
    with as_user(buyer_id) as client:
        unpaid = client.get(f'/api/download/{order_id}/{wanted[0][1]}',
                            follow_redirects=False)
        check('an unpaid order is turned away', unpaid.status_code in (302, 403),
              unpaid.status_code)
        check('and it is a redirect to the order list, not a bare refusal',
              unpaid.status_code == 302, unpaid.status_code)


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
