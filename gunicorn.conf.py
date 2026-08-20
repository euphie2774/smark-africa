import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scale import int_env, pool_plan, worker_plan

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
# Use sync workers to avoid gevent/SSL recursion bug on Python 3.14
worker_class = 'sync'

# Both numbers come from scale.worker_plan, which config.py also reads to size the
# database pool. Deriving them here as well is how the pool ends up sized for a
# process count the server does not actually run.
workers, threads = worker_plan()

timeout = 120
graceful_timeout = 30

# Import the application once in the master and let the workers inherit it,
# instead of every worker importing it independently. Two reasons, and on a small
# instance both are the difference between booting and not:
#
#   * Memory. Forked children share the parent's pages copy-on-write, so the
#     interpreter, Flask, SQLAlchemy and this application's own module are paid
#     for once rather than once per worker. Without it each worker carries its own
#     full copy and the instance is killed before it can log why.
#   * init_database() runs at import. Nine workers importing concurrently means
#     nine processes running create_all() and the whole additive migration against
#     one database at the same moment, blocking on each other's DDL locks. Once,
#     in the master, before any worker exists, is what was always intended.
preload_app = True

# Threads do not survive fork(), and main.py starts its outbound queue drainer at
# import. Under preload that import happens in the master, so the drainer would end
# up in the supervisor process and no worker would have one - delivery pinned to a
# single thread in the one process that is not supposed to run application code, and
# nothing draining at all if that thread dies. Its own docstring says every worker
# should drain. So the import-time start is suppressed here and post_fork starts one
# inside each worker instead. setdefault, so an operator who set this deliberately
# keeps their value.
os.environ.setdefault('DEFER_OUTBOUND_WORKER', '1')


def post_fork(server, worker):
    """Give this worker its own database pool and its own queue drainer.

    Disposing is required by preload_app, not optional with it: any connection the
    master opened during import is now a socket that several processes each believe
    they own, and two workers using it at once get each other's responses. Disposing
    replaces the pool with an empty one, so this worker dials its own.
    """
    try:
        from main import db
        db.engine.dispose()
    except Exception as exc:  # pragma: no cover - boot path
        server.log.warning('post_fork engine dispose skipped: %s', exc)
    try:
        import main
        if main.start_outbound_worker():
            server.log.info('outbound worker started in pid %s', worker.pid)
    except Exception as exc:  # pragma: no cover - boot path
        server.log.warning('post_fork outbound worker not started: %s', exc)


def when_ready(server):
    """Release the connections the master opened while importing the app.

    The master supervises and does not serve requests, so the pool it filled during
    init_database is a handful of connections nothing will read again, counted
    against the same provider cap the workers have to share. dispose() only empties
    the pool - anything in the master that still needs a connection opens a fresh
    one.
    """
    try:
        from main import db
        db.engine.dispose()
    except Exception as exc:  # pragma: no cover - boot path
        server.log.warning('master engine dispose skipped: %s', exc)

# Recycling workers bounds the damage from anything that leaks per request, but it
# also drops whatever that worker was holding: the jitter keeps a thousand-request
# cliff from arriving for every worker at the same moment, which would empty every
# buffered view count and every connection pool at once.
max_requests = int_env('GUNICORN_MAX_REQUESTS', 1000)
max_requests_jitter = int_env('GUNICORN_MAX_REQUESTS_JITTER', 100)

accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('LOG_LEVEL', 'info').lower()


def on_starting(server):
    """State the connection arithmetic once, before anything can fail on it.

    A pool exhausted at peak looks like slow requests, and a provider cap reached
    looks like the database is down; neither points at the multiplication that
    caused it. Printing it at boot means the number is in the logs above the
    incident rather than absent from it.
    """
    pool_size, max_overflow, planned_workers = pool_plan()
    server.log.info(
        'db pool: %d workers x %d threads, %d pooled + %d overflow '
        '= up to %d connections (budget %s)',
        planned_workers, threads, pool_size, max_overflow,
        planned_workers * (pool_size + max_overflow),
        int_env('DB_CONNECTION_BUDGET', 80))
