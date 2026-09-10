"""Secure admission and service-completion integration checks; no real payments."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run():
    with tempfile.TemporaryDirectory(prefix='ticket-workflow-') as scratch:
        os.environ.update(DATABASE_URL='sqlite:///' + (Path(scratch) / 'test.db').as_posix(),
            DISABLE_BACKGROUND_JOBS='1', DEFER_OUTBOUND_WORKER='1', DISABLE_OUTBOUND_WORKER='1',
            FLASK_ENV='development', ALLOW_EPHEMERAL_SQLITE='1', SECRET_KEY='ticket-test', REDIS_URL='')
        # Authentication is session-fixtured below; avoid scrypt's memory cost here.
        with patch('werkzeug.security.generate_password_hash', return_value='test-fixture-hash'):
            import main
        from models import db, User, ServiceListing, ServicePriceTier, ServiceOrder, TicketAdmission, ServiceLinkRequest
        from service_fulfilment import ticket_token
        app = main.app
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        main.limiter.enabled = False
        with app.app_context(), app.test_client() as client:
            try:
                admin = User.query.filter_by(is_admin=True).first()
                buyer = User(username='admission_buyer', email='buyer@example.test', password_hash='unused')
                provider = User(username='admission_provider', email='provider@example.test', password_hash='unused')
                stranger = User(username='admission_stranger', email='stranger@example.test', password_hash='unused')
                db.session.add_all([buyer, provider, stranger]); db.session.commit()
                def login(user):
                    from flask import g
                    g.pop('_login_user', None)
                    with client.session_transaction() as session:
                        session['_user_id'] = str(user.id); session['_fresh'] = True
                event = ServiceListing(title='Verified festival', provider_id=provider.id,
                    service_key='events_tickets', category='Event Tickets', fulfilment_profile='dropoff',
                    price=200, is_active=True, ticket_review_status='pending',
                    event_venue='Main hall', event_starts_at=main.utcnow())
                db.session.add(event); db.session.flush()
                tier = ServicePriceTier(service_id=event.id, name='VIP', price=200, quantity_total=2,
                    quantity_sold=0, max_per_order=2, is_active=True)
                db.session.add(tier); db.session.commit()
                assert event.profile == 'ticket' and event.pickup_display is None
                login(buyer)
                page = client.get(f'/services/{event.id}').get_data(as_text=True)
                assert 'No pickup offered' not in page and 'contactForm' not in page.split('<script>')[0]
                with patch.object(main, 'start_service_payment') as payment:
                    client.post(f'/services/{event.id}/buy', data={'tier_id': tier.id, 'quantity': 1})
                    assert not payment.called and ServiceOrder.query.count() == 0
                login(admin)
                assert client.post(f'/services/{event.id}/review-ticket', data={'decision': 'approve'}).status_code == 302
                assert event.ticket_review_status == 'approved'
                login(buyer)
                page = client.get(f'/services/{event.id}').get_data(as_text=True)
                assert f'/services/{event.id}/buy' in page
                with patch.object(main, 'start_service_payment', return_value=(True, 'Test prompt')):
                    client.post(f'/services/{event.id}/buy', data={'tier_id': tier.id, 'quantity': 2})
                order = ServiceOrder.query.one()
                assert client.get(f'/services/orders/{order.id}/tickets').status_code == 409
                assert not client.get(f'/services/orders/{order.id}/payment-status').json['ticket_url']
                assert not main.finalize_paid_service_order(order, 'SHORT', 1)
                assert TicketAdmission.query.count() == 0
                assert main.finalize_paid_service_order(order, 'TEST-PAID', 400)
                assert not main.finalize_paid_service_order(order, 'TEST-PAID', 400)
                admissions = TicketAdmission.query.order_by(TicketAdmission.id).all()
                assert len(admissions) == 2 and len({a.nonce for a in admissions}) == 2
                token = ticket_token(admissions[0])
                assert client.get(f'/services/orders/{order.id}/tickets').status_code == 200
                assert client.get(f'/services/orders/{order.id}/payment-status').json['ticket_url']
                assert client.get(f'/services/tickets/{admissions[0].id}/qr').status_code == 200
                login(stranger)
                assert client.get(f'/services/orders/{order.id}/tickets').status_code == 403
                assert client.get(f'/services/orders/{order.id}/payment-status').status_code == 403
                assert client.post(f'/services/{event.id}/check-in/{token}').status_code == 403
                login(provider)
                assert client.post(f'/services/{event.id}/check-in/{token}x').status_code == 404
                assert client.post(f'/services/{event.id}/check-in/{token}').status_code == 200
                assert client.post(f'/services/{event.id}/check-in/{token}').status_code == 409
                late = ServiceOrder(service_id=event.id, client_id=buyer.id, provider_id=provider.id,
                    tier_id=tier.id, quantity=1, amount=200, payment_status='pending', status='pending')
                db.session.add(late); db.session.commit()
                assert main.finalize_paid_service_order(late, 'LATE', 200)
                assert late.status == 'oversold_refund_due' and not late.ticket_code
                assert TicketAdmission.query.filter_by(order_id=late.id).count() == 0
                print('PASS: approval gate, legacy ticket identity, payment-only issuance, tamper/access protection, one-use check-in, oversold suppression')

                # Seat ranges are shared across orders, and tier/event caps apply across purchases.
                event2 = ServiceListing(title='Seated concert', provider_id=provider.id,
                    service_key='events_tickets', category='Event Tickets', fulfilment_profile='ticket',
                    price=50, is_active=True, ticket_review_status='approved', ticket_buyer_limit=3,
                    event_venue='Concert hall', event_starts_at=main.utcnow())
                db.session.add(event2); db.session.flush()
                vip = ServicePriceTier(service_id=event2.id, name='VIP', price=100, quantity_total=6, max_per_order=2)
                standing = ServicePriceTier(service_id=event2.id, name='Standing', price=50, quantity_total=0, max_per_order=0)
                db.session.add_all([vip, standing]); db.session.commit()
                def paid(tier, owner, quantity):
                    item = ServiceOrder(service_id=event2.id, client_id=owner.id, provider_id=provider.id,
                        tier_id=tier.id, quantity=quantity, amount=tier.price * quantity,
                        payment_status='pending', status='pending')
                    db.session.add(item); db.session.commit()
                    assert main.finalize_paid_service_order(item, 'INVENTORY', item.amount)
                    return item
                first = paid(vip, buyer, 1)
                second = paid(vip, buyer, 1)
                assert (first.ticket_seat_start, second.ticket_seat_start) == (1, 2)
                assert TicketAdmission.query.filter_by(order_id=second.id).one().allocated_seat == 2
                login(buyer)
                page = client.get(f'/services/{event2.id}').get_data(as_text=True)
                assert '4 left' not in page and '6 seats' not in page and 'seats remaining' not in page
                with patch.object(main, 'start_service_payment') as payment:
                    response = client.post(f'/services/{event2.id}/buy', data={'tier_id': vip.id, 'quantity': 1}, follow_redirects=True)
                    assert not payment.called and 'across purchases' in response.get_data(as_text=True)
                third = paid(vip, stranger, 2)
                fourth = paid(vip, admin, 2)
                assert (third.ticket_seat_start, fourth.ticket_seat_start) == (3, 5) and vip.seats_left == 0
                with patch.object(main, 'start_service_payment') as payment:
                    response = client.post(f'/services/{event2.id}/buy', data={'tier_id': vip.id, 'quantity': 1}, follow_redirects=True)
                    assert not payment.called and 'sold out' in response.get_data(as_text=True)
                unseated = paid(standing, buyer, 1)
                assert unseated.ticket_seat_start is None
                over_limit = paid(standing, buyer, 1)
                assert over_limit.status == 'oversold_refund_due' and not over_limit.ticket_code
                event2.ticket_buyer_limit = 0; db.session.commit()
                unlimited = paid(standing, buyer, 50)
                assert TicketAdmission.query.filter_by(order_id=unlimited.id).count() == 50
                assert standing.seats_left is None
                login(provider)
                assert client.get(f'/services/{event2.id}/ticket-sales').status_code == 200
                assert client.post(f'/services/orders/{third.id}/ticket-status', data={'action':'cancelled', 'reason':'Test'}).status_code == 403
                third_ticket = TicketAdmission.query.filter_by(order_id=third.id).first()
                third_token = ticket_token(third_ticket)
                login(admin)
                client.post(f'/services/orders/{third.id}/ticket-status', data={'action':'cancelled', 'reason':'Buyer requested cancellation'})
                response = client.get(f'/services/{event2.id}/check-in/{third_token}')
                assert response.status_code == 409 and 'Cancelled. Do not admit.' in response.get_data(as_text=True)
                assert client.post(f'/services/{event2.id}/check-in/{third_token}').status_code == 409
                assert client.post(f'/services/orders/{third.id}/ticket-status', data={'action':'refunded', 'reason':'Refund'}).status_code == 400
                client.post(f'/services/orders/{third.id}/ticket-status', data={'action':'refunded', 'reason':'Completed refund', 'reference':'REF-TEST', 'refund_confirmed':'yes'})
                response = client.get(f'/services/{event2.id}/check-in/{third_token}')
                assert response.status_code == 409 and 'Refunded. Do not admit.' in response.get_data(as_text=True)
                assert vip.seats_left == 0
                login(buyer)
                assert client.get(f'/services/{event2.id}/ticket-sales').status_code == 403

                # An unsigned callback claiming success cannot create a ticket.
                pending = ServiceOrder(service_id=event2.id, client_id=buyer.id, provider_id=provider.id,
                    tier_id=standing.id, quantity=1, amount=50, checkout_request_id='VERIFY-CHECKOUT',
                    payment_status='pending', status='pending')
                db.session.add(pending); db.session.commit()
                with patch.object(main, 'stk_push') as push:
                    main.start_service_payment(pending, '254700000000')
                    assert not push.called and pending.checkout_request_id == 'VERIFY-CHECKOUT'
                callback = {'Body': {'stkCallback': {'CheckoutRequestID':'VERIFY-CHECKOUT', 'ResultCode':0,
                    'CallbackMetadata': {'Item':[{'Name':'Amount','Value':50}, {'Name':'MpesaReceiptNumber','Value':'CALLBACK-REF'}]}}}}
                with patch.object(main, 'check_payment_status', return_value={'CheckoutRequestID':'VERIFY-CHECKOUT', 'ResultCode':'1032'}):
                    assert client.post('/mpesa/callback', json=callback).status_code == 503
                    assert not pending.is_paid and not pending.ticket_code
                pending.payment_status = 'pending'; pending.status = 'pending'; db.session.commit()
                with patch.object(main, 'check_payment_status', return_value=None):
                    assert client.post('/mpesa/callback', json=callback).status_code == 503
                    assert not pending.is_paid
                with patch.object(main, 'check_payment_status', return_value={'CheckoutRequestID':'VERIFY-CHECKOUT', 'ResultCode':'0'}):
                    assert client.get(f'/services/orders/{pending.id}/payment-status').json['ticket_url']
                    assert pending.is_paid and TicketAdmission.query.filter_by(order_id=pending.id).count() == 1
                print('PASS: global seat ranges, private capacity, cumulative limits, unlimited admission, revocation, refund checks and independent payment verification')

                from concurrent.futures import ThreadPoolExecutor
                from threading import Barrier
                last_seat = ServicePriceTier(service_id=event2.id, name='Final seat', price=100,
                    quantity_total=1, quantity_sold=0, max_per_order=0)
                db.session.add(last_seat); db.session.flush()
                racers = [ServiceOrder(service_id=event2.id, client_id=person.id, provider_id=provider.id,
                    tier_id=last_seat.id, quantity=1, amount=100, status='pending', payment_status='pending')
                    for person in (buyer, stranger)]
                db.session.add_all(racers); db.session.commit()
                race_ids = [item.id for item in racers]
                barrier = Barrier(2)
                def settle_racer(order_id):
                    with app.app_context():
                        barrier.wait(timeout=10)
                        item = db.session.get(ServiceOrder, order_id)
                        assert main.finalize_paid_service_order(item, 'RACE', 100)
                        return item.status
                db.session.commit()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    outcomes = list(pool.map(settle_racer, race_ids))
                db.session.expire_all()
                assert outcomes.count('oversold_refund_due') == 1
                assert last_seat.quantity_sold == 1
                winner = TicketAdmission.query.filter(TicketAdmission.order_id.in_(race_ids)).one()
                scan_token = ticket_token(winner)
                race_event_id, organiser_id = event2.id, provider.id
                barrier = Barrier(2)
                def scan_racer(_):
                    with app.test_client() as scan_client:
                        with scan_client.session_transaction() as session:
                            session['_user_id'] = str(organiser_id); session['_fresh'] = True
                        barrier.wait(timeout=10)
                        return scan_client.post(f'/services/{race_event_id}/check-in/{scan_token}').status_code
                db.session.commit()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    outcomes = list(pool.map(scan_racer, range(2)))
                assert sorted(outcomes) == [200, 409], outcomes
                print('PASS: concurrent last-seat payments issue one admission; concurrent scans admit once')

                service = ServiceListing(title='Laundry delivery', provider_id=provider.id,
                    service_key='laundry', category='Laundry', fulfilment_profile='dropoff',
                    price=100, is_active=True, pay_to='platform', pay_when='after')
                db.session.add(service); db.session.flush()
                row = ServiceLinkRequest(service_id=service.id, client_id=buyer.id,
                    assigned_admin_id=admin.id, status='linked')
                db.session.add(row); db.session.commit()
                login(buyer)
                client.post(f'/services/requests/{row.id}/thread', data={'body':'Please confirm delivery.'})
                login(provider)
                client.post(f'/services/requests/{row.id}/thread', data={'body':'Delivery is arranged here.'})
                login(admin)
                thread = client.get(f'/services/requests/{row.id}/thread').json
                assert any(m['body'] == 'Delivery is arranged here.' for m in thread['messages'])
                client.post(f'/services/requests/{row.id}/thread', data={'body':'Support is here to assist.'})
                thread = client.get(f'/services/requests/{row.id}/thread').json
                assert any(m['from_admin'] and m['body'] == 'Support is here to assist.' for m in thread['messages'])
                client.post(f'/services/requests/{row.id}/quote', data={'amount':'250', 'description':'2 kg laundry and return delivery'})
                assert row.quoted_amount == 250
                login(buyer)
                token_quote = app.jinja_env.globals['quote_version'](row)
                assert client.get(f'/services/requests/{row.id}').status_code == 200
                client.post(f'/services/requests/{row.id}/accept-quote', data={'quote_version':token_quote})
                order_id = row.service_order_id
                client.post(f'/services/requests/{row.id}/accept-quote', data={'quote_version':token_quote})
                assert row.service_order_id == order_id and row.service_order.amount == 250
                with patch.object(main, 'start_service_payment') as payment:
                    client.post(f'/services/orders/{order_id}/pay')
                    assert not payment.called
                login(stranger)
                assert client.post(f'/services/requests/{row.id}/progress', data={'action':'confirm'}).status_code == 403
                login(provider)
                assert client.post(f'/services/requests/{row.id}/progress', data={'action':'ready'}).status_code == 200
                login(buyer)
                assert client.post(f'/services/requests/{row.id}/progress', data={'action':'confirm'}).status_code == 200
                assert row.client_confirmed_at
                login(admin)
                client.post(f'/admin/services/requests/{row.id}/close')
                assert row.status == 'linked'
                assert main.finalize_paid_service_order(row.service_order, 'SERVICE-PAID', 250)
                client.post(f'/admin/services/requests/{row.id}/close')
                assert row.status == 'closed' and row.service_order.status == 'completed'
                assert service.orders_completed == 1
                client.post(f'/admin/services/requests/{row.id}/close')
                assert service.orders_completed == 1
                for path in ['/services/requests', '/account', '/my-tickets', '/admin/services']:
                    assert client.get(path).status_code == 200, path
                print('PASS: quote acceptance, amount preservation, readiness gate, client confirmation, admin closure and idempotency')
                service.pickup_required = True; service.pickup_return_included = True; service.pickup_cost = 75
                service.location_lat = 0; service.location_lng = 36
                db.session.commit()
                login(buyer)
                page = client.get(f'/services/{service.id}').get_data(as_text=True)
                assert 'data-service-location-view' in page and 'Provider pickup and return' in page
                with patch.object(main, 'notify_service_provider', return_value={}), patch.object(main, 'handoff_service_request_to_whatsapp', return_value={}):
                    response = client.post(f'/services/{service.id}/contact-admin', data={'handover_method':'pickup', 'pickup_address':'Test entrance', 'pickup_window':'Friday 10am'})
                assert response.status_code == 200
                pickup_row = db.session.get(ServiceLinkRequest, response.json['request_id'])
                assert pickup_row.pickup_requested and pickup_row.pickup_status == 'requested' and 'fee: KSh 75.00' in pickup_row.client_note
                login(admin)
                client.post(f'/admin/services/requests/{pickup_row.id}/link')
                login(stranger)
                assert client.post(f'/services/requests/{pickup_row.id}/progress', data={'action':'pickup_collect'}).status_code == 403
                login(provider)
                assert client.post(f'/services/requests/{pickup_row.id}/progress', data={'action':'pickup_return'}).status_code == 409
                assert client.post(f'/services/requests/{pickup_row.id}/progress', data={'action':'pickup_schedule','pickup_window':'Friday 11am'}).status_code == 200
                assert client.post(f'/services/requests/{pickup_row.id}/progress', data={'action':'pickup_collect'}).status_code == 200
                assert pickup_row.picked_up_at and pickup_row.pickup_status == 'collected'
                assert client.post(f'/services/requests/{pickup_row.id}/progress', data={'action':'pickup_return'}).status_code == 200
                assert pickup_row.returned_at and pickup_row.provider_completed_at and pickup_row.pickup_status == 'returned'
                login(buyer)
                assert client.post(f'/services/orders/{order_id}/admin-status', data={'action':'refunded','reason':'test'}).status_code == 403
                login(admin)
                assert client.post(f'/services/orders/{order_id}/admin-status', data={'action':'refunded','reason':'Exceptional refund','reference':'REF-SERVICE','refund_confirmed':'yes'}).status_code == 302
                assert row.service_order.status == 'refunded'
                from service_forms import pickup_request_details, read_location
                service.pickup_required = False
                try:
                    pickup_request_details(service, {'handover_method':'pickup','pickup_address':'Test','pickup_window':'Friday'})
                    raise AssertionError('Pickup was accepted while disabled')
                except ValueError:
                    pass
                for coordinates in ({'location_lat':'91','location_lng':'0'}, {'location_lat':'0'}, {'location_lat':'nan','location_lng':'0'}):
                    try:
                        read_location(coordinates)
                        raise AssertionError('Invalid map pin accepted')
                    except ValueError:
                        pass
                assert read_location({'location_lat':'0','location_lng':'0'}) == (0, 0)
                print('PASS: service maps, pickup fees and staged collection/return, disabled-pickup rejection and admin-only service refunds')
                from models import Product
                db.session.add_all([
                    Product(name='Campus USB drive', slug='suggest-usb', description='Portable files', selling_price=100, is_active=True),
                    Product(name='Campus hidden product', slug='suggest-hidden', description='Hidden', selling_price=100, is_active=False),
                    Product(name='Waterproof rain boots', slug='suggest-rain', description='Dry feet', selling_price=200, is_active=True)])
                db.session.commit()
                matches = client.get('/api/products/suggestions?q=camp').json['suggestions']
                assert any(m['name'] == 'Campus USB drive' for m in matches)
                assert not any(m['name'] == 'Campus hidden product' for m in matches)
                assert client.get('/api/products/suggestions?q=c').json['suggestions'] == []
                assert client.get('/api/products/suggestions?q=%25%25').json['suggestions'] == []
                assert any(m['name'] == 'Waterproof rain boots' for m in client.get('/api/products/suggestions?q=rainy%20weather').json['suggestions'])
                assert any(m['name'] == 'Campus USB drive' for m in client.get('/api/products/suggestions?q=flash%20disk').json['suggestions'])
                ids = main.cached_product_search_ids(search='rainy weather')
                assert Product.query.filter_by(slug='suggest-rain').one().id in ids
                assert 'data-product-suggestions' in client.get('/shop').get_data(as_text=True)
                if os.environ.get('SEARCH_PREVIEW_DIR'):
                    import shutil
                    preview = Path(os.environ['SEARCH_PREVIEW_DIR'])
                    (preview / 'static').mkdir(parents=True, exist_ok=True)
                    (preview / 'api/products').mkdir(parents=True, exist_ok=True)
                    (preview / 'index.html').write_text(client.get('/shop').get_data(as_text=True), encoding='utf-8')
                    (preview / 'api/products/suggestions').write_bytes(client.get('/api/products/suggestions?q=camp').data)
                    for asset in ('style.css', 'search_suggestions.js'):
                        shutil.copyfile(Path('static') / asset, preview / 'static' / asset)
                print('PASS: prefix suggestions, inactive exclusion, wildcard escaping and meaning-based discovery')
            finally:
                db.session.remove(); db.engine.dispose()


if __name__ == '__main__':
    run()
