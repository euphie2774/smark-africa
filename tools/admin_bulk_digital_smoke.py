"""Smoke check for admin bulk document upload.

Run with: python tools/admin_bulk_digital_smoke.py

The seller version of this flow parks every listing for review; the admin version
must not, because an admin is the reviewer and there is nobody above them to wait
for. That is one flag's difference in shared code, which is exactly the kind of
difference that gets applied to both paths by accident - so the checks here are as
much about the seller default staying untouched as about the admin path working.

Also covers the two things only the admin path has: no storefront to take a location
pin from, and a commission percentage that comes off the form rather than a constant.
"""

import contextlib
import io
import os
import sys

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
os.environ.setdefault('CACHE_TYPE', 'NullCache')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app_module
from main import app, bulk_digital_location, db
from models import BusinessStorefront, Product, User

FAILURES = []
TAG = 'adminbulk'
PDF = b'%PDF-1.4\n' + b'0' * 512
DOCX = b'PK\x03\x04' + b'0' * 512


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + str(detail)) if detail else ""}')


@contextlib.contextmanager
def as_user(user_id):
    """One app context per identity - Flask-Login caches the user on ``g``."""
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


def made_products():
    # ilike, not like: digital_product_name title-cases the filename, so the tag
    # comes back as 'Adminbulk'. LIKE is case-sensitive on Postgres.
    return Product.query.filter(Product.name.ilike(f'%{TAG}%')).all()


def teardown():
    db.session.rollback()
    for product in made_products():
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


def make_admin():
    user = User(username=f'{TAG}_admin', email=f'{TAG}_admin@example.invalid',
                phone='+254798100111')
    user.set_password('x')
    user.is_admin = True
    db.session.add(user)
    db.session.commit()
    return user


def make_seller():
    user = User(username=f'{TAG}_seller', email=f'{TAG}_seller@example.invalid',
                phone='+254798100222')
    user.set_password('x')
    user.seller_status = 'verified'
    for flag in ('is_verified_seller', 'is_seller'):
        if hasattr(user, flag):
            setattr(user, flag, True)
    db.session.add(user)
    db.session.commit()
    db.session.add(BusinessStorefront(
        owner_id=user.id, business_name=f'{TAG} Study Hub',
        slug=f'{TAG}-study-hub', status='approved',
        physical_address='Kimathi Street, Nairobi',
        location_lat=-1.2841, location_lng=36.8233))
    db.session.commit()
    return user


def upload(client, url, files, **fields):
    data = {
        'selling_price': fields.pop('selling_price', '300'),
        'owns_rights': fields.pop('owns_rights', 'on'),
        'is_active': fields.pop('is_active', 'on'),
        'first_page_preview': 'on',
    }
    data.update({k: v for k, v in fields.items() if v is not None})
    data['files'] = [(io.BytesIO(body), name) for name, body in files]
    return client.post(url, data=data, content_type='multipart/form-data')


def check_location_guard():
    print('an admin has no storefront, and that is not a crash')
    # bulk_digital_location dereferences storefront.id on the seller path, so the
    # admin path passing None is the one call that would 500 before the guard.
    result = bulk_digital_location(None)
    check('a missing storefront yields an empty pin', isinstance(result, dict), result)
    check('with no storefront id', result.get('storefront') is None, result)
    check('and no coordinates',
          result.get('lat') is None and result.get('lng') is None, result)


def check_admin_batch(admin_id, seller_id):
    print('an admin batch publishes immediately')
    app.config['WTF_CSRF_ENABLED'] = False
    with as_user(admin_id) as client:
        page = client.get('/admin/products/bulk')
        check('GET /admin/products/bulk renders', page.status_code == 200,
              page.status_code)

        response = upload(client, '/admin/products/bulk/upload', [
            (f'{TAG}_NUR-201_Past_Paper.pdf', PDF),
            (f'{TAG}_Anatomy_Revision.pdf', PDF),
            (f'{TAG}_Pharmacology_Notes.docx', DOCX),
        ], name_prefix='Nursing', commission_percent='12.5')
        check('the batch is accepted', response.status_code == 200, response.status_code)
        body = response.get_json()
        check('three listings created', body['summary']['created'] == 3, body['summary'])
        check('none failed', body['summary']['failed'] == 0, body.get('failed'))
        check('and none is reported as awaiting review',
              not any(item.get('review') for item in body['created']),
              [item.get('review') for item in body['created']])

    admin_products = [p for p in made_products() if p.seller_id != seller_id]
    check('every admin listing is approved',
          admin_products and all(p.review_status == 'approved' for p in admin_products),
          [p.review_status for p in admin_products])
    check('and live, not parked',
          all(p.is_active for p in admin_products),
          [p.is_active for p in admin_products])
    check('each is digital with a file on disk',
          all(p.is_digital and p.file_path
              and os.path.exists(os.path.join(app.root_path, p.file_path.lstrip('/')))
              for p in admin_products))
    check('the commission came off the form, not the seller constant',
          all(abs((p.commission_percent or 0) - 12.5) < 0.01 for p in admin_products),
          [p.commission_percent for p in admin_products])
    check('platform stock is flagged as admin priority',
          all(p.admin_priority for p in admin_products),
          [p.admin_priority for p in admin_products])
    check('and carries no storefront pin, since an admin has no storefront',
          all(p.location_lat is None for p in admin_products),
          [p.location_lat for p in admin_products])
    check('the prefix is on the name',
          all(p.name.startswith('Nursing') for p in admin_products),
          [p.name for p in admin_products])

    print('the commission stays inside the platform band')
    # read_bulk_digital_form clamps to 10-15, the same band the single-product admin
    # form allows, so an out-of-band value is corrected rather than refused.
    with as_user(admin_id) as client:
        response = upload(client, '/admin/products/bulk/upload',
                          [(f'{TAG}_Band_High.pdf', PDF)], commission_percent='90')
        check('an out-of-band batch still uploads', response.status_code == 200,
              response.status_code)
    high = Product.query.filter(Product.name.ilike('%band high%')).first()
    check('and 90 percent was clamped to the ceiling',
          high is not None and abs((high.commission_percent or 0) - 15.0) < 0.01,
          high and high.commission_percent)

    with as_user(admin_id) as client:
        upload(client, '/admin/products/bulk/upload',
               [(f'{TAG}_Band_Low.pdf', PDF)], commission_percent='1')
    low = Product.query.filter(Product.name.ilike('%band low%')).first()
    check('and 1 percent was raised to the floor',
          low is not None and abs((low.commission_percent or 0) - 10.0) < 0.01,
          low and low.commission_percent)


def check_seller_default_untouched(seller_id):
    print("the seller default is not collateral damage")
    with as_user(seller_id) as client:
        response = upload(client, '/seller/products/bulk/upload',
                          [(f'{TAG}_Seller_Notes.pdf', PDF)])
        check('the seller batch is accepted', response.status_code == 200,
              response.status_code)
        body = response.get_json()
        check('one listing created', body['summary']['created'] == 1, body['summary'])

    seller_products = [p for p in made_products() if p.seller_id == seller_id]
    if app_module.DIGITAL_REVIEW_REQUIRED:
        check('a seller listing still parks for review',
              all(p.review_status == 'admin_review' and not p.is_active
                  for p in seller_products),
              [(p.review_status, p.is_active) for p in seller_products])
    else:
        check('with review off, a seller listing publishes too',
              all(p.review_status == 'approved' for p in seller_products),
              [p.review_status for p in seller_products])
    check('and it keeps the storefront pin',
          all(p.location_lat is not None for p in seller_products),
          [p.location_lat for p in seller_products])


def check_admin_only(seller_id):
    print('a seller cannot reach the admin batch')
    with as_user(seller_id) as client:
        for url in ('/admin/products/bulk', '/admin/products/bulk/upload',
                    '/admin/products/bulk/cover'):
            response = client.post(url, data={}, follow_redirects=False) \
                if url != '/admin/products/bulk' else client.get(url, follow_redirects=False)
            check(f'{url} is closed to a seller',
                  response.status_code in (302, 401, 403, 404), response.status_code)


def check_form_validation(admin_id):
    print('the admin form is no less strict about the things that matter')
    with as_user(admin_id) as client:
        response = upload(client, '/admin/products/bulk/upload',
                          [(f'{TAG}_norights.pdf', PDF)], owns_rights=None)
        check('no rights confirmation, no upload', response.status_code == 400,
              response.status_code)
        response = upload(client, '/admin/products/bulk/upload',
                          [(f'{TAG}_free.pdf', PDF)], selling_price='0')
        check('a zero price is refused', response.status_code == 400,
              response.status_code)
        response = upload(client, '/admin/products/bulk/upload', [])
        check('an empty batch is refused', response.status_code == 400,
              response.status_code)
        response = upload(client, '/admin/products/bulk/upload',
                          [(f'{TAG}_malware.exe', b'MZ\x90\x00' + b'0' * 64)])
        body = response.get_json()
        check('an executable is refused for an admin too',
              body['summary']['created'] == 0, body['summary'])


def run():
    check_location_guard()
    admin = make_admin()
    seller = make_seller()
    admin_id, seller_id = admin.id, seller.id
    check_admin_batch(admin_id, seller_id)
    check_seller_default_untouched(seller_id)
    check_admin_only(seller_id)
    check_form_validation(admin_id)


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
