"""Smoke check for the batched notification and digest fan-out.

Run with: python tools/fanout_smoke.py

The claim these paths make is not "it works" but "the cost does not grow with the
size of the audience", so this counts SQL statements as well as results. A loop
that issues one query per follower still passes a correctness test; it fails
here. Everything it creates is removed at the end, including on failure.
"""

import os
import sys

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
os.environ.setdefault('CACHE_TYPE', 'NullCache')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event
from sqlalchemy.orm import joinedload

import main as app_module
import runtime
from main import app, db, notify_storefront_followers
from models import (BusinessStorefront, Category, CategoryFollow, CustomerNotification,
                    OutboundMessage, PriceAlert, Product, StorefrontFollow, User)

FAILURES = []
FOLLOWERS = 40
TAG = 'fanoutsmoke'
# Status the job does not select, used to hold foreign alerts aside for a run.
PARKED = 'fanoutsmoke_parked'


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + str(detail)) if detail else ""}')


class StatementCounter:
    """Counts statements issued against the engine inside a with-block."""

    def __init__(self):
        self.count = 0

    def __enter__(self):
        self._hook = lambda conn, cursor, statement, params, context, many: self._bump()
        event.listen(db.engine, 'before_cursor_execute', self._hook)
        return self

    def _bump(self):
        self.count += 1

    def __exit__(self, *exc):
        event.remove(db.engine, 'before_cursor_execute', self._hook)
        return False


def build_fixture():
    """A storefront with FOLLOWERS followers, plus the owner following it too."""
    category = Category(name=f'{TAG} category', slug=f'{TAG}-category')
    db.session.add(category)
    db.session.flush()

    owner = User(username=f'{TAG}_owner', email=f'{TAG}_owner@example.invalid')
    owner.set_password('x')
    db.session.add(owner)
    db.session.flush()

    storefront = BusinessStorefront(
        owner_id=owner.id,
        business_name=f'{TAG} shop',
        slug=f'{TAG}-shop',
        status='approved',
    )
    db.session.add(storefront)
    db.session.flush()

    product = Product(
        name=f'{TAG} widget',
        slug=f'{TAG}-widget',
        description=f'{TAG} fixture product',
        selling_price=100.0,
        category_id=category.id,
        seller_id=owner.id,
        is_active=True,
    )
    db.session.add(product)
    db.session.flush()

    followers = []
    for index in range(FOLLOWERS):
        user = User(username=f'{TAG}_u{index}', email=f'{TAG}_u{index}@example.invalid')
        user.set_password('x')
        db.session.add(user)
        followers.append(user)
    db.session.flush()

    for user in followers:
        db.session.add(StorefrontFollow(user_id=user.id, storefront_id=storefront.id))
        db.session.add(CategoryFollow(user_id=user.id, category_id=category.id,
                                      email_updates=True))
    # The owner follows their own shop, to prove they are excluded.
    db.session.add(StorefrontFollow(user_id=owner.id, storefront_id=storefront.id))
    db.session.commit()
    return category, owner, storefront, product, followers


def teardown():
    """Remove everything tagged by this script.

    Driven off the tag rather than the objects the build returned, so a run that
    dies halfway through the fixture still cleans up after itself, and so a
    previous crashed run's rows are cleared before this one starts.
    """
    db.session.rollback()
    try:
        parked = unpark()
        if parked:
            print(f'  restored {parked} alert(s) left parked by an earlier run')
    except Exception as exc:
        db.session.rollback()
        print(f'  could not unpark alerts: {exc}')
    user_ids = [row[0] for row in db.session.query(User.id)
                .filter(User.username.like(f'{TAG}%')).all()]
    product_ids = [row[0] for row in db.session.query(Product.id)
                   .filter(Product.slug.like(f'{TAG}%')).all()]
    steps = [
        (OutboundMessage, OutboundMessage.recipient.like(f'{TAG}%')),
        (OutboundMessage, OutboundMessage.channel == 'notification_fanout'),
        (StorefrontFollow, StorefrontFollow.user_id.in_(user_ids or [0])),
        (CategoryFollow, CategoryFollow.user_id.in_(user_ids or [0])),
        (PriceAlert, PriceAlert.user_id.in_(user_ids or [0])),
        (CustomerNotification, CustomerNotification.user_id.in_(user_ids or [0])),
        (CustomerNotification, CustomerNotification.product_id.in_(product_ids or [0])),
        (Product, Product.slug.like(f'{TAG}%')),
        (BusinessStorefront, BusinessStorefront.slug.like(f'{TAG}%')),
        (User, User.username.like(f'{TAG}%')),
        (Category, Category.slug.like(f'{TAG}%')),
    ]
    for model, criteria in steps:
        try:
            model.query.filter(criteria).delete(synchronize_session=False)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            print(f'  cleanup of {model.__name__} failed: {exc}')


def run_price_alert_job(follower_ids):
    """Run notify_price_alerts() against this script's alerts and nothing else.

    The job deliberately works on the whole table, so in a database that already
    holds live alerts a bare call would mark a real user's alert notified and queue
    a real email to them. Foreign active alerts are parked under a status the job
    does not select, then restored - which also makes the statement and scoring
    counts below mean what they claim, instead of silently including whatever else
    happened to be pending.

    Returns (queued, statements, recommendation_calls).
    """
    foreign = [row[0] for row in db.session.query(PriceAlert.id)
               .filter(PriceAlert.status == 'active',
                       ~PriceAlert.user_id.in_(follower_ids or [0])).all()]
    if foreign:
        PriceAlert.query.filter(PriceAlert.id.in_(foreign)).update(
            {PriceAlert.status: PARKED}, synchronize_session=False)
        db.session.commit()

    calls = {'count': 0}
    real_reco = app_module.smart_product_recommendations

    def counting_reco(*a, **kw):
        calls['count'] += 1
        return real_reco(*a, **kw)

    app_module.smart_product_recommendations = counting_reco
    db.session.expire_all()
    try:
        with StatementCounter() as counter:
            queued = app_module.notify_price_alerts()
    finally:
        app_module.smart_product_recommendations = real_reco
        unpark()
        if foreign:
            print(f'  (parked and restored {len(foreign)} pre-existing alert(s))')
    return queued, counter.count, calls['count']


def unpark():
    """Put any parked alert back, including after a crashed earlier run."""
    restored = PriceAlert.query.filter(PriceAlert.status == PARKED).update(
        {PriceAlert.status: 'active'}, synchronize_session=False)
    db.session.commit()
    return restored


def forget(*models):
    """Drop bulk-deleted rows of ``models`` from the identity map.

    A bulk delete leaves the objects behind, and SQLite hands the freed primary
    keys straight back out, so the next insert collides with a stale identity.
    Only the named models are expunged - a blanket expunge would detach the
    fixture objects the rest of the run still uses.
    """
    for obj in list(db.session.identity_map.values()):
        if isinstance(obj, models):
            db.session.expunge(obj)


def run():
    category, owner, storefront, product, followers = build_fixture()
    follower_ids = [u.id for u in followers]

    print('storefront fan-out')
    db.session.expire_all()
    with StatementCounter() as counter:
        reached = notify_storefront_followers(
            storefront, 'Fan-out smoke', 'body', product_id=product.id)
    check(f'reached every follower but the owner ({FOLLOWERS})',
          reached == FOLLOWERS, reached)
    written = CustomerNotification.query.filter(
        CustomerNotification.user_id.in_(follower_ids),
        CustomerNotification.notification_type == 'storefront').count()
    check('one notification row per follower', written == FOLLOWERS, written)
    check('owner was not notified',
          CustomerNotification.query.filter_by(user_id=owner.id).count() == 0)
    check(f'cost did not scale with the audience ({counter.count} statements '
          f'for {FOLLOWERS} followers)',
          counter.count < FOLLOWERS, counter.count)
    check('the notifications were committed, not left in the session',
          not db.session.new)

    print('large fan-out goes to the queue')
    limit = runtime.FANOUT_INLINE_LIMIT
    try:
        runtime.FANOUT_INLINE_LIMIT = 5
        before = CustomerNotification.query.filter(
            CustomerNotification.user_id.in_(follower_ids),
            CustomerNotification.notification_type == 'queued-test').count()
        runtime.fanout_notifications(follower_ids, 'Queued fan-out', 'body',
                                     notification_type='queued-test')
        check('nothing written inline', before == 0, before)
        job = OutboundMessage.query.filter_by(channel='notification_fanout').first()
        check('a fan-out job was queued', job is not None)
        runtime.drain_outbound(limit=5)
        after = CustomerNotification.query.filter(
            CustomerNotification.user_id.in_(follower_ids),
            CustomerNotification.notification_type == 'queued-test').count()
        check('draining wrote every recipient', after == FOLLOWERS, after)
        OutboundMessage.query.filter_by(channel='notification_fanout').delete(
            synchronize_session=False)
        db.session.commit()
    finally:
        runtime.FANOUT_INLINE_LIMIT = limit

    print('category digest')
    db.session.expire_all()
    with StatementCounter() as counter:
        queued = app_module.send_category_follow_updates()
    digests = OutboundMessage.query.filter(
        OutboundMessage.channel == 'email',
        OutboundMessage.recipient.like(f'{TAG}_u%')).all()
    check(f'queued one digest per follower ({FOLLOWERS})',
          len(digests) == FOLLOWERS, (queued, len(digests)))
    check('every digest carries the category name',
          all(f'{TAG} category' in (d.subject or '') for d in digests))
    check('the body is built once, not per follower',
          len({d.body for d in digests}) == 1)
    check(f'cost did not scale with the audience ({counter.count} statements '
          f'for {FOLLOWERS} followers)',
          counter.count < FOLLOWERS, counter.count)
    OutboundMessage.query.filter(
        OutboundMessage.recipient.like(f'{TAG}%')).delete(synchronize_session=False)
    db.session.commit()
    forget(OutboundMessage)

    print('price alerts')
    for user in followers[:10]:
        db.session.add(PriceAlert(user_id=user.id, product_id=product.id,
                                  search_query=product.name, target_price=500.0,
                                  status='active'))
    db.session.commit()
    queued, statements, _ = run_price_alert_job(follower_ids)
    tagged_mail = OutboundMessage.query.filter(
        OutboundMessage.recipient.like(f'{TAG}_u%')).count()
    check('queued an email for each met alert', queued == 10 and tagged_mail == 10,
          (queued, tagged_mail))
    check('met alerts were marked notified',
          PriceAlert.query.filter(PriceAlert.user_id.in_(follower_ids),
                                  PriceAlert.status == 'active').count() == 0)
    # 10 alerts: one batch select, one email INSERT and one status UPDATE each,
    # a second (empty) page select and the commit. Lazy loading the product and
    # user would add 20 more on top of that.
    check(f'no lazy loads on top of the write cost ({statements} statements '
          f'for 10 alerts)', statements <= 30, statements)

    print('alerts load with their product and user in one query')
    PriceAlert.query.filter(PriceAlert.user_id.in_(follower_ids)).update(
        {PriceAlert.status: 'active', PriceAlert.last_notified_at: None},
        synchronize_session=False)
    db.session.commit()
    db.session.expire_all()
    with StatementCounter() as counter:
        alerts = (PriceAlert.query
                  .options(joinedload(PriceAlert.product), joinedload(PriceAlert.user))
                  .filter(PriceAlert.user_id.in_(follower_ids),
                          PriceAlert.status == 'active')
                  .order_by(PriceAlert.id.asc())
                  .all())
        hydrated = [(a.product.name, a.user.email) for a in alerts if a.product and a.user]
    check('one statement for the whole batch', counter.count == 1, counter.count)
    check('and every alert was really hydrated', len(hydrated) == 10, len(hydrated))

    print('a repeated search phrase is scored once, not once per watcher')
    PriceAlert.query.filter(PriceAlert.user_id.in_(follower_ids)).delete(
        synchronize_session=False)
    db.session.commit()
    forget(PriceAlert, OutboundMessage)
    for user in followers[:6]:
        db.session.add(PriceAlert(user_id=user.id, product_id=None,
                                  search_query='identical smoke phrase',
                                  target_price=0.01, status='active'))
    db.session.commit()
    _, _, reco_calls = run_price_alert_job(follower_ids)
    check('six watchers of one phrase cost one scoring pass',
          reco_calls == 1, reco_calls)


def main():
    with app.app_context():
        teardown()  # clear anything a previous crashed run left behind
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
