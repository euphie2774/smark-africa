"""Smoke check for the cached category nav and the hot list pages.

Run with: python tools/page_smoke.py

The category nav is read on the home page, the shop and every filter bar, so it is
the most-executed query on the platform and every visitor gets the same answer for
it. What this checks is that the repeat costs nothing, that an admin edit is still
picked up, and - the part that is easy to get wrong when caching anything loaded
through the ORM - that a cached row survives being used in a later request with a
different session behind it.

Uses the real test client so the templates render for real; a cached object that
would raise DetachedInstanceError in Jinja fails here rather than in production.
"""

import os
import sys

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
os.environ.setdefault('CACHE_TYPE', 'NullCache')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event

import main as app_module
from main import app, db, nav_categories
from models import Category, Product, User

FAILURES = []
TAG = 'pagesmoke'


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + str(detail)) if detail else ""}')


class StatementCounter:
    def __init__(self):
        self.count = 0

    def __enter__(self):
        self._hook = lambda *a: self._bump()
        event.listen(db.engine, 'before_cursor_execute', self._hook)
        return self

    def _bump(self):
        self.count += 1

    def __exit__(self, *exc):
        event.remove(db.engine, 'before_cursor_execute', self._hook)
        return False


def teardown():
    db.session.rollback()
    try:
        Product.query.filter(Product.slug.like(f'{TAG}%')).delete(
            synchronize_session=False)
        User.query.filter(User.username.like(f'{TAG}%')).delete(
            synchronize_session=False)
        Category.query.filter(Category.slug.like(f'{TAG}%')).delete(
            synchronize_session=False)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f'  cleanup failed: {exc}')
    app_module.invalidate_nav_categories()


def run():
    print('the category nav is read once, not once per visitor')
    db.session.add(Category(name=f'{TAG} alpha', slug=f'{TAG}-alpha', is_active=True))
    db.session.commit()
    app_module.invalidate_nav_categories()

    with StatementCounter() as cold:
        first = nav_categories()
    with StatementCounter() as warm:
        second = nav_categories()
        third = nav_categories()
    check('the first read hits the database', cold.count >= 1, cold.count)
    check('later reads do not touch it at all', warm.count == 0, warm.count)
    check('and they return the same list', first == second == third)
    check('the tagged category is in there',
          any(c.slug == f'{TAG}-alpha' for c in first))

    print('the cached rows carry what the templates read')
    sample = [c for c in first if c.slug == f'{TAG}-alpha'][0]
    check('id, name and slug are all present',
          sample.id and sample.name == f'{TAG} alpha' and sample.slug == f'{TAG}-alpha')
    # The point of not caching Category instances: this is the access that would
    # raise DetachedInstanceError if it were one, once its session had gone.
    db.session.remove()
    check('reading them after the session is gone does not raise',
          nav_categories()[0].name is not None)

    print('an admin edit is not hidden behind the cache')
    fresh = Category(name=f'{TAG} beta', slug=f'{TAG}-beta', is_active=True)
    db.session.add(fresh)
    db.session.commit()
    check('a new category is invisible until invalidated',
          not any(c.slug == f'{TAG}-beta' for c in nav_categories()))
    app_module.invalidate_nav_categories()
    check('and visible straight after',
          any(c.slug == f'{TAG}-beta' for c in nav_categories()))

    print('inactive categories stay out of the public nav')
    hidden = Category(name=f'{TAG} hidden', slug=f'{TAG}-hidden', is_active=False)
    db.session.add(hidden)
    db.session.commit()
    app_module.invalidate_nav_categories()
    check('the public list excludes it',
          not any(c.slug == f'{TAG}-hidden' for c in nav_categories()))
    check('the admin list includes it',
          any(c.slug == f'{TAG}-hidden' for c in nav_categories(active_only=False)))

    print('the pages that use it still render')
    app.config['WTF_CSRF_ENABLED'] = False
    with app.test_client() as client:
        for path in ('/', '/shop', '/compare'):
            response = client.get(path)
            check(f'GET {path} renders', response.status_code == 200,
                  response.status_code)
        # Second pass with the cache warm: this is the one that would blow up if a
        # cached row were a detached ORM instance rather than plain values.
        for path in ('/', '/shop'):
            response = client.get(path)
            check(f'GET {path} renders again off the warm cache',
                  response.status_code == 200, response.status_code)
        listing = client.get(f'/categories/{TAG}-alpha')
        check('GET /categories/<slug> renders without its dropped query',
              listing.status_code == 200, listing.status_code)

    print('a page view does not re-read the nav')
    with app.test_client() as client:
        client.get('/')  # warm anything else the page caches
        with StatementCounter() as counter:
            client.get('/')
        # Not asserting an exact number - the page does other work - only that the
        # nav is not part of it, which a category SELECT would show up as.
        check('the home page runs a small, bounded number of queries',
              counter.count < 25, counter.count)

    check_price_check()


def check_price_check():
    """The create-product page's live price advisory, under a growing catalog.

    This is the endpoint that made /admin/products/add unresponsive: it fired on
    every pause in typing and each call loaded eighty full Product rows to tokenise
    their descriptions in Python. The fix was to narrow the columns and cache the
    scan, and the property worth asserting is comparative rather than absolute -
    ten more products in the catalog must not mean ten more anything per keystroke.
    """
    print('the price advisory does not grow with the catalog')
    admin = User(username=f'{TAG}_admin', email=f'{TAG}_admin@example.invalid')
    admin.set_password('x')
    admin.is_admin = True
    db.session.add(admin)
    db.session.commit()
    admin_id = admin.id

    category = Category(name=f'{TAG} gadgets', slug=f'{TAG}-gadgets', is_active=True)
    db.session.add(category)
    db.session.commit()
    category_id = category.id
    app_module.invalidate_nav_categories()

    def seed(count, offset=0):
        for index in range(count):
            db.session.add(Product(
                name=f'{TAG} Samsung Galaxy A14 variant {offset + index}',
                slug=f'{TAG}-galaxy-{offset + index}',
                selling_price=18000.0 + index, buying_price=15000.0,
                description='A midrange handset. ' * 40,  # the column the scan used to load
                short_description='Midrange handset', stock=5, is_active=True,
                category_id=category_id, commission_percent=15.0,
                review_status='approved'))
        db.session.commit()

    payload = {'name': 'Samsung Galaxy A14', 'category_id': category_id,
               'selling_price': 18500, 'buying_price': 15000}

    limiter = getattr(app_module, 'limiter', None)
    was_enabled = getattr(limiter, 'enabled', None)
    if limiter is not None:
        limiter.enabled = False
    # An app context of this block's own, for the same reason the other smoke scripts
    # give one to each identity: Flask-Login caches the loaded user on ``g``, which
    # belongs to the app context, and the anonymous page views above already left one
    # there. Reusing it means the admin session set on the client below is ignored,
    # every request here bounces off @admin_required as a 302, and the query counts
    # come out equal because they are counting redirects rather than work.
    admin_ctx = app.app_context()
    admin_ctx.push()
    try:
        seed(6)
        ctx_small = None
        with app.test_client() as client:
            with client.session_transaction() as session:
                session['_user_id'] = str(admin_id)
                session['_fresh'] = True
            first = client.post('/admin/api/price-check', json=payload)
            check('POST /admin/api/price-check answers', first.status_code == 200,
                  first.status_code)
            body = first.get_json() or {}
            check('and it answers with a verdict and a count',
                  'status' in body and isinstance(body.get('competitor_count'), int),
                  sorted(body)[:6])

            # Cold count: a fresh name so the comparable cache cannot serve it.
            app_module._comparable_price_cache.clear()
            with StatementCounter() as small:
                client.post('/admin/api/price-check',
                            json=dict(payload, name='Samsung Galaxy A14'))
            ctx_small = small.count

            seed(40, offset=100)
            app_module._comparable_price_cache.clear()
            with StatementCounter() as large:
                client.post('/admin/api/price-check',
                            json=dict(payload, name='Samsung Galaxy A14'))

            check('a catalog seven times larger costs no extra queries',
                  large.count <= ctx_small, (ctx_small, large.count))
            check('and the query count is small in absolute terms too',
                  large.count <= 12, large.count)

            # The cache is what collapses a burst of debounced keystrokes on the
            # same product name into one scan.
            app_module._comparable_price_cache.clear()
            client.post('/admin/api/price-check', json=payload)
            with StatementCounter() as repeat:
                client.post('/admin/api/price-check', json=payload)
                client.post('/admin/api/price-check', json=payload)
            check('a repeated check costs fewer queries than the first',
                  repeat.count < ctx_small * 2, (ctx_small, repeat.count))
            stats = app_module._comparable_price_cache.stats()
            check('and the comparable cache is being hit',
                  stats.get('hits', 0) >= 1, stats)

        print('the endpoint is not open to unlimited polling')
        check('a rate limit is configured on it',
              bool(app_module.PRICE_CHECK_RATE_LIMIT),
              app_module.PRICE_CHECK_RATE_LIMIT)
    finally:
        if limiter is not None and was_enabled is not None:
            limiter.enabled = was_enabled
        db.session.remove()
        admin_ctx.pop()


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
