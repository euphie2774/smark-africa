"""Smoke check for the invoice system: arithmetic, the document, and who may bill.

Run with: python tools/invoice_smoke.py

An invoice is a demand for money sent to somebody's inbox under the company name,
so the things worth asserting are the things that would be expensive to get wrong:

  * the totals. Discount clamped to the subtotal so a fat-fingered figure cannot
    produce a negative demand, and tax charged on what is left after the discount
    rather than on the gross
  * the numbering. SMK-INV-, not the POS INV- prefix, and never the same number
    twice - invoice_number is unique, so a collision is an IntegrityError in front
    of an admin mid-form
  * the document. The SMARK-AFRICA watermark and the logo, in the one renderer the
    hosted page, the print view and the admin preview all share
  * the emailed request. Logo, balance, and a button that opens the watermarked
    copy - mail clients drop the layered watermark, so the email links to the
    document rather than pretending to be it
  * the token. Unknown, draft and cancelled all 404 alike, because a different
    answer for each tells an address-guesser which tokens are real
  * money arriving twice. Safaricom retries its callback, and the second copy must
    not book the same shilling again
  * who may issue. The MVP, and any admin explicitly nominated - not every admin,
    because billing a client is a different trust from moderating a listing

No outbound mail is touched: send_invoice_email is left alone and the email body is
asserted as a pure function, so running this cannot post a payment demand to
anybody. Printed output is ASCII only, and values are escaped on the way out, so a
cp1252 console cannot turn a passing check into a UnicodeEncodeError.
"""

import contextlib
import os
import sys
from datetime import date, timedelta

os.environ.setdefault('DISABLE_BACKGROUND_JOBS', '1')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app_module
from main import (INVOICE_MAX_LINES, app, db, generate_invoice_token,
                  invoice_can_issue, invoice_document_html, invoice_email_html,
                  invoice_money, invoice_quantity, next_invoice_number,
                  recalculate_invoice, refresh_invoice_payment_state,
                  settle_invoice_stk, utcnow)
from models import (CustomerNotification, Invoice, InvoiceItem, InvoicePayment,
                    Setting, User)

app.config['WTF_CSRF_ENABLED'] = False

FAILURES = []
TAG = 'invsmoke'
CLIENT_EMAIL = f'{TAG}_client@example.invalid'


def ascii_safe(value):
    return str(value).encode('unicode_escape').decode('ascii')


def check(label, condition, detail=''):
    status = 'ok  ' if condition else 'FAIL'
    if not condition:
        FAILURES.append(label)
    print(f'  [{status}] {label}{(" - " + ascii_safe(detail)) if detail else ""}')


@contextlib.contextmanager
def as_user(user_id):
    """One app context per identity: Flask-Login caches the user on ``g``."""
    ctx = app.app_context()
    ctx.push()
    try:
        with app.test_client() as client:
            with client.session_transaction() as http_session:
                http_session['_user_id'] = str(user_id)
                http_session['_fresh'] = True
            yield client
    finally:
        db.session.remove()
        ctx.pop()


@contextlib.contextmanager
def as_anonymous():
    ctx = app.app_context()
    ctx.push()
    try:
        with app.test_client() as client:
            yield client
    finally:
        db.session.remove()
        ctx.pop()


@contextlib.contextmanager
def rate_limits_off():
    """The desk is capped at sixty an hour, which caps the test, not the code.

    With REDIS_URL set the window outlives the process, so a second run inside the
    hour would start reading 429s as failures.
    """
    limiter = getattr(app_module, 'limiter', None)
    was_enabled = getattr(limiter, 'enabled', None)
    if limiter is not None:
        limiter.enabled = False
    try:
        yield
    finally:
        if limiter is not None and was_enabled is not None:
            limiter.enabled = was_enabled


@contextlib.contextmanager
def settings(**values):
    """Pin Setting rows and restore exactly what was there.

    "Absent" and "present and equal to the default" are different states, so the
    raw row is captured rather than Setting.get.
    """
    saved = {}
    for key in values:
        row = Setting.query.filter_by(key=key).first()
        saved[key] = row.value if row else None
    for key, value in values.items():
        Setting.set(key, value)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                Setting.delete(key)
            else:
                Setting.set(key, value)


def teardown():
    db.session.rollback()
    user_ids = [row[0] for row in db.session.query(User.id)
                .filter(User.username.like(f'{TAG}%')).all()] or [0]
    invoice_ids = [row[0] for row in db.session.query(Invoice.id).filter(
        db.or_(Invoice.issued_by_id.in_(user_ids),
               Invoice.client_email == CLIENT_EMAIL)).all()] or [0]
    # Children first. SQLite would let a dangling row through and PostgreSQL would
    # not, so a teardown that only works on one of them fails on deploy.
    InvoicePayment.query.filter(
        InvoicePayment.invoice_id.in_(invoice_ids)).delete(synchronize_session=False)
    InvoiceItem.query.filter(
        InvoiceItem.invoice_id.in_(invoice_ids)).delete(synchronize_session=False)
    Invoice.query.filter(Invoice.id.in_(invoice_ids)).delete(synchronize_session=False)
    CustomerNotification.query.filter(
        CustomerNotification.user_id.in_(user_ids)).delete(synchronize_session=False)
    User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)
    db.session.commit()


def make_user(suffix, **fields):
    user = User(username=f'{TAG}_{suffix}', email=f'{TAG}_{suffix}@example.invalid',
                is_active=True, **fields)
    user.set_password('x')
    db.session.add(user)
    db.session.commit()
    return user


def make_invoice(issuer_id, lines, status='draft', commit=True, **fields):
    # Defaults, not fixed values: check_document builds an invoice whose client name
    # is markup, to prove the document escapes it. Passing them as keywords as well
    # as splatting **fields would collide on exactly that call.
    fields.setdefault('client_name', f'{TAG} Client')
    fields.setdefault('client_email', CLIENT_EMAIL)
    fields.setdefault('currency', 'KES')
    invoice = Invoice(invoice_number=next_invoice_number(),
                      public_token=generate_invoice_token(),
                      issued_by_id=issuer_id, status=status, **fields)
    invoice.items = [InvoiceItem(description=text, quantity=quantity,
                                 unit_price=price, sort_order=index)
                     for index, (text, quantity, price) in enumerate(lines)]
    recalculate_invoice(invoice)
    if commit:
        db.session.add(invoice)
        db.session.commit()
    return invoice


def form_lines(*lines):
    """The shared invoice form posts three parallel lists, one entry per line."""
    return {'line_description': [text for text, _, _ in lines],
            'line_quantity': [str(quantity) for _, quantity, _ in lines],
            'line_price': [str(price) for _, _, price in lines]}


# --- the checks ------------------------------------------------------------

def check_arithmetic(issuer_id):
    print('the totals: discount clamped, tax on what is left')
    invoice = make_invoice(issuer_id, [('Consultancy', 2, 500.0)],
                           commit=False, discount_amount=200.0, tax_percent=16.0)
    check('the subtotal is quantity times price, per line',
          invoice.subtotal == 1000.0, invoice.subtotal)
    check('the line total is stored, not recomputed on read',
          invoice.items[0].line_total == 1000.0, invoice.items[0].line_total)
    check('tax is charged on the discounted figure, not the gross',
          invoice.tax_amount == 128.0, invoice.tax_amount)
    check('and the total is taxable plus tax',
          invoice.total_amount == 928.0, invoice.total_amount)

    invoice.discount_amount = 5000.0
    recalculate_invoice(invoice)
    check('a discount larger than the bill is clamped to the bill',
          invoice.discount_amount == 1000.0, invoice.discount_amount)
    check('so the total floors at zero instead of going negative',
          invoice.total_amount == 0.0, invoice.total_amount)

    invoice.discount_amount = -50.0
    recalculate_invoice(invoice)
    check('a negative discount is floored at zero, not treated as a surcharge',
          invoice.discount_amount == 0.0 and invoice.total_amount == 1160.0,
          invoice.total_amount)

    fractional = make_invoice(issuer_id, [('Design hours', 1.5, 200.50)],
                              commit=False)
    check('a fractional quantity is priced exactly',
          fractional.items[0].line_total == 300.75, fractional.items[0].line_total)
    check('a whole quantity prints without decimals',
          invoice_quantity(3) == '3', invoice_quantity(3))
    check('and a fractional one keeps them',
          invoice_quantity(1.5) == '1.50', invoice_quantity(1.5))
    check('money always prints to two places with the currency',
          invoice_money(1234.5, 'KES') == 'KES 1,234.50',
          invoice_money(1234.5, 'KES'))

    reordered = make_invoice(issuer_id, [('Second', 1, 10.0), ('First', 1, 20.0)],
                             commit=False)
    check('sort_order is restated from position on every edit',
          [item.sort_order for item in reordered.items] == [0, 1],
          [item.sort_order for item in reordered.items])


def check_numbering(issuer_id):
    print('the invoice number')
    first = make_invoice(issuer_id, [('One', 1, 100.0)])
    second = make_invoice(issuer_id, [('Two', 1, 100.0)])
    check('the prefix is the invoice series, not the POS one',
          first.invoice_number.startswith('SMK-INV-')
          and not first.invoice_number.startswith('INV-'), first.invoice_number)
    check('it carries the date it was raised',
          utcnow().strftime('%Y%m%d') in first.invoice_number, first.invoice_number)
    check('two invoices in a row do not collide',
          first.invoice_number != second.invoice_number,
          f'{first.invoice_number} / {second.invoice_number}')
    check('the next number probes past what is already used',
          next_invoice_number() not in (first.invoice_number,
                                        second.invoice_number),
          next_invoice_number())
    check('and the public token is unguessable and unique',
          len(first.public_token) >= 20
          and first.public_token != second.public_token, len(first.public_token))
    return first


def check_document(issuer_id):
    print('the document carries the watermark and the logo')
    invoice = make_invoice(issuer_id, [('Branding work', 2, 7500.0)],
                           status='sent', discount_amount=500.0, tax_percent=16.0,
                           title=f'{TAG} Brand refresh',
                           notes='Half up front.', terms='Payable in 7 days.')
    html_doc = invoice_document_html(invoice)
    check('the watermark layer is present',
          'class="inv-watermark"' in html_doc)
    check('it repeats the company name sixteen times',
          html_doc.count('<span>SMARK-AFRICA</span>') == 16,
          html_doc.count('<span>SMARK-AFRICA</span>'))
    check('with the logo set into the middle of it',
          'smark-africa-logo.png' in html_doc.split('class="inv-sheet"')[0])
    check('the logo is also in the header',
          html_doc.split('class="inv-sheet"')[1].count('smark-africa-logo.png') >= 1)
    check('the watermark is hidden from screen readers, being decoration',
          'aria-hidden="true"' in html_doc.split('class="inv-sheet"')[0])
    check('the print rules force the watermark to actually print',
          'print-color-adjust:exact' in html_doc)
    check('the number is on the face of it', invoice.invoice_number in html_doc)
    check('so is the client', f'{TAG} Client' in html_doc)
    check('the discount is shown rather than folded into the subtotal',
          'Discount' in html_doc and 'KES -500.00' in html_doc)
    check('the tax line names the rate it was charged at',
          'Tax (16%)' in html_doc, 'Tax (16%)')
    check('the total is stated', invoice_money(invoice.total_amount) in html_doc,
          invoice_money(invoice.total_amount))
    check('an unpaid invoice shows a balance due', 'Balance due' in html_doc)
    check('the notes and terms both appear',
          'Half up front.' in html_doc and 'Payable in 7 days.' in html_doc)

    naked = invoice_document_html(invoice, include_css=False)
    check('embedding a second copy drops the stylesheet',
          '<style>' not in naked)
    check('but never the watermark',
          naked.count('<span>SMARK-AFRICA</span>') == 16)

    injected = make_invoice(issuer_id, [('<script>alert(1)</script>', 1, 10.0)],
                            client_name='<b>Bad</b>', commit=False)
    escaped = invoice_document_html(injected)
    check('a line description is escaped, not rendered',
          '<script>alert(1)</script>' not in escaped
          and '&lt;script&gt;' in escaped)
    check('and so is the client name',
          '<b>Bad</b>' not in escaped and '&lt;b&gt;Bad&lt;/b&gt;' in escaped)

    empty = Invoice(invoice_number='SMK-INV-EMPTY', public_token='x' * 24,
                    issued_by_id=issuer_id, client_name=f'{TAG} Client',
                    client_email=CLIENT_EMAIL, status='draft')
    check('an invoice with no lines renders instead of raising',
          'No lines on this invoice yet.' in invoice_document_html(empty))


def check_email_body(issuer_id):
    print('the emailed payment request')
    invoice = make_invoice(issuer_id, [('Site build', 1, 42000.0)], status='sent',
                           title=f'{TAG} Website', due_date=date(2026, 9, 30))
    body = invoice_email_html(invoice)
    check('the logo survives into the mail body',
          'smark-africa-logo.png' in body)
    check('the number is in it', invoice.invoice_number in body)
    check('the amount due is the balance, not the gross',
          invoice_money(invoice.balance_due) in body,
          invoice_money(invoice.balance_due))
    check('the due date is spelled out', '30 Sep 2026' in body)
    check('there is one button, and it opens the invoice',
          'View &amp; pay this invoice' in body
          and invoice.public_token in body)
    check('the line is itemised so the client can see what for',
          'Site build' in body)
    # The honest limitation, pinned so nobody "fixes" it by pasting the hosted
    # document into the mail body and assuming it survived: mail clients strip
    # position, so the watermark cannot travel. The button goes to the copy that
    # has it.
    check('the mail body does not pretend to carry the watermark layer',
          'class="inv-watermark"' not in body)
    check('and uses inline styles a mail client will keep',
          'style="background:#0b6b3a' in body)

    invoice.amount_paid = 12000.0
    db.session.commit()
    part_paid = invoice_email_html(invoice)
    check('a part-paid invoice shows what was already received',
          'Already paid' in part_paid and '-KES 12,000.00' in part_paid)
    check('and asks only for the remainder',
          invoice_money(30000.0) in part_paid, invoice_money(invoice.balance_due))

    reminder = invoice_email_html(invoice, reminder=True)
    check('a reminder is worded as one',
          'still open' in reminder and 'has issued you' not in reminder)


def check_token_and_public_page(issuer_id):
    print('the public page, and what a guessed token gets')
    draft = make_invoice(issuer_id, [('Draft work', 1, 500.0)], status='draft')
    cancelled = make_invoice(issuer_id, [('Called off', 1, 500.0)],
                             status='cancelled', cancelled_at=utcnow())
    sent = make_invoice(issuer_id, [('Delivered work', 1, 2500.0)], status='sent',
                        issued_at=utcnow(), sent_at=utcnow())
    draft_token, cancelled_token = draft.public_token, cancelled.public_token
    sent_token, sent_id = sent.public_token, sent.id

    with rate_limits_off(), as_anonymous() as client:
        unknown = client.get('/invoice/nosuchtoken000000000000')
        check('an unknown token is a 404', unknown.status_code == 404,
              unknown.status_code)
        check('a draft is a 404 too - the client has not been told about it yet',
              client.get(f'/invoice/{draft_token}').status_code == 404)
        check('and a cancelled one gives the same answer, not a different hint',
              client.get(f'/invoice/{cancelled_token}').status_code == 404)

        response = client.get(f'/invoice/{sent_token}')
        check('a sent invoice opens without an account',
              response.status_code == 200, response.status_code)
        check('and the page carries the watermarked document',
              b'inv-watermark' in response.data
              and response.data.count(b'<span>SMARK-AFRICA</span>') == 16)
        check('with a way to ask a question about it',
              b'wa.me/254' in response.data)

        printable = client.get(f'/invoice/{sent_token}/print')
        check('the print view renders the same document',
              printable.status_code == 200
              and printable.data.count(b'<span>SMARK-AFRICA</span>') == 16,
              printable.status_code)

    # The view committed in the request's own session, not this one. db.session.get
    # would answer from this session's identity map without emitting any SQL, so
    # without expiring first these two checks read the status as it was before the
    # page was ever opened - and both would pass whatever the route did.
    db.session.expire_all()
    row = db.session.get(Invoice, sent_id)
    check('the first open is recorded, so "never opened it" is knowable',
          row.status == 'viewed' and row.viewed_at is not None, row.status)
    first_seen = row.viewed_at
    with rate_limits_off(), as_anonymous() as client:
        client.get(f'/invoice/{sent_token}')
    db.session.expire_all()
    row = db.session.get(Invoice, sent_id)
    check('refreshing does not rewrite the timestamp, or the write is per refresh',
          row.viewed_at == first_seen, row.viewed_at)

    check('a draft is not payable', not draft.is_payable, draft.status)
    check('nor is a cancelled one', not cancelled.is_payable, cancelled.status)
    check('a sent one is', row.is_payable, row.status)
    settled = make_invoice(issuer_id, [('Paid up', 1, 800.0)], status='sent',
                           amount_paid=800.0)
    check('a settled one is not, so the pay button cannot double-charge',
          settled.is_settled and not settled.is_payable, settled.balance_due)
    with rate_limits_off(), as_anonymous() as client:
        attempt = client.post(f'/invoice/{settled.public_token}/pay',
                              data={'phone': '0712345678'})
        check('and the pay endpoint refuses it rather than pushing an STK',
              attempt.status_code == 400, attempt.status_code)
        payload = attempt.get_json() or {}
        check('with a reason the client can read',
              'settled' in (payload.get('error') or '').lower(),
              payload.get('error'))

    overdue = make_invoice(issuer_id, [('Late work', 1, 300.0)], status='sent',
                           due_date=date.today() - timedelta(days=2))
    check('overdue is derived from the date, not waiting on a nightly job',
          overdue.is_overdue and overdue.status_display == 'overdue',
          overdue.status_display)
    check('a settled invoice past its date is not overdue',
          not settled.is_overdue)


def check_duplicate_callback(issuer_id, client_user_id):
    print('money arriving twice')
    invoice = make_invoice(issuer_id, [('Fit-out', 1, 1000.0)], status='sent',
                           client_id=client_user_id, sent_at=utcnow())
    checkout_id = f'{TAG}-CHECKOUT-1'
    db.session.add(InvoicePayment(invoice_id=invoice.id, amount=1000.0,
                                  method='mpesa', status='pending',
                                  checkout_request_id=checkout_id,
                                  phone='0712345678'))
    db.session.commit()

    handled = settle_invoice_stk(checkout_id, 0, 'QAB1234XYZ', 1000.0)
    db.session.commit()
    check('the callback is recognised as an invoice payment', handled is True)
    check('the invoice is marked paid', invoice.status == 'paid', invoice.status)
    check('for the full amount', invoice.amount_paid == 1000.0, invoice.amount_paid)
    check('with nothing outstanding', invoice.balance_due == 0.0,
          invoice.balance_due)
    check('and the receipt kept for reconciliation',
          invoice.mpesa_receipt == 'QAB1234XYZ', invoice.mpesa_receipt)

    handled_again = settle_invoice_stk(checkout_id, 0, 'QAB1234XYZ', 1000.0)
    db.session.commit()
    check('a retried callback is still claimed, so the caller stops looking',
          handled_again is True)
    check('but the money is not booked twice',
          invoice.amount_paid == 1000.0, invoice.amount_paid)
    check('and only one successful payment row exists',
          len([row for row in invoice.payments if row.status == 'success']) == 1,
          len(invoice.payments))

    partial = make_invoice(issuer_id, [('Phase one', 1, 1000.0)], status='sent',
                           sent_at=utcnow())
    part_id = f'{TAG}-CHECKOUT-2'
    db.session.add(InvoicePayment(invoice_id=partial.id, amount=400.0,
                                  method='mpesa', status='pending',
                                  checkout_request_id=part_id))
    db.session.commit()
    settle_invoice_stk(part_id, 0, 'QAB999PART', 400.0)
    db.session.commit()
    check('a part payment reads as partially paid, not paid',
          partial.status == 'partially_paid', partial.status)
    check('with the remainder still due', partial.balance_due == 600.0,
          partial.balance_due)
    check('and no paid_at, which would read as settled in a report',
          partial.paid_at is None)

    failed_id = f'{TAG}-CHECKOUT-3'
    db.session.add(InvoicePayment(invoice_id=partial.id, amount=600.0,
                                  method='mpesa', status='pending',
                                  checkout_request_id=failed_id))
    db.session.commit()
    settle_invoice_stk(failed_id, 1032, '', 600.0, 'Request cancelled by user')
    db.session.commit()
    check('a cancelled push is logged as failed, not silently dropped',
          any(row.status == 'failed' for row in partial.payments))
    check('and does not move the balance', partial.amount_paid == 400.0,
          partial.amount_paid)

    # A reversal: the money is taken back out of the log.
    for row in partial.payments:
        if row.status == 'success':
            row.status = 'failed'
    refresh_invoice_payment_state(partial)
    db.session.commit()
    check('reversing the payment falls back to sent, not to draft',
          partial.status == 'sent' and partial.amount_paid == 0.0, partial.status)

    cancelled = make_invoice(issuer_id, [('Called off', 1, 500.0)],
                            status='cancelled')
    db.session.add(InvoicePayment(invoice_id=cancelled.id, amount=500.0,
                                  method='cash', status='success'))
    db.session.commit()
    refresh_invoice_payment_state(cancelled)
    db.session.commit()
    check('money against a cancelled invoice does not quietly reopen it',
          cancelled.status == 'cancelled', cancelled.status)
    check('though the amount is still recorded for a human to reconcile',
          cancelled.amount_paid == 500.0, cancelled.amount_paid)

    unknown = settle_invoice_stk('not-an-invoice-checkout', 0, 'X', 10.0)
    check("another feature's callback is declined, cheaply", unknown is False)
    check('and an empty id is not treated as a match',
          settle_invoice_stk('', 0, 'X', 10.0) is False)


def check_who_may_bill(mvp_id, agent_id, plain_admin_id, plain_user_id):
    print('who may raise and send an invoice')
    mvp = db.session.get(User, mvp_id)
    agent = db.session.get(User, agent_id)
    plain_admin = db.session.get(User, plain_admin_id)
    plain_user = db.session.get(User, plain_user_id)
    check('the MVP always may', invoice_can_issue(mvp) is True)
    check('a nominated admin may', invoice_can_issue(agent) is True)
    check('an ordinary admin may not - it is not part of being an admin',
          invoice_can_issue(plain_admin) is False)
    check('a non-admin may not, even flagged as an agent',
          invoice_can_issue(plain_user) is False)
    check('and neither may nobody at all', invoice_can_issue(None) is False)

    with rate_limits_off():
        with as_anonymous() as client:
            response = client.get('/admin/invoices')
            check('the desk sends an anonymous visitor to sign in',
                  response.status_code == 302
                  and 'login' in (response.headers.get('Location') or ''),
                  response.headers.get('Location'))
        with as_user(plain_admin_id) as client:
            response = client.get('/admin/invoices')
            check('an unnominated admin is redirected, not shown the book',
                  response.status_code == 302, response.status_code)
        with as_user(plain_user_id) as client:
            response = client.get('/admin/invoices')
            check('so is a buyer', response.status_code == 302,
                  response.status_code)
        with as_user(agent_id) as client:
            response = client.get('/admin/invoices')
            check('a nominated admin gets the desk', response.status_code == 200,
                  response.status_code)
            check('and the new-invoice form renders',
                  client.get('/admin/invoices/new').status_code == 200)


def check_form(agent_id):
    print('raising one through the form')
    with rate_limits_off(), settings(invoice_tax_percent='0',
                                     invoice_default_due_days='7'):
        with as_user(agent_id) as client:
            data = {'client_name': f'{TAG} Form Client',
                    'client_email': CLIENT_EMAIL,
                    'client_phone': '0712345678',
                    'title': f'{TAG} Form Invoice',
                    'discount_amount': '100', 'tax_percent': '16'}
            data.update(form_lines(('Consultancy', 2, 500.0), ('Travel', 1, 300.0)))
            response = client.post('/admin/invoices/new', data=data)
            check('a filled form redirects to the invoice it made',
                  response.status_code == 302, response.status_code)

            blank = dict(data)
            blank['client_name'] = ''
            check('a nameless client is refused with the form back',
                  client.post('/admin/invoices/new', data=blank).status_code == 200)

            bad_email = dict(data)
            bad_email['client_email'] = 'not-an-address'
            check('so is an unusable email - the request is sent there',
                  client.post('/admin/invoices/new',
                              data=bad_email).status_code == 200)

            no_lines = {key: value for key, value in data.items()
                        if not key.startswith('line_')}
            check('an invoice with no lines is refused',
                  client.post('/admin/invoices/new',
                              data=no_lines).status_code == 200)

            free = dict(data)
            free.update(form_lines(('Goodwill', 1, 0.0)))
            free['discount_amount'] = '0'
            check('and one totalling zero is refused, there being nothing to pay',
                  client.post('/admin/invoices/new', data=free).status_code == 200)

            bad_date = dict(data)
            bad_date['due_date'] = '30-09-2026'
            check('a misformatted due date is refused rather than guessed',
                  client.post('/admin/invoices/new',
                              data=bad_date).status_code == 200)

            too_many = dict(data)
            count = INVOICE_MAX_LINES + 1
            too_many.update(form_lines(*[(f'Line {index}', 1, 10.0)
                                         for index in range(count)]))
            check('and a form past the line cap is refused, not truncated',
                  client.post('/admin/invoices/new',
                              data=too_many).status_code == 200)

    made = Invoice.query.filter_by(title=f'{TAG} Form Invoice').first()
    check('exactly one invoice came out of that',
          Invoice.query.filter_by(title=f'{TAG} Form Invoice').count() == 1)
    check('it starts as a draft, so nothing is emailed by saving',
          made is not None and made.status == 'draft', made and made.status)
    check('the lines were kept in order',
          made is not None and [item.description for item in made.items]
          == ['Consultancy', 'Travel'])
    check('the total is subtotal less discount plus tax on the remainder',
          made is not None and made.total_amount == 1392.0,
          made and made.total_amount)
    check('a due date was filled in from the default',
          made is not None and made.due_date is not None, made and made.due_date)
    check('nothing was emailed', (made.email_sent_count or 0) == 0,
          made and made.email_sent_count)
    check('and the rejected attempts left no rows behind',
          Invoice.query.filter_by(client_name=f'{TAG} Form Client').count() == 1)


def run():
    mvp = make_user('mvp', is_admin=True, admin_level='mvp')
    agent = make_user('agent', is_admin=True, admin_level='admin',
                      invoice_agent=True)
    plain_admin = make_user('plainadmin', is_admin=True, admin_level='admin')
    # invoice_agent on a non-admin: the flag alone must not be a route in.
    plain_user = make_user('buyer', invoice_agent=True)
    mvp_id, agent_id = mvp.id, agent.id
    plain_admin_id, plain_user_id = plain_admin.id, plain_user.id

    check_arithmetic(mvp_id)
    check_numbering(mvp_id)
    check_document(mvp_id)
    check_email_body(mvp_id)
    check_token_and_public_page(mvp_id)
    check_duplicate_callback(mvp_id, plain_user_id)
    check_who_may_bill(mvp_id, agent_id, plain_admin_id, plain_user_id)
    check_form(agent_id)


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
