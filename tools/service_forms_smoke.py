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
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()


if __name__ == '__main__':
    run()
