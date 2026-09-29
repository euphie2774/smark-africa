"""HTTPS CSRF and production STK regression checks; no live payments."""
import os
from pathlib import Path
import re
import sys
import tempfile
from contextlib import ExitStack
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run():
    with tempfile.TemporaryDirectory(prefix='cart-payment-') as scratch, ExitStack() as cleanup:
        os.environ.update(DATABASE_URL='sqlite:///' + (Path(scratch) / 'test.db').as_posix(),
            DISABLE_BACKGROUND_JOBS='1', DISABLE_OUTBOUND_WORKER='1', DEFER_OUTBOUND_WORKER='1',
            SECRET_KEY='cart-payment-test', FLASK_ENV='development', ALLOW_EPHEMERAL_SQLITE='1', CACHE_TYPE='NullCache')
        with patch('werkzeug.security.generate_password_hash', return_value='fixture'):
            import main
        from models import db, User, Product, Category, Cart, Setting
        app = main.app
        def close_database():
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
        cleanup.callback(close_database)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
        main.limiter.enabled = False
        base = 'https://localhost'
        with app.app_context():
            admin = User.query.filter_by(is_admin=True).first()
            product = Product(name='Snake light', slug='snake-light-test', selling_price=50,
                              description='LED strip light', short_description='', image_url='/logo.png',
                              buying_price=20, stock=10, is_active=True, free_delivery=True,
                              category_id=Category.query.first().id, seller_id=admin.id)
            db.session.add(product)
            db.session.flush()
            db.session.add(Cart(user_id=admin.id, product_id=product.id, quantity=1))
            db.session.commit()
            admin_id, product_id = admin.id, product.id
        client = app.test_client()
        with client.session_transaction(base_url=base) as session:
            session['_user_id'] = str(admin_id)
            session['_fresh'] = True

        def page_token(path):
            response = client.get(path, base_url=base)
            assert response.status_code == 200, (path, response.status_code)
            return re.search(r'name="csrf-token" content="([^"]+)"', response.text).group(1)

        def headers(path):
            return {'X-CSRFToken': page_token(path), 'Referer': base + path}

        if os.environ.get('CART_PREVIEW_DIR'):
            preview = Path(os.environ['CART_PREVIEW_DIR'])
            preview.mkdir(parents=True, exist_ok=True)
            html = client.get('/cart', base_url=base).text
            html = re.sub(r'<script\b[\s\S]*?</script>', '', html)
            html = re.sub(r'/static/style.css\?[^" ]+', '/style.css', html)
            (preview / 'index.html').write_text(html, encoding='utf-8')
            root = Path(__file__).resolve().parents[1]
            (preview / 'style.css').write_bytes((root / 'static/style.css').read_bytes())
            (preview / 'logo.png').write_bytes((root / 'static/images/favicon.png').read_bytes())

        edit = f'/admin/products/edit/{product_id}'
        edit_headers = headers(edit)
        for endpoint, helper, payload, result in (
            ('/admin/api/price-check', 'market_price_reference', {'name':'Snake light','selling_price':50}, {}),
            ('/admin/api/product-description', 'generate_product_description', {'name':'Snake light'}, 'LED light'),
        ):
            with patch.object(main, helper, return_value=result) as mocked:
                denied = client.post(endpoint, base_url=base, json=payload)
                assert denied.status_code == 400 and denied.json['error'] == 'csrf_failed'
                assert not mocked.called
                response = client.post(endpoint, base_url=base, headers=edit_headers, json=payload)
                assert response.status_code == 200 and mocked.called
        with client.session_transaction(base_url=base) as session:
            assert not session.get('_flashes'), 'Background API failure polluted the next page'
        response = client.post(edit, base_url=base, headers=edit_headers,
                               data={'name':'Snake light updated','selling_price':'60','stock':'10'})
        assert response.status_code == 302
        with app.app_context():
            assert db.session.get(Product, product_id).name == 'Snake light updated'
        print('PASS: listing AJAX CSRF, successful edit, no false session flash')

        settings_headers = headers('/admin/settings')
        response = client.post('/admin/settings', base_url=base, headers=settings_headers, data={
            'daraja_env':'production', 'daraja_consumer_key':'fixture-key',
            'daraja_consumer_secret':'fixture-secret', 'daraja_passkey':'fixture-passkey',
            'daraja_shortcode':'600123', 'app_base_url':'https://shop.example.test'})
        assert response.status_code == 302
        with app.app_context():
            assert Setting.get('daraja_env') == 'production'
        checkout_headers = headers('/checkout')
        with patch.object(main, 'quote_cart_delivery', return_value={'total_amount':0, 'free_delivery':True, 'lines':[]}):
            response = client.post('/api/shipping-cost', base_url=base, headers=checkout_headers,
                                   json={'country':'Kenya', 'city':'Nairobi'})
            assert response.status_code == 200
        token_response = Mock(status_code=200, text='fixture', json=lambda: {'access_token':'fixture-token'})
        stk_response = Mock(status_code=200, text='fixture', json=lambda: {
            'ResponseCode':'0', 'CheckoutRequestID':'fixture-checkout', 'MerchantRequestID':'fixture-merchant'})
        with patch.object(main.requests, 'get', return_value=token_response) as oauth, \
             patch.object(main.requests, 'post', return_value=stk_response) as stk, \
             patch.object(main, 'quote_cart_delivery', return_value={'total_amount':0, 'lines':[]}):
            response = client.post('/checkout', base_url=base, headers=checkout_headers, data={
                'phone':'0712345678', 'phone_country_code':'+254',
                'shipping_country':'Kenya', 'shipping_city':'Nairobi', 'shipping_address':'Test address'})
            assert response.status_code == 200, response.location
            assert b'Check your phone' in response.data
            assert oauth.call_args.args[0].startswith('https://api.safaricom.co.ke/')
            assert stk.call_args.args[0] == 'https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest'
            assert stk.call_args.kwargs['json']['BusinessShortCode'] == '600123'
        print('PASS: saved production settings, HTTPS shipping quote, production STK submission (mocked)')
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


if __name__ == '__main__':
    run()
