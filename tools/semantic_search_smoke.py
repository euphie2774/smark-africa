"""Smoke check for the search widening that makes "bluetooth jammer" find an ESP32.

Run with: python tools/semantic_search_smoke.py

Three things have to hold at once, and two of them pull against each other.

It has to work. Somebody typing the name of the thing they want has to find the
listing that *is* that thing under a different name. "bluetooth jammer" is the
literal example this was built for, so it is asserted literally.

It has to be invisible. There is no "AI results" heading, no badge, no second block
of suggestions - the widened matches join the ordinary result list and the page
renders exactly as it did before. One check below reads the rendered HTML and
asserts the absence of the phrases a feature like this normally announces itself
with. That is not cosmetic: a visible "we guessed for you" band is the difference
between a search that works and a search that admits it did not.

And a search that already works must not pay for any of it. The widening only runs
when the plain search came back with almost nothing, so the cost lands on the
queries that return an empty page today rather than on the traffic. That is checked
by counting calls and queries, not by reading the code - which is the only version
of the claim that survives someone editing the gate later.

The AI leg is off by default (SEMANTIC_SEARCH_AI=0) because it is a network call on
the search path. So there is also a check that the default configuration makes no
outbound request at all: main's `requests` is swapped for one that raises if it is
touched, and the search still has to answer.
"""

import contextlib
import os
import sys

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
os.environ.setdefault('CACHE_TYPE', 'NullCache')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event

import main as app_module
from main import (CACHE_MISS, DEFAULT_SEARCH_CONCEPTS, SEMANTIC_SEARCH_AI,
                  SEMANTIC_SEARCH_MIN_RESULTS, app, build_product_search_query,
                  cached_product_search_ids, concept_expansion, db,
                  expanded_search_terms, normalise_search_text, search_concept_map)
from models import BusinessStorefront, Category, Product, Setting, User

FAILURES = []
TAG = 'semsmoke'

# Phrases a feature like this announces itself with. Deliberately specific: a bare
# "ai" matches "available" and "email", and a bare "smart" matches SMARKAFRICA and
# the /smart-shopping link in the nav, so either would fail on every page forever
# and the check would end up deleted rather than fixed.
AI_MARKERS = [
    'semantic', 'ai search', 'ai-powered', 'ai powered', 'powered by ai',
    'did you mean', 'related searches', 'suggested searches', 'smart match',
    'similar terms', 'we also searched', 'expanded your search', 'no exact match',
]


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + str(detail)) if detail else ""}')


class StatementCounter:
    """Counts statements actually sent to the database, optionally filtered."""

    def __init__(self, match=None):
        self.count = 0
        self.match = (match or '').lower()

    def __enter__(self):
        self._hook = lambda conn, cursor, statement, *a: self._bump(statement)
        event.listen(db.engine, 'before_cursor_execute', self._hook)
        return self

    def _bump(self, statement):
        if not self.match or self.match in (statement or '').lower():
            self.count += 1

    def __exit__(self, *exc):
        event.remove(db.engine, 'before_cursor_execute', self._hook)
        return False


class ExpansionCounter:
    """Wraps main's expanded_search_terms to count how often the widening ran.

    Patches the name on the module rather than wrapping the function object,
    because cached_product_search_ids looks that global up at call time - which is
    what makes this observe the real call site instead of a copy of it.
    """

    def __init__(self):
        self.calls = []
        self._real = app_module.expanded_search_terms

    def __enter__(self):
        def counted(text):
            self.calls.append(text)
            return self._real(text)
        app_module.expanded_search_terms = counted
        return self

    def __exit__(self, *exc):
        app_module.expanded_search_terms = self._real
        return False


class NoNetwork:
    """Swaps main's `requests` for one that refuses to be used.

    Replaces the module-level name inside main rather than patching the real
    requests module, so nothing leaks out of this check into the rest of the suite.
    """

    class _Refuse:
        def post(self, *a, **k):
            raise AssertionError('the search path made an outbound HTTP request')

        def get(self, *a, **k):
            raise AssertionError('the search path made an outbound HTTP request')

    def __enter__(self):
        self._real = app_module.requests
        app_module.requests = self._Refuse()
        return self

    def __exit__(self, *exc):
        app_module.requests = self._real
        return False


@contextlib.contextmanager
def anonymous_client():
    ctx = app.app_context()
    ctx.push()
    try:
        with app.test_client() as client:
            yield client
    finally:
        db.session.remove()
        ctx.pop()


def teardown():
    db.session.rollback()
    user_ids = [row[0] for row in db.session.query(User.id)
                .filter(User.username.like(f'{TAG}%')).all()] or [0]
    for model, clause in ((Product, Product.name.like(f'{TAG}%')),
                          (BusinessStorefront,
                           BusinessStorefront.business_name.like(f'{TAG}%')),
                          (Category, Category.slug.like(f'{TAG}%')),
                          (Setting, Setting.key == 'search_concept_map'),
                          (User, User.id.in_(user_ids))):
        model.query.filter(clause).delete(synchronize_session=False)
    db.session.commit()
    # A bulk delete goes round Setting.set, so the row is gone while this worker's
    # copy of it is not. Without this the next script to read the concept map would
    # be served a value that no longer exists in the database.
    Setting._cache.clear()
    app_module._search_expansion_cache.clear()
    app_module.invalidate_nav_categories()
    app_module.invalidate_product_cache()


def make_fixture():
    seller = User(username=f'{TAG}_seller', email=f'{TAG}_seller@example.invalid')
    seller.set_password('x')
    seller.seller_status = 'verified'
    for flag in ('is_verified_seller', 'is_seller'):
        if hasattr(seller, flag):
            setattr(seller, flag, True)
    db.session.add(seller)
    db.session.commit()

    db.session.add(BusinessStorefront(
        owner_id=seller.id, business_name=f'{TAG} Shop', slug=f'{TAG}-shop',
        status='approved', physical_address='Kimathi Street, Nairobi',
        location_lat=-1.2841, location_lng=36.8233))
    category = Category(name=f'{TAG} Electronics', slug=f'{TAG}-electronics',
                        is_active=True)
    db.session.add(category)
    db.session.commit()

    def add(name, slug):
        product = Product(
            name=name, slug=slug, seller_id=seller.id, category_id=category.id,
            selling_price=1500.0, buying_price=900.0,
            description='A development board with wifi and bluetooth on it.',
            short_description='Dev board', stock=10, is_active=True,
            review_status='approved', commission_percent=15.0,
            location_lat=-1.2841, location_lng=36.8233,
            location_label='Kimathi Street, Nairobi')
        db.session.add(product)
        db.session.commit()
        return product

    # The listing the example is about. Nothing in its text says "jammer" - that is
    # the whole point, and the reason a plain ilike can never find it.
    target = add(f'{TAG} ESP32 Development Board', f'{TAG}-esp32-dev-board')

    # Five listings for the healthy-search case. The plain search finds all five, so
    # the widening must not run at all.
    healthy = [add(f'{TAG} earbuds model {i}', f'{TAG}-earbuds-{i}') for i in range(5)]
    return seller, category, target, healthy


def run():
    seller, category, target, healthy = make_fixture()

    print('the concept map answers the example that was asked for')
    expansion = concept_expansion('bluetooth jammer')
    check('"bluetooth jammer" expands to esp32',
          'esp32' in [t.lower() for t in expansion], expansion)
    check('the seed map is not empty', len(DEFAULT_SEARCH_CONCEPTS) >= 10,
          f'{len(DEFAULT_SEARCH_CONCEPTS)} concepts')
    check('normalising collapses case and whitespace',
          normalise_search_text('  Bluetooth   JAMMER ') == 'bluetooth jammer',
          repr(normalise_search_text('  Bluetooth   JAMMER ')))
    check('a query with no concept gets no terms',
          expanded_search_terms('zzqqx widget of no description') == [])
    check('a one-character query is skipped', expanded_search_terms('a') == [])
    check('an over-long query is skipped',
          expanded_search_terms('bluetooth jammer ' + 'x' * 90) == [])

    print('the widened search finds it, end to end')
    plain_ids = [row[0] for row in
                 build_product_search_query('bluetooth jammer')[0]
                 .with_entities(Product.id).all()]
    # Its own check so that if this database ever does hold enough literal
    # "bluetooth jammer" listings to clear the gate, the output says so instead of
    # blaming the widening for a result it was never asked to produce.
    check('the plain search is thin enough to trigger widening',
          len(plain_ids) <= SEMANTIC_SEARCH_MIN_RESULTS,
          f'{len(plain_ids)} plain matches, gate is {SEMANTIC_SEARCH_MIN_RESULTS}')
    check('the plain search does not find the ESP32 board',
          target.id not in plain_ids)

    app_module._search_expansion_cache.clear()
    widened = cached_product_search_ids('bluetooth jammer')
    check('searching "bluetooth jammer" returns the ESP32 board',
          target.id in widened, f'{len(widened)} results')

    print('the words the shopper typed still rank first')
    app_module._search_expansion_cache.clear()
    mixed = cached_product_search_ids('esp32')
    check('an exact match is in its own result list', target.id in mixed, mixed[:3])

    print('a search that already works pays nothing')
    app_module._search_expansion_cache.clear()
    with ExpansionCounter() as counter:
        healthy_ids = cached_product_search_ids('earbuds')
        check('the healthy search found more than the gate',
              len(healthy_ids) > SEMANTIC_SEARCH_MIN_RESULTS, len(healthy_ids))
        check('the widening never ran for it', counter.calls == [], counter.calls)

    app_module._search_expansion_cache.clear()
    with ExpansionCounter() as counter:
        cached_product_search_ids('bluetooth jammer')
        check('the widening ran exactly once for the thin search',
              len(counter.calls) == 1, counter.calls)

    print('widening costs one more query, not one per term')
    app_module._search_expansion_cache.clear()
    with StatementCounter(match='from products') as thin:
        cached_product_search_ids('bluetooth jammer')
    app_module._search_expansion_cache.clear()
    with StatementCounter(match='from products') as well_served:
        cached_product_search_ids('earbuds')
    check('the widened search runs at most one extra product query',
          thin.count - well_served.count <= 1,
          f'thin {thin.count} vs healthy {well_served.count}')

    print('the default configuration never leaves the machine')
    app_module._search_expansion_cache.clear()
    try:
        with NoNetwork():
            offline = cached_product_search_ids('bluetooth jammer')
            check('the search answers with no outbound request', target.id in offline)
            check('an unmapped query answers with no outbound request',
                  expanded_search_terms('qqzz nonexistent thing here') == [])
    except AssertionError as exc:
        check('the search path stays offline in the default configuration', False, exc)
    if os.environ.get('SEMANTIC_SEARCH_AI'):
        # Reported rather than asserted: somebody has deliberately turned the AI leg
        # on in this environment, and failing the suite for that would be wrong.
        print(f'  [note] SEMANTIC_SEARCH_AI is set to '
              f'{os.environ["SEMANTIC_SEARCH_AI"]!r} here, so the off-by-default '
              f'checks are reported instead of asserted')
    else:
        check('the AI leg ships off', SEMANTIC_SEARCH_AI is False, SEMANTIC_SEARCH_AI)
        check('ai_expansion returns nothing while it is off',
              app_module.ai_expansion('bluetooth jammer') == [])

    print('the expansion is cached, so one query is computed once')
    app_module._search_expansion_cache.clear()
    first = expanded_search_terms('bluetooth jammer')
    cached = app_module._search_expansion_cache.lookup('bluetooth jammer')
    check('the second lookup is served from the cache', cached is not CACHE_MISS,
          cached)
    check('the cached value is what was computed', cached == first, first)

    print('an admin can edit the map without a deploy')
    Setting.set('search_concept_map', '{"drone parts": ["esc", "brushless motor"]}')
    merged = search_concept_map()
    check('an admin key is merged in', 'esc' in merged.get('drone parts', []),
          merged.get('drone parts'))
    check('the seeds survive the merge',
          'esp32' in merged.get('bluetooth jammer', []))
    Setting.set('search_concept_map', '{not json at all')
    check('invalid JSON falls back to the seeds instead of raising',
          'esp32' in search_concept_map().get('bluetooth jammer', []))
    Setting.set('search_concept_map', '')

    print('the query builder is unchanged for every existing caller')
    without = str(build_product_search_query('phone')[0])
    with_none = str(build_product_search_query('phone', extra_terms=None)[0])
    check('extra_terms=None generates identical SQL', without == with_none)
    with_terms = str(build_product_search_query('phone', extra_terms=['esp32'])[0])
    check('extra_terms widens the SQL',
          with_terms != without and
          with_terms.lower().count('like') > without.lower().count('like'),
          f'{without.lower().count("like")} -> {with_terms.lower().count("like")} '
          f'like clauses')
    check('an empty extra term is ignored',
          str(build_product_search_query('phone', extra_terms=['', None])[0]) == without)

    print('the page gives no sign that any of this happened')
    with anonymous_client() as client:
        app_module._search_expansion_cache.clear()
        response = client.get('/shop?search=bluetooth+jammer')
        body = response.get_data(as_text=True)
        lowered = body.lower()
        check('the results page renders', response.status_code == 200,
              response.status_code)
        check('the ESP32 board is on the page',
              'esp32 development board' in lowered)
        found = [marker for marker in AI_MARKERS if marker in lowered]
        check('no phrase on the page hints that the search was widened',
              not found, found)
        check('the search box still echoes what was typed',
              'bluetooth jammer' in lowered)

        # The same page for a query that needs no help, so the absence above cannot
        # be an empty page rendering differently and passing by accident.
        well_served = client.get('/shop?search=earbuds')
        check('a normal search renders the same way',
              well_served.status_code == 200, well_served.status_code)
        check('the normal search page is equally quiet',
              not [m for m in AI_MARKERS
                   if m in well_served.get_data(as_text=True).lower()])


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
