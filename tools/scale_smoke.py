"""Smoke check for the scale/runtime layer.

Run with: python tools/scale_smoke.py

Verifies the runtime tables exist, that the settings cache serves repeated reads
without a query and invalidates on write, that the ephemeral store round-trips,
and that a job lease can be taken and renewed. Cleans up everything it creates.
"""

import os
import sys

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect as sa_inspect

import runtime
import config
from main import (app, db, queue_outbound, send_email, send_sms_notification,
                 sms_provider_configured)
from models import JobLock, OutboundMessage, Setting
from scale import int_env, pool_plan

FAILURES = []


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + str(detail)) if detail else ""}')


POOL_KEYS = ('WEB_CONCURRENCY', 'GUNICORN_THREADS', 'MAX_WEB_CONCURRENCY',
             'DB_CONNECTION_BUDGET', 'DB_POOL_SIZE', 'DB_MAX_OVERFLOW',
             'DB_CONNECTION_PLAN')


def plan_under(**env):
    """(pool_size, max_overflow, workers, total) for one environment.

    Clears every pool knob first, so a check reads the same whether it runs on a
    developer's machine with half of these exported or on a bare CI box.
    """
    saved = {key: os.environ.pop(key, None) for key in POOL_KEYS}
    try:
        os.environ.update({k: str(v) for k, v in env.items()})
        pool_size, overflow, workers = pool_plan()
        return pool_size, overflow, workers, workers * (pool_size + overflow)
    finally:
        for key in POOL_KEYS:
            os.environ.pop(key, None)
            if saved.get(key) is not None:
                os.environ[key] = saved[key]


def check_pool_plan():
    """The pool arithmetic, which is the one setting that can refuse every request.

    Postgres does not slow down when the pool is oversized - it stops accepting
    connections, for every worker at once, and the traceback names whichever query
    happened to be next. So what is asserted here is the product, across host
    sizes nobody has deployed to yet.
    """
    print('database pool stays inside its budget')
    cap = int_env('DB_CONNECTION_BUDGET', 80)
    for workers in (1, 5, 9, 17, 33, 64, 200):
        _, _, _, total = plan_under(WEB_CONCURRENCY=workers)
        # More workers than the budget has connections is over-provisioning that no
        # pool size can undo - one each is the floor. Anything above that floor is
        # the config's fault, and is what this asserts.
        allowed = max(cap, workers)
        check(f'{workers} workers stay under the {cap}-connection budget',
              total <= allowed, f'{total} connections')

    # The old fixed 5 + 5 is what these numbers are being compared against: at 17
    # workers it asked for 170 connections against a cap that is usually 100.
    _, _, _, total = plan_under(WEB_CONCURRENCY=17)
    check('the eight-core case that used to want 170 is now bounded',
          total <= cap, f'{total} connections')

    pool_size, overflow, _, _ = plan_under(WEB_CONCURRENCY=4, GUNICORN_THREADS=8)
    check('a pool covers every thread plus the outbox write',
          pool_size >= 8 + 1 or pool_size + overflow >= 8 + 1,
          f'{pool_size} + {overflow} for 8 threads')

    pool_size, overflow, _, _ = plan_under(DB_POOL_SIZE=7, DB_MAX_OVERFLOW=11)
    check('an explicit DB_POOL_SIZE still wins', (pool_size, overflow) == (7, 11),
          f'{pool_size} + {overflow}')

    pool_size, overflow, _, _ = plan_under(DB_POOL_SIZE='not-a-number')
    check('and an unparseable one falls back instead of failing to boot',
          pool_size >= 2, f'{pool_size} + {overflow}')

    tight = plan_under(WEB_CONCURRENCY=33, DB_CONNECTION_BUDGET=20)
    check('a budget smaller than the worker count still yields a usable pool',
          tight[0] >= 1 and tight[3] <= max(20, 33 * 2), tight)

    # A budget is a ceiling, not a target: raising it lets more workers fit, it
    # does not inflate each worker's pool past what its threads can use.
    small = plan_under(WEB_CONCURRENCY=9, DB_CONNECTION_BUDGET=80)
    large = plan_under(WEB_CONCURRENCY=9, DB_CONNECTION_BUDGET=400)
    check('raising the budget never shrinks the pool', large[0] >= small[0],
          f'{small[0]} -> {large[0]}')
    check('and never inflates it past what the threads can use',
          large[0] <= int_env('GUNICORN_THREADS', 4) + 1, large)

    sqlite_opts = config._engine_options('sqlite:///smoke.db')
    check('sqlite is left alone', 'pool_size' not in sqlite_opts, sqlite_opts)
    pg_opts = config._engine_options('postgresql://u:p@h/db')
    check('postgres gets pre-ping and recycling',
          pg_opts.get('pool_pre_ping') and pg_opts.get('pool_recycle'), pg_opts)


def main():
    with app.app_context():
        inspector = sa_inspect(db.session.get_bind())
        tables = set(inspector.get_table_names())

        print('schema')
        for table in ('ephemeral_kv', 'outbound_messages', 'job_locks'):
            check(f'table {table}', table in tables)
        order_columns = {col['name'] for col in inspector.get_columns('orders')}
        check('orders.mpesa_checkout_request_id',
              'mpesa_checkout_request_id' in order_columns)
        order_indexes = {idx['name'] for idx in inspector.get_indexes('orders')}
        check('index ix_orders_mpesa_checkout',
              'ix_orders_mpesa_checkout' in order_indexes)

        print('settings cache')
        Setting.invalidate_cache()
        Setting.set('scale_smoke_key', 'one')
        before = Setting.cache_stats()
        first = Setting.get('scale_smoke_key')
        second = Setting.get('scale_smoke_key')
        after = Setting.cache_stats()
        check('repeated read returns the value', first == second == 'one', second)
        check('second read was a cache hit',
              after['hits'] > before['hits'], after)
        Setting.set('scale_smoke_key', 'two')
        check('write invalidates the cache', Setting.get('scale_smoke_key') == 'two')
        Setting.delete('scale_smoke_key')
        check('delete falls back to the default',
              Setting.get('scale_smoke_key', 'MISSING') == 'MISSING')

        print('ephemeral store')
        runtime.ephemeral_set('scale_smoke_kv', {'a': 1}, ttl_seconds=60)
        check('json round trip', runtime.ephemeral_get_json('scale_smoke_kv') == {'a': 1})
        runtime.ephemeral_set('scale_smoke_kv', {'a': 2}, ttl_seconds=60)
        check('upsert overwrites', runtime.ephemeral_get_json('scale_smoke_kv') == {'a': 2})
        runtime.ephemeral_delete('scale_smoke_kv')
        check('delete clears', runtime.ephemeral_get_json('scale_smoke_kv') is None)
        runtime.ephemeral_set('scale_smoke_expired', 'x', ttl_seconds=1)
        db.session.execute(db.text(
            "UPDATE ephemeral_kv SET expires_at = '2000-01-01 00:00:00' "
            "WHERE key = 'scale_smoke_expired'"))
        db.session.commit()
        check('expired row reads as absent',
              runtime.ephemeral_get('scale_smoke_expired') is None)
        check('sweeper removes expired rows', runtime.sweep_ephemeral() >= 1)

        print('outbound queue')
        calls = []
        runtime.register_sender('smoke', lambda message: calls.append(message.recipient) or True)
        queued = runtime.enqueue('smoke', recipient='+254700000000', body='hi')
        sent, failed, requeued = runtime.drain_outbound(limit=10)
        check('drain delivered the queued job', sent >= 1, (sent, failed, requeued))
        check('sender actually ran', '+254700000000' in calls, calls)
        OutboundMessage.query.filter_by(channel='smoke').delete()
        db.session.commit()
        # SQLite reuses the primary keys we just freed, so drop the deleted rows
        # from the identity map before inserting again.
        db.session.expunge_all()

        print('concurrent claiming')
        for index in range(4):
            runtime.enqueue('smoke', recipient=f'claim-{index}', body='x', commit=False)
        db.session.commit()
        claim_a = runtime.claim_batch(limit=2)
        claim_b = runtime.claim_batch(limit=10)
        overlap = {row.id for row in claim_a} & {row.id for row in claim_b}
        check('two claims never hand over the same row', not overlap, overlap)
        check('between them they claim all four', len(claim_a) + len(claim_b) == 4,
              (len(claim_a), len(claim_b)))
        OutboundMessage.query.filter_by(channel='smoke').delete()
        db.session.commit()

        # Enqueue only, never drain: draining these would hit the live provider
        # and put a real message on someone's phone.
        print('email and sms enqueue instead of sending')
        db.session.rollback()
        try:
            check('send_email reports accepted',
                  send_email('nobody@example.invalid', 'Smoke', '<p>hi</p>'))
            check('send_email queued a row instead of connecting',
                  OutboundMessage.query.filter_by(
                      channel='email', recipient='nobody@example.invalid').count() == 1)
            check('send_sms_notification reports accepted',
                  send_sms_notification('+254700000001', 'smoke'))
            check('send_sms_notification queued a row instead of connecting',
                  OutboundMessage.query.filter_by(
                      channel='sms', recipient='+254700000001').count() == 1)
        finally:
            OutboundMessage.query.filter(
                OutboundMessage.recipient.in_(['nobody@example.invalid', '+254700000001'])
            ).delete(synchronize_session=False)
            db.session.commit()

        print('queue_outbound picks the right connection')
        db.session.rollback()
        check('a clean session gets its own connection',
              queue_outbound('smoke', 'clean-session', body='x'))
        check('and the row is committed immediately',
              OutboundMessage.query.filter_by(recipient='clean-session').count() == 1)
        pending = Setting(key='scale_smoke_dirty', value='1')
        db.session.add(pending)
        check('a dirty session joins the caller transaction',
              queue_outbound('smoke', 'dirty-session', body='x'))
        db.session.rollback()
        check('so a rolled back caller sends nothing',
              OutboundMessage.query.filter_by(recipient='dirty-session').count() == 0)
        OutboundMessage.query.filter_by(channel='smoke').delete()
        Setting.query.filter_by(key='scale_smoke_dirty').delete()
        db.session.commit()

        print('unconfigured channels are terminal, not retried forever')
        if sms_provider_configured():
            print('  [skip] SMS credentials are live; not draining a real send')
        else:
            queued = runtime.enqueue('sms', recipient='+254700000002', body='no provider')
            queued_id = queued.id
            sent, failed, requeued = runtime.drain_outbound(limit=10)
            parked = db.session.get(OutboundMessage, queued_id)
            check('a channel with no credentials parks as dead',
                  parked is not None and parked.status == 'dead',
                  parked.status if parked else None)
            check('and is not queued for another attempt', requeued == 0, requeued)
            OutboundMessage.query.filter_by(recipient='+254700000002').delete()
            db.session.commit()

        print('job lease')
        check('lease acquired', runtime.acquire_lease('scale_smoke_job'))
        check('same worker renews', runtime.acquire_lease('scale_smoke_job'))
        runtime.mark_lease_run('scale_smoke_job')
        runtime.release_lease('scale_smoke_job')
        JobLock.query.filter_by(name='scale_smoke_job').delete()
        db.session.commit()

        print('housekeeping sweepers')
        results = runtime.housekeeping()
        for label, value in sorted(results.items()):
            check(f'{label} sweeper ran', value is not None, value)

    check_pool_plan()

    print()
    if FAILURES:
        print(f'{len(FAILURES)} check(s) failed: {", ".join(FAILURES)}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
