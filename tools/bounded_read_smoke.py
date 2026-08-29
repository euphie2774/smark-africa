"""Smoke check for reads that must not grow with the size of the platform.

Run with: python tools/bounded_read_smoke.py

Four paths in main.py used to pull a whole table into one worker's memory:
`send_system_update` loaded every active user before queuing the first email,
`find_similar_products` opened one image off disk per active product,
`smart_product_recommendations` loaded the entire catalogue and then read a lazy
`product.category` per row, and the POS inventory total summed two columns in
Python over every non-digital product. None of it is visible at a few hundred
rows, and all of it is an out-of-memory kill on a 512MB container at the sizes
this platform is being built for.

The interesting thing about fixing reads like these is that the obvious fix is
wrong. Putting a `.limit()` on the user fan-out bounds the memory and silently
stops emailing everyone past the cap - a worse failure than the one it fixes,
and an invisible one. Capping the inventory sum bounds it and reports a wrong
number on an owner's money dashboard. So each check below asserts a *pair*: the
read is bounded, and it still gives the same answer. A `.limit()`-only fix
passes the first half and fails the second; the original code passes the second
and fails the first. Only a fix that batches or aggregates passes both.

Statement text is inspected, not just counted, because "bounded" is a property
of the SQL - a query with no LIMIT is unbounded however few rows happen to be in
the table when the check runs.

The rating preload is checked here because this file is what caught it. Bounding
the recommendation candidates fixed the catalogue-sized read and left a query
per candidate standing, so the cost stopped growing with the catalogue and
started growing with the cap instead: 400 candidates, 400 queries. Nothing about
the fix looked wrong, and the count said otherwise.
"""

import os
import sys

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
os.environ.setdefault('CACHE_TYPE', 'NullCache')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event, or_

import main as app_module
from main import app, db
from models import Category, Product, Review, User

FAILURES = []
TAG = 'boundedread'


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + str(detail)) if detail else ""}')


class StatementLog:
    """Record every statement, so a check can ask what the SQL actually said."""

    def __init__(self):
        self.statements = []

    def __enter__(self):
        self._hook = lambda conn, cur, stmt, *a: self.statements.append(stmt)
        event.listen(db.engine, 'before_cursor_execute', self._hook)
        return self

    def __exit__(self, *exc):
        event.remove(db.engine, 'before_cursor_execute', self._hook)
        return False

    @property
    def count(self):
        return len(self.statements)

    def against(self, table):
        lowered = f'from {table}'
        return [s for s in self.statements if lowered in ' '.join(s.lower().split())]

    def unbounded_against(self, table):
        return [s for s in self.against(table) if 'limit' not in s.lower()]


def teardown():
    db.session.rollback()
    try:
        # Reviews before either: a review points at both a product and a user, so
        # it has to go before the rows it references, the same reason products go
        # before categories below.
        mine = [row.id for row in Product.query
                .filter(Product.slug.like(f'{TAG}%')).with_entities(Product.id).all()]
        reviewers = [row.id for row in User.query
                     .filter(User.username.like(f'{TAG}%')).with_entities(User.id).all()]
        if mine or reviewers:
            Review.query.filter(or_(Review.product_id.in_(mine or [0]),
                                    Review.user_id.in_(reviewers or [0]))).delete(
                synchronize_session=False)
        # Products before users and categories: the product carries both as
        # foreign keys, so removing either side first orphans the row and the
        # whole cleanup fails on Postgres.
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


def make_category(name, slug):
    row = Category(name=name, slug=slug, is_active=True)
    db.session.add(row)
    db.session.flush()
    return row


def make_seller(suffix):
    seller = User(username=f'{TAG}_seller{suffix}',
                  email=f'{TAG}_seller{suffix}@example.invalid')
    seller.set_password('x')
    db.session.add(seller)
    db.session.flush()
    return seller


def make_product(slug, name, seller_id, category_id, **overrides):
    fields = dict(name=name, slug=slug, selling_price=1000.0, buying_price=800.0,
                  description='A thing.', short_description='A thing', stock=5,
                  is_active=True, category_id=category_id, seller_id=seller_id,
                  commission_percent=15.0, review_status='approved')
    fields.update(overrides)
    row = Product(**fields)
    db.session.add(row)
    return row


def check_system_update_is_batched_and_complete():
    """The pair: bounded per read, and nobody is skipped."""
    print('the system-update fan-out is batched and still reaches everyone')
    active = []
    for index in range(7):
        user = User(username=f'{TAG}_mail{index:02d}',
                    email=f'{TAG}_mail{index:02d}@example.invalid')
        user.set_password('x')
        user.is_active = True
        db.session.add(user)
        active.append(f'{TAG}_mail{index:02d}@example.invalid')
    dormant = User(username=f'{TAG}_dormant',
                   email=f'{TAG}_dormant@example.invalid')
    dormant.set_password('x')
    dormant.is_active = False
    db.session.add(dormant)
    db.session.commit()

    sent = []
    real_send = app_module.send_email
    real_batch = app_module.SYSTEM_EMAIL_BATCH
    # A batch smaller than the tagged users forces the loop to go round several
    # times, which is where a keyset walk either repeats rows or drops them.
    app_module.SYSTEM_EMAIL_BATCH = 3
    app_module.send_email = lambda to, subject, body: sent.append(to) or True
    try:
        with StatementLog() as log:
            queued = app_module.send_system_update('Subject here', 'Body here')
    finally:
        app_module.send_email = real_send
        app_module.SYSTEM_EMAIL_BATCH = real_batch

    user_reads = log.against('users')
    check('the user table is read in more than one go',
          len(user_reads) >= 3, f'{len(user_reads)} reads')
    check('every read of it is bounded by a LIMIT',
          not log.unbounded_against('users'),
          log.unbounded_against('users')[:1])
    mine = [address for address in sent if address.startswith(TAG)]
    check('every active user was reached exactly once',
          sorted(mine) == sorted(active), f'{len(mine)} of {len(active)}')
    check('the inactive user was not', f'{TAG}_dormant@example.invalid' not in sent)
    check('and the count it returns matches what it queued',
          queued == len(sent), f'{queued} vs {len(sent)}')


def check_inventory_total_is_summed_in_sql():
    """The pair: one aggregate, and the same number the Python loop gave."""
    print('the POS inventory total is summed in SQL and unchanged by it')
    category = make_category(f'{TAG} stock', f'{TAG}-stock')
    seller = make_seller('inv')
    # The awkward rows are the point: a null price and a null stock each have to
    # contribute zero, the way `(x or 0) * (y or 0)` did, and a digital product
    # has to stay out of it entirely.
    make_product(f'{TAG}-inv-1', f'{TAG} priced', seller.id, category.id,
                 buying_price=250.0, stock=4)
    make_product(f'{TAG}-inv-2', f'{TAG} no price', seller.id, category.id,
                 buying_price=None, stock=9)
    make_product(f'{TAG}-inv-3', f'{TAG} no stock', seller.id, category.id,
                 buying_price=500.0, stock=None)
    make_product(f'{TAG}-inv-4', f'{TAG} digital', seller.id, category.id,
                 buying_price=700.0, stock=3, is_digital=True)
    db.session.commit()

    # The expression this replaced, run against the same rows.
    expected = sum((product.buying_price or 0) * (product.stock or 0)
                   for product in Product.query.filter_by(is_digital=False).all())
    with StatementLog() as few:
        first = app_module.pos_report_payload()['inventory_value']
    check('it equals the Python sum it replaced', abs(first - expected) < 0.01,
          f'{first} vs {expected}')

    for index in range(20):
        make_product(f'{TAG}-inv-bulk-{index}', f'{TAG} bulk {index}', seller.id,
                     category.id, buying_price=10.0, stock=2)
    db.session.commit()
    with StatementLog() as many:
        second = app_module.pos_report_payload()['inventory_value']
    check('twenty more products cost no more statements',
          many.count <= few.count, f'{few.count} then {many.count}')
    check('and the total moved by exactly their value',
          abs((second - first) - 20 * 10.0 * 2) < 0.01, second - first)


def check_recommendations_do_not_scale_with_the_catalogue():
    """The pair: flat cost as the catalogue grows, and the same things still match."""
    print('recommendations cost the same on a big catalogue as a small one')
    category = make_category(f'{TAG} zorbcat', f'{TAG}-zorbcat')
    plain = make_category(f'{TAG} plain', f'{TAG}-plain')
    seller = make_seller('rec')
    for index in range(4):
        make_product(f'{TAG}-rec-{index}', f'{TAG} zorbulon widget {index}',
                     seller.id, plain.id)
    db.session.commit()

    with StatementLog() as few:
        small = app_module.smart_product_recommendations('zorbulon', 6)
    for index in range(25):
        make_product(f'{TAG}-recbulk-{index}', f'{TAG} zorbulon widget bulk {index}',
                     seller.id, plain.id)
    db.session.commit()
    with StatementLog() as many:
        big = app_module.smart_product_recommendations('zorbulon', 6)

    check('a matching term finds the products', len(small) > 0, len(small))
    check('twenty-five more of them cost no more statements',
          many.count <= few.count, f'{few.count} then {many.count}')
    check('and it still returns results', len(big) > 0, len(big))
    check('no read of the product table is unbounded',
          not many.unbounded_against('products'),
          many.unbounded_against('products')[:1])

    # The old scorer matched on the category name too, because it scored against
    # a string that included it. If the SQL prefilter had left the category out,
    # this product - whose own columns never say "zorbcat" - would vanish.
    make_product(f'{TAG}-rec-bycat', f'{TAG} unremarkable item', seller.id,
                 category.id)
    db.session.commit()
    by_category = app_module.smart_product_recommendations('zorbcat', 6)
    check('a term that only matches the category still finds the product',
          any(item['product'].slug == f'{TAG}-rec-bycat' for item in by_category),
          [item['product'].slug for item in by_category])

    empty = app_module.smart_product_recommendations('', 6)
    check('an empty query falls back rather than returning nothing',
          len(empty) > 0, len(empty))


def check_preloaded_ratings_match_the_lazy_property():
    """The pair: one query for the whole set, and the numbers it replaced.

    A preload that returns *wrong* averages passes a statement-count check
    perfectly, which is why counting is only half of this. The awkward rows are
    the point again: `average_rating` skipped a review whose `is_visible` was
    false, and skipped a rating of 0 too - `if r.rating` is false for zero, not
    just for null. An aggregate that forgets either one moves every score it
    feeds, for a reason no reader of the scorer could see.
    """
    print('preloaded ratings cost one query and match the property')
    category = make_category(f'{TAG} rated', f'{TAG}-rated')
    seller = make_seller('rated')
    mixed = make_product(f'{TAG}-rate-mixed', f'{TAG} mixed reviews', seller.id,
                         category.id)
    hidden = make_product(f'{TAG}-rate-hidden', f'{TAG} hidden reviews', seller.id,
                          category.id)
    unreviewed = make_product(f'{TAG}-rate-none', f'{TAG} no reviews', seller.id,
                              category.id)
    db.session.flush()

    # Visible 5 and 2 average to 3.5. The 1 is hidden and the 0 is falsy to the
    # property, so neither may reach the average. Each review needs its own
    # author: reviews are unique on (user_id, product_id).
    rows = [(mixed, 5, True), (mixed, 2, True), (mixed, 1, False),
            (mixed, 0, True), (hidden, 4, False)]
    for index, (product, rating, visible) in enumerate(rows):
        reviewer = make_seller(f'rev{index}')
        db.session.add(Review(user_id=reviewer.id, product_id=product.id,
                              rating=rating, is_visible=visible, comment='ok'))
    db.session.commit()

    products = [mixed, hidden, unreviewed]
    # The property first, one product at a time - the shape being replaced, and
    # the reference answer. It also warms the ids, which the commit above
    # expired; without that the preload's own count would include a refresh per
    # product and the comparison below would be measuring the wrong thing.
    with StatementLog() as per_product:
        reference = {product.id: product.average_rating for product in products}
    with StatementLog() as bulk:
        ratings = app_module.average_ratings_for(products)

    check('the whole set costs one query', bulk.count == 1,
          f'{bulk.count} statements')
    check('reading it per product costs more', per_product.count > bulk.count,
          f'{per_product.count} then {bulk.count}')
    check('hidden reviews and zero ratings stay out of the average',
          abs(ratings.get(mixed.id, 0.0) - 3.5) < 0.001, ratings.get(mixed.id))
    check('a product whose only review is hidden has no entry',
          hidden.id not in ratings and reference[hidden.id] == 0.0,
          f'{ratings.get(hidden.id)} vs {reference[hidden.id]}')
    check('nor does one with no reviews at all',
          unreviewed.id not in ratings and reference[unreviewed.id] == 0.0,
          f'{ratings.get(unreviewed.id)} vs {reference[unreviewed.id]}')
    mismatched = [product.slug for product in products
                  if abs(ratings.get(product.id, 0.0) - reference[product.id]) >= 0.001]
    check('every average equals the one the property computed', not mismatched,
          mismatched or f'{len(products)} products')

    # The default is a fallback, not a value: a caller handing the scorer a real
    # 0.0 must not send it back to the lazy property for a second opinion.
    with StatementLog() as scored:
        app_module.product_search_score(unreviewed, 'no reviews', 0.0)
    check('scoring with a preloaded zero reads no reviews',
          not scored.against('reviews'), f'{scored.count} statements')


def check_image_match_scores_a_bounded_candidate_set():
    """The expensive part is per candidate, so the candidate count is the budget."""
    print('image matching scores a bounded number of candidates')
    category = make_category(f'{TAG} img', f'{TAG}-img')
    seller = make_seller('img')
    for index in range(12):
        make_product(f'{TAG}-img-{index}', f'{TAG} image item {index}', seller.id,
                     category.id)
    db.session.commit()

    scored = []
    real_score = app_module.product_image_match_score
    real_profile = app_module.image_profile
    real_budget = app_module.IMAGE_MATCH_CANDIDATES
    app_module.IMAGE_MATCH_CANDIDATES = 5
    app_module.image_profile = lambda path: None
    app_module.product_image_match_score = (
        lambda upload, profile, tokens, product: scored.append(product.id) or (0, ''))
    try:
        # image_url_to_path builds a static URL to compare against, and url_for
        # needs somewhere to hang a relative one off, so this path only runs
        # inside a request.
        with app.test_request_context('/'), StatementLog() as log:
            app_module.find_similar_products('nonexistent.jpg', 'a thing.jpg', limit=3)
    finally:
        app_module.product_image_match_score = real_score
        app_module.image_profile = real_profile
        app_module.IMAGE_MATCH_CANDIDATES = real_budget

    check('it scores no more candidates than the budget allows',
          len(scored) <= 5, f'{len(scored)} scored')
    check('the candidate read is bounded by a LIMIT',
          not log.unbounded_against('products'),
          log.unbounded_against('products')[:1])


def run():
    check_system_update_is_batched_and_complete()
    print()
    check_inventory_total_is_summed_in_sql()
    print()
    check_recommendations_do_not_scale_with_the_catalogue()
    print()
    check_preloaded_ratings_match_the_lazy_property()
    print()
    check_image_match_scores_a_bounded_candidate_set()


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
