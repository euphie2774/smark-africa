"""Smoke test for MVP-issued referral promo codes.

Copies the working database to a throwaway file so this never mutates real data.
Run with the base interpreter (the venv's ctypes is broken):

    PYTHONPATH=".:.venv/Lib/site-packages" \
      "C:/Users/euwin/AppData/Local/Programs/Python/Python314/python.exe" \
      test_promo_codes.py

Covers: the MVP issuing a code to a chosen customer, the KES 1,000 goods floor,
self-use and reuse blocks, the redemption cap and expiry, the 10% coming off
goods but never off delivery, and the introducer being paid coins exactly once
when the money actually lands.
"""
import os
import shutil
import tempfile

REPO = os.path.dirname(os.path.abspath(__file__))


def _scratch_database():
    """Clone the dev database so the test can write freely."""
    candidates = [
        os.path.join(REPO, 'instance', 'smarkafrica.db'),
        os.path.join(REPO, 'smarkafrica.db'),
    ]
    source = next((p for p in candidates if os.path.exists(p)), None)
    scratch = os.path.join(tempfile.mkdtemp(prefix='smark-promo-'), 'test.db')
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
                    PromoCode, PromoCodeRedemption, CoinTransaction,
                    CustomerNotification, Setting)
from datetime import timedelta  # noqa: E402

app = main.app
app.config['TESTING'] = True
app.config['WTF_CSRF_ENABLED'] = False

FAILURES = []


def check(label, condition, detail=''):
    print(f'  [{"PASS" if condition else "FAIL"}] {label}'
          f'{(" -> " + str(detail)) if detail else ""}')
    if not condition:
        FAILURES.append(label)


def login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


with app.app_context():
    print(f'== schema (scratch db: {SCRATCH_DB}) ==')
    main.ensure_phase_two_schema()
    db.create_all()
    db.session.commit()

    inspector = db.inspect(db.engine)
    tables = set(inspector.get_table_names())
    check('promo_codes table created', 'promo_codes' in tables)
    check('promo_code_redemptions table created', 'promo_code_redemptions' in tables)
    order_cols = {c['name'] for c in inspector.get_columns('orders')}
    check('orders.promo_code_id migrated', 'promo_code_id' in order_cols)

    category = Category.query.filter_by(is_active=True).first()
    if not category:
        category = Category(name='Electronics', slug='electronics', is_active=True)
        db.session.add(category)
        db.session.commit()

    mvp = User(username='promomvp', email='promomvp@test.local', password_hash='dummy',
               is_admin=True, admin_level='mvp')
    plain_admin = User(username='promoadmin', email='promoadmin@test.local',
                       password_hash='dummy', is_admin=True, admin_level='admin')
    owner = User(username='promoowner', email='promoowner@test.local',
                 password_hash='dummy', country='Kenya')
    friend = User(username='promofriend', email='promofriend@test.local',
                  password_hash='dummy', country='Kenya')
    stranger = User(username='promostranger', email='promostranger@test.local',
                    password_hash='dummy', country='Kenya')
    seller = User(username='promoseller', email='promoseller@test.local',
                  password_hash='dummy', seller_status='verified', country='Kenya')
    db.session.add_all([mvp, plain_admin, owner, friend, stranger, seller])
    db.session.commit()

    product = Product(name='Promo Test Kettle', slug='promo-test-kettle',
                      description='Kettle for the promo code tests.',
                      selling_price=1200.0, buying_price=800.0, stock=50,
                      seller_id=seller.id, category_id=category.id,
                      is_active=True, review_status='approved')
    db.session.add(product)
    db.session.commit()

    Setting.set('coins_referral_bonus', '50')
    db.session.commit()

    mvp_id, admin_id = mvp.id, plain_admin.id
    owner_id, friend_id, stranger_id = owner.id, friend.id, stranger.id
    product_id = product.id


print('\n== only the MVP can issue codes ==')
with app.test_client() as client:
    login(client, admin_id)
    r = client.get('/admin/promo-codes')
    check('a plain admin is turned away', r.status_code == 302, r.status_code)
    r = client.post('/admin/promo-codes/issue',
                    data={'owner_id': owner_id, 'reason': 'sneaking in'},
                    follow_redirects=False)
    check('and cannot post one either', r.status_code == 302, r.status_code)

with app.app_context():
    check('no code was created', PromoCode.query.count() == 0)

with app.test_client() as client:
    login(client, mvp_id)
    r = client.get('/admin/promo-codes')
    check('the MVP gets the page', r.status_code == 200, r.status_code)

    # The customer is looked up rather than listed, so the issue form (and the
    # note about delivery) only appears once a search has matched somebody.
    r = client.get('/admin/promo-codes?search=promoowner')
    found = r.get_data(as_text=True)
    check('searching finds the customer', 'promoowner' in found)
    check('the form explains delivery is never discounted',
          'delivery is always charged in full' in found)

    r = client.post('/admin/promo-codes/issue',
                    data={'owner_id': owner_id, 'reason': ''},
                    follow_redirects=True)
    check('a code without a reason is refused',
          'say why this customer' in r.get_data(as_text=True).lower())

with app.app_context():
    check('still no code created', PromoCode.query.count() == 0)

print('\n== issuing a code to a chosen customer ==')
with app.test_client() as client:
    login(client, mvp_id)
    r = client.post('/admin/promo-codes/issue',
                    data={'owner_id': owner_id,
                          'reason': 'Loyal buyer since launch',
                          'discount_percent': '10',
                          'min_order_amount': '1000',
                          'owner_coins': '50'},
                    follow_redirects=True)
    check('issuing succeeds', r.status_code == 200, r.status_code)

with app.app_context():
    promo = PromoCode.query.filter_by(owner_id=owner_id).first()
    check('the code exists', promo is not None)
    check('it belongs to the chosen customer', promo.owner_id == owner_id)
    check('the reason is kept on the record',
          promo.reason == 'Loyal buyer since launch', promo.reason)
    check('10% by default', promo.discount_percent == 10.0, promo.discount_percent)
    check('KES 1,000 floor by default', promo.min_order_amount == 1000.0,
          promo.min_order_amount)
    check('the code is readable - no O/0 or I/1 confusion',
          not set('O0I1') & set(promo.code), promo.code)
    check('the owner was told about it',
          CustomerNotification.query.filter_by(
              user_id=owner_id, notification_type='promo_code').count() == 1)
    promo_id, promo_code = promo.id, promo.code

print('\n== who may use it, and on what ==')
with app.app_context():
    promo = db.session.get(PromoCode, promo_id)
    owner = db.session.get(User, owner_id)
    friend = db.session.get(User, friend_id)

    # The floor is the whole point of the rule the MVP set: goods worth 1000+.
    check('rejected below the floor',
          'Spend KES 1,000' in (main.promo_code_error(promo, friend, 999.0) or ''),
          main.promo_code_error(promo, friend, 999.0))
    check('accepted exactly at the floor',
          main.promo_code_error(promo, friend, 1000.0) is None)
    check('accepted above the floor',
          main.promo_code_error(promo, friend, 2400.0) is None)

    check('the owner cannot use their own code',
          'your own code' in (main.promo_code_error(promo, owner, 5000.0) or ''),
          main.promo_code_error(promo, owner, 5000.0))
    check('an unknown code is rejected',
          main.promo_code_error(None, friend, 5000.0) is not None)
    check('a signed-out shopper is rejected',
          main.promo_code_error(promo, None, 5000.0) is not None)

    check('10% of 2,400 is 240', main.promo_discount_amount(promo, 2400.0) == 240.0,
          main.promo_discount_amount(promo, 2400.0))
    check('a zero subtotal discounts nothing',
          main.promo_discount_amount(promo, 0) == 0.0)
    check('a negative subtotal cannot pay the shopper',
          main.promo_discount_amount(promo, -500) == 0.0)

    # Codes are matched case- and space-insensitively: shoppers retype them
    # off a WhatsApp message.
    messy = f'  {promo_code.lower()} '
    check('a messily typed code still matches',
          main.find_promo_code(messy) is not None, messy)
    evaluated = main.evaluate_promo_code(messy, friend, 2400.0)
    check('and evaluates to the same discount', evaluated['discount'] == 240.0,
          evaluated['discount'])
    check('an empty box is not an error',
          main.evaluate_promo_code('', friend, 2400.0)['error'] is None)

print('\n== checkout applies it to goods, never to delivery ==')
with app.app_context():
    friend = db.session.get(User, friend_id)
    promo = db.session.get(PromoCode, promo_id)
    # Two kettles = 2,400 goods, clear of the floor.
    order = Order(user_id=friend_id, order_number='PROMO-TEST-1',
                  amount_paid=2400.0 - 240.0 + 300.0,
                  shipping_cost=300.0, discount_amount=240.0,
                  promo_code_id=promo.id, payment_status='pending',
                  status='pending')
    db.session.add(order)
    db.session.flush()
    db.session.add(OrderItem(order_id=order.id, product_id=product_id,
                             product_name='Promo Test Kettle', price=1200.0,
                             quantity=2, is_digital=False))
    db.session.commit()
    order_id = order.id

    check('the shopper pays goods less 10%, plus full delivery',
          order.amount_paid == 2460.0, order.amount_paid)
    check('delivery was not discounted', order.shipping_cost == 300.0)

print('\n== the introducer is paid only when the money lands ==')
with app.app_context():
    check('no coins before payment',
          CoinTransaction.query.filter_by(user_id=owner_id).count() == 0)

    order = db.session.get(Order, order_id)
    main.finalize_paid_order(order)
    db.session.commit()

    redemptions = PromoCodeRedemption.query.filter_by(promo_code_id=promo_id).all()
    check('the redemption was recorded', len(redemptions) == 1, len(redemptions))
    check('it names the shopper, not the owner',
          redemptions[0].user_id == friend_id)
    check('it stores what was taken off', redemptions[0].discount_amount == 240.0,
          redemptions[0].discount_amount)
    check('it stores the goods value it was measured on',
          redemptions[0].order_subtotal == 2400.0, redemptions[0].order_subtotal)

    coins = CoinTransaction.query.filter_by(user_id=owner_id).all()
    check('the introducer earned coins', len(coins) == 1, len(coins))
    check('50 coins, per the referral setting', coins[0].amount == 50, coins[0].amount)
    check('booked as a referral', coins[0].coin_type == 'referral', coins[0].coin_type)

    promo = db.session.get(PromoCode, promo_id)
    check('the counter moved', promo.times_used == 1, promo.times_used)
    check('the running discount total moved',
          promo.total_discount_given == 240.0, promo.total_discount_given)
    check('the running coin total moved',
          promo.total_coins_awarded == 50, promo.total_coins_awarded)
    check('the owner was notified of the use',
          CustomerNotification.query.filter_by(
              user_id=owner_id, notification_type='promo_code').count() == 2)

print('\n== a second finalize must not pay twice ==')
with app.app_context():
    order = db.session.get(Order, order_id)
    main.finalize_paid_order(order)
    main.record_promo_redemption(order)
    db.session.commit()

    check('still one redemption',
          PromoCodeRedemption.query.filter_by(promo_code_id=promo_id).count() == 1)
    check('still one coin payment',
          CoinTransaction.query.filter_by(user_id=owner_id).count() == 1)
    promo = db.session.get(PromoCode, promo_id)
    check('the counter did not double', promo.times_used == 1, promo.times_used)

print('\n== each shopper gets one go ==')
with app.app_context():
    promo = db.session.get(PromoCode, promo_id)
    friend = db.session.get(User, friend_id)
    stranger = db.session.get(User, stranger_id)
    check('the shopper who used it is blocked',
          'already used' in (main.promo_code_error(promo, friend, 2400.0) or ''),
          main.promo_code_error(promo, friend, 2400.0))
    check('but a new shopper may still use it',
          main.promo_code_error(promo, stranger, 2400.0) is None)

print('\n== caps, expiry and the off switch ==')
with app.app_context():
    stranger = db.session.get(User, stranger_id)
    promo = db.session.get(PromoCode, promo_id)

    promo.max_redemptions = 1
    db.session.commit()
    check('a reached cap closes the code',
          'reached its limit' in (main.promo_code_error(promo, stranger, 2400.0) or ''),
          main.promo_code_error(promo, stranger, 2400.0))
    promo.max_redemptions = 5
    db.session.commit()
    check('raising the cap reopens it',
          main.promo_code_error(promo, stranger, 2400.0) is None)

    promo.expires_at = main.utcnow() - timedelta(days=1)
    db.session.commit()
    check('an expired code is refused',
          'expired' in (main.promo_code_error(promo, stranger, 2400.0) or ''),
          main.promo_code_error(promo, stranger, 2400.0))
    promo.expires_at = main.utcnow() + timedelta(days=30)
    db.session.commit()
    check('a future expiry is fine',
          main.promo_code_error(promo, stranger, 2400.0) is None)

with app.test_client() as client:
    login(client, mvp_id)
    r = client.post(f'/admin/promo-codes/{promo_id}/toggle', follow_redirects=True)
    check('the MVP can switch a code off', r.status_code == 200, r.status_code)

with app.app_context():
    promo = db.session.get(PromoCode, promo_id)
    stranger = db.session.get(User, stranger_id)
    check('it is now inactive', promo.is_active is False)
    check('and refuses to apply',
          'no longer active' in (main.promo_code_error(promo, stranger, 2400.0) or ''),
          main.promo_code_error(promo, stranger, 2400.0))
    promo.is_active = True
    db.session.commit()

print('\n== the shopper sees it on the checkout page ==')
with app.test_client() as client:
    login(client, stranger_id)
    client.post(f'/cart/add/{product_id}', data={'quantity': 2},
                follow_redirects=True)
    r = client.get('/checkout')
    check('checkout renders', r.status_code == 200, r.status_code)
    body = r.get_data(as_text=True)
    check('there is a promo code box', 'name="promo_code"' in body)

    r = client.get(f'/checkout?promo_code={promo_code}')
    applied = r.get_data(as_text=True)
    check('applying it shows the saving', 'Code applied' in applied)
    check('and names the code', promo_code in applied)

    r = client.get('/checkout?promo_code=NOTAREALCODE')
    rejected = r.get_data(as_text=True)
    check('a bad code is reported, not silently ignored',
          'not recognised' in rejected)

print('\n== the owner sees their code on the coins page ==')
with app.test_client() as client:
    login(client, owner_id)
    r = client.get('/coins')
    check('coins page renders', r.status_code == 200, r.status_code)
    body = r.get_data(as_text=True)
    check('the code is shown to its owner', promo_code in body)

    login(client, stranger_id)
    other = client.get('/coins').get_data(as_text=True)
    check('but not to somebody else', promo_code not in other)

print('\n' + ('ALL CHECKS PASSED' if not FAILURES
              else f'{len(FAILURES)} FAILED: ' + ', '.join(FAILURES)))
