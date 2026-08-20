"""Smoke check for automated second-hand phone ownership evidence.

Run with: python tools/phone_evidence_smoke.py

The verdict on a second-hand phone is reached by arithmetic and nobody looks at it
afterwards, so the arithmetic is the whole safety margin. That makes two kinds of
failure worth checking with equal weight:

  * a thief gets through - a duplicated IMEI, a handset still on a BNPL plan, one
    receipt photographed once and used on six listings, a made-up IMEI
  * an honest seller is turned away - a genuine submission of exactly the two
    required items has to clear the threshold, and an accessory whose name happens
    to contain 'phone' must never be dragged into the flow at all

Real image bytes throughout, because the scoring measures resolution and sharpness
off disk. Everything created here, rows and files, is removed at the end.
"""

import contextlib
import io
import os
import sys

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
os.environ.setdefault('CACHE_TYPE', 'NullCache')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app_module
from main import (CLOUDINARY_PUBLIC_FOLDERS, PHONE_EVIDENCE_FOLDER,
                  PHONE_EVIDENCE_MIN_SCORE, app, db, imei_checksum_valid,
                  is_phone_listing, phone_evidence_required, phone_listing_gate,
                  score_phone_evidence)
from models import (BNPLPlan, BusinessStorefront, Category,
                    KYCIdentityVerification, PhoneOwnershipEvidence, Product, User)

FAILURES = []
TAG = 'phevsmoke'
_PHONE_SEQ = 0

# Luhn-valid IMEIs. The check digit is what separates these from fifteen typed
# digits, which is the only thing a checksum can prove and the cheapest filter there
# is.
IMEI_A = '490154203237518'
IMEI_B = '356938035643809'
IMEI_C = '013964003725480'
IMEI_D = '352099001761481'


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + str(detail)) if detail else ""}')


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
        # Now that the outer session is current again, end its transaction. Reading any
        # attribute off a committed instance opens one, so by the time a request runs
        # the outer session is usually already holding a read view from before it - and
        # rows the request just changed still read at that older point. Rolling back
        # here means every check after a request sees what the request actually
        # committed, instead of each one having to remember to refresh.
        db.session.rollback()


def photo_bytes(width=1280, height=960, sharp=True, seed=1):
    """A JPEG that passes or fails the sharpness check, on purpose.

    Laplacian variance is what the scoring measures, so a flat fill would read as
    blurred no matter how large it is. Hard-edged noise gives strong second
    derivatives; a smooth gradient gives almost none.
    """
    from PIL import Image
    img = Image.new('RGB', (width, height))
    pixels = img.load()
    if sharp:
        for y in range(height):
            for x in range(0, width, 2):
                # A hard black/white checker: maximum second derivative per pixel.
                value = 255 if ((x // 2 + y + seed) % 2) else 0
                pixels[x, y] = (value, value, value)
                if x + 1 < width:
                    pixels[x + 1, y] = (255 - value, 255 - value, 255 - value)
        # The checker has only two states - (x // 2 + y + seed) % 2 turns on the
        # seed's parity and nothing else - so every odd seed rendered the same image
        # as every other odd seed, byte for byte. The duplicate-proof check is keyed
        # on exactly those bytes, so two photos meant to be unrelated were read as one
        # receipt submitted twice, and the checks below then passed or failed for a
        # reason this file never intended. A seed-dependent patch gives each its own
        # identity while leaving the sharpness the checker exists to provide.
        for offset in range(16):
            pixels[offset, 0] = ((seed * 37 + offset * 11) % 256,
                                 (seed * 17 + offset) % 256, seed % 256)
    else:
        for y in range(height):
            level = int(255 * y / max(height - 1, 1))
            for x in range(width):
                pixels[x, y] = (level, level, level)
    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=95)
    return buffer.getvalue()


def write_evidence_file(name, body):
    """Put bytes where the scoring reads them from, and return the stored URL."""
    folder = os.path.join(app.config['UPLOAD_FOLDER'], PHONE_EVIDENCE_FOLDER)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f'{TAG}_{name}')
    with open(path, 'wb') as handle:
        handle.write(body)
    return f'/static/uploads/{PHONE_EVIDENCE_FOLDER}/{TAG}_{name}'


def teardown():
    db.session.rollback()
    user_ids = [row[0] for row in db.session.query(User.id)
                .filter(User.username.like(f'{TAG}%')).all()] or [0]
    product_ids = [row[0] for row in db.session.query(Product.id)
                   .filter(Product.name.like(f'%{TAG}%')).all()] or [0]
    PhoneOwnershipEvidence.query.filter(
        db.or_(PhoneOwnershipEvidence.user_id.in_(user_ids),
               PhoneOwnershipEvidence.product_id.in_(product_ids))
    ).delete(synchronize_session=False)
    BNPLPlan.query.filter(BNPLPlan.user_id.in_(user_ids)).delete(synchronize_session=False)
    KYCIdentityVerification.query.filter(
        KYCIdentityVerification.user_id.in_(user_ids)).delete(synchronize_session=False)
    Product.query.filter(Product.id.in_(product_ids)).delete(synchronize_session=False)
    # After the products, because they point at it. Without this the script is not
    # rerunnable: a run that dies partway leaves the category behind and the next
    # run trips the unique slug on the way in.
    Category.query.filter(Category.slug.like(f'{TAG}%')).delete(synchronize_session=False)
    BusinessStorefront.query.filter(
        BusinessStorefront.business_name.like(f'{TAG}%')).delete(synchronize_session=False)
    User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)
    db.session.commit()

    folder = os.path.join(app.config['UPLOAD_FOLDER'], PHONE_EVIDENCE_FOLDER)
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.startswith(TAG):
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass
    app_module.invalidate_nav_categories()


def make_seller(suffix, storefront=True, kyc=True, admin=False):
    # User.phone is UNIQUE, and str.hash is salted per process - deriving the number
    # from it makes a collision between two suffixes possible on some runs and not
    # others, which is the worst kind of failing test. A counter is deterministic.
    global _PHONE_SEQ
    _PHONE_SEQ += 1
    user = User(username=f'{TAG}_{suffix}', email=f'{TAG}_{suffix}@example.invalid',
                phone=f'+25479801{_PHONE_SEQ:04d}')
    user.set_password('x')
    user.seller_status = 'verified'
    user.is_admin = admin
    for flag in ('is_verified_seller', 'is_seller'):
        if hasattr(user, flag):
            setattr(user, flag, True)
    db.session.add(user)
    db.session.commit()
    if storefront:
        db.session.add(BusinessStorefront(
            owner_id=user.id, business_name=f'{TAG} {suffix} Shop',
            slug=f'{TAG}-{suffix}-shop', status='approved',
            physical_address='Kimathi Street, Nairobi',
            location_lat=-1.2841, location_lng=36.8233))
    if kyc:
        # document_type and document_fingerprint are NOT NULL, and the fingerprint
        # is unique - one per seller, salted with the suffix.
        db.session.add(KYCIdentityVerification(
            user_id=user.id, status='approved', document_type='national_id',
            document_fingerprint=f'{TAG}{suffix}'.ljust(64, '0')[:64]))
    db.session.commit()
    return user


def make_phone(seller_id, suffix, condition='second_hand', name=None,
               category_id=None, active=False):
    product = Product(
        name=name or f'{TAG} Samsung Galaxy A14 {suffix}',
        slug=f'{TAG}-galaxy-{suffix}', seller_id=seller_id,
        selling_price=18000.0, buying_price=15000.0,
        description='A used handset listed for the smoke check.',
        short_description='Used handset', stock=1, is_active=active,
        product_condition=condition, review_status='pending_evidence',
        commission_percent=15.0, category_id=category_id)
    db.session.add(product)
    db.session.commit()
    return product


def check_detection():
    print('a phone is a phone, and an accessory is not')
    phones = ['Samsung Galaxy A14', 'iPhone 12 Pro Max', 'Tecno Spark 10',
              'Infinix Hot 30', 'Redmi Note 12', 'Second hand phone',
              'itel A60s handset', 'Nokia 105']
    accessories = ['Wireless earphones', 'Bluetooth headphones', 'Phone case',
                   'iPhone 12 screen protector', 'Samsung phone charger',
                   'Airpods Pro 2', 'Type-C cable', 'Tempered glass',
                   'Galaxy A14 flip cover', 'Nursing revision notes',
                   'Power bank 20000mAh']
    for name in phones:
        check(f'{name!r} is a phone', is_phone_listing(name))
    for name in accessories:
        check(f'{name!r} is not a phone', not is_phone_listing(name))

    # The category carries the signal when the name does not.
    category = Category(name=f'{TAG} Mobile Phones', slug=f'{TAG}-mobile-phones')
    db.session.add(category)
    db.session.commit()
    check('a bare model name under a phone category counts',
          is_phone_listing('A14 64GB', category))
    check('but an accessory under a phone category still does not',
          not is_phone_listing('A14 flip cover', category))
    return category


def check_luhn():
    print('the IMEI checksum rejects invented numbers')
    for value in (IMEI_A, IMEI_B, IMEI_C, IMEI_D):
        check(f'{value} passes Luhn', imei_checksum_valid(value))
    for value in ('123456789012345', '000000000000000', '111111111111111',
                  '49015420323751', '4901542032375180', '', None,
                  'abcdefghijklmno', '490154203237519'):
        check(f'{value!r} fails Luhn', not imei_checksum_valid(value))
    # Spaces and dashes are how a number gets copied off a screen.
    check('a spaced IMEI is still read', imei_checksum_valid('49 01 54 20 32 37 518'))
    check('and a dashed one', imei_checksum_valid('490154-203237-518'))


def check_requirement(category):
    print('the evidence requirement fires on exactly the right listings')
    seller = make_seller('req')
    used = make_phone(seller.id, 'req-used')
    check('a second-hand phone needs evidence', phone_evidence_required(used))

    refurb = make_phone(seller.id, 'req-refurb', condition='refurbished')
    check('so does a refurbished one', phone_evidence_required(refurb))

    new = make_phone(seller.id, 'req-new', condition='new')
    check('a new phone does not', not phone_evidence_required(new))

    accessory = make_phone(seller.id, 'req-acc',
                           name=f'{TAG} Samsung phone charger cable')
    check('nor does a used accessory', not phone_evidence_required(accessory))

    notes = make_phone(seller.id, 'req-doc', name=f'{TAG} Nursing revision notes')
    notes.is_digital = True
    db.session.commit()
    check('nor does a digital document', not phone_evidence_required(notes))

    bare = make_phone(seller.id, 'req-cat', name=f'{TAG} A14 64GB',
                      category_id=category.id)
    check('a model name under a phone category does', phone_evidence_required(bare))
    return seller


def check_gate():
    print('the gate wants a storefront, identity, or admin')
    no_shop = make_seller('nogate', storefront=False, kyc=False)
    blocked = phone_listing_gate(no_shop)
    check('no storefront is refused', bool(blocked), blocked)
    check('and the reason names the storefront', 'storefront' in (blocked or '').lower())

    no_kyc = make_seller('nokyc', storefront=True, kyc=False)
    blocked = phone_listing_gate(no_kyc)
    check('no KYC is refused', bool(blocked), blocked)
    check('and the reason names identity', 'identity' in (blocked or '').lower())

    full = make_seller('full')
    check('storefront plus KYC passes', phone_listing_gate(full) is None)

    admin = make_seller('admin', storefront=False, kyc=False, admin=True)
    check('an admin is exempt from both', phone_listing_gate(admin) is None)
    return full


def check_scoring(seller):
    print('a genuine submission of the two required items clears the threshold')
    good_imei_photo = write_evidence_file('imei_ok.jpg', photo_bytes(seed=1))
    good_proof = write_evidence_file('proof_ok.jpg', photo_bytes(seed=2))

    scored = score_phone_evidence(IMEI_A, good_imei_photo, good_proof, seller)
    check('it is not a hard fail', not scored['hard_fail'], scored['reasons'])
    check(f'it scores at or above {PHONE_EVIDENCE_MIN_SCORE:.0f}',
          scored['total_score'] >= PHONE_EVIDENCE_MIN_SCORE, scored['total_score'])
    check('the IMEI is marked valid and unique',
          scored['imei_valid'] and scored['uniqueness_ok'])
    check('a fingerprint was taken of the proof', bool(scored['proof_fingerprint']))

    print('each missing item is its own refusal, named')
    cases = [
        ('no IMEI', ('', good_imei_photo, good_proof), 'imei'),
        ('a made-up IMEI', ('123456789012345', good_imei_photo, good_proof), 'imei'),
        ('no IMEI photo', (IMEI_A, '', good_proof), 'screen'),
        ('no purchase proof', (IMEI_A, good_imei_photo, ''), 'proof'),
    ]
    for label, args, word in cases:
        result = score_phone_evidence(args[0], args[1], args[2], seller)
        check(f'{label} is refused', result['hard_fail'], result['reasons'])
        check(f'and {label} says why',
              any(word in reason.lower() for reason in result['reasons']),
              result['reasons'])
        check(f'and {label} carries no score to argue over',
              result['total_score'] == 0.0, result['total_score'])

    print('a photo too poor to read is refused, not just marked down')
    tiny = write_evidence_file('imei_tiny.jpg', photo_bytes(200, 150, seed=3))
    blurred = write_evidence_file('proof_blur.jpg', photo_bytes(sharp=False, seed=4))
    weak = score_phone_evidence(IMEI_B, tiny, blurred, seller)
    # A refusal rather than a deduction, because the weights cannot express it: a
    # flawless submission that forfeits every one of the 20 proof points still totals
    # 80, so no threshold on this score could require both photos to be readable. Each
    # band measures one property independently, which is how two photos named
    # unreadable used to add up to 76.75 and pass.
    check('a tiny IMEI photo and a blurred proof are refused', weak['hard_fail'],
          weak['reasons'])
    check('and the score is not left as something to argue over',
          weak['total_score'] < PHONE_EVIDENCE_MIN_SCORE, weak['total_score'])
    check('and the reasons name both photos',
          any('IMEI photo' in r for r in weak['reasons'])
          and any('Purchase proof' in r for r in weak['reasons']),
          weak['reasons'])

    print('a missing file on disk is not mistaken for a good photo')
    ghost = score_phone_evidence(IMEI_C, '/static/uploads/phone_docs/nope.jpg',
                                 good_proof, seller)
    check('a path with nothing behind it scores zero for that item',
          ghost['photo_score'] == 0.0, ghost['photo_score'])
    check('and lands below the threshold',
          ghost['total_score'] < PHONE_EVIDENCE_MIN_SCORE, ghost['total_score'])
    return good_imei_photo, good_proof


def check_conflicts(seller, good_imei_photo, good_proof):
    print('the same handset cannot be listed twice')
    other = make_seller('rival')
    listed = make_phone(other.id, 'rival-live', active=True)
    listed.review_status = 'approved'
    db.session.add(PhoneOwnershipEvidence(
        product_id=listed.id, user_id=other.id, imei=IMEI_D,
        imei_photo_path=good_imei_photo, proof_path=None,
        imei_valid=True, uniqueness_ok=True, total_score=88.0, status='approved'))
    db.session.commit()

    clash = score_phone_evidence(IMEI_D, good_imei_photo, good_proof, seller)
    check('an IMEI on another live listing is refused', clash['hard_fail'],
          clash['reasons'])
    check('and the reason says another account',
          any('another account' in r.lower() for r in clash['reasons']),
          clash['reasons'])

    print('a handset still being financed here cannot be resold')
    financed_imei = IMEI_C
    # product_id and principal_amount are NOT NULL on a plan; which product it is
    # does not matter here, only that the IMEI is under a live plan.
    financed_on = make_phone(seller.id, 'financed', condition='new')
    db.session.add(BNPLPlan(user_id=seller.id, product_id=financed_on.id,
                            principal_amount=18000.0, device_imei=financed_imei,
                            approval_status='approved'))
    db.session.commit()
    financed = score_phone_evidence(financed_imei, good_imei_photo, good_proof, seller)
    check('a live BNPL plan blocks the IMEI', financed['hard_fail'], financed['reasons'])
    check('and the reason names BNPL',
          any('bnpl' in r.lower() for r in financed['reasons']), financed['reasons'])

    print('a paid-off plan does not block the buyer from selling it on')
    plan = BNPLPlan.query.filter_by(device_imei=financed_imei).first()
    plan.approval_status = 'completed'
    db.session.commit()
    freed = score_phone_evidence(financed_imei, good_imei_photo, good_proof, seller)
    check('a completed plan releases the handset', not freed['hard_fail'],
          freed['reasons'])

    print('one receipt cannot serve six listings')
    reused_product = make_phone(other.id, 'rival-two')
    db.session.add(PhoneOwnershipEvidence(
        product_id=reused_product.id, user_id=other.id, imei=IMEI_B,
        proof_path=good_proof,
        proof_fingerprint=app_module.file_content_fingerprint(good_proof),
        imei_valid=True, uniqueness_ok=True, total_score=80.0, status='approved'))
    db.session.commit()
    recycled = score_phone_evidence(IMEI_A, good_imei_photo, good_proof, seller)
    check('a proof image already used elsewhere is refused', recycled['hard_fail'],
          recycled['reasons'])
    check('and the reason names the reuse',
          any('already' in r.lower() for r in recycled['reasons']), recycled['reasons'])

    print('but the same receipt twice for the same handset is a resubmission')
    # The path a rejected seller actually takes: retake the blurred IMEI photo, send
    # the receipt they already sent. proof_fingerprint is globally unique, so the
    # second attempt must not try to claim the image its own first attempt holds -
    # otherwise the resubmission this flow promises dies as an IntegrityError on
    # commit and the listing can never be fixed. Asserted at the database, because
    # that is the layer that would refuse it.
    retry_product = make_phone(seller.id, 'retry-same-proof')
    # A receipt of its own: good_proof was just claimed by the cross-listing case
    # above, and reusing it here would hard-fail for that reason instead of testing
    # this one.
    retry_proof = write_evidence_file('proof_retry.jpg', photo_bytes(seed=71))
    first_pass = score_phone_evidence(IMEI_C, good_imei_photo, retry_proof, seller,
                                      product_id=retry_product.id)
    check('the first attempt claims the proof image',
          bool(first_pass['proof_fingerprint']), first_pass['proof_fingerprint'])
    db.session.add(PhoneOwnershipEvidence(
        product_id=retry_product.id, user_id=seller.id, imei=IMEI_C,
        proof_path=retry_proof,
        # or None, exactly as record_phone_evidence does it: '' is a value, and two
        # empty strings collide under a UNIQUE constraint where two NULLs would not.
        # Without it a fingerprint that came back empty raises an IntegrityError here
        # and buries the check above that was trying to tell us why it was empty.
        proof_fingerprint=first_pass['proof_fingerprint'] or None,
        imei_valid=True, uniqueness_ok=True, total_score=62.0, status='auto_rejected'))
    db.session.commit()

    second_pass = score_phone_evidence(IMEI_C, good_imei_photo, retry_proof, seller,
                                       product_id=retry_product.id)
    check('the same receipt on the same listing is not called reuse',
          not second_pass['hard_fail'], second_pass['reasons'])
    check('and the second attempt does not re-claim the image',
          not second_pass['proof_fingerprint'],
          second_pass['proof_fingerprint'])
    db.session.add(PhoneOwnershipEvidence(
        product_id=retry_product.id, user_id=seller.id, imei=IMEI_C,
        proof_path=retry_proof,
        proof_fingerprint=second_pass['proof_fingerprint'] or None,
        imei_valid=True, uniqueness_ok=True, total_score=88.0, status='approved'))
    try:
        db.session.commit()
        stored = True
    except Exception as exc:
        db.session.rollback()
        stored = False
        print(f'         commit refused the resubmission: {exc}')
    check('so the resubmission commits', stored)
    check('with both attempts on the record',
          PhoneOwnershipEvidence.query.filter_by(product_id=retry_product.id).count() == 2)


def check_end_to_end(category):
    """The real routes, through the real form, for a real seller.

    The scoring is unit-checked above; what this adds is the parts only the route
    does - the first-listing hold, publishing on approval, and a rejection that is
    immediately resubmittable.
    """
    print('a first phone listing is held for one human look')
    app.config['WTF_CSRF_ENABLED'] = False
    seller = make_seller('e2e')
    product = make_phone(seller.id, 'e2e-one', category_id=category.id)
    seller_id, product_id = seller.id, product.id

    with as_user(seller_id) as client:
        page = client.get(f'/seller/products/{product_id}/ownership')
        check('the guided page renders', page.status_code == 200, page.status_code)
        check('and it does not oversell what the checks can do',
              b'read the words' in page.data or b'does not read' in page.data.lower(),
              page.status_code)

        response = client.post(
            f'/seller/products/{product_id}/ownership',
            data={'imei': IMEI_A,
                  'imei_photo': (io.BytesIO(photo_bytes(seed=11)), 'imei.jpg'),
                  'proof_file': (io.BytesIO(photo_bytes(seed=12)), 'receipt.jpg')},
            content_type='multipart/form-data', follow_redirects=False)
        check('the submission is accepted', response.status_code == 302,
              response.status_code)

    evidence = app_module.latest_phone_evidence(product_id)
    check('a row was written', evidence is not None)
    check('the first listing is held rather than auto-approved',
          evidence and evidence.status == 'manual_second_review', evidence and evidence.status)
    check('even though it scored a pass',
          evidence and evidence.total_score >= PHONE_EVIDENCE_MIN_SCORE,
          evidence and evidence.total_score)
    held = db.session.get(Product, product_id)
    check('the listing itself carries the hold',
          held.review_status == 'manual_second_review', held.review_status)
    check('and it stays down while it is held', not held.is_active, held.is_active)

    print('an admin decision publishes it and tells the seller')
    admin = make_seller('e2eadmin', storefront=False, kyc=False, admin=True)
    admin_id, evidence_id = admin.id, evidence.id
    with as_user(admin_id) as client:
        queue = client.get('/admin/phone-evidence')
        check('the admin queue renders', queue.status_code == 200, queue.status_code)
        check('and the waiting submission is on it',
              str(evidence_id).encode() in queue.data or b'Waiting' in queue.data)
        decided = client.post(f'/admin/phone-evidence/{evidence_id}/approve',
                              data={'note': 'Receipt matches the handset.'},
                              follow_redirects=False)
        check('approving redirects', decided.status_code == 302, decided.status_code)

    settled = db.session.get(PhoneOwnershipEvidence, evidence_id)
    db.session.refresh(settled)
    live = db.session.get(Product, product_id)
    db.session.refresh(live)
    check('the evidence is approved', settled.status == 'approved', settled.status)
    check('the listing is live', live.review_status == 'approved' and live.is_active,
          (live.review_status, live.is_active))
    check('the admin note is on the record',
          any('Receipt matches' in r for r in settled.reasons), settled.reasons)
    notice = app_module.CustomerNotification.query.filter_by(
        user_id=seller_id, product_id=product_id).first()
    check('the seller was told', notice is not None, notice and notice.title)

    print('a second listing from the same seller is decided automatically')
    second = make_phone(seller_id, 'e2e-two', category_id=category.id)
    second_id = second.id
    with as_user(seller_id) as client:
        client.post(f'/seller/products/{second_id}/ownership',
                    data={'imei': IMEI_B,
                          'imei_photo': (io.BytesIO(photo_bytes(seed=21)), 'imei2.jpg'),
                          'proof_file': (io.BytesIO(photo_bytes(seed=22)), 'receipt2.jpg')},
                    content_type='multipart/form-data')
    auto = app_module.latest_phone_evidence(second_id)
    check('no human was involved the second time',
          auto and auto.status == 'approved', auto and auto.status)
    published = db.session.get(Product, second_id)
    db.session.refresh(published)
    check('and it went live on its own',
          published.review_status == 'approved' and published.is_active,
          (published.review_status, published.is_active))

    print('a rejection is immediately resubmittable')
    third = make_phone(seller_id, 'e2e-three', category_id=category.id)
    third_id = third.id
    with as_user(seller_id) as client:
        client.post(f'/seller/products/{third_id}/ownership',
                    data={'imei': '123456789012345',
                          'imei_photo': (io.BytesIO(photo_bytes(seed=31)), 'imei3.jpg'),
                          'proof_file': (io.BytesIO(photo_bytes(seed=32)), 'receipt3.jpg')},
                    content_type='multipart/form-data')
        rejected = app_module.latest_phone_evidence(third_id)
        check('a bad IMEI is auto-rejected',
              rejected and rejected.status == 'auto_rejected',
              rejected and rejected.status)
        check('and the listing is down',
              not db.session.get(Product, third_id).is_active)

        client.post(f'/seller/products/{third_id}/ownership',
                    data={'imei': IMEI_C,
                          'imei_photo': (io.BytesIO(photo_bytes(seed=33)), 'imei4.jpg'),
                          'proof_file': (io.BytesIO(photo_bytes(seed=34)), 'receipt4.jpg')},
                    content_type='multipart/form-data')
    fixed = app_module.latest_phone_evidence(third_id)
    check('resubmitting straight away is accepted',
          fixed and fixed.status == 'approved', fixed and fixed.status)
    check('and the earlier attempt is still on the record',
          PhoneOwnershipEvidence.query.filter_by(product_id=third_id).count() == 2)

    print('an admin posting platform stock skips the flow entirely')
    with as_user(admin_id) as client:
        page = client.get('/admin/products/add')
        check('the admin form renders', page.status_code == 200, page.status_code)
        response = client.post('/admin/products/add', data={
            'name': f'{TAG} Refurbished iPhone 11 platform stock',
            'selling_price': '32000', 'buying_price': '26000',
            'stock': '2', 'product_condition': 'second_hand',
            'description': 'Platform stock with known history.',
            'short_description': 'Refurbished handset',
            'category_id': str(category.id), 'is_active': 'on',
            'device_imei': IMEI_D.replace('490154203237518', IMEI_D),
        }, follow_redirects=False)
        check('the admin listing saves', response.status_code in (302, 200),
              response.status_code)

    stock = Product.query.filter(Product.name.like(f'%{TAG}%platform stock%')).first()
    check('platform stock is approved without evidence',
          stock is not None and stock.review_status == 'approved',
          stock and stock.review_status)
    check('and honours the active flag on the form',
          stock is not None and stock.is_active, stock and stock.is_active)


def check_boundaries():
    print('evidence never leaves local disk')
    check('phone_docs is not a public Cloudinary folder',
          PHONE_EVIDENCE_FOLDER not in CLOUDINARY_PUBLIC_FOLDERS)
    check('nor a private one',
          PHONE_EVIDENCE_FOLDER not in getattr(app_module,
                                               'CLOUDINARY_PRIVATE_FOLDERS', set()))
    check('and it is the folder the flow actually uses',
          PHONE_EVIDENCE_FOLDER == 'phone_docs', PHONE_EVIDENCE_FOLDER)


def run():
    category = check_detection()
    check_luhn()
    check_requirement(category)
    seller = check_gate()
    good_imei_photo, good_proof = check_scoring(seller)
    check_conflicts(seller, good_imei_photo, good_proof)
    check_end_to_end(category)
    check_boundaries()
    Category.query.filter(Category.name.like(f'{TAG}%')).delete(synchronize_session=False)
    db.session.commit()


def main():
    # A request context, not just an app context: the scoring reads its evidence files
    # through uploaded_static_url_to_path, which asks url_for for the /static prefix,
    # and url_for needs a request to build a relative URL from. Production always has
    # one here. Running these checks without one is how the fingerprints silently came
    # back empty and every duplicate-evidence check passed for the wrong reason.
    with app.test_request_context('/'):
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
