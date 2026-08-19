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
