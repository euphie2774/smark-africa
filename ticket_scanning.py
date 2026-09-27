"""Event-scoped bearer scanners. QR and barcode consume the same admission."""
import hashlib
import hmac
import secrets
from datetime import datetime
from io import BytesIO
from urllib.parse import urlsplit

from flask import abort, current_app, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from itsdangerous import BadSignature, URLSafeSerializer
from werkzeug.exceptions import HTTPException

from models import db, ServiceListing, ServiceOrder, TicketAdmission
from service_fulfilment import admission_state, resolve_ticket


def scanner_signer():
    return URLSafeSerializer(current_app.config['SECRET_KEY'], salt='event-scanner-v1')


def scanner_key(service):
    return scanner_signer().dumps([service.id, service.ticket_scanner_version])


def barcode_value(admission):
    digest = hmac.new(current_app.config['SECRET_KEY'].encode(),
                      ('card:' + admission.nonce).encode(), hashlib.sha256).hexdigest()[:24]
    return f'B{admission.id}-{digest}'


def barcode_svg(admission):
    from barcode import Code128
    from barcode.writer import SVGWriter
    out = BytesIO()
    Code128(barcode_value(admission), writer=SVGWriter()).write(out, options={
        'module_width': 0.18, 'module_height': 12, 'quiet_zone': 3, 'write_text': False})
    return out.getvalue().decode()


def register_ticket_scanning(app, limiter):
    @app.after_request
    def scanner_privacy(response):
        if request.endpoint in ('ticket_scanner', 'ticket_scanner_settings', 'ticket_card'):
            response.headers['Cache-Control'] = 'private, no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        return response

    def authorized_service(service_id, key):
        service = db.session.get(ServiceListing, service_id) or abort(404)
        try:
            identity, version = scanner_signer().loads(key)
            if identity != service.id or version != service.ticket_scanner_version:
                abort(404)
        except (BadSignature, ValueError, TypeError):
            abort(404)
        if service.profile != 'ticket' or service.is_retired:
            abort(404)
        return service

    @app.route('/services/<int:service_id>/scanner-settings', methods=['GET', 'POST'])
    @login_required
    def ticket_scanner_settings(service_id):
        service = db.session.get(ServiceListing, service_id) or abort(404)
        if current_user.id != service.provider_id:
            abort(403)
        if service.profile != 'ticket':
            abort(404)
        if request.method == 'POST':
            if request.form.get('action') == 'rotate':
                service.ticket_scanner_version = secrets.token_hex(32)
            else:
                service.ticket_format = 'barcode' if request.form.get('ticket_format') == 'barcode' and service.ticket_print_allowed else 'qr'
                service.ticket_auto_print = service.ticket_print_allowed and request.form.get('auto_print') == 'yes'
            db.session.commit()
            return redirect(url_for('ticket_scanner_settings', service_id=service.id))
        return render_template('ticket_scanner_settings.html', service=service,
            scanner_url=url_for('ticket_scanner', service_id=service.id, key=scanner_key(service), _external=True))

    @app.route('/services/<int:service_id>/scanner/<key>', methods=['GET', 'POST'])
    @limiter.limit('600 per minute; 20000 per hour', override_defaults=True)
    def ticket_scanner(service_id, key):
        # Scanning is authorized by a revocable bearer capability, never by a
        # login cookie. Require the capability in a custom header as well as
        # the URL: cross-site forms cannot supply this header, and cross-origin
        # JavaScript needs a CORS preflight (this endpoint does not grant CORS).
        # This lets a gate stay open without a session/CSRF token expiring, and
        # preserves no-referrer so the private link cannot leak through headers.
        if request.method == 'POST' and not secrets.compare_digest(
                request.headers.get('X-Scanner-Key', '').encode(), key.encode()):
            return jsonify(state='unauthorized', message='Scanner authorization is missing. Open your private scanner link.'), 403
        service = authorized_service(service_id, key)
        if request.method == 'GET':
            return render_template('ticket_scanner.html', service=service)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(state='invalid', message='Invalid scan request.'), 400
        raw = payload.get('code', '')
        if not isinstance(raw, str) or len(raw) > 2048:
            return jsonify(state='invalid', message='Invalid ticket.'), 400
        raw = raw.strip()
        is_barcode = raw.startswith('B') and '-' in raw and raw[1:raw.index('-')].isdigit()
        try:
            if is_barcode:
                if len(raw.split('-', 1)[0]) > 13 or len(raw.split('-', 1)[1]) != 24:
                    abort(404)
                admission = db.session.get(TicketAdmission, int(raw[1:raw.index('-')]))
                if not admission or admission.order.service_id != service.id or not secrets.compare_digest(raw, barcode_value(admission)):
                    abort(404)
            else:
                token = urlsplit(raw).path.rsplit('/', 1)[-1] if '://' in raw else raw
                admission = resolve_ticket(service.id, token, include_invalid=True)
        except (HTTPException, ValueError, TypeError):
            return jsonify(state='invalid', message='Invalid ticket.'), 404
        # Same lock order as payment settlement and the original check-in endpoint.
        ServiceListing.query.filter_by(id=service.id).update({'id': ServiceListing.id}, synchronize_session=False)
        ServiceOrder.query.filter_by(id=admission.order_id).update({'id': ServiceOrder.id}, synchronize_session=False)
        db.session.refresh(service)
        authorized_service(service.id, key)
        db.session.refresh(admission.order)
        db.session.refresh(admission)
        state = admission_state(admission)
        if state != 'Valid':
            db.session.rollback()
            return jsonify(state='used' if state == 'Used' else 'invalid',
                           message='Used. Do not admit again.' if state == 'Used' else 'Invalid ticket. ' + state + '.'), 409
        consumed = TicketAdmission.query.filter_by(id=admission.id, checked_in_at=None).update({
            'checked_in_at': datetime.utcnow(), 'checked_in_by_id': None}, synchronize_session=False)
        db.session.commit()
        if not consumed:
            return jsonify(state='used', message='Used. Do not admit again.'), 409
        card = url_for('ticket_card', admission_id=admission.id, key=key) if service.ticket_print_allowed else None
        return jsonify(state='accepted', message='Accepted. Admit one person.',
                       category=admission.order.tier.name if admission.order.tier else 'Regular',
                       card_url=card, auto_print=bool(service.ticket_auto_print and card and not is_barcode))

    @app.route('/services/tickets/<int:admission_id>/card')
    def ticket_card(admission_id):
        admission = db.session.get(TicketAdmission, admission_id) or abort(404)
        service = admission.order.service
        if request.args.get('key'):
            authorized_service(service.id, request.args['key'])
        elif not current_user.is_authenticated or current_user.id not in (service.provider_id, admission.order.client_id):
            abort(403)
        if not service.ticket_print_allowed:
            abort(403)
        if admission_state(admission) not in ('Valid', 'Used'):
            abort(409)
        return render_template('ticket_card.html', admission=admission, service=service, barcode=barcode_svg(admission))
