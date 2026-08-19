"""Service layer over the runtime infrastructure tables.

Sits between the application and ``EphemeralKV`` / ``OutboundMessage`` /
``JobLock``. Three groups of functions:

  * ephemeral_*  - cross-worker short-lived values (scanner handoffs).
  * enqueue_* / drain_outbound - the outbound queue: work that talks to a third
    party or writes an unbounded number of rows, moved off the request path.
  * job lease + sweepers - the periodic maintenance that keeps the fast-growing
    tables from growing without limit.

Senders are registered by the application rather than imported from it, because
the SMTP and SMS implementations live alongside the credentials they read and
this module must not depend on Flask.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta

from scale import JobLease, bool_env, int_env, worker_identity
from models import db, EphemeralKV, JobLock, OutboundMessage

logger = logging.getLogger(__name__)

# Channel name -> callable(OutboundMessage) -> bool. Registered at app start.
_SENDERS = {}

# How long a claimed row may sit in 'sending' before another drain assumes the
# worker holding it died and puts it back in the queue.
STUCK_CLAIM_MINUTES = int_env('OUTBOUND_STUCK_CLAIM_MINUTES', 10)

# Rows per bulk insert when fanning notifications out. Large enough that the
# round trips stop mattering, small enough that one statement stays modest.
FANOUT_CHUNK = int_env('NOTIFICATION_FANOUT_CHUNK', 1000)

# Fan-outs at or below this go straight through in the request; anything larger
# is queued. A shop with a handful of followers should not wait for a worker
# cycle to notify them.
FANOUT_INLINE_LIMIT = int_env('NOTIFICATION_FANOUT_INLINE_LIMIT', 200)


def register_sender(channel, fn):
    """Attach the implementation that actually delivers ``channel``."""
    _SENDERS[channel] = fn


def sender_for(channel):
    return _SENDERS.get(channel)


# ---------------------------------------------------------------------------
# Ephemeral cross-worker values
# ---------------------------------------------------------------------------

DEFAULT_EPHEMERAL_TTL = int_env('EPHEMERAL_TTL_SECONDS', 900)


def ephemeral_set(key, value, ttl_seconds=None, commit=True):
    """Store a short-lived value visible to every worker.

    Upserts, so a till that scans twice in a row overwrites rather than
    accumulating. The expiry is absolute so the sweeper needs no knowledge of
    what wrote the row.
    """
    if not key:
        return None
    ttl = int(ttl_seconds or DEFAULT_EPHEMERAL_TTL)
    expires_at = datetime.utcnow() + timedelta(seconds=max(1, ttl))
    payload = value if isinstance(value, str) else json.dumps(value)
    row = EphemeralKV.query.filter_by(key=key).first()
    if row:
        row.value = payload
        row.expires_at = expires_at
    else:
        row = EphemeralKV(key=key, value=payload, expires_at=expires_at)
        db.session.add(row)
    if commit:
        db.session.commit()
    return row


def ephemeral_get(key, default=None):
    """Read a short-lived value, treating an expired row as absent.

    Expired rows are left for the sweeper rather than deleted here: a read
    should not turn into a write, because reads are the frequent operation.
    """
    if not key:
        return default
    row = EphemeralKV.query.filter_by(key=key).first()
    if not row or row.is_expired:
        return default
    return row.value


def ephemeral_get_json(key, default=None):
    raw = ephemeral_get(key)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def ephemeral_delete(key, commit=True):
    if not key:
        return 0
    removed = EphemeralKV.query.filter_by(key=key).delete(synchronize_session=False)
    if commit:
        db.session.commit()
    return removed


def sweep_ephemeral(limit=5000):
    """Delete expired handoff rows. Returns how many went."""
    cutoff = datetime.utcnow()
    ids = [
        row.id
        for row in EphemeralKV.query
        .filter(EphemeralKV.expires_at <= cutoff)
        .limit(limit)
        .all()
    ]
    if not ids:
        return 0
    EphemeralKV.query.filter(EphemeralKV.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    return len(ids)


# ---------------------------------------------------------------------------
# Outbound queue
# ---------------------------------------------------------------------------

def enqueue(channel, recipient=None, subject=None, body=None, payload=None,
            max_attempts=5, delay_seconds=0, commit=True):
    """Add a job to the outbound queue.

    Deliberately does not commit by default when called inside a larger unit of
    work: the caller decides. The queue row and whatever it describes should land
    together, so an order confirmation cannot be queued for an order that rolled
    back.
    """
    message = OutboundMessage(
        channel=channel,
        recipient=(recipient or '')[:240] or None,
        subject=(subject or '')[:300] or None,
        body=body,
        payload=json.dumps(payload) if payload is not None else None,
        status='queued',
        attempts=0,
        max_attempts=int(max_attempts),
        next_attempt_at=datetime.utcnow() + timedelta(seconds=max(0, int(delay_seconds))),
    )
    db.session.add(message)
    if commit:
        db.session.commit()
    return message


def enqueue_email(to_email, subject, body_html, commit=True):
    if not to_email:
        return None
    return enqueue('email', recipient=to_email, subject=subject, body=body_html, commit=commit)


def enqueue_sms(phone_number, message, commit=True):
    if not phone_number:
        return None
    return enqueue('sms', recipient=phone_number, body=message, commit=commit)


def enqueue_detached(channel, recipient=None, subject=None, body=None, payload=None,
                     max_attempts=5, delay_seconds=0):
    """Queue a job on its own connection, outside the caller's transaction.

    This is what ``send_email`` and ``send_sms_notification`` use, and the
    reasoning is worth stating because the obvious alternative is wrong. Queuing
    on the request's session means the row only exists if the caller commits, and
    plenty of callers never do - a "resend my link" handler reads and sends and
    commits nothing. Those messages would vanish silently, which is the worst
    failure mode available for a password reset.

    The cost is that a message queued inside a unit of work that later rolls back
    still goes out. A spurious email is recoverable; a receipt that was never sent
    and left no trace is not. Callers that genuinely need the message tied to the
    transaction use ``enqueue(..., commit=False)`` directly.
    """
    now = datetime.utcnow()
    values = {
        'channel': channel,
        'recipient': (recipient or '')[:240] or None,
        'subject': (subject or '')[:300] or None,
        'body': body,
        'payload': json.dumps(payload) if payload is not None else None,
        'status': 'queued',
        'attempts': 0,
        'max_attempts': int(max_attempts),
        'next_attempt_at': now + timedelta(seconds=max(0, int(delay_seconds))),
        'created_at': now,
    }
    with db.engine.begin() as connection:
        connection.execute(OutboundMessage.__table__.insert().values(**values))
    return True


def enqueue_many(channel, recipients, subject=None, body=None, subject_for=None,
                 body_for=None, max_attempts=5, delay_seconds=0, commit=True):
    """Queue one job per recipient in chunked bulk inserts.

    For digest jobs, where the recipient list is the whole followed audience and
    a per-recipient ``enqueue`` would be one INSERT and one flush each. Same
    reasoning as the notification fan-out: at a hundred thousand followers the
    loop is not slow, it is a job that never finishes.

    ``subject_for``/``body_for`` are optional callables taking the recipient, for
    when the message differs per person; otherwise the flat ``subject``/``body``
    is reused for everyone.

    Returns the number of rows queued.
    """
    now = datetime.utcnow()
    next_attempt_at = now + timedelta(seconds=max(0, int(delay_seconds)))
    rows = []
    for recipient in dict.fromkeys(recipients or ()):
        address = (recipient or '')[:240]
        if not address:
            continue
        rows.append({
            'channel': channel,
            'recipient': address,
            'subject': ((subject_for(recipient) if subject_for else subject) or '')[:300] or None,
            'body': body_for(recipient) if body_for else body,
            'payload': None,
            'status': 'queued',
            'attempts': 0,
            'max_attempts': int(max_attempts),
            'next_attempt_at': next_attempt_at,
            'created_at': now,
        })
    if not rows:
        return 0
    for start in range(0, len(rows), FANOUT_CHUNK):
        db.session.bulk_insert_mappings(OutboundMessage, rows[start:start + FANOUT_CHUNK])
    if commit:
        db.session.commit()
    return len(rows)


def recover_stuck_claims():
    """Return rows abandoned mid-send to the queue.

    A worker killed between claiming and sending would otherwise leave the row
    in 'sending' forever. Reclaimed rather than failed, because the send may not
    have happened at all.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=STUCK_CLAIM_MINUTES)
    stuck = OutboundMessage.query.filter(
        OutboundMessage.status == 'sending',
        OutboundMessage.claimed_at <= cutoff,
    ).limit(500).all()
    for message in stuck:
        message.status = 'queued'
        message.claimed_by = None
        message.claimed_at = None
        message.next_attempt_at = datetime.utcnow()
    if stuck:
        db.session.commit()
        logger.warning('Recovered %s stuck outbound message(s)', len(stuck))
    return len(stuck)


def claim_batch(limit=25):
    """Take the next due jobs and mark them in flight.

    Safe for every worker to call at once, which is the point: a queue only one
    process is allowed to drain has the throughput of one process, and at a
    million users the mail volume needs all of them.

    Two workers that pick the same candidate ids cannot both win. The claiming
    UPDATE re-checks ``status = 'queued'``, so the loser updates nothing, and the
    read-back is filtered on this call's own claim token rather than on the
    worker identity - a worker draining on two threads would otherwise pick up
    the other thread's rows.
    """
    now = datetime.utcnow()
    candidates = (
        db.session.query(OutboundMessage.id)
        .filter(OutboundMessage.status == 'queued', OutboundMessage.next_attempt_at <= now)
        .order_by(OutboundMessage.next_attempt_at.asc())
        .limit(limit)
    )
    # SKIP LOCKED is the right tool where it exists: concurrent drains walk past
    # each other's rows instead of contending. SQLite has one writer anyway.
    try:
        if db.session.get_bind().dialect.name == 'postgresql':
            candidates = candidates.with_for_update(skip_locked=True)
    except Exception:
        pass

    ids = [row[0] for row in candidates.all()]
    if not ids:
        db.session.rollback()
        return []

    token = f'{worker_identity()}/{uuid.uuid4().hex[:8]}'[:120]
    claimed = (
        OutboundMessage.query
        .filter(OutboundMessage.id.in_(ids), OutboundMessage.status == 'queued')
        .update({'status': 'sending', 'claimed_by': token, 'claimed_at': now},
                synchronize_session=False)
    )
    db.session.commit()
    if not claimed:
        return []
    return (
        OutboundMessage.query
        .filter(OutboundMessage.claimed_by == token, OutboundMessage.status == 'sending')
        .all()
    )


def _deliver(message):
    """Run one job through its sender. Returns True when it is done for good."""
    if message.channel == 'notification_fanout':
        return run_fanout(message)
    sender = _SENDERS.get(message.channel)
    if sender is None:
        # Nothing registered to handle it. Park rather than retry forever.
        message.status = 'dead'
        message.last_error = f'No sender registered for channel {message.channel}'
        logger.error('Outbound message %s has no sender for channel %s', message.id, message.channel)
        return True
    return bool(sender(message))


def drain_outbound(limit=25):
    """Send one batch. Returns (sent, failed, requeued)."""
    recover_stuck_claims()
    batch = claim_batch(limit)
    sent = failed = requeued = 0
    for message in batch:
        message.attempts = (message.attempts or 0) + 1
        try:
            ok = _deliver(message)
        except Exception as exc:
            db.session.rollback()
            # Re-read: the rollback detached whatever we had staged.
            message = db.session.get(OutboundMessage, message.id)
            if message is None:
                continue
            ok = False
            message.last_error = f'{type(exc).__name__}: {exc}'[:2000]
            logger.exception('Outbound message %s raised', message.id)

        if ok and message.status != 'dead':
            message.status = 'sent'
            message.sent_at = datetime.utcnow()
            message.last_error = None
            sent += 1
        elif message.status == 'dead':
            failed += 1
        elif (message.attempts or 0) >= (message.max_attempts or 5):
            message.status = 'failed'
            failed += 1
            logger.error(
                'Outbound %s to %s gave up after %s attempts: %s',
                message.channel, message.recipient, message.attempts, message.last_error,
            )
        else:
            message.status = 'queued'
            message.claimed_by = None
            message.claimed_at = None
            message.next_attempt_at = datetime.utcnow() + timedelta(seconds=message.backoff_seconds())
            requeued += 1
        db.session.commit()
    return sent, failed, requeued


def prune_outbound(days=None):
    """Drop delivered history. Failures are kept for investigation."""
    horizon = int(days or int_env('OUTBOUND_RETENTION_DAYS', 7))
    cutoff = datetime.utcnow() - timedelta(days=horizon)
    removed = (
        OutboundMessage.query
        .filter(OutboundMessage.status == 'sent', OutboundMessage.sent_at <= cutoff)
        .delete(synchronize_session=False)
    )
    if removed:
        db.session.commit()
    return removed


# ---------------------------------------------------------------------------
# Notification fan-out
# ---------------------------------------------------------------------------

def fanout_notifications(user_ids, title, body, notification_type='update', product_id=None,
                         commit=True):
    """Create one notification per user without a query per user.

    A per-user INSERT loop is fine for ten followers and fatal for a hundred
    thousand: the request outlives the worker timeout and dies partway. Small
    fan-outs run inline via a chunked bulk insert; anything larger is queued so
    the publishing request returns immediately either way.

    Returns the number of recipients the notification will reach.
    """
    from models import CustomerNotification

    targets = [int(uid) for uid in dict.fromkeys(user_ids) if uid]
    if not targets:
        return 0

    if len(targets) > FANOUT_INLINE_LIMIT:
        enqueue(
            'notification_fanout',
            subject=title,
            body=body,
            payload={
                'user_ids': targets,
                'title': title,
                'body': body,
                'notification_type': notification_type,
                'product_id': product_id,
            },
            commit=commit,
        )
        return len(targets)

    _bulk_insert_notifications(CustomerNotification, targets, title, body,
                              notification_type, product_id)
    if commit:
        db.session.commit()
    return len(targets)


def _bulk_insert_notifications(model, targets, title, body, notification_type, product_id):
    now = datetime.utcnow()
    rows = [
        {
            'user_id': user_id,
            'product_id': product_id,
            'title': (title or '')[:180],
            'body': body,
            'notification_type': notification_type,
            'is_read': False,
            'created_at': now,
        }
        for user_id in targets
    ]
    for start in range(0, len(rows), FANOUT_CHUNK):
        db.session.bulk_insert_mappings(model, rows[start:start + FANOUT_CHUNK])


def run_fanout(message):
    """Execute a queued fan-out. Chunked and committed per chunk.

    Committing per chunk means a fan-out interrupted halfway does not roll back
    the notifications already delivered; the duplicate-suppression on retry is
    the recipient set, which is fixed in the payload.
    """
    from models import CustomerNotification

    try:
        payload = json.loads(message.payload or '{}')
    except (TypeError, ValueError):
        message.status = 'dead'
        message.last_error = 'Fan-out payload is not valid JSON'
        return True

    targets = [int(uid) for uid in dict.fromkeys(payload.get('user_ids') or []) if uid]
    if not targets:
        return True

    title = payload.get('title') or message.subject or ''
    body = payload.get('body') or message.body or ''
    notification_type = payload.get('notification_type') or 'update'
    product_id = payload.get('product_id')

    # Resume support: skip recipients already written by an earlier attempt.
    already = set()
    if (message.attempts or 0) > 1:
        already = {
            row[0]
            for row in db.session.query(CustomerNotification.user_id)
            .filter(
                CustomerNotification.user_id.in_(targets),
                CustomerNotification.title == (title or '')[:180],
                CustomerNotification.notification_type == notification_type,
            )
            .all()
        }
    remaining = [uid for uid in targets if uid not in already]

    now = datetime.utcnow()
    for start in range(0, len(remaining), FANOUT_CHUNK):
        chunk = remaining[start:start + FANOUT_CHUNK]
        db.session.bulk_insert_mappings(CustomerNotification, [
            {
                'user_id': user_id,
                'product_id': product_id,
                'title': (title or '')[:180],
                'body': body,
                'notification_type': notification_type,
                'is_read': False,
                'created_at': now,
            }
            for user_id in chunk
        ])
        db.session.commit()
    logger.info('Fan-out %s delivered %s notification(s)', message.id, len(remaining))
    return True


# ---------------------------------------------------------------------------
# Job leases
# ---------------------------------------------------------------------------

DEFAULT_LEASE = JobLease(ttl_seconds=int_env('JOB_LEASE_TTL_SECONDS', 90))


def acquire_lease(name, lease=None):
    """Try to become the worker that runs ``name``.

    Returns True when this process holds the lease. Safe to call on every tick:
    the holder re-acquires cheaply, and everyone else gets False until the holder
    stops renewing.
    """
    lease = lease or DEFAULT_LEASE
    me = worker_identity()
    now = datetime.utcnow()
    try:
        row = JobLock.query.filter_by(name=name).first()
        if row is None:
            row = JobLock(name=name, holder=me, acquired_at=now,
                          expires_at=lease.next_expiry(now))
            db.session.add(row)
            db.session.commit()
            return True
        if not lease.is_takeable(row.holder, row.expires_at, now, me):
            return False
        previous_holder = row.holder
        if previous_holder and previous_holder != me:
            logger.info('Job lease %s taken over from %s by %s', name, previous_holder, me)
        # Only reset the acquired-at stamp on a genuine handover, so a renewing
        # holder keeps its original start time.
        if previous_holder != me or not row.acquired_at:
            row.acquired_at = now
        row.holder = me
        row.expires_at = lease.next_expiry(now)
        db.session.commit()
        return True
    except Exception:
        # A unique-constraint clash means another worker won the same race. Not
        # an error: it is the mechanism working.
        db.session.rollback()
        logger.debug('Could not acquire job lease %s', name, exc_info=True)
        return False


def release_lease(name, force=False):
    """Give up a lease.

    Only the holder may release by default, so a worker cannot unlock a job
    another worker is still running. ``force`` exists for the admin "it is
    stuck, clear it" path, where the operator is asserting the holder is gone.
    """
    try:
        row = JobLock.query.filter_by(name=name).first()
        if row and (force or row.holder == worker_identity()):
            row.holder = None
            row.expires_at = None
            db.session.commit()
    except Exception:
        db.session.rollback()


def mark_lease_run(name):
    try:
        row = JobLock.query.filter_by(name=name).first()
        if row:
            row.last_run_at = datetime.utcnow()
            db.session.commit()
    except Exception:
        db.session.rollback()


# ---------------------------------------------------------------------------
# Sweepers for the fast-growing tables
# ---------------------------------------------------------------------------

def prune_driver_pings(retention_days=None, chunk=5000, max_chunks=40):
    """Delete GPS breadcrumbs past the retention window.

    This table grows faster than everything else in the schema combined - one
    ping every few seconds per active driver - and nothing was removing it. At a
    few hundred drivers that is millions of rows a day, so the delete is chunked:
    a single unbounded DELETE over a table this size holds locks long enough to
    stall the writes still arriving.

    The most recent fix per driver is kept regardless of age, because the
    dispatch map reads it and a quiet driver should not vanish from the board.
    """
    from models import DriverLocationPing

    horizon = int(retention_days if retention_days is not None
                  else int_env('DRIVER_PING_RETENTION_DAYS', 14))
    if horizon <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=horizon)

    # The "newest per driver" set stays inside the database as a subquery. Pulling
    # it into Python and sending it back as an IN list would mean one bind
    # parameter per driver on every chunk, which is the same shape of problem this
    # function exists to avoid.
    newest_per_driver = (
        db.session.query(db.func.max(DriverLocationPing.id))
        .group_by(DriverLocationPing.driver_id)
        .scalar_subquery()
    )

    removed = 0
    for _ in range(max_chunks):
        ids = [
            row[0] for row in
            db.session.query(DriverLocationPing.id)
            .filter(DriverLocationPing.created_at <= cutoff,
                    DriverLocationPing.id.notin_(newest_per_driver))
            .order_by(DriverLocationPing.id.asc())
            .limit(chunk)
            .all()
        ]
        if not ids:
            break
        DriverLocationPing.query.filter(DriverLocationPing.id.in_(ids)).delete(
            synchronize_session=False)
        db.session.commit()
        removed += len(ids)
        if len(ids) < chunk:
            break
    if removed:
        logger.info('Pruned %s driver location ping(s) older than %s day(s)', removed, horizon)
    return removed


def prune_audit_logs(retention_days=None, chunk=5000, max_chunks=20):
    """Trim the audit trail to its retention window.

    Kept longer than anything else here by default, since this is the table an
    incident investigation reads, but not kept forever - it takes a row per admin
    action and security event and has no natural ceiling.
    """
    from models import AuditLog

    horizon = int(retention_days if retention_days is not None
                  else int_env('AUDIT_LOG_RETENTION_DAYS', 180))
    if horizon <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=horizon)
    removed = 0
    for _ in range(max_chunks):
        ids = [
            row.id for row in
            AuditLog.query.filter(AuditLog.timestamp <= cutoff)
            .order_by(AuditLog.id.asc()).limit(chunk).all()
        ]
        if not ids:
            break
        AuditLog.query.filter(AuditLog.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
        removed += len(ids)
        if len(ids) < chunk:
            break
    return removed


def prune_shipping_quotes(retention_days=None, chunk=5000, max_chunks=20):
    """Trim quote history.

    Quotes exist to answer "why was I charged this", which stops being asked
    after a couple of months. Unattached quotes (never became an order) go first
    and are the bulk of the table.
    """
    from models import ShippingQuote

    horizon = int(retention_days if retention_days is not None
                  else int_env('SHIPPING_QUOTE_RETENTION_DAYS', 90))
    if horizon <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=horizon)
    removed = 0
    for _ in range(max_chunks):
        ids = [
            row.id for row in
            ShippingQuote.query
            .filter(ShippingQuote.created_at <= cutoff, ShippingQuote.order_id.is_(None))
            .order_by(ShippingQuote.id.asc()).limit(chunk).all()
        ]
        if not ids:
            break
        ShippingQuote.query.filter(ShippingQuote.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
        removed += len(ids)
        if len(ids) < chunk:
            break
    return removed


def housekeeping():
    """One maintenance pass. Returns a dict of what it removed, for the log."""
    results = {}
    for label, fn in (
        ('ephemeral', sweep_ephemeral),
        ('outbound', prune_outbound),
        ('driver_pings', prune_driver_pings),
        ('shipping_quotes', prune_shipping_quotes),
        ('audit_logs', prune_audit_logs),
    ):
        try:
            results[label] = fn()
        except Exception:
            db.session.rollback()
            logger.exception('Housekeeping step %s failed', label)
            results[label] = None
    return results
