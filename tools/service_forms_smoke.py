"""Run directly: python tools/service_forms_smoke.py (disposable database)."""
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run():
    with tempfile.TemporaryDirectory(prefix='service-forms-') as scratch:
        os.environ.update(DATABASE_URL='sqlite:///' + (Path(scratch) / 'test.db').as_posix(),
                          DISABLE_BACKGROUND_JOBS='1', DEFER_OUTBOUND_WORKER='1',
                          FLASK_ENV='development', ALLOW_EPHEMERAL_SQLITE='1',
                          SECRET_KEY='service-form-test', REDIS_URL='')
        import main
        from models import db, User, ServiceListing, SERVICE_PROFILE_BY_KEY
        from service_forms import SERVICE_QUESTIONS, read_service_answers
        from werkzeug.datastructures import MultiDict

        app = main.app
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        main.limiter.enabled = False
        try:
            with app.app_context(), app.test_client() as client:
                admin = User.query.filter_by(is_admin=True).first()
                with client.session_transaction() as session:
                    session['_user_id'] = str(admin.id)
                    session['_fresh'] = True
                response = client.get('/services/create')
                assert response.status_code == 200, response.status_code
                html = response.get_data(as_text=True)
                assert 'Choose a ticket tier' in html and 'value="VVIP"' in html
                assert set(SERVICE_QUESTIONS) == set(SERVICE_PROFILE_BY_KEY)
                for category, questions in SERVICE_QUESTIONS.items():
                    assert f'data-category="{category}"' in html
                    data = MultiDict({'service_key': category, 'title': 'Form test ' + category,
                                      'provider_phone': '0712345678', 'price': '250'})
                    for key, label, hint in questions:
                        data['detail_' + category + '_' + key] = 'Details for ' + label
                    data['detail_unrelated'] = 'Must not be stored'
                    if category == 'events_tickets':
                        data.setlist('tier_name', ['Regular', 'VIP', 'VVIP', 'Custom'])
                        data.setlist('tier_custom_name', ['', '', '', 'Backstage'])
                        data.setlist('tier_price', ['100', '500.50', '1000', '1500'])
                        data.setlist('tier_quantity', ['100', '20', '10', '5'])
                        data.setlist('tier_max', ['5', '2', '2', '1'])
                    result = client.post('/services/create', data=data)
                    assert result.status_code == 302, (category, result.status_code)
                    listing = ServiceListing.query.filter_by(title=data['title']).one()
                    assert len(listing.offering_answers) == len(questions)
                    assert 'Must not be stored' not in listing.offering_details
                    page = client.get(result.headers['Location']).get_data(as_text=True)
                    assert 'How this service is offered' in page
                    for answer in listing.offering_answers:
                        from markupsafe import escape
                        assert str(escape(answer['value'])) in page
                    if category == 'events_tickets':
                        assert {t.name: t.price for t in listing.tiers} == {
                            'Regular': 100, 'VIP': 500.50, 'VVIP': 1000, 'Backstage': 1500}
                        assert listing.price == 100
                print('PASS: all 18 category forms save and display their own answers; four ticket tiers retain separate prices')

                for data in [MultiDict([('tier_name', 'VIP'), ('tier_price', 'nan')]),
                             MultiDict([('tier_name', 'Custom'), ('tier_price', '200')]),
                             MultiDict([('tier_name', 'VIP'), ('tier_name', 'vip'),
                                        ('tier_price', '100'), ('tier_price', '200')])]:
                    with app.test_request_context(method='POST', data=data):
                        assert main.read_service_tiers()[1]
                try:
                    read_service_answers('laundry', {'detail_laundry_laundry_items': 'x' * 1001})
                except ValueError:
                    pass
                else:
                    raise AssertionError('Oversize answer accepted')
                assert read_service_answers('laundry', {'detail_device_repair_devices': 'wrong category'}) == []
                print('PASS: invalid prices, duplicate tiers, missing custom names and invalid category answers rejected')
                from service_forms import read_price_items, request_summary, form_design, validate_design
                from types import SimpleNamespace
                from unittest.mock import patch
                import json
                import service_design_jobs
                from models import ServiceCatalogueItem

                menu = MultiDict({'service_key': 'food_delivery', 'title': 'Structured menu',
                    'provider_phone': '0712345678', 'delivery_fee': '50',
                    'item_name': 'Rice bowl', 'item_price': '125.50', 'item_unit': 'portion',
                    'item_description': 'Large bowl'})
                result = client.post('/services/create', data=menu)
                assert result.status_code == 302
                listing = ServiceListing.query.filter_by(title='Structured menu').one()
                assert listing.price_items[0]['price'] == '125.50'
                assert listing.price == 125.50
                summary = request_summary(listing, {'quantity_0': '2', 'total': '1', 'request_destination': 'Gate B'})
                assert '301.00' in summary and 'Gate B' in summary, summary
                page = client.get(result.headers['Location']).get_data(as_text=True)
                assert 'Rice bowl' in page and '125.50' in page and 'Large bowl' in page
                for price in ['nan', 'inf', '-1', '1.001', '10000001', '']:
                    invalid = MultiDict(menu)
                    invalid['item_price'] = price
                    try:
                        read_price_items(invalid)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError('Accepted invalid item price: ' + price)
                for quantity in ['-1', '101', '1.5', 'bad']:
                    try:
                        request_summary(listing, {'quantity_0': quantity})
                    except ValueError:
                        pass
                    else:
                        raise AssertionError('Accepted invalid quantity')
                result = client.post('/services/create', data={
                    'service_key': 'events_tickets', 'title': 'One admission price',
                    'provider_phone': '0712345678', 'ticket_mode': 'single',
                    'single_ticket_price': '300.50', 'single_ticket_quantity': '40'})
                assert result.status_code == 302
                ticket = ServiceListing.query.filter_by(title='One admission price').one()
                assert ticket.tiers[0].name == 'General admission' and ticket.tiers[0].price == 300.50
                assert ticket.tiers[0].quantity_total == 40
                print('PASS: menus persist, estimates ignore client totals, invalid amounts rejected, single-price tickets work')

                future = ServiceCatalogueItem(key='pet_care', label='Pet care', fulfilment_profile='visit',
                    is_active=True, seller_listable=True, form_profile_auto=True)
                db.session.add(future)
                db.session.commit()
                main.invalidate_service_caches()
                page = client.get('/services/create').get_data(as_text=True)
                assert 'detail_pet_care_scope' in page
                design = {'questions': [['pets', 'Animals accepted', 'Species and size limits']],
                          'client_fields': [['animal', 'Animal and care needed']],
                          'price_label': 'Care package', 'profile': 'visit'}
                with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only'}), patch.object(
                        service_design_jobs, 'generate_design', return_value=design) as generate:
                    service_design_jobs.process_pending_designs()
                    assert generate.called
                assert future.form_design_status == 'ready'
                main.invalidate_service_caches()
                page = client.get('/services/create').get_data(as_text=True)
                assert 'detail_pet_care_pets' in page
                result = client.post('/services/create', data={'service_key': 'pet_care',
                    'title': 'Pet sitting', 'provider_phone': '0712345678',
                    'detail_pet_care_pets': 'Cats'})
                assert result.status_code == 302
                pet_listing = ServiceListing.query.filter_by(title='Pet sitting').one()
                future.form_design_json = json.dumps(form_design('new', 'Other', 'session'))
                db.session.commit()
                assert pet_listing.service_design['client_fields'][0][0] == 'animal'
                assert pet_listing.offering_answers[0]['value'] == 'Cats'
                future.form_design_status = 'pending'
                future.form_design_attempts = 2
                db.session.commit()
                with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only'}), patch.object(
                        service_design_jobs, 'generate_design', side_effect=ValueError('Invalid AI response')):
                    service_design_jobs.process_pending_designs()
                assert future.form_design_status == 'failed'
                assert future.form_design_attempts == 3
                for path in ['/account', '/about']:
                    response = client.get(path)
                    assert response.status_code == 200, path
                    assert 'Bottom navigation' in response.get_data(as_text=True)
                assert 'Quick links' in client.get('/about').get_data(as_text=True)
                print('PASS: future category fallback, AI design persistence, snapshot isolation, bounded retries and navigation')
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()


if __name__ == '__main__':
    run()
