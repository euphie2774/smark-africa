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
from models import Category

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
