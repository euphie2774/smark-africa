"""Concurrency smoke: simultaneous identical requests must not multiply the work.

Run with: python tools/concurrency_smoke.py

Every other check in this repo proves deduplication *sequentially* - call a cached
function twice in a row and watch the second one cost nothing. That is not the claim
the caching was added for. The claim is about many people asking the same thing at the
same moment, and sequential evidence says nothing about it: a cache with no
single-flight behaves identically in both tests right up until the entry is missing,
at which point every concurrent caller runs the query.

So this measures three things a sequential test cannot see:

  * **Warm concurrency.** With the entry present, N simultaneous callers must cost
    zero queries, not N. This is the actual "thousands of people open the same page"
    case, and it is also where a lock in the wrong place would show up as work.
  * **Cold concurrency.** With the entry missing, N simultaneous callers must cost
    **one** query, not N. This is what SingleFlight (scale.py) was added for, and it
    is the only claim in the repo that cannot be shown sequentially at all: before
    the wrapper this measured 24 queries for 24 callers - a full stampede on every
    TTL expiry and on every deploy's cold cache - and sequential tests read
    identically either way. Asserted as ``queries <= 1 + flight timeouts`` so there
    is no tolerance to hide behind: one caller computes, and any extra query has to
    be explained by the flight's bounded wait expiring on a slow machine.
  * **Thread safety of the packed entries.** ``pack_ids``/``unpack_ids`` store an
    ``array('q')`` and hand back a list copy. If that copy were ever skipped, one
    caller sorting or truncating its result would be editing what every other thread
    is reading, and the corruption would surface as wrong search results under load
    and nothing at all under test. Threads here mutate what they are given, hard, and
    then the entry is checked.

Deliberately not added to tools/run_all_checks.py. Thread interleaving is not
reproducible, and the suite's value is that a failure there means something broke.

Caveats worth stating rather than implying away: this is one process against the dev
database on Windows, not twelve gunicorn workers against Postgres on Linux. It cannot
measure throughput. What it can measure - whether concurrent identical work is
deduplicated, whether anything raises under threads, and whether cached state stays
intact - does not depend on any of that.
"""

import os
import sys
import threading

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
os.environ.setdefault('DISABLE_OUTBOUND_WORKER', '1')
# Not NullCache, unlike the other tools: this one is measuring the cache, so turning
# it off would make every assertion here pass for the wrong reason. FileSystemCache
# is the backend a deploy without REDIS_URL actually gets, and it is the one that
# routes the growing key space to the in-process TTLCache being tested.
os.environ.setdefault('CACHE_TYPE', 'FileSystemCache')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event

import main as app_module
from main import app, db
from models import Category, Product, User

FAILURES = []
TAG = 'concsmoke'
THREADS = 24


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + str(detail)) if detail else ""}')


class QueryCounter:
    """Counts statements matching ``match``, from every thread.

    The hook is registered on the engine, which every thread shares, so this is a
    total across the pool rather than a per-thread number - which is exactly what is
    wanted here: the question is how much work the database was asked to do in total
    for N simultaneous askers.
    """

    def __init__(self, match='from products'):
        self.match = match
        self.statements = []
        self._lock = threading.Lock()

    def __enter__(self):
        self._hook = lambda conn, cursor, statement, *a: self._bump(statement)
        event.listen(db.engine, 'before_cursor_execute', self._hook)
        return self

    def _bump(self, statement):
        text = ' '.join(str(statement).split()).lower()
        if self.match in text:
            with self._lock:
                self.statements.append(text)

    def __exit__(self, *exc):
        event.remove(db.engine, 'before_cursor_execute', self._hook)
        return False

    @property
    def count(self):
        with self._lock:
            return len(self.statements)


# The original name, kept because that is what the product sections read as.
def ProductQueryCounter():
    return QueryCounter('from products')


def run_concurrently(fn, threads=THREADS):
    """Start ``threads`` copies of ``fn`` and release them together.

    A barrier, not a loop of ``start()`` calls: without one, the first thread is
    usually finished before the last has been created, so the test would quietly
    measure sequential behaviour and report it as concurrent. Each thread gets its
    own app context because Flask-SQLAlchemy scopes the session to it - sharing one
    would have the threads sharing a session, which is a different bug than the one
    being looked for.
    """
    barrier = threading.Barrier(threads)
    results, errors = [None] * threads, [None] * threads

    def worker(index):
        try:
            barrier.wait(timeout=30)
            with app.app_context():
                results[index] = fn(index)
        except Exception as exc:            # noqa: BLE001 - reported, not swallowed
            errors[index] = f'{type(exc).__name__}: {exc}'

    pool = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(threads)]
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join(timeout=60)
    alive = [t for t in pool if t.is_alive()]
    return results, [e for e in errors if e], alive


def seed():
    category = Category.query.filter_by(slug=f'{TAG}-cat').first()
    if category is None:
        category = Category(name=f'{TAG} category', slug=f'{TAG}-cat', is_active=True)
        db.session.add(category)
        db.session.flush()
    seller = User.query.filter_by(username=f'{TAG}-seller').first()
    if seller is None:
        seller = User(username=f'{TAG}-seller', email=f'{TAG}@example.invalid')
        seller.set_password('x')
        db.session.add(seller)
        db.session.flush()
    existing = Product.query.filter(Product.slug.like(f'{TAG}-%')).count()
    for i in range(existing, 40):
        db.session.add(Product(
            name=f'{TAG} widget {i}', slug=f'{TAG}-{i}',
            description=f'{TAG} searchable body', short_description=f'{TAG} short',
            selling_price=100.0 + i, buying_price=50.0, stock=5,
            category_id=category.id, seller_id=seller.id,
            commission_percent=15.0, is_active=True, review_status='approved'))
    db.session.commit()
    return category


def teardown():
    db.session.rollback()
    try:
        Product.query.filter(Product.slug.like(f'{TAG}-%')).delete(synchronize_session=False)
        User.query.filter(User.username.like(f'{TAG}-%')).delete(synchronize_session=False)
        Category.query.filter(Category.slug.like(f'{TAG}-%')).delete(synchronize_session=False)
        db.session.commit()
    except Exception as exc:                # noqa: BLE001
        db.session.rollback()
        print(f'  cleanup failed: {exc}')


def run():
    ids_cache = app_module._product_ids_cache
    target = app_module.search_ids_cache_target()
    print(f'search id cache target: {target} (must not be "off", or nothing here means anything)')
    check('the cache under test is actually on', target != 'off', target)

    seed()
    expected = app_module.cached_product_search_ids(search=TAG)
    check('the seeded search finds its products', len(expected) >= 40, f'{len(expected)} ids')

    # --- warm concurrency: the case the caching exists for -------------------------
    print(f'{THREADS} simultaneous callers, entry already warm')
    with ProductQueryCounter() as counter:
        results, errors, alive = run_concurrently(
            lambda i: app_module.cached_product_search_ids(search=TAG))
    check('no caller raised', not errors, '; '.join(errors[:3]))
    check('no caller hung', not alive, f'{len(alive)} still running')
    check('a warm entry costs no queries however many ask at once', counter.count == 0,
          f'{counter.count} product queries for {THREADS} simultaneous callers')
    check('every caller got the same ids',
          all(r == expected for r in results if r is not None),
          f'{sum(1 for r in results if r != expected)} of {THREADS} differed')

    # --- the returned list must be a copy, proven under threads --------------------
    print('every caller mutates what it was handed')

    def mutate(index):
        got = app_module.cached_product_search_ids(search=TAG)
        # Exactly what a caller is entitled to do with a list it was given, and what
        # would corrupt the entry for everyone if the cache handed out its own.
        got.sort(reverse=True)
        del got[3:]
        got.append(-1)
        return len(got)

    results, errors, alive = run_concurrently(mutate)
    check('no caller raised while mutating', not errors, '; '.join(errors[:3]))
    after = app_module.cached_product_search_ids(search=TAG)
    check('the cached entry survived every caller mutating its result', after == expected,
          f'{len(after)} ids left of {len(expected)}')
    stored = ids_cache.get(app_module.product_search_cache_key(search=TAG)) \
        if target == 'local' else None
    if target == 'local':
        check('the entry is still packed after concurrent access',
              type(stored).__name__ == 'array', type(stored).__name__)

    # --- cold concurrency: measure the stampede rather than assume it --------------
    print(f'{THREADS} simultaneous callers, entry missing')
    ids_cache.clear()
    if target == 'shared':
        app_module.cache.delete(app_module.product_search_cache_key(search=TAG))
    flight_before = app_module._search_flight.stats()
    with ProductQueryCounter() as counter:
        results, errors, alive = run_concurrently(
            lambda i: app_module.cached_product_search_ids(search=TAG))
    cold = counter.count
    flight_after = app_module._search_flight.stats()
    # Deltas, not absolutes: these counters are cumulative for the life of the process
    # and the warm section above already moved them.
    timeouts = flight_after['timeouts'] - flight_before['timeouts']
    collapsed = flight_after['collapsed'] - flight_before['collapsed']
    check('no caller raised on a cold entry', not errors, '; '.join(errors[:3]))
    check('every caller got the same ids from a cold cache',
          all(r == expected for r in results if r is not None),
          f'{sum(1 for r in results if r != expected)} of {THREADS} differed')
    # The sharp form of the claim, with no magic number in it: one caller computes,
    # and every *additional* query must be accounted for by the single-flight's own
    # bounded wait expiring. So this is exact rather than a tolerance - a regression
    # that removed the deduplication would show 24 queries against 0 timeouts, and a
    # machine slow enough to blow the 2.5s wait explains its own extra queries.
    check('every query beyond the first is explained by a recorded flight timeout',
          cold <= 1 + timeouts,
          f'{cold} queries with {timeouts} timeouts')
    # Kept as the outer bound it always was: whatever the interleaving, a cold entry
    # must never cost *more* than one query per caller, which is what a retry loop or
    # a lookup that re-queries on contention would produce.
    check('a cold entry costs at most one query per caller', cold <= THREADS,
          f'{cold} product queries for {THREADS} callers')
    print(f'  ..... measured: {cold} queries for {THREADS} cold simultaneous callers '
          f'({cold / THREADS:.0%} of unbatched, {collapsed} collapsed, {timeouts} timed out)')

    # --- the other cached read paths, measured rather than assumed ------------------
    # The searches were wrapped because a duplicated query there is a full catalogue
    # scan. These three are small indexed reads, so the question was not whether they
    # can stampede - every unguarded cache can - but whether the burst is big enough
    # to be worth another lock. Measured here so that judgement rests on a number.
    #
    # It was worth it for all three, and the catalogue is why this loop exists rather
    # than a one-off measurement: it read 2 queries for 24 callers on one run and 24 on
    # the next, from identical code. A cache with no single-flight stampedes or does not
    # purely on thread timing, so "measured small once" is not evidence of anything. All
    # three now assert the sharp form, which cannot pass by luck.
    print('other cached reads, cold, all callers at once')
    for label, warm, clear, call, match, flight in (
        ('service ids', lambda: app_module.cached_service_ids(search=TAG),
         lambda: app_module._service_ids_cache.clear(),
         lambda i: app_module.cached_service_ids(search=TAG), 'from service_listings',
         app_module._search_flight),
        ('admin duty state (15s TTL)', lambda: app_module.service_duty_state(),
         lambda: app_module._service_duty_cache.clear(),
         lambda i: app_module.service_duty_state(), 'from users',
         app_module._service_flight),
        ('service catalogue', lambda: app_module.service_catalogue(),
         lambda: app_module._service_catalogue_cache.clear(),
         lambda i: app_module.service_catalogue(), 'from service_catalogue_items',
         app_module._service_flight),
    ):
        warm()
        clear()
        before = flight.stats()
        with QueryCounter(match) as counter:
            _, errors, alive = run_concurrently(call)
        timed_out = flight.stats()['timeouts'] - before['timeouts']
        check(f'{label}: no caller raised', not errors, '; '.join(errors[:3]))
        check(f'{label}: no caller hung', not alive, f'{len(alive)} still running')
        # Same sharp form as the product search above: one caller computes, and any
        # additional query has to be explained by the flight's own bounded wait.
        check(f'{label}: every query beyond the first is explained by a timeout',
              counter.count <= 1 + timed_out,
              f'{counter.count} queries with {timed_out} timeouts')
        print(f'  ..... {label}: {counter.count} queries for {THREADS} cold callers '
              f'({timed_out} timed out)')


    # Fanned out to what this process's connection pool can actually serve, not to
    # THREADS. A full page render holds a connection for its whole life, so 24 of them
    # at once against the local SQLite default of 5 pooled + 10 overflow exhausts the
    # pool and raises QueuePool timeouts - which says nothing about the code, because
    # production never has that shape: gunicorn gives each worker `threads` concurrent
    # requests against a pool sized by scale.pool_plan to cover them (asserted in
    # tools/scale_smoke.py). Reading the real capacity keeps this deterministic instead
    # of flaky, and the number is printed so a reduced fan-out is never silent.
    pool = db.engine.pool
    capacity = getattr(pool, 'size', lambda: 5)() + getattr(pool, '_max_overflow', 10)
    fetchers = max(2, min(THREADS, capacity - 1))
    print(f'{fetchers} simultaneous GETs of the same shop page '
          f'(pool capacity {capacity}; a render holds a connection throughout)')

    def fetch(index):
        with app.test_client() as client:
            response = client.get(f'/shop?search={TAG}')
            return response.status_code, len(response.data)

    results, errors, alive = run_concurrently(fetch, threads=fetchers)
    check('no request raised', not errors, '; '.join(errors[:3]))
    codes = [r[0] for r in results if r is not None]
    check('every concurrent request returned 200', codes and all(c == 200 for c in codes),
          f'{sorted(set(codes))}')
    sizes = {r[1] for r in results if r is not None}
    check('every concurrent request rendered the same page', len(sizes) == 1,
          f'{len(sizes)} distinct response sizes: {sorted(sizes)[:4]}')


if __name__ == '__main__':
    with app.app_context():
        try:
            run()
        finally:
            teardown()
    if FAILURES:
        print(f'\n{len(FAILURES)} check(s) failed')
        sys.exit(1)
    print('\nall checks passed')
