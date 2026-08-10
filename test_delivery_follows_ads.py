"""Smoke test for delivery exclusions, store follows, Cloudinary, and social ads.

Copies the working database to a throwaway file so this never mutates real data.
Run with the base interpreter (the venv's ctypes is broken):

    PYTHONPATH=".:.venv/Lib/site-packages" \
      "C:/Users/euwin/AppData/Local/Programs/Python/Python314/python.exe" \
      test_delivery_follows_ads.py

Covers: seller country exclusions (and that Kenya can never be excluded), the
follow-a-shop toggle and deal announcements, the Daraja sandbox passkey fallback,
Cloudinary falling back to local storage, and the admin-only social ad queue.
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
    scratch = os.path.join(tempfile.mkdtemp(prefix='smark-delivery-'), 'test.db')
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
from models import (db, User, Product, Category, BusinessStorefront,  # noqa: E402
                    StorefrontFollow, SocialAdPost, AdCampaign,
                    CustomerNotification, Setting)

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
    product_cols = {c['name'] for c in inspector.get_columns('products')}
    check('products.excluded_countries migrated', 'excluded_countries' in product_cols)
    tables = set(inspector.get_table_names())
    check('storefront_follows table created', 'storefront_follows' in tables)
    check('social_ad_posts table created', 'social_ad_posts' in tables)

    Setting.set('seller_signup_enabled', '1')
    db.session.commit()

    category = Category.query.filter_by(is_active=True).first()
    if not category:
        category = Category(name='Electronics', slug='electronics', is_active=True)
        db.session.add(category)
        db.session.commit()

    seller = User(username='dfaseller', email='dfaseller@test.local',
                  password_hash='dummy', seller_status='verified',
                  is_verified_seller=True, country='Kenya')
    ke_buyer = User(username='dfakenyan', email='dfakenyan@test.local',
                    password_hash='dummy', country='Kenya')
    ug_buyer = User(username='dfaugandan', email='dfaugandan@test.local',
                    password_hash='dummy', country='Uganda')
    admin = User(username='dfaadmin', email='dfaadmin@test.local',
                 password_hash='dummy', is_admin=True)
    db.session.add_all([seller, ke_buyer, ug_buyer, admin])
    db.session.commit()

    storefront = BusinessStorefront(owner_id=seller.id, business_name='DFA Test Shop',
                                    slug='dfa-test-shop', status='approved')
    db.session.add(storefront)
    db.session.commit()

    product = Product(name='DFA Solar Lantern', slug='dfa-solar-lantern',
                      description='Solar lantern for the delivery tests.',
                      selling_price=2500.0, buying_price=1500.0, stock=5,
                      seller_id=seller.id, category_id=category.id,
                      is_active=True, review_status='approved',
                      excluded_countries='["Uganda"]')
    db.session.add(product)
    db.session.commit()

    seller_id, ke_id, ug_id, admin_id = seller.id, ke_buyer.id, ug_buyer.id, admin.id
    storefront_id, product_id, product_slug = storefront.id, product.id, product.slug

print('\n== exclusion helpers ==')
with app.app_context():
    p = db.session.get(Product, product_id)
    check('parsed exclusion list', main.product_excluded_countries(p) == ['Uganda'],
          main.product_excluded_countries(p))
    check('Ugandan destination blocked', main.product_delivery_blocked(p, 'Uganda'))
    check('Kenyan destination allowed', not main.product_delivery_blocked(p, 'Kenya'))
    check('unrelated country allowed', not main.product_delivery_blocked(p, 'Tanzania'))
    check('blank country allowed', not main.product_delivery_blocked(p, ''))

    # Kenya must never be excludable, whatever the form posts.
    check('Kenya dropped from excludable list', 'Kenya' not in main.excludable_countries())
    check('Kenya rejected by parser',
          main.parse_excluded_countries(['Kenya', 'Uganda']) == ['Uganda'],
          main.parse_excluded_countries(['Kenya', 'Uganda']))
    check('Kenyan county rejected by parser',
          main.parse_excluded_countries(['Nairobi', 'Mombasa', 'Kisumu']) == [],
          main.parse_excluded_countries(['Nairobi', 'Mombasa', 'Kisumu']))
    check('unknown country rejected',
          main.parse_excluded_countries(['Wakanda']) == [])
    check('duplicates collapsed',
          main.parse_excluded_countries(['Uganda', 'Uganda']) == ['Uganda'])
    check('empty serializes to None', main.serialize_excluded_countries([]) is None)

    # Corrupt or legacy values must not raise on a product page.
    broken = Product(name='DFA Broken JSON', slug='dfa-broken-json',
                     selling_price=100.0, stock=1, excluded_countries='not json')
    check('corrupt JSON tolerated', main.product_excluded_countries(broken) == [])
    scalar = Product(name='DFA Scalar', slug='dfa-scalar',
                     selling_price=100.0, stock=1, excluded_countries='"Uganda"')
    check('non-list JSON tolerated', main.product_excluded_countries(scalar) == [])

print('\n== buyers only see exclusions that affect them ==')
with app.test_client() as client:
    login(client, ug_id)
    r = client.get(f'/product/{product_slug}')
    check('Ugandan buyer loads product', r.status_code == 200, r.status_code)
    body = r.get_data(as_text=True)
    check('Ugandan buyer warned', 'does not deliver to Uganda' in body)

with app.test_client() as client:
    login(client, ke_id)
    r = client.get(f'/product/{product_slug}')
    check('Kenyan buyer loads product', r.status_code == 200, r.status_code)
    body = r.get_data(as_text=True)
    check('Kenyan buyer sees no warning', 'does not deliver to' not in body)
    check('follow-shop button present', 'Follow shop' in body)

with app.test_client() as client:
    r = client.get(f'/product/{product_slug}')
    check('anonymous buyer sees no warning',
          r.status_code == 200 and 'does not deliver to' not in r.get_data(as_text=True))
print('\n== seller form round trip ==')
with app.test_client() as client:
    login(client, seller_id)
    r = client.get('/seller/products')
    check('seller product form renders', r.status_code == 200, r.status_code)
    body = r.get_data(as_text=True)
    check('exclusion multi-select present', 'name="excluded_countries"' in body)
    check('Kenya not offered as excludable', '<option value="Kenya"' not in body)
    check('Uganda pre-selected for this seller', 'Uganda' in body)

    # A tampered post that names Kenya and a county must still be scrubbed.
    r = client.post(f'/seller/products/{product_id}', data={
        'name': 'DFA Solar Lantern',
        'selling_price': '2500',
        'buying_price': '1500',
        'stock': '5',
        'category_id': '',
        'product_condition': 'new',
        'excluded_countries': ['Kenya', 'Nairobi', 'Uganda', 'Tanzania'],
        'is_active': '1',
    }, follow_redirects=True)
    check('tampered save accepted', r.status_code == 200, r.status_code)

with app.app_context():
    saved = main.product_excluded_countries(db.session.get(Product, product_id))
    check('Kenya stripped server-side', 'Kenya' not in saved, saved)
    check('Kenyan county stripped server-side', 'Nairobi' not in saved, saved)
    check('legitimate exclusions kept',
          sorted(saved) == ['Tanzania', 'Uganda'], saved)

print('\n== checkout blocks a refused destination ==')
with app.app_context():
    p = db.session.get(Product, product_id)
    check('checkout guard sees Uganda blocked', main.product_delivery_blocked(p, 'Uganda'))
    check('checkout guard allows Kenya', not main.product_delivery_blocked(p, 'Kenya'))

print('\n== follow a shop ==')
with app.test_client() as client:
    login(client, ke_id)
    r = client.post(f'/storefront/{storefront_id}/follow', follow_redirects=True)
    check('follow accepted', r.status_code == 200, r.status_code)

with app.app_context():
    count = StorefrontFollow.query.filter_by(
        storefront_id=storefront_id, user_id=ke_id).count()
    check('follow row created', count == 1, count)

with app.test_client() as client:
    login(client, ke_id)
    client.post(f'/storefront/{storefront_id}/follow', follow_redirects=True)

with app.app_context():
    count = StorefrontFollow.query.filter_by(
        storefront_id=storefront_id, user_id=ke_id).count()
    check('second post unfollows (toggle)', count == 0, count)

with app.test_client() as client:
    login(client, ke_id)
    client.post(f'/storefront/{storefront_id}/follow', follow_redirects=True)

with app.app_context():
    storefront = db.session.get(BusinessStorefront, storefront_id)
    check('follower count helper', main.storefront_follower_count(storefront) == 1,
          main.storefront_follower_count(storefront))

print('\n== announcing a deal reaches followers only ==')
with app.app_context():
    before_ke = CustomerNotification.query.filter_by(user_id=ke_id).count()
    before_ug = CustomerNotification.query.filter_by(user_id=ug_id).count()

with app.test_client() as client:
    login(client, seller_id)
    r = client.get('/storefront/apply')
    check('storefront page renders', r.status_code == 200, r.status_code)
    body = r.get_data(as_text=True)
    check('opening hours input removed', 'name="opening_hours"' not in body)
    check('announce form present', 'name="announcement"' in body)
    check('follower count shown', 'Followers' in body)

    r = client.post('/storefront/apply', data={
        'action': 'announce',
        'announcement': 'Clearance weekend: 30% off all lanterns.',
    }, follow_redirects=True)
    check('announcement accepted', r.status_code == 200, r.status_code)

with app.app_context():
    after_ke = CustomerNotification.query.filter_by(user_id=ke_id).count()
    after_ug = CustomerNotification.query.filter_by(user_id=ug_id).count()
    check('follower notified', after_ke == before_ke + 1, (before_ke, after_ke))
    check('non-follower untouched', after_ug == before_ug, (before_ug, after_ug))
    latest = CustomerNotification.query.filter_by(user_id=ke_id).order_by(
        CustomerNotification.id.desc()).first()
    check('notification body carries the message',
          latest is not None and 'Clearance weekend' in (latest.body or ''))
    check('notification starts unread', latest is not None and not latest.is_read)

with app.test_client() as client:
    login(client, seller_id)
    r = client.post('/storefront/apply', data={'action': 'announce', 'announcement': '  '},
                    follow_redirects=True)
    check('blank announcement rejected',
          'Write the deal' in r.get_data(as_text=True))

print('\n== Daraja sandbox passkey fallback ==')
with app.app_context():
    Setting.set('daraja_env', 'sandbox')
    Setting.set('daraja_passkey', 'N/A')
    Setting.set('daraja_consumer_key', 'sandbox-key')
    Setting.set('daraja_consumer_secret', 'sandbox-secret')
    Setting.set('daraja_shortcode', '174379')
    db.session.commit()
    check('sandbox passkey falls back',
          main.daraja_passkey() == main.DARAJA_SANDBOX_PASSKEY)
    check('fallback flagged in diagnostics',
          main.daraja_using_sandbox_default_passkey())
    check('no config error in sandbox', main.daraja_config_error() == '',
          main.daraja_config_error())

    Setting.set('daraja_env', 'production')
    db.session.commit()
    check('production still refuses N/A', main.daraja_passkey() == '')
    check('production reports the missing passkey',
          'passkey' in main.daraja_config_error().lower(),
          main.daraja_config_error())
    check('production not flagged as sandbox default',
          not main.daraja_using_sandbox_default_passkey())

    Setting.set('daraja_env', 'sandbox')
    db.session.commit()

print('\n== Cloudinary stays optional ==')
with app.app_context():
    Setting.set('cloudinary_cloud_name', '')
    Setting.set('cloudinary_api_key', '')
    Setting.set('cloudinary_api_secret', '')
    db.session.commit()
    check('disabled without credentials', not main.cloudinary_enabled())
    check('upload helper returns None when disabled',
          main.upload_to_cloudinary(b'x', folder='products') is None)
    status = main.cloudinary_status()
    check('status reports disabled', status['enabled'] is False, status)

    Setting.set('cloudinary_cloud_name', 'demo')
    Setting.set('cloudinary_api_key', 'key')
    Setting.set('cloudinary_api_secret', 'secret')
    db.session.commit()
    creds = main.cloudinary_credentials()
    check('credentials read from settings', creds['cloud_name'] == 'demo', creds)

    # Private folders must never leave this server, even fully configured.
    check('digital downloads never go to Cloudinary',
          not main.cloudinary_allowed_for('digital'))
    check('KYC documents never go to Cloudinary',
          not main.cloudinary_allowed_for('seller_docs'))

    Setting.set('cloudinary_cloud_name', '')
    Setting.set('cloudinary_api_key', '')
    Setting.set('cloudinary_api_secret', '')
    db.session.commit()
print('\n== social ads: payment gate and admin-only access ==')
with app.app_context():
    unpaid = AdCampaign(seller_id=seller_id, product_id=product_id, platform='Instagram',
                        placement='social', budget=1000.0, total_charged=1000.0,
                        ad_copy='Solar lanterns, half price this week.',
                        status='pending_payment')
    paid = AdCampaign(seller_id=seller_id, product_id=product_id, platform='Instagram',
                      placement='social', budget=1000.0, total_charged=1000.0,
                      ad_copy='Clearance lanterns now live.', status='pending_approval')
    house = AdCampaign(seller_id=admin_id, platform='Instagram', placement='social',
                       budget=0.0, total_charged=0.0, status='active')
    db.session.add_all([unpaid, paid, house])
    db.session.commit()
    unpaid_id, paid_id, house_id = unpaid.id, paid.id, house.id

    check('unpaid campaign is not postable',
          not main.social_ad_campaign_is_paid(unpaid))
    check('paid campaign is postable', main.social_ad_campaign_is_paid(paid))
    check('free house ad is postable', main.social_ad_campaign_is_paid(house))
    check('house post with no campaign is postable',
          main.social_ad_campaign_is_paid(None))
    check('hashtag counter', main.social_ad_hashtag_count('#a #b #c') == 3)

print('\n== non-admins are shut out ==')
with app.test_client() as client:
    login(client, seller_id)
    for path in ['/admin/social-ads',
                 '/admin/social-ads/new',
                 '/admin/social-ads/1/edit']:
        r = client.get(path)
        check(f'seller blocked from {path}', r.status_code in (302, 403), r.status_code)
    for path in ['/admin/social-ads/1/mark-posted', '/admin/social-ads/1/archive']:
        r = client.post(path, data={'posted_url': 'https://instagram.com/p/x'})
        check(f'seller blocked from POST {path}', r.status_code in (302, 403), r.status_code)

print('\n== admin composes and records a post ==')
with app.test_client() as client:
    login(client, admin_id)
    r = client.get('/admin/social-ads')
    check('queue renders', r.status_code == 200, r.status_code)
    body = r.get_data(as_text=True)
    check('paid campaign offers Compose', 'Compose' in body)
    check('unpaid campaign offers Mark paid', 'Mark paid' in body)

    r = client.get(f'/admin/social-ads/new?campaign_id={unpaid_id}', follow_redirects=True)
    check('composer refuses an unpaid campaign',
          'has not been paid for' in r.get_data(as_text=True))

    r = client.get(f'/admin/social-ads/new?campaign_id={paid_id}')
    check('composer opens for a paid campaign', r.status_code == 200, r.status_code)
    body = r.get_data(as_text=True)
    check('caption pre-filled from the seller copy', 'Clearance lanterns now live' in body)

    r = client.post(f'/admin/social-ads/new?campaign_id={paid_id}', data={
        'platform': 'instagram',
        'caption': 'Clearance lanterns now live at SMARKAFRICA.',
        'hashtags': '#smarkafrica #solar #kenya',
        'creative_url': 'https://example.com/lantern.jpg',
    }, follow_redirects=True)
    check('draft saved', r.status_code == 200, r.status_code)

    # Caption and hashtag limits are enforced server-side, not just in the browser.
    r = client.post('/admin/social-ads/new', data={
        'platform': 'instagram',
        'caption': 'x' * (main.SOCIAL_AD_CAPTION_LIMIT + 1),
    }, follow_redirects=True)
    check('over-long caption rejected', 'characters' in r.get_data(as_text=True))

    r = client.post('/admin/social-ads/new', data={
        'platform': 'instagram',
        'caption': 'Fine caption.',
        'hashtags': ' '.join(f'#tag{i}' for i in range(31)),
    }, follow_redirects=True)
    check('31 hashtags rejected', '30 hashtags' in r.get_data(as_text=True))

    r = client.post('/admin/social-ads/new', data={'platform': 'instagram', 'caption': '  '},
                    follow_redirects=True)
    check('blank caption rejected', 'Write a caption' in r.get_data(as_text=True))

with app.app_context():
    post = SocialAdPost.query.filter_by(campaign_id=paid_id).first()
    check('draft persisted', post is not None)
    check('draft starts as draft', post is not None and post.status == 'draft',
          post.status if post else None)
    post_id = post.id if post else 0

    # A draft attached to an unpaid campaign, to prove the gate holds at mark-posted.
    blocked_post = SocialAdPost(campaign_id=unpaid_id, platform='instagram',
                               caption='Should not go live.', status='draft')
    db.session.add(blocked_post)
    db.session.commit()
    blocked_id = blocked_post.id
    before_seller_notes = CustomerNotification.query.filter_by(user_id=seller_id).count()

with app.test_client() as client:
    login(client, admin_id)
    r = client.post(f'/admin/social-ads/{blocked_id}/mark-posted',
                    data={'posted_url': 'https://instagram.com/p/blocked'},
                    follow_redirects=True)
    check('unpaid campaign cannot be marked posted',
          'has not been paid for' in r.get_data(as_text=True))

    r = client.post(f'/admin/social-ads/{post_id}/mark-posted',
                    data={'posted_url': 'not-a-url'}, follow_redirects=True)
    check('bad URL rejected', 'full link' in r.get_data(as_text=True))

    r = client.post(f'/admin/social-ads/{post_id}/mark-posted',
                    data={'posted_url': 'https://instagram.com/p/abc123'},
                    follow_redirects=True)
    check('paid post marked live', r.status_code == 200, r.status_code)

with app.app_context():
    blocked_post = db.session.get(SocialAdPost, blocked_id)
    check('blocked post still a draft', blocked_post.status == 'draft', blocked_post.status)

    post = db.session.get(SocialAdPost, post_id)
    check('status is posted', post.status == 'posted', post.status)
    check('live URL stored', post.posted_url == 'https://instagram.com/p/abc123',
          post.posted_url)
    check('posted_at stamped', post.posted_at is not None)
    check('posted_by recorded', post.posted_by_id == admin_id, post.posted_by_id)

    after_seller_notes = CustomerNotification.query.filter_by(user_id=seller_id).count()
    check('seller notified their ad is live',
          after_seller_notes == before_seller_notes + 1,
          (before_seller_notes, after_seller_notes))
    note = CustomerNotification.query.filter_by(user_id=seller_id).order_by(
        CustomerNotification.id.desc()).first()
    check('notification carries the link',
          note is not None and 'instagram.com/p/abc123' in (note.body or ''))

with app.test_client() as client:
    login(client, admin_id)
    r = client.post(f'/admin/social-ads/{post_id}/archive', follow_redirects=True)
    check('archive accepted', r.status_code == 200, r.status_code)

with app.app_context():
    check('post archived', db.session.get(SocialAdPost, post_id).status == 'archived')

print('\n' + '=' * 60)
if FAILURES:
    print(f'{len(FAILURES)} FAILED:')
    for label in FAILURES:
        print(f'  - {label}')
    sys.exit(1)
print('All checks passed.')
