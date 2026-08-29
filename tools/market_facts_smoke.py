"""Smoke check for the market intelligence reads behind the admin dashboard.

Run with: python tools/market_facts_smoke.py

`build_market_intelligence_payload` used to issue two queries per category every time
it was called - one `.all()` that loaded every active listing in the category into
memory to average a single column, and one leading-wildcard `ilike` against
Manufacturer that no index can serve. `pricing_suggestions` then called it eighty
times per run, once per product, keyed only on that product's category, and read
`average_rating` on each product for eighty more queries on top.

None of that showed up as slowness while the catalogue was small, and none of it is
visible from a status code. So there are two kinds of assertion here, because either
one alone would pass while the page was still wrong:

**Bounded** - the query count does not move when the catalogue grows tenfold, and
`pricing_suggestions` costs a handful of queries rather than one per product.

**Correct** - the averages still equal an average computed independently in Python,
the manufacturer named for a category is still the highest-priority one that matches
it, and asking for one category still returns exactly what asking for all of them
returns for that category. A cache that returns a fast wrong number passes every
count-based check ever written, which is the whole reason this half exists.
"""

import os
import re
import sys

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
os.environ.setdefault('CACHE_TYPE', 'NullCache')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event

import main as app_module
from main import (app, build_market_intelligence_payload, db, market_category_facts,
                  pricing_suggestions)
from models import Category, Manufacturer, Product, Review, User

FAILURES = []
TAG = 'mktsmoke'


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + str(detail)) if detail else ""}')


class StatementCounter:
    """Counts statements sent to the database on this thread only.

    Only this thread: the engine is shared, and background traffic counted as the
    measured work is exactly what made these numbers untrustworthy before.
    """

    def __init__(self):
        self.count = 0
        self.shapes = {}
        self.other_threads = 0
        import threading
        self._thread = threading.get_ident()

    def _record(self, conn, cursor, statement, params, context, executemany):
        import threading
        if threading.get_ident() != self._thread:
            self.other_threads += 1
            return
        self.count += 1
        shape = re.sub(r'\b\d+\b', '?', ' '.join((statement or '').split()))[:150]
        self.shapes[shape] = self.shapes.get(shape, 0) + 1

    def __enter__(self):
        event.listen(db.engine, 'before_cursor_execute', self._record)
        return self

    def __exit__(self, *exc):
        event.remove(db.engine, 'before_cursor_execute', self._record)
        return False

    def top(self, limit=6):
        return sorted(self.shapes.items(), key=lambda item: -item[1])[:limit]


def cold():
    """Drop the facts cache so the next call actually queries.

    Without this every measurement after the first reads the cache and reports zero,
    which would make a completely broken query look like the cheapest code in the app.
    """
    app_module._market_facts_cache.clear()


def teardown():
    product_ids = [row[0] for row in db.session.query(Product.id)
                   .filter(Product.name.like(f'{TAG}%')).all()] or [0]
    user_ids = [row[0] for row in db.session.query(User.id)
                .filter(User.username.like(f'{TAG}%')).all()] or [0]
    for model, clause in ((Review, Review.product_id.in_(product_ids)),
                          (Product, Product.id.in_(product_ids)),
                          (Manufacturer, Manufacturer.name.like(f'{TAG}%')),
                          (Category, Category.slug.like(f'{TAG}%')),
                          (User, User.id.in_(user_ids))):
        model.query.filter(clause).delete(synchronize_session=False)
    db.session.commit()
    cold()
    app_module.invalidate_nav_categories()
    app_module.invalidate_product_cache()


def make_seller():
    user = User(username=f'{TAG}_seller', email=f'{TAG}_seller@example.invalid')
    user.set_password('x')
    user.seller_status = 'verified'
    db.session.add(user)
    db.session.commit()
    return user


def make_category(suffix):
    category = Category(name=f'{TAG} {suffix}', slug=f'{TAG}-{suffix}', is_active=True)
    db.session.add(category)
    db.session.commit()
    return category


def add_products(seller_id, category_id, count, price_from=100.0, start=0):
    made = []
    for i in range(start, start + count):
        product = Product(
            name=f'{TAG} item {i}', slug=f'{TAG}-item-{i}', seller_id=seller_id,
            category_id=category_id, selling_price=price_from + i, buying_price=10.0,
            description='A listing that exists only to be averaged.',
            short_description='Test listing', stock=10, is_active=True,
            review_status='approved', commission_percent=15.0, discount_percent=0.0)
        db.session.add(product)
        made.append(product)
    db.session.commit()
    return made


def expected_average(category_id):
    """The average the old per-category loop computed, recomputed here in Python.

    This is the oracle for the SQL aggregate that replaced it, and it is written the
    way the old code was written on purpose - `discounted_price` is a Python property,
    so this reads the property rather than restating its arithmetic. If the two ever
    disagree, the aggregate is what is wrong.
    """
    products = Product.query.filter_by(category_id=category_id, is_active=True).all()
    if not products:
        return None
    return sum((p.discounted_price or p.selling_price or 0)
               for p in products) / max(1, len(products))


def run():
    teardown()
    seller = make_seller()
    plain = make_category('plain')
    discounted = make_category('discounted')
    empty = make_category('empty')

    add_products(seller.id, plain.id, 12, price_from=100.0)
    # A category whose prices all run through the discount branch, because that branch
    # is the one restated in SQL and so the one that can silently drift.
    for product in add_products(seller.id, discounted.id, 8, price_from=500.0, start=500):
        product.discount_percent = 25.0
    db.session.commit()

    # Two suppliers matching the same category, differing only in priority: the row the
    # payload names has to be the one the old ordering would have named.
    db.session.add(Manufacturer(name=f'{TAG} runner up', country='Ghana',
                                product_categories=f'{TAG} plain', priority=1, rating=4.9))
    db.session.add(Manufacturer(name=f'{TAG} top pick', country='Kenya',
                                product_categories=f'{TAG} plain', priority=9, rating=0.1))
    db.session.commit()

    print('the category facts are three queries, not two per category')
    cold()
    with StatementCounter() as first:
        facts = market_category_facts()
    check('market_category_facts returns every category', len(facts) >= 3, len(facts))
    check('and costs a fixed handful of queries', first.count <= 4,
          f'{first.count} queries')
    if first.count > 4:
        for shape, hits in first.top():
            print(f'         x{hits}  {shape}')

    with StatementCounter() as warm:
        market_category_facts()
    check('a second call inside the TTL queries nothing at all', warm.count == 0,
          f'{warm.count} queries')

    add_products(seller.id, plain.id, 120, price_from=100.0, start=1000)
    cold()
    with StatementCounter() as grown:
        market_category_facts()
    check('and the count does not move when the catalogue grows tenfold',
          grown.count <= first.count, f'{first.count} -> {grown.count}')
    if grown.count > first.count:
        for shape, hits in grown.top():
            print(f'         x{hits}  {shape}')

    print('the numbers are still the numbers')
    cold()
    facts = {cid: (name, base, src, region)
             for cid, name, base, src, region in market_category_facts()}

    for category in (plain, discounted):
        want = expected_average(category.id)
        got = facts[category.id][1]
        check(f'{category.name} averages what Python averages',
              want is not None and abs(got - want) <= 0.01,
              f'python {want!r} vs sql {got!r}')

    check('a category with no listings falls back to its stand-in price',
          abs(facts[empty.id][1] - (1000 + empty.id * 137)) < 0.01,
          facts[empty.id][1])
    check('the highest-priority matching supplier is the one named',
          facts[plain.id][2] == f'{TAG} top pick', facts[plain.id][2])
    check('and its country comes with it', facts[plain.id][3] == 'Kenya',
          facts[plain.id][3])
    check('a category no supplier lists falls back to the index',
          facts[empty.id][2] == 'World supplier index', facts[empty.id][2])

    print('asking for one category matches asking for all of them')
    everything = {row['category_id']: row
                  for row in build_market_intelligence_payload('all')['rows']}
    single = build_market_intelligence_payload(str(plain.id))['rows']
    check('one category comes back as one row', len(single) == 1, len(single))
    if single:
        # updated_at is a clock reading taken per call, so it is expected to differ and
        # is the one field excluded rather than quietly ignored.
        volatile = {'updated_at'}
        mine = everything.get(plain.id) or {}
        differing = {field for field in set(mine) | set(single[0])
                     if field not in volatile and mine.get(field) != single[0].get(field)}
        check('and is identical to its row in the full payload', not differing,
              sorted(differing))

    print('pricing suggestions cost a fixed number of queries, not one per product')
    # A low-rated expensive listing, so the rating branch both fires and clears the
    # one-shilling floor that decides whether a suggestion is emitted at all.
    pressured = add_products(seller.id, plain.id, 1, price_from=5000.0, start=9000)[0]
    for i in range(3):
        rater = User(username=f'{TAG}_rater{i}', email=f'{TAG}_rater{i}@example.invalid')
        rater.set_password('x')
        db.session.add(rater)
        db.session.flush()
        db.session.add(Review(product_id=pressured.id, user_id=rater.id, rating=1,
                              comment='not good', is_visible=True))
    db.session.commit()

    cold()
    with StatementCounter() as cold_run:
        suggestions = pricing_suggestions()
    check('pricing_suggestions runs', isinstance(suggestions, list), type(suggestions))
    # The old shape was 80 products x (a payload rebuild + a rating read). Anything
    # near that many statements means one of the two loops came back.
    check('a cold run stays well under one query per product', cold_run.count <= 12,
          f'{cold_run.count} queries for up to 80 products')
    if cold_run.count > 12:
        for shape, hits in cold_run.top():
            print(f'         x{hits}  {shape}')

    with StatementCounter() as warm_run:
        pricing_suggestions()
    check('a warm run is the product read and the rating read', warm_run.count <= 3,
          f'{warm_run.count} queries')

    mine = [s for s in suggestions if s['product'].id == pressured.id]
    check('the low-rated listing is still suggested', len(mine) == 1,
          f'{len(mine)} of {len(suggestions)} suggestions')
    if mine:
        check('and rating pressure is still the reason given',
              'rating pressure' in mine[0]['reason'], mine[0]['reason'])
        check('with the price marked down, not up',
              mine[0]['suggested'] < mine[0]['current'],
              f"{mine[0]['current']} -> {mine[0]['suggested']}")


def main():
    with app.app_context():
        try:
            run()
        finally:
            teardown()
    print()
    if FAILURES:
        print(f'{len(FAILURES)} check(s) failed:')
        for label in FAILURES:
            print(f'  - {label}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
