"""Paid admission verification and auditable service completion / quote actions."""
import secrets
import hashlib
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO

from flask import abort, current_app, flash, jsonify, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from itsdangerous import URLSafeSerializer, BadSignature
from sqlalchemy.exc import IntegrityError

from models import db, ServiceListing, ServiceOrder, ServicePriceTier, TicketAdmission, TicketOrderAction, ServiceLinkRequest, ServiceLinkMessage, User


def ticket_limit_exceeded(service, tier, buyer_id, quantity, exclude_order_id=None):
    orders = ServiceOrder.query.filter(ServiceOrder.service_id == service.id,
        ServiceOrder.client_id == buyer_id, ServiceOrder.payment_status == 'paid',
        ServiceOrder.status.notin_(['cancelled', 'refunded', 'oversold_refund_due', 'payment_failed']))
    if exclude_order_id:
        orders = orders.filter(ServiceOrder.id != exclude_order_id)
    total = lambda query: query.with_entities(db.func.coalesce(db.func.sum(ServiceOrder.quantity), 0)).scalar()
    if service.ticket_buyer_limit and total(orders) + quantity > service.ticket_buyer_limit:
        return f'This event allows at most {service.ticket_buyer_limit} tickets per buyer across all tiers.'
    if tier.max_per_order and total(orders.filter(ServiceOrder.tier_id == tier.id)) + quantity > tier.max_per_order:
        return f'{tier.name} allows at most {tier.max_per_order} tickets per buyer across purchases.'
    return ''


def assign_legacy_seats(tier):
    """Called under a tier write lock; preserve ranges already issued, including revoked ones."""
    if tier.is_unlimited:
        return
    end = db.session.query(db.func.max(ServiceOrder.ticket_seat_start + ServiceOrder.quantity - 1)).filter(
        ServiceOrder.tier_id == tier.id).scalar() or 0
    for old in ServiceOrder.query.filter(ServiceOrder.tier_id == tier.id,
            ServiceOrder.ticket_code.isnot(None), ServiceOrder.ticket_seat_start.is_(None)).order_by(ServiceOrder.id):
        old.ticket_seat_start = end + 1
        end += max(1, old.quantity or 1)
    tier.quantity_sold = max(tier.quantity_sold or 0, end)
    db.session.flush()


def admission_state(admission):
    order = admission.order
    if order.status in ('cancelled', 'refunded', 'oversold_refund_due', 'payment_failed'):
        return {'oversold_refund_due': 'Refund required', 'payment_failed': 'Payment failed'}.get(order.status, order.status.title())
    if not order.is_paid:
        return 'Unpaid'
    if order.service.ticket_review_status != 'approved':
        return 'Event not approved'
    if admission.checked_in_at:
        return 'Used'
    return 'Valid'


def issue_admissions(order):
    if not order.is_paid or order.status in ('oversold_refund_due', 'refunded', 'cancelled'):
        return
    if order.tier and not order.tier.is_unlimited and order.ticket_seat_start is None:
        ServicePriceTier.query.filter_by(id=order.tier_id).update(
            {'quantity_sold': db.func.coalesce(ServicePriceTier.quantity_sold, 0)}, synchronize_session=False)
        db.session.refresh(order.tier)
        assign_legacy_seats(order.tier)
    existing = {row.seat_number for row in TicketAdmission.query.filter_by(order_id=order.id).all()}
    for seat in range(1, min(50, max(1, order.quantity or 1)) + 1):
        if seat not in existing:
            db.session.add(TicketAdmission(order_id=order.id, seat_number=seat, nonce=secrets.token_hex(32)))


def signer():
    return URLSafeSerializer(current_app.config['SECRET_KEY'], salt='smarkafrica-event-admission-v1')


def ticket_token(admission):
    return signer().dumps({'admission': admission.id, 'event': admission.order.service_id, 'nonce': admission.nonce})


def resolve_ticket(service_id, token, include_invalid=False):
    try:
        data = signer().loads(token)
        if data['event'] != service_id:
            abort(404)
        admission = db.session.get(TicketAdmission, int(data['admission']))
        if not admission or not secrets.compare_digest(admission.nonce, data['nonce']):
            abort(404)
    except (BadSignature, KeyError, ValueError, TypeError):
        abort(404)
    order = admission.order
    if order.service_id != service_id:
        abort(404)
    if not include_invalid and admission_state(admission) not in ('Valid', 'Used'):
        abort(409)
    return admission


def register_service_routes(app, notify, invalidate, reconcile_payment):
    app.jinja_env.globals['ticket_token'] = ticket_token
    app.jinja_env.globals['admission_state'] = admission_state

    @app.after_request
    def protect_ticket_responses(response):
        if request.endpoint in ('service_ticket_checkin', 'service_ticket_wallet', 'service_ticket_qr', 'event_ticket_sales'):
            response.headers['Cache-Control'] = 'private, no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.route('/services/<int:service_id>/ticket-sales')
    @login_required
    def event_ticket_sales(service_id):
        service = db.session.get(ServiceListing, service_id) or abort(404)
        if not current_user.is_admin and current_user.id != service.provider_id:
            abort(403)
        if service.profile != 'ticket':
            abort(404)
        pagination = ServiceOrder.query.filter_by(service_id=service_id).order_by(ServiceOrder.id.desc()).paginate(per_page=30, error_out=False)
        ids = [order.id for order in pagination.items]
        admissions = TicketAdmission.query.filter(TicketAdmission.order_id.in_(ids)).order_by(TicketAdmission.id).all() if ids else []
        grouped = {}
        for admission in admissions:
            grouped.setdefault(admission.order_id, []).append(admission)
        return render_template('event_ticket_sales.html', service=service, pagination=pagination, admissions=grouped)

    @app.route('/services/orders/<int:order_id>/ticket-status', methods=['POST'])
    @app.route('/services/orders/<int:order_id>/admin-status', methods=['POST'])
    @login_required
    def change_ticket_status(order_id):
        if not current_user.is_admin:
            abort(403)
        order = db.session.get(ServiceOrder, order_id) or abort(404)
        action = request.form.get('action')
        reason = (request.form.get('reason') or '').strip()[:300]
        reference = (request.form.get('reference') or '').strip()[:100]
        if action not in ('cancelled', 'refunded') or not reason:
            abort(400)
        if action == 'refunded' and (not order.is_paid or not reference or request.form.get('refund_confirmed') != 'yes'):
            abort(400)
        ServiceOrder.query.filter_by(id=order.id).update({'id': ServiceOrder.id}, synchronize_session=False)
        db.session.refresh(order)
        if order.status != action and order.status != 'refunded':
            order.status = action
            db.session.add(TicketOrderAction(order_id=order.id, admin_id=current_user.id, action=action,
                reason=reason, reference=reference or None))
            notify(order.client_id, 'Service order updated', f'Order #{order.id}: {action}. {reason}', 'service')
        db.session.commit()
        flash('Order status saved.', 'success')
        if order.service.profile != 'ticket':
            row = ServiceLinkRequest.query.filter_by(service_order_id=order.id).first()
            return redirect(url_for('service_conversation', request_id=row.id) if row else url_for('service_detail', service_id=order.service_id))
        return redirect(url_for('event_ticket_sales', service_id=order.service_id))
    def quote_version(row):
        return hashlib.sha256(f'{row.id}:{row.quoted_amount}:{row.quote_description}'.encode()).hexdigest()
    app.jinja_env.globals['quote_version'] = quote_version

    @app.route('/services/orders/<int:order_id>/payment-status')
    @login_required
    def service_payment_status(order_id):
        order = db.session.get(ServiceOrder, order_id) or abort(404)
        if current_user.id != order.client_id:
            abort(403)
        if order.payment_status == 'pending' and order.checkout_request_id:
            cutoff = datetime.utcnow() - timedelta(seconds=30)
            claimed = ServiceOrder.query.filter(ServiceOrder.id == order.id, ServiceOrder.payment_status == 'pending',
                db.or_(ServiceOrder.payment_checked_at.is_(None), ServiceOrder.payment_checked_at < cutoff)).update(
                    {'payment_checked_at': datetime.utcnow()}, synchronize_session=False)
            db.session.commit()
            if claimed:
                reconcile_payment(order)
            db.session.refresh(order)
        response = jsonify(payment_status=order.payment_status, status=order.status,
            ticket_url=url_for('service_ticket_wallet', order_id=order.id)
            if order.is_paid and order.ticket_code and order.status not in ('oversold_refund_due', 'cancelled', 'refunded', 'payment_failed')
            and order.service.ticket_review_status == 'approved' else None)
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @app.route('/services/requests')
    @login_required
    def service_requests_inbox():
        query = ServiceLinkRequest.query.join(ServiceListing)
        if not current_user.is_admin:
            query = query.filter(db.or_(ServiceLinkRequest.client_id == current_user.id,
                db.and_(ServiceListing.provider_id == current_user.id, ServiceLinkRequest.status.in_(['linked', 'closed']))))
        pagination = query.order_by(ServiceLinkRequest.created_at.desc()).paginate(per_page=30, error_out=False)
        return render_template('service_requests_inbox.html', rows=pagination.items, pagination=pagination)

    @app.route('/services/requests/<int:request_id>')
    @login_required
    def service_conversation(request_id):
        row = db.session.get(ServiceLinkRequest, request_id) or abort(404)
        if (not current_user.is_admin and current_user.id != row.client_id
                and not (current_user.id == row.service.provider_id and row.status in ('linked', 'closed'))):
            abort(403)
        return render_template('service_conversation.html', row=row)

    @app.route('/my-tickets')
    @login_required
    def my_event_tickets():
        orders = ServiceOrder.query.filter_by(client_id=current_user.id, payment_status='paid').filter(
            ServiceOrder.ticket_code.isnot(None)).order_by(ServiceOrder.created_at.desc()).paginate(per_page=20, error_out=False)
        return render_template('my_tickets.html', pagination=orders)

    @app.route('/services/<int:service_id>/review-ticket', methods=['POST'])
    @login_required
    def review_service_ticket(service_id):
        if not current_user.is_admin:
            abort(403)
        service = db.session.get(ServiceListing, service_id) or abort(404)
        if service.profile != 'ticket' or service.is_retired:
            abort(400)
        approved = request.form.get('decision') == 'approve'
        if approved and (not service.event_starts_at or not service.event_venue or not ServicePriceTier.query.filter_by(service_id=service.id, is_active=True).first()):
            flash('An event needs its start time, venue and tickets before approval.', 'danger')
        else:
            service.ticket_review_status = 'approved' if approved else 'rejected'
            service.ticket_reviewed_at = datetime.utcnow()
            service.ticket_reviewed_by_id = current_user.id
            notify(service.provider_id, 'Event listing reviewed',
                   f'{service.title}: {service.ticket_review_status}.', 'service')
            db.session.commit()
            invalidate()
            flash('Event review saved.', 'success')
        return redirect(url_for('service_detail', service_id=service.id))

    @app.route('/services/orders/<int:order_id>/tickets')
    @login_required
    def service_ticket_wallet(order_id):
        order = db.session.get(ServiceOrder, order_id) or abort(404)
        if current_user.id != order.client_id and not current_user.is_admin:
            abort(403)
        if order.service.profile != 'ticket' or order.service.ticket_review_status != 'approved' or not order.is_paid or order.status in ('oversold_refund_due', 'refunded', 'cancelled', 'payment_failed'):
            abort(409)
        # Upgrade previously paid tickets to individually verifiable admissions.
        issue_admissions(order)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if TicketAdmission.query.filter_by(order_id=order.id).count() != max(1, order.quantity or 1):
                raise
        tickets = TicketAdmission.query.filter_by(order_id=order.id).order_by(TicketAdmission.seat_number).all()
        response = make_response(render_template('service_tickets.html', order=order, tickets=tickets))
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.route('/services/tickets/<int:admission_id>/qr')
    @login_required
    def service_ticket_qr(admission_id):
        admission = db.session.get(TicketAdmission, admission_id) or abort(404)
        if current_user.id != admission.order.client_id and not current_user.is_admin:
            abort(403)
        resolve_ticket(admission.order.service_id, ticket_token(admission))
        import qrcode
        import qrcode.image.svg
        image = qrcode.make(url_for('service_ticket_checkin', service_id=admission.order.service_id,
                                   token=ticket_token(admission), _external=True), image_factory=qrcode.image.svg.SvgPathImage)
        output = BytesIO()
        image.save(output)
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'image/svg+xml'
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @app.route('/services/<int:service_id>/check-in/<token>', methods=['GET', 'POST'])
    @login_required
    def service_ticket_checkin(service_id, token):
        service = db.session.get(ServiceListing, service_id) or abort(404)
        if not current_user.is_admin and current_user.id != service.provider_id:
            abort(403)
        admission = resolve_ticket(service_id, token, include_invalid=True)
        if request.method == 'POST':
            ServiceListing.query.filter_by(id=service.id).update({'id': ServiceListing.id}, synchronize_session=False)
            ServiceOrder.query.filter_by(id=admission.order_id).update({'id': ServiceOrder.id}, synchronize_session=False)
            db.session.refresh(service)
            db.session.refresh(admission.order)
        state = admission_state(admission)
        if state not in ('Valid', 'Used'):
            return render_template('ticket_checkin.html', service=service, admission=admission,
                state=state, result=f'{state}. Do not admit.'), 409
        if request.method == 'POST':
            valid_orders = db.session.query(ServiceOrder.id).filter(ServiceOrder.payment_status == 'paid',
                ServiceOrder.service_id == service_id,
                ServiceOrder.status.notin_(['oversold_refund_due', 'refunded', 'cancelled', 'payment_failed']))
            consumed = TicketAdmission.query.filter(TicketAdmission.id == admission.id,
                TicketAdmission.checked_in_at.is_(None), TicketAdmission.order_id.in_(valid_orders)).update({
                'checked_in_at': datetime.utcnow(), 'checked_in_by_id': current_user.id}, synchronize_session=False)
            db.session.commit()
            db.session.refresh(admission)
            if not consumed:
                return render_template('ticket_checkin.html', service=service, admission=admission, result='Already used. Do not admit again.'), 409
            return render_template('ticket_checkin.html', service=service, admission=admission, state='Used', result='Admission confirmed.')
        response = make_response(render_template('ticket_checkin.html', service=service, admission=admission, state=state, result=''))
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    def thread_message(row, text):
        db.session.add(ServiceLinkMessage(request_id=row.id, sender_id=current_user.id,
            from_admin=bool(current_user.is_admin),
            from_provider=current_user.id == row.service.provider_id and not current_user.is_admin,
            body=text))

    @app.route('/services/requests/<int:request_id>/progress', methods=['POST'])
    @login_required
    def service_request_progress(request_id):
        row = db.session.get(ServiceLinkRequest, request_id) or abort(404)
        if row.status == 'closed':
            return jsonify(success=False, error='This request is closed.'), 409
        action = request.form.get('action')
        if action in ('pickup_schedule', 'pickup_collect', 'pickup_return') and row.pickup_requested and row.status == 'linked' and (current_user.is_admin or current_user.id == row.service.provider_id):
            previous, following = {'pickup_schedule': ('requested', 'scheduled'), 'pickup_collect': ('scheduled', 'collected'), 'pickup_return': ('collected', 'returned')}[action]
            if action == 'pickup_return' and row.service.profile == 'errand':
                following = 'delivered'
            values = {'pickup_status': following}
            if action == 'pickup_schedule':
                window = (request.form.get('pickup_window') or '').strip()
                if not window or len(window) > 200:
                    return jsonify(success=False, error='Enter the agreed collection date and time.'), 400
                values['pickup_window'] = window
            elif action == 'pickup_collect':
                values['picked_up_at'] = datetime.utcnow()
            else:
                values['returned_at'] = datetime.utcnow()
                values['provider_completed_at'] = datetime.utcnow()
            changed = ServiceLinkRequest.query.filter_by(id=row.id, pickup_status=previous, status='linked').update(values, synchronize_session=False)
            if not changed:
                db.session.rollback()
                return jsonify(success=False, error='This pickup stage has already changed. Refresh the conversation.'), 409
            db.session.refresh(row)
            thread_message(row, f'Pickup status: {following}. Collection window: {row.pickup_window}.')
            notify(row.client_id, 'Pickup progress updated', f'{row.service.title}: {following}.', 'service')
        elif action == 'confirm' and row.client_id == current_user.id:
            if not row.client_confirmed_at:
                row.client_confirmed_at = datetime.utcnow()
                thread_message(row, 'Client confirmed: I received this service and am satisfied. Please review and close the request.')
                admins = [row.assigned_admin_id] if row.assigned_admin_id else [u.id for u in User.query.filter_by(is_admin=True).limit(30).all()]
                for admin_id in admins:
                    notify(admin_id, 'Client confirmed service received', f'Request #{row.id}: {row.service.title}. Review and close at the service desk.', 'service')
        elif action == 'ready' and current_user.id == row.service.provider_id and row.status == 'linked':
            if not row.provider_completed_at:
                row.provider_completed_at = datetime.utcnow()
                thread_message(row, 'Provider marked the service delivered / ready. Client, please confirm receipt when satisfied.')
                notify(row.client_id, 'Service ready for confirmation', f'{row.service.title}: confirm receipt in your service conversation.', 'service')
        else:
            abort(403)
        db.session.commit()
        return jsonify(success=True)

    @app.route('/services/requests/<int:request_id>/quote', methods=['POST'])
    @login_required
    def service_request_quote(request_id):
        if not current_user.is_admin:
            abort(403)
        row = db.session.get(ServiceLinkRequest, request_id) or abort(404)
        if row.status == 'closed' or row.quote_accepted_at or row.service.profile == 'ticket':
            abort(409)
        try:
            amount = Decimal(request.form.get('amount', ''))
            if not amount.is_finite() or not 0 < amount <= 10000000 or amount != amount.quantize(Decimal('0.01')):
                raise ValueError()
        except (InvalidOperation, ValueError):
            flash('Enter a valid total with at most two decimals.', 'danger')
            return redirect(url_for('admin_service_requests'))
        description = (request.form.get('description') or '').strip()
        if not description or len(description) > 500:
            abort(400)
        row.quoted_amount = float(amount)
        row.quote_description = description
        thread_message(row, f'Final quote: KSh {amount:.2f}. {description}. Please accept the quote before work/payment.')
        notify(row.client_id, 'Service quote ready', f'Review the quote for {row.service.title} in your conversation.', 'service')
        db.session.commit()
        return redirect(url_for('admin_service_requests'))

    @app.route('/services/requests/<int:request_id>/accept-quote', methods=['POST'])
    @login_required
    def accept_service_quote(request_id):
        row = ServiceLinkRequest.query.filter_by(id=request_id).with_for_update().first_or_404()
        if current_user.id != row.client_id:
            abort(403)
        if row.status == 'closed' or not row.quoted_amount:
            abort(409)
        if not secrets.compare_digest(request.form.get('quote_version', ''), quote_version(row)):
            flash('The quote changed. Review the latest total before accepting.', 'warning')
            return redirect(url_for('service_conversation', request_id=row.id))
        if not row.quote_accepted_at:
            changed = ServiceLinkRequest.query.filter_by(id=row.id, quote_accepted_at=None).update(
                {'quote_accepted_at': datetime.utcnow()}, synchronize_session=False)
            if changed:
                service = row.service
                if not service.pays_provider_direct:
                    fee = round(row.quoted_amount * (service.platform_commission or 0) / 100, 2)
                    order = ServiceOrder(service_id=service.id, client_id=row.client_id, provider_id=service.provider_id,
                        amount=row.quoted_amount, platform_fee=fee, provider_payout=row.quoted_amount-fee,
                        requirements=row.quote_description, pay_to='platform', status='pending', payment_status='pending')
                    db.session.add(order)
                    db.session.flush()
                    row.service_order_id = order.id
                thread_message(row, f'Client accepted the final quote of KSh {row.quoted_amount:.2f}.')
                db.session.commit()
        return redirect(url_for('service_conversation', request_id=row.id))
