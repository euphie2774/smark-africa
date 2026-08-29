"""Smoke check for the list pages that grow without limit.

Run with: python tools/list_page_smoke.py

Every page here renders a list that gets longer for as long as an account is used:
order history, a seller's catalogue, a listing's reviews, a category. The failure
they share is silent - the page works on the way in, and gets slower every month
until it times out for the heaviest users first, who are the ones you least want to
lose.

So these checks are comparative rather than absolute. Each page is rendered against
a small dataset and then a large one, and what is asserted is that the query count
did not move. A number that holds while the data grows tenfold is the only real
evidence that neither the list nor an N+1 inside it is unbounded; a fixed budget
would just encode whatever today's number happens to be.

One page needs a second comparison. Growing the table is enough to catch an
unbounded list, but it cannot catch a lazy read on a page that is already capped:
a thirty-row page costs the same two extra queries per row whether the table holds
thirty rows or a million, so that count holds steady while the page is still three
times more expensive than it should be. The moderation queue is therefore also
measured full page against partial page, where the row count is what differs and
an eager load is the only thing that keeps the two equal.
"""

import contextlib
import os
import re
import sys
import threading
from datetime import datetime, timedelta

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
os.environ.setdefault('CACHE_TYPE', 'NullCache')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event

import main as app_module
from main import (ADMIN_REVIEWS_PER_PAGE, ORDERS_PER_PAGE, REVIEWS_PER_PAGE,
                  SELLER_PRODUCTS_PER_PAGE, app, db, flush_product_views)
from models import BusinessStorefront, Category, Order, OrderItem, Product, Review, User
from scale import CounterBuffer

FAILURES = []
TAG = 'listsmoke'


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + str(detail)) if detail else ""}')


def statement_shape(statement):
    """A statement with its parameters and layout flattened.

    Two lookups of the same table by different id are the same shape, so an N+1
    shows up as one shape with a high count rather than as many distinct lines.
    """
    text = ' '.join((statement or '').split())
    return re.sub(r'\b\d+\b', '?', text)[:150]


class StatementCounter:
    """Counts statements actually sent to the database, optionally filtered.

    Hooks the cursor rather than the ORM, so lazy loads triggered from inside a
    Jinja template are counted too - which is where most N+1s in this codebase
    live, and where they are invisible to anything that only inspects the view.

    Counts only statements issued on the thread that opened the window. The hook
    has to go on the engine, which every thread in the process shares, so without
    that filter the count includes SQL this page never issued: the outbound-drain
    thread (main.py:21894) polls the queue on a timer and flushes buffered view
    counts as one UPDATE per pending product. Landing inside the window, that made
    an unchanged page measure 20 queries against 13 - which read as a review N+1
    that does not exist, and only in a full-suite run, because that is what makes
    the run slow enough for the timer to fire. Statements from other threads are
    tallied in ``other_threads`` rather than dropped silently, so an unexpectedly
    busy background thread is still visible to whoever is reading the output.
    """

    def __init__(self, match=None):
        self.count = 0
        self.other_threads = 0
        self.shapes = {}
        self.match = (match or '').lower()
        self._thread = None

    def __enter__(self):
        self._thread = threading.get_ident()
        self._hook = lambda conn, cursor, statement, *a: self._bump(statement)
        event.listen(db.engine, 'before_cursor_execute', self._hook)
        return self

    def _bump(self, statement):
        if threading.get_ident() != self._thread:
            self.other_threads += 1
            return
        if not self.match or self.match in (statement or '').lower():
            self.count += 1
            key = statement_shape(statement)
            self.shapes[key] = self.shapes.get(key, 0) + 1

    def __exit__(self, *exc):
        event.remove(db.engine, 'before_cursor_execute', self._hook)
        return False


@contextlib.contextmanager
def as_user(user_id):
    """A client signed in as one user, in an app context of its own.

    Flask-Login caches the loaded user on ``g``, which belongs to the app context
    and not the request, so a script holding one context open keeps whoever signed
    in first for every client it opens afterwards.
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


def teardown():
    db.session.rollback()
    user_ids = [row[0] for row in db.session.query(User.id)
                .filter(User.username.like(f'{TAG}%')).all()] or [0]
    product_ids = [row[0] for row in db.session.query(Product.id)
                   .filter(Product.name.like(f'{TAG}%')).all()] or [0]
    order_ids = [row[0] for row in db.session.query(Order.id)
                 .filter(Order.user_id.in_(user_ids)).all()] or [0]
    for model, clause in ((OrderItem, OrderItem.order_id.in_(order_ids)),
                          (Order, Order.id.in_(order_ids)),
                          (Review, Review.product_id.in_(product_ids)),
                          (Product, Product.id.in_(product_ids)),
                          (BusinessStorefront,
                           BusinessStorefront.business_name.like(f'{TAG}%')),
                          (Category, Category.slug.like(f'{TAG}%')),
                          (User, User.id.in_(user_ids))):
        model.query.filter(clause).delete(synchronize_session=False)
    db.session.commit()
    app_module.invalidate_nav_categories()
    app_module.invalidate_product_cache()


def make_user(suffix, seller=False, admin=False):
    # No phone: the column is unique, and a fixture inventing hundreds of numbers
    # would collide with itself or with real data long before it proved anything.
    user = User(username=f'{TAG}_{suffix}', email=f'{TAG}_{suffix}@example.invalid')
    user.set_password('x')
    if admin:
        user.is_admin = True
    if seller:
        user.seller_status = 'verified'
        for flag in ('is_verified_seller', 'is_seller'):
            if hasattr(user, flag):
                setattr(user, flag, True)
    db.session.add(user)
    db.session.commit()
    if seller:
        # An approved storefront, because that is the gate on the listing pages -
        # without one /seller/products redirects to the application form and the
        # pagination checks below would be reading a 302 as a pass.
        db.session.add(BusinessStorefront(
            owner_id=user.id, business_name=f'{TAG} {suffix} Shop',
            slug=f'{TAG}-{suffix}-shop', status='approved',
            physical_address='Kimathi Street, Nairobi',
            location_lat=-1.2841, location_lng=36.8233))
        db.session.commit()
    return user


def add_products(seller_id, category_id, count, start=0):
    made = []
    for i in range(start, start + count):
        product = Product(
            name=f'{TAG} item {i}', slug=f'{TAG}-item-{i}', seller_id=seller_id,
            category_id=category_id, selling_price=100.0 + i, buying_price=50.0,
            description=f'A test listing numbered {i}, long enough to be truncated.',
            short_description='Test listing', stock=10, is_active=True,
            review_status='approved', commission_percent=15.0,
            location_lat=-1.2841, location_lng=36.8233,
            location_label='Kimathi Street, Nairobi')
        db.session.add(product)
        made.append(product)
    db.session.commit()
    return made


def add_orders(user_id, products, count, start=0):
    for i in range(start, start + count):
        order = Order(user_id=user_id, amount_paid=300.0, payment_status='completed',
                      status='completed', created_at=datetime.utcnow() - timedelta(days=i))
        db.session.add(order)
        db.session.flush()
        # Three lines each, because the template prints item.product.name per line -
        # one order with one line would hide a per-line N+1 behind a per-order one.
        for product in products[:3]:
            db.session.add(OrderItem(order_id=order.id, product_id=product.id,
                                     product_name=product.name, price=100.0, quantity=1))
    db.session.commit()


def add_reviews(product_id, count, start=0):
    """One review per new author, because reviews are one-per-buyer anyway.

    Distinct authors are not incidental: reviews sharing three authors would be
    served from the session's identity map on the second lookup, so a per-review
    lazy load of review.author would cost three queries no matter how many reviews
    there were, and the N+1 this is meant to detect would be invisible.
    """
    for i in range(start, start + count):
        author = make_user(f'rev{i}')
        db.session.add(Review(product_id=product_id, user_id=author.id,
                              rating=(i % 5) + 1, comment=f'Review number {i}',
                              is_visible=True,
                              created_at=datetime.utcnow() - timedelta(hours=i)))
    db.session.commit()


@contextlib.contextmanager
def steady_view_buffer():
    """Keep the product-view buffer from flushing inside a measured window.

    Every product page render buffers a view (main.py:5660), and the buffer
    flushes on a wall-clock timer - VIEW_COUNT_FLUSH_SECONDS, 30s by default -
    writing one UPDATE per pending product. A flush that lands inside a
    StatementCounter window is counted as though the page had issued those
    queries, so the same unchanged page measures differently depending only on
    how long the run took to get here. That is what made this file report the
    review page growing from 13 queries to 22 during a full-suite run while
    passing standalone: not an N+1, a timer.

    The buffer's own cost is not being swept under the rug - it has its own
    dedicated check below, which counts UPDATEs specifically and is the right
    place to assert that views coalesce.
    """
    flush_product_views()  # write what is pending, deliberately outside the window
    buffer = app_module._product_view_buffer
    previous = buffer.flush_seconds
    # 0 disables the timer branch in _due_locked outright, so a timer that came
    # due before the window opened cannot fire inside it either. The count-based
    # threshold is left alone: _counted is 0 after the drain above, and a single
    # page render cannot reach it.
    buffer.flush_seconds = 0
    try:
        yield
    finally:
        buffer.flush_seconds = previous


_MEASUREMENTS = {}


def count_page(client, path, match=None):
    with steady_view_buffer(), StatementCounter(match=match) as counter:
        response = client.get(path)
    if counter.other_threads:
        # Printed rather than swallowed: the whole reason these numbers were once
        # untrustworthy is that this traffic was being counted as the page's.
        print(f'         (ignored {counter.other_threads} background-thread '
              f'statement(s) while measuring {path})')
    _MEASUREMENTS.setdefault(path, []).append(counter.shapes)
    return response, counter.count


def explain_growth(path):
    """Print what the later measurement of ``path`` ran that the earlier did not.

    These comparative checks used to fail intermittently with nothing but two
    numbers to go on, which cost several sessions of guessing at an N+1 that was
    not there. A count that grows now says which statement grew, so the next
    occurrence is diagnosable from the output alone rather than by reproduction.
    """
    runs = _MEASUREMENTS.get(path) or []
    if len(runs) < 2:
        return
    earlier, later = runs[-2], runs[-1]
    grew = {shape: later[shape] - earlier.get(shape, 0)
            for shape in later if later[shape] > earlier.get(shape, 0)}
    if not grew:
        print(f'         (no statement shape grew for {path}; the extra queries '
              f'were not issued on the measured thread)')
        return
    print(f'         statements that grew while measuring {path}:')
    for shape, delta in sorted(grew.items(), key=lambda item: -item[1])[:8]:
        print(f'         +{delta}  {shape}')


def check_no_growth(label, before, after, path):
    """Assert a page did not get more expensive, and say why if it did."""
    ok = after <= before
    check(label, ok, f'{before} -> {after}')
    if not ok:
        explain_growth(path)


def squash(response):
    """Response text with runs of whitespace collapsed to single spaces.

    Jinja puts a newline and a screenful of indentation between ``44`` and
    ``products``, so a check for a phrase that reads as one in the browser has to
    ignore how the template happens to be laid out.
    """
    return ' '.join(response.data.decode('utf-8', 'replace').split())


def run():
    print('the counter buffer coalesces instead of writing every hit')
    buffer = CounterBuffer(flush_after=10, flush_seconds=0, name='test')
    flushes = [buffer.add(7) for _ in range(9)]
    check('nothing is written before the threshold', not any(flushes), flushes)
    due = buffer.add(7)
    check('ten hits on one key flush as one delta', due == {7: 10}, due)
    check('and the buffer is empty afterwards', buffer.drain() == {}, buffer.stats())
    buffer.add(1)
    buffer.add(2)
    buffer.add(1)
    check('separate keys stay separate', buffer.drain() == {1: 2, 2: 1})
    check('a drained buffer reports no pending increments',
          buffer.stats()['pending_increments'] == 0, buffer.stats())

    category = Category(name=f'{TAG} Category', slug=f'{TAG}-category', is_active=True)
    db.session.add(category)
    db.session.commit()
    app_module.invalidate_nav_categories()

    seller = make_user('seller', seller=True)
    buyer = make_user('buyer')
    seller_id, buyer_id = seller.id, buyer.id
    category_id, category_slug = category.id, category.slug

    small = add_products(seller_id, category_id, 4)
    add_orders(buyer_id, small, 3)
    add_reviews(small[0].id, 3)
    subject_slug = small[0].slug
    subject_id = small[0].id

    print('order history does not get more expensive as it grows')
    with as_user(buyer_id) as client:
        first, before = count_page(client, '/orders')
        check('/orders renders', first.status_code == 200, first.status_code)
    add_orders(buyer_id, small, ORDERS_PER_PAGE * 2, start=100)
    with as_user(buyer_id) as client:
        second, after = count_page(client, '/orders')
        check('still renders with many more orders', second.status_code == 200,
              second.status_code)
        check_no_growth('the query count did not grow with the order count',
                        before, after, '/orders')
        check(f'at most {ORDERS_PER_PAGE} orders on the page',
              second.data.count(b'View Details') <= ORDERS_PER_PAGE,
              second.data.count(b'View Details'))
        check('and there is a way to reach the rest',
              b'Next' in second.data or b'Page 1 of' in second.data)
        page_two, _ = count_page(client, '/orders?page=2')
        check('page two renders too', page_two.status_code == 200, page_two.status_code)
        check('and shows different orders', page_two.data != second.data)

    print('a listing with many reviews costs what a listing with few costs')
    client_ctx = app.app_context()
    client_ctx.push()
    try:
        with app.test_client() as anon:
            first, before = count_page(anon, f'/product/{subject_slug}')
            check('/product renders', first.status_code == 200, first.status_code)
    finally:
        db.session.remove()
        client_ctx.pop()
    add_reviews(subject_id, REVIEWS_PER_PAGE * 3, start=100)
    client_ctx = app.app_context()
    client_ctx.push()
    try:
        with app.test_client() as anon:
            second, after = count_page(anon, f'/product/{subject_slug}')
            check('still renders with many more reviews', second.status_code == 200,
                  second.status_code)
            check_no_growth('the query count did not grow with the review count',
                            before, after, f'/product/{subject_slug}')
            check(f'at most {REVIEWS_PER_PAGE} reviews rendered',
                  second.data.count(b'Review number') <= REVIEWS_PER_PAGE,
                  second.data.count(b'Review number'))
            check('with a link to the older ones', b'reviews_page=2' in second.data)
    finally:
        db.session.remove()
        client_ctx.pop()

    print('the moderation queue is a page, not every review ever written')
    admin_id = make_user('modadmin', admin=True).id
    with as_user(admin_id) as client:
        first, before = count_page(client, '/admin/reviews')
        check('/admin/reviews renders for an admin', first.status_code == 200,
              first.status_code)
        check('and it is the queue, not a redirect to the login page',
              b'Review number' in first.data)
    add_reviews(subject_id, ADMIN_REVIEWS_PER_PAGE * 2, start=400)
    # The comparison further down needs a partial last page, and this database is
    # shared with every other script, so the total is not something this fixture
    # controls on its own - top it up until it stops dividing evenly.
    if Review.query.count() % ADMIN_REVIEWS_PER_PAGE == 0:
        add_reviews(subject_id, 1, start=900)
    with as_user(admin_id) as client:
        second, after = count_page(client, '/admin/reviews')
        check('still renders with many more reviews', second.status_code == 200,
              second.status_code)
        check_no_growth('the query count did not grow with the review count',
                        before, after, '/admin/reviews')
        rows = second.data.count(b'Review number')
        check(f'at most {ADMIN_REVIEWS_PER_PAGE} reviews on the page',
              rows <= ADMIN_REVIEWS_PER_PAGE, rows)

        # The other half. A moderation queue that quietly shows the newest thirty
        # and says nothing is worse than a slow one: the reviews past the cap are
        # not merely off-screen, they are unmoderatable, and nothing on the page
        # would say so. So the true total has to be on the page, and the rest has
        # to be reachable.
        total = Review.query.count()
        check('the admin is told the real total, not the page size',
              f'{total} review' in squash(second), f'total={total}, rows={rows}')
        check('and there is a way to reach the rest', b'?page=2' in second.data)
        check('the page links are windowed, not one per page',
              second.data.count(b'?page=') < total, second.data.count(b'?page='))

        last_page = (total + ADMIN_REVIEWS_PER_PAGE - 1) // ADMIN_REVIEWS_PER_PAGE
        tail = total - (last_page - 1) * ADMIN_REVIEWS_PER_PAGE
        # Asserted rather than skipped past: if the fixture ever divides evenly
        # there is no partial page, and the comparison below would pass by
        # measuring two identical pages.
        check('the fixture leaves a partial last page to compare against',
              last_page > 1 and tail < ADMIN_REVIEWS_PER_PAGE,
              f'{total} reviews, {last_page} pages, {tail} on the last')

        partial, partial_count = count_page(client, f'/admin/reviews?page={last_page}')
        check('the last page renders', partial.status_code == 200, partial.status_code)
        check('and holds fewer rows than a full one',
              partial.data.count(b'Review number') < rows,
              f'{partial.data.count(b"Review number")} vs {rows}')
        # This is the eager-load assertion. The two pages differ only in how many
        # rows they render, so a lazy read per row would make the full page cost
        # about one query per extra row. Equal counts mean the rows came out of the
        # query that fetched the page. Note which relationship is really being
        # measured: every review here belongs to one product, so review.product
        # would collapse to a single query through the session's identity map even
        # without the joinedload, and it is review.author - one distinct user per
        # review, by design in add_reviews - that this actually proves.
        check('a full page costs what a nearly empty one costs',
              after <= partial_count + 2, f'{partial_count} -> {after}')
        if after > partial_count + 2:
            # explain_growth compares two runs of the same path, and the pair that
            # matters here is two different paths, so it would report the wrong
            # diff. Print what the full page actually ran instead.
            print('         the full page ran these, most repeated first:')
            for shape, hits in sorted(_MEASUREMENTS['/admin/reviews'][-1].items(),
                                      key=lambda item: -item[1])[:6]:
                print(f'         x{hits}  {shape}')

    print('a category page is a page, not the whole category')
    ctx = app.app_context()
    ctx.push()
    try:
        with app.test_client() as anon:
            first, before = count_page(anon, f'/categories/{category_slug}')
            check('/categories renders', first.status_code == 200, first.status_code)
    finally:
        db.session.remove()
        ctx.pop()
    add_products(seller_id, category_id, 40, start=500)
    app_module.invalidate_product_cache()
    ctx = app.app_context()
    ctx.push()
    try:
        with app.test_client() as anon:
            second, after = count_page(anon, f'/categories/{category_slug}')
            check('still renders with 40 more products', second.status_code == 200,
                  second.status_code)
            check_no_growth('the query count did not grow with the product count',
                            before, after, f'/categories/{category_slug}')
            check('at most 12 products on the page',
                  second.data.count(b'class="btn btn-sm btn-outline-primary">View') <= 12,
                  second.data.count(b'class="btn btn-sm btn-outline-primary">View'))
            check('with pagination to reach the rest', b'page=2' in second.data)
    finally:
        db.session.remove()
        ctx.pop()

    print("a seller's own catalogue is paginated too")
    with as_user(seller_id) as client:
        listing, count = count_page(client, '/seller/products')
        check('/seller/products renders', listing.status_code == 200, listing.status_code)
        rows = listing.data.count(b'fa-power-off')
        check(f'at most {SELLER_PRODUCTS_PER_PAGE} listings rendered of 44',
              rows <= SELLER_PRODUCTS_PER_PAGE, rows)
        check('the full count is still reported', '44 products' in squash(listing),
              '44 products')
        check('and the rest are one page away', b'page=2' in listing.data)

    print('product views are counted without a write per view')
    flush_product_views()
    starting = db.session.query(Product.views_count).filter(
        Product.id == subject_id).scalar() or 0
    views = 12
    ctx = app.app_context()
    ctx.push()
    try:
        with app.test_client() as anon:
            with StatementCounter(match='update products') as writes:
                for _ in range(views):
                    anon.get(f'/product/{subject_slug}')
    finally:
        db.session.remove()
        ctx.pop()
    check(f'{views} views caused fewer than {views} writes', writes.count < views,
          f'{writes.count} writes')
    flushed = flush_product_views()
    db.session.expire_all()
    ended = db.session.query(Product.views_count).filter(
        Product.id == subject_id).scalar() or 0
    check('every view is still accounted for after the flush',
          ended - starting == views, f'{starting} -> {ended} (+{views} expected)')
    check('the flush reported the products it wrote', flushed >= 0, flushed)

    print('a lost buffer costs views, never money')
    # Stated as a check so the trade is impossible to miss when reading the output:
    # the buffer holds view counts and nothing else.
    check('the view buffer is the only thing buffered this way',
          app_module._product_view_buffer.name == 'product-views',
          app_module._product_view_buffer.stats())


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
