"""Smoke check for the coin and shopping-card ledgers under concurrency.

Run with: python tools/ledger_smoke.py

Balances here are read-then-write pairs, so the thing worth testing is not that a
single award works but that awards and spends landing at the same instant still
add up. This drives them from real threads on real connections rather than
simulating the interleave, so the locking is exercised the way production would
exercise it.

SQLite is not a free pass, which is the whole reason these checks exist: it
serialises the writes but not the reads that decided what to write, so before the
locking went in every one of these failed on the dev database - eight threads read
the same balance and wrote the same total. Everything created is removed at the
end, including on failure.
"""

import os
import sys
import threading

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
os.environ.setdefault('CACHE_TYPE', 'NullCache')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import (app, award_coins, create_shopping_card, credit_shopping_card, db,
                 get_user_coin_balance, redeem_shopping_card, shopping_card_issue_fee,
                 spend_coins)
from models import CoinTransaction, ShoppingCard, ShoppingCardTransaction, User

FAILURES = []
TAG = 'ledgersmoke'
THREADS = 8
PER_THREAD = 5
AWARD = 10


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + str(detail)) if detail else ""}')


def make_user(suffix):
    user = User(username=f'{TAG}_{suffix}', email=f'{TAG}_{suffix}@example.invalid',
                phone=f'+2547999{suffix:04d}')
    user.set_password('x')
    db.session.add(user)
    db.session.commit()
    return user


def make_card(user):
    """A card with a known PIN, paying the issue fee so the gate lets it through.

    customer_sets_pin=False keeps this off the SMS path - the True branch texts the
    customer a PIN setup link, which is a live send.
    """
    card, pin = create_shopping_card(
        user,
        issue_fee_paid=shopping_card_issue_fee(),
        issued_by=None,
        customer_sets_pin=False,
    )
    db.session.commit()
    return card, pin


def teardown():
    """Tag-driven, so a run that dies halfway still cleans up after itself."""
    db.session.rollback()
    user_ids = [row[0] for row in db.session.query(User.id)
                .filter(User.username.like(f'{TAG}%')).all()]
    card_ids = [row[0] for row in db.session.query(ShoppingCard.id)
                .filter(ShoppingCard.user_id.in_(user_ids or [0])).all()]
    steps = [
        (ShoppingCardTransaction, ShoppingCardTransaction.card_id.in_(card_ids or [0])),
        (ShoppingCard, ShoppingCard.id.in_(card_ids or [0])),
        (CoinTransaction, CoinTransaction.user_id.in_(user_ids or [0])),
        (User, User.username.like(f'{TAG}%')),
    ]
    for model, criteria in steps:
        try:
            model.query.filter(criteria).delete(synchronize_session=False)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            print(f'  cleanup of {model.__name__} failed: {exc}')


def hammer(worker):
    """Run ``worker`` on THREADS threads at once and collect what each returned.

    Each thread pushes its own app context, so it gets its own session and its own
    pool connection - a shared session would serialise them in Python and prove
    nothing about the database.
    """
    results = [None] * THREADS
    errors = []
    start = threading.Barrier(THREADS)

    def target(index):
        try:
            # Leaving the context tears the session down and returns the pool
            # connection, so there is nothing to clean up by hand out here.
            with app.app_context():
                start.wait(timeout=30)
                results[index] = worker(index)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            errors.append(f'thread {index}: {type(exc).__name__}: {exc}')

    threads = [threading.Thread(target=target, args=(i,)) for i in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results, errors


def run():
    print('concurrent awards all land')
    user = make_user(1)
    user_id = user.id

    def award_worker(_index):
        landed = 0
        for _ in range(PER_THREAD):
            if award_coins(user_id, AWARD, 'smoke', description='concurrent award'):
                db.session.commit()
                landed += 1
        return landed

    _, errors = hammer(award_worker)
    check('no thread errored', not errors, '; '.join(errors))
    expected = THREADS * PER_THREAD * AWARD
    rows = CoinTransaction.query.filter_by(user_id=user_id).count()
    check(f'every award wrote a row ({THREADS * PER_THREAD})',
          rows == THREADS * PER_THREAD, rows)
    balance = get_user_coin_balance(user_id)
    # The failure this catches: concurrent awards reading the same starting
    # balance and both writing the same total, so the ledger's newest row is
    # short even though every row is present.
    check(f'the newest row equals the sum of the awards ({expected})',
          balance == expected, balance)
    amounts = db.session.query(db.func.coalesce(db.func.sum(CoinTransaction.amount), 0)) \
        .filter(CoinTransaction.user_id == user_id).scalar()
    check('and it agrees with the sum of the amounts', balance == amounts,
          (balance, amounts))

    print('a spend cannot overdraw, however many race for it')
    # Exactly enough for half the attempts, so the other half must be refused.
    affordable = THREADS // 2
    spend_user = make_user(2)
    spend_user_id = spend_user.id
    award_coins(spend_user_id, affordable * AWARD, 'smoke', description='float')
    db.session.commit()

    def spend_worker(_index):
        txn = spend_coins(spend_user_id, AWARD, 'smoke', description='concurrent spend')
        if txn is None:
            db.session.rollback()
            return False
        db.session.commit()
        return True

    granted, errors = hammer(spend_worker)
    check('no thread errored', not errors, '; '.join(errors))
    check(f'only the affordable spends were granted ({affordable})',
          sum(1 for g in granted if g) == affordable, granted)
    final = get_user_coin_balance(spend_user_id)
    check('the balance landed exactly on zero, never below', final == 0, final)
    negatives = CoinTransaction.query.filter(
        CoinTransaction.user_id == spend_user_id,
        CoinTransaction.balance_after < 0).count()
    check('no row ever recorded a negative balance', negatives == 0, negatives)

    print('blocked coin types are still refused')
    blocked = spend_coins(spend_user_id, 1, 'raffle_ticket', description='should not pass')
    db.session.rollback()
    check('a raffle spend returns nothing', blocked is None, blocked)

    print('concurrent card credits all land')
    card_user = make_user(3)
    card_user_id = card_user.id
    card, _pin = make_card(card_user)
    card_id = card.id

    def credit_worker(_index):
        landed = 0
        for _ in range(PER_THREAD):
            if credit_shopping_card(card_user_id, credits=100, note='concurrent credit'):
                db.session.commit()
                landed += 1
        return landed

    _, errors = hammer(credit_worker)
    check('no thread errored', not errors, '; '.join(errors))
    db.session.expire_all()
    card = db.session.get(ShoppingCard, card_id)
    expected_credits = THREADS * PER_THREAD * 100
    check(f'the card holds every credit ({expected_credits})',
          int(card.credit_balance or 0) == expected_credits, card.credit_balance)

    print('a card cannot be redeemed past its balance')
    redeem_user = make_user(4)
    redeem_user_id = redeem_user.id
    redeem_card, pin = make_card(redeem_user)
    redeem_card_id = redeem_card.id
    number = redeem_card.card_number
    # Credits are cents, so this is exactly `affordable` swipes of KSh 1.00.
    credit_shopping_card(redeem_user_id, credits=affordable * 100, note='float')
    db.session.commit()

    def redeem_worker(_index):
        try:
            redeem_shopping_card(number, pin, 1.00, reference_type='smoke')
        except ValueError:
            db.session.rollback()
            return False
        db.session.commit()
        return True

    swiped, errors = hammer(redeem_worker)
    check('no thread errored', not errors, '; '.join(errors))
    check(f'only the covered swipes went through ({affordable})',
          sum(1 for s in swiped if s) == affordable, swiped)
    db.session.expire_all()
    redeem_card = db.session.get(ShoppingCard, redeem_card_id)
    check('the card emptied to zero and no further',
          int(redeem_card.credit_balance or 0) == 0
          and float(redeem_card.cash_balance or 0) == 0.0,
          (redeem_card.credit_balance, redeem_card.cash_balance))

    print('the newest ledger row is not a coin flip on a shared timestamp')
    tie_user = make_user(5)
    # Both rows get the same created_at because they are written together, which is
    # what the id tiebreak in get_user_coin_balance is there for.
    award_coins(tie_user.id, 10, 'smoke', description='first')
    db.session.commit()
    award_coins(tie_user.id, 10, 'smoke', description='second')
    db.session.commit()
    stamps = {row[0] for row in db.session.query(CoinTransaction.created_at)
              .filter(CoinTransaction.user_id == tie_user.id).all()}
    check('reads the later row even when the timestamps tie',
          get_user_coin_balance(tie_user.id) == 20,
          f'{get_user_coin_balance(tie_user.id)} (distinct timestamps: {len(stamps)})')


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
