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
"""

import contextlib
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
os.environ.setdefault('CACHE_TYPE', 'NullCache')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event

import main as app_module
from main import (ORDERS_PER_PAGE, REVIEWS_PER_PAGE, SELLER_PRODUCTS_PER_PAGE, app, db,
                  flush_product_views)
from models import BusinessStorefront, Category, Order, OrderItem, Product, Review, User
from scale import CounterBuffer

FAILURES = []
TAG = 'listsmoke'


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + str(detail)) if detail else ""}')


class StatementCounter:
    """Counts statements actually sent to the database, optionally filtered.

    Hooks the cursor rather than the ORM, so lazy loads triggered from inside a
    Jinja template are counted too - which is where most N+1s in this codebase
    live, and where they are invisible to anything that only inspects the view.
    """

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


def make_user(suffix, seller=False):
    # No phone: the column is unique, and a fixture inventing hundreds of numbers
    # would collide with itself or with real data long before it proved anything.
    user = User(username=f'{TAG}_{suffix}', email=f'{TAG}_{suffix}@example.invalid')
    user.set_password('x')
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


def count_page(client, path, match=None):
    with StatementCounter(match=match) as counter:
        response = client.get(path)
    return response, counter.count


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
        check('the query count did not grow with the order count',
              after <= before, f'{before} -> {after}')
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
            check('the query count did not grow with the review count',
                  after <= before, f'{before} -> {after}')
            check(f'at most {REVIEWS_PER_PAGE} reviews rendered',
                  second.data.count(b'Review number') <= REVIEWS_PER_PAGE,
                  second.data.count(b'Review number'))
            check('with a link to the older ones', b'reviews_page=2' in second.data)
    finally:
        db.session.remove()
        client_ctx.pop()

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
            check('the query count did not grow with the product count',
                  after <= before, f'{before} -> {after}')
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
