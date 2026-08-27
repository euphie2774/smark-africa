"""Runtime primitives for high-concurrency operation.

Everything here exists to keep slow, repeated or unbounded work out of the
request path. Three concerns, deliberately kept free of any Flask or database
import so this module can be pulled in from anywhere without a cycle:

  * ``TTLCache``    - a small thread-safe read cache, used in front of the
                      settings table (which the request path reads dozens of
                      times per page) and anything else with the same shape.
  * ``CounterBuffer`` - the write-side equivalent: coalesces per-request counter
                      increments so a popular product is not a row lock every
                      visitor has to queue for.
  * ``JobLease``    - the pure clock/identity logic behind "only one worker
                      should run this job", so the database side stays a thin
                      shell around it.
  * env helpers     - tolerant int/float/bool readers, since every knob here is
                      tunable without a deploy.

None of this requires Redis. REDIS_URL is honoured where it helps, but every
path has a working fallback, so no existing environment variable has to change
for the app to boot.
"""

import math
import os
import socket
import threading
import time
from array import array
from datetime import timedelta


class _Miss:
    """Sentinel for "not in the cache", distinct from a cached None."""

    __slots__ = ()

    def __repr__(self):
        return '<CACHE_MISS>'

    def __bool__(self):
        return False


CACHE_MISS = _Miss()
_MISS = CACHE_MISS


def int_env(name, default):
    try:
        return int(str(os.environ.get(name) or default).strip())
    except (TypeError, ValueError):
        return int(default)


def float_env(name, default):
    try:
        return float(str(os.environ.get(name) or default).strip())
    except (TypeError, ValueError):
        return float(default)


def bool_env(name, default=False):
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == '':
        return bool(default)
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


class TTLCache:
    """Thread-safe cache with a per-entry expiry and a hard entry cap.

    Bounded on purpose. An unbounded dict keyed by anything derived from user
    input is a memory leak that only shows up under the traffic it was added to
    survive, so the cap is not optional and eviction is not deferred.

    Eviction is soonest-expiry-first and only runs on insert into a full cache,
    which keeps the read path to a dict lookup under a lock.
    """

    def __init__(self, ttl_seconds=30.0, max_entries=4096, name='ttl-cache'):
        self.ttl = max(0.0, float(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self.name = name
        self._data = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key, default=None):
        found = self.lookup(key)
        return default if found is _MISS else found

    def lookup(self, key):
        """Like ``get`` but distinguishes a cached ``None`` from a miss.

        Callers that cache falsy values need this: a setting legitimately
        holding '' or None must not be re-fetched on every request just because
        it is falsy.
        """
        if self.ttl <= 0:
            return _MISS
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return _MISS
            expires_at, value = entry
            if expires_at <= now:
                self._data.pop(key, None)
                self.misses += 1
                return _MISS
            self.hits += 1
            return value

    def set(self, key, value, ttl=None):
        span = self.ttl if ttl is None else float(ttl)
        if span <= 0:
            return value
        expires_at = time.monotonic() + span
        with self._lock:
            if key not in self._data and len(self._data) >= self.max_entries:
                self._evict_locked()
            self._data[key] = (expires_at, value)
        return value

    def _evict_locked(self):
        """Drop expired entries; if none are expired, drop the nearest to it.

        Called with the lock held and only when the cache is full.
        """
        now = time.monotonic()
        expired = [k for k, (expires_at, _) in self._data.items() if expires_at <= now]
        if expired:
            for key in expired:
                self._data.pop(key, None)
            self.evictions += len(expired)
            return
        # Nothing expired, so make room by discarding the oldest quarter rather
        # than one entry at a time - otherwise a full cache pays a scan per
        # insert forever.
        victims = sorted(self._data, key=lambda k: self._data[k][0])[: max(1, self.max_entries // 4)]
        for key in victims:
            self._data.pop(key, None)
        self.evictions += len(victims)

    def invalidate(self, key):
        with self._lock:
            self._data.pop(key, None)

    def clear(self):
        with self._lock:
            self._data.clear()

    def stats(self):
        with self._lock:
            entries = len(self._data)
        total = self.hits + self.misses
        return {
            'name': self.name,
            'entries': entries,
            'max_entries': self.max_entries,
            'ttl_seconds': self.ttl,
            'hits': self.hits,
            'misses': self.misses,
            'evictions': self.evictions,
            'hit_rate': round(self.hits / total, 4) if total else 0.0,
        }


def pack_ids(ids):
    """Compact a list of primary keys for storage in a long-lived cache.

    Measured on 1000 ids: 35.2KB as a list of Python ints against 7.9KB as an
    ``array('q')``, 4.5x, because the array holds raw 8-byte slots instead of a
    thousand separately boxed integers plus a thousand pointers to them.

    **Why:** the caches this exists for hold one entry per distinct search, so their
    resident size is the entry cap times the per-entry cost, and both product and
    service searches cap their id list at 1000. At a 2048-entry cap that is 70MB of
    every gunicorn worker against a WORKER_MEMORY_MB budget of 200 - and reachable
    on purpose, not just in theory, since short substrings each match a large slice
    of the catalogue and are distinct cache keys. Packed, the same cap costs 16MB,
    so the entry cap can stay where the hit rate wants it.

    Falls back to a plain list rather than raising: these caches sit on public
    search paths, so a value that cannot pack - an id outside signed 64-bit, or a
    non-integer key from some future model - must cost memory, never a 500 on a
    page anyone can load.
    """
    try:
        return array('q', ids)
    except (TypeError, OverflowError, ValueError):
        return list(ids)


def unpack_ids(packed):
    """Whatever ``pack_ids`` stored, back as a plain list of ints.

    Callers slice and count the result, and one of them hands it to ``in_()``, so
    the packing stays strictly internal to the cache: nothing outside these two
    functions ever sees an array. Copying is also what keeps a cached entry
    immutable - a caller that sorted the returned list in place would otherwise be
    editing what every later hit reads.
    """
    return list(packed)


class SingleFlight:
    """Let one caller compute a missing cache entry while the rest wait for it.

    A TTL cache answers a warm key for free no matter how many ask at once -
    measured at 0 queries for 24 simultaneous callers. It does nothing at all for a
    *cold* key: the same measurement is 24 queries for 24 callers, one each, because
    nothing stands between them and the database. Sequential tests cannot see this,
    since with one caller the two cases are identical.

    That gap matters at two specific moments, and both are ordinary rather than
    exotic. Every TTL expiry on a popular key lands while requests for it are in
    flight, so the busiest search on the platform re-runs its query once per
    concurrent asker, every TTL, forever. And a deploy starts every worker with an
    empty cache under whatever traffic is already arriving, which is the worst
    possible moment to multiply the database load by the concurrency.

    **Striped, not per-key.** A dict of locks keyed by cache key is itself a key
    space that grows with traffic - the exact shape of leak this module's caches are
    bounded to avoid - and refcounting entries out of it again is a second chance to
    get it wrong. A fixed array of locks chosen by hash has no growth and no cleanup
    path to be wrong: two unrelated keys can collide and one waits briefly for the
    other, which costs a little latency on a miss and cannot cost correctness,
    because callers re-check the cache after acquiring and simply do their own work
    if it is still absent.

    **The lock is an optimisation and never a dependency.** ``run`` waits only up to
    ``timeout``; past that it computes anyway. So the worst thing a slow or stuck
    holder can do is return this path to exactly the behaviour it had before this
    class existed, rather than pile threads up behind it. That property is what makes
    this safe to put on a page anyone can load.

    **Per process, not per platform.** Each gunicorn worker holds its own locks and
    its own cache, so a cold key across twelve workers costs up to twelve queries -
    one each - not one. What this removes is the *inner* multiplier: the cost of a
    cold key stops scaling with the concurrency inside a worker and scales only with
    the worker count, which is a fixed number set at deploy. Collapsing it further
    would take a shared lock in Redis, and Redis is optional here by requirement, so
    a cross-process flight would either be a hard dependency or a second code path
    that only runs in production. Worker-count duplication is the deliberate price of
    not having either.
    """

    def __init__(self, stripes=64, timeout=2.5, name='single-flight'):
        self._locks = [threading.Lock() for _ in range(max(1, int(stripes)))]
        self.timeout = max(0.0, float(timeout))
        self.name = name
        # Guarded, unlike the read-mostly counters elsewhere in this module.
        # ``x += 1`` is a load-add-store, so concurrent callers do lose increments -
        # and the concurrency check asserts that every query beyond the first is
        # explained by a recorded timeout, which a lost increment turns into a
        # spurious failure. The lock is only ever taken on a cache miss, which is
        # already about to do a database query.
        self._counts = threading.Lock()
        self.collapsed = 0
        self.timeouts = 0

    def _bump(self, attribute):
        with self._counts:
            setattr(self, attribute, getattr(self, attribute) + 1)

    def run(self, key, read, compute):
        """Return ``read()``'s value, computing it once across concurrent callers.

        ``read`` must return the ``CACHE_MISS`` sentinel when absent, not None -
        a cached empty list is a real answer and re-computing it on every request
        would defeat the point on exactly the searches that return nothing.
        """
        lock = self._locks[hash(key) % len(self._locks)]
        acquired = lock.acquire(timeout=self.timeout) if self.timeout else False
        if not acquired:
            # Either a genuinely slow holder or an unlucky stripe collision. Neither
            # is worth queueing for: do the work, which is what would have happened
            # anyway without any of this.
            self._bump('timeouts')
            return compute()
        try:
            # Double-checked on purpose: whoever held this lock may have been
            # computing this very key and filling the cache while we waited.
            found = read()
            if found is not CACHE_MISS:
                self._bump('collapsed')
                return found
            return compute()
        finally:
            lock.release()

    def stats(self):
        return {
            'name': self.name,
            'stripes': len(self._locks),
            'timeout_seconds': self.timeout,
            'collapsed': self.collapsed,
            'timeouts': self.timeouts,
        }


class CounterBuffer:
    """Coalesces many small increments into occasional batched writes.

    A counter incremented inside the request writes a row per hit, so every
    concurrent visitor to the same popular item queues behind the same row lock:
    the busiest listing on the platform becomes the slowest one to open, and the
    hotter it gets the worse it gets. Holding the deltas here and flushing them
    together turns N writes into one, and takes the lock once per flush instead
    of once per view.

    The trade is deliberate and worth stating: increments still buffered when a
    worker is killed are lost, and a reader can be up to one flush interval
    behind. That is the right trade for a view count and the wrong one for
    anything a person is owed, so only counters belong in here - never money,
    stock or coins.

    Flushing is left to the caller, which is what keeps this module free of any
    database import: ``add`` returns the pending deltas when a flush is due and
    None otherwise, and the caller writes them.
    """

    def __init__(self, flush_after=50, flush_seconds=30, max_keys=5000, name='counter'):
        self.flush_after = max(1, int(flush_after))
        self.flush_seconds = max(0.0, float(flush_seconds))
        self.max_keys = max(1, int(max_keys))
        self.name = name
        self._pending = {}
        self._counted = 0
        self._last_flush = time.monotonic()
        self._lock = threading.Lock()
        self.buffered = 0
        self.flushes = 0

    def add(self, key, amount=1):
        """Record ``amount`` against ``key``; return deltas to write, or None.

        The returned dict has already been removed from the buffer, so a caller
        that fails to write it drops those increments rather than double-counting
        them on the next flush.
        """
        if key is None or not amount:
            return None
        with self._lock:
            self._pending[key] = self._pending.get(key, 0) + amount
            self._counted += abs(amount)
            self.buffered += abs(amount)
            if not self._due_locked():
                return None
            return self._drain_locked()

    def _due_locked(self):
        if self._counted >= self.flush_after or len(self._pending) >= self.max_keys:
            return True
        return bool(self.flush_seconds) and (
            time.monotonic() - self._last_flush >= self.flush_seconds)

    def _drain_locked(self):
        pending, self._pending = self._pending, {}
        self._counted = 0
        self._last_flush = time.monotonic()
        self.flushes += 1
        return pending

    def drain(self):
        """Take everything buffered regardless of whether a flush was due.

        For shutdown and for the housekeeping pass, so a quiet counter still
        lands eventually instead of waiting for traffic that may not come.
        """
        with self._lock:
            if not self._pending:
                return {}
            return self._drain_locked()

    def stats(self):
        with self._lock:
            keys, counted = len(self._pending), self._counted
        return {
            'name': self.name,
            'pending_keys': keys,
            'pending_increments': counted,
            'flush_after': self.flush_after,
            'flush_seconds': self.flush_seconds,
            'total_buffered': self.buffered,
            'flushes': self.flushes,
        }


def _cgroup_value(*paths):
    """The first of these cgroup files that can be read, stripped. '' if none."""
    for path in paths:
        try:
            with open(path) as handle:
                return handle.read().strip()
        except OSError:
            continue
    return ''


def container_memory_limit():
    """Bytes of RAM this process is allowed, or 0 when nothing limits it.

    Inside a container the host's total RAM is the wrong number, and it is the
    only number Python offers. A 512MB instance scheduled onto a sixteen-core
    host still reports sixteen cores to ``os.cpu_count()``, so the classic worker
    formula sizes for the machine and the process gets killed for exceeding its
    slice. An OOM kill produces no application traceback and, on a small
    instance, often no log line at all - the failure that is hardest to diagnose
    is the one this function exists to prevent.
    """
    raw = _cgroup_value('/sys/fs/cgroup/memory.max',                    # cgroup v2
                        '/sys/fs/cgroup/memory/memory.limit_in_bytes')  # cgroup v1
    if not raw or raw == 'max':
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    # cgroup v1 spells "unlimited" as a sentinel just under 2**63 rather than as
    # a word, and treating that as a real limit would cap nothing while looking
    # like it capped something.
    if value <= 0 or value > (1 << 62):
        return 0
    return value


def container_cpu_quota():
    """CPUs this process may use, as a float. 0.0 when unrestricted.

    Render's free and starter instances sell a fraction of a core, so this is
    usually well below one and ``os.cpu_count()`` is off by more than an order
    of magnitude.
    """
    raw = _cgroup_value('/sys/fs/cgroup/cpu.max')  # v2: "<quota> <period>" or "max <period>"
    if raw:
        parts = raw.split()
        if len(parts) == 2 and parts[0] != 'max':
            try:
                period = int(parts[1])
                return int(parts[0]) / period if period > 0 else 0.0
            except ValueError:
                return 0.0
        return 0.0
    quota = _cgroup_value('/sys/fs/cgroup/cpu/cpu.cfs_quota_us')       # v1
    period = _cgroup_value('/sys/fs/cgroup/cpu/cpu.cfs_period_us')
    try:
        if int(quota) > 0 and int(period) > 0:
            return int(quota) / int(period)
    except ValueError:
        pass
    return 0.0


def worker_plan():
    """The worker and thread counts the web server will actually run with.

    One function, read by both gunicorn.conf.py and config.py, because the
    database pool has to be sized against the real process count: two files each
    deriving it from the CPU count would agree on the day they were written and
    quietly disagree after the first edit.

    The worker count is capped three ways. ``cpu_count * 2 + 1`` is the classic
    sync-worker formula and it assumes one request per worker, but these workers
    run four threads each, so on a sixteen-core host the uncapped formula asks
    for 33 processes and 132 concurrent requests against one database.

    The other two caps come from the container rather than the host, and they are
    the ones that decide whether the app boots at all. Each worker is a separate
    process holding its own copy of this application, so RAM - not CPU - is the
    binding constraint on a small instance: nine workers of roughly 110MB each
    cannot start inside 512MB, and what the operator sees is a service that
    returns 503 with an empty log, because the kernel killed the processes before
    anything got far enough to write a line. WEB_CONCURRENCY still overrides
    everything for anyone who has measured their own host.
    """
    try:
        cpus = os.cpu_count() or 1
    except NotImplementedError:
        cpus = 1
    quota = container_cpu_quota()
    if quota:
        # Round up, so half a core still gets one worker rather than zero.
        cpus = max(1, int(math.ceil(quota)))
    threads = max(1, int_env('GUNICORN_THREADS', 4))
    default_workers = min(cpus * 2 + 1, int_env('MAX_WEB_CONCURRENCY', 12))

    limit = container_memory_limit()
    if limit:
        # Measured at import, not guessed: this app is a single large module plus
        # SQLAlchemy and Flask. Deliberately generous, because being one worker
        # short costs a little throughput while being one worker over costs the
        # whole service.
        per_worker = max(1, int_env('WORKER_MEMORY_MB', 200)) * 1024 * 1024
        # Not the whole limit: the master process, the pooled connections and any
        # request holding an uploaded image all live in the same budget.
        affordable = int(limit * 0.75) // per_worker
        default_workers = max(1, min(default_workers, affordable))

    workers = max(1, int_env('WEB_CONCURRENCY', default_workers))
    return workers, threads


def pool_plan():
    """Per-worker database pool sizes, derived from a total connection budget.

    Returns ``(pool_size, max_overflow, workers)``.

    Every worker is a separate process holding a pool of its own, so what the
    database sees is ``workers x (pool_size + max_overflow)`` - never pool_size on
    its own. Fixed per-worker defaults therefore encode a total that changes with
    the size of the host: 5 + 5 across the uncapped worker formula is 90
    connections on a four-core box and 170 on an eight-core one, against a
    provider cap that is usually 100. Nothing in any request would be at fault -
    the app would simply start refusing connections for everyone at once after a
    deploy onto a bigger machine, and the error would not point here.

    So the budget is stated as a total and divided by the worker count, rather
    than stated per worker and multiplied by a number nobody looked at. Explicit
    DB_POOL_SIZE and DB_MAX_OVERFLOW still win; this only decides what happens
    when they are unset, which is the case that was wrong.
    """
    workers, threads = worker_plan()
    # Headroom under the provider cap for everything else that connects: a
    # migration, a psql session, the provider's own metrics scraper.
    budget = max(1, int_env('DB_CONNECTION_BUDGET', 80))
    # Floored at one, not two. Two looks like the safer floor and is not: at more
    # workers than half the budget it silently spends double what the operator
    # allowed, which is the failure this function exists to prevent. One
    # connection per worker is slow - four threads taking turns on it - but it
    # runs, and it obeys the number it was given. More workers than connections
    # is over-provisioning that no pool size can fix; the plan is reported at boot
    # so it is visible rather than inferred from a refused connection.
    per_worker = max(1, budget // workers)

    # A thread holds a connection for as long as it is serving a request, and
    # enqueue_detached briefly takes a second one to write its outbox row, so a
    # fully busy worker wants one more than its thread count.
    pool_size = max(1, int_env('DB_POOL_SIZE', max(1, min(threads + 1, per_worker))))
    # Overflow connections are opened and closed per use rather than kept, so they
    # are a burst valve rather than capacity. Small on purpose.
    headroom = max(0, per_worker - pool_size)
    max_overflow = max(0, int_env('DB_MAX_OVERFLOW', min(headroom, 3)))
    return pool_size, max_overflow, workers


def worker_identity():
    """Stable-per-process label used to own a job lease.

    Host plus PID, so a lease held by a dead worker on another instance is
    still attributable in logs when it expires.
    """
    try:
        host = socket.gethostname()
    except Exception:
        host = 'unknown-host'
    return f'{host}:{os.getpid()}'


class JobLease:
    """Clock logic for "one worker runs this, the rest stand down".

    Kept separate from storage so the rules are testable without a database:
    a lease is takeable when it is unheld, already ours, or expired. The holder
    renews well before expiry; if the holder dies, the lease simply times out
    and the next worker to check takes it over.
    """

    def __init__(self, ttl_seconds=90.0):
        self.ttl = max(5.0, float(ttl_seconds))

    @property
    def renew_after(self):
        """Renew at a third of the term so one slow cycle never drops it."""
        return self.ttl / 3.0

    def is_takeable(self, holder, expires_at, now, me):
        if not holder:
            return True
        if holder == me:
            return True
        if expires_at is None:
            return True
        return expires_at <= now

    def next_expiry(self, now):
        return now + timedelta(seconds=self.ttl)
