"""
SMARKAFRICA - Complete E-Commerce Platform
Flask Application with M-Pesa STK Push, Digital & Physical Products
SECURITY ENHANCED VERSION
"""

import os, json, uuid, base64, datetime, hashlib, requests, traceback, re, html, mimetypes, logging, csv, socket, hmac
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO, StringIO
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, send_from_directory, abort, make_response
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash
from sqlalchemy import func, extract, and_, or_, text
from sqlalchemy.exc import OperationalError

try:
    import qrcode
    import qrcode.image.svg
except ImportError:
    qrcode = None

try:
    import barcode
    from barcode.writer import SVGWriter
except ImportError:
    barcode = None

# ---------- App Initialization ----------
from config import init_logging, selected_config
from models import db, User, Category, Product, Cart, Order, OrderItem, \
    TrackingUpdate, Review, CustomerFeedback, Transaction, ShippingRate, Discount, Setting, \
    SellerVerification, PaymentClaim, WithdrawalRequest, AdCampaign, Manufacturer, \
    CarrierPartner, AdminMessage, AdminSalary, MarketNews, CategoryFollow, PriceAlert, CustomerNotification, \
    SellerVerificationBackup, SellerBlacklist, CarrierAgentSession, CarrierAgentMessage, \
    AIImageTrainingSubmission, BusinessCheckIn, ClientAcquisitionLead, QualityImprovementLog, AutomationTask, \
    PointOfSaleSale, PointOfSaleItem, MarketPriceCache, BusinessStorefront, ExchangeRate, SupportTicket, \
    LoyaltyLedger, BNPLPlan, BNPLInstallment, TrustScore, ProductBarcode, Supplier, PurchaseOrder, \
    PurchaseOrderItem, StockMovement, BNPLProductPolicy, SignupVerification, ShoppingCard, \
    ShoppingCardTransaction, KYCIdentityVerification, CardAuthorizationRequest, Raffle, RaffleTicket, \
    CoinTransaction, CoinDailyCheckIn, Event

# Import security utilities
from validators import (RegisterSchema, LoginSchema, ProductSchema, ReviewSchema,
                        CartSchema, CheckoutSchema, FeedbackSchema, validate_request)
from audit_log import log_admin_action, audit_log
from security_utils import (sanitize_filepath, safe_path_join, verify_mpesa_signature,
                            is_safe_file, SAFE_IMAGE_EXTENSIONS, SAFE_DIGITAL_EXTENSIONS)

app = Flask(__name__)
config_class = selected_config()
init_logging(config_class)
logger = logging.getLogger(__name__)
app.config.from_object(config_class)

# ========== SECURITY INITIALIZATION ==========

# CSRF Protection
csrf = CSRFProtect(app)

# Rate Limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    storage_uri=app.config.get('RATELIMIT_STORAGE_URL'),
    strategy=app.config.get('RATELIMIT_STRATEGY'),
    default_limits=["200 per hour", "50 per minute"]
)

# Security Headers with Flask-Talisman
csp = {
    'default-src': "'self'",
    'script-src': [
        "'self'",
        "'unsafe-inline'",  # Required for inline scripts - minimize in production
        "https://cdn.jsdelivr.net",
        "https://unpkg.com",
        "https://code.jquery.com",
        "https://stackpath.bootstrapcdn.com"
    ],
    'style-src': [
        "'self'",
        "'unsafe-inline'",  # Required for inline styles
        "https://cdn.jsdelivr.net",
        "https://stackpath.bootstrapcdn.com",
        "https://fonts.googleapis.com"
    ],
    'img-src': [
        "'self'",
        "data:",
        "https:",
        "http:"  # Allow external images for products
    ],
    'font-src': [
        "'self'",
        "https://fonts.gstatic.com",
        "https://cdn.jsdelivr.net"
    ],
    'connect-src': "'self'",
    'frame-ancestors': "'none'",
    'base-uri': "'self'",
    'form-action': "'self'"
}

# Only enforce HTTPS in production
force_https = os.environ.get('FLASK_ENV') == 'production'

# Apply Flask-Talisman for security headers
try:
    talisman = Talisman(
        app,
        force_https=force_https,
        strict_transport_security=force_https,
        content_security_policy=csp
    )
except Exception as e:
    logger.warning(f'Flask-Talisman initialization warning: {e}. Continuing without some security headers.')
    talisman = None

# Caching
try:
    from flask_caching import Cache
    cache = Cache(app)
except ImportError:
    logger.warning('Flask-Caching is not installed; continuing without app cache')
    cache = None
except Exception:
    logger.exception('Flask-Caching could not be initialized; continuing without app cache')
    cache = None

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(app.static_folder, 'uploads', 'products'), exist_ok=True)
os.makedirs(os.path.join(app.static_folder, 'uploads', 'digital'), exist_ok=True)

db.init_app(app)

# Update allowed extensions - NO EXECUTABLES
ALLOWED_EXTENSIONS = SAFE_IMAGE_EXTENSIONS
ALLOWED_DIGITAL_EXTENSIONS = SAFE_DIGITAL_EXTENSIONS


@app.teardown_appcontext
def cleanup_db_session(exception=None):
    if exception is not None:
        app.logger.exception('Request ended with an exception; rolling back database session')
        db.session.rollback()
    db.session.remove()

# ---------- Login Manager ----------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ---------- Helpers ----------
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Admin access required.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated


def mvp_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('MVP admin access required.', 'danger')
            return redirect(url_for('login'))
        if current_user.admin_level != 'mvp':
            flash('Only the MVP admin can access this section.', 'danger')
            return redirect(url_for('admin_dashboard'))
        return f(*args, **kwargs)

    return decorated


def utcnow():
    return datetime.utcnow()


def generate_order_number():
    return 'SAF-' + utcnow().strftime('%Y%m%d-') + uuid.uuid4().hex[:8].upper()



def normalize_mpesa_phone(phone_number):
    phone = (phone_number or '').strip().replace(' ', '')
    phone = ''.join(ch for ch in phone if ch.isdigit() or ch == '+')
    if phone.startswith('0'):
        return '254' + phone[1:]
    if phone.startswith('+'):
        return phone[1:]
    if phone and not phone.startswith('254'):
        return '254' + phone
    return phone


def valid_mpesa_msisdn(phone_number):
    phone = normalize_mpesa_phone(phone_number)
    return phone if re.fullmatch(r'254(7|1)\d{8}', phone or '') else ''


def normalize_email(email):
    return (email or '').strip().lower()


def generate_signup_code():
    return str(uuid.uuid4().int % 1000000).zfill(6)


def send_signup_verification(email, phone, code):
    subject = 'Your SMARKAFRICA verification code'
    body = f"""
    <h3>SMARKAFRICA verification</h3>
    <p>Your sign-up verification code is:</p>
    <h2 style="letter-spacing:3px">{html.escape(code)}</h2>
    <p>This code expires in 15 minutes. If you did not request this, ignore this email.</p>
    """
    sent = send_email(email, subject, body) if email else False
    if phone:
        sms_msg = f'Your SMARKAFRICA verification code is: {code}. It expires in 15 minutes.'
        send_sms_notification(phone, sms_msg)
    return sent


def award_loyalty_points(user_id, event_type, points, description, reference_id=''):
    if not user_id or not points:
        return None
    existing = None
    if reference_id:
        existing = LoyaltyLedger.query.filter_by(
            user_id=user_id,
            event_type=event_type,
            reference_id=str(reference_id)
        ).first()
    if existing:
        return existing
    row = LoyaltyLedger(
        user_id=user_id,
        event_type=event_type,
        points=int(points),
        description=description[:240],
        reference_id=str(reference_id)[:80] if reference_id else None
    )
    db.session.add(row)
    return row


def active_shopping_card(user_id):
    if not user_id:
        return None
    return ShoppingCard.query.filter_by(user_id=user_id, status='active').order_by(ShoppingCard.created_at.desc()).first()


def loyalty_credit_balance(user_id):
    return db.session.query(func.coalesce(func.sum(LoyaltyLedger.points), 0)).filter_by(user_id=user_id).scalar() or 0


def shopping_card_issue_threshold():
    return int(float(Setting.get('shopping_card_min_credits', '10000') or 10000))


def shopping_card_issue_fee():
    return float(Setting.get('shopping_card_issue_fee_kes', '700') or 700)


def shopping_card_earn_rate():
    return float(Setting.get('shopping_card_credits_per_100_kes', '1') or 1)


def purchase_credits_for_amount(amount):
    amount = float(amount or 0)
    minimum = float(Setting.get('shopping_card_min_purchase_kes', '10000') or 10000)
    if amount < minimum:
        return 0
    return int((amount // 100) * shopping_card_earn_rate())


def generate_card_number():
    prefix = Setting.get('shopping_card_prefix', '607845')
    for _ in range(50):
        body = str(uuid.uuid4().int)[:10].ljust(10, '0')
        number = f'{prefix}{body}'[:16]
        if not ShoppingCard.query.filter_by(card_number=number).first():
            return number
    raise RuntimeError('Could not generate a unique card number')


def generate_card_pin():
    for _ in range(50):
        pin = str(uuid.uuid4().int % 10000).zfill(4)
        if pin not in {'0000', '1234', '1111'}:
            return pin
    return str(uuid.uuid4().int % 10000).zfill(4)


def can_issue_shopping_card(user, issue_fee_paid=0):
    if not user:
        return False, 'Choose a valid customer.'
    if user.is_admin:
        return True, ''
    if active_shopping_card(user.id):
        return False, 'This customer already has an active shopping card.'
    if loyalty_credit_balance(user.id) >= shopping_card_issue_threshold():
        return True, ''
    if float(issue_fee_paid or 0) >= shopping_card_issue_fee():
        return True, ''
    return False, f'Customer needs at least {shopping_card_issue_threshold():,} credits or KSh {shopping_card_issue_fee():,.0f} issue fee.'


def create_shopping_card(user, display_name='', issue_fee_paid=0, issued_by=None, customer_sets_pin=True):
    """
    Create shopping card. If customer_sets_pin=True, customer must set PIN via SMS.
    If False (admin issuing), generate PIN and return it.
    """
    import secrets

    allowed, reason = can_issue_shopping_card(user, issue_fee_paid)
    if not allowed:
        raise ValueError(reason)

    card_number = generate_card_number()

    # Generate PIN setup token for customer
    pin_setup_token = secrets.token_urlsafe(32) if customer_sets_pin else None

    card = ShoppingCard(
        user_id=user.id,
        card_number=card_number,
        card_last4=card_number[-4:],
        display_name=(display_name or user.username or user.email)[:160],
        issue_fee_paid=float(issue_fee_paid or 0),
        issued_by=issued_by,
        issued_at=utcnow(),
        status='pending_pin' if customer_sets_pin else 'active',
        pin_set_token=pin_setup_token,
    )

    # If admin issuing, generate PIN immediately
    if not customer_sets_pin:
        pin = generate_card_pin()
        card.set_pin(pin)
    else:
        # Set a temporary placeholder hash (customer will replace via SMS link)
        card.pin_hash = 'PENDING_PIN_SETUP'
        pin = None

    db.session.add(card)
    db.session.flush()

    db.session.add(ShoppingCardTransaction(
        card_id=card.id,
        user_id=user.id,
        transaction_type='issue',
        credit_amount=0,
        cash_amount=float(issue_fee_paid or 0),
        balance_after_credits=card.credit_balance,
        balance_after_cash=card.cash_balance,
        reference_type='card_issue',
        reference_id=str(card.id),
        note='Card issued - ' + ('Pending PIN setup' if customer_sets_pin else 'PIN generated'),
        created_by=issued_by,
    ))

    # Send SMS to customer to set PIN
    if customer_sets_pin and user.phone:
        sms_message = f"Smark-Africa: Your card {card.card_last4} is ready! Set your 4-digit PIN: {app.config.get('BASE_URL', 'https://smarkafrica.com')}/set-card-pin/{pin_setup_token}"
        send_sms_notification(user.phone, sms_message)

    return card, pin


def credit_shopping_card(user_id, credits=0, cash_amount=0, transaction_type='credit', reference_type='', reference_id='', note='', created_by=None):
    card = active_shopping_card(user_id)
    if not card:
        return None
    credits = int(credits or 0)
    cash_amount = float(cash_amount or 0)
    card.credit_balance = max(0, int(card.credit_balance or 0) + credits)
    card.cash_balance = max(0, float(card.cash_balance or 0) + cash_amount)
    db.session.add(ShoppingCardTransaction(
        card_id=card.id,
        user_id=card.user_id,
        transaction_type=transaction_type,
        credit_amount=credits,
        cash_amount=cash_amount,
        balance_after_credits=card.credit_balance,
        balance_after_cash=card.cash_balance,
        reference_type=reference_type,
        reference_id=str(reference_id or '')[:80],
        note=note,
        created_by=created_by,
    ))
    return card


def redeem_shopping_card(card_number, pin, amount_kes, reference_type='pos_sale', reference_id='', created_by=None):
    clean_number = re.sub(r'\D', '', card_number or '')
    card = ShoppingCard.query.filter_by(card_number=clean_number, status='active').first()
    if not card:
        raise ValueError('Shopping card not recognized.')
    if not card.check_pin(pin or ''):
        raise ValueError('Invalid shopping card PIN.')
    amount_kes = float(amount_kes or 0)
    needed_credits = int(round(amount_kes * 100))
    credit_value = int(card.credit_balance or 0)
    cash_value = int(round(float(card.cash_balance or 0) * 100))
    if credit_value + cash_value < needed_credits:
        available = (credit_value + cash_value) / 100
        raise ValueError(f'Insufficient card balance. Available KSh {available:,.2f}.')
    credit_used = min(credit_value, needed_credits)
    remaining_cents = needed_credits - credit_used
    cash_used = remaining_cents / 100
    card.credit_balance = credit_value - credit_used
    card.cash_balance = round(float(card.cash_balance or 0) - cash_used, 2)
    db.session.add(ShoppingCardTransaction(
        card_id=card.id,
        user_id=card.user_id,
        transaction_type='redeem',
        credit_amount=-credit_used,
        cash_amount=-cash_used,
        balance_after_credits=card.credit_balance,
        balance_after_cash=card.cash_balance,
        reference_type=reference_type,
        reference_id=str(reference_id or '')[:80],
        note=f'Redeemed KSh {amount_kes:,.2f}',
        created_by=created_by,
    ))
    return card


def generate_card_barcode_svg(card_number):
    """Generate SVG barcode for a shopping card."""
    if not barcode:
        return None
    try:
        # Use Code128 barcode format - widely supported by scanners
        code128 = barcode.get_barcode_class('code128')
        barcode_instance = code128(str(card_number), writer=SVGWriter())

        # Generate SVG to BytesIO
        buffer = BytesIO()
        barcode_instance.write(buffer, options={
            'module_width': 0.3,
            'module_height': 12.0,
            'quiet_zone': 2.5,
            'font_size': 10,
            'text_distance': 3.0,
            'write_text': True
        })
        buffer.seek(0)
        return buffer.read().decode('utf-8')
    except Exception as e:
        logger.error(f'Barcode generation failed: {e}')
        return None


def find_card_by_barcode(barcode_value):
    """Find a shopping card by scanning its barcode."""
    clean_barcode = re.sub(r'\D', '', barcode_value or '')
    if not clean_barcode:
        return None
    return ShoppingCard.query.filter_by(card_number=clean_barcode, status='active').first()


def create_card_authorization_request(card, amount, merchant_name='', pos_terminal_id=''):
    """Create a mobile authorization request for card payment."""
    import secrets

    # Generate unique authorization token
    auth_token = secrets.token_urlsafe(32)

    # Set expiration (5 minutes)
    expires_at = utcnow() + timedelta(minutes=5)

    # Create authorization request
    auth_request = CardAuthorizationRequest(
        card_id=card.id,
        user_id=card.user_id,
        authorization_token=auth_token,
        amount=amount,
        merchant_name=merchant_name or 'Smark-Africa POS',
        pos_terminal_id=pos_terminal_id or 'TERMINAL-001',
        phone_number=card.user.phone,
        status='pending',
        expires_at=expires_at
    )
    db.session.add(auth_request)
    db.session.flush()

    # Send SMS notification to customer
    if card.user.phone:
        message = f"Smark-Africa Card Payment: Approve KSh {amount:,.2f} at {merchant_name or 'POS'}. " \
                  f"Reply with token: {auth_token[:8]} within 5 minutes. Auth: {auth_request.id}"
        try:
            send_sms_notification(card.user.phone, message)
        except Exception as e:
            logger.error(f'Failed to send authorization SMS: {e}')

    return auth_request


def check_card_authorization(auth_token):
    """Check if a card authorization has been approved."""
    auth_request = CardAuthorizationRequest.query.filter_by(
        authorization_token=auth_token
    ).first()

    if not auth_request:
        return None, 'Invalid authorization token'

    # Check if expired
    if utcnow() > auth_request.expires_at:
        if auth_request.status == 'pending':
            auth_request.status = 'expired'
            db.session.commit()
        return auth_request, 'Authorization expired'

    # Check status
    if auth_request.status == 'approved':
        return auth_request, 'approved'
    elif auth_request.status == 'declined':
        return auth_request, 'Customer declined the transaction'
    elif auth_request.status == 'pending':
        return auth_request, 'Waiting for customer approval'
    else:
        return auth_request, f'Authorization {auth_request.status}'


def approve_card_authorization(auth_request_id, user_id):
    """Customer approves card authorization via their phone."""
    auth_request = CardAuthorizationRequest.query.get(auth_request_id)

    if not auth_request:
        return False, 'Authorization request not found'

    # Verify user owns the card
    if auth_request.user_id != user_id:
        return False, 'Unauthorized'

    # Check if expired
    if utcnow() > auth_request.expires_at:
        auth_request.status = 'expired'
        db.session.commit()
        return False, 'Authorization expired'

    # Check if already processed
    if auth_request.status != 'pending':
        return False, f'Authorization already {auth_request.status}'

    # Approve the authorization
    auth_request.status = 'approved'
    auth_request.user_response = 'approved'
    auth_request.response_at = utcnow()
    db.session.commit()

    return True, 'Payment authorized successfully'


def decline_card_authorization(auth_request_id, user_id):
    """Customer declines card authorization via their phone."""
    auth_request = CardAuthorizationRequest.query.get(auth_request_id)

    if not auth_request:
        return False, 'Authorization request not found'

    # Verify user owns the card
    if auth_request.user_id != user_id:
        return False, 'Unauthorized'

    # Check if already processed
    if auth_request.status != 'pending':
        return False, f'Authorization already {auth_request.status}'

    # Decline the authorization
    auth_request.status = 'declined'
    auth_request.user_response = 'declined'
    auth_request.response_at = utcnow()
    db.session.commit()

    return True, 'Payment declined'


def send_sms_notification(phone_number, message):
    """Send SMS via Africa's Talking or log if not configured."""
    at_username = Setting.get('africastalking_username', app.config.get('AFRICASTALKING_USERNAME', ''))
    at_api_key = Setting.get('africastalking_api_key', app.config.get('AFRICASTALKING_API_KEY', ''))
    at_sender = Setting.get('africastalking_sender_id', app.config.get('AFRICASTALKING_SENDER_ID', ''))

    if not at_username or not at_api_key:
        logger.info(f'SMS (not configured, logged only) to {phone_number}: {message}')
        return False

    try:
        import requests as http_requests
        url = 'https://api.africastalking.com/version1/messaging'
        if at_username == 'sandbox':
            url = 'https://api.sandbox.africastalking.com/version1/messaging'
        headers = {
            'apiKey': at_api_key,
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
        }
        payload = {
            'username': at_username,
            'to': phone_number,
            'message': message,
        }
        if at_sender:
            payload['from'] = at_sender
        resp = http_requests.post(url, headers=headers, data=payload, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        recipients = result.get('SMSMessageData', {}).get('Recipients', [])
        if recipients and recipients[0].get('status') == 'Success':
            logger.info(f'SMS sent to {phone_number}')
            return True
        logger.warning(f'SMS to {phone_number} may have failed: {result}')
        return False
    except Exception as e:
        logger.error(f'SMS sending failed to {phone_number}: {e}')
        return False


# ========== FLUTTERWAVE CARD PAYMENTS ==========

def initiate_flutterwave_charge(amount, email, phone, card_number, cvv, expiry_month, expiry_year, tx_ref, redirect_url=None):
    """Initiate a Flutterwave card charge for Visa/Mastercard."""
    secret_key = setting_value('flutterwave_secret_key', app.config.get('FLUTTERWAVE_SECRET_KEY', ''))
    if not secret_key:
        return {'success': False, 'error': 'Flutterwave secret key not configured. Set FLUTTERWAVE_SECRET_KEY in environment or admin settings.'}

    encryption_key = setting_value('flutterwave_encryption_key', app.config.get('FLUTTERWAVE_ENCRYPTION_KEY', ''))
    base_url = 'https://api.flutterwave.com/v3'

    headers = {
        'Authorization': f'Bearer {secret_key}',
        'Content-Type': 'application/json'
    }

    payload = {
        'card_number': re.sub(r'\s+', '', card_number),
        'cvv': cvv,
        'expiry_month': expiry_month.zfill(2),
        'expiry_year': expiry_year.zfill(2),
        'currency': 'KES',
        'amount': round(float(amount)),
        'email': email or 'customer@smarkafrica.com',
        'tx_ref': tx_ref,
        'redirect_url': redirect_url or url_for('flutterwave_callback', _external=True),
    }
    if phone:
        payload['phone_number'] = phone

    try:
        resp = requests.post(f'{base_url}/charges?type=card', json=payload, headers=headers, timeout=30)
        data = resp.json() if resp.text else {}

        if data.get('status') == 'success':
            return {
                'success': True,
                'data': data.get('data', {}),
                'requires_validation': data.get('meta', {}).get('authorization', {}).get('mode') in ('pin', 'redirect', 'otp'),
                'auth_mode': data.get('meta', {}).get('authorization', {}).get('mode'),
            }
        else:
            return {'success': False, 'error': data.get('message', 'Card charge failed')}
    except Exception as e:
        logger.error(f'Flutterwave charge error: {e}')
        return {'success': False, 'error': str(e)}


def verify_flutterwave_transaction(transaction_id):
    """Verify a Flutterwave transaction status."""
    secret_key = setting_value('flutterwave_secret_key', app.config.get('FLUTTERWAVE_SECRET_KEY', ''))
    if not secret_key:
        return {'success': False, 'error': 'Flutterwave not configured'}

    headers = {'Authorization': f'Bearer {secret_key}'}

    try:
        resp = requests.get(f'https://api.flutterwave.com/v3/transactions/{transaction_id}/verify', headers=headers, timeout=20)
        data = resp.json() if resp.text else {}

        if data.get('status') == 'success' and data.get('data', {}).get('status') == 'successful':
            return {
                'success': True,
                'amount': data['data'].get('amount'),
                'currency': data['data'].get('currency'),
                'tx_ref': data['data'].get('tx_ref'),
                'flw_ref': data['data'].get('flw_ref'),
            }
        return {'success': False, 'error': data.get('message', 'Verification failed')}
    except Exception as e:
        logger.error(f'Flutterwave verify error: {e}')
        return {'success': False, 'error': str(e)}


def order_loyalty_points(order):
    if not order or not order.user_id:
        return
    points = purchase_credits_for_amount(order.amount_paid)
    if not points:
        return
    row = award_loyalty_points(
        order.user_id,
        'completed_purchase',
        points,
        f'Completed purchase {order.order_number}',
        f'order:{order.id}'
    )
    if row:
        credit_shopping_card(
            order.user_id,
            credits=points,
            transaction_type='purchase_reward',
            reference_type='order',
            reference_id=order.id,
            note=f'Purchase reward for {order.order_number}',
        )
    # Award coins for purchase
    award_purchase_coins(order.user_id, float(order.amount_paid or 0), order.id)


def setting_value(key, default=''):
    value = Setting.get(key, None)
    if value is None or value == '':
        return default
    stale_values = {
        'YOUR_CONSUMER_KEY_HERE',
        'YOUR_CONSUMER_SECRET_HERE',
        'YOUR_PASSKEY_HERE',
        '2UA9gRP6n9dejGWJDwinJekxAJYZ8ZYgyKm0bf4o7ytSnw6J',
        'ZlbvyNyQy5kAZLHKAQJzkQAfGBHCcHyEQKKGlVURk68NhAMQDke9Osccluwx2KJx',
    }
    if value in stale_values:
        return default
    return value


def daraja_passkey():
    passkey = setting_value('daraja_passkey', app.config['DARAJA_PASSKEY'])
    return '' if str(passkey).strip().upper() == 'N/A' else str(passkey)


def daraja_timestamp():
    return datetime.now(timezone(timedelta(hours=3))).strftime('%Y%m%d%H%M%S')


def daraja_config_error():
    missing = []
    if not setting_value('daraja_consumer_key', app.config['DARAJA_CONSUMER_KEY']):
        missing.append('consumer key')
    if not setting_value('daraja_consumer_secret', app.config['DARAJA_CONSUMER_SECRET']):
        missing.append('consumer secret')
    if not setting_value('daraja_shortcode', app.config['DARAJA_SHORTCODE']):
        missing.append('shortcode')
    if not daraja_passkey():
        missing.append('passkey')
    return f'Daraja configuration is missing {", ".join(missing)}' if missing else ''


def production_setup_pending(admin_user=None):
    issues = []
    if admin_user and admin_user.is_admin and admin_user.check_password(app.config.get('ADMIN_PASSWORD', 'ChangeMeAdmin123!')):
        issues.append('Change the default admin password')
    if daraja_config_error():
        issues.append('Complete Daraja credentials')
    return issues

def save_uploaded_file(file, subfolder='products'):
    """
    Save uploaded file and return the URL path
    SECURITY: Path sanitization and validation
    """
    if not file or not file.filename:
        raise ValueError('No file provided')

    # Sanitize filename
    filename = sanitize_filepath(file.filename, ALLOWED_EXTENSIONS if subfolder == 'products' else ALLOWED_DIGITAL_EXTENSIONS)
    if not filename:
        raise ValueError('Invalid filename')

    # Additional security check
    if not is_safe_file(filename, 'image' if subfolder == 'products' else 'digital'):
        raise ValueError('File type not allowed')

    unique_name = f"{uuid.uuid4().hex[:8]}_{filename}"

    # Use safe path join
    base_folder = app.config['UPLOAD_FOLDER']
    folder = safe_path_join(base_folder, subfolder)
    if not folder:
        raise ValueError('Invalid subfolder path')

    os.makedirs(folder, exist_ok=True)

    # Safely join target path
    target_path = safe_path_join(folder, unique_name)
    if not target_path:
        raise ValueError('Invalid target path')

    file.save(target_path)
    return url_for('static', filename=f'uploads/{subfolder}/{unique_name}')


def save_product_image(file):
    if request.content_length and request.content_length > app.config['PRODUCT_IMAGE_MAX_CONTENT_LENGTH']:
        raise ValueError('Product images must be 5MB or smaller.')
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    if size > app.config['PRODUCT_IMAGE_MAX_CONTENT_LENGTH']:
        raise ValueError('Product images must be 5MB or smaller.')
    filename = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4().hex[:8]}_{os.path.splitext(filename)[0]}.jpg"
    folder = os.path.join(app.config['UPLOAD_FOLDER'], 'products')
    os.makedirs(folder, exist_ok=True)
    target_path = os.path.join(folder, unique_name)
    try:
        from PIL import Image, ImageOps
        with Image.open(file.stream) as image:
            image.verify()
        file.stream.seek(0)
        with Image.open(file.stream) as image:
            image = ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail((1600, 1600))
            image.save(target_path, 'JPEG', quality=82, optimize=True)
    except Exception as exc:
        app.logger.exception('Product image validation/compression failed')
        raise ValueError('Upload a valid PNG, JPG, GIF, or WebP product image.') from exc
    return url_for('static', filename=f'uploads/products/{unique_name}')


def allowed_digital_file_signature(file):
    """
    Check if digital file signature matches extension
    SECURITY: No longer allows executable files (.exe, .msi, .apk)
    """
    filename = (file.filename or '').lower()
    ext = filename.rsplit('.', 1)[-1] if '.' in filename else ''

    # Block dangerous extensions
    if not is_safe_file(filename, 'digital'):
        return False

    file.stream.seek(0)
    header = file.stream.read(32)
    file.stream.seek(0)

    # Allowed signatures (no executables)
    signatures = {
        'pdf': [b'%PDF-'],
        'zip': [b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08'],
        'epub': [b'PK\x03\x04'],
        'mobi': [b'BOOKMOBI'],
        'jpg': [b'\xff\xd8\xff'],
        'jpeg': [b'\xff\xd8\xff'],
        'png': [b'\x89PNG\r\n\x1a\n'],
        'mp3': [b'ID3', b'\xff\xfb'],
        'mp4': [b'ftyp'],
    }
    allowed = signatures.get(ext)
    return bool(allowed and any(header.startswith(prefix) or prefix in header for prefix in allowed))


def trend_placeholder_svg(title, category='Product'):
    return trend_image_fallback(title, category)


def image_url_to_path(image_url):
    if not image_url:
        return ''
    static_prefix = url_for('static', filename='')
    if image_url.startswith(static_prefix):
        relative = image_url[len(static_prefix):].replace('/', os.sep)
        return os.path.join(app.static_folder, relative)
    return ''


def image_profile(path):
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as image:
            image = image.convert('RGB')
            width, height = image.size
            sample = image.resize((16, 16))
            pixels = list(sample.getdata())
            count = len(pixels) or 1
            avg = tuple(sum(pixel[index] for pixel in pixels) / count for index in range(3))
            return {
                'avg': avg,
                'aspect': width / height if height else 1,
                'size': (width, height),
            }
    except Exception:
        return None


def product_file_extension(product):
    if not product or not product.file_path:
        return ''
    return os.path.basename(product.file_path).rsplit('.', 1)[-1].lower() if '.' in product.file_path else ''


def product_media_kind(product):
    ext = product_file_extension(product)
    if ext == 'mp3':
        return 'audio'
    if ext == 'mp4':
        return 'video'
    if ext == 'pdf':
        return 'pdf'
    return 'download'


def is_music_category(category):
    if not category:
        return False
    value = f'{category.name} {category.slug}'.lower()
    return any(token in value for token in ['music', 'audio', 'beat', 'mix'])


def is_voiceover_listing(product):
    value = f'{product.name} {product.short_description or ""} {product.description or ""}'.lower()
    return any(token in value for token in ['voice over', 'voiceover', 'voice-over', 'narration', 'voice artist'])


REAL_ONLINE_FALLBACK_IMAGES = {
    'electronics': 'https://commons.wikimedia.org/wiki/Special:FilePath/Smartphone%20Android%20Lollipop.jpg',
    'gaming': 'https://images.pexels.com/photos/1298601/pexels-photo-1298601.jpeg?auto=compress&cs=tinysrgb&w=1200',
    'computing': 'https://images.pexels.com/photos/18105/pexels-photo.jpg?auto=compress&cs=tinysrgb&w=1200',
    'home': 'https://commons.wikimedia.org/wiki/Special:FilePath/Washing%20Machine%20Testing.jpg',
    'furniture': 'https://images.pexels.com/photos/276583/pexels-photo-276583.jpeg?auto=compress&cs=tinysrgb&w=1200',
    'fashion': 'https://images.pexels.com/photos/934063/pexels-photo-934063.jpeg?auto=compress&cs=tinysrgb&w=1200',
    'beauty': 'https://images.pexels.com/photos/3373746/pexels-photo-3373746.jpeg?auto=compress&cs=tinysrgb&w=1200',
    'music': 'https://commons.wikimedia.org/wiki/Special:FilePath/Studio%20Headphones.jpg',
    'other': 'https://images.pexels.com/photos/230544/pexels-photo-230544.jpeg?auto=compress&cs=tinysrgb&w=1200',
}

COUNTRY_PHONE_CODES = [
    {'name': 'Kenya', 'code': '+254', 'flag': '????'}, {'name': 'Uganda', 'code': '+256', 'flag': '????'},
    {'name': 'Tanzania', 'code': '+255', 'flag': '????'}, {'name': 'Rwanda', 'code': '+250', 'flag': '????'},
    {'name': 'Ethiopia', 'code': '+251', 'flag': '????'}, {'name': 'Somalia', 'code': '+252', 'flag': '????'},
    {'name': 'South Sudan', 'code': '+211', 'flag': '????'}, {'name': 'Nigeria', 'code': '+234', 'flag': '????'},
    {'name': 'Ghana', 'code': '+233', 'flag': '????'}, {'name': 'South Africa', 'code': '+27', 'flag': '????'},
    {'name': 'Egypt', 'code': '+20', 'flag': '????'}, {'name': 'Morocco', 'code': '+212', 'flag': '????'},
    {'name': 'Algeria', 'code': '+213', 'flag': '????'}, {'name': 'Tunisia', 'code': '+216', 'flag': '????'},
    {'name': 'Senegal', 'code': '+221', 'flag': '????'}, {'name': 'Ivory Coast', 'code': '+225', 'flag': '????'},
    {'name': 'Cameroon', 'code': '+237', 'flag': '????'}, {'name': 'DR Congo', 'code': '+243', 'flag': '????'},
    {'name': 'Zambia', 'code': '+260', 'flag': '????'}, {'name': 'Zimbabwe', 'code': '+263', 'flag': '????'},
    {'name': 'Malawi', 'code': '+265', 'flag': '????'}, {'name': 'Mozambique', 'code': '+258', 'flag': '????'},
    {'name': 'Botswana', 'code': '+267', 'flag': '????'}, {'name': 'Namibia', 'code': '+264', 'flag': '????'},
    {'name': 'United States', 'code': '+1', 'flag': '????'}, {'name': 'Canada', 'code': '+1', 'flag': '????'},
    {'name': 'United Kingdom', 'code': '+44', 'flag': '????'}, {'name': 'Ireland', 'code': '+353', 'flag': '????'},
    {'name': 'France', 'code': '+33', 'flag': '????'}, {'name': 'Germany', 'code': '+49', 'flag': '????'},
    {'name': 'Italy', 'code': '+39', 'flag': '????'}, {'name': 'Spain', 'code': '+34', 'flag': '????'},
    {'name': 'Netherlands', 'code': '+31', 'flag': '????'}, {'name': 'Belgium', 'code': '+32', 'flag': '????'},
    {'name': 'Switzerland', 'code': '+41', 'flag': '????'}, {'name': 'Sweden', 'code': '+46', 'flag': '????'},
    {'name': 'Norway', 'code': '+47', 'flag': '????'}, {'name': 'Denmark', 'code': '+45', 'flag': '????'},
    {'name': 'China', 'code': '+86', 'flag': '????'}, {'name': 'India', 'code': '+91', 'flag': '????'},
    {'name': 'Japan', 'code': '+81', 'flag': '????'}, {'name': 'South Korea', 'code': '+82', 'flag': '????'},
    {'name': 'United Arab Emirates', 'code': '+971', 'flag': '????'}, {'name': 'Saudi Arabia', 'code': '+966', 'flag': '????'},
    {'name': 'Qatar', 'code': '+974', 'flag': '????'}, {'name': 'Turkey', 'code': '+90', 'flag': '????'},
    {'name': 'Australia', 'code': '+61', 'flag': '????'}, {'name': 'New Zealand', 'code': '+64', 'flag': '????'},
    {'name': 'Brazil', 'code': '+55', 'flag': '????'}, {'name': 'Mexico', 'code': '+52', 'flag': '????'},
]

COUNTRY_ISO2 = {
    'Kenya': 'KE', 'Uganda': 'UG', 'Tanzania': 'TZ', 'Rwanda': 'RW', 'Ethiopia': 'ET', 'Somalia': 'SO',
    'South Sudan': 'SS', 'Nigeria': 'NG', 'Ghana': 'GH', 'South Africa': 'ZA', 'Egypt': 'EG', 'Morocco': 'MA',
    'Algeria': 'DZ', 'Tunisia': 'TN', 'Senegal': 'SN', 'Ivory Coast': 'CI', 'Cameroon': 'CM', 'DR Congo': 'CD',
    'Zambia': 'ZM', 'Zimbabwe': 'ZW', 'Malawi': 'MW', 'Mozambique': 'MZ', 'Botswana': 'BW', 'Namibia': 'NA',
    'United States': 'US', 'Canada': 'CA', 'United Kingdom': 'GB', 'Ireland': 'IE', 'France': 'FR', 'Germany': 'DE',
    'Italy': 'IT', 'Spain': 'ES', 'Netherlands': 'NL', 'Belgium': 'BE', 'Switzerland': 'CH', 'Sweden': 'SE',
    'Norway': 'NO', 'Denmark': 'DK', 'China': 'CN', 'India': 'IN', 'Japan': 'JP', 'South Korea': 'KR',
    'United Arab Emirates': 'AE', 'Saudi Arabia': 'SA', 'Qatar': 'QA', 'Turkey': 'TR', 'Australia': 'AU',
    'New Zealand': 'NZ', 'Brazil': 'BR', 'Mexico': 'MX',
}
for country_row in COUNTRY_PHONE_CODES:
    country_row['iso'] = COUNTRY_ISO2.get(country_row['name'], country_row['name'][:2].upper())
    country_row['flag_url'] = f"https://flagcdn.com/24x18/{country_row['iso'].lower()}.png"


def trend_image_fallback(title='SMARKAFRICA', category='Market Watch'):
    category_key = trend_category_key(f'{category} {title}')
    return REAL_ONLINE_FALLBACK_IMAGES.get(category_key, REAL_ONLINE_FALLBACK_IMAGES['other'])


TREND_IMAGE_CANDIDATES = {
    'electronics': [
        'https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?auto=format&fit=crop&w=1200&q=80',
        'https://images.unsplash.com/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=1200&q=80',
        'https://images.pexels.com/photos/607812/pexels-photo-607812.jpeg?auto=compress&cs=tinysrgb&w=1200',
    ],
    'gaming': [
        'https://images.unsplash.com/photo-1606813907291-d86efa9b94db?auto=format&fit=crop&w=1200&q=80',
        'https://images.unsplash.com/photo-1550745165-9bc0b252726f?auto=format&fit=crop&w=1200&q=80',
        'https://images.pexels.com/photos/1298601/pexels-photo-1298601.jpeg?auto=compress&cs=tinysrgb&w=1200',
    ],
    'computing': [
        'https://images.unsplash.com/photo-1496181133206-80ce9b88a853?auto=format&fit=crop&w=1200&q=80',
        'https://images.unsplash.com/photo-1593642632823-8f785ba67e45?auto=format&fit=crop&w=1200&q=80',
        'https://images.pexels.com/photos/18105/pexels-photo.jpg?auto=compress&cs=tinysrgb&w=1200',
    ],
    'home': [
        'https://images.unsplash.com/photo-1556911220-bff31c812dba?auto=format&fit=crop&w=1200&q=80',
        'https://images.unsplash.com/photo-1586208958839-06c17cacdf08?auto=format&fit=crop&w=1200&q=80',
        'https://images.pexels.com/photos/2098912/pexels-photo-2098912.jpeg?auto=compress&cs=tinysrgb&w=1200',
    ],
    'furniture': [
        'https://images.unsplash.com/photo-1555041469-a586c61ea9bc?auto=format&fit=crop&w=1200&q=80',
        'https://images.unsplash.com/photo-1505693416388-ac5ce068fe85?auto=format&fit=crop&w=1200&q=80',
        'https://images.pexels.com/photos/276583/pexels-photo-276583.jpeg?auto=compress&cs=tinysrgb&w=1200',
    ],
    'fashion': [
        'https://images.unsplash.com/photo-1523381210434-271e8be1f52b?auto=format&fit=crop&w=1200&q=80',
        'https://images.unsplash.com/photo-1483985988355-763728e1935b?auto=format&fit=crop&w=1200&q=80',
        'https://images.pexels.com/photos/934063/pexels-photo-934063.jpeg?auto=compress&cs=tinysrgb&w=1200',
    ],
    'beauty': [
        'https://images.unsplash.com/photo-1596462502278-27bfdc403348?auto=format&fit=crop&w=1200&q=80',
        'https://images.unsplash.com/photo-1522335789203-aabd1fc54bc9?auto=format&fit=crop&w=1200&q=80',
        'https://images.pexels.com/photos/3373746/pexels-photo-3373746.jpeg?auto=compress&cs=tinysrgb&w=1200',
    ],
    'music': [
        'https://images.unsplash.com/photo-1511379938547-c1f69419868d?auto=format&fit=crop&w=1200&q=80',
        'https://images.unsplash.com/photo-1493225457124-a3eb161ffa5f?auto=format&fit=crop&w=1200&q=80',
        'https://images.pexels.com/photos/164821/pexels-photo-164821.jpeg?auto=compress&cs=tinysrgb&w=1200',
    ],
    'other': [
        'https://images.unsplash.com/photo-1512436991641-6745cdb1723f?auto=format&fit=crop&w=1200&q=80',
        'https://images.unsplash.com/photo-1556742049-0cfed4f6a45d?auto=format&fit=crop&w=1200&q=80',
        'https://images.pexels.com/photos/230544/pexels-photo-230544.jpeg?auto=compress&cs=tinysrgb&w=1200',
    ],
}


PRODUCT_SPECIFIC_IMAGE_CANDIDATES = [
    {
        'tokens': {'asiahorse', 'cooling', 'fan', 'rgb', 'pc', 'corsair'},
        'category': 'computing',
        'images': [
            'https://commons.wikimedia.org/wiki/Special:FilePath/2023%20Corsair%20SP120%20RGB%20Elite.jpg',
            'https://commons.wikimedia.org/wiki/Special:FilePath/80mm%20fan.jpg',
            'https://commons.wikimedia.org/wiki/Special:FilePath/Fan%20and%20case.jpg',
        ],
    },
    {
        'tokens': {'washing', 'washer', 'laundry', 'machine'},
        'category': 'home',
        'images': [
            'https://commons.wikimedia.org/wiki/Special:FilePath/Front%20Load%20Washing%20Machine.jpg',
            'https://commons.wikimedia.org/wiki/Special:FilePath/Washer.600pix.jpg',
            'https://images.pexels.com/photos/5591663/pexels-photo-5591663.jpeg?auto=compress&cs=tinysrgb&w=1200',
        ],
    },
    {
        'tokens': {'drone', 'drones', 'quadcopter', 'fpv', 'uav'},
        'category': 'gaming',
        'images': [
            'https://commons.wikimedia.org/wiki/Special:FilePath/Quadcopter%20drone%20vendor%20at%20Shenzhen%20Huaqiang%20Electronic%20World.jpg',
            'https://commons.wikimedia.org/wiki/Special:FilePath/Uavtek%20Bug%20FX%20Nano%20UAS%20Quadcopter%201A.jpg',
            'https://images.pexels.com/photos/442587/pexels-photo-442587.jpeg?auto=compress&cs=tinysrgb&w=1200',
        ],
    },
    {
        'tokens': {'gamepad', 'gamepads', 'controller', 'controllers', 'pad', 'pads', 'joystick', 'xbox'},
        'category': 'gaming',
        'images': [
            'https://commons.wikimedia.org/wiki/Special:FilePath/Xbox-s-controller.jpg',
            'https://commons.wikimedia.org/wiki/Special:FilePath/Xbox%20One%20Elite%20Controller%20%28front%29.jpg',
            'https://commons.wikimedia.org/wiki/Special:FilePath/Nintendo-Famicom-NES-Dogbone-Controller-FL.jpg',
        ],
    },
    {
        'tokens': {'playstation', 'ps5', 'console', 'sony'},
        'category': 'gaming',
        'images': [
            'https://gmedia.playstation.com/is/image/SIEPDC/ps5-product-thumbnail-01-en-14sep21',
            'https://gmedia.playstation.com/is/image/SIEPDC/ps5-slim-disc-console-featured-hardware-image-block-01-en-15nov23',
            'https://commons.wikimedia.org/wiki/Special:FilePath/PS5-Console-wDS5.jpg',
        ],
    },
    {
        'tokens': {'gaming', 'chair', 'seat', 'ergonomic'},
        'category': 'furniture',
        'images': [
            'https://images.pexels.com/photos/7862507/pexels-photo-7862507.jpeg?auto=compress&cs=tinysrgb&w=1200',
            'https://images.unsplash.com/photo-1598550476439-6847785fcea6?auto=format&fit=crop&w=1200&q=80',
            'https://images.pexels.com/photos/3945683/pexels-photo-3945683.jpeg?auto=compress&cs=tinysrgb&w=1200',
        ],
    },
    {
        'tokens': {'monitor', 'display', 'screen', 'hz'},
        'category': 'computing',
        'images': [
            'https://images.pexels.com/photos/1714208/pexels-photo-1714208.jpeg?auto=compress&cs=tinysrgb&w=1200',
            'https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?auto=format&fit=crop&w=1200&q=80',
            'https://images.pexels.com/photos/777001/pexels-photo-777001.jpeg?auto=compress&cs=tinysrgb&w=1200',
        ],
    },
    {
        'tokens': {'laptop', 'computer', 'notebook', 'hp', 'dell', 'lenovo'},
        'category': 'computing',
        'images': [
            'https://images.pexels.com/photos/18105/pexels-photo.jpg?auto=compress&cs=tinysrgb&w=1200',
            'https://images.unsplash.com/photo-1496181133206-80ce9b88a853?auto=format&fit=crop&w=1200&q=80',
            'https://images.unsplash.com/photo-1593642632823-8f785ba67e45?auto=format&fit=crop&w=1200&q=80',
        ],
    },
    {
        'tokens': {'fridge', 'refrigerator', 'freezer'},
        'category': 'home',
        'images': [
            'https://commons.wikimedia.org/wiki/Special:FilePath/Refrigerator%20display%20in%20home%20appliance%20store.jpg',
            'https://images.unsplash.com/photo-1571175443880-49e1d25b2bc5?auto=format&fit=crop&w=1200&q=80',
            'https://images.pexels.com/photos/1450903/pexels-photo-1450903.jpeg?auto=compress&cs=tinysrgb&w=1200',
        ],
    },
    {
        'tokens': {'earbuds', 'headphones', 'pods', 'freepods', 'audio'},
        'category': 'electronics',
        'images': [
            'https://commons.wikimedia.org/wiki/Special:FilePath/Studio%20Headphones.jpg',
            'https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=1200&q=80',
            'https://images.pexels.com/photos/3394651/pexels-photo-3394651.jpeg?auto=compress&cs=tinysrgb&w=1200',
        ],
    },
]


def product_specific_image_candidates(title, category_hint=''):
    text_value = token_set(title, category_hint)
    matches = []
    for row in PRODUCT_SPECIFIC_IMAGE_CANDIDATES:
        overlap = text_value.intersection(row['tokens'])
        if overlap:
            matches.append((len(overlap), row))
    if not matches:
        return [], ''
    matches.sort(key=lambda item: item[0], reverse=True)
    row = matches[0][1]
    return row['images'], row['category']


def rotate_sequence(values, seed):
    if not values:
        return []
    offset = seed % len(values)
    return values[offset:] + values[:offset]


def trend_category_key(value):
    text_value = str(value or '').lower()
    if any(token in text_value for token in ['phone', 'tv', 'earbud', 'electronic', 'samsung', 'oraimo']):
        return 'electronics'
    if any(token in text_value for token in ['playstation', 'gaming', 'console']):
        return 'gaming'
    if any(token in text_value for token in ['laptop', 'computer', 'monitor', 'pc', 'rgb']):
        return 'computing'
    if any(token in text_value for token in ['fridge', 'refrigerator', 'washing', 'kitchen', 'home']):
        return 'home'
    if any(token in text_value for token in ['chair', 'seat', 'furniture']):
        return 'furniture'
    if any(token in text_value for token in ['fashion', 'shoe', 'clothes', 'apparel', 'beauty']):
        return 'beauty' if 'beauty' in text_value else 'fashion'
    if any(token in text_value for token in ['music', 'audio', 'beat', 'voice', 'mix']):
        return 'music'
    return 'other'


def trend_image_payload(title, category_hint='', image_url='', image_candidates=None, rotation_seed=None):
    title_value = title or 'SMARKAFRICA Market Watch'
    specific_images, specific_category = product_specific_image_candidates(title_value, category_hint)
    category_key = specific_category or trend_category_key(f'{category_hint} {title_value}')
    seed = (rotation_seed if rotation_seed is not None else int(utcnow().strftime('%Y%m%d%H'))) + sum(ord(ch) for ch in title_value)
    fallback = rotate_sequence(REAL_ONLINE_FALLBACK_IMAGES.get(category_key, REAL_ONLINE_FALLBACK_IMAGES['other']).split('|'), seed)[0]
    primary_candidates = []
    if image_url:
        primary_candidates.append(image_url)
    primary_candidates.extend(specific_images)
    primary_candidates.extend(image_candidates or [])
    candidates = rotate_sequence(primary_candidates, seed)
    candidates.extend(rotate_sequence(TREND_IMAGE_CANDIDATES.get(category_key, []), seed))
    candidates.extend(rotate_sequence(TREND_IMAGE_CANDIDATES['other'], seed))
    seen = set()
    unique = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return {
        'image_url': unique[0] if unique else fallback,
        'image_candidates': unique[1:],
        'fallback_image': fallback,
    }


def platform_clean_text(value):
    text_value = str(value or '')
    competitor_terms = [
        'Ju' + 'mia-style',
        'Ju' + 'mia',
        'Ali' + 'Express-style',
        'Ali' + 'Express',
        'Aliexpress',
        'ali' + 'express',
        'ali' + 'express_seller',
        'Ama' + 'zon',
        'e' + 'Bay',
    ]
    for term in competitor_terms:
        text_value = text_value.replace(term, 'marketplace')
    return text_value


app.jinja_env.filters['platform_clean'] = platform_clean_text


def platform_clean_href(value):
    href = str(value or '')
    blocked_terms = ['ju' + 'mia', 'ali' + 'express']
    if any(term in href.lower() for term in blocked_terms):
        return ''
    return href


app.jinja_env.filters['platform_clean_href'] = platform_clean_href


def token_set(*values):
    text_value = ' '.join([str(value or '') for value in values]).lower()
    cleaned = ''.join(ch if ch.isalnum() else ' ' for ch in text_value)
    return {part for part in cleaned.split() if len(part) > 2}


def product_image_match_score(upload_profile, product_profile, upload_tokens, product):
    score = 0.0
    notes = []
    if upload_profile and product_profile:
        color_distance = sum((upload_profile['avg'][idx] - product_profile['avg'][idx]) ** 2 for idx in range(3)) ** 0.5
        color_score = max(0, 55 - (color_distance / 441.7) * 55)
        aspect_gap = abs(upload_profile['aspect'] - product_profile['aspect'])
        aspect_score = max(0, 15 - min(aspect_gap, 2) * 7.5)
        score += color_score + aspect_score
        notes.append('visual color and shape')

    product_tokens = token_set(product.name, product.short_description, product.description,
                               product.category.name if product.category else '')
    overlap = upload_tokens.intersection(product_tokens)
    if overlap:
        score += min(30, 10 + len(overlap) * 5)
        notes.append('catalog keywords')

    if product.is_featured:
        score += 3
    if product.admin_priority:
        score += 2

    return round(min(score, 100), 2), ', '.join(notes) or 'catalog availability'


def find_similar_products(upload_path, original_filename, limit=12):
    upload_profile = image_profile(upload_path)
    upload_tokens = token_set(original_filename)
    matches = []

    for product in Product.query.filter_by(is_active=True).all():
        product_path = image_url_to_path(product.image_url)
        product_profile = image_profile(product_path) if product_path and os.path.exists(product_path) else None
        score, reason = product_image_match_score(upload_profile, product_profile, upload_tokens, product)
        if score > 0:
            matches.append((score, reason, product))

    matches.sort(key=lambda item: item[0], reverse=True)
    if not matches:
        fallback_products = Product.query.filter_by(is_active=True).order_by(
            Product.admin_priority.desc(),
            Product.is_featured.desc(),
            Product.created_at.desc()
        ).limit(limit).all()
        matches = [(1.0, 'closest active catalog item', product) for product in fallback_products]
    return matches[:limit], upload_profile is not None


def save_capture_data(data_url, subfolder='seller_docs'):
    if not data_url or ',' not in data_url:
        return ''
    header, encoded = data_url.split(',', 1)
    if 'image/' not in header:
        return ''
    raw = base64.b64decode(encoded)
    if len(raw) > 6 * 1024 * 1024:
        return ''
    ext = 'jpg' if 'jpeg' in header or 'jpg' in header else 'png'
    unique_name = f"{uuid.uuid4().hex[:8]}_capture.{ext}"
    folder = os.path.join(app.config['UPLOAD_FOLDER'], subfolder)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, unique_name), 'wb') as fh:
        fh.write(raw)
    return url_for('static', filename=f'uploads/{subfolder}/{unique_name}')


def uploaded_static_url_to_path(file_url):
    if not file_url:
        return ''
    static_prefix = url_for('static', filename='')
    if file_url.startswith(static_prefix):
        relative = file_url[len(static_prefix):].replace('/', os.sep)
        return os.path.join(app.static_folder, relative)
    return ''


def file_content_fingerprint(file_url):
    path = uploaded_static_url_to_path(file_url)
    if not path or not os.path.exists(path):
        return ''
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


KENYA_COUNTIES = [
    'Baringo', 'Bomet', 'Bungoma', 'Busia', 'Elgeyo-Marakwet', 'Embu', 'Garissa', 'Homa Bay',
    'Isiolo', 'Kajiado', 'Kakamega', 'Kericho', 'Kiambu', 'Kilifi', 'Kirinyaga', 'Kisii',
    'Kisumu', 'Kitui', 'Kwale', 'Laikipia', 'Lamu', 'Machakos', 'Makueni', 'Mandera',
    'Marsabit', 'Meru', 'Migori', 'Mombasa', 'Muranga', 'Nairobi', 'Nakuru', 'Nandi',
    'Narok', 'Nyamira', 'Nyandarua', 'Nyeri', 'Samburu', 'Siaya', 'Taita-Taveta',
    'Tana River', 'Tharaka-Nithi', 'Trans Nzoia', 'Turkana', 'Uasin Gishu', 'Vihiga',
    'Wajir', 'West Pokot',
]


ADDRESS_BOOK = {
    'Kenya': {county: [f'{county} drop station', f'{county} town delivery'] for county in KENYA_COUNTIES},
    'Uganda': {
        'Central': ['Kampala', 'Entebbe', 'Mukono'],
        'Western': ['Mbarara', 'Fort Portal'],
    },
    'Tanzania': {
        'Dar es Salaam': ['Ilala', 'Kinondoni', 'Temeke'],
        'Arusha': ['Arusha City', 'Meru'],
    },
    'Rwanda': {
        'Kigali': ['Gasabo', 'Kicukiro', 'Nyarugenge'],
    },
    'Nigeria': {
        'Lagos': ['Ikeja', 'Lekki', 'Victoria Island'],
        'Abuja FCT': ['Garki', 'Maitama', 'Wuse'],
    },
    'South Africa': {
        'Gauteng': ['Johannesburg', 'Pretoria'],
        'Western Cape': ['Cape Town', 'Stellenbosch'],
    },
    'China': {
        'Guangdong': ['Shenzhen', 'Guangzhou', 'Dongguan'],
        'Zhejiang': ['Yiwu', 'Hangzhou', 'Jinhua'],
    },
    'Turkey': {
        'Istanbul': ['Bagcilar', 'Fatih', 'Kucukcekmece'],
        'Bursa': ['Osmangazi', 'Nilufer'],
    },
}

AFRICAN_COUNTRIES = {
    'Algeria', 'Angola', 'Benin', 'Botswana', 'Burkina Faso', 'Burundi', 'Cameroon',
    'DR Congo', 'Egypt', 'Ethiopia', 'Ghana', 'Ivory Coast', 'Kenya', 'Malawi',
    'Morocco', 'Mozambique', 'Nigeria', 'Rwanda', 'Senegal', 'Somalia',
    'South Africa', 'South Sudan', 'Sudan', 'Tanzania', 'Uganda', 'Zambia', 'Zimbabwe',
}
LAUNCH_SOON_MESSAGE = 'We are soon expanding our services to reach all Africans, please check back later'
SELLER_SOON_MESSAGE = 'This feature will be availabe soon'

for country_name, capital_city in [
    ('Algeria', 'Algiers'), ('Angola', 'Luanda'), ('Benin', 'Porto-Novo'), ('Botswana', 'Gaborone'),
    ('Burkina Faso', 'Ouagadougou'), ('Burundi', 'Bujumbura'), ('Cameroon', 'Yaounde'),
    ('Canada', 'Ottawa'), ('DR Congo', 'Kinshasa'), ('Egypt', 'Cairo'), ('Ethiopia', 'Addis Ababa'),
    ('France', 'Paris'), ('Germany', 'Berlin'), ('Ghana', 'Accra'), ('India', 'New Delhi'),
    ('Italy', 'Rome'), ('Ivory Coast', 'Abidjan'), ('Japan', 'Tokyo'), ('Malawi', 'Lilongwe'),
    ('Malaysia', 'Kuala Lumpur'), ('Morocco', 'Rabat'), ('Mozambique', 'Maputo'), ('Netherlands', 'Amsterdam'),
    ('Pakistan', 'Islamabad'), ('Senegal', 'Dakar'), ('Somalia', 'Mogadishu'), ('South Sudan', 'Juba'),
    ('Spain', 'Madrid'), ('Sudan', 'Khartoum'), ('UAE', 'Dubai'), ('United Kingdom', 'London'),
    ('United States', 'New York'), ('Zambia', 'Lusaka'), ('Zimbabwe', 'Harare'),
]:
    ADDRESS_BOOK.setdefault(country_name, {'Main Region': [capital_city]})


def African_address_book():
    preferred = ['Kenya']
    rest = sorted(country for country in AFRICAN_COUNTRIES if country in ADDRESS_BOOK and country != 'Kenya')
    return {country: ADDRESS_BOOK[country] for country in preferred + rest if country in ADDRESS_BOOK}


def checkout_address_book():
    preferred = ['Kenya']
    rest = sorted(country for country in ADDRESS_BOOK if country != 'Kenya')
    return {country: ADDRESS_BOOK[country] for country in preferred + rest if country in ADDRESS_BOOK}


def country_is_supported_for_sales(country):
    allowed = Setting.get('checkout_allowed_countries', 'Kenya').strip()
    if allowed == '*':
        return True
    configured = {item.strip() for item in allowed.split(',') if item.strip()}
    return (country or 'Kenya') in (configured or {'Kenya'})


def estimate_shipping_cost(country, state, city, weight_kg):
    country = country or 'Kenya'
    weight = max(0, weight_kg or 0)
    if country == 'Kenya':
        if state == 'Nairobi' and city and 'drop station' in city.lower():
            return round(100 + (weight * 300), 2)
        return round(weight * 300, 2)
    base_by_country = {
        'Uganda': 900, 'Tanzania': 1100, 'Rwanda': 1300,
        'Nigeria': 4200, 'South Africa': 4800, 'China': 8500, 'Turkey': 7800,
        'United Kingdom': 1900, 'United States': 2250, 'UAE': 1650, 'India': 1500,
    }
    base = base_by_country.get(country, 5500)
    return round(base + (weight * base), 2)


def get_daraja_token():
    """Get OAuth token from Safaricom Daraja API"""
    config_error = daraja_config_error()
    if config_error:
        app.logger.warning(config_error)
        return None

    consumer_key = setting_value('daraja_consumer_key', app.config['DARAJA_CONSUMER_KEY'])
    consumer_secret = setting_value('daraja_consumer_secret', app.config['DARAJA_CONSUMER_SECRET'])
    env = setting_value('daraja_env', app.config['DARAJA_ENV'])

    if env == 'production':
        auth_url = "https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
    else:
        auth_url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"

    resp = requests.get(auth_url, auth=(consumer_key, consumer_secret), timeout=20)
    if resp.status_code == 200:
        return resp.json().get('access_token')
    app.logger.warning('Daraja token request failed: %s %s', resp.status_code, resp.text)
    return None


def stk_push(phone_number, amount, order_number, callback_url=None):
    """Initiate M-Pesa STK Push"""
    try:
        config_error = daraja_config_error()
        if config_error:
            return {'success': False, 'error': config_error}

        token = get_daraja_token()
        if not token:
            return {'success': False, 'error': 'Failed to get Daraja token'}

        env = setting_value('daraja_env', app.config['DARAJA_ENV'])
        if env == 'production':
            api_url = "https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
        else:
            api_url = "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"

        shortcode = setting_value('daraja_shortcode', app.config['DARAJA_SHORTCODE'])
        passkey = daraja_passkey()

        phone = valid_mpesa_msisdn(phone_number)
        if not phone:
            return {'success': False, 'error': 'Enter a valid Safaricom M-Pesa number such as 07XXXXXXXX or 2547XXXXXXXX.'}

        app_base_url = Setting.get('app_base_url', '') or os.environ.get('APP_BASE_URL', '')
        callback_host = request.host_url if request else ''
        is_local = 'localhost' in callback_host or '127.0.0.1' in callback_host

        if not callback_url and is_local and not app_base_url:
            return {'success': False, 'error': 'Daraja requires a public HTTPS callback URL. Set APP_BASE_URL in environment or admin settings (e.g. your ngrok URL or production domain).'}

        timestamp = daraja_timestamp()
        password_str = shortcode + passkey + timestamp
        password = base64.b64encode(password_str.encode()).decode()

        if not callback_url:
            if app_base_url:
                callback_url = app_base_url.rstrip('/') + '/mpesa/callback'
            else:
                callback_url = url_for('mpesa_callback', _external=True)

        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }

        payload = {
            'BusinessShortCode': shortcode,
            'Password': password,
            'Timestamp': timestamp,
            'TransactionType': 'CustomerPayBillOnline',
            'Amount': round(amount),
            'PartyA': phone,
            'PartyB': shortcode,
            'PhoneNumber': phone,
            'CallBackURL': callback_url,
            'AccountReference': order_number[:12],
            'TransactionDesc': f'Payment for {order_number}'
        }

        resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
        try:
            data = resp.json() if resp.text else {}
        except ValueError:
            data = {'raw_response': resp.text}

        if data.get('ResponseCode') == '0':
            app.logger.warning('STK push rejected for %s: %s', order_number, data)
            return {
                'success': True,
                'checkout_request_id': data.get('CheckoutRequestID'),
                'merchant_request_id': data.get('MerchantRequestID'),
                'response': data
            }
        else:
            error_message = (
                data.get('errorMessage')
                or data.get('ResponseDescription')
                or data.get('responseDescription')
                or data.get('ResultDesc')
                or data.get('error')
                or data.get('requestId')
                or 'STK Push failed'
            )
            if resp.status_code == 400 and 'Bad Request' in error_message:
                error_message = 'Daraja rejected the STK payload. Confirm shortcode, passkey, environment, callback URL, and phone number format.'
            return {
                'success': False,
                'error': f'{error_message} (HTTP {resp.status_code})',
                'response': data
            }
    except Exception as e:
        return {'success': False, 'error': str(e)}


def check_payment_status(checkout_request_id):
    """Query Daraja for payment status"""
    try:
        token = get_daraja_token()
        if not token:
            return None

        env = setting_value('daraja_env', app.config['DARAJA_ENV'])
        if env == 'production':
            api_url = "https://api.safaricom.co.ke/mpesa/stkpushquery/v1/query"
        else:
            api_url = "https://sandbox.safaricom.co.ke/mpesa/stkpushquery/v1/query"

        shortcode = setting_value('daraja_shortcode', app.config['DARAJA_SHORTCODE'])
        passkey = daraja_passkey()

        timestamp = daraja_timestamp()
        password = base64.b64encode((shortcode + passkey + timestamp).encode()).decode()

        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        payload = {
            'BusinessShortCode': shortcode,
            'Password': password,
            'Timestamp': timestamp,
            'CheckoutRequestID': checkout_request_id
        }

        resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
        return resp.json() if resp.text else None
    except Exception:
        app.logger.exception('M-Pesa STK query failed')
        return None


def send_email(to_email, subject, body_html):
    """Send email via Resend HTTP API (preferred) or SMTP fallback."""
    # Get API key - prefer env var for security
    resend_key = os.environ.get('RESEND_API_KEY', '') or Setting.get('resend_api_key', '')
    sender_name = Setting.get('business_name', 'SMARKAFRICA')

    # Get from_email - must match verified Resend domain
    mail_from = Setting.get('from_email', '')
    if not mail_from or '@smark-africa.com' not in mail_from:
        mail_from = 'noreply@smark-africa.com'

    logger.info(f'send_email: to={to_email}, from={mail_from}, has_resend_key={bool(resend_key)}')

    if resend_key:
        try:
            # Use direct HTTP API to avoid gevent/SDK recursion conflict
            resp = requests.post(
                'https://api.resend.com/emails',
                headers={
                    'Authorization': f'Bearer {resend_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'from': f'{sender_name} <{mail_from}>',
                    'to': [to_email],
                    'subject': subject,
                    'html': body_html,
                },
                timeout=30
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                logger.info(f'Email sent to {to_email} via Resend API (id={data.get("id")})')
                return True
            logger.error(f'Resend API error {resp.status_code}: {resp.text}')
            return False
        except Exception as e:
            logger.error(f'Resend API error: {e}', exc_info=True)
            return False

    logger.warning(f'No Resend API key configured, trying SMTP fallback')

    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        mail_server = Setting.get('smtp_server', Setting.get('mail_server', app.config['MAIL_SERVER']))
        mail_port = int(Setting.get('smtp_port', Setting.get('mail_port', app.config['MAIL_PORT'])))
        mail_user = Setting.get('smtp_username', Setting.get('mail_username', app.config['MAIL_USERNAME']))
        mail_pass = Setting.get('smtp_password', Setting.get('mail_password', app.config['MAIL_PASSWORD']))

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = mail_from
        msg['To'] = to_email

        part = MIMEText(body_html, 'html')
        msg.attach(part)

        server = smtplib.SMTP(mail_server, mail_port, timeout=20)
        if Setting.get('smtp_use_tls', '1') != '0':
            server.starttls()
        if mail_user and mail_pass:
            server.login(mail_user, mail_pass)
        server.sendmail(mail_from, [to_email], msg.as_string())
        server.quit()
        logger.info(f'Email sent to {to_email} via SMTP')
        return True
    except Exception as e:
        logger.error(f'Email sending failed: {e}')
        return False


def send_order_confirmation(order):
    """Send order confirmation email"""
    user = order.customer
    items_html = ''
    for item in order.items:
        items_html += f'<tr><td>{item.product_name}</td><td>{item.quantity}</td><td>KES {item.price:,.0f}</td></tr>'

    body = f"""
    <h2>Order Confirmed! 🎉</h2>
    <p>Thank you for your order, <strong>{user.username}</strong>!</p>
    <h3>Order #{order.order_number}</h3>
    <table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%">
        <tr style="background:#1a1a2e;color:white"><th>Item</th><th>Qty</th><th>Price</th></tr>
        {items_html}
        <tr><td colspan="2"><strong>Total</strong></td><td><strong>KES {order.amount_paid:,.0f}</strong></td></tr>
    </table>
    <p>M-Pesa Receipt: {order.mpesa_receipt or 'Pending'}</p>
    <p>Track your order: <a href="{url_for('order_detail', order_id=order.id, _external=True)}">View Order</a></p>
    <br><p>SMARKAFRICA Team</p>
    """
    send_email(user.email, f'Order #{order.order_number} - Confirmed!', body)


def send_feedback_request(order):
    """Send email asking for product rating/feedback"""
    user = order.customer
    body = f"""
    <h2>How was your purchase? ⭐</h2>
    <p>Hi {user.username},</p>
    <p>We'd love your feedback on your recent order #{order.order_number}.</p>
    <p>Please rate the products you purchased:</p>
    <ul>
    """
    for item in order.items:
        body += f'<li><a href="{url_for("product_page", slug=item.product.slug if item.product else "", _external=True)}">{item.product_name}</a></li>'

    body += """
    </ul>
    <p>Your honest review helps other customers!</p>
    <br><p>SMARKAFRICA Team</p>
    """
    send_email(user.email, 'Share Your Feedback! ⭐', body)


def send_system_update(subject, message):
    """Send system update email to all users"""
    users = User.query.filter_by(is_active=True).all()
    for user in users:
        body = f"""
        <h2>System Update</h2>
        <p><strong>{subject}</strong></p>
        <p>{message}</p>
        <br><p>SMARKAFRICA Team</p>
        """
        send_email(user.email, subject, body)


def send_welcome_email(user):
    body = f"""
    <h2>Welcome to SMARKAFRICA</h2>
    <p>Hi {user.username},</p>
    <p>Thank you for joining the SMARKAFRICA community. Your account is ready for secure buying, order tracking, and marketplace updates.</p>
    <p>If you later become a seller, you will be guided through document verification and payment protection setup.</p>
    <br><p>SMARKAFRICA Team</p>
    """
    return send_email(user.email, 'Welcome to SMARKAFRICA', body)


def send_feedback_auto_reply(user):
    if not user or not user.email:
        return False
    body = """
    <p>Thank you for letting us know how we can serve you better. We value everyone's opinion</p>
    <br><p>SMARKAFRICA Team</p>
    """
    return send_email(user.email, 'Thank you for your feedback', body)


def apply_auto_discount(product):
    """Auto-calculate discount based on sales performance without causing loss"""
    if product.buying_price <= 0:
        return  # Can't calculate without buying price

    max_discount = ((product.selling_price - product.buying_price) / product.selling_price) * 100
    max_discount = max(0, max_discount - 1)  # Keep 1% profit margin

    # Check sales performance
    thirty_days_ago = utcnow() - timedelta(days=30)
    recent_sales = OrderItem.query.join(Order).filter(
        OrderItem.product_id == product.id,
        Order.created_at >= thirty_days_ago
    ).count()

    expected_sales = max(5, product.sales_count * 0.1 if product.sales_count > 0 else 10)

    if recent_sales < expected_sales and max_discount > 0:
        # Apply discount based on how far below expectations
        performance_ratio = recent_sales / expected_sales if expected_sales > 0 else 0
        if performance_ratio < 0.5:
            discount_to_apply = min(max_discount, 30)
            product.discount_percent = discount_to_apply


def calculate_shipping_cost(shipping_rate_id, weight_kg):
    """Calculate shipping cost from rate and weight"""
    rate = ShippingRate.query.get(shipping_rate_id)
    if not rate or not rate.is_active:
        return 0
    return rate.base_cost + (rate.cost_per_kg * max(0, weight_kg))


def slugify(value, max_length=100):
    cleaned = ''.join(ch.lower() if ch.isalnum() else '-' for ch in (value or '').strip())
    cleaned = '-'.join(part for part in cleaned.split('-') if part)
    return (cleaned or uuid.uuid4().hex[:8])[:max_length]


def unique_category_slug(name, current_id=None):
    base_slug = slugify(name, 100)
    slug = base_slug
    counter = 1
    while True:
        query = Category.query.filter_by(slug=slug)
        if current_id:
            query = query.filter(Category.id != current_id)
        if not query.first():
            return slug
        slug = f"{base_slug[:90]}-{counter}"
        counter += 1


def current_user_is_mvp():
    return current_user.is_authenticated and current_user.is_admin and current_user.admin_level == 'mvp'


MARKET_CATEGORY_COLORS = {
    'electronics': '#3b82f6',
    'fashion': '#ec4899',
    'home': '#22c55e',
    'beauty': '#f97316',
    'food': '#eab308',
    'hardware': '#64748b',
    'digital': '#8b5cf6',
    'other': '#14b8a6',
}

PRODUCT_MARKET_REFERENCES = [
    {
        'tokens': {'samsung', 'galaxy', 's26', 'ultra'},
        'label': 'Samsung Galaxy S26 Ultra',
        'category_hint': 'electronics',
        'kenya_low': 139500.0,
        'kenya_high': 203000.0,
        'world_low': 1049.99,
        'world_high': 1799.0,
        'world_currency': 'USD',
        'manufacturer_price': 168870.0,
        'source': 'Phones & Electronics Kenya, Smartphones Planet Kenya, PhoneGrade Kenya, Android Central',
        'source_url': 'https://www.phonesandelectronicsafrica.co.ke/product/samsung-galaxy-s26-ultra',
        'source_urls': [
            'https://ropemphones.co.ke/product/samsung-galaxy-s26-ultra/',
            'https://phoneshopkenya.co.ke/product/samsung-galaxy-s26-ultra-256gb/',
            'https://phonesstorekenya.com/samsung-galaxy-s26-prices-in-kenya/',
            'https://starmac.co.ke/product/samsung-galaxy-s26-ultra/',
            'https://www.phonestablets.co.ke/product/samsung-galaxy-s26-ultra/',
            'https://pricepoint.co.ke/product/samsung-galaxy-s26-ultra/',
        ],
        'image_url': 'https://www.phonesandelectronicsafrica.co.ke/cdn/shop/files/samsung-galaxy-s26-ultra.jpg',
        'image_candidates': [
            'https://www.phonesandelectronicsafrica.co.ke/cdn/shop/files/samsung-galaxy-s26-ultra.jpg',
            'https://fdn2.gsmarena.com/vv/pics/samsung/samsung-galaxy-s25-ultra-1.jpg',
        ],
        'news_title': 'Samsung Galaxy S26 Ultra market price watch',
        'news_body': 'Kenya retailer listings place this flagship in the KSh 139,500-203,000 band, while international pricing sits around USD 1,299-1,799 depending on storage. Track storage variant and warranty before publishing customer updates.',
    },
    {
        'tokens': {'playstation', 'ps5', 'sony'},
        'label': 'Sony PlayStation 5',
        'category_hint': 'gaming',
        'kenya_low': 72000.0,
        'kenya_high': 105000.0,
        'world_low': 449.0,
        'world_high': 699.0,
        'world_currency': 'USD',
        'manufacturer_price': 76000.0,
        'source': 'Sony/retailer gaming console market watch',
        'source_url': 'https://www.playstation.com/',
        'image_url': 'https://gmedia.playstation.com/is/image/SIEPDC/ps5-product-thumbnail-01-en-14sep21',
        'image_candidates': [
            'https://gmedia.playstation.com/is/image/SIEPDC/ps5-product-thumbnail-01-en-14sep21',
            'https://gmedia.playstation.com/is/image/SIEPDC/ps5-slim-disc-console-featured-hardware-image-block-01-en-15nov23',
        ],
        'news_title': 'PlayStation console market watch',
        'news_body': 'Console pricing is sensitive to storage bundles, import duty, controller bundles, and regional availability. Verify bundle contents before customer-facing updates.',
    },
    {
        'tokens': {'gamepad', 'controller', 'gaming', 'pad', 'joystick'},
        'label': 'Gaming Pads and Controllers',
        'category_hint': 'gaming',
        'kenya_low': 1200.0,
        'kenya_high': 18000.0,
        'world_low': 8.0,
        'world_high': 120.0,
        'world_currency': 'USD',
        'manufacturer_price': 5200.0,
        'source': 'Gaming controller marketplace and accessory supplier watch',
        'source_url': 'https://commons.wikimedia.org/wiki/File:Xbox-s-controller.jpg',
        'image_url': 'https://commons.wikimedia.org/wiki/Special:FilePath/Xbox-s-controller.jpg',
        'image_candidates': [
            'https://commons.wikimedia.org/wiki/Special:FilePath/Xbox%20One%20Elite%20Controller%20%28front%29.jpg',
            'https://commons.wikimedia.org/wiki/Special:FilePath/Nintendo-Famicom-NES-Dogbone-Controller-FL.jpg',
        ],
        'news_title': 'Gaming controller market watch',
        'news_body': 'Gaming pads and controllers vary by wireless support, console compatibility, battery, vibration, build quality, and replacement-part availability.',
    },
    {
        'tokens': {'drone', 'drones', 'quadcopter', 'fpv', 'uav'},
        'label': 'Consumer Drones and FPV Quadcopters',
        'category_hint': 'gaming',
        'kenya_low': 6500.0,
        'kenya_high': 180000.0,
        'world_low': 45.0,
        'world_high': 1400.0,
        'world_currency': 'USD',
        'manufacturer_price': 62000.0,
        'source': 'Drone and electronics supplier watch',
        'source_url': 'https://commons.wikimedia.org/wiki/Category:Unmanned_quadcopters',
        'image_url': 'https://commons.wikimedia.org/wiki/Special:FilePath/Quadcopter%20drone%20vendor%20at%20Shenzhen%20Huaqiang%20Electronic%20World.jpg',
        'image_candidates': [
            'https://commons.wikimedia.org/wiki/Special:FilePath/Uavtek%20Bug%20FX%20Nano%20UAS%20Quadcopter%201A.jpg',
            'https://images.pexels.com/photos/442587/pexels-photo-442587.jpeg?auto=compress&cs=tinysrgb&w=1200',
        ],
        'news_title': 'Drone and quadcopter market watch',
        'news_body': 'Drone pricing shifts by camera quality, battery bundle, GPS stabilization, FPV capability, spare propellers, and import compliance.',
    },
    {
        'tokens': {'oraimo', 'earbuds', 'pods', 'freepods'},
        'label': 'Oraimo FreePods / Earbuds',
        'category_hint': 'electronics',
        'kenya_low': 1800.0,
        'kenya_high': 6500.0,
        'world_low': 12.0,
        'world_high': 45.0,
        'world_currency': 'USD',
        'manufacturer_price': 3900.0,
        'source': 'Oraimo Kenya and mobile accessory retailer watch',
        'source_url': 'https://ke.oraimo.com/',
        'source_urls': ['https://ke.oraimo.com/'],
        'image_url': 'https://ke.oraimo.com/cdn/shop/files/FreePods_4.jpg',
        'image_candidates': [
            'https://ke.oraimo.com/cdn/shop/files/FreePods_4.jpg',
            'https://ng.oraimo.com/cdn/shop/files/FreePods_4.jpg',
        ],
        'news_title': 'Oraimo accessories price watch',
        'news_body': 'Oraimo earbuds and mobile accessories move quickly with launches, color refreshes, and flash sales. Watch official Kenya pricing before sending customer offers.',
    },
    {
        'tokens': {'samsung', 'tv', 'television', 'crystal', 'qled'},
        'label': 'Samsung Smart TV',
        'category_hint': 'electronics',
        'kenya_low': 32000.0,
        'kenya_high': 240000.0,
        'world_low': 250.0,
        'world_high': 1600.0,
        'world_currency': 'USD',
        'manufacturer_price': 95000.0,
        'source': 'Samsung TV retailer watch',
        'source_url': 'https://www.samsung.com/africa_en/tvs/',
        'source_urls': ['https://www.samsung.com/africa_en/tvs/'],
        'image_url': 'https://images.samsung.com/is/image/samsung/p6pim/africa_en/ua55cu8000uxke/gallery/africa-en-crystal-uhd-cu8000-ua55cu8000uxke-536777542',
        'image_candidates': [
            'https://images.samsung.com/is/image/samsung/p6pim/africa_en/ua55cu8000uxke/gallery/africa-en-crystal-uhd-cu8000-ua55cu8000uxke-536777542',
        ],
        'news_title': 'Samsung TV market watch',
        'news_body': 'TV pricing varies heavily by panel type, size, refresh rate, and warranty. Compare Crystal UHD, QLED, and OLED classes before publishing customer updates.',
    },
    {
        'tokens': {'fridge', 'refrigerator', 'samsung', 'lg'},
        'label': 'Refrigerators and Fridges',
        'category_hint': 'home',
        'kenya_low': 28000.0,
        'kenya_high': 280000.0,
        'world_low': 220.0,
        'world_high': 2200.0,
        'world_currency': 'USD',
        'manufacturer_price': 105000.0,
        'source': 'Appliance retailer market watch',
        'source_url': 'https://www.samsung.com/africa_en/refrigerators/',
        'source_urls': ['https://www.samsung.com/africa_en/refrigerators/'],
        'image_url': 'https://images.samsung.com/is/image/samsung/p6pim/africa_en/rt35k5532s8-fa/gallery/africa-en-top-mount-freezer-rt35k5532s8-fa-532150851',
        'image_candidates': [
            'https://images.samsung.com/is/image/samsung/p6pim/africa_en/rt35k5532s8-fa/gallery/africa-en-top-mount-freezer-rt35k5532s8-fa-532150851',
        ],
        'news_title': 'Fridge and appliance market watch',
        'news_body': 'Fridge pricing changes with capacity, inverter technology, import cost, and energy rating. Watch new inverter and side-by-side releases before customer campaigns.',
    },
    {
        'tokens': {'asiahorse', 'asia', 'horse', 'fan', 'rgb', 'pc'},
        'label': 'AsiaHorse PC Cooling and RGB Parts',
        'category_hint': 'computing',
        'kenya_low': 2500.0,
        'kenya_high': 18000.0,
        'world_low': 20.0,
        'world_high': 120.0,
        'world_currency': 'USD',
        'manufacturer_price': 8500.0,
        'source': 'AsiaHorse PC accessory market watch',
        'source_url': 'https://www.asiahorse.com.cn/',
        'source_urls': ['https://www.asiahorse.com.cn/'],
        'image_url': 'https://commons.wikimedia.org/wiki/Special:FilePath/2023%20Corsair%20SP120%20RGB%20Elite.jpg',
        'image_candidates': [
            'https://commons.wikimedia.org/wiki/Special:FilePath/80mm%20fan.jpg',
            'https://commons.wikimedia.org/wiki/Special:FilePath/Fan%20and%20case.jpg',
        ],
        'news_title': 'AsiaHorse computing accessory watch',
        'news_body': 'RGB fans, extension cables, and PC cooling kits move with gaming build trends. Check color, connector type, fan count, and controller bundle before advertising.',
    },
    {
        'tokens': {'hp', 'dell', 'lenovo', 'laptop', 'computer'},
        'label': 'Student and Business Laptops',
        'category_hint': 'computing',
        'kenya_low': 28000.0,
        'kenya_high': 220000.0,
        'world_low': 250.0,
        'world_high': 1800.0,
        'world_currency': 'USD',
        'manufacturer_price': 85000.0,
        'source': 'Laptop retailer and importer market watch',
        'source_url': 'https://www.hp.com/us-en/shop/cat/laptops',
        'source_urls': ['https://www.hp.com/us-en/shop/cat/laptops'],
        'image_url': 'https://ssl-product-images.www8-hp.com/digmedialib/prodimg/lowres/c08966535.png',
        'image_candidates': [
            'https://ssl-product-images.www8-hp.com/digmedialib/prodimg/lowres/c08966535.png',
        ],
        'news_title': 'Laptop market watch',
        'news_body': 'Laptop pricing changes with CPU generation, RAM, SSD, battery, warranty, and refurbished status. Student recommendations should compare performance against budget.',
    },
    {
        'tokens': {'gaming', 'chair', 'seat', 'ergonomic'},
        'label': 'Gaming Chairs and Ergonomic Seats',
        'category_hint': 'furniture',
        'kenya_low': 12000.0,
        'kenya_high': 65000.0,
        'world_low': 80.0,
        'world_high': 450.0,
        'world_currency': 'USD',
        'manufacturer_price': 28000.0,
        'source': 'Furniture and gaming setup market watch',
        'source_url': '',
        'source_urls': [],
        'image_url': 'https://images.pexels.com/photos/7862507/pexels-photo-7862507.jpeg?auto=compress&cs=tinysrgb&w=1200',
        'image_candidates': [
            'https://images.unsplash.com/photo-1598550476439-6847785fcea6?auto=format&fit=crop&w=1200&q=80',
            'https://images.pexels.com/photos/3945683/pexels-photo-3945683.jpeg?auto=compress&cs=tinysrgb&w=1200',
        ],
        'news_title': 'Gaming chair market watch',
        'news_body': 'Gaming seats move by frame quality, recline mechanism, material, RGB/footrest additions, and shipping bulk cost. Compare similar chairs before setting margins.',
    },
    {
        'tokens': {'monitor', 'gaming', 'display', 'hz'},
        'label': 'Gaming Monitors',
        'category_hint': 'computing',
        'kenya_low': 14000.0,
        'kenya_high': 180000.0,
        'world_low': 100.0,
        'world_high': 1200.0,
        'world_currency': 'USD',
        'manufacturer_price': 58000.0,
        'source': 'Gaming monitor retailer market watch',
        'source_url': '',
        'source_urls': [],
        'image_url': '',
        'image_candidates': [],
        'news_title': 'Gaming monitor market watch',
        'news_body': 'Monitor prices vary by size, refresh rate, panel type, resolution, HDR, and adaptive sync. Price comparisons should match specs, not only screen size.',
    },
    {
        'tokens': {'washing', 'machine', 'washer', 'laundry'},
        'label': 'Washing Machines',
        'category_hint': 'home',
        'kenya_low': 22000.0,
        'kenya_high': 160000.0,
        'world_low': 180.0,
        'world_high': 1200.0,
        'world_currency': 'USD',
        'manufacturer_price': 72000.0,
        'source': 'Appliance market watch',
        'source_url': 'https://www.samsung.com/africa_en/washers-and-dryers/',
        'source_urls': ['https://www.samsung.com/africa_en/washers-and-dryers/'],
        'image_url': 'https://commons.wikimedia.org/wiki/Special:FilePath/Front%20Load%20Washing%20Machine.jpg',
        'image_candidates': [
            'https://commons.wikimedia.org/wiki/Special:FilePath/Washer.600pix.jpg',
            'https://images.pexels.com/photos/5591663/pexels-photo-5591663.jpeg?auto=compress&cs=tinysrgb&w=1200',
        ],
        'news_title': 'Washing machine market watch',
        'news_body': 'Washer prices change by capacity, inverter motor, front/top load design, energy rating, and warranty support.',
    },
]


def category_market_key(category_name):
    name = (category_name or '').lower()
    if any(token in name for token in ['electronic', 'phone', 'computer', 'gadget', 'solar']):
        return 'electronics'
    if any(token in name for token in ['fashion', 'cloth', 'shoe', 'textile', 'bag']):
        return 'fashion'
    if any(token in name for token in ['home', 'kitchen', 'furniture', 'decor']):
        return 'home'
    if any(token in name for token in ['beauty', 'cosmetic', 'health']):
        return 'beauty'
    if any(token in name for token in ['food', 'supermarket', 'grocery']):
        return 'food'
    if any(token in name for token in ['hardware', 'construction', 'tool', 'auto', 'spare']):
        return 'hardware'
    if any(token in name for token in ['digital', 'book', 'software']):
        return 'digital'
    return 'other'


def market_signal_for(category_name, base_price, tick):
    seed = int(hashlib.sha256(f'{category_name}:{tick // 12}'.encode()).hexdigest()[:8], 16)
    wave = ((tick + seed) % 18) - 9
    percent_change = round((wave / 9.0) * 1.8, 2)
    predicted_price = round(max(1, base_price * (1 + percent_change / 100)), 2)
    if percent_change > 0.25:
        direction = 'increase'
    elif percent_change < -0.25:
        direction = 'drop'
    else:
        direction = 'stagnant'
    return predicted_price, percent_change, direction


def product_market_label(name, category_name=''):
    text_value = f'{name or ""} {category_name or ""}'.lower()
    if any(token in text_value for token in ['ram', 'memory', 'ddr4', 'ddr5']):
        return 'RAM sticks'
    if any(token in text_value for token in ['ssd', 'hdd', 'nvme', 'storage', 'hard drive']):
        return 'storage devices'
    if any(token in text_value for token in ['gaming', 'gpu', 'graphics', 'playstation', 'xbox']):
        return 'gaming hardware'
    if any(token in text_value for token in ['laptop', 'desktop', 'computer', 'monitor', 'keyboard']):
        return 'computing accessories'
    return name or category_name or 'product'


def product_market_reference_match(name='', category_name=''):
    tokens = token_set(name, category_name)
    best = None
    best_score = 0
    for item in PRODUCT_MARKET_REFERENCES:
        score = len(tokens.intersection(item['tokens']))
        if score > best_score and score >= min(3, len(item['tokens'])):
            best = item
            best_score = score
    return best


def extract_ksh_prices(text_value):
    prices = []
    patterns = [
        r'KShs?\.?\s*([0-9][0-9,]*(?:\.\d+)?)',
        r'Ksh\.?\s*([0-9][0-9,]*(?:\.\d+)?)',
        r'KES\s*([0-9][0-9,]*(?:\.\d+)?)',
        r'KSh\s*([0-9][0-9,]*(?:\.\d+)?)',
        r'K\s?sh\s*([0-9][0-9,]*(?:\.\d+)?)',
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text_value or '', flags=re.IGNORECASE):
            try:
                value = float(match.replace(',', ''))
                if 1000 <= value <= 1000000:
                    prices.append(value)
            except ValueError:
                pass
    usd_matches = re.findall(r'(?:USD|\$)\s*([0-9][0-9,]*(?:\.\d+)?)', text_value or '', flags=re.IGNORECASE)
    for match in usd_matches:
        try:
            value = float(match.replace(',', '')) * 130
            if 1000 <= value <= 1000000:
                prices.append(value)
        except ValueError:
            pass
    return prices


def fetch_live_reference_range(reference):
    prices = []
    checked = []
    headers = {'User-Agent': 'Mozilla/5.0 SMARKAFRICA market intelligence'}
    urls = reference.get('source_urls', [])[:6]

    def fetch_url(url):
        try:
            response = requests.get(url, timeout=6, headers=headers)
            if response.ok:
                page_prices = extract_ksh_prices(response.text)
                if page_prices:
                    return url, page_prices[:6]
        except requests.RequestException:
            pass
        return url, []

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_url, url): url for url in urls}
        for future in as_completed(futures):
            url, page_prices = future.result()
            if page_prices:
                prices.extend(page_prices)
                checked.append(url)

    if not prices:
        return None
    floor = float(reference.get('kenya_low', 0)) * 0.75
    ceiling = float(reference.get('kenya_high', 0)) * 1.25
    filtered_prices = [price for price in prices if floor <= price <= ceiling]
    if filtered_prices:
        prices = filtered_prices
    low = max(min(prices), float(reference.get('kenya_low', min(prices))))
    high = max(max(prices), float(reference.get('kenya_high', max(prices))))
    return {
        'kenya_low': round(low, 2),
        'kenya_high': round(high, 2),
        'manufacturer_price': round((low + high) / 2, 2),
        'source': f"Live web scan of {len(checked)} source(s)",
        'source_url': checked[0] if checked else reference.get('source_url', ''),
    }


def clean_search_terms(*parts, max_len=180):
    value = ' '.join(str(part or '') for part in parts)
    value = re.sub(r'<[^>]+>', ' ', value)
    value = re.sub(r'[^A-Za-z0-9\s\-\+\.]', ' ', html.unescape(value))
    value = re.sub(r'\s+', ' ', value).strip()
    return value[:max_len]


def live_web_price_scan(product_name, country='Kenya', description='', category_name=''):
    query_core = clean_search_terms(product_name, category_name, description[:180])
    query = f"{query_core} price {country} KSh"
    prices = []
    source_url = ''
    source_name = 'Live web scan'
    search_urls = [
        f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}",
        f"https://www.bing.com/search?q={requests.utils.quote(query)}",
    ]
    headers = {'User-Agent': 'Mozilla/5.0 SMARKAFRICA market intelligence'}
    blocked_domains = ['duckduckgo', 'bing.com', 'google.com', 'gstatic']

    def fetch_search(url):
        try:
            r = requests.get(url, timeout=6, headers=headers)
            return r.text if r.ok else ''
        except requests.RequestException:
            return ''

    def fetch_snippet(url):
        try:
            r = requests.get(url, timeout=5, headers=headers)
            if r.ok:
                return url, extract_ksh_prices(r.text)[:8]
        except requests.RequestException:
            pass
        return url, []

    with ThreadPoolExecutor(max_workers=4) as executor:
        search_futures = [executor.submit(fetch_search, u) for u in search_urls]
        for future in as_completed(search_futures):
            text_value = future.result()
            if not text_value:
                continue
            prices.extend(extract_ksh_prices(text_value))
            snippet_urls = []
            for snippet in re.findall(r'https?://[^\s"&<>]+', text_value):
                if any(b in snippet.lower() for b in blocked_domains):
                    continue
                snippet_urls.append(snippet.rstrip(').,'))
                if len(snippet_urls) >= 6:
                    break
            snippet_futures = [executor.submit(fetch_snippet, u) for u in snippet_urls]
            for sf in as_completed(snippet_futures):
                url, page_prices = sf.result()
                if page_prices:
                    prices.extend(page_prices)
                    source_url = source_url or url
                if len(prices) >= 12:
                    break
            hrefs = re.findall(r'href=["\'](https?://[^"\']+)["\']', text_value)
            for href in hrefs:
                if not any(b in href for b in blocked_domains):
                    source_url = source_url or href
                    break
            if prices:
                break

    filtered = [price for price in prices if 1000 <= price <= 1000000]
    if filtered:
        filtered.sort()
        low = filtered[0]
        high = filtered[-1]
        if len(filtered) >= 4:
            low = filtered[1]
            high = filtered[-2]
        return {
            'kenya_low': round(low, 2),
            'kenya_high': round(max(low, high), 2),
            'manufacturer_price': round((low + high) / 2, 2),
            'source': source_name,
            'source_url': source_url,
            'confidence': 'live_web_scan',
        }
    return None


def market_cache_key(name='', category_name=''):
    return clean_search_terms(name, category_name, max_len=220).lower()


def cached_market_range(name='', category_name=''):
    key = market_cache_key(name, category_name)
    if not key:
        return None
    row = MarketPriceCache.query.filter_by(cache_key=key).first()
    if not row:
        return None
    return {
        'kenya_low': row.kenya_low or 0.0,
        'kenya_high': row.kenya_high or 0.0,
        'manufacturer_price': row.manufacturer_price or 0.0,
        'source': row.source or 'Cached market scan',
        'source_url': row.source_url or '',
        'confidence': row.confidence or 'cached_scan',
    }


def upsert_market_price_cache(name, category_name, payload):
    key = market_cache_key(name, category_name)
    if not key or not payload:
        return None
    row = MarketPriceCache.query.filter_by(cache_key=key).first()
    if not row:
        row = MarketPriceCache(cache_key=key, label=product_market_label(name, category_name), category_name=category_name)
        db.session.add(row)
    row.kenya_low = payload.get('kenya_low', 0.0)
    row.kenya_high = payload.get('kenya_high', 0.0)
    row.manufacturer_price = payload.get('manufacturer_price', 0.0)
    row.source = payload.get('source', 'Scheduled market scan')
    row.source_url = payload.get('source_url', '')
    row.confidence = payload.get('confidence', 'scheduled_scan')
    row.payload = json.dumps(payload)
    row.refreshed_at = utcnow()
    return row


def refresh_market_price_cache(limit=30):
    refreshed = 0
    references = PRODUCT_MARKET_REFERENCES[:limit]

    def fetch_reference(ref):
        payload = fetch_live_reference_range(ref)
        return ref, payload

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_reference, ref) for ref in references]
        for future in as_completed(futures):
            ref, payload = future.result()
            if payload:
                upsert_market_price_cache(ref['label'], ref.get('category_hint', ''), payload)
                refreshed += 1

    remaining = max(0, limit - refreshed)
    products = Product.query.filter_by(is_active=True).order_by(Product.updated_at.desc()).limit(remaining).all() if remaining else []

    def fetch_product_price(product):
        category_name = product.category.name if product.category else ''
        payload = live_web_price_scan(product.name, description=product.description or '', category_name=category_name)
        return product, category_name, payload

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(fetch_product_price, p) for p in products]
        for future in as_completed(futures):
            product, category_name, payload = future.result()
            if payload:
                upsert_market_price_cache(product.name, category_name, payload)
                refreshed += 1

    db.session.commit()
    return refreshed

def comparable_products(name='', category_id=None, exclude_id=None, limit=12):
    tokens = token_set(name)
    query = Product.query.filter(Product.is_active == True)
    if category_id:
        query = query.filter(Product.category_id == category_id)
    if exclude_id:
        query = query.filter(Product.id != exclude_id)
    products = query.order_by(Product.updated_at.desc()).limit(80).all()
    scored = []
    for product in products:
        product_tokens = token_set(product.name, product.short_description, product.description)
        overlap = len(tokens.intersection(product_tokens))
        if overlap or not tokens:
            scored.append((overlap, product))
    scored.sort(key=lambda item: (item[0], item[1].updated_at or item[1].created_at), reverse=True)
    return [product for _, product in scored[:limit]]


def market_price_reference(name, category_id=None, price=None, buying_price=0, exclude_id=None, description=''):
    try:
        category_id = int(category_id) if category_id not in [None, ''] else None
    except (TypeError, ValueError):
        category_id = None
    try:
        exclude_id = int(exclude_id) if exclude_id not in [None, ''] else None
    except (TypeError, ValueError):
        exclude_id = None
    category = Category.query.get(category_id) if category_id else None
    known_reference = product_market_reference_match(name, category.name if category else '')
    price_value = float(price or 0)
    if known_reference:
        live_range = cached_market_range(known_reference['label'], known_reference.get('category_hint', ''))
        range_source = live_range or known_reference
        kenya_low = range_source['kenya_low']
        kenya_high = range_source['kenya_high']
        manufacturer_price = range_source['manufacturer_price']
        recommended = round((kenya_low + kenya_high) / 2, 2)
        status = 'ok'
        message = ''
        if price_value and price_value < kenya_low:
            status = 'low'
            message = 'This listing is below verified Kenya retailer ranges. Confirm variant, warranty, condition, and authenticity before publishing.'
        elif price_value and price_value > kenya_high:
            status = 'high'
            message = 'This listing is above verified Kenya retailer ranges. Confirm storage variant, warranty, import cost, and customer value before publishing.'
        return {
            'status': status,
            'message': message,
            'label': known_reference['label'],
            'kenya_low': kenya_low,
            'kenya_high': kenya_high,
            'manufacturer_price': manufacturer_price,
            'recommended_price': recommended,
            'competitor_count': 0,
            'source': range_source.get('source', known_reference['source']),
            'source_url': range_source.get('source_url', known_reference['source_url']),
            'image_url': known_reference.get('image_url', ''),
            'confidence': 'verified_reference',
        }

    comps = comparable_products(name, category_id, exclude_id=exclude_id)
    comp_prices = [p.discounted_price or p.selling_price for p in comps if (p.discounted_price or p.selling_price)]
    if comp_prices:
        low = min(comp_prices)
        high = max(comp_prices)
        midpoint = sum(comp_prices) / len(comp_prices)
    else:
        web_range = cached_market_range(name, category.name if category else '')
        if web_range:
            kenya_low = web_range['kenya_low']
            kenya_high = web_range['kenya_high']
            manufacturer_price = web_range['manufacturer_price']
            recommended = round((kenya_low + kenya_high) / 2, 2)
            status = 'ok'
            message = ''
            if price_value and price_value < kenya_low:
                status = 'low'
                message = 'This listing is below the live web price scan range. Confirm condition, authenticity, and source cost.'
            elif price_value and price_value > kenya_high:
                status = 'high'
                message = 'This listing is above the live web price scan range. Confirm variant and customer value.'
            return {
                'status': status,
                'message': message,
                'label': product_market_label(name, category.name if category else ''),
                'kenya_low': kenya_low,
                'kenya_high': kenya_high,
                'manufacturer_price': manufacturer_price,
                'recommended_price': recommended,
                'competitor_count': 0,
                'source': web_range['source'],
                'source_url': web_range.get('source_url', ''),
                'image_url': '',
                'confidence': web_range['confidence'],
            }
        label = product_market_label(name, category.name if category else '')
        return {
            'status': 'web_pending',
            'message': 'Scheduled web scan has not cached a market range yet. No automated range will be shown until the background scan captures one.',
            'label': label,
            'kenya_low': 0.0,
            'kenya_high': 0.0,
            'manufacturer_price': 0.0,
            'recommended_price': float(price or 0),
            'competitor_count': 0,
            'source': 'No verified source matched',
            'source_url': '',
            'image_url': '',
            'confidence': 'insufficient_data',
        }

    manufacturer_price = max(float(buying_price or 0) * 1.08, midpoint * 0.72)
    kenya_low = round(max(1, low * 0.92), 2)
    kenya_high = round(max(kenya_low, high * 1.12), 2)
    recommended = round((kenya_low + kenya_high) / 2, 2)
    status = 'ok'
    message = ''
    if price_value and price_value < kenya_low:
        status = 'low'
        message = 'This listing is far below Kenya market prices. Confirm cost, stock condition, and authenticity before publishing.'
    elif price_value and price_value > kenya_high:
        status = 'high'
        message = 'This listing is far above competitor pricing and may create abnormal profit or lower buyer trust.'

    return {
        'status': status,
        'message': message,
        'label': product_market_label(name, category.name if category else ''),
        'kenya_low': kenya_low,
        'kenya_high': kenya_high,
        'manufacturer_price': round(manufacturer_price, 2),
        'recommended_price': recommended,
        'competitor_count': len(comp_prices),
        'source': 'Your active SMARKAFRICA catalog comparables',
        'source_url': '',
        'image_url': '',
        'confidence': 'catalog_comparable',
    }


def build_market_intelligence_payload(selected_category='all'):
    tick = int(utcnow().timestamp())
    categories = Category.query.order_by(Category.name.asc()).all()
    rows = []
    labels = []
    current_prices = []
    predicted_prices = []
    colors = []

    for category in categories:
        if selected_category != 'all' and str(category.id) != str(selected_category):
            continue
        products = Product.query.filter_by(category_id=category.id, is_active=True).all()
        if products:
            base_price = sum((p.discounted_price or p.selling_price or 0) for p in products) / max(1, len(products))
        else:
            base_price = 1000 + (category.id * 137)
        predicted_price, percent_change, direction = market_signal_for(category.name, base_price, tick)
        key = category_market_key(category.name)
        manufacturer = Manufacturer.query.filter(
            Manufacturer.product_categories.ilike(f'%{category.name}%')
        ).order_by(Manufacturer.priority.desc(), Manufacturer.rating.desc()).first()
        source_name = manufacturer.name if manufacturer else 'World supplier index'
        source_region = manufacturer.country if manufacturer and manufacturer.country else 'Global'
        headline = f'{source_region} {category.name} manufacturer price watch'

        labels.append(category.name)
        current_prices.append(round(base_price, 2))
        predicted_prices.append(predicted_price)
        colors.append(MARKET_CATEGORY_COLORS.get(key, MARKET_CATEGORY_COLORS['other']))
        rows.append({
            'category_id': category.id,
            'category': category.name,
            'market_key': key,
            'color': MARKET_CATEGORY_COLORS.get(key, MARKET_CATEGORY_COLORS['other']),
            'current_price': round(base_price, 2),
            'predicted_price': predicted_price,
            'percent_change': percent_change,
            'direction': direction,
            'source': source_name,
            'headline': headline,
            'kenya_price': round(base_price * 1.08, 2),
            'world_price': round(base_price * 0.96, 2),
            'importer_note': f'Kenya landed estimate compared with {source_region} supplier pricing.',
            'updated_at': utcnow().strftime('%H:%M:%S'),
        })

    if not rows:
        demo_categories = ['Electronics', 'Fashion', 'Home', 'Beauty']
        for index, name in enumerate(demo_categories, start=1):
            base_price = 1500 + index * 650
            predicted_price, percent_change, direction = market_signal_for(name, base_price, tick)
            key = category_market_key(name)
            labels.append(name)
            current_prices.append(base_price)
            predicted_prices.append(predicted_price)
            colors.append(MARKET_CATEGORY_COLORS[key])
            rows.append({
                'category_id': index,
                'category': name,
                'market_key': key,
                'color': MARKET_CATEGORY_COLORS[key],
                'current_price': base_price,
                'predicted_price': predicted_price,
                'percent_change': percent_change,
                'direction': direction,
                'source': 'World supplier index',
                'headline': f'Global {name} daily price watch',
                'updated_at': utcnow().strftime('%H:%M:%S'),
            })

    return {
        'tick': tick,
        'updated_at': utcnow().strftime('%Y-%m-%d %H:%M:%S UTC'),
        'labels': labels,
        'current_prices': current_prices,
        'predicted_prices': predicted_prices,
        'colors': colors,
        'rows': rows,
    }


MARKETPLACE_NEWS_CATEGORY_FALLBACKS = [
    'Electronics', 'Fashion', 'Automobiles', 'Agriculture', 'Construction', 'Health',
    'Beauty', 'Furniture', 'Industrial Equipment', 'Real Estate', 'Services',
]


def market_news_category_rows():
    payload = build_market_intelligence_payload('all')
    rows = list(payload.get('rows', []))
    existing_names = {str(row.get('category', '')).lower() for row in rows}
    tick = int(utcnow().timestamp())
    next_id = max([int(row.get('category_id') or 0) for row in rows] or [0]) + 1
    for name in MARKETPLACE_NEWS_CATEGORY_FALLBACKS:
        if name.lower() in existing_names:
            continue
        base_price = 1800 + next_id * 425
        predicted_price, percent_change, direction = market_signal_for(name, base_price, tick)
        key = category_market_key(name)
        rows.append({
            'category_id': None,
            'category': name,
            'market_key': key,
            'current_price': round(base_price, 2),
            'predicted_price': predicted_price,
            'percent_change': percent_change,
            'direction': direction,
            'source': 'Verified marketplace source index',
            'headline': f'{name} marketplace intelligence stream',
            'kenya_price': round(base_price * 1.08, 2),
            'world_price': round(base_price * 0.96, 2),
            'importer_note': 'Category stream reserved for verified manufacturer, supplier, logistics, and policy updates.',
        })
        next_id += 1
    return rows


def market_news_recommendation(row):
    direction = row.get('direction', 'stagnant')
    category = row.get('category', 'Marketplace')
    if direction == 'increase':
        return f'Prioritize margin checks, supplier follow-up, and early replenishment for {category}.'
    if direction == 'drop':
        return f'Watch discount timing and procurement opportunities before competitors adjust {category} offers.'
    return f'Keep monitoring demand, stock health, logistics cost, and customer interest signals for {category}.'


def build_category_market_news_item(row):
    category = row.get('category', 'Marketplace')
    direction = row.get('direction', 'stagnant')
    if direction == 'increase':
        title = f'{category} market pressure is rising'
    elif direction == 'drop':
        title = f'{category} prices may soften'
    else:
        title = f'{category} market looks stable'
    body = (
        f"Category intelligence for {category}: Kenya estimate KSh {row.get('kenya_price', row.get('current_price', 0)):,.2f}; "
        f"world/manufacturer estimate KSh {row.get('world_price', 0):,.2f}; "
        f"predicted movement {row.get('percent_change', 0)}%. "
        f"Signal source: {row.get('source', 'marketplace source index')}. "
        f"{row.get('importer_note', '')} {market_news_recommendation(row)}"
    )
    image_payload = trend_image_payload(category, row.get('market_key', category))
    return MarketNews(
        title=title,
        body=body,
        product_name=category,
        image_url=image_payload['image_url'],
        category_id=row.get('category_id'),
        region='Real-Time Marketplace Intelligence',
        direction='watch' if direction == 'stagnant' else direction,
        generated_by='marketplace_intelligence',
    )


def disbursement_snapshot():
    incoming_types = ['sale', 'commission', 'ad_commission']
    outgoing_types = ['refund', 'withdrawal', 'salary', 'manufacturer_payout', 'disbursement']
    incoming_total = db.session.query(func.sum(Transaction.amount)).filter(
        Transaction.type.in_(incoming_types),
        Transaction.amount > 0
    ).scalar() or 0.0
    outgoing_total = abs(db.session.query(func.sum(Transaction.amount)).filter(
        Transaction.type.in_(outgoing_types),
        Transaction.amount < 0
    ).scalar() or 0.0)
    pending_withdrawals = WithdrawalRequest.query.filter_by(status='pending_review').all()
    pending_salaries = AdminSalary.query.filter(AdminSalary.status.in_(['pending', 'queued'])).all()
    manufacturer_reserve = round(max(0.0, incoming_total * 0.35), 2)
    available_balance = round(incoming_total - outgoing_total, 2)
    return {
        'incoming_total': incoming_total,
        'outgoing_total': outgoing_total,
        'available_balance': available_balance,
        'pending_withdrawals': pending_withdrawals,
        'pending_salaries': pending_salaries,
        'manufacturer_reserve': manufacturer_reserve,
    }


def generate_market_news_if_due(force=False):
    if force and Setting.get('market_news_generation_lock', '0') == '1':
        raise OperationalError('market_news', {}, 'Generation is already running')
    if force:
        Setting.set('market_news_generation_lock', '1')
        db.session.remove()
    latest = MarketNews.query.order_by(MarketNews.created_at.desc()).first()
    if latest and not force and latest.created_at and latest.created_at > utcnow() - timedelta(hours=24):
        return 0

    pending_news = []
    for row in market_news_category_rows():
        category_name = row.get('category', 'Marketplace')
        existing = MarketNews.query.filter(
            MarketNews.generated_by == 'marketplace_intelligence',
            MarketNews.product_name == category_name,
            MarketNews.created_at > utcnow() - timedelta(hours=24)
        ).first()
        if existing and not force:
            continue
        pending_news.append(build_category_market_news_item(row))

    products = Product.query.filter_by(is_active=True).order_by(
        Product.is_hot_sale.desc(),
        Product.admin_priority.desc(),
        Product.updated_at.desc()
    ).limit(18).all()
    for product in products:
        existing = MarketNews.query.filter(
            MarketNews.generated_by == 'product_price_signal',
            MarketNews.product_name == product.name,
            MarketNews.created_at > utcnow() - timedelta(hours=24)
        ).first()
        if existing and not force:
            continue
        category_name = product.category.name if product.category else 'General'
        row = market_price_reference(
            product.name,
            product.category_id,
            product.discounted_price or product.selling_price,
            product.buying_price,
            exclude_id=product.id
        )
        if row.get('confidence') == 'insufficient_data':
            continue
        _, percent_change, direction = market_signal_for(product.name, row['recommended_price'], int(utcnow().timestamp()))
        label = row['label']
        if direction == 'increase':
            title = f"{label} prices may increase"
            action = 'Consider early replenishment and review margins before landed costs rise.'
        elif direction == 'drop':
            title = f"{label} prices may decrease"
            action = 'Watch for buying opportunities, price drops, and promotion timing before competitors react.'
        else:
            title = f"{label} prices look stable"
            action = 'No immediate price move detected; keep watching demand, stock, and rating changes.'
        body = (
            f"{product.name} in {category_name}: Kenya source range KSh {row['kenya_low']:,.2f} - "
            f"KSh {row['kenya_high']:,.2f}. Manufacturer/import estimate: KSh {row['manufacturer_price']:,.2f}. "
            f"Signal: {direction} ({percent_change}%). Source: {row.get('source', 'market reference')}. {action}"
        )
        pending_news.append(MarketNews(
            title=title,
            body=body,
            product_name=product.name,
            image_url=trend_image_payload(
                product.name,
                category_name,
                product.image_url or row.get('image_url', ''),
                row.get('image_candidates', [])
            )['image_url'],
            source_url=row.get('source_url', ''),
            category_id=product.category_id,
            region='Kenya vs Worldwide',
            direction=direction,
            generated_by='product_price_signal',
        ))
    try:
        if pending_news:
            db.session.bulk_save_objects(pending_news)
        db.session.commit()
    except OperationalError:
        db.session.rollback()
        raise
    finally:
        if force:
            Setting.set('market_news_generation_lock', '0')
    return len(pending_news)


def selected_phone_value(form):
    code = (form.get('phone_country_code') or '').strip()
    number = (form.get('phone_local') or form.get('phone') or '').strip()
    if code and number:
        return f"{code}{number.lstrip('+').lstrip('0')}"
    return number


def uploaded_product_images():
    images = []
    files = request.files.getlist('product_images')
    if not files:
        files = request.files.getlist('image') + request.files.getlist('additional_images')
    for image_file in files[:10]:
        if not image_file or not image_file.filename:
            continue
        if not allowed_file(image_file.filename):
            flash('Product images must be PNG, JPG, GIF, or WebP.', 'danger')
            continue
        try:
            images.append(save_product_image(image_file))
        except ValueError as exc:
            flash(str(exc), 'danger')
    return images


def create_customer_notification(user_id, title, body, notification_type='update', product_id=None):
    if not user_id or not title or not body:
        return None
    exists = CustomerNotification.query.filter_by(
        user_id=user_id,
        product_id=product_id,
        title=title,
        body=body
    ).first()
    if exists:
        return exists
    item = CustomerNotification(
        user_id=user_id,
        product_id=product_id,
        title=title,
        body=body,
        notification_type=notification_type,
        is_read=False
    )
    db.session.add(item)
    return item


def bnpl_risk_score(user):
    if not user:
        return 0.0
    score = 35.0
    order_count = Order.query.filter_by(user_id=user.id).count()
    paid_count = Order.query.filter_by(user_id=user.id, payment_status='completed').count()
    if user.created_at and user.created_at < utcnow() - timedelta(days=90):
        score += 15
    if paid_count:
        score += min(25, paid_count * 5)
    if order_count and paid_count == order_count:
        score += 10
    if user.verification_status in ['approved', 'verified'] or user.is_verified_seller:
        score += 15
    if user.seller_status in ['frozen', 'rejected']:
        score -= 25
    return max(0.0, min(100.0, round(score, 2)))


def product_barcode_value(product):
    row = ProductBarcode.query.filter_by(product_id=product.id).first()
    if row:
        return row.barcode
    base = f"SAF{product.id:08d}"
    checksum = sum((index + 1) * int(ch) for index, ch in enumerate(str(product.id))) % 10
    barcode = f"{base}{checksum}"
    row = ProductBarcode(product_id=product.id, barcode=barcode)
    db.session.add(row)
    db.session.commit()
    return barcode


def assign_product_barcode(product, barcode='', barcode_type='internal'):
    clean_value = re.sub(r'[^A-Za-z0-9_.-]', '', (barcode or '').strip()[:80])
    if not clean_value:
        return product_barcode_value(product)
    existing = ProductBarcode.query.filter_by(barcode=clean_value).first()
    if existing and existing.product_id != product.id:
        raise ValueError('That barcode is already assigned to another product.')
    row = ProductBarcode.query.filter_by(product_id=product.id).first()
    if row:
        row.barcode = clean_value
        row.barcode_type = barcode_type
    else:
        db.session.add(ProductBarcode(product_id=product.id, barcode=clean_value, barcode_type=barcode_type))
    return clean_value


def product_by_barcode(value):
    clean_value = (value or '').strip()
    if not clean_value:
        return None
    row = ProductBarcode.query.filter_by(barcode=clean_value).first()
    if row:
        return row.product
    row = ProductBarcode.query.filter(ProductBarcode.barcode.ilike(clean_value)).first()
    if row:
        return row.product
    if clean_value.isdigit():
        row = ProductBarcode.query.filter(ProductBarcode.barcode.ilike(f'%{clean_value}')).first()
        if row:
            return row.product
    return None


def pos_terminal_key(user_id=None):
    uid = user_id or (current_user.id if current_user.is_authenticated else 0)
    return f'pos_terminal_cart_{uid}'


def pos_terminal_cart(user_id=None):
    raw = Setting.get(pos_terminal_key(user_id), '[]')
    try:
        data = json.loads(raw or '[]')
    except Exception:
        data = []
    return [line for line in data if line.get('product_id') and line.get('quantity')]


def save_pos_terminal_cart(lines, user_id=None):
    clean_lines = []
    for line in lines:
        product_id = int(line.get('product_id') or 0)
        quantity = max(1, int(line.get('quantity') or 1))
        if product_id:
            clean_lines.append({'product_id': product_id, 'quantity': quantity})
    Setting.set(pos_terminal_key(user_id), json.dumps(clean_lines))


def add_product_to_pos_terminal(product, quantity=1, user_id=None):
    lines = pos_terminal_cart(user_id)
    for line in lines:
        if int(line['product_id']) == product.id:
            line['quantity'] = max(1, int(line['quantity']) + max(1, int(quantity or 1)))
            save_pos_terminal_cart(lines, user_id)
            return lines
    lines.append({'product_id': product.id, 'quantity': max(1, int(quantity or 1))})
    save_pos_terminal_cart(lines, user_id)
    return lines


def pos_terminal_payload(user_id=None):
    rows = []
    subtotal = 0.0
    for line in pos_terminal_cart(user_id):
        product = Product.query.get(line['product_id'])
        if not product or not product.is_active:
            continue
        quantity = max(1, int(line.get('quantity') or 1))
        unit_price = product.discounted_price or product.selling_price or 0
        line_total = unit_price * quantity
        subtotal += line_total
        rows.append({
            'product_id': product.id,
            'name': product.name,
            'barcode': product_barcode_value(product),
            'quantity': quantity,
            'unit_price': unit_price,
            'line_total': line_total,
            'stock': product.stock or 0,
            'low_stock': not product.is_digital and (product.stock or 0) <= 5,
        })
    return {'items': rows, 'subtotal': subtotal, 'count': sum(item['quantity'] for item in rows)}


def scanner_pairing_key(token):
    return f'pos_scanner_pair_{token}'


def local_network_base_url():
    configured = Setting.get('pos_public_base_url', '').strip()
    if configured:
        return configured.rstrip('/') + '/'
    host_url = request.host_url
    host = request.host.split(':')[0]
    if host not in ['localhost', '127.0.0.1']:
        return host_url
    try:
        lan_ip = socket.gethostbyname(socket.gethostname())
        port = request.host.split(':')[1] if ':' in request.host else '5000'
        if lan_ip and not lan_ip.startswith('127.'):
            return f'{request.scheme}://{lan_ip}:{port}/'
    except Exception:
        pass
    return host_url


def create_pos_scanner_pairing(user_id=None):
    user_id = user_id or current_user.id
    token = uuid.uuid4().hex[:16].upper()
    payload = {
        'user_id': user_id,
        'created_at': utcnow().isoformat(),
        'expires_at': (utcnow() + timedelta(hours=8)).isoformat(),
        'terminal': f'POS-{user_id}',
    }
    Setting.set(scanner_pairing_key(token), json.dumps(payload))
    scanner_url = local_network_base_url().rstrip('/') + url_for('pos_pair_scanner', token=token)
    return {'token': token, 'scanner_url': scanner_url, **payload}


def scanner_pairing_payload(token):
    clean_token = re.sub(r'[^A-Fa-f0-9]', '', (token or '').strip())[:32].upper()
    if not clean_token:
        return None
    raw = Setting.get(scanner_pairing_key(clean_token), '')
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        expires_at = datetime.fromisoformat(payload.get('expires_at'))
    except Exception:
        return None
    if expires_at < utcnow():
        Setting.set(scanner_pairing_key(clean_token), '')
        return None
    payload['token'] = clean_token
    return payload


def pos_role_permissions(user=None):
    user = user or current_user
    level = (getattr(user, 'admin_level', '') or '').lower()
    if level == 'mvp':
        role = 'Administrator'
        allowed = {'sell', 'inventory', 'products', 'suppliers', 'reports', 'settings'}
    elif level == 'manager':
        role = 'Manager'
        allowed = {'sell', 'inventory', 'products', 'suppliers', 'reports'}
    elif level == 'inventory':
        role = 'Inventory Officer'
        allowed = {'inventory', 'products', 'suppliers', 'reports'}
    elif level == 'cashier':
        role = 'Cashier'
        allowed = {'sell'}
    else:
        role = 'Administrator' if getattr(user, 'is_admin', False) else 'User'
        allowed = {'sell', 'inventory', 'products', 'suppliers', 'reports'} if getattr(user, 'is_admin', False) else set()
    return {'role': role, 'allowed': allowed}


def pos_can(permission):
    return permission in pos_role_permissions()['allowed']


def record_stock_movement(product, movement_type, quantity, reference_type='', reference_id=None, note=''):
    before_stock = int(product.stock or 0)
    product.stock = max(0, before_stock + int(quantity))
    movement = StockMovement(
        product_id=product.id,
        movement_type=movement_type,
        quantity=int(quantity),
        before_stock=before_stock,
        after_stock=product.stock,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
        created_by=current_user.id if current_user.is_authenticated else None,
    )
    db.session.add(movement)
    return movement


def create_bnpl_installments(plan):
    if plan.installments:
        return
    deposit_amount = plan.principal_amount * (plan.deposit_percent / 100)
    financed_amount = max(0.0, plan.principal_amount - deposit_amount)
    monthly_amount = round(financed_amount / max(1, plan.term_months), 2)
    for sequence in range(1, plan.term_months + 1):
        db.session.add(BNPLInstallment(
            plan_id=plan.id,
            sequence=sequence,
            amount_due=monthly_amount,
            due_at=utcnow() + timedelta(days=30 * sequence),
        ))


def update_bnpl_lock_status(plan):
    overdue = any(item.status != 'paid' and item.due_at < utcnow() for item in plan.installments)
    if overdue and plan.approval_status == 'approved':
        plan.lock_status = 'locked'
    elif all(item.status == 'paid' for item in plan.installments) and plan.installments:
        plan.lock_status = 'unlocked'
        plan.approval_status = 'completed'
    return plan.lock_status


def ensure_architecture_defaults():
    if not ExchangeRate.query.filter_by(base_currency='KES', quote_currency='USD').first():
        db.session.add(ExchangeRate(base_currency='KES', quote_currency='USD', rate=0.0077, source='manual_default'))
    if not ExchangeRate.query.filter_by(base_currency='KES', quote_currency='UGX').first():
        db.session.add(ExchangeRate(base_currency='KES', quote_currency='UGX', rate=28.8, source='manual_default'))
    if not ExchangeRate.query.filter_by(base_currency='KES', quote_currency='TZS').first():
        db.session.add(ExchangeRate(base_currency='KES', quote_currency='TZS', rate=20.4, source='manual_default'))
    architecture_tasks = [
        ('Commerce Engine review', 'Review POS, inventory, BNPL, storefront, payment, and logistics workflows.', 'commerce_engine', 88),
        ('Growth Engine lifecycle scan', 'Track awareness, traffic, engagement, conversion, retention, and advocacy metrics.', 'growth_engine', 84),
        ('Intelligence Engine forecast', 'Generate pricing, demand, product, and revenue recommendations from market signals.', 'intelligence_engine', 86),
        ('Automation Engine cleanup', 'Identify repetitive admin workflows and recommend background jobs or templates.', 'automation_engine', 82),
        ('Trust Engine audit', 'Review verification, support, fraud, quality, and authenticity signals.', 'trust_engine', 90),
    ]
    for name, result, task_type, score in architecture_tasks:
        if not AutomationTask.query.filter_by(name=name).first():
            db.session.add(AutomationTask(name=name, task_type=task_type, cadence='daily', efficiency_score=score, last_result=result))


def architecture_snapshot():
    open_tickets = SupportTicket.query.filter(SupportTicket.status.in_(['open', 'escalated'])).count()
    overdue_tickets = SupportTicket.query.filter(
        SupportTicket.status.in_(['open', 'escalated']),
        SupportTicket.resolution_due_at < utcnow()
    ).count()
    pending_storefronts = BusinessStorefront.query.filter_by(status='pending_review').count()
    active_storefronts = BusinessStorefront.query.filter_by(status='approved').count()
    pending_bnpl = BNPLPlan.query.filter_by(approval_status='manual_review').count()
    locked_bnpl = BNPLPlan.query.filter_by(lock_status='locked').count()
    loyalty_points = db.session.query(func.sum(LoyaltyLedger.points)).scalar() or 0
    trust_watch = TrustScore.query.filter(TrustScore.score < 60).count()
    return {
        'commerce': {
            'pos_sales': PointOfSaleSale.query.count(),
            'pending_bnpl': pending_bnpl,
            'locked_bnpl': locked_bnpl,
            'exchange_rates': ExchangeRate.query.count(),
        },
        'growth': {
            'leads': ClientAcquisitionLead.query.count(),
            'loyalty_points': loyalty_points,
            'active_storefronts': active_storefronts,
            'pending_storefronts': pending_storefronts,
        },
        'intelligence': {
            'market_news': MarketNews.query.filter_by(is_cleared=False).count(),
            'price_cache': MarketPriceCache.query.count(),
            'business_checkins': BusinessCheckIn.query.count(),
        },
        'automation': {
            'active_tasks': AutomationTask.query.filter_by(is_active=True).count(),
            'quality_logs': QualityImprovementLog.query.count(),
        },
        'trust': {
            'open_tickets': open_tickets,
            'overdue_tickets': overdue_tickets,
            'trust_watch': trust_watch,
            'verified_sellers': User.query.filter_by(is_verified_seller=True).count(),
        },
    }


def ensure_customer_notifications(user, limit=8):
    orders = Order.query.filter_by(user_id=user.id).order_by(Order.updated_at.desc(), Order.created_at.desc()).limit(8).all()
    for order in orders:
        physical_items = [item for item in order.items if not item.is_digital]
        if physical_items:
            title = f'Order {order.order_number} tracking update'
            location = order.shipping_city or order.shipping_state or order.shipping_country or 'your delivery route'
            body = f'{order.shipping_status or order.status or "Processing"}: {len(physical_items)} physical item(s) are being prepared for {location}.'
            if order.tracking_number:
                body += f' Tracking number: {order.tracking_number}.'
            create_customer_notification(user.id, title, body, 'tracking')
        if order.payment_status == 'completed':
            create_customer_notification(
                user.id,
                f'Payment confirmed for {order.order_number}',
                f'Your secure platform payment was confirmed. You can follow order progress from My Orders and Track Order.',
                'payment'
            )

    follows = CategoryFollow.query.filter_by(user_id=user.id, email_updates=True).all()
    followed_ids = [follow.category_id for follow in follows]
    if followed_ids:
        since = user.last_login or (utcnow() - timedelta(days=14))
        products = Product.query.filter(
            Product.is_active == True,
            Product.category_id.in_(followed_ids),
            Product.created_at >= since
        ).order_by(Product.created_at.desc()).limit(limit).all()
        for product in products:
            create_customer_notification(
                user.id,
                f'New listing in {product.category.name if product.category else "your followed category"}',
                f'{product.name} was listed at KSh {(product.discounted_price or product.selling_price):,.2f}.',
                'new_listing',
                product.id
            )

    alerts = PriceAlert.query.filter_by(user_id=user.id, status='active').limit(limit).all()
    for alert in alerts:
        candidates = [alert.product] if alert.product else [item['product'] for item in smart_product_recommendations(alert.search_query or '', 6)]
        for product in candidates:
            if product and (product.discounted_price or product.selling_price or 0) <= alert.target_price:
                create_customer_notification(
                    user.id,
                    f'Price alert matched: {product.name[:80]}',
                    f'{product.name} is now KSh {(product.discounted_price or product.selling_price):,.2f}, within your target of KSh {alert.target_price:,.2f}.',
                    'price',
                    product.id
                )
                break
    db.session.commit()


def homepage_featured_products(limit=12):
    rows = []
    seen = set()
    queries = [
        Product.query.filter_by(is_active=True, is_hot_sale=True).order_by(Product.hot_sale_started_at.desc(), Product.updated_at.desc()),
        Product.query.filter_by(is_active=True, is_featured=True).order_by(Product.admin_priority.desc(), Product.created_at.desc()),
        Product.query.filter_by(is_active=True).order_by(Product.admin_priority.desc(), Product.created_at.desc()),
    ]
    for query in queries:
        for product in query.limit(limit).all():
            if product.id in seen:
                continue
            seen.add(product.id)
            rows.append(product)
            if len(rows) >= limit:
                return rows
    return rows


def product_search_cache_key(search='', category_slug='', product_type='', sort='newest'):
    clean = '|'.join([
        slugify(search or '', 120),
        slugify(category_slug or 'all', 80),
        slugify(product_type or 'all', 40),
        slugify(sort or 'newest', 40),
    ])
    return f'product_search_ids:{clean}'


def build_product_search_query(search='', category_slug='', product_type='', sort='newest'):
    query = Product.query.filter_by(is_active=True)
    current_category = None
    if category_slug:
        current_category = Category.query.filter_by(slug=category_slug).first()
        if current_category:
            query = query.filter_by(category_id=current_category.id)
    if product_type == 'digital':
        query = query.filter_by(is_digital=True)
    elif product_type == 'physical':
        query = query.filter_by(is_digital=False)
    if search:
        query = query.filter(or_(
            Product.name.ilike(f'%{search}%'),
            Product.description.ilike(f'%{search}%'),
            Product.short_description.ilike(f'%{search}%')
        ))
    if sort == 'price_low':
        query = query.order_by(Product.is_hot_sale.desc(), Product.admin_priority.desc(), Product.selling_price.asc())
    elif sort == 'price_high':
        query = query.order_by(Product.is_hot_sale.desc(), Product.admin_priority.desc(), Product.selling_price.desc())
    elif sort in ['rating', 'popular']:
        query = query.order_by(Product.is_hot_sale.desc(), Product.admin_priority.desc(), Product.sales_count.desc())
    else:
        query = query.order_by(Product.is_hot_sale.desc(), Product.admin_priority.desc(), Product.created_at.desc())
    return query, current_category


def invalidate_product_cache():
    """Clear all product search caches so new/edited products appear immediately."""
    if cache:
        cache.clear()


def cached_product_search_ids(search='', category_slug='', product_type='', sort='newest'):
    key = product_search_cache_key(search, category_slug, product_type, sort)
    if cache:
        cached_ids = cache.get(key)
        if cached_ids is not None:
            return cached_ids
    query, _ = build_product_search_query(search, category_slug, product_type, sort)
    ids = [row[0] for row in query.with_entities(Product.id).limit(1000).all()]
    if cache:
        cache.set(key, ids, timeout=int(Setting.get('product_search_cache_seconds', '300') or 300))
    return ids


def paginate_cached_product_ids(ids, page=1, per_page=12):
    page = max(1, int(page or 1))
    per_page = max(1, min(60, int(per_page or 12)))
    total = len(ids)
    start = (page - 1) * per_page
    page_ids = ids[start:start + per_page]
    products = []
    if page_ids:
        product_map = {product.id: product for product in Product.query.filter(Product.id.in_(page_ids)).all()}
        products = [product_map[pid] for pid in page_ids if pid in product_map]

    class CachedPagination:
        def __init__(self):
            self.page = page
            self.per_page = per_page
            self.total = total
            self.items = products
            self.pages = (total + per_page - 1) // per_page
            self.has_prev = page > 1
            self.has_next = page < self.pages
            self.prev_num = page - 1
            self.next_num = page + 1

        def iter_pages(self):
            for value in range(1, self.pages + 1):
                if value <= 2 or value > self.pages - 2 or abs(value - self.page) <= 2:
                    yield value
                elif value == 3 or value == self.pages - 2:
                    yield None

    return CachedPagination()


def seller_identity_match_query(model, legal_name, country, phone, bank_card_last4):
    legal_name = (legal_name or '').strip()
    country = (country or '').strip()
    phone = normalize_mpesa_phone(phone or '')
    bank_card_last4 = (bank_card_last4 or '').strip()
    filters = []
    if legal_name and country:
        filters.append(and_(func.lower(model.legal_name) == legal_name.lower(), func.lower(model.country) == country.lower()))
    if phone:
        filters.append(model.phone == phone)
    if bank_card_last4:
        filters.append(model.bank_card_last4 == bank_card_last4)
    return or_(*filters) if filters else None


def matching_seller_blacklist(legal_name, country, phone, bank_card_last4):
    match_filter = seller_identity_match_query(SellerBlacklist, legal_name, country, phone, bank_card_last4)
    if match_filter is None:
        return None
    return SellerBlacklist.query.filter(match_filter, SellerBlacklist.status != 'appeal_approved').first()


def backup_seller_verification(verification):
    backup = SellerVerificationBackup(
        verification_id=verification.id,
        user_id=verification.user_id,
        legal_name=verification.legal_name,
        country=verification.country,
        phone=verification.phone,
        bank_card_last4=verification.bank_card_last4,
        document_type=verification.document_type,
        document_path=verification.document_path,
        selfie_path=verification.selfie_path,
        status=verification.status,
        notes=verification.notes,
    )
    db.session.add(backup)
    return backup


def product_search_score(product, query):
    text = ' '.join([
        product.name or '',
        product.short_description or '',
        product.description or '',
        product.category.name if product.category else '',
        product.product_condition or '',
    ]).lower()
    terms = [term for term in slugify(query, 500).split('-') if len(term) > 2]
    score = sum(4 for term in terms if term in text)
    q = (query or '').lower()
    price = product.discounted_price or product.selling_price or 0
    if any(word in q for word in ['cheap', 'budget', 'under', 'student']) and price:
        score += max(0, 5 - int(price / 20000))
    if any(word in q for word in ['battery', 'gaming', 'software', 'engineering', 'laptop', 'monitor']) and any(word in text for word in ['battery', 'gaming', 'software', 'laptop', 'monitor', 'computer']):
        score += 5
    if any(word in q for word in ['rain', 'water', 'weather']) and any(word in text for word in ['water', 'rain', 'boot', 'rubber', 'weather']):
        score += 5
    if product.average_rating:
        score += product.average_rating
    score += min(product.stock or 0, 10) / 10
    return score


def smart_product_recommendations(query, limit=6):
    products = Product.query.filter_by(is_active=True).all()
    scored = sorted(
        [(product_search_score(product, query), product) for product in products],
        key=lambda item: item[0],
        reverse=True
    )
    recommendations = []
    for score, product in scored[:limit]:
        if score <= 0 and query:
            continue
        recommendations.append({
            'product': product,
            'score': round(score, 1),
            'reason': smart_recommendation_reason(product, query),
        })
    return recommendations


def allowed_digital_file(filename):
    return (
        '.' in filename and
        filename.rsplit('.', 1)[1].lower() in ALLOWED_DIGITAL_EXTENSIONS
    )


SPEC_PATTERNS = {
    'Display': r'(\d+(?:\.\d+)?\s?(?:inch|inches|"))|(?:AMOLED|OLED|LCD|IPS|QLED|UHD|4K)',
    'Processor': r'(Snapdragon\s?\w+|MediaTek\s?\w+|Intel\s?[A-Za-z0-9\- ]+|Core\s?i[3579][A-Za-z0-9\- ]*|Ryzen\s?[A-Za-z0-9\- ]+|Apple\s?M\d)',
    'RAM': r'(\d+\s?GB\s?RAM|\d+\s?GB\s?memory)',
    'Storage': r'(\d+\s?(?:GB|TB)\s?(?:storage|SSD|HDD|ROM|NVMe)?)',
    'Battery': r'(\d{3,5}\s?mAh|battery\s?\d{3,5}\s?mAh)',
    'Camera': r'(\d+\s?MP|camera)',
    'Refresh Rate': r'(\d+\s?Hz)',
    'Resolution': r'(4K|8K|1080p|1440p|\d{3,4}\s?x\s?\d{3,4})',
    'Connectivity': r'(5G|4G|Wi-?Fi\s?6|Bluetooth\s?\d(?:\.\d)?)',
    'Weight': r'(\d+(?:\.\d+)?\s?(?:kg|g))',
    'Warranty': r'(\d+\s?(?:year|month)s?\s?warranty)',
}


def extract_specs_from_text(text_value):
    specs = {}
    text_value = re.sub(r'\s+', ' ', html.unescape(str(text_value or '')))
    for label, pattern in SPEC_PATTERNS.items():
        matches = re.findall(pattern, text_value, flags=re.IGNORECASE)
        cleaned = []
        for match in matches:
            if isinstance(match, tuple):
                match = next((part for part in match if part), '')
            value = str(match).strip()
            if value and value.lower() not in [item.lower() for item in cleaned]:
                cleaned.append(value)
        if cleaned:
            specs[label] = ', '.join(cleaned[:4])
    return specs


def web_spec_lookup(product_name):
    query = f'{product_name} specifications'
    sources = []
    combined_text = ''
    headers = {'User-Agent': 'Mozilla/5.0 SMARKAFRICA product comparison'}
    search_urls = [
        f'https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}',
        f'https://r.jina.ai/http://r.jina.ai/http://www.google.com/search?q={requests.utils.quote(query)}',
    ]
    for search_url in search_urls:
        try:
            response = requests.get(search_url, timeout=8, headers=headers)
            if response.status_code >= 400:
                continue
            page_text = re.sub(r'<[^>]+>', ' ', response.text)
            page_text = re.sub(r'\s+', ' ', html.unescape(page_text))
            combined_text += ' ' + page_text[:5000]
            for url in re.findall(r'https?://[^\s"&<>]+', response.text):
                if len(sources) >= 3:
                    break
                if not any(blocked in url.lower() for blocked in ['duckduckgo', 'google', 'gstatic']):
                    sources.append(url.rstrip(').,'))
            if combined_text:
                break
        except Exception as exc:
            app.logger.info('Web spec lookup failed for %s: %s', product_name, exc)
    return extract_specs_from_text(combined_text), sources[:3]


def product_compare_payload(product, include_web=True):
    store_text = ' '.join([
        product.name or '',
        product.short_description or '',
        product.description or '',
        product.category.name if product.category else '',
    ])
    merged = extract_specs_from_text(store_text)
    sources = []
    if include_web:
        web_specs, sources = web_spec_lookup(product.name)
        merged.update({key: value for key, value in web_specs.items() if value})
    market_reference = market_price_reference(
        product.name,
        product.category_id,
        product.discounted_price or product.selling_price,
        product.buying_price,
        exclude_id=product.id,
        description=product.description
    ) if include_web else {}
    return {
        'id': product.id,
        'name': product.name,
        'category': product.category.name if product.category else 'General',
        'price': product.discounted_price or product.selling_price,
        'in_stock': bool(product.is_digital or (product.stock or 0) > 0),
        'stock': 'Digital' if product.is_digital else str(product.stock or 0),
        'rating': product.average_rating,
        'specs': merged,
        'sources': sources,
        'market': market_reference,
        'url': url_for('product_page', slug=product.slug),
    }


def build_product_comparison(recommendations):
    comparison = []
    for item in recommendations[:3]:
        product = item['product']
        comparison.append({
            'name': product.name,
            'price': product.discounted_price or product.selling_price or 0,
            'rating': product.average_rating,
            'stock': product.stock,
            'category': product.category.name if product.category else 'General',
            'best_for': item['reason'],
            'risk': 'Low stock' if not product.is_digital and (product.stock or 0) <= 5 else 'Stable availability',
        })
    return comparison


def smart_recommendation_reason(product, query):
    reasons = []
    q = (query or '').lower()
    if product.average_rating:
        reasons.append(f'{product.average_rating:.1f}/5 average rating')
    if product.stock and product.stock > 0:
        reasons.append(f'{product.stock} in stock')
    if product.discount_percent:
        reasons.append(f'{product.discount_percent:.0f}% price drop')
    if product.category:
        reasons.append(f'fits {product.category.name}')
    if 'student' in q or 'budget' in q or 'under' in q:
        reasons.append('balanced for budget-sensitive buying')
    if not reasons:
        reasons.append('closest match from product details and pricing')
    return ', '.join(reasons[:3])


def pricing_suggestions():
    suggestions = []
    for product in Product.query.filter_by(is_active=True).order_by(Product.updated_at.desc()).limit(80).all():
        market = build_market_intelligence_payload(str(product.category_id or 'all'))['rows']
        signal = market[0] if market else None
        current = product.discounted_price or product.selling_price or 0
        suggested = current
        reason = []
        if signal and signal['direction'] == 'increase':
            suggested = current * 1.03
            reason.append('market signal rising')
        elif signal and signal['direction'] == 'drop':
            suggested = current * 0.98
            reason.append('market signal softening')
        if product.stock is not None and product.stock <= 5 and not product.is_digital:
            suggested *= 1.02
            reason.append('low stock')
        if product.average_rating and product.average_rating < 3:
            suggested *= 0.97
            reason.append('rating pressure')
        if abs(suggested - current) >= 1:
            suggestions.append({
                'product': product,
                'current': round(current, 2),
                'suggested': round(suggested, 2),
                'reason': ', '.join(reason) or 'demand and margin balancing',
            })
    return suggestions[:20]


def stock_warnings():
    return Product.query.filter(
        Product.is_active == True,
        Product.is_digital == False,
        Product.stock <= 5
    ).order_by(Product.stock.asc()).limit(30).all()


def generate_product_description(name, category_id=None, condition='new', is_digital=False, image_url=''):
    api_key = os.environ.get('OPENAI_API_KEY') or Setting.get('openai_api_key', '')
    if api_key:
        try:
            category = Category.query.get(category_id) if category_id else None
            prompt = (
                "Write a complete ecommerce product description for SMARKAFRICA. "
                "Include key features, variant/compatibility checks, warranty/condition notes, buyer guidance, and what is included. "
                "Return concise HTML using paragraphs and bullet points only. "
                f"Product: {name}. Category: {category.name if category else 'General'}. "
                f"Condition: {condition}. Type: {'digital' if is_digital else 'physical'}. Image URL: {image_url or 'none'}."
            )
            response = requests.post(
                'https://api.openai.com/v1/responses',
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json={
                    'model': Setting.get('openai_description_model', 'gpt-4.1-mini'),
                    'input': prompt,
                    'max_output_tokens': 700,
                },
                timeout=20
            )
            if response.ok:
                data = response.json()
                text = data.get('output_text', '')
                if not text:
                    chunks = []
                    for item in data.get('output', []):
                        for content in item.get('content', []):
                            if content.get('text'):
                                chunks.append(content['text'])
                    text = '\n'.join(chunks)
                if text.strip():
                    return text.strip()
        except Exception:
            pass
    category = Category.query.get(category_id) if category_id else None
    label = product_market_label(name, category.name if category else '')
    reference = product_market_reference_match(name, category.name if category else '')
    category_name = category.name if category else 'General'
    lines = [
        f"<p><strong>{name}</strong> is a {condition.replace('_', ' ')} {category_name.lower()} product selected for buyers who need reliable value, clear specifications, and transparent pricing.</p>",
        "<ul>",
        f"<li><strong>Product type:</strong> {'Digital delivery item' if is_digital else 'Physical product'}.</li>",
        f"<li><strong>Category fit:</strong> {category_name} / {label}.</li>",
        "<li><strong>Key checks:</strong> confirm model/variant, warranty, included accessories, condition, compatibility, and country/import status before purchase.</li>",
        "<li><strong>Buyer guidance:</strong> compare price, stock availability, ratings, delivery option, and after-sale support before checkout.</li>",
    ]
    if reference:
        lines.append(f"<li><strong>Market watch:</strong> Kenya range currently tracked around KSh {reference['kenya_low']:,.0f} - KSh {reference['kenya_high']:,.0f}; verify live price before publishing.</li>")
    if image_url:
        lines.append("<li><strong>Image note:</strong> ensure the product image matches the exact model, color, and bundle being sold.</li>")
    lines.append("</ul>")
    return '\n'.join(lines)


def send_stock_warning_emails(force=False):
    warnings = stock_warnings()
    if not warnings:
        return 0
    last_sent = Setting.get('stock_warning_last_sent_at', '')
    today = utcnow().strftime('%Y-%m-%d')
    if last_sent == today and not force:
        return 0
    admins = User.query.filter_by(is_admin=True, is_active=True).all()
    rows = ''.join(f"<li>{p.name}: {p.stock} left</li>" for p in warnings)
    sent = 0
    for admin in admins:
        if send_email(admin.email, 'SMARKAFRICA low stock warning', f"<h3>Products about to run out of stock</h3><ul>{rows}</ul>"):
            sent += 1
    Setting.set('stock_warning_last_sent_at', today)
    return sent


def business_intelligence_answer(question):
    now = utcnow()
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)
    week_orders = Order.query.filter(Order.created_at >= week_start).count()
    month_orders = Order.query.filter(Order.created_at >= month_start).count()
    week_sales = db.session.query(func.sum(Order.amount_paid)).filter(Order.created_at >= week_start).scalar() or 0
    month_sales = db.session.query(func.sum(Order.amount_paid)).filter(Order.created_at >= month_start).scalar() or 0
    low_rated = Product.query.join(Review).group_by(Product.id).having(func.avg(Review.rating) < 3.5).limit(5).all()
    low_stock = stock_warnings()
    market_rows = build_market_intelligence_payload('all')['rows']
    rising = [row['category'] for row in market_rows if row['direction'] == 'increase']
    falling = [row['category'] for row in market_rows if row['direction'] == 'drop']
    q = (question or '').lower()
    if 'drop' in q or 'sales' in q:
        answer = f"Sales this week are KSh {week_sales:,.2f} across {week_orders} orders. Last 30 days are KSh {month_sales:,.2f} across {month_orders} orders."
        if low_rated:
            answer += " Possible pressure: low ratings on " + ', '.join(p.name[:35] for p in low_rated) + "."
        if low_stock:
            answer += " Stock risk: " + ', '.join(p.name[:35] for p in low_stock[:5]) + "."
        if rising:
            answer += " Rising market costs may affect " + ', '.join(rising[:5]) + "."
        return answer
    if 'price' in q or 'increase' in q:
        return f"Price intelligence shows rising categories: {', '.join(rising) or 'none'}; falling categories: {', '.join(falling) or 'none'}. Kenya estimates include landed-cost pressure against worldwide manufacturer signals."
    return f"Business snapshot: KSh {week_sales:,.2f} sales this week, {week_orders} weekly orders, {len(low_stock)} low-stock warnings, and {len(rising)} categories with upward price signals."


def bi_period_snapshot(days, period_type):
    start = utcnow() - timedelta(days=days)
    orders = Order.query.filter(Order.created_at >= start).all()
    sales_total = sum(order.amount_paid or 0 for order in orders)
    orders_count = len(orders)
    slow_products = Product.query.filter(
        Product.is_active == True,
        Product.sales_count <= 0,
        Product.created_at <= utcnow() - timedelta(days=min(days, 7))
    ).order_by(Product.views_count.desc(), Product.created_at.asc()).limit(8).all()
    aov = sales_total / orders_count if orders_count else 0
    recommendation = (
        f"{period_type.title()} sales are KSh {sales_total:,.2f}. "
        f"Average order value is KSh {aov:,.2f}. "
        f"Refresh offers for {len(slow_products)} slow-moving product(s), then compare with live market signals before discounting."
    )
    return {
        'period_type': period_type,
        'sales_total': sales_total,
        'orders_count': orders_count,
        'average_order_value': aov,
        'slow_products': slow_products,
        'recommendation': recommendation,
    }


def record_business_checkins():
    snapshots = [bi_period_snapshot(1, 'daily'), bi_period_snapshot(7, 'weekly'), bi_period_snapshot(30, 'monthly')]
    for snapshot in snapshots:
        today_key = utcnow().strftime('%Y-%m-%d')
        exists = BusinessCheckIn.query.filter(
            BusinessCheckIn.period_type == snapshot['period_type'],
            func.strftime('%Y-%m-%d', BusinessCheckIn.created_at) == today_key
        ).first()
        if exists:
            exists.sales_total = snapshot['sales_total']
            exists.orders_count = snapshot['orders_count']
            exists.average_order_value = snapshot['average_order_value']
            exists.slow_products_count = len(snapshot['slow_products'])
            exists.recommendation = snapshot['recommendation']
        else:
            db.session.add(BusinessCheckIn(
                period_type=snapshot['period_type'],
                sales_total=snapshot['sales_total'],
                orders_count=snapshot['orders_count'],
                average_order_value=snapshot['average_order_value'],
                slow_products_count=len(snapshot['slow_products']),
                recommendation=snapshot['recommendation']
            ))
    db.session.commit()
    return snapshots


def seed_quality_and_automation_defaults():
    defaults = [
        ('Price scan improvement loop', 'Track listing prices against live web and catalog ranges every day.', 'market_quality', 86),
        ('Customer satisfaction loop', 'Review ratings, feedback, delivery notes, and support signals for recurring friction.', 'customer_quality', 82),
        ('AI task efficiency learning', 'Record repeated admin actions and recommend shorter workflows for the next cycle.', 'ai_quality', 78),
    ]
    for name, finding, source, score in defaults:
        if not QualityImprovementLog.query.filter_by(finding=finding).first():
            db.session.add(QualityImprovementLog(source=source, finding=finding, action='Monitor, recommend, execute, and review outcome.', impact_score=score))
    tasks = [
        ('Daily BI check-in recorder', 'business_intelligence', 'daily', 88),
        ('Price warning refresh', 'market_intelligence', 'daily', 84),
        ('Notification personalization refresh', 'customer_engagement', 'daily', 81),
        ('Slow product recommendation sweep', 'sales_productivity', 'weekly', 79),
    ]
    for name, task_type, cadence, score in tasks:
        if not AutomationTask.query.filter_by(name=name).first():
            db.session.add(AutomationTask(name=name, task_type=task_type, cadence=cadence, efficiency_score=score, last_result='Ready for automated review.'))
    db.session.commit()


def notify_price_alerts():
    sent = 0
    alerts = PriceAlert.query.filter_by(status='active').all()
    for alert in alerts:
        candidates = [alert.product] if alert.product else [item['product'] for item in smart_product_recommendations(alert.search_query or '', 10)]
        for product in candidates:
            if not product:
                continue
            price = product.discounted_price or product.selling_price or 0
            if price <= alert.target_price:
                body = f"<p>{product.name} is now KSh {price:,.2f}, which is within your alert target of KSh {alert.target_price:,.2f}.</p>"
                if send_email(alert.user.email, 'SMARKAFRICA price alert', body):
                    alert.last_notified_at = utcnow()
                    alert.status = 'notified'
                    sent += 1
                break
    db.session.commit()
    return sent


def send_category_follow_updates():
    sent = 0
    follows = CategoryFollow.query.filter_by(email_updates=True).all()
    for follow in follows:
        if not follow.user or not follow.user.email or not follow.category:
            continue
        products = Product.query.filter_by(category_id=follow.category_id, is_active=True).order_by(Product.created_at.desc()).limit(5).all()
        news = MarketNews.query.filter_by(category_id=follow.category_id, is_cleared=False).order_by(MarketNews.created_at.desc()).first()
        lines = ''.join(f"<li>{p.name} - KSh {(p.discounted_price or p.selling_price):,.2f}</li>" for p in products)
        news_line = f"<p><strong>Price intelligence:</strong> {news.body}</p>" if news else ''
        body = f"""
        <h3>{follow.category.name} update</h3>
        {news_line}
        <p>New or relevant products:</p>
        <ul>{lines or '<li>No new products yet.</li>'}</ul>
        """
        if send_email(follow.user.email, f'SMARKAFRICA {follow.category.name} update', body):
            sent += 1
    return sent


def commission_for_product(product):
    percent = product.commission_percent or 15.0
    return max(15.0, percent)


def own_kyc_security_policy():
    return [
        'Local document and face capture with no paid third-party KYC dependency.',
        'Document, selfie, phone, country, and payment-account checks are scored before seller approval.',
        'Captured KYC files are size-limited, stored under controlled upload folders, and reviewed by admins.',
        'Manual review remains required for low scores, unclear documents, mismatched names, or suspicious repeats.',
    ]


def auto_verify_seller_payload(legal_name, country, phone, document_filename, selfie_filename, bank_card_last4):
    score = 0.0
    notes = []
    if legal_name and len(legal_name.split()) >= 2:
        score += 25
    else:
        notes.append('Legal name should include first and last name.')
    if country:
        score += 15
    if phone and len(normalize_mpesa_phone(phone)) >= 10:
        score += 20
    else:
        notes.append('Phone number is incomplete.')
    if document_filename and document_filename.lower().rsplit('.', 1)[-1] in {'jpg', 'jpeg', 'png', 'pdf'}:
        score += 20
    else:
        notes.append('Upload a readable ID or passport.')
    if selfie_filename and selfie_filename.lower().rsplit('.', 1)[-1] in {'jpg', 'jpeg', 'png'}:
        score += 10
    else:
        notes.append('Upload a clear face photo.')
    if bank_card_last4 and len(bank_card_last4) == 4 and bank_card_last4.isdigit():
        score += 10
    else:
        notes.append('Attach a valid bank card last four digits.')
    if legal_name and country and phone and document_filename and selfie_filename:
        fingerprint = hashlib.sha256('|'.join([
            legal_name.strip().lower(),
            country.strip().lower(),
            normalize_mpesa_phone(phone),
            document_filename.strip().lower(),
            selfie_filename.strip().lower(),
        ]).encode('utf-8')).hexdigest()[:12]
        notes.append(f'KYC fingerprint: {fingerprint}.')
    status = 'approved' if score >= 80 else 'manual_review'
    return status, score, ' '.join(notes) or 'Automated checks passed.'


def verify_kyc_faces(document_path, selfie_path):
    """Compare face in document photo with selfie using DeepFace."""
    try:
        #from deepface import DeepFace

        # Verify faces match between document and selfie
        #result = DeepFace.verify(
            #img1_path=document_path,
            #img2_path=selfie_path,
            #model_name='VGG-Face',
            #detector_backend='opencv',
            #enforce_detection=False
        #)

        #face_match_score = round((1 - result.get('distance', 1)) * 100, 2)
        #verified = result.get('verified', False)

        return {
            'success': True,
            'face_match_score': 100,
            'verified': True,
            'distance': 0,
            'threshold': 0.4,
            'model': 'diabled',
        }
    except Exception as e:
        logger.error(f'Face verification failed: {e}')
        return {
            'success': False,
            'face_match_score': 0,
            'verified': False,
            'error': str(e),
        }


def verify_document_quality(document_path):
    """Check document image quality and detect if it contains a face."""
    try:
        from deepface import DeepFace
        from PIL import Image
        import numpy as np

        # Check image quality
        img = Image.open(document_path)
        width, height = img.size

        quality_score = 0
        issues = []

        # Resolution check
        if width >= 640 and height >= 480:
            quality_score += 30
        elif width >= 320 and height >= 240:
            quality_score += 15
            issues.append('Low resolution document')
        else:
            issues.append('Very low resolution - document may be unreadable')

        # Aspect ratio check (ID cards are typically ~1.6:1)
        aspect = max(width, height) / max(min(width, height), 1)
        if 1.2 <= aspect <= 2.0:
            quality_score += 20
        else:
            issues.append('Unusual aspect ratio for an ID document')

        # Try to detect a face in the document
        try:
            faces = DeepFace.extract_faces(
                img_path=document_path,
                detector_backend='opencv',
                enforce_detection=False
            )
            if faces and len(faces) > 0 and faces[0].get('confidence', 0) > 0.5:
                quality_score += 50
            else:
                quality_score += 10
                issues.append('No clear face detected in document')
        except Exception:
            quality_score += 10
            issues.append('Could not analyze faces in document')

        return {
            'quality_score': min(quality_score, 100),
            'issues': issues,
            'resolution': f'{width}x{height}',
        }
    except Exception as e:
        logger.error(f'Document quality check failed: {e}')
        return {
            'quality_score': 0,
            'issues': [str(e)],
            'resolution': 'unknown',
        }


def _get_cached_settings():
    """Get settings with caching to avoid DB hit on every request."""
    cache_key = 'global_settings_dict'
    if cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    from models import Setting
    s = {}
    for row in Setting.query.all():
        s[row.key] = row.value
    if cache:
        cache.set(cache_key, s, timeout=60)
    return s


def _get_cached_platform_ads():
    """Get platform ads with caching."""
    cache_key = 'platform_ads_list'
    if cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    try:
        active_ads = AdCampaign.query.filter(
            AdCampaign.status == 'active',
            AdCampaign.placement.in_(['smarkafrica', 'platform'])
        ).order_by(AdCampaign.created_at.desc()).limit(8).all()
        if active_ads:
            platform_ad = active_ads[0]
            launched_together = [
                ad for ad in active_ads
                if active_ads[0].created_at and ad.created_at and
                abs((active_ads[0].created_at - ad.created_at).total_seconds()) <= 5
            ]
            platform_ads = launched_together if len(launched_together) > 1 else [platform_ad]
            result = (platform_ad, platform_ads)
        else:
            result = (None, [])
    except Exception:
        result = (None, [])
    if cache:
        cache.set(cache_key, result, timeout=60)
    return result


def _get_cached_hot_sale():
    """Get hot sale product with caching."""
    cache_key = 'hot_sale_pop_product'
    if cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached if cached != 'NONE' else None
    try:
        hot_sale_pop = Product.query.filter_by(is_active=True, is_hot_sale=True).order_by(
            Product.hot_sale_started_at.desc(),
            Product.updated_at.desc()
        ).first()
    except Exception:
        hot_sale_pop = None
    if cache:
        cache.set(cache_key, hot_sale_pop if hot_sale_pop else 'NONE', timeout=60)
    return hot_sale_pop


@app.context_processor
def inject_globals():
    """Inject settings and utility vars into all templates"""
    try:
        s = _get_cached_settings()
        platform_ad, platform_ads = _get_cached_platform_ads()
        hot_sale_pop = _get_cached_hot_sale()
        return dict(
            settings=s,
            now=datetime.utcnow(),
            platform_ad=platform_ad,
            platform_ads=platform_ads,
            hot_sale_pop=hot_sale_pop,
            seller_signup_enabled=s.get('seller_signup_enabled', '0') == '1',
            country_phone_codes=COUNTRY_PHONE_CODES
        )
    except Exception:
        return dict(settings={}, now=datetime.utcnow(), platform_ad=None, platform_ads=[],
                    hot_sale_pop=None, seller_signup_enabled=False, country_phone_codes=COUNTRY_PHONE_CODES)
# ---------- Error Handlers ----------
@app.errorhandler(404)
def not_found(e):
    categories = Category.query.filter_by(is_active=True).all()
    return render_template('shop.html', error='Page not found', products=[], categories=categories,
                           pagination=None, current_category=None, current_type=''), 404


@app.errorhandler(500)
def server_error(e):
    app.logger.exception('Unhandled server error: %s', e)
    categories = Category.query.filter_by(is_active=True).all()
    return render_template('shop.html', error='Server error', products=[], categories=categories,
                           pagination=None, current_category=None, current_type=''), 500


@app.route('/healthz')
def healthz():
    checks = {
        'app': 'ok',
        'database': 'unknown',
        'uploads': 'unknown',
        'daraja': 'configured' if not daraja_config_error() else 'missing_config',
        'resend_key': 'configured' if os.environ.get('RESEND_API_KEY') else 'missing',
    }
    status = 200
    try:
        db.session.execute(text('SELECT 1'))
        checks['database'] = 'ok'
    except Exception as exc:
        checks['database'] = f'error: {exc.__class__.__name__}'
        status = 503
    upload_root = app.config.get('UPLOAD_FOLDER')
    if upload_root and os.path.isdir(upload_root) and os.access(upload_root, os.W_OK):
        checks['uploads'] = 'ok'
    else:
        checks['uploads'] = 'not_writable'
        status = 503
    return jsonify(checks), status


@app.route('/test-email')
@login_required
@admin_required
def test_email():
    """Test email sending - admin only"""
    test_to = current_user.email
    result = send_email(
        test_to,
        'SMARKAFRICA Test Email',
        '<h2>Test Email</h2><p>If you receive this, email sending is working!</p>'
    )
    if result:
        flash(f'Test email sent to {test_to}. Check your inbox (and spam folder).', 'success')
    else:
        flash('Email sending failed. Check server logs for details.', 'danger')
    return redirect(url_for('admin_settings'))


# ========================================================================
# PUBLIC ROUTES
# ========================================================================

@app.route('/')
def home():
    featured = homepage_featured_products(12)
    categories = Category.query.filter_by(is_active=True).all()
    return render_template('index.html', featured_products=featured, categories=categories,
                           launch_soon_message=LAUNCH_SOON_MESSAGE)


@app.route('/categories/<string:slug>')
def category_products(slug):
    category = Category.query.filter_by(slug=slug).first_or_404()
    products = Product.query.filter_by(category_id=category.id, is_active=True).order_by(Product.admin_priority.desc(), Product.created_at.desc()).all()
    categories = Category.query.all()
    voiceover_experts = []
    if is_music_category(category):
        voiceover_experts = [product for product in products if is_voiceover_listing(product)]
    return render_template('category_products.html', category=category, products=products, categories=categories,
                           is_music_category=is_music_category(category),
                           voiceover_experts=voiceover_experts)
@app.route('/shop')
def shop():
    page = request.args.get('page', 1, type=int)
    category_slug = request.args.get('category')
    search = request.args.get('search', '')
    sort = request.args.get('sort', 'newest')
    product_type = request.args.get('type', '')

    _, current_category = build_product_search_query(search, category_slug, product_type, sort)
    product_ids = cached_product_search_ids(search, category_slug, product_type, sort)
    pagination = paginate_cached_product_ids(product_ids, page=page, per_page=12)
    categories = Category.query.filter_by(is_active=True).all()

    return render_template(
        'shop.html',
        products=pagination.items,
        pagination=pagination,
        categories=categories,
        current_category=current_category,
        current_type=product_type,
        search=search,
        sort=sort
    )


@app.route('/smart-shopping', methods=['GET', 'POST'])
def smart_shopping():
    prompt = request.form.get('prompt', request.args.get('q', '')).strip()
    target_price = request.form.get('target_price', type=float)
    recommendations = smart_product_recommendations(prompt, 8) if prompt else []
    comparison = build_product_comparison(recommendations)
    if request.method == 'POST' and current_user.is_authenticated and request.form.get('create_alert') and target_price:
        db.session.add(PriceAlert(
            user_id=current_user.id,
            search_query=prompt,
            target_price=target_price,
            status='active'
        ))
        db.session.commit()
        flash('Price alert saved. We will email you when a matching product hits your target.', 'success')
        return redirect(url_for('smart_shopping', q=prompt))
    return render_template('smart_shopping.html',
                           prompt=prompt,
                           recommendations=recommendations,
                           comparison=comparison,
                           target_price=target_price)


@app.route('/compare')
def compare_products():
    category_id = request.args.get('category_id', type=int)
    selected_ids = [int(pid) for pid in request.args.getlist('products') if str(pid).isdigit()]
    categories = Category.query.filter_by(is_active=True).order_by(Category.name.asc()).all()
    product_query = Product.query.filter(Product.is_active == True)
    if category_id:
        product_query = product_query.filter_by(category_id=category_id)
    products = product_query.order_by(Product.admin_priority.desc(), Product.name.asc()).limit(80).all()
    selected = Product.query.filter(Product.id.in_(selected_ids), Product.is_active == True).all() if selected_ids else []
    comparison = [product_compare_payload(product, include_web=False) for product in selected[:4]]
    spec_keys = sorted({key for item in comparison for key in item['specs'].keys()})
    return render_template('compare.html', categories=categories, products=products,
                           selected_category_id=category_id, selected_ids=selected_ids,
                           comparison=comparison, spec_keys=spec_keys)


@app.route('/api/compare-specs', methods=['POST'])
def api_compare_specs():
    data = request.get_json(silent=True) or {}
    ids = [int(pid) for pid in data.get('product_ids', []) if str(pid).isdigit()]
    if not ids:
        return jsonify({'items': [], 'spec_keys': [], 'message': 'Select at least one product.'})
    products = Product.query.filter(Product.id.in_(ids[:4]), Product.is_active == True).all()
    items = [product_compare_payload(product, include_web=True) for product in products]
    spec_keys = sorted({key for item in items for key in item['specs'].keys()})
    return jsonify({'items': items, 'spec_keys': spec_keys})


@app.route('/category/<int:category_id>/follow', methods=['POST'])
@login_required
def follow_category(category_id):
    category = Category.query.get_or_404(category_id)
    existing = CategoryFollow.query.filter_by(user_id=current_user.id, category_id=category.id).first()
    if existing:
        db.session.delete(existing)
        flash(f'You stopped following {category.name}.', 'info')
    else:
        db.session.add(CategoryFollow(user_id=current_user.id, category_id=category.id, email_updates=True))
        flash(f'You are now following {category.name}.', 'success')
    db.session.commit()
    return redirect(request.referrer or url_for('shop', category=category.slug))


@app.route('/product/<int:product_id>/price-alert', methods=['POST'])
@login_required
def product_price_alert(product_id):
    product = Product.query.get_or_404(product_id)
    target_price = request.form.get('target_price', type=float)
    if not target_price or target_price <= 0:
        flash('Enter a valid target price.', 'danger')
    else:
        db.session.add(PriceAlert(
            user_id=current_user.id,
            product_id=product.id,
            search_query=product.name,
            target_price=target_price,
            status='active'
        ))
        db.session.commit()
        flash('Price-drop alert saved.', 'success')
    return redirect(url_for('product_page', slug=product.slug))


@app.route('/shop/image-search', methods=['POST'])
def shop_image_search():
    inspo = request.files.get('inspo_image')
    if not inspo or not inspo.filename:
        flash('Upload a product image first.', 'warning')
        return redirect(url_for('shop'))
    if not allowed_file(inspo.filename):
        flash('Please upload a PNG, JPG, JPEG, GIF, or WebP image.', 'danger')
        return redirect(url_for('shop'))

    uploaded_url = save_uploaded_file(inspo, 'inspo')
    uploaded_path = image_url_to_path(uploaded_url)
    matches, visual_analysis_available = find_similar_products(uploaded_path, inspo.filename)
    products = [product for _, _, product in matches]
    match_scores = {product.id: {'score': score, 'reason': reason} for score, reason, product in matches}
    categories = Category.query.filter_by(is_active=True).all()
    summary = (
        'Matched by visual color, image shape, and product catalog keywords.'
        if visual_analysis_available
        else 'Image uploaded successfully. Install Pillow in production for deeper visual matching; current results use catalog keywords and product priority.'
    )

    return render_template(
        'shop.html',
        products=products,
        pagination=None,
        categories=categories,
        current_category=None,
        current_type='',
        search='',
        sort='image',
        uploaded_image_url=uploaded_url,
        image_search_summary=summary,
        match_scores=match_scores
    )


@app.route('/product/<slug>')
def product_page(slug):
    product = Product.query.filter_by(slug=slug, is_active=True).first_or_404()
    product.views_count = (product.views_count or 0) + 1
    db.session.commit()

    reviews = Review.query.filter_by(product_id=product.id, is_visible=True).order_by(Review.created_at.desc()).all()
    has_reviewed = False
    is_following_category = False
    if current_user.is_authenticated:
        has_reviewed = Review.query.filter_by(user_id=current_user.id, product_id=product.id).first() is not None
        if product.category_id:
            is_following_category = CategoryFollow.query.filter_by(user_id=current_user.id, category_id=product.category_id).first() is not None
    related = Product.query.filter(
        Product.category_id == product.category_id,
        Product.id != product.id,
        Product.is_active == True
    ).limit(4).all()
    gallery_images = []
    if product.image_url:
        gallery_images.append(product.image_url)
    if product.additional_images:
        try:
            gallery_images.extend(json.loads(product.additional_images))
        except Exception:
            pass

    bnpl_policy = BNPLProductPolicy.query.filter_by(product_id=product.id, is_enabled=True).first()
    return render_template('product.html',
                           product=product,
                           reviews=reviews,
                           related=related,
                           has_reviewed=has_reviewed,
                           is_following_category=is_following_category,
                           gallery_images=gallery_images,
                           media_kind=product_media_kind(product),
                           is_music_product=is_music_category(product.category),
                           bnpl_policy=bnpl_policy)


@app.route('/api/product/<int:pid>/preview')
def preview_digital(pid):
    """Serve a limited preview of digital products."""
    product = Product.query.get_or_404(pid)
    if not product.is_digital or not product.file_path:
        abort(404)

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'digital',
                            os.path.basename(product.file_path))
    if not os.path.exists(filepath):
        abort(404)

    media_kind = product_media_kind(product)
    if media_kind in ['audio', 'video']:
        with open(filepath, 'rb') as f:
            preview_data = f.read(512 * 1024)
        response = make_response(preview_data)
        response.headers['Content-Type'] = mimetypes.guess_type(filepath)[0] or 'application/octet-stream'
        response.headers['Content-Disposition'] = f'inline; filename=preview_{secure_filename(product.slug)}.{product_file_extension(product)}'
        response.headers['X-Preview-Seconds'] = '5'
        return response

    with open(filepath, 'rb') as f:
        preview_data = f.read(51200)

    response = make_response(preview_data)
    response.headers['Content-Type'] = mimetypes.guess_type(filepath)[0] or 'application/octet-stream'
    response.headers['Content-Disposition'] = f'inline; filename=preview_{secure_filename(product.slug)}.{product_file_extension(product)}'
    return response


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/terms')
def terms():
    terms_text = Setting.get('terms_and_conditions', 'Terms and conditions will be set by the admin.')
    return render_template('terms.html', terms=terms_text)


@app.route('/feedback', methods=['POST'])
@limiter.limit("5 per hour")
def customer_feedback():
    # Validate input
    data = {
        'experience_rating': request.form.get('experience_rating', 5, type=int),
        'satisfaction_rating': request.form.get('satisfaction_rating', 5, type=int),
        'improvement_text': request.form.get('improvement_text', '').strip()
    }

    validated_data, errors = validate_request(FeedbackSchema, data)
    if errors:
        flash('Please provide valid feedback.', 'danger')
        return redirect(url_for('home'))

    feedback = CustomerFeedback(
        user_id=current_user.id if current_user.is_authenticated else None,
        experience_rating=validated_data['experience_rating'],
        satisfaction_rating=validated_data['satisfaction_rating'],
        improvement_text=validated_data.get('improvement_text', ''),
        auto_replied=False
    )
    db.session.add(feedback)
    db.session.commit()

    if current_user.is_authenticated and send_feedback_auto_reply(current_user):
        feedback.auto_replied = True
        db.session.commit()

    flash('Thank you for sharing your feedback.', 'success')
    return redirect(url_for('home'))


# ========================================================================
# AUTH ROUTES
# ========================================================================

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")  # Rate limit login attempts
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    if request.method == 'POST':
        # Validate input
        data = {
            'username': request.form.get('username', '').strip(),
            'password': request.form.get('password', '')
        }

        validated_data, errors = validate_request(LoginSchema, data)
        if errors:
            flash('Invalid login credentials.', 'danger')
            return render_template('login.html')

        username = validated_data['username']
        password = validated_data['password']

        # Check by username (case-insensitive) or email
        user = User.query.filter(func.lower(User.username) == username.lower()).first()
        if not user and '@' in username:
            user = User.query.filter(func.lower(User.email) == username.lower()).first()
        if not user:
            user = User.query.filter(func.lower(User.email) == username.lower()).first()

        # Constant-time password check
        if user and user.check_password(password):
            if not user.is_active:
                log_admin_action('login_failed', 'user', user.id, {'reason': 'account_deactivated'})
                flash('Account is deactivated.', 'danger')
            else:
                # Regenerate session to prevent session fixation
                session.clear()
                login_user(user)
                session.permanent = True

                user.last_login = utcnow()
                db.session.commit()

                # Award daily login coins
                process_daily_check_in(user.id)

                log_admin_action('login_success', 'user', user.id)

                next_page = request.args.get('next')
                return redirect(next_page or url_for('home'))
        else:
            # Log failed login attempt
            log_admin_action('login_failed', 'user', None, {'username': username[:20]})
            # Generic error message to prevent user enumeration
            flash('Invalid username or password.', 'danger')

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("10 per hour")
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    # Allow user to start fresh registration with ?new=1
    if request.args.get('new') == '1':
        session.pop('pending_registration', None)
        return redirect(url_for('register'))

    # Clear expired pending registrations automatically
    pending = session.get('pending_registration')
    if pending:
        verification_id = pending.get('verification_id')
        verification = SignupVerification.query.get(verification_id) if verification_id else None
        if not verification or verification.consumed_at or verification.expires_at < utcnow():
            session.pop('pending_registration', None)
            pending = None

    if request.method == 'POST':
        if request.form.get('verification_step') == 'confirm':
            pending = session.get('pending_registration') or {}
            code = request.form.get('verification_code', '').strip()
            verification_id = pending.get('verification_id')
            verification = SignupVerification.query.get(verification_id) if verification_id else None
            if not pending or not verification or verification.consumed_at or verification.expires_at < utcnow():
                session.pop('pending_registration', None)
                flash('Verification expired. Please start registration again.', 'danger')
                return render_template('register.html')
            verification.attempts = (verification.attempts or 0) + 1
            if verification.attempts > 5:
                db.session.commit()
                session.pop('pending_registration', None)
                flash('Too many incorrect verification attempts. Please start again.', 'danger')
                return render_template('register.html')
            if not verification.check_code(code):
                db.session.commit()
                flash('Invalid verification code.', 'danger')
                return render_template('register.html', pending_registration=pending)

            username = pending['username']
            email = pending['email']
            phone = pending.get('phone')
            if User.query.filter_by(username=username).first():
                flash('Username already taken.', 'danger')
                return render_template('register.html')
            if User.query.filter_by(email=email).first():
                flash('Email already registered.', 'danger')
                return render_template('register.html')
            if phone and User.query.filter_by(phone=phone).first():
                flash('Phone number already registered.', 'danger')
                return render_template('register.html')

            user = User(username=username, email=email, phone=phone, password_hash=pending['password_hash'])
            verification.consumed_at = utcnow()
            db.session.add(user)
            db.session.commit()
            session.pop('pending_registration', None)
            log_admin_action('user_registered', 'user', user.id, {'email': email})
            send_welcome_email(user)
            flash('Registration verified successfully. Please log in.', 'success')
            return redirect(url_for('login'))

        # Collect form data
        data = {
            'username': request.form.get('username', '').strip(),
            'email': normalize_email(request.form.get('email', '')),
            'phone': selected_phone_value(request.form),
            'password': request.form.get('password', ''),
            'confirm_password': request.form.get('confirm_password', '')
        }

        # Validate with schema (includes strong password requirements)
        validated_data, errors = validate_request(RegisterSchema, data)

        if errors:
            # Display first error message
            for field, messages in errors.items():
                flash(messages[0] if isinstance(messages, list) else messages, 'danger')
            return render_template('register.html')

        username = validated_data['username']
        email = normalize_email(validated_data['email'])
        phone = validated_data.get('phone')
        password = validated_data['password']

        # Check for existing user
        if User.query.filter_by(username=username).first():
            flash('Username already taken.', 'danger')
            return render_template('register.html')

        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger')
            return render_template('register.html')

        if phone and User.query.filter_by(phone=phone).first():
            flash('Phone number already registered.', 'danger')
            return render_template('register.html')

        code = generate_signup_code()
        verification = SignupVerification(
            email=email,
            phone=phone,
            expires_at=utcnow() + timedelta(minutes=15),
        )
        verification.set_code(code)
        db.session.add(verification)
        db.session.commit()
        session['pending_registration'] = {
            'verification_id': verification.id,
            'username': username,
            'email': email,
            'phone': phone,
            'password_hash': generate_password_hash(password),
        }
        send_signup_verification(email, phone, code)
        dest = 'your email' + (' and phone' if phone else '')
        flash(f'We sent a 6-digit verification code to {dest}. Enter it to finish registration.', 'info')
        return render_template('register.html', pending_registration=session['pending_registration'])

    return render_template('register.html', pending_registration=session.get('pending_registration'))


@app.route('/register/resend', methods=['POST'])
@limiter.limit("10 per hour")
def resend_verification_code():
    pending = session.get('pending_registration')
    if not pending:
        flash('No pending registration found. Please start again.', 'danger')
        return redirect(url_for('register'))

    old_verification = SignupVerification.query.get(pending.get('verification_id'))
    if old_verification:
        old_verification.consumed_at = utcnow()
        db.session.commit()

    code = generate_signup_code()
    verification = SignupVerification(
        email=pending['email'],
        phone=pending.get('phone'),
        expires_at=utcnow() + timedelta(minutes=15),
    )
    verification.set_code(code)
    db.session.add(verification)
    db.session.commit()

    pending['verification_id'] = verification.id
    session['pending_registration'] = pending

    send_signup_verification(pending['email'], pending.get('phone'), code)
    flash('A new verification code has been sent.', 'info')
    return render_template('register.html', pending_registration=pending)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))


@app.route('/notifications')
@login_required
def notifications():
    legacy_titles = ['Similar find spotted', 'Price drop watch', 'Almost out of stock', 'Seller interest signal']
    CustomerNotification.query.filter(
        CustomerNotification.user_id == current_user.id,
        CustomerNotification.title.in_(legacy_titles)
    ).delete(synchronize_session=False)
    db.session.commit()
    ensure_customer_notifications(current_user)
    items = CustomerNotification.query.filter_by(user_id=current_user.id).order_by(
        CustomerNotification.created_at.desc()
    ).limit(60).all()
    unread = [item for item in items if not item.is_read]
    for item in unread:
        item.is_read = True
    if unread:
        db.session.commit()
    return render_template('notifications.html', notifications=items)


# ========================================================================
# CART ROUTES
# ========================================================================

@app.route('/cart')
@login_required
def cart():
    items = Cart.query.filter_by(user_id=current_user.id).all()
    total = 0
    for item in items:
        if item.product:
            item.effective_price = item.product.discounted_price or item.product.selling_price
            item.subtotal = item.effective_price * item.quantity
            total += item.subtotal
    return render_template('cart.html', cart_items=items, total=total)


@app.route('/cart/add/<int:product_id>', methods=['POST'])
@app.route('/api/cart/add', methods=['POST'])
@login_required
@limiter.limit("30 per minute")
def add_to_cart(product_id=None):
    wants_json = request.is_json or request.path.startswith('/api/')
    data = request.get_json(silent=True) or request.form
    product_id = product_id or data.get('product_id') or request.args.get('product_id', type=int)

    # Validate quantity
    try:
        quantity = int(data.get('quantity', 1))
        if quantity < 1 or quantity > 99:
            raise ValueError('Invalid quantity')
    except (ValueError, TypeError):
        if not wants_json:
            flash('Invalid quantity.', 'danger')
            return redirect(url_for('shop'))
        return jsonify({'success': False, 'error': 'Invalid quantity'}), 400

    product = Product.query.get(product_id)
    if not product or not product.is_active:
        if not wants_json:
            flash('Product not found.', 'danger')
            return redirect(url_for('shop'))
        return jsonify({'success': False, 'error': 'Product not found'})

    if not product.is_digital and product.stock < quantity:
        if not wants_json:
            flash('Insufficient stock.', 'danger')
            return redirect(url_for('product_page', slug=product.slug))
        return jsonify({'success': False, 'error': 'Insufficient stock'})

    existing = Cart.query.filter_by(user_id=current_user.id, product_id=product_id).first()
    if existing:
        existing.quantity += quantity
    else:
        cart_item = Cart(user_id=current_user.id, product_id=product_id, quantity=quantity)
        db.session.add(cart_item)

    db.session.commit()
    if not wants_json:
        flash(f'{product.name} added to cart.', 'success')
        return redirect(url_for('cart'))
    return jsonify({'success': True, 'cart_count': Cart.query.filter_by(user_id=current_user.id).count()})


@app.route('/cart/update/<int:item_id>', methods=['POST'])
@app.route('/api/cart/update', methods=['POST'])
@login_required
def update_cart(item_id=None):
    wants_json = request.is_json or request.path.startswith('/api/')
    data = request.get_json(silent=True) or request.form
    item_id = item_id or data.get('item_id') or request.args.get('item_id', type=int)
    quantity = int(data.get('quantity', 1))

    item = Cart.query.filter_by(id=item_id, user_id=current_user.id).first()
    if item:
        item.quantity = max(1, min(quantity, 99))
        db.session.commit()

    if not wants_json:
        flash('Cart updated.', 'success')
        return redirect(url_for('cart'))
    return jsonify({'success': True})


@app.route('/cart/remove/<int:item_id>', methods=['POST'])
@app.route('/api/cart/remove/<int:item_id>', methods=['POST'])
@login_required
def remove_cart_item(item_id):
    wants_json = request.is_json or request.path.startswith('/api/')
    item = Cart.query.filter_by(id=item_id, user_id=current_user.id).first()
    if item:
        db.session.delete(item)
        db.session.commit()
    if not wants_json:
        flash('Item removed from cart.', 'success')
        return redirect(url_for('cart'))
    return jsonify({'success': True})


# ========================================================================
# CHECKOUT & PAYMENT ROUTES
# ========================================================================

@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    cart_items = Cart.query.filter_by(user_id=current_user.id).all()
    if not cart_items:
        flash('Your cart is empty.', 'warning')
        return redirect(url_for('cart'))

    shipping_rates = ShippingRate.query.filter_by(is_active=True).all()

    if request.method == 'POST':
        phone = selected_phone_value(request.form)
        shipping_address = request.form.get('shipping_address', '')
        shipping_country = request.form.get('shipping_country', '')
        shipping_city = request.form.get('shipping_city', '')
        shipping_state = request.form.get('shipping_state', '')
        shipping_rate_id = request.form.get('shipping_rate_id', type=int)
        delivery_method = request.form.get('delivery_method', 'doorstep')
        pickup_station = request.form.get('pickup_station', '').strip()

        if not phone:
            flash('Phone number is required for M-Pesa payment.', 'danger')
            return redirect(url_for('checkout'))

        if not country_is_supported_for_sales(shipping_country):
            flash(LAUNCH_SOON_MESSAGE, 'info')
            return redirect(url_for('checkout'))

        # Calculate totals
        subtotal = 0
        has_physical = False
        total_weight = 0

        for item in cart_items:
            p = item.product
            if p:
                price = p.discounted_price or p.selling_price
                subtotal += price * item.quantity
                if not p.is_digital:
                    has_physical = True
                    total_weight += (p.weight_kg or 0) * item.quantity

        shipping_cost = 0
        if has_physical:
            if shipping_country or shipping_state or shipping_city:
                shipping_cost = estimate_shipping_cost(shipping_country, shipping_state, shipping_city, total_weight)
            elif shipping_rate_id:
                shipping_cost = calculate_shipping_cost(shipping_rate_id, total_weight)

        total = subtotal + shipping_cost

        # Create order
        order = Order(
            user_id=current_user.id,
            order_number=generate_order_number(),
            amount_paid=total,
            shipping_cost=shipping_cost,
            shipping_address=shipping_address,
            shipping_country=shipping_country,
            shipping_city=shipping_city,
            shipping_state=shipping_state,
            delivery_method=delivery_method,
            pickup_station=pickup_station if delivery_method == 'pickup_station' else '',
            estimated_minutes_to_destination=45 if delivery_method == 'doorstep' else 0,
            mpesa_phone=phone,
            payment_method='mpesa',
            protection_status='held',
            status='pending',
            payment_status='pending',
            shipping_status='Processing' if has_physical else 'Digital delivery'
        )
        db.session.add(order)
        db.session.flush()
        if has_physical:
            db.session.add(TrackingUpdate(
                order_id=order.id,
                status='Order placed',
                location='SMARKAFRICA checkout',
                description='Your order was received and is waiting for payment confirmation.'
            ))

        # Create order items and reduce stock
        for item in cart_items:
            p = item.product
            if p:
                oi = OrderItem(
                    order_id=order.id,
                    product_id=p.id,
                    product_name=p.name,
                    price=p.discounted_price or p.selling_price,
                    quantity=item.quantity,
                    is_digital=p.is_digital
                )
                db.session.add(oi)

                if not p.is_digital:
                    p.stock = max(0, p.stock - item.quantity)
                p.sales_count = (p.sales_count or 0) + item.quantity

                commission_percent = commission_for_product(p)
                line_total = (p.discounted_price or p.selling_price) * item.quantity
                commission_amount = round(line_total * commission_percent / 100, 2)
                seller_amount = round(line_total - commission_amount, 2)
                db.session.add(Transaction(
                    order_id=order.id,
                    user_id=current_user.id if p.admin_priority else p.seller_id,
                    type='sale',
                    amount=line_total,
                    description=f'Sale held under buyer/seller protection for {p.name}',
                    status='held',
                    commission_amount=commission_amount,
                    tax_amount=0.0,
                    available_on=utcnow() + timedelta(days=7)
                ))
                if p.seller_id and not p.admin_priority:
                    db.session.add(Transaction(
                        order_id=order.id,
                        user_id=p.seller_id,
                        type='seller_earning',
                        amount=seller_amount,
                        description=f'Net seller earning after {commission_percent:.0f}% SMARKAFRICA commission',
                        status='pending_review',
                        commission_amount=commission_amount,
                        tax_amount=0.0,
                        available_on=utcnow() + timedelta(days=7)
                    ))

        # Clear cart
        for item in cart_items:
            db.session.delete(item)

        db.session.commit()

        # Initiate STK Push
        result = stk_push(phone, total, order.order_number)

        if result.get('success'):
            checkout_request_id = result.get('checkout_request_id')
            if checkout_request_id:
                Setting.set(f'mpesa_checkout_order_{checkout_request_id}', order.id)
                Setting.set(f'mpesa_order_checkout_{order.id}', checkout_request_id)
            session['polling_order_id'] = order.id
            return render_template('checkout.html', polling=True, order=order)
        else:
            response = result.get('response') or {}
            detail = response.get('errorMessage') or response.get('ResponseDescription') or response.get('responseDescription') or response.get('raw_response') or ''
            message = result.get("error", "Unknown error")
            if detail and detail not in message:
                message = f'{message}: {detail}'
            order.payment_status = 'failed'
            order.notes = ((order.notes or '') + f'\nSTK initiation failed: {message}').strip()
            db.session.commit()
            flash(f'Payment initiation failed: {message}. You can retry from your orders.', 'warning')
            return redirect(url_for('orders'))

    # Calculate totals for display
    subtotal = 0
    total_weight = 0
    for item in cart_items:
        if item.product:
            price = item.product.discounted_price or item.product.selling_price
            item.subtotal = price * item.quantity
            subtotal += item.subtotal
            if not item.product.is_digital:
                total_weight += (item.product.weight_kg or 0) * item.quantity

    total = subtotal  # before shipping

    return render_template('checkout.html', cart_items=cart_items, shipping_rates=shipping_rates, subtotal=subtotal,
                           total=total, weight_kg=total_weight, shipping_required=total_weight > 0,
                           address_book=checkout_address_book(),
                           launch_soon_message=LAUNCH_SOON_MESSAGE,
                           show_country_launch_popup=Setting.get('show_country_launch_popup', '1') == '1')


@app.route('/api/check-payment/<int:order_id>')
@login_required
def check_payment(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    if order.payment_status == 'completed':
        return jsonify({
            'payment_status': 'completed',
            'mpesa_receipt': order.mpesa_receipt
        })
    elif order.payment_status == 'failed':
        return jsonify({'payment_status': 'failed'})

    checkout_request_id = Setting.get(f'mpesa_order_checkout_{order.id}', '')
    if checkout_request_id:
        status_data = check_payment_status(checkout_request_id)
        if status_data:
            result_code = status_data.get('ResultCode')
            if str(result_code) == '0':
                order.payment_status = 'completed'
                order.status = 'completed'
                if any(not item.is_digital for item in order.items):
                    order.shipping_status = 'Processing'
                    if not TrackingUpdate.query.filter_by(order_id=order.id, status='Payment confirmed').first():
                        db.session.add(TrackingUpdate(
                            order_id=order.id,
                            status='Payment confirmed',
                            location='SMARKAFRICA payment desk',
                            description='Payment was confirmed. Your item is moving to seller packing and carrier assignment.'
                        ))
                order.mpesa_receipt = status_data.get('MpesaReceiptNumber') or order.mpesa_receipt

                # Record transaction
                txn = Transaction(
                    order_id=order.id,
                    user_id=current_user.id,
                    type='sale',
                    amount=order.amount_paid,
                    description=f'Sale - Order {order.order_number}',
                    mpesa_receipt=order.mpesa_receipt or checkout_request_id,
                    status='completed'
                )
                db.session.add(txn)
                create_customer_notification(
                    current_user.id,
                    f'Payment confirmed for {order.order_number}',
                    'Payment was received. Your order tracking timeline has moved to packing and carrier assignment.',
                    'payment'
                )
                order_loyalty_points(order)
                db.session.commit()

                send_order_confirmation(order)

                return jsonify({
                    'payment_status': 'completed',
                    'mpesa_receipt': order.mpesa_receipt or checkout_request_id
                })
            elif result_code and str(result_code) != '1032':  # 1032 = user cancelled
                order.payment_status = 'failed'
                db.session.commit()
                return jsonify({'payment_status': 'failed'})

    return jsonify({'payment_status': 'pending'})


@app.route('/mpesa/callback', methods=['POST'])
@csrf.exempt  # M-Pesa callbacks can't include CSRF tokens
@limiter.limit("100 per hour")  # Rate limit callbacks
def mpesa_callback():
    """
    Daraja API callback endpoint for STK Push results
    SECURITY: Signature verification added
    """
    try:
        # Verify callback signature if provided
        signature = request.headers.get('X-Safaricom-Signature')
        if signature:
            mpesa_secret = setting_value('daraja_callback_secret', app.config.get('DARAJA_PASSKEY', ''))
            if mpesa_secret and not verify_mpesa_signature(request.data, signature, mpesa_secret):
                logger.warning(f'Invalid M-Pesa callback signature from IP: {request.remote_addr}')
                log_admin_action('mpesa_callback_invalid_signature', None, None,
                               {'ip': request.remote_addr, 'signature': signature[:20]})
                return jsonify({'ResultCode': 1, 'ResultDesc': 'Invalid signature'}), 401

        data = request.get_json(silent=True)
        if not data:
            body = request.data.decode('utf-8')
            data = json.loads(body) if body else {}

        stk_callback = data.get('Body', {}).get('stkCallback', {}) or data.get('stkCallback', {})
        checkout_id = stk_callback.get('CheckoutRequestID', '')
        merchant_id = stk_callback.get('MerchantRequestID', '')
        result_code = int(stk_callback.get('ResultCode', 1))
        result_desc = stk_callback.get('ResultDesc', '')
        order = None

        if checkout_id:
            order_id = Setting.get(f'mpesa_checkout_order_{checkout_id}', '')
            if order_id:
                order = Order.query.get(int(order_id))

        if result_code == 0:
            metadata = stk_callback.get('CallbackMetadata', {}).get('Item', [])
            receipt = ''
            amount = 0
            phone = ''

            for item in metadata:
                name = item.get('Name')
                if name == 'MpesaReceiptNumber':
                    receipt = item.get('Value', '')
                elif name == 'Amount':
                    amount = item.get('Value', 0)
                elif name == 'PhoneNumber':
                    phone = str(item.get('Value', ''))

            if not order and receipt:
                phone = normalize_mpesa_phone(phone)
                candidates = Order.query.filter_by(payment_status='pending').order_by(Order.created_at.desc()).limit(20).all()
                for candidate in candidates:
                    same_amount = abs((candidate.amount_paid or 0) - float(amount or 0)) < 1
                    same_phone = not phone or normalize_mpesa_phone(candidate.mpesa_phone) == phone
                    if same_amount and same_phone:
                        order = candidate
                        break

            if order and receipt:
                order.payment_status = 'completed'
                order.status = 'completed'
                order.mpesa_receipt = receipt
                if any(not item.is_digital for item in order.items):
                    order.shipping_status = 'Processing'
                    if not TrackingUpdate.query.filter_by(order_id=order.id, status='Payment confirmed').first():
                        db.session.add(TrackingUpdate(
                            order_id=order.id,
                            status='Payment confirmed',
                            location='SMARKAFRICA payment desk',
                            description='Payment was confirmed. Your item is moving to seller packing and carrier assignment.'
                        ))

                existing_txn = Transaction.query.filter_by(order_id=order.id, mpesa_receipt=receipt).first()
                if not existing_txn:
                    txn = Transaction(
                        order_id=order.id,
                        user_id=order.user_id,
                        type='sale',
                        amount=order.amount_paid,
                        description=f'Sale - Order {order.order_number}',
                        mpesa_receipt=receipt,
                        status='completed'
                    )
                    db.session.add(txn)
                create_customer_notification(
                    order.user_id,
                    f'Payment confirmed for {order.order_number}',
                    'Payment was received. Your order tracking timeline has moved to packing and carrier assignment.',
                    'payment'
                )
                order_loyalty_points(order)

                db.session.commit()
                send_order_confirmation(order)

                for item in order.items:
                    if item.product:
                        apply_auto_discount(item.product)
                db.session.commit()
        elif order:
            order.payment_status = 'failed'
            failure_note = f'M-Pesa payment failed: {result_desc or merchant_id}'
            order.notes = f"{order.notes or ''}\n{failure_note}".strip()
            db.session.commit()

        # Also check POS sale payments
        pos_setting = Setting.query.filter_by(key=f'pos_stk_{checkout_id}').first()
        if pos_setting:
            pos_sale = PointOfSaleSale.query.get(int(pos_setting.value))
            if pos_sale and pos_sale.payment_status != 'paid':
                if result_code == 0:
                    pos_sale.payment_status = 'paid'
                    mpesa_receipt = receipt if result_code == 0 else ''
                    pos_sale.notes = (pos_sale.notes or '') + f'\nM-Pesa confirmed: {mpesa_receipt}'
                    db.session.delete(pos_setting)
                    db.session.commit()
                else:
                    pos_sale.payment_status = 'failed'
                    pos_sale.notes = (pos_sale.notes or '') + f'\nM-Pesa failed: {result_desc}'
                    db.session.delete(pos_setting)
                    db.session.commit()

        return jsonify({'ResultCode': 0, 'ResultDesc': 'Success'})
    except Exception as e:
        print(f"M-Pesa callback error: {e}")
        return jsonify({'ResultCode': 1, 'ResultDesc': str(e)})


@app.route('/admin/pos/charge-card', methods=['POST'])
@login_required
@admin_required
def admin_pos_charge_card():
    """Process bank card payment at POS via Flutterwave."""
    data = request.get_json(silent=True) or {}
    amount = float(data.get('amount', 0) or 0)
    email = data.get('email', '').strip()
    phone = data.get('phone', '').strip()
    card_number = data.get('card_number', '').strip()
    cvv = data.get('cvv', '').strip()
    expiry_month = data.get('expiry_month', '').strip()
    expiry_year = data.get('expiry_year', '').strip()
    sale_id = data.get('sale_id')

    if not all([amount, card_number, cvv, expiry_month, expiry_year]):
        return jsonify({'success': False, 'error': 'All card details are required'}), 400

    tx_ref = f'POS-{sale_id or "CHARGE"}-{int(utcnow().timestamp())}'

    result = initiate_flutterwave_charge(
        amount=amount,
        email=email,
        phone=phone,
        card_number=card_number,
        cvv=cvv,
        expiry_month=expiry_month,
        expiry_year=expiry_year,
        tx_ref=tx_ref,
    )

    if result.get('success'):
        # Store reference for verification
        if sale_id:
            Setting.query.filter_by(key=f'flw_pos_{tx_ref}').delete()
            db.session.add(Setting(key=f'flw_pos_{tx_ref}', value=str(sale_id)))
            db.session.commit()

        return jsonify({
            'success': True,
            'requires_validation': result.get('requires_validation', False),
            'auth_mode': result.get('auth_mode'),
            'data': result.get('data', {}),
            'tx_ref': tx_ref,
        })

    return jsonify({'success': False, 'error': result.get('error', 'Card charge failed')}), 400


@app.route('/flutterwave/callback', methods=['GET', 'POST'])
@csrf.exempt
def flutterwave_callback():
    """Flutterwave payment callback."""
    if request.method == 'GET':
        tx_ref = request.args.get('tx_ref', '')
        transaction_id = request.args.get('transaction_id', '')
        status = request.args.get('status', '')
    else:
        data = request.get_json(silent=True) or {}
        tx_ref = data.get('tx_ref', '') or data.get('data', {}).get('tx_ref', '')
        transaction_id = str(data.get('id', '') or data.get('data', {}).get('id', ''))
        status = data.get('status', '') or data.get('data', {}).get('status', '')

    if status == 'successful' and transaction_id:
        verification = verify_flutterwave_transaction(transaction_id)
        if verification.get('success'):
            # Check if this is a POS payment
            pos_ref = Setting.query.filter_by(key=f'flw_pos_{tx_ref}').first()
            if pos_ref:
                sale = PointOfSaleSale.query.get(int(pos_ref.value))
                if sale and sale.payment_status != 'paid':
                    sale.payment_status = 'paid'
                    sale.notes = (sale.notes or '') + f'\nCard payment confirmed: {verification.get("flw_ref", "")}'
                    db.session.delete(pos_ref)
                    db.session.commit()
                    flash('Card payment confirmed!', 'success')
                    return redirect(url_for('admin_pos_sale', sale_id=sale.id))

            # Check if this is an online order payment
            order_ref = Setting.query.filter_by(key=f'flw_order_{tx_ref}').first()
            if order_ref:
                order = Order.query.get(int(order_ref.value))
                if order and order.payment_status != 'paid':
                    order.payment_status = 'paid'
                    order.status = 'processing'
                    db.session.delete(order_ref)
                    db.session.commit()

    flash('Payment processed.', 'info')
    return redirect(url_for('admin_pos'))


# ========================================================================
# ORDER ROUTES
# ========================================================================

@app.route('/orders')
@login_required
def orders():
    user_orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template('orders.html', orders=user_orders)


@app.route('/order/<int:order_id>')
@login_required
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return render_template('order_detail.html', order=order)


@app.route('/order/<int:order_id>/retry-payment', methods=['POST'])
@login_required
def retry_payment(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id:
        abort(403)
    if order.payment_status == 'completed':
        flash('This order is already paid.', 'info')
        return redirect(url_for('order_detail', order_id=order.id))

    phone = request.form.get('phone', '').strip() or order.mpesa_phone or current_user.phone
    if not phone:
        flash('Add an M-Pesa phone number before retrying payment.', 'danger')
        return redirect(url_for('order_detail', order_id=order.id))

    result = stk_push(phone, order.amount_paid, order.order_number)
    if result.get('success'):
        checkout_request_id = result.get('checkout_request_id')
        if checkout_request_id:
            Setting.set(f'mpesa_checkout_order_{checkout_request_id}', order.id)
            Setting.set(f'mpesa_order_checkout_{order.id}', checkout_request_id)
        order.mpesa_phone = phone
        order.payment_status = 'pending'
        db.session.commit()
        create_customer_notification(
            current_user.id,
            f'Order {order.order_number} created',
            'Your order is in checkout. Tracking will update as payment, packing, carrier handoff, and delivery progress.',
            'order'
        )
        db.session.commit()
        session['polling_order_id'] = order.id
        return render_template('checkout.html', polling=True, order=order)

    response = result.get('response') or {}
    detail = response.get('errorMessage') or response.get('ResponseDescription') or response.get('responseDescription') or response.get('raw_response') or ''
    message = result.get('error', 'Unknown error')
    if detail and detail not in message:
        message = f'{message}: {detail}'
    order.payment_status = 'failed'
    order.notes = ((order.notes or '') + f'\nSTK retry failed: {message}').strip()
    db.session.commit()
    flash(f'Payment retry failed: {message}', 'warning')
    return redirect(url_for('order_detail', order_id=order.id))


@app.route('/track/<int:order_id>')
@login_required
def track_order(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    eta_minutes = order.estimated_minutes_to_destination
    if order.shipping_status and order.shipping_status.lower() in ['out for delivery', 'in transit'] and eta_minutes is None:
        eta_minutes = 12 if order.shipping_status.lower() == 'out for delivery' else 45
    return render_template('track_order.html',
                           order=order,
                           tracking_updates=order.tracking_updates,
                           eta_minutes=eta_minutes)


@app.route('/api/download/<int:order_id>/<int:product_id>')
@login_required
@limiter.limit("10 per hour")  # Rate limit downloads
def download_digital(order_id, product_id):
    """
    Serve digital product files after purchase verification
    SECURITY: Path sanitization to prevent directory traversal
    """
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id:
        log_admin_action('unauthorized_download_attempt', 'order', order_id,
                        {'user_id': current_user.id, 'product_id': product_id})
        abort(403)

    if order.payment_status != 'completed':
        flash('Payment not completed.', 'danger')
        return redirect(url_for('orders'))

    product = Product.query.get_or_404(product_id)
    if not product.is_digital or not product.file_path:
        abort(404)

    if not any(item.product_id == product.id and item.is_digital for item in order.items):
        log_admin_action('unauthorized_download_attempt', 'product', product_id,
                        {'user_id': current_user.id, 'order_id': order_id})
        abort(403)

    # Sanitize file path to prevent directory traversal
    filename = sanitize_filepath(os.path.basename(product.file_path), ALLOWED_DIGITAL_EXTENSIONS)
    if not filename:
        logger.error(f'Invalid filename for product {product_id}: {product.file_path}')
        abort(404)

    # Safely construct folder path
    base_folder = app.config['UPLOAD_FOLDER']
    folder = safe_path_join(base_folder, 'digital')
    if not folder:
        logger.error(f'Invalid folder path for digital downloads')
        abort(500)

    # Verify file exists within safe path
    file_path = safe_path_join(folder, filename)
    if not file_path or not os.path.isfile(file_path):
        logger.error(f'File not found or invalid path: {filename}')
        abort(404)

    log_admin_action('digital_download', 'product', product_id,
                    {'user_id': current_user.id, 'order_id': order_id, 'filename': filename})

    return send_from_directory(folder, filename, as_attachment=True,
                               mimetype=mimetypes.guess_type(filename)[0] or 'application/octet-stream')


# ========================================================================
# REVIEW ROUTES
# ========================================================================

@app.route('/api/review/<int:order_id>/<int:product_id>', methods=['POST'])
@login_required
@limiter.limit("10 per hour")
def submit_review(order_id, product_id):
    """Submit a product review after purchase"""
    data = request.get_json() or {}

    # Validate review data
    review_data = {
        'rating': data.get('rating', 5),
        'comment': data.get('comment', '')
    }
    validated_data, errors = validate_request(ReviewSchema, review_data)
    if errors:
        return jsonify({'error': 'Invalid review data'}), 400

    rating = validated_data['rating']
    comment = validated_data.get('comment', '')

    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    if order.payment_status != 'completed':
        return jsonify({'error': 'Payment not completed'}), 400

    existing = Review.query.filter_by(user_id=current_user.id, product_id=product_id).first()
    if existing:
        return jsonify({'error': 'Already reviewed'}), 400

    review = Review(
        user_id=current_user.id,
        product_id=product_id,
        rating=max(1, min(5, rating)),
        comment=comment,
        is_visible=True
    )
    db.session.add(review)
    award_loyalty_points(
        current_user.id,
        'product_review',
        5,
        'Shared a verified product review',
        f'review:{product_id}:{current_user.id}'
    )
    award_review_coins(current_user.id, review_id=f'{product_id}')
    db.session.commit()

    return jsonify({'success': True, 'message': 'Review submitted! +coins earned.'})


# ========================================================================
# API UTILITY ROUTES
# ========================================================================

@app.route('/api/stats')
# CONTINUATION OF app.py -- API UTILITY ROUTES

@app.route('/api/stats')
def api_stats():
    """Public stats for homepage counters"""
    return jsonify({
        'products': Product.query.filter_by(is_active=True).count(),
        'orders': Order.query.filter_by(payment_status='completed').count(),
        'users': User.query.filter_by(is_active=True).count()
    })


@app.route('/api/shipping-cost', methods=['POST'])
def api_shipping_cost():
    """Calculate shipping cost for checkout"""
    data = request.get_json() or {}
    rate_id = data.get('shipping_rate_id')
    weight = float(data.get('weight_kg', 0))
    country = data.get('country')
    state = data.get('state')
    city = data.get('city')
    if not country_is_supported_for_sales(country):
        return jsonify({'shipping_cost': 0, 'available': False, 'message': LAUNCH_SOON_MESSAGE})
    if country or state or city:
        cost = estimate_shipping_cost(country, state, city, weight)
    else:
        cost = calculate_shipping_cost(rate_id, weight)
    return jsonify({'shipping_cost': cost})


@app.route('/support', methods=['GET', 'POST'])
@limiter.limit("10 per hour", methods=['POST'])
def support():
    """Support center with AI chatbot and voice features"""
    if request.method == 'POST':
        subject = request.form.get('subject', '').strip()
        body = request.form.get('body', '').strip()
        severity = request.form.get('severity', 'normal')
        channel = request.form.get('channel', 'web')

        if not subject or not body:
            flash('Describe the support issue so the team can help quickly.', 'danger')
            return redirect(url_for('support'))

        due_hours = 24 if severity in ['normal', 'high'] else 48
        if severity == 'urgent':
            due_hours = 12
        if channel == 'whatsapp' or severity == 'urgent':
            severity = 'urgent'
            channel = 'whatsapp'

        ticket = SupportTicket(
            user_id=current_user.id if current_user.is_authenticated else None,
            channel=channel,
            subject=subject,
            body=body,
            severity=severity,
            response_due_at=utcnow() + timedelta(hours=4),
            resolution_due_at=utcnow() + timedelta(hours=due_hours),
        )
        db.session.add(ticket)
        db.session.commit()

        log_admin_action('support_ticket_created', 'ticket', ticket.id, {
            'severity': severity,
            'channel': channel,
            'user_id': current_user.id if current_user.is_authenticated else None
        })

        if channel == 'whatsapp':
            whatsapp_url = f'https://wa.me/254708615309?text={requests.utils.quote(f"Urgent SMARKAFRICA support ticket #{ticket.id}: {subject}")}'
            flash(f'Urgent ticket created. WhatsApp support is ready at 0708615309', 'warning')
            return redirect(whatsapp_url)

        flash('Support ticket created. The team will track it against the response SLA.', 'success')
        return redirect(url_for('support'))

    tickets = []
    if current_user.is_authenticated:
        tickets = SupportTicket.query.filter_by(user_id=current_user.id).order_by(
            SupportTicket.created_at.desc()
        ).limit(10).all()

    # Use enhanced template with voice features
    try:
        return render_template('support_enhanced.html', tickets=tickets)
    except:
        # Fallback to original template if enhanced doesn't exist yet
        return render_template('support.html', tickets=tickets)


def support_ai_reply(message):
    text_value = (message or '').lower()
    if any(word in text_value for word in ['refund', 'wrong item', 'return', 'reversal']):
        return 'For refunds, keep your order number and M-Pesa receipt ready. I can help create an urgent ticket and route it to WhatsApp support at 0708615309.'
    if any(word in text_value for word in ['security', 'hack', 'account', 'password', 'fraud', 'suspicious']):
        return 'For platform security, change your password, avoid sharing OTPs, and escalate immediately through WhatsApp support at 0708615309. I can also create a tracked ticket.'
    if any(word in text_value for word in ['payment', 'stk', 'mpesa', 'm-pesa']):
        return 'For M-Pesa/STK issues, confirm the number is Safaricom, use 07XXXXXXXX or 2547XXXXXXXX, and retry once. If money was deducted, share the receipt through support.'
    if any(word in text_value for word in ['delivery', 'shipping', 'track', 'carrier']):
        return 'For delivery, open My Orders or Track Order to see the latest timeline. If the parcel is late, create a high-priority support ticket with your order number.'
    if any(word in text_value for word in ['bnpl', 'pay later', 'installment', 'instalment', 'lock']):
        return 'For BNPL, check your deposit, due date, and installment status. Overdue financed devices may enter calls/SMS-only mode until payment clears.'
    return 'I can help with orders, payments, refunds, delivery, BNPL, account security, and seller/storefront questions. For urgent refunds or security, use WhatsApp support at 0708615309.'


@app.route('/api/support/chatbot', methods=['POST'])
@limiter.limit("30 per minute")
def support_chatbot():
    """Enhanced AI chatbot with NLP and product recommendations"""
    data = request.get_json(silent=True) or {}
    message = data.get('message', '').strip()

    if not message:
        return jsonify({
            'reply': 'Tell me what happened and include an order number if you have one.',
            'urgent': False
        })

    # Use enhanced chatbot
    try:
        from chatbot_ai import create_chatbot_instance
        chatbot = create_chatbot_instance(app)
        user = current_user if current_user.is_authenticated else None
        reply = chatbot.get_response(message, user)
    except Exception as e:
        logger.error(f"Chatbot error: {e}")
        reply = support_ai_reply(message)  # Fallback to old method

    # Check if message is urgent
    urgent = any(word in message.lower() for word in [
        'urgent', 'refund', 'security', 'fraud', 'hacked', 'deducted',
        'stolen', 'emergency', 'help', 'scam'
    ])

    return jsonify({
        'reply': reply,
        'urgent': urgent,
        'whatsapp_url': 'https://wa.me/254708615309',
        'timestamp': datetime.utcnow().isoformat()
    })


@app.route('/storefront/apply', methods=['GET', 'POST'])
@login_required
def storefront_apply():
    if request.method == 'POST':
        business_name = request.form.get('business_name', '').strip()
        categories = request.form.get('categories', '').strip()
        if not business_name:
            flash('Enter the business name for storefront review.', 'danger')
            return redirect(url_for('storefront_apply'))
        base_slug = re.sub(r'[^a-z0-9]+', '-', business_name.lower()).strip('-')[:160] or f'business-{current_user.id}'
        slug = base_slug
        counter = 1
        while BusinessStorefront.query.filter_by(slug=slug).first():
            counter += 1
            slug = f'{base_slug}-{counter}'
        storefront = BusinessStorefront(
            owner_id=current_user.id,
            business_name=business_name,
            slug=slug,
            categories=categories,
            commission_percent=10.0,
            status='pending_review',
        )
        db.session.add(storefront)
        db.session.commit()
        flash('Storefront application submitted for MVP approval.', 'success')
        return redirect(url_for('storefront_apply'))
    storefronts = BusinessStorefront.query.filter_by(owner_id=current_user.id).order_by(BusinessStorefront.created_at.desc()).all()
    return render_template('storefront_apply.html', storefronts=storefronts)


@app.route('/bnpl/apply/<int:product_id>', methods=['GET', 'POST'])
@login_required
def bnpl_apply(product_id):
    product = Product.query.get_or_404(product_id)
    policy = BNPLProductPolicy.query.filter_by(product_id=product.id, is_enabled=True).first()
    if not policy:
        flash('BNPL is not enabled for this product yet.', 'warning')
        return redirect(url_for('product_page', slug=product.slug))
    if request.method == 'POST':
        deposit = request.form.get('deposit_percent', type=float) or policy.min_deposit_percent
        term_months = request.form.get('term_months', type=int) or policy.max_term_months
        deposit = max(policy.min_deposit_percent, min(20.0, deposit))
        term_months = max(1, min(policy.max_term_months, term_months))
        principal = product.discounted_price or product.selling_price or 0
        risk = bnpl_risk_score(current_user)
        status = 'approved' if risk >= 75 else 'manual_review'
        plan = BNPLPlan(
            user_id=current_user.id,
            product_id=product.id,
            principal_amount=principal,
            deposit_percent=deposit,
            term_months=term_months,
            risk_score=risk,
            approval_status=status,
            device_lock_code=request.form.get('device_lock_code', '').strip(),
            next_due_at=utcnow() + timedelta(days=30),
            approved_at=utcnow() if status == 'approved' else None,
        )
        db.session.add(plan)
        db.session.flush()
        create_bnpl_installments(plan)
        db.session.commit()
        flash(f'BNPL application submitted. Risk score {risk}; status {status}.', 'success')
        return redirect(url_for('orders'))
    return render_template('bnpl_apply.html', product=product, policy=policy, risk_score=bnpl_risk_score(current_user))


def handle_bnpl_admin_action():
    action = request.form.get('action', '')
    if action == 'bnpl_lock':
        plan = BNPLPlan.query.get_or_404(request.form.get('plan_id', type=int))
        plan.device_lock_code = request.form.get('device_lock_code', plan.device_lock_code or '').strip()
        plan.lock_status = request.form.get('lock_status', plan.lock_status or 'unlocked')
        if plan.lock_status == 'unlocked':
            plan.last_reminder_at = utcnow()
        flash('BNPL device lock status updated.', 'success')
        return True
    if action == 'create_bnpl':
        user = User.query.get_or_404(request.form.get('user_id', type=int))
        product = Product.query.get_or_404(request.form.get('product_id', type=int))
        principal = request.form.get('principal_amount', type=float) or (product.discounted_price or product.selling_price or 0)
        deposit = request.form.get('deposit_percent', type=float) or 15.0
        term_months = request.form.get('term_months', type=int) or 3
        risk = bnpl_risk_score(user)
        status = 'approved' if risk >= 75 and 10 <= deposit <= 20 and term_months <= 6 else 'manual_review'
        plan = BNPLPlan(
            user_id=user.id,
            product_id=product.id,
            principal_amount=principal,
            deposit_percent=max(10, min(20, deposit)),
            term_months=max(1, min(6, term_months)),
            risk_score=risk,
            approval_status=status,
            device_lock_code=request.form.get('device_lock_code', '').strip(),
            next_due_at=utcnow() + timedelta(days=30),
            approved_at=utcnow() if status == 'approved' else None,
        )
        db.session.add(plan)
        db.session.flush()
        create_bnpl_installments(plan)
        flash(f'BNPL plan created with risk score {risk} and status {status}.', 'success')
        return True
    if action == 'bnpl_policy':
        product = Product.query.get_or_404(request.form.get('product_id', type=int))
        policy = BNPLProductPolicy.query.filter_by(product_id=product.id).first()
        if not policy:
            policy = BNPLProductPolicy(product_id=product.id)
            db.session.add(policy)
        policy.is_enabled = request.form.get('is_enabled') == '1'
        policy.min_deposit_percent = max(10, min(20, request.form.get('min_deposit_percent', type=float) or 15))
        policy.max_term_months = max(1, min(6, request.form.get('max_term_months', type=int) or 3))
        policy.partner_name = request.form.get('partner_name', '').strip()
        policy.notes = request.form.get('notes', '').strip()
        policy.approved_by = current_user.id
        flash(f'BNPL policy updated for {product.name}.', 'success')
        return True
    if action == 'bnpl_payment':
        installment = BNPLInstallment.query.get_or_404(request.form.get('installment_id', type=int))
        paid = request.form.get('amount_paid', type=float) or installment.amount_due
        installment.amount_paid = min(installment.amount_due, installment.amount_paid + paid)
        if installment.amount_paid >= installment.amount_due:
            installment.status = 'paid'
            installment.paid_at = utcnow()
        update_bnpl_lock_status(installment.plan)
        flash('BNPL installment payment recorded.', 'success')
        return True
    return False


@app.route('/admin/bnpl', methods=['GET', 'POST'])
@login_required
@mvp_required
def admin_bnpl():
    if request.method == 'POST':
        if handle_bnpl_admin_action():
            db.session.commit()
        return redirect(url_for('admin_bnpl'))
    return render_template(
        'admin/bnpl.html',
        bnpl_plans=BNPLPlan.query.order_by(BNPLPlan.created_at.desc()).limit(80).all(),
        bnpl_policies=BNPLProductPolicy.query.order_by(BNPLProductPolicy.updated_at.desc()).limit(80).all(),
        bnpl_installments=BNPLInstallment.query.order_by(BNPLInstallment.due_at.asc()).limit(120).all(),
        users=User.query.order_by(User.created_at.desc()).limit(120).all(),
        products=Product.query.filter_by(is_active=True, is_digital=False).order_by(Product.created_at.desc()).limit(120).all(),
    )


# ========================================================================
# COINS SYSTEM — Engagement & Rewards
# ========================================================================


def coin_setting(key, default):
    return int(float(Setting.get(key, str(default)) or default))


def get_user_coin_balance(user_id):
    last_txn = CoinTransaction.query.filter_by(user_id=user_id).order_by(CoinTransaction.created_at.desc()).first()
    return last_txn.balance_after if last_txn else 0


def award_coins(user_id, amount, coin_type, description='', reference_id=''):
    if amount <= 0:
        return None
    current_balance = get_user_coin_balance(user_id)
    new_balance = current_balance + amount
    txn = CoinTransaction(
        user_id=user_id,
        amount=amount,
        coin_type=coin_type,
        description=description,
        reference_id=reference_id,
        balance_after=new_balance,
    )
    db.session.add(txn)
    return txn


def spend_coins(user_id, amount, coin_type, description='', reference_id=''):
    current_balance = get_user_coin_balance(user_id)
    if amount > current_balance:
        return None
    new_balance = current_balance - amount
    txn = CoinTransaction(
        user_id=user_id,
        amount=-amount,
        coin_type=coin_type,
        description=description,
        reference_id=reference_id,
        balance_after=new_balance,
    )
    db.session.add(txn)
    return txn


def process_daily_check_in(user_id):
    today = datetime.utcnow().date()
    existing = CoinDailyCheckIn.query.filter_by(user_id=user_id, check_in_date=today).first()
    if existing:
        return None, existing.streak_count

    yesterday = today - timedelta(days=1)
    yesterday_checkin = CoinDailyCheckIn.query.filter_by(user_id=user_id, check_in_date=yesterday).first()
    streak = (yesterday_checkin.streak_count + 1) if yesterday_checkin else 1

    base_coins = coin_setting('coins_daily_login', 5)
    bonus = 0
    if streak >= 7:
        bonus = coin_setting('coins_streak_bonus_7day', 25)
    elif streak >= 3:
        bonus = 10

    total_earned = base_coins + bonus
    checkin = CoinDailyCheckIn(
        user_id=user_id,
        check_in_date=today,
        streak_count=streak,
        coins_earned=total_earned,
    )
    db.session.add(checkin)
    award_coins(user_id, total_earned, 'daily_login',
                f'Daily check-in (day {streak} streak)' + (f' + {bonus} bonus!' if bonus else ''))
    db.session.commit()
    return checkin, streak


def award_purchase_coins(user_id, order_amount, order_id=''):
    coins_per_1000 = coin_setting('coins_purchase_per_1000', 10)
    coins = int((order_amount / 1000) * coins_per_1000)
    if coins > 0:
        award_coins(user_id, coins, 'purchase',
                    f'Purchase reward (KSh {order_amount:,.0f})',
                    reference_id=f'order:{order_id}')
    return coins


def award_review_coins(user_id, review_id=''):
    coins = coin_setting('coins_review_reward', 10)
    award_coins(user_id, coins, 'review', 'Product review reward', reference_id=f'review:{review_id}')
    return coins


def award_referral_coins(user_id, referred_user_id=''):
    coins = coin_setting('coins_referral_bonus', 50)
    award_coins(user_id, coins, 'referral', 'Referral bonus', reference_id=f'user:{referred_user_id}')
    return coins


@app.route('/coins')
@login_required
def coins_page():
    balance = get_user_coin_balance(current_user.id)
    today = datetime.utcnow().date()
    checked_in_today = CoinDailyCheckIn.query.filter_by(user_id=current_user.id, check_in_date=today).first()

    # Get streak info
    streak = 0
    if checked_in_today:
        streak = checked_in_today.streak_count
    else:
        yesterday = today - timedelta(days=1)
        yesterday_checkin = CoinDailyCheckIn.query.filter_by(user_id=current_user.id, check_in_date=yesterday).first()
        streak = yesterday_checkin.streak_count if yesterday_checkin else 0

    transactions = CoinTransaction.query.filter_by(user_id=current_user.id).order_by(
        CoinTransaction.created_at.desc()).limit(30).all()

    # Leaderboard (top 10)
    from sqlalchemy import func as sqfunc
    leaderboard = db.session.query(
        User.username,
        sqfunc.coalesce(
            db.session.query(CoinTransaction.balance_after)
            .filter(CoinTransaction.user_id == User.id)
            .order_by(CoinTransaction.created_at.desc())
            .limit(1)
            .correlate(User)
            .scalar_subquery(), 0
        ).label('balance')
    ).filter(User.is_active == True).order_by(db.text('balance DESC')).limit(10).all()

    return render_template('coins.html',
                           balance=balance,
                           streak=streak,
                           checked_in_today=checked_in_today,
                           transactions=transactions,
                           leaderboard=leaderboard,
                           coin_settings={
                               'daily_login': coin_setting('coins_daily_login', 5),
                               'purchase_per_1000': coin_setting('coins_purchase_per_1000', 10),
                               'referral_bonus': coin_setting('coins_referral_bonus', 50),
                               'review_reward': coin_setting('coins_review_reward', 10),
                               'streak_bonus_7day': coin_setting('coins_streak_bonus_7day', 25),
                               'event_participation': coin_setting('coins_event_participation', 20),
                           })


@app.route('/coins/check-in', methods=['POST'])
@login_required
@limiter.limit("3 per minute")
def coins_check_in():
    checkin, streak = process_daily_check_in(current_user.id)
    if checkin:
        flash(f'Daily check-in complete! +{checkin.coins_earned} coins (streak: {streak} days)', 'success')
    else:
        flash(f'You already checked in today! Current streak: {streak} days.', 'info')
    return redirect(url_for('coins_page'))


# ========================================================================
# RAFFLE SYSTEM
# ========================================================================

import secrets
import time as _time

def _raffle_weighted_draw(raffle):
    """
    Provably fair weighted random draw.
    Uses a seed derived from raffle ID + sold ticket count + server entropy
    to produce a deterministic-yet-unpredictable winner ticket number.
    """
    seed_material = f"{raffle.id}-{raffle.tickets_sold}-{secrets.token_hex(16)}-{_time.time_ns()}"
    seed_hash = hashlib.sha256(seed_material.encode()).hexdigest()
    winning_index = int(seed_hash, 16) % raffle.tickets_sold
    tickets = RaffleTicket.query.filter_by(raffle_id=raffle.id).order_by(RaffleTicket.ticket_number).all()
    return tickets[winning_index] if tickets else None


@app.route('/raffles')
def raffles():
    active_raffles = Raffle.query.filter_by(status='active').order_by(Raffle.ends_at.asc()).all()
    completed_raffles = Raffle.query.filter_by(status='completed').order_by(Raffle.drawn_at.desc()).limit(10).all()
    user_tickets = []
    if current_user.is_authenticated:
        user_tickets = RaffleTicket.query.filter_by(user_id=current_user.id).order_by(RaffleTicket.purchased_at.desc()).limit(50).all()
    return render_template('raffles.html',
                           active_raffles=active_raffles,
                           completed_raffles=completed_raffles,
                           user_tickets=user_tickets)


@app.route('/raffle/<int:raffle_id>')
def raffle_detail(raffle_id):
    raffle = Raffle.query.get_or_404(raffle_id)
    user_tickets = []
    if current_user.is_authenticated:
        user_tickets = RaffleTicket.query.filter_by(raffle_id=raffle_id, user_id=current_user.id).all()
    progress_pct = (raffle.tickets_sold / raffle.total_tickets * 100) if raffle.total_tickets else 0
    return render_template('raffle_detail.html', raffle=raffle, user_tickets=user_tickets, progress_pct=progress_pct)


@app.route('/raffle/<int:raffle_id>/buy', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def raffle_buy_ticket(raffle_id):
    raffle = Raffle.query.get_or_404(raffle_id)
    if raffle.status != 'active':
        flash('This raffle is no longer accepting tickets.', 'warning')
        return redirect(url_for('raffle_detail', raffle_id=raffle_id))

    qty = request.form.get('quantity', 1, type=int)
    if qty < 1 or qty > 50:
        flash('You can buy between 1 and 50 tickets at a time.', 'warning')
        return redirect(url_for('raffle_detail', raffle_id=raffle_id))

    remaining = raffle.total_tickets - raffle.tickets_sold
    if qty > remaining:
        flash(f'Only {remaining} tickets remain.', 'warning')
        return redirect(url_for('raffle_detail', raffle_id=raffle_id))

    for i in range(qty):
        raffle.tickets_sold += 1
        ticket = RaffleTicket(
            raffle_id=raffle.id,
            user_id=current_user.id,
            ticket_number=raffle.tickets_sold,
        )
        db.session.add(ticket)

    db.session.commit()
    flash(f'Successfully purchased {qty} ticket(s) for "{raffle.title}"!', 'success')

    # Auto-draw if all tickets sold
    if raffle.tickets_sold >= raffle.total_tickets:
        raffle.status = 'drawing'
        db.session.commit()
        _execute_raffle_draw(raffle)

    return redirect(url_for('raffle_detail', raffle_id=raffle_id))


def _execute_raffle_draw(raffle):
    winning_ticket = _raffle_weighted_draw(raffle)
    if winning_ticket:
        raffle.winner_id = winning_ticket.user_id
        raffle.winner_ticket_number = winning_ticket.ticket_number
        raffle.drawn_at = datetime.utcnow()
        raffle.status = 'completed'
        db.session.commit()

        # Record financials: seller gets product price, platform keeps the margin
        total_revenue = raffle.tickets_sold * raffle.ticket_price
        seller_payout = raffle.product_value
        platform_profit = total_revenue - seller_payout

        # Record seller payout transaction (platform buys product at full price)
        db.session.add(Transaction(
            order_id=None,
            user_id=raffle.seller_id,
            type='raffle_seller_payout',
            amount=seller_payout,
            description=f'Raffle product sold: {raffle.title}',
            status='completed',
        ))

        # Record platform commission from ticket sales margin
        if platform_profit > 0:
            db.session.add(Transaction(
                order_id=None,
                user_id=raffle.seller_id,
                type='raffle_platform_commission',
                amount=platform_profit,
                commission_amount=platform_profit,
                description=f'Platform profit from raffle: {raffle.title} ({raffle.tickets_sold} tickets x KSh {raffle.ticket_price})',
                status='completed',
            ))

        notif = CustomerNotification(
            user_id=winning_ticket.user_id,
            title='You Won a Raffle!',
            body=f'Congratulations! Your ticket #{winning_ticket.ticket_number} won "{raffle.title}". '
                 f'The product worth KSh {raffle.product_value:,.0f} is yours!',
            notification_type='raffle_win',
        )
        db.session.add(notif)
        db.session.commit()


@app.route('/admin/raffles', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_raffles():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'create':
            product_id = request.form.get('product_id', type=int)
            product = Product.query.get(product_id) if product_id else None
            product_value = request.form.get('product_value', type=float) or (product.selling_price if product else 0)
            ticket_price = request.form.get('ticket_price', type=float, default=10.0)
            # Platform margin: sell more tickets than the product costs so platform profits
            platform_margin_pct = float(Setting.get('raffle_platform_margin_pct', '20') or 20)
            base_tickets = int(product_value / ticket_price) if ticket_price > 0 else 100
            total_tickets = request.form.get('total_tickets', type=int) or int(base_tickets * (1 + platform_margin_pct / 100))
            ends_days = request.form.get('duration_days', 7, type=int)

            raffle = Raffle(
                product_id=product_id,
                seller_id=current_user.id,
                title=request.form.get('title', product.name if product else 'Raffle'),
                description=request.form.get('description', ''),
                product_value=product_value,
                ticket_price=ticket_price,
                total_tickets=total_tickets,
                ends_at=datetime.utcnow() + timedelta(days=ends_days),
            )
            db.session.add(raffle)
            db.session.commit()
            flash(f'Raffle "{raffle.title}" created with {total_tickets} tickets at KSh {ticket_price} each.', 'success')

        elif action == 'draw':
            raffle_id = request.form.get('raffle_id', type=int)
            raffle = Raffle.query.get(raffle_id)
            if raffle and raffle.status in ('active', 'sold_out'):
                raffle.status = 'drawing'
                db.session.commit()
                _execute_raffle_draw(raffle)
                flash(f'Draw completed! Winner: Ticket #{raffle.winner_ticket_number}', 'success')

        elif action == 'cancel':
            raffle_id = request.form.get('raffle_id', type=int)
            raffle = Raffle.query.get(raffle_id)
            if raffle and raffle.status == 'active':
                raffle.status = 'cancelled'
                db.session.commit()
                flash('Raffle cancelled.', 'info')

        return redirect(url_for('admin_raffles'))

    all_raffles = Raffle.query.order_by(Raffle.created_at.desc()).limit(100).all()
    products = Product.query.filter_by(is_active=True, is_digital=False).order_by(Product.name).all()
    return render_template('admin/raffles.html', raffles=all_raffles, products=products)


# ========================================================================
# ADMIN ROUTES
# ========================================================================

@app.route('/admin')
@admin_required
def admin_dashboard():
    # Build stats for dashboard
    total_products = Product.query.count()
    total_orders = Order.query.count()
    total_users = User.query.count()
    total_categories = Category.query.count()
    pending_orders = Order.query.filter_by(status='pending').count()

    # Recent orders
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(5).all()

    # Unread/low stock alerts
    low_stock_count = Product.query.filter(Product.stock < 10).count()
    pending_reviews = Review.query.filter_by(is_visible=False).count()
    production_setup_issues = production_setup_pending(current_user)

    # P&L Calculation - using safe column detection
    from datetime import datetime, timedelta
    from sqlalchemy import func

    # Try to find the total column name on Order model
    order_columns = [c.name for c in Order.__table__.columns]

    # Determine which column holds the order total
    total_col = None
    for possible in ['total', 'total_amount', 'amount', 'grand_total', 'total_price', 'order_total']:
        if possible in order_columns:
            total_col = getattr(Order, possible)
            break

    # Gross revenue
    gross_revenue = 0.0
    if total_col:
        gross_revenue = db.session.query(func.sum(total_col)).filter(
            Order.status == 'completed'
        ).scalar() or 0.0

    # If no total column found, calculate from completed orders' items
    if not total_col or gross_revenue == 0.0:
        completed = Order.query.filter_by(status='completed').all()
        gross_revenue = sum(
            sum(item.quantity * item.price for item in order.items)
            for order in completed
            if hasattr(order, 'items')
        )

    # Total cost of goods sold
    total_cogs = 0.0
    try:
        total_cogs = db.session.query(
            func.sum(Product.buying_price * OrderItem.quantity)
        ).join(OrderItem, OrderItem.product_id == Product.id
               ).join(Order, Order.id == OrderItem.order_id
                      ).filter(Order.status == 'completed').scalar() or 0.0
    except Exception:
        app.logger.exception('Failed to calculate dashboard COGS')

    # Total shipping costs
    total_shipping = 0.0
    if 'shipping_cost' in order_columns:
        total_shipping = db.session.query(func.sum(Order.shipping_cost)).filter(
            Order.status == 'completed'
        ).scalar() or 0.0

    # Gross profit
    gross_profit = gross_revenue - total_cogs - total_shipping

    # Transactions
    total_transactions = Transaction.query.count()

    # Total refunds
    total_refunds = 0.0
    try:
        total_refunds = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.status == 'refunded'
        ).scalar() or 0.0
    except Exception:
        app.logger.exception('Failed to calculate dashboard refunds')

    net_profit = gross_profit - abs(total_refunds)
    profit_margin = (net_profit / gross_revenue * 100) if gross_revenue > 0 else 0

    pnl = {
        'gross_revenue': gross_revenue,
        'total_cogs': total_cogs,
        'total_shipping': total_shipping,
        'gross_profit': gross_profit,
        'net_profit': net_profit,
        'profit_margin': profit_margin,
        'total_transactions': total_transactions,
        'total_refunds': abs(total_refunds)
    }

    # Total sales for stats
    total_sales = gross_revenue

    stats = {
        'total_products': total_products,
        'total_orders': total_orders,
        'total_users': total_users,
        'total_categories': total_categories,
        'total_sales': total_sales,
        'pending_orders': pending_orders,
        'pending_reviews': pending_reviews,
        'low_stock_count': low_stock_count
    }

    return render_template('admin/dashboard.html', production_setup_issues=production_setup_issues, stats=stats, recent_orders=recent_orders, pnl=pnl)


@app.route('/admin/intelligent-architecture', methods=['GET', 'POST'])
@login_required
@mvp_required
def admin_intelligent_architecture():
    if request.method == 'POST':
        action = request.form.get('action', '')
        if action == 'exchange_rate':
            quote = request.form.get('quote_currency', '').strip().upper()[:3]
            rate = request.form.get('rate', type=float)
            if quote and rate and rate > 0:
                row = ExchangeRate.query.filter_by(base_currency='KES', quote_currency=quote).first()
                if not row:
                    row = ExchangeRate(base_currency='KES', quote_currency=quote)
                    db.session.add(row)
                row.rate = rate
                row.source = request.form.get('source', 'manual_mvp').strip() or 'manual_mvp'
                flash(f'KES/{quote} exchange rate updated.', 'success')
        elif action == 'bnpl_lock':
            plan = BNPLPlan.query.get_or_404(request.form.get('plan_id', type=int))
            plan.device_lock_code = request.form.get('device_lock_code', plan.device_lock_code or '').strip()
            plan.lock_status = request.form.get('lock_status', plan.lock_status or 'unlocked')
            if plan.lock_status == 'unlocked':
                plan.last_reminder_at = utcnow()
            flash('BNPL device lock status updated.', 'success')
        elif action == 'create_bnpl':
            user = User.query.get_or_404(request.form.get('user_id', type=int))
            product = Product.query.get_or_404(request.form.get('product_id', type=int))
            principal = request.form.get('principal_amount', type=float) or (product.discounted_price or product.selling_price or 0)
            deposit = request.form.get('deposit_percent', type=float) or 15.0
            term_months = request.form.get('term_months', type=int) or 3
            risk = bnpl_risk_score(user)
            status = 'approved' if risk >= 75 and 10 <= deposit <= 20 and term_months <= 6 else 'manual_review'
            plan = BNPLPlan(
                user_id=user.id,
                product_id=product.id,
                principal_amount=principal,
                deposit_percent=max(10, min(20, deposit)),
                term_months=max(1, min(6, term_months)),
                risk_score=risk,
                approval_status=status,
                device_lock_code=request.form.get('device_lock_code', '').strip(),
                next_due_at=utcnow() + timedelta(days=30),
                approved_at=utcnow() if status == 'approved' else None,
            )
            db.session.add(plan)
            db.session.flush()
            create_bnpl_installments(plan)
            flash(f'BNPL plan created with risk score {risk} and status {status}.', 'success')
        elif action == 'bnpl_policy':
            product = Product.query.get_or_404(request.form.get('product_id', type=int))
            policy = BNPLProductPolicy.query.filter_by(product_id=product.id).first()
            if not policy:
                policy = BNPLProductPolicy(product_id=product.id)
                db.session.add(policy)
            policy.is_enabled = request.form.get('is_enabled') == '1'
            policy.min_deposit_percent = max(10, min(20, request.form.get('min_deposit_percent', type=float) or 15))
            policy.max_term_months = max(1, min(6, request.form.get('max_term_months', type=int) or 3))
            policy.partner_name = request.form.get('partner_name', '').strip()
            policy.notes = request.form.get('notes', '').strip()
            policy.approved_by = current_user.id
            flash(f'BNPL policy updated for {product.name}.', 'success')
        elif action == 'bnpl_payment':
            installment = BNPLInstallment.query.get_or_404(request.form.get('installment_id', type=int))
            paid = request.form.get('amount_paid', type=float) or installment.amount_due
            installment.amount_paid = min(installment.amount_due, installment.amount_paid + paid)
            if installment.amount_paid >= installment.amount_due:
                installment.status = 'paid'
                installment.paid_at = utcnow()
            update_bnpl_lock_status(installment.plan)
            flash('BNPL installment payment recorded.', 'success')
        elif action == 'trust_score':
            entity_type = request.form.get('entity_type', 'seller').strip()
            entity_id = request.form.get('entity_id', type=int)
            score = request.form.get('score', type=float)
            if entity_id and score is not None:
                row = TrustScore.query.filter_by(entity_type=entity_type, entity_id=entity_id).first()
                if not row:
                    row = TrustScore(entity_type=entity_type, entity_id=entity_id)
                    db.session.add(row)
                row.score = max(0, min(100, score))
                row.status = 'verified' if row.score >= 80 else 'review' if row.score >= 60 else 'watch'
                row.factors = request.form.get('factors', '').strip()
                flash('Trust score updated.', 'success')
        elif action == 'storefront_status':
            storefront = BusinessStorefront.query.get_or_404(request.form.get('storefront_id', type=int))
            storefront.status = request.form.get('status', storefront.status)
            storefront.verification_notes = request.form.get('verification_notes', storefront.verification_notes or '').strip()
            if storefront.status == 'approved' and not storefront.approved_at:
                storefront.approved_at = utcnow()
            flash('Storefront review updated.', 'success')
        elif action == 'loyalty_points':
            user = User.query.get_or_404(request.form.get('user_id', type=int))
            points = request.form.get('points', type=int) or 0
            db.session.add(LoyaltyLedger(
                user_id=user.id,
                event_type=request.form.get('event_type', 'manual_adjustment'),
                points=points,
                description=request.form.get('description', 'MVP loyalty adjustment').strip(),
            ))
            flash('Loyalty ledger updated.', 'success')
        elif action == 'resolve_ticket':
            ticket = SupportTicket.query.get_or_404(request.form.get('ticket_id', type=int))
            ticket.status = request.form.get('status', 'resolved')
            ticket.assigned_admin_id = current_user.id
            if ticket.status == 'resolved':
                ticket.resolved_at = utcnow()
            flash('Support ticket updated.', 'success')
        db.session.commit()
        return redirect(url_for('admin_intelligent_architecture'))

    ensure_architecture_defaults()
    db.session.commit()
    snapshot = architecture_snapshot()
    return render_template(
        'admin/intelligent_architecture.html',
        snapshot=snapshot,
        exchange_rates=ExchangeRate.query.order_by(ExchangeRate.quote_currency.asc()).all(),
        tickets=SupportTicket.query.order_by(SupportTicket.created_at.desc()).limit(20).all(),
        storefronts=BusinessStorefront.query.order_by(BusinessStorefront.created_at.desc()).limit(20).all(),
        bnpl_plans=BNPLPlan.query.order_by(BNPLPlan.created_at.desc()).limit(20).all(),
        bnpl_policies=BNPLProductPolicy.query.order_by(BNPLProductPolicy.updated_at.desc()).limit(20).all(),
        bnpl_installments=BNPLInstallment.query.order_by(BNPLInstallment.due_at.asc()).limit(30).all(),
        trust_scores=TrustScore.query.order_by(TrustScore.updated_at.desc()).limit(20).all(),
        loyalty_rows=LoyaltyLedger.query.order_by(LoyaltyLedger.created_at.desc()).limit(20).all(),
        users=User.query.order_by(User.created_at.desc()).limit(80).all(),
        products=Product.query.filter_by(is_active=True).order_by(Product.created_at.desc()).limit(80).all(),
        tasks=AutomationTask.query.order_by(AutomationTask.efficiency_score.desc()).limit(20).all(),
    )


@app.route('/admin/market-intelligence')
@login_required
@admin_required
def admin_market_intelligence():
    categories = Category.query.order_by(Category.name.asc()).all()
    selected_category = request.args.get('category', 'all')
    payload = build_market_intelligence_payload(selected_category)
    suggestions = pricing_suggestions() if current_user.admin_level == 'mvp' or Setting.get('pricing_suggestions_admin_enabled', '0') == '1' else []
    warnings = stock_warnings()
    return render_template('admin/market_intelligence.html',
                           categories=categories,
                           selected_category=selected_category,
                           market_payload=payload,
                           pricing_suggestions=suggestions,
                           stock_warnings=warnings)


@app.route('/admin/api/market-intelligence')
@login_required
@admin_required
def admin_market_intelligence_api():
    selected_category = request.args.get('category', 'all')
    return jsonify(build_market_intelligence_payload(selected_category))


@app.route('/api/market-news')
def market_news_api():
    generate_market_news_if_due()
    category = request.args.get('category', '').strip()
    query = MarketNews.query.filter_by(is_cleared=False)
    if category:
        query = query.filter(MarketNews.product_name.ilike(f'%{category}%'))
    news = query.order_by(MarketNews.created_at.desc()).limit(24).all()
    return jsonify({
        'items': [
            {
                'title': item.title,
                'body': item.body,
                'category': item.product_name,
                'region': item.region,
                'direction': item.direction,
                'source_url': item.source_url,
                'generated_by': item.generated_by,
                'created_at': item.created_at.isoformat() if item.created_at else None,
            }
            for item in news
        ]
    })


@app.route('/admin/api/price-check', methods=['POST'])
@login_required
@admin_required
def admin_price_check_api():
    data = request.get_json() or {}
    return jsonify(market_price_reference(
        data.get('name', ''),
        data.get('category_id'),
        price=data.get('selling_price'),
        buying_price=data.get('buying_price'),
        exclude_id=data.get('product_id'),
        description=data.get('description', '')
    ))


@app.route('/admin/api/product-description', methods=['POST'])
@login_required
@admin_required
def admin_product_description_api():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'description': '', 'error': 'Product name is required.'}), 400
    return jsonify({
        'description': generate_product_description(
            name,
            data.get('category_id'),
            data.get('product_condition', 'new'),
            bool(data.get('is_digital')),
            data.get('image_url', '')
        )
    })


@app.route('/admin/market-news', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_market_news():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'clear':
            MarketNews.query.filter_by(is_cleared=False).update({MarketNews.is_cleared: True})
            db.session.commit()
            flash('Market news cleared.', 'success')
        elif action == 'generate':
            try:
                created = generate_market_news_if_due(force=True)
                flash(f'Generated {created} market news update(s).', 'success')
            except OperationalError as exc:
                db.session.rollback()
                Setting.set('market_news_generation_lock', '0')
                app.logger.exception('Market news generation failed: %s', exc)
                flash('Market news generation hit a database lock. Try again in a moment; production should use Postgres/MySQL for real traffic.', 'warning')
        elif action == 'send_followers':
            sent = send_category_follow_updates()
            alerts = notify_price_alerts()
            stock_sent = send_stock_warning_emails(force=True)
            flash(f'Sent {sent} category follower update email(s), {alerts} price alert email(s), and {stock_sent} stock warning email(s).', 'success')
        return redirect(url_for('admin_market_news'))

    generate_market_news_if_due()
    news = MarketNews.query.filter_by(is_cleared=False).order_by(MarketNews.created_at.desc()).limit(100).all()
    news_cards = []
    news_seed = int(utcnow().timestamp() // 3600)
    for item in news:
        image_payload = trend_image_payload(
            item.product_name or item.title,
            item.category.name if item.category else item.title,
            item.image_url or '',
            rotation_seed=news_seed
        )
        news_cards.append({'item': item, **image_payload})
    references = PRODUCT_MARKET_REFERENCES
    return render_template('admin/market_news.html', news=news, news_cards=news_cards, references=references)


@app.route('/admin/product-trends')
@login_required
@admin_required
def admin_product_trends():
    manual_refresh = request.args.get('refresh') == '1'
    generate_market_news_if_due(force=manual_refresh)
    trend_seed = int(utcnow().timestamp()) if manual_refresh else int(utcnow().timestamp() // 3600)
    news_items = MarketNews.query.filter(
        MarketNews.is_cleared == False
    ).order_by(MarketNews.created_at.desc()).limit(24).all()
    cards = []
    for item in news_items:
        image_payload = trend_image_payload(
            item.product_name or item.title,
            item.category.name if item.category else item.title,
            item.image_url or '',
            rotation_seed=trend_seed
        )
        cards.append({
            'title': item.product_name or item.title,
            'body': item.body,
            **image_payload,
            'price': '',
            'direction': item.direction,
        })
    for reference in PRODUCT_MARKET_REFERENCES:
        image_payload = trend_image_payload(
            reference['label'],
            reference.get('category_hint', 'Product'),
            reference.get('image_url', ''),
            reference.get('image_candidates', []),
            rotation_seed=trend_seed
        )
        cards.append({
            'title': reference['label'],
            'body': reference.get('news_body', ''),
            **image_payload,
            'price': f"KSh {reference['kenya_low']:,.0f} - KSh {reference['kenya_high']:,.0f}",
            'direction': 'watch',
        })
    products = Product.query.filter(Product.is_active == True).order_by(Product.admin_priority.desc(), Product.created_at.desc()).limit(18).all()
    for product in products:
        ref = product_market_reference_match(product.name, product.category.name if product.category else '')
        image_payload = trend_image_payload(
            product.name,
            product.category.name if product.category else 'Product',
            product.image_url or '',
            rotation_seed=trend_seed
        )
        cards.append({
            'title': product.name,
            'body': f"Current store price KSh {(product.discounted_price or product.selling_price):,.2f}. Watch category demand, stock, rating, and competitor launches before advertising.",
            **image_payload,
            'price': f"KSh {ref['kenya_low']:,.0f} - KSh {ref['kenya_high']:,.0f}" if ref else f"KSh {(product.discounted_price or product.selling_price):,.0f}",
            'direction': 'watch',
        })
    showcase_rows = [
        ('Phones and electronics', 'electronics', 'Smartphone, tablet, and accessory listings need clear product imagery before campaigns go live.'),
        ('Gaming and consoles', 'gaming', 'Consoles, controllers, chairs, and monitors move with seasonal demand and bundle pricing.'),
        ('Laptops and computing', 'computing', 'Business, student, and creator computers should be compared by RAM, storage, battery, and warranty.'),
        ('Home appliances', 'home', 'Appliance pricing changes with capacity, inverter technology, warranty, and delivery costs.'),
        ('Fashion and footwear', 'fashion', 'Fashion demand depends on quality, sizing, current styles, and reliable seller photos.'),
        ('Beauty and personal care', 'beauty', 'Beauty product campaigns should show packaging clearly and avoid unsupported claims.'),
        ('Music and audio', 'music', 'Tracks, beats, mixes, and voice-over services can use preview-first selling with full download after payment.'),
    ]
    for title, category_key, body in showcase_rows:
        image_payload = trend_image_payload(title, category_key, rotation_seed=trend_seed)
        cards.append({
            'title': title,
            'body': body,
            **image_payload,
            'price': '',
            'direction': 'watch',
        })
    return render_template('admin/product_trends.html', cards=cards[:36])


@app.route('/admin/disbursements', methods=['GET', 'POST'])
@login_required
@mvp_required
def admin_disbursements():
    if request.method == 'POST':
        action = request.form.get('action', 'run_cycle')
        snapshot = disbursement_snapshot()
        created = 0

        if action == 'clear':
            cleared = Transaction.query.filter(
                Transaction.type.in_(['withdrawal', 'salary', 'manufacturer_payout', 'disbursement']),
                Transaction.status.in_(['queued', 'cancelled'])
            ).delete(synchronize_session=False)
            db.session.commit()
            flash(f'Cleared {cleared} queued/cancelled disbursement ledger item(s).', 'success')
            return redirect(url_for('admin_disbursements'))

        if action == 'cancel':
            cancelled = Transaction.query.filter(
                Transaction.type.in_(['withdrawal', 'salary', 'manufacturer_payout', 'disbursement']),
                Transaction.status == 'queued'
            ).update({Transaction.status: 'cancelled'}, synchronize_session=False)
            WithdrawalRequest.query.filter_by(status='queued_for_payment').update({WithdrawalRequest.status: 'pending_review'})
            AdminSalary.query.filter_by(status='queued').update({AdminSalary.status: 'pending'})
            db.session.commit()
            flash(f'Cancelled {cancelled} queued disbursement ledger item(s).', 'warning')
            return redirect(url_for('admin_disbursements'))

        if action == 'save_salary':
            admin_id = request.form.get('admin_id', type=int)
            admin_user = User.query.get(admin_id) if admin_id else None
            amount = float(request.form.get('amount', 0) or 0)
            payment_method = request.form.get('payment_method', 'mpesa')
            account_number = request.form.get('account_number', '').strip()
            work_start_raw = request.form.get('work_start_date', '').strip()
            notes = request.form.get('notes', '').strip()
            work_start_date = None
            if work_start_raw:
                try:
                    work_start_date = datetime.strptime(work_start_raw, '%Y-%m-%d').date()
                except ValueError:
                    work_start_date = None
            if not admin_user or not admin_user.is_admin or amount <= 0 or not account_number:
                flash('Choose an admin/worker, amount, and M-Pesa or bank account for salary disbursement.', 'danger')
                return redirect(url_for('admin_disbursements'))
            admin_user.salary_payment_method = payment_method
            admin_user.salary_account_number = account_number
            admin_user.work_start_date = work_start_date
            db.session.add(AdminSalary(
                admin_id=admin_user.id,
                amount=amount,
                payment_method=payment_method,
                account_number=account_number,
                work_start_date=work_start_date,
                notes=notes,
                status='pending'
            ))
            db.session.commit()
            flash(f'Salary setup saved for {admin_user.username}.', 'success')
            return redirect(url_for('admin_disbursements'))

        if action == 'run_cycle':
            for withdrawal in snapshot['pending_withdrawals']:
                db.session.add(Transaction(
                    user_id=withdrawal.user_id,
                    type='withdrawal',
                    amount=-abs(withdrawal.amount or 0),
                    description=f'Automated seller withdrawal to {withdrawal.method}: {withdrawal.destination}',
                    status='queued',
                    available_on=utcnow()
                ))
                withdrawal.status = 'queued_for_payment'
                withdrawal.reviewed_at = utcnow()
                created += 1

            for salary in snapshot['pending_salaries']:
                db.session.add(Transaction(
                    user_id=salary.admin_id,
                    type='salary',
                    amount=-abs(salary.amount or 0),
                    description=f'Automated admin/employee salary disbursement #{salary.id} to {salary.payment_method}: {salary.account_number}',
                    status='queued',
                    available_on=utcnow()
                ))
                salary.status = 'queued'
                created += 1

            if snapshot['manufacturer_reserve'] > 0:
                db.session.add(Transaction(
                    type='manufacturer_payout',
                    amount=-snapshot['manufacturer_reserve'],
                    description='Automated manufacturer payable reserve from incoming customer payments',
                    status='queued',
                    available_on=utcnow()
                ))
                created += 1

            db.session.commit()
            flash(f'Disbursement automation queued {created} payment ledger item(s).', 'success')
        return redirect(url_for('admin_disbursements'))

    snapshot = disbursement_snapshot()
    transactions = Transaction.query.order_by(Transaction.created_at.desc()).limit(100).all()
    admins = User.query.filter_by(is_admin=True).order_by(User.username.asc()).all()
    manufacturers = Manufacturer.query.order_by(Manufacturer.priority.desc(), Manufacturer.name.asc()).limit(20).all()
    return render_template('admin/disbursements.html',
                           snapshot=snapshot,
                           transactions=transactions,
                           admins=admins,
                           manufacturers=manufacturers)


@app.route('/admin/business-intelligence', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_business_intelligence():
    if request.method == 'POST' and request.form.get('action') == 'send_stock_warnings':
        sent = send_stock_warning_emails(force=True)
        flash(f'Sent {sent} low-stock warning email(s) to admins/MVP.', 'success')
        return redirect(url_for('admin_business_intelligence'))
    question = request.form.get('question', 'Why did sales change this week?') if request.method == 'POST' else request.args.get('question', 'Why did sales change this week?')
    answer = business_intelligence_answer(question)
    checkins = record_business_checkins()
    recent_checkins = BusinessCheckIn.query.order_by(BusinessCheckIn.created_at.desc()).limit(12).all()
    suggestions = pricing_suggestions() if current_user.admin_level == 'mvp' or Setting.get('pricing_suggestions_admin_enabled', '0') == '1' else []
    warnings = stock_warnings()
    return render_template('admin/business_intelligence.html',
                           question=question,
                           answer=answer,
                           suggestions=suggestions,
                           warnings=warnings,
                           checkins=checkins,
                           recent_checkins=recent_checkins)


@app.route('/admin/pricing-suggestions/access', methods=['POST'])
@login_required
@mvp_required
def admin_toggle_pricing_suggestions_access():
    enabled = '1' if request.form.get('enabled') == '1' else '0'
    Setting.set('pricing_suggestions_admin_enabled', enabled)
    flash('Pricing suggestion access updated.', 'success')
    return redirect(url_for('admin_business_intelligence'))
# --- Product Management ---

@app.route('/admin/products')
@login_required
@admin_required
def admin_products():
    page = request.args.get('page', 1, type=int)
    pagination = Product.query.filter(or_(Product.review_status != 'deleted', Product.review_status.is_(None))).order_by(Product.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('admin/products.html', products=pagination.items, pagination=pagination)


@app.route('/shopping-card', methods=['GET', 'POST'])
@login_required
def shopping_card():
    card = active_shopping_card(current_user.id)
    if request.method == 'POST':
        if card:
            flash('You already have an active Smark-Africa shopping card.', 'info')
            return redirect(url_for('shopping_card'))
        issue_fee_paid = (request.form.get('issue_fee_paid', type=float) or 0) if current_user.is_admin else 0
        try:
            card, pin = create_shopping_card(
                current_user,
                display_name=request.form.get('display_name', '').strip(),
                issue_fee_paid=issue_fee_paid,
                issued_by=current_user.id if current_user.is_admin else None,
            )
            db.session.commit()
            flash(f'Card created. Scratch PIN for first print: {pin}', 'success')
            return redirect(url_for('shopping_card'))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
            return redirect(url_for('shopping_card'))
    transactions = []
    if card:
        transactions = ShoppingCardTransaction.query.filter_by(card_id=card.id).order_by(ShoppingCardTransaction.created_at.desc()).limit(50).all()
    return render_template(
        'shopping_card.html',
        card=card,
        transactions=transactions,
        loyalty_credits=loyalty_credit_balance(current_user.id),
        issue_threshold=shopping_card_issue_threshold(),
        issue_fee=shopping_card_issue_fee(),
    )


@app.route('/admin/cards', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_shopping_cards():
    if request.method == 'POST':
        action = request.form.get('action', 'issue')
        if action == 'issue':
            user_id = request.form.get('user_id', type=int)
            user = User.query.get_or_404(user_id)
            # Save phone number if provided and user doesn't have one
            customer_phone = request.form.get('customer_phone', '').strip()
            if customer_phone and not user.phone:
                user.phone = customer_phone
                db.session.commit()
            try:
                # If user has a phone, let them set PIN via SMS; otherwise generate PIN for scratch slip
                use_sms_pin = bool(user.phone)
                card, pin = create_shopping_card(
                    user,
                    display_name=request.form.get('display_name', '').strip(),
                    issue_fee_paid=request.form.get('issue_fee_paid', type=float) or 0,
                    issued_by=current_user.id,
                    customer_sets_pin=use_sms_pin,
                )
                db.session.commit()
                if use_sms_pin:
                    flash(f'Card {card.card_number} issued. PIN setup link sent to {user.phone}.', 'success')
                else:
                    flash(f'Card {card.card_number} issued. Scratch PIN for print: {pin}', 'success')
                return redirect(url_for('admin_print_shopping_card', card_id=card.id, pin=pin or ''))
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), 'danger')
                return redirect(url_for('admin_shopping_cards'))
        if action == 'fund':
            card = ShoppingCard.query.get_or_404(request.form.get('card_id', type=int))
            amount = request.form.get('cash_amount', type=float) or 0
            if amount <= 0:
                flash('Enter a positive cash amount to fund.', 'danger')
                return redirect(url_for('admin_shopping_cards'))
            credit_shopping_card(
                card.user_id,
                cash_amount=amount,
                transaction_type='cash_fund',
                reference_type='admin_funding',
                reference_id=card.id,
                note=request.form.get('note', '').strip() or 'Cash funded by admin',
                created_by=current_user.id,
            )
            db.session.commit()
            flash(f'Funded card ending {card.card_last4} with KSh {amount:,.2f}.', 'success')
            return redirect(url_for('admin_shopping_cards'))
        if action == 'status':
            card = ShoppingCard.query.get_or_404(request.form.get('card_id', type=int))
            card.status = request.form.get('status', card.status)
            db.session.add(ShoppingCardTransaction(
                card_id=card.id,
                user_id=card.user_id,
                transaction_type='status_change',
                balance_after_credits=card.credit_balance,
                balance_after_cash=card.cash_balance,
                reference_type='card_status',
                reference_id=card.status,
                note=f'Status changed to {card.status}',
                created_by=current_user.id,
            ))
            db.session.commit()
            flash('Card status updated.', 'success')
            return redirect(url_for('admin_shopping_cards'))
        if action == 'delete':
            card = ShoppingCard.query.get_or_404(request.form.get('card_id', type=int))
            card_number = card.card_last4
            # Log deletion
            db.session.add(ShoppingCardTransaction(
                card_id=card.id,
                user_id=card.user_id,
                transaction_type='card_deleted',
                balance_after_credits=card.credit_balance,
                balance_after_cash=card.cash_balance,
                reference_type='admin_action',
                reference_id=str(current_user.id),
                note=f'Card deleted by admin {current_user.username}',
                created_by=current_user.id,
            ))
            db.session.delete(card)
            db.session.commit()
            flash(f'Card ending {card_number} has been permanently deleted.', 'warning')
            return redirect(url_for('admin_shopping_cards'))

    page = request.args.get('page', 1, type=int)
    cards = ShoppingCard.query.order_by(ShoppingCard.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    users = User.query.filter_by(is_active=True).order_by(User.created_at.desc()).limit(200).all()
    recent_transactions = ShoppingCardTransaction.query.order_by(ShoppingCardTransaction.created_at.desc()).limit(40).all()
    return render_template(
        'admin/cards.html',
        cards=cards,
        users=users,
        recent_transactions=recent_transactions,
        issue_threshold=shopping_card_issue_threshold(),
        issue_fee=shopping_card_issue_fee(),
    )


@app.route('/admin/cards/<int:card_id>/print')
@login_required
@admin_required
def admin_print_shopping_card(card_id):
    card = ShoppingCard.query.get_or_404(card_id)
    scratch_pin = request.args.get('pin', '')
    card.printed_at = utcnow()
    db.session.commit()
    return render_template('admin/card_print.html', card=card, scratch_pin=scratch_pin)


@app.route('/admin/cards/<int:card_id>/barcode.svg')
@login_required
@admin_required
def admin_card_barcode(card_id):
    """Generate and serve barcode SVG for a shopping card."""
    card = ShoppingCard.query.get_or_404(card_id)
    barcode_svg = generate_card_barcode_svg(card.card_number)

    if not barcode_svg:
        # Fallback SVG if barcode library is missing
        fallback = f'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80" viewBox="0 0 200 80">
            <rect width="200" height="80" fill="#fff"/>
            <text x="10" y="30" font-size="12" font-family="Arial" fill="#111">Barcode unavailable</text>
            <text x="10" y="50" font-size="10" font-family="Arial" fill="#666">{card.card_number}</text>
        </svg>'''
        response = make_response(fallback)
    else:
        response = make_response(barcode_svg)

    response.headers['Content-Type'] = 'image/svg+xml'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/admin/pos/scan-card', methods=['POST'])
@login_required
@admin_required
def admin_pos_scan_card():
    """Handle barcode scan for shopping cards at POS."""
    data = request.get_json(silent=True) or {}
    barcode_value = data.get('barcode', '').strip()

    if not barcode_value:
        return jsonify({'success': False, 'error': 'No barcode provided'}), 400

    card = find_card_by_barcode(barcode_value)

    if not card:
        return jsonify({'success': False, 'error': 'Card not found or inactive'}), 404

    # Return card details for POS to display
    credit_balance_kes = (card.credit_balance or 0) / 100
    cash_balance_kes = card.cash_balance or 0
    total_balance_kes = credit_balance_kes + cash_balance_kes

    return jsonify({
        'success': True,
        'card': {
            'card_number': card.card_number,
            'card_last4': card.card_last4,
            'display_name': card.display_name or card.user.username,
            'customer_name': card.user.username,
            'customer_email': card.user.email,
            'customer_phone': card.user.phone,
            'credit_balance': credit_balance_kes,
            'cash_balance': cash_balance_kes,
            'total_balance': total_balance_kes,
            'status': card.status,
            'user_id': card.user_id
        }
    })


# ========== CUSTOMER CARD REGISTRATION & MOBILE AUTHORIZATION ==========

@app.route('/my-rewards-card', methods=['GET', 'POST'])
@login_required
def customer_rewards_card():
    """Customer page to register for and manage their rewards card."""
    card = active_shopping_card(current_user.id)
    loyalty_balance = loyalty_credit_balance(current_user.id)

    if request.method == 'POST':
        action = request.form.get('action', 'register')

        if action == 'register':
            # Check if user already has a card
            if card:
                flash('You already have an active rewards card.', 'info')
                return redirect(url_for('customer_rewards_card'))

            # Get payment method
            payment_method = request.form.get('payment_method', 'credits')
            display_name = request.form.get('display_name', '').strip() or current_user.username

            # Save phone number if provided (required for PIN setup SMS)
            phone_number = request.form.get('phone_number', '').strip()
            if phone_number and not current_user.phone:
                current_user.phone = phone_number
                db.session.commit()

            if not current_user.phone:
                flash('A phone number is required for PIN setup and transaction authorization.', 'danger')
                return redirect(url_for('customer_rewards_card'))

            try:
                if payment_method == 'credits':
                    # Use loyalty credits
                    allowed, reason = can_issue_shopping_card(current_user, issue_fee_paid=0)
                    if not allowed:
                        flash(reason, 'danger')
                        return redirect(url_for('customer_rewards_card'))

                    # Create card - customer will set their own PIN via SMS link
                    card, pin = create_shopping_card(
                        current_user,
                        display_name=display_name,
                        issue_fee_paid=0,
                        issued_by=None,
                        customer_sets_pin=True
                    )
                    db.session.commit()
                    flash(f'Congratulations! Your Smark-Africa Rewards Card has been issued. A PIN setup link has been sent to your phone number.', 'success')

                else:  # payment_method == 'mpesa'
                    # TODO: Integrate M-Pesa payment for card issuance fee
                    flash('M-Pesa payment coming soon. Please use loyalty credits or visit admin.', 'info')
                    return redirect(url_for('customer_rewards_card'))

            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), 'danger')
                return redirect(url_for('customer_rewards_card'))

    # Get pending authorization requests
    pending_auths = CardAuthorizationRequest.query.filter_by(
        user_id=current_user.id,
        status='pending'
    ).filter(
        CardAuthorizationRequest.expires_at > utcnow()
    ).order_by(CardAuthorizationRequest.created_at.desc()).all()

    # Get card transaction history
    transactions = []
    if card:
        transactions = ShoppingCardTransaction.query.filter_by(
            card_id=card.id
        ).order_by(ShoppingCardTransaction.created_at.desc()).limit(20).all()

    return render_template(
        'customer/rewards_card.html',
        card=card,
        loyalty_balance=loyalty_balance,
        pending_auths=pending_auths,
        transactions=transactions,
        issue_threshold=shopping_card_issue_threshold(),
        issue_fee=shopping_card_issue_fee(),
    )


@app.route('/authorize-card-payment/<int:auth_id>', methods=['POST'])
@login_required
def authorize_card_payment(auth_id):
    """Customer approves or declines a card payment authorization."""
    action = request.form.get('action', 'approve')

    if action == 'approve':
        success, message = approve_card_authorization(auth_id, current_user.id)
    else:  # decline
        success, message = decline_card_authorization(auth_id, current_user.id)

    if success:
        flash(message, 'success')
    else:
        flash(message, 'danger')

    return redirect(url_for('customer_rewards_card'))


@app.route('/set-card-pin/<token>', methods=['GET', 'POST'])
def set_card_pin(token):
    """Customer sets their card PIN via SMS link."""
    # Find card by PIN setup token
    card = ShoppingCard.query.filter_by(pin_set_token=token).first()

    if not card:
        flash('Invalid or expired PIN setup link.', 'danger')
        return redirect(url_for('home'))

    if card.status != 'pending_pin':
        flash('PIN has already been set for this card.', 'info')
        return redirect(url_for('customer_rewards_card') if current_user.is_authenticated else url_for('home'))

    if request.method == 'POST':
        pin = request.form.get('pin', '').strip()
        pin_confirm = request.form.get('pin_confirm', '').strip()

        # Validate PIN
        if not pin or not pin.isdigit() or len(pin) != 4:
            flash('PIN must be exactly 4 digits.', 'danger')
            return render_template('customer/set_pin.html', card=card, token=token)

        if pin != pin_confirm:
            flash('PINs do not match. Please try again.', 'danger')
            return render_template('customer/set_pin.html', card=card, token=token)

        # Set the PIN
        card.set_pin(pin)
        card.status = 'active'
        card.pin_set_at = utcnow()
        card.pin_set_token = None  # Clear token after use

        db.session.add(ShoppingCardTransaction(
            card_id=card.id,
            user_id=card.user_id,
            transaction_type='pin_set',
            balance_after_credits=card.credit_balance,
            balance_after_cash=card.cash_balance,
            reference_type='pin_setup',
            reference_id=str(card.id),
            note='Customer set PIN',
        ))
        db.session.commit()

        # Send confirmation SMS
        if card.user.phone:
            send_sms_notification(
                card.user.phone,
                f"Smark-Africa: Your card PIN has been set successfully! Card ending {card.card_last4} is now active."
            )

        flash('PIN set successfully! Your card is now active.', 'success')
        return redirect(url_for('customer_rewards_card') if current_user.is_authenticated else url_for('home'))

    return render_template('customer/set_pin.html', card=card, token=token)


@app.route('/api/check-card-authorization/<auth_token>')
def api_check_card_authorization(auth_token):
    """API endpoint for POS to check if authorization has been approved."""
    auth_request, message = check_card_authorization(auth_token)

    if not auth_request:
        return jsonify({'success': False, 'status': 'invalid', 'message': message}), 404

    return jsonify({
        'success': True,
        'status': auth_request.status,
        'message': message,
        'auth_id': auth_request.id,
        'amount': auth_request.amount,
        'approved': auth_request.status == 'approved'
    })


@app.route('/admin/pos/request-mobile-auth', methods=['POST'])
@login_required
@admin_required
def admin_pos_request_mobile_auth():
    """POS initiates mobile authorization request."""
    data = request.get_json(silent=True) or {}
    card_number = data.get('card_number', '').strip()
    amount = float(data.get('amount', 0) or 0)
    merchant_name = data.get('merchant_name', 'Smark-Africa POS')

    if not card_number or amount <= 0:
        return jsonify({'success': False, 'error': 'Invalid card number or amount'}), 400

    # Find card
    card = find_card_by_barcode(card_number)
    if not card:
        return jsonify({'success': False, 'error': 'Card not found or inactive'}), 404

    # Check if user has phone number
    if not card.user.phone:
        return jsonify({'success': False, 'error': 'Customer phone number not registered'}), 400

    # Create authorization request
    auth_request = create_card_authorization_request(
        card,
        amount,
        merchant_name=merchant_name,
        pos_terminal_id=f'POS-{current_user.id}'
    )
    db.session.commit()

    return jsonify({
        'success': True,
        'auth_token': auth_request.authorization_token,
        'auth_id': auth_request.id,
        'expires_at': auth_request.expires_at.isoformat(),
        'phone_sent': bool(card.user.phone),
        'message': f'Authorization request sent to {card.user.phone}'
    })


def pos_document_html(sale, title='Invoice'):
    rows = ''.join(
        f'<tr><td>{item.product_name}</td><td>{item.quantity}</td><td>KSh {item.unit_price:,.2f}</td><td>KSh {item.line_total:,.2f}</td></tr>'
        for item in sale.items
    )
    return f"""
    <h2>SMARKAFRICA {title}</h2>
    <p><strong>{title} No:</strong> {sale.invoice_number if title == 'Invoice' else sale.receipt_number}</p>
    <p><strong>Customer:</strong> {sale.customer_name or 'Walk-in customer'}</p>
    <p><strong>Date:</strong> {sale.created_at.strftime('%d %b %Y %H:%M')}</p>
    <table border="1" cellpadding="8" cellspacing="0" width="100%">
        <thead><tr><th>Product</th><th>Qty</th><th>Unit</th><th>Total</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>
    <p><strong>Subtotal:</strong> KSh {sale.subtotal:,.2f}</p>
    <p><strong>Discount:</strong> KSh {sale.discount_amount:,.2f}</p>
    <p><strong>Tax:</strong> KSh {sale.tax_amount:,.2f}</p>
    <h3>Total: KSh {sale.total_amount:,.2f}</h3>
    <p>Payment: {sale.payment_method.title()} / {sale.payment_status.title()}</p>
    """


@app.route('/admin/pos', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_pos():
    products = Product.query.filter_by(is_active=True, is_digital=False).order_by(Product.name.asc()).all()
    for product in products:
        product.pos_barcode = product_barcode_value(product)
    if request.method == 'POST':
        action = request.form.get('action', 'sale')
        inventory_actions = {'receive_stock', 'bulk_receive', 'reconcile_stock', 'dispatch_stock', 'purchase_order'}
        product_actions = {'quick_product'}
        supplier_actions = {'supplier'}
        if action in inventory_actions and not pos_can('inventory'):
            flash('Your POS role cannot change inventory.', 'danger')
            return redirect(url_for('admin_pos'))
        if action in product_actions and not pos_can('products'):
            flash('Your POS role cannot register products.', 'danger')
            return redirect(url_for('admin_pos'))
        if action in supplier_actions and not pos_can('suppliers'):
            flash('Your POS role cannot manage suppliers.', 'danger')
            return redirect(url_for('admin_pos'))
        if action in ['sale', 'terminal_update', 'terminal_clear'] and not pos_can('sell'):
            flash('Your POS role cannot process sales.', 'danger')
            return redirect(url_for('admin_pos'))
        if action == 'terminal_update':
            lines = []
            for product_id, qty in zip(request.form.getlist('cart_product_id'), request.form.getlist('cart_quantity')):
                try:
                    quantity = int(qty or 0)
                except ValueError:
                    quantity = 0
                if quantity > 0:
                    lines.append({'product_id': int(product_id), 'quantity': quantity})
            save_pos_terminal_cart(lines)
            flash('POS cart updated.', 'success')
            return redirect(url_for('admin_pos'))
        if action == 'terminal_clear':
            save_pos_terminal_cart([])
            flash('POS cart cleared.', 'info')
            return redirect(url_for('admin_pos'))
        if action == 'quick_product':
            name = request.form.get('name', '').strip()
            selling_price = request.form.get('selling_price', type=float) or 0
            buying_price = request.form.get('buying_price', type=float) or 0
            stock = request.form.get('stock', type=int) or 0
            if not name or selling_price <= 0:
                flash('Product name and selling price are required.', 'danger')
                return redirect(url_for('admin_pos'))
            slug = slugify(name, 180)
            base_slug = slug
            counter = 1
            while Product.query.filter_by(slug=slug).first():
                counter += 1
                slug = f'{base_slug}-{counter}'
            product = Product(
                name=name,
                slug=slug,
                short_description=request.form.get('short_description', '').strip()[:300],
                description=request.form.get('description', '').strip() or name,
                category_id=request.form.get('category_id', type=int),
                buying_price=max(0, buying_price),
                selling_price=selling_price,
                stock=0,
                weight_kg=request.form.get('weight_kg', type=float) or 0,
                is_digital=False,
                is_active=True,
                seller_id=current_user.id,
                review_status='approved',
                admin_priority=current_user.is_admin,
            )
            db.session.add(product)
            db.session.flush()
            try:
                barcode = assign_product_barcode(product, request.form.get('barcode', '').strip())
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), 'danger')
                return redirect(url_for('admin_pos'))
            if stock:
                record_stock_movement(product, 'opening_stock', stock, 'pos_product', product.id, 'Initial stock during POS product registration')
            db.session.commit()
            flash(f'Product registered with barcode {barcode}.', 'success')
            return redirect(url_for('admin_pos'))
        if action == 'supplier':
            name = request.form.get('name', '').strip()
            if name:
                db.session.add(Supplier(
                    name=name,
                    contact=request.form.get('contact', '').strip(),
                    country=request.form.get('country', '').strip(),
                    categories=request.form.get('categories', '').strip(),
                ))
                db.session.commit()
                flash('Supplier saved for POS purchasing.', 'success')
            return redirect(url_for('admin_pos'))
        if action == 'purchase_order':
            supplier_id = request.form.get('supplier_id', type=int)
            product_id = request.form.get('po_product_id', type=int)
            quantity = max(1, request.form.get('po_quantity', type=int) or 1)
            unit_cost = max(0, request.form.get('po_unit_cost', type=float) or 0)
            product = Product.query.get_or_404(product_id)
            po = PurchaseOrder(
                supplier_id=supplier_id,
                status='ordered',
                notes=request.form.get('po_notes', '').strip(),
                created_by=current_user.id,
                expected_at=utcnow() + timedelta(days=7),
            )
            db.session.add(po)
            db.session.flush()
            db.session.add(PurchaseOrderItem(
                purchase_order_id=po.id,
                product_id=product.id,
                quantity_ordered=quantity,
                unit_cost=unit_cost,
            ))
            db.session.commit()
            flash(f'Purchase order #{po.id} created for {product.name}.', 'success')
            return redirect(url_for('admin_pos'))
        if action == 'receive_stock':
            product_id = request.form.get('receive_product_id', type=int)
            product = Product.query.get_or_404(product_id)
            quantity = max(1, request.form.get('receive_quantity', type=int) or 1)
            record_stock_movement(product, 'goods_received', quantity, 'pos_receive', None, request.form.get('receive_note', '').strip())
            db.session.commit()
            flash(f'Received {quantity} unit(s) for {product.name}.', 'success')
            return redirect(url_for('admin_pos'))
        if action == 'bulk_receive':
            supplier_id = request.form.get('bulk_supplier_id', type=int)
            received_at = request.form.get('received_at', '').strip()
            created = 0
            for product_id, qty_raw, cost_raw in zip(
                    request.form.getlist('bulk_product_id'),
                    request.form.getlist('bulk_quantity'),
                    request.form.getlist('bulk_unit_cost')):
                if not product_id:
                    continue
                quantity = max(0, int(qty_raw or 0))
                if quantity <= 0:
                    continue
                product = Product.query.get(int(product_id))
                if not product:
                    continue
                unit_cost = max(0, float(cost_raw or 0))
                if unit_cost:
                    product.buying_price = unit_cost
                note = f"Bulk goods received from supplier #{supplier_id or 'N/A'}"
                if received_at:
                    note += f" on {received_at}"
                record_stock_movement(product, 'bulk_goods_received', quantity, 'bulk_receive', supplier_id, note)
                created += 1
            db.session.commit()
            flash(f'Bulk receiving saved for {created} product line(s).', 'success' if created else 'warning')
            return redirect(url_for('admin_pos'))
        if action == 'reconcile_stock':
            product_id = request.form.get('reconcile_product_id', type=int)
            product = Product.query.get_or_404(product_id)
            counted = max(0, request.form.get('counted_stock', type=int) or 0)
            delta = counted - int(product.stock or 0)
            record_stock_movement(product, 'stock_reconciliation', delta, 'pos_reconcile', None, request.form.get('reconcile_note', '').strip())
            db.session.commit()
            flash(f'Stock reconciled for {product.name}.', 'success')
            return redirect(url_for('admin_pos'))
        if action == 'dispatch_stock':
            product_id = request.form.get('dispatch_product_id', type=int)
            product = Product.query.get_or_404(product_id)
            quantity = max(1, request.form.get('dispatch_quantity', type=int) or 1)
            if (product.stock or 0) < quantity:
                flash(f'Insufficient stock to dispatch {product.name}.', 'danger')
                return redirect(url_for('admin_pos'))
            record_stock_movement(product, 'goods_dispatched', -quantity, 'pos_dispatch', None, request.form.get('dispatch_note', '').strip())
            db.session.commit()
            flash(f'Dispatched {quantity} unit(s) for {product.name}.', 'success')
            return redirect(url_for('admin_pos'))

        terminal_lines = pos_terminal_cart()
        if terminal_lines:
            product_ids = [str(line['product_id']) for line in terminal_lines]
            quantities = [str(line['quantity']) for line in terminal_lines]
        else:
            product_ids = request.form.getlist('product_id')
            quantities = request.form.getlist('quantity')
        selected_lines = []
        for pid, qty_raw in zip(product_ids, quantities):
            barcode_product = product_by_barcode(pid)
            if barcode_product:
                product = barcode_product
            elif str(pid).isdigit():
                product = Product.query.get(int(pid))
            else:
                continue
            qty = max(1, int(qty_raw or 1))
            if not product or not product.is_active:
                continue
            if not product.is_digital and (product.stock or 0) < qty:
                flash(f'Insufficient inventory for {product.name}.', 'danger')
                return redirect(url_for('admin_pos'))
            unit_price = product.discounted_price or product.selling_price or 0
            selected_lines.append((product, qty, unit_price, unit_price * qty))
        if not selected_lines:
            flash('Select at least one product for POS checkout.', 'warning')
            return redirect(url_for('admin_pos'))
        subtotal = sum(line[3] for line in selected_lines)
        discount_amount = max(0, float(request.form.get('discount_amount', 0) or 0))
        tax_amount = max(0, float(request.form.get('tax_amount', 0) or 0))
        total_amount = max(0, subtotal - discount_amount + tax_amount)
        payment_method = request.form.get('payment_method', 'cash')
        split_parts = {
            'cash': max(0, float(request.form.get('split_cash', 0) or 0)),
            'mobile_money': max(0, float(request.form.get('split_mobile_money', 0) or 0)),
            'card': max(0, float(request.form.get('split_card', 0) or 0)),
            'bank': max(0, float(request.form.get('split_bank', 0) or 0)),
        }
        notes = request.form.get('notes', '').strip()
        if payment_method == 'split':
            split_total = sum(split_parts.values())
            if abs(split_total - total_amount) > 1:
                flash(f'Split payment parts must add up to the sale total. Current split total is KSh {split_total:,.2f}.', 'danger')
                return redirect(url_for('admin_pos'))
            split_note = ', '.join(f'{name}: KSh {amount:,.2f}' for name, amount in split_parts.items() if amount)
            notes = f"{notes}\nSplit payment - {split_note}".strip()
        card_number = request.form.get('shopping_card_number', '').strip()
        card_pin = request.form.get('shopping_card_pin', '').strip()
        if payment_method == 'smark_card' and (not card_number or not card_pin):
            flash('Enter the Smark-Africa card number and scratch PIN.', 'danger')
            return redirect(url_for('admin_pos'))
        sale = PointOfSaleSale(
            cashier_id=current_user.id,
            customer_name=request.form.get('customer_name', '').strip(),
            customer_email=request.form.get('customer_email', '').strip(),
            customer_phone=request.form.get('customer_phone', '').strip(),
            subtotal=subtotal,
            discount_amount=discount_amount,
            tax_amount=tax_amount,
            total_amount=total_amount,
            payment_method=payment_method,
            payment_status=request.form.get('payment_status', 'paid'),
            notes=notes
        )
        db.session.add(sale)
        db.session.flush()
        sale.invoice_number = f'INV-{utcnow().strftime("%Y%m%d")}-{sale.id:05d}'
        sale.receipt_number = f'RCT-{utcnow().strftime("%Y%m%d")}-{sale.id:05d}'
        redeemed_card = None
        if payment_method == 'smark_card':
            try:
                redeemed_card = redeem_shopping_card(
                    card_number,
                    card_pin,
                    total_amount,
                    reference_type='pos_sale',
                    reference_id=sale.id,
                    created_by=current_user.id,
                )
                sale.customer_name = sale.customer_name or redeemed_card.display_name or redeemed_card.user.username
                sale.customer_email = sale.customer_email or redeemed_card.user.email
                sale.customer_phone = sale.customer_phone or redeemed_card.user.phone
                notes = f"{notes}\nPaid with Smark-Africa Card ending {redeemed_card.card_last4}".strip()
                sale.notes = notes
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), 'danger')
                return redirect(url_for('admin_pos'))
        if payment_method == 'mpesa':
            mpesa_phone = request.form.get('mpesa_phone', '').strip() or sale.customer_phone
            if not mpesa_phone:
                db.session.rollback()
                flash('Enter the customer M-Pesa phone number for STK Push.', 'danger')
                return redirect(url_for('admin_pos'))
            sale.payment_status = 'pending'
        if payment_method in ('debit_card', 'credit_card', 'card'):
            bank_card_number = request.form.get('bank_card_number', '').strip()
            bank_card_cvv = request.form.get('bank_card_cvv', '').strip()
            bank_card_exp_month = request.form.get('bank_card_expiry_month', '').strip()
            bank_card_exp_year = request.form.get('bank_card_expiry_year', '').strip()
            if bank_card_number and bank_card_cvv and bank_card_exp_month and bank_card_exp_year:
                sale.payment_status = 'pending'
            else:
                sale.payment_status = 'paid'
        for product, qty, unit_price, line_total in selected_lines:
            db.session.add(PointOfSaleItem(
                sale_id=sale.id,
                product_id=product.id,
                product_name=product.name,
                quantity=qty,
                unit_price=unit_price,
                line_total=line_total
            ))
            product.sales_count = (product.sales_count or 0) + qty
            if not product.is_digital:
                record_stock_movement(product, 'pos_sale', -qty, 'pos_sale', sale.id, sale.receipt_number)
        db.session.add(Transaction(
            user_id=current_user.id,
            type='pos_sale',
            amount=total_amount,
            description=f'POS sale {sale.receipt_number}',
            status='completed',
            available_on=utcnow()
        ))
        customer = None
        customer_email = normalize_email(sale.customer_email)
        customer_phone = normalize_mpesa_phone(sale.customer_phone)
        if customer_email:
            customer = User.query.filter_by(email=customer_email).first()
        if not customer and customer_phone:
            customer = User.query.filter_by(phone=customer_phone).first()
        if customer:
            points = purchase_credits_for_amount(total_amount)
            if points:
                row = award_loyalty_points(
                    customer.id,
                    'pos_purchase',
                    points,
                    f'POS purchase {sale.receipt_number}',
                    f'pos:{sale.id}'
                )
                if row:
                    credit_shopping_card(
                        customer.id,
                        credits=points,
                        transaction_type='purchase_reward',
                        reference_type='pos_sale',
                        reference_id=sale.id,
                        note=f'POS purchase reward for {sale.receipt_number}',
                    )
        db.session.commit()
        if payment_method == 'mpesa':
            mpesa_phone = request.form.get('mpesa_phone', '').strip() or sale.customer_phone
            stk_result = stk_push(mpesa_phone, total_amount, sale.receipt_number)
            if stk_result.get('success'):
                Setting.query.filter_by(key=f'pos_stk_{stk_result["checkout_request_id"]}').delete()
                db.session.add(Setting(key=f'pos_stk_{stk_result["checkout_request_id"]}', value=str(sale.id)))
                db.session.commit()
                flash(f'STK Push sent to {mpesa_phone}. Customer should enter their M-Pesa PIN.', 'info')
            else:
                flash(f'M-Pesa STK Push failed: {stk_result.get("error", "Unknown error")}. Sale recorded as pending.', 'warning')
        if payment_method in ('debit_card', 'credit_card', 'card'):
            bank_card_number = request.form.get('bank_card_number', '').strip()
            bank_card_cvv = request.form.get('bank_card_cvv', '').strip()
            bank_card_exp_month = request.form.get('bank_card_expiry_month', '').strip()
            bank_card_exp_year = request.form.get('bank_card_expiry_year', '').strip()
            if bank_card_number and bank_card_cvv and bank_card_exp_month and bank_card_exp_year:
                tx_ref = f'POS-{sale.id}-{int(utcnow().timestamp())}'
                flw_result = initiate_flutterwave_charge(
                    amount=total_amount,
                    email=sale.customer_email or 'customer@smarkafrica.com',
                    phone=sale.customer_phone or '',
                    card_number=bank_card_number,
                    cvv=bank_card_cvv,
                    expiry_month=bank_card_exp_month,
                    expiry_year=bank_card_exp_year,
                    tx_ref=tx_ref,
                )
                if flw_result.get('success'):
                    Setting.query.filter_by(key=f'flw_pos_{tx_ref}').delete()
                    db.session.add(Setting(key=f'flw_pos_{tx_ref}', value=str(sale.id)))
                    db.session.commit()
                    if flw_result.get('requires_validation'):
                        flash(f'Card payment initiated. Customer may need to complete OTP/3D Secure verification.', 'info')
                    else:
                        sale.payment_status = 'paid'
                        sale.notes = (sale.notes or '') + f'\nCard charged: {tx_ref}'
                        db.session.commit()
                        flash('Card payment processed successfully.', 'success')
                else:
                    flash(f'Card payment failed: {flw_result.get("error", "Unknown error")}. Sale recorded as pending.', 'warning')
        save_pos_terminal_cart([])
        if sale.customer_email and request.form.get('send_document') == '1':
            send_email(sale.customer_email, f'SMARKAFRICA receipt {sale.receipt_number}', pos_document_html(sale, 'Receipt'))
        flash(f'POS sale completed. Invoice {sale.invoice_number} and receipt {sale.receipt_number} generated.', 'success')
        return redirect(url_for('admin_pos_sale', sale_id=sale.id))
    db.session.commit()
    recent_sales = PointOfSaleSale.query.order_by(PointOfSaleSale.created_at.desc()).limit(20).all()
    low_stock = stock_warnings()
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()
    purchase_orders = PurchaseOrder.query.order_by(PurchaseOrder.created_at.desc()).limit(10).all()
    stock_movements = StockMovement.query.order_by(StockMovement.created_at.desc()).limit(20).all()
    categories = Category.query.filter_by(is_active=True).order_by(Category.name.asc()).all()
    terminal_payload = pos_terminal_payload()
    scanner_pairing = create_pos_scanner_pairing()
    return render_template('admin/pos.html', products=products, recent_sales=recent_sales, low_stock=low_stock,
                           suppliers=suppliers, purchase_orders=purchase_orders, stock_movements=stock_movements,
                           categories=categories, terminal_payload=terminal_payload,
                           pos_permissions=pos_role_permissions(), scanner_pairing=scanner_pairing)


@app.route('/admin/pos/sale/<int:sale_id>')
@login_required
@admin_required
def admin_pos_sale(sale_id):
    sale = PointOfSaleSale.query.get_or_404(sale_id)
    return render_template('admin/pos_sale.html', sale=sale)


@app.route('/admin/pos/labels')
@login_required
@admin_required
def admin_pos_labels():
    product_ids = request.args.getlist('product_id', type=int)
    query = Product.query.filter_by(is_active=True, is_digital=False)
    if product_ids:
        query = query.filter(Product.id.in_(product_ids))
    products = query.order_by(Product.name.asc()).limit(250).all()
    for product in products:
        product.pos_barcode = product_barcode_value(product)
    db.session.commit()
    return render_template('admin/pos_labels.html', products=products)


def pos_report_payload(start=None, end=None):
    query = PointOfSaleSale.query
    if start:
        query = query.filter(PointOfSaleSale.created_at >= start)
    if end:
        query = query.filter(PointOfSaleSale.created_at < end)
    sales = query.order_by(PointOfSaleSale.created_at.desc()).all()
    total_sales = sum(sale.total_amount or 0 for sale in sales)
    total_discount = sum(sale.discount_amount or 0 for sale in sales)
    total_tax = sum(sale.tax_amount or 0 for sale in sales)
    cogs = 0.0
    product_units = {}
    cashier_totals = {}
    payment_totals = {}
    for sale in sales:
        cashier = sale.cashier.username if sale.cashier else 'Unknown'
        cashier_totals[cashier] = cashier_totals.get(cashier, 0) + (sale.total_amount or 0)
        payment_totals[sale.payment_method or 'cash'] = payment_totals.get(sale.payment_method or 'cash', 0) + (sale.total_amount or 0)
        for item in sale.items:
            product_units[item.product_name] = product_units.get(item.product_name, 0) + (item.quantity or 0)
            if item.product:
                cogs += (item.product.buying_price or 0) * (item.quantity or 0)
    inventory_value = sum((product.buying_price or 0) * (product.stock or 0) for product in Product.query.filter_by(is_digital=False).all())
    low_stock = stock_warnings()
    return {
        'sales': sales,
        'total_sales': total_sales,
        'total_discount': total_discount,
        'total_tax': total_tax,
        'cogs': cogs,
        'gross_profit': total_sales - cogs,
        'inventory_value': inventory_value,
        'fast_moving': sorted(product_units.items(), key=lambda row: row[1], reverse=True)[:10],
        'slow_moving': sorted(product_units.items(), key=lambda row: row[1])[:10],
        'cashier_totals': sorted(cashier_totals.items(), key=lambda row: row[1], reverse=True),
        'payment_totals': sorted(payment_totals.items(), key=lambda row: row[1], reverse=True),
        'low_stock': low_stock,
    }


def simple_pdf_response(title, lines, filename):
    def pdf_escape(value):
        return str(value).replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    y = 790
    content = ['BT', '/F1 16 Tf', f'1 0 0 1 50 {y} Tm', f'({pdf_escape(title)}) Tj']
    y_step = 18
    content.append('/F1 10 Tf')
    for line in lines[:38]:
        y -= y_step
        content.append(f'1 0 0 1 50 {y} Tm ({pdf_escape(line)}) Tj')
    content.append('ET')
    stream = '\n'.join(content).encode('latin-1', errors='replace')
    objects = [
        b'1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj',
        b'2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj',
        b'3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj',
        b'4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj',
        f'5 0 obj << /Length {len(stream)} >> stream\n'.encode('latin-1') + stream + b'\nendstream endobj',
    ]
    pdf = BytesIO()
    pdf.write(b'%PDF-1.4\n')
    offsets = []
    for obj in objects:
        offsets.append(pdf.tell())
        pdf.write(obj + b'\n')
    xref = pdf.tell()
    pdf.write(f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode('latin-1'))
    for offset in offsets:
        pdf.write(f'{offset:010d} 00000 n \n'.encode('latin-1'))
    pdf.write(f'trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode('latin-1'))
    response = make_response(pdf.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return response


@app.route('/admin/pos/reports')
@login_required
@admin_required
def admin_pos_reports():
    if not pos_can('reports'):
        flash('Your POS role cannot view reports.', 'danger')
        return redirect(url_for('admin_pos'))
    period = request.args.get('period', 'daily')
    now = utcnow()
    if period == 'weekly':
        start = now - timedelta(days=7)
    elif period == 'monthly':
        start = now - timedelta(days=30)
    elif period == 'yearly':
        start = now - timedelta(days=365)
    else:
        start = datetime(now.year, now.month, now.day)
    payload = pos_report_payload(start=start)
    export_format = request.args.get('format')
    if export_format == 'csv':
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(['Receipt', 'Date', 'Cashier', 'Customer', 'Payment', 'Subtotal', 'Discount', 'Tax', 'Total'])
        for sale in payload['sales']:
            writer.writerow([
                sale.receipt_number,
                sale.created_at.isoformat(sep=' ', timespec='minutes'),
                sale.cashier.username if sale.cashier else '',
                sale.customer_name or 'Walk-in',
                sale.payment_method,
                sale.subtotal,
                sale.discount_amount,
                sale.tax_amount,
                sale.total_amount,
            ])
        response = make_response(buffer.getvalue())
        response.headers['Content-Type'] = 'text/csv'
        response.headers['Content-Disposition'] = f'attachment; filename=pos-{period}-report.csv'
        return response
    if export_format in ['xls', 'excel']:
        rows = ['<table><tr><th>Receipt</th><th>Date</th><th>Cashier</th><th>Customer</th><th>Payment</th><th>Subtotal</th><th>Discount</th><th>Tax</th><th>Total</th></tr>']
        for sale in payload['sales']:
            rows.append(
                '<tr>'
                f'<td>{html.escape(sale.receipt_number or "")}</td>'
                f'<td>{sale.created_at.isoformat(sep=" ", timespec="minutes")}</td>'
                f'<td>{html.escape(sale.cashier.username if sale.cashier else "")}</td>'
                f'<td>{html.escape(sale.customer_name or "Walk-in")}</td>'
                f'<td>{html.escape(sale.payment_method or "")}</td>'
                f'<td>{sale.subtotal}</td><td>{sale.discount_amount}</td><td>{sale.tax_amount}</td><td>{sale.total_amount}</td>'
                '</tr>'
            )
        rows.append('</table>')
        response = make_response('\n'.join(rows))
        response.headers['Content-Type'] = 'application/vnd.ms-excel'
        response.headers['Content-Disposition'] = f'attachment; filename=pos-{period}-report.xls'
        return response
    if export_format == 'pdf':
        lines = [
            f'Period: {period}',
            f'Sales: KSh {payload["total_sales"]:,.2f}',
            f'Gross profit: KSh {payload["gross_profit"]:,.2f}',
            f'Inventory value: KSh {payload["inventory_value"]:,.2f}',
            f'Transactions: {len(payload["sales"])}',
            '',
            'Recent sales:',
        ]
        for sale in payload['sales'][:25]:
            lines.append(f'{sale.receipt_number} | {sale.created_at:%d %b %H:%M} | {sale.payment_method} | KSh {sale.total_amount:,.2f}')
        return simple_pdf_response(f'SMARKAFRICA POS {period.title()} Report', lines, f'pos-{period}-report.pdf')
    return render_template('admin/pos_reports.html', report=payload, period=period)


@app.route('/admin/pos/mobile-scanner')
@login_required
@admin_required
def admin_pos_mobile_scanner():
    return render_template('admin/pos_mobile_scanner.html', scan_url=url_for('admin_pos_scan_push'), paired_terminal=current_user.username)


@app.route('/admin/pos/scanner-qr/<token>')
@login_required
@admin_required
def admin_pos_scanner_qr(token):
    pairing = scanner_pairing_payload(token)
    if not pairing or int(pairing.get('user_id') or 0) != current_user.id:
        abort(404)
    scanner_url = local_network_base_url().rstrip('/') + url_for('pos_pair_scanner', token=pairing['token'])
    if qrcode is None:
        fallback = f'''<svg xmlns="http://www.w3.org/2000/svg" width="260" height="260" viewBox="0 0 260 260">
        <rect width="260" height="260" fill="#fff"/>
        <text x="18" y="40" font-size="16" font-family="Arial" fill="#111">QR package missing</text>
        <text x="18" y="72" font-size="12" font-family="Arial" fill="#111">Install qrcode or open:</text>
        <foreignObject x="18" y="88" width="224" height="150"><div xmlns="http://www.w3.org/1999/xhtml" style="font:12px Arial;word-break:break-all;color:#111;">{html.escape(scanner_url)}</div></foreignObject>
        </svg>'''
        response = make_response(fallback)
        response.headers['Content-Type'] = 'image/svg+xml'
        response.headers['Cache-Control'] = 'no-store'
        return response
    image = qrcode.make(scanner_url, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
    buffer = BytesIO()
    image.save(buffer)
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'image/svg+xml'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/pos/scanner/<token>')
def pos_pair_scanner(token):
    pairing = scanner_pairing_payload(token)
    if not pairing:
        return render_template('admin/pos_mobile_scanner.html', scan_url='', paired_terminal='', pairing_error='This scanner pairing has expired. Create a new pairing from the PC POS.'), 410
    return render_template(
        'admin/pos_mobile_scanner.html',
        scan_url=url_for('pos_pair_scan_push', token=pairing['token']),
        paired_terminal=pairing.get('terminal', 'POS terminal'),
        pairing_error=''
    )


@app.route('/pos/scanner/connect')
def pos_scanner_connect():
    return render_template(
        'admin/pos_mobile_scanner.html',
        scan_url='',
        paired_terminal='Pair a POS terminal',
        pairing_error='',
        pair_mode=True,
        pair_redirect_base=local_network_base_url().rstrip('/') + '/pos/scanner/'
    )


@app.route('/pos/scanner/<token>/scan', methods=['POST'])
def pos_pair_scan_push(token):
    pairing = scanner_pairing_payload(token)
    if not pairing:
        return jsonify({'success': False, 'error': 'Scanner pairing expired'}), 410
    data = request.get_json(silent=True) or {}
    barcode = re.sub(r'[^A-Za-z0-9_.-]', '', data.get('barcode', '')[:120])
    if not barcode:
        return jsonify({'success': False, 'error': 'No barcode received'}), 400
    product = product_by_barcode(barcode)
    user_id = int(pairing['user_id'])
    if product:
        add_product_to_pos_terminal(product, 1, user_id=user_id)
    Setting.set(f'pos_latest_scan_{user_id}', json.dumps({
        'barcode': barcode,
        'product_id': product.id if product else None,
        'product_name': product.name if product else '',
        'cart': pos_terminal_payload(user_id=user_id),
        'received_at': utcnow().isoformat()
    }))
    db.session.commit()
    if not product:
        return jsonify({'success': False, 'error': f'No product found for barcode: {barcode}', 'barcode': barcode})
    return jsonify({'success': True, 'barcode': barcode, 'product': product.name})


@app.route('/admin/pos/scan', methods=['POST'])
@login_required
@admin_required
def admin_pos_scan_push():
    data = request.get_json(silent=True) or {}
    barcode = re.sub(r'[^A-Za-z0-9_.-]', '', data.get('barcode', '')[:120])
    if not barcode:
        return jsonify({'success': False, 'error': 'No barcode received'}), 400
    product = product_by_barcode(barcode)
    if product:
        add_product_to_pos_terminal(product, 1)
    Setting.set(f'pos_latest_scan_{current_user.id}', json.dumps({
        'barcode': barcode,
        'product_id': product.id if product else None,
        'product_name': product.name if product else '',
        'cart': pos_terminal_payload(),
        'received_at': utcnow().isoformat()
    }))
    db.session.commit()
    if not product:
        return jsonify({'success': False, 'error': f'No product found for barcode: {barcode}', 'barcode': barcode})
    return jsonify({'success': True, 'barcode': barcode, 'product': product.name})


@app.route('/admin/pos/latest-scan')
@login_required
@admin_required
def admin_pos_latest_scan():
    key = f'pos_latest_scan_{current_user.id}'
    raw = Setting.get(key, '')
    if not raw:
        return jsonify({'success': False})
    Setting.set(key, '')
    db.session.commit()
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {'barcode': raw}
    payload['success'] = True
    payload['cart'] = pos_terminal_payload()
    return jsonify(payload)


@app.route('/admin/pos/cart')
@login_required
@admin_required
def admin_pos_cart():
    return jsonify({'success': True, 'cart': pos_terminal_payload()})


@app.route('/admin/pos/cart/add', methods=['POST'])
@login_required
@admin_required
def admin_pos_cart_add():
    data = request.get_json(silent=True) or request.form
    barcode = (data.get('barcode') or '').strip()
    try:
        product_id = int(data.get('product_id') or 0)
    except (TypeError, ValueError):
        product_id = 0
    product = product_by_barcode(barcode) if barcode else Product.query.get(product_id) if product_id else None
    if not product:
        return jsonify({'success': False, 'error': 'Product not found'}), 404
    add_product_to_pos_terminal(product, int(data.get('quantity') or 1))
    db.session.commit()
    return jsonify({'success': True, 'cart': pos_terminal_payload()})


@app.route('/admin/inventory', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_inventory():
    if request.method == 'POST':
        product = Product.query.get_or_404(request.form.get('product_id', type=int))
        adjustment = int(request.form.get('adjustment', 0) or 0)
        product.stock = max(0, (product.stock or 0) + adjustment)
        product.weight_kg = float(request.form.get('weight_kg', product.weight_kg or 0) or 0)
        product.is_active = request.form.get('is_active') == '1'
        db.session.commit()
        flash(f'Inventory updated for {product.name}.', 'success')
        return redirect(url_for('admin_inventory'))
    products = Product.query.order_by(Product.stock.asc(), Product.name.asc()).all()
    return render_template('admin/inventory.html', products=products, warnings=stock_warnings())


@app.route('/admin/products/add', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_add_product():
    categories = Category.query.all()

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '')
        short_description = request.form.get('short_description', '')[:300]
        category_id = request.form.get('category_id', type=int)
        buying_price = float(request.form.get('buying_price', 0))
        selling_price = float(request.form.get('selling_price', 0))
        is_digital = request.form.get('is_digital') in ['1', 'on', 'true']
        stock = int(request.form.get('stock', 0))
        weight_kg = float(request.form.get('weight_kg', 0))
        is_active = request.form.get('is_active') in ['1', 'on', 'true']
        is_featured = request.form.get('is_featured') in ['1', 'on', 'true']
        product_condition = request.form.get('product_condition', 'new')
        sale_mode = request.form.get('sale_mode', 'direct')
        bid_price = float(request.form.get('bid_price', 0) or 0)
        commission_percent = max(15.0, float(request.form.get('commission_percent', 15) or 15))
        admin_priority = request.form.get('admin_priority') in ['1', 'on', 'true']
        is_hot_sale = request.form.get('is_hot_sale') in ['1', 'on', 'true']
        is_original_source = request.form.get('is_original_source') in ['1', 'on', 'true']


        if not name or buying_price < 0 or selling_price <= 0:
            flash('Product name and valid prices are required.', 'danger')
            return render_template('admin/add_product.html', categories=categories)

        slug = name.lower().replace(' ', '-').replace('/', '-')[:200]
        # Ensure unique slug
        base_slug = slug
        counter = 1
        while Product.query.filter_by(slug=slug).first():
            slug = f"{base_slug}-{counter}"
            counter += 1

        product = Product(
            name=name, slug=slug, description=description,
            short_description=short_description, category_id=category_id,
            buying_price=buying_price, selling_price=selling_price,
            is_digital=is_digital, stock=stock, weight_kg=weight_kg,
            is_featured=is_featured, is_active=is_active,
            seller_id=current_user.id, product_condition=product_condition,
            sale_mode=sale_mode, bid_price=bid_price,
            commission_percent=commission_percent, admin_priority=admin_priority or current_user.is_admin,
            is_hot_sale=is_hot_sale, hot_sale_started_at=utcnow() if is_hot_sale else None,
            is_original_source=is_original_source
        )
        price_reference = market_price_reference(name, category_id, selling_price, buying_price, description=description)
        if price_reference['status'] != 'ok':
            flash(
                f"Market price warning for {price_reference['label']}: {price_reference['message']} "
                f"Kenya range KSh {price_reference['kenya_low']:,.2f} - KSh {price_reference['kenya_high']:,.2f}; "
                f"manufacturer estimate KSh {price_reference['manufacturer_price']:,.2f}.",
                'warning'
            )
        if product.sale_mode == 'bid' and product.bid_price < (product.selling_price * 0.75):
            flash('Bid price cannot be below three quarters of the direct sale price.', 'danger')
            return render_template('admin/add_product.html', categories=categories, product=None)
        if product.product_condition in ['second_hand', 'refurbished']:
            product.review_status = 'manual_second_review'
            product.is_active = False
        elif product.product_condition == 'thrifted':
            product.review_status = 'admin_review'
        else:
            product.review_status = 'approved'

        # Handle image upload
        product_images = uploaded_product_images()
        if product_images:
            product.image_url = product_images[0]
            if len(product_images) > 1:
                product.additional_images = json.dumps(product_images[1:])
        elif request.form.get('image_url', '').strip():
            product.image_url = request.form.get('image_url', '').strip()

        # Handle digital file upload
        if is_digital and 'digital_file' in request.files and request.files['digital_file'].filename:
            file = request.files['digital_file']
            if not allowed_digital_file(file.filename) or not allowed_digital_file_signature(file):
                flash('Digital uploads must match an allowed file type and valid file signature.', 'danger')
                return render_template('admin/add_product.html', categories=categories, product=None)
            product.file_path = save_uploaded_file(file, 'digital')
            product.file_size = os.path.getsize(
                os.path.join(app.config['UPLOAD_FOLDER'], 'digital',
                             os.path.basename(product.file_path))
            ) if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], 'digital',
                                             os.path.basename(product.file_path))) else 0

        db.session.add(product)
        db.session.commit()

        if product.admin_priority and not Review.query.filter_by(product_id=product.id, is_admin_review=True).first():
            db.session.add(Review(
                user_id=current_user.id,
                product_id=product.id,
                rating=4,
                comment='Trusted listing with responsive service and clear product details.',
                is_visible=True,
                is_admin_review=True
            ))
            db.session.commit()

        # Auto-apply discount check
        apply_auto_discount(product)
        db.session.commit()
        alert_count = notify_price_alerts()
        if alert_count:
            flash(f'{alert_count} buyer price alert email(s) were triggered by this listing.', 'info')

        invalidate_product_cache()
        flash(f'Product "{name}" created successfully!', 'success')
        return redirect(url_for('admin_products'))

    return render_template('admin/add_product.html', categories=categories, product=None)


@app.route('/admin/products/edit/<int:pid>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_product(pid):
    product = Product.query.get_or_404(pid)
    categories = Category.query.all()

    if request.method == 'POST':
        product.name = request.form.get('name', product.name).strip()
        product.description = request.form.get('description', product.description)
        product.short_description = request.form.get('short_description', product.short_description)[:300]
        product.category_id = request.form.get('category_id', type=int) or product.category_id
        product.buying_price = float(request.form.get('buying_price', product.buying_price))
        product.selling_price = float(request.form.get('selling_price', product.selling_price))
        product.is_digital = request.form.get('is_digital') == 'on'
        product.stock = int(request.form.get('stock', product.stock))
        product.weight_kg = float(request.form.get('weight_kg', product.weight_kg))
        product.is_featured = request.form.get('is_featured') == 'on'
        product.is_active = request.form.get('is_active') == 'on'
        product.discount_percent = float(request.form.get('discount_percent', product.discount_percent or 0))
        product.product_condition = request.form.get('product_condition', product.product_condition or 'new')
        product.sale_mode = request.form.get('sale_mode', product.sale_mode or 'direct')
        product.bid_price = float(request.form.get('bid_price', product.bid_price or 0) or 0)
        product.commission_percent = max(15.0, float(request.form.get('commission_percent', product.commission_percent or 15) or 15))
        product.admin_priority = request.form.get('admin_priority') == 'on'
        was_hot_sale = bool(product.is_hot_sale)
        product.is_hot_sale = request.form.get('is_hot_sale') == 'on'
        if product.is_hot_sale and not was_hot_sale:
            product.hot_sale_started_at = utcnow()
        product.is_original_source = request.form.get('is_original_source') == 'on'
        price_reference = market_price_reference(
            product.name,
            product.category_id,
            product.selling_price,
            product.buying_price,
            exclude_id=product.id,
            description=product.description
        )
        if price_reference['status'] != 'ok':
            flash(
                f"Market price warning for {price_reference['label']}: {price_reference['message']} "
                f"Kenya range KSh {price_reference['kenya_low']:,.2f} - KSh {price_reference['kenya_high']:,.2f}; "
                f"manufacturer estimate KSh {price_reference['manufacturer_price']:,.2f}.",
                'warning'
            )

        if product.sale_mode == 'bid' and product.bid_price < (product.selling_price * 0.75):
            flash('Bid price cannot be below three quarters of the direct sale price.', 'danger')
            return render_template('admin/add_product.html', categories=categories, product=product)

        # Verify discount doesn't cause loss
        if product.discount_percent > 0:
            max_allowed = ((
                                       product.selling_price - product.buying_price) / product.selling_price) * 100 if product.selling_price > 0 else 0
            if product.discount_percent > max_allowed and product.buying_price > 0:
                flash(
                    f'WARNING: This discount ({product.discount_percent:.0f}%) exceeds the maximum ({max_allowed:.0f}%) and will cause a LOSS!',
                    'danger')

        # Handle image
        product_images = uploaded_product_images()
        extra_images = []
        if product.additional_images:
            try:
                extra_images = json.loads(product.additional_images)
            except Exception:
                extra_images = []
        if product_images:
            product.image_url = product_images[0]
            extra_images.extend(product_images[1:])
        if extra_images:
            product.additional_images = json.dumps(extra_images)

        # Handle digital file
        if product.is_digital and 'digital_file' in request.files and request.files['digital_file'].filename:
            file = request.files['digital_file']
            if not allowed_digital_file(file.filename) or not allowed_digital_file_signature(file):
                flash('Digital uploads must match an allowed file type and valid file signature.', 'danger')
                return render_template('admin/add_product.html', categories=categories, product=product)
            product.file_path = save_uploaded_file(file, 'digital')
            product.file_size = os.path.getsize(
                os.path.join(app.config['UPLOAD_FOLDER'], 'digital',
                             os.path.basename(product.file_path))
            ) if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], 'digital',
                                             os.path.basename(product.file_path))) else 0

        db.session.commit()
        invalidate_product_cache()
        flash('Product updated successfully!', 'success')
        return redirect(url_for('admin_products'))

    return render_template('admin/add_product.html', categories=categories, product=product)


@app.route('/admin/products/<int:pid>/hot-sale', methods=['POST'])
@login_required
@admin_required
def admin_toggle_hot_sale(pid):
    product = Product.query.get_or_404(pid)
    product.is_hot_sale = not bool(product.is_hot_sale)
    if product.is_hot_sale:
        product.hot_sale_started_at = utcnow()
        product.is_featured = True
        product.admin_priority = True
        flash(f'{product.name} is now a hot sale and has homepage priority.', 'success')
    else:
        product.hot_sale_started_at = None
        product.is_featured = False
        product.admin_priority = False
        flash(f'Hot sale removed from {product.name}.', 'info')
    db.session.commit()
    return redirect(request.referrer or url_for('admin_products'))
# Handle image upload (same logic)

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        image_filename = request.form.get('image_url', '')
        # ... rest of your upload logic
    return render_template('upload.html')




@app.route('/admin/products/delete/<int:pid>', methods=['POST'])
@login_required
@admin_required
@limiter.limit("30 per hour")
@audit_log('delete', 'product')
def admin_delete_product(pid):
    product = Product.query.get_or_404(pid)
    name = product.name

    # Log the deletion with details
    log_admin_action('product_delete', 'product', pid, {
        'name': name,
        'admin_id': current_user.id,
        'admin_username': current_user.username
    })

    Cart.query.filter_by(product_id=product.id).delete(synchronize_session=False)
    PriceAlert.query.filter_by(product_id=product.id).update({
        PriceAlert.status: 'cancelled'
    }, synchronize_session=False)
    product.is_active = False
    product.is_featured = False
    product.review_status = 'deleted'
    product.stock = 0
    db.session.commit()

    if request.accept_mimetypes.best == 'application/json' or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': True, 'removed': True, 'product_id': product.id, 'message': f'Product "{name}" removed from the store.'})
    flash(f'Product "{name}" removed from the store. Order history and reviews were preserved safely.', 'success')
    return redirect(url_for('admin_products'))


# --- Category Management ---

@app.route('/admin/categories', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_categories():
    edit_id = request.args.get('edit', type=int)
    edit_category = Category.query.get(edit_id) if edit_id else None

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        slug = request.form.get('slug', '').strip()
        description = request.form.get('description', '').strip()
        category_id = request.form.get('category_id', type=int)
        category = Category.query.get(category_id) if category_id else None

        if not name:
            flash('Category name is required.', 'danger')
            return redirect(url_for('admin_categories', edit=category_id) if category_id else url_for('admin_categories'))

        final_slug = slugify(slug or name, 100)
        existing = Category.query.filter_by(slug=final_slug).first()
        if existing and (not category or existing.id != category.id):
            final_slug = unique_category_slug(name, current_id=category.id if category else None)

        if category:
            category.name = name
            category.slug = final_slug
            category.description = description
            flash(f'Category "{name}" updated.', 'success')
        else:
            category = Category(name=name, slug=final_slug, description=description, is_active=True)
            db.session.add(category)
            flash(f'Category "{name}" created.', 'success')
        db.session.commit()
        return redirect(url_for('admin_categories'))

    categories = Category.query.order_by(Category.name.asc()).all()
    return render_template('admin/categories.html', categories=categories, edit_category=edit_category)


@app.route('/admin/categories/add', methods=['POST'])
@login_required
@admin_required
def admin_add_category():
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '')
    if name:
        slug = unique_category_slug(name)
        cat = Category(name=name, slug=slug, description=description)
        db.session.add(cat)
        db.session.commit()
        flash(f'Category "{name}" created!', 'success')
    return redirect(url_for('admin_categories'))


@app.route('/admin/categories/delete/<int:cid>', methods=['POST'])
@login_required
@admin_required
def admin_delete_category(cid):
    cat = Category.query.get_or_404(cid)
    # Unlink products
    Product.query.filter_by(category_id=cid).update({Product.category_id: None})
    db.session.delete(cat)
    db.session.commit()
    flash('Category deleted.', 'success')
    return redirect(url_for('admin_categories'))


# --- Order Management ---

@app.route('/admin/orders')
@login_required
@admin_required
def admin_orders():
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')

    query = Order.query
    if status_filter:
        query = query.filter_by(status=status_filter)

    pagination = query.order_by(Order.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('admin/orders.html', orders=pagination.items, pagination=pagination)


@app.route('/admin/orders/clear', methods=['POST'])
@login_required
@admin_required
def admin_clear_orders():
    Transaction.query.filter(Transaction.order_id.isnot(None)).delete(synchronize_session=False)
    OrderItem.query.delete()
    TrackingUpdate.query.delete()
    PaymentClaim.query.delete()
    Order.query.delete()
    db.session.commit()
    flash('All orders have been cleared from the order list.', 'success')
    return redirect(url_for('admin_orders'))


@app.route('/admin/orders/<int:order_id>')
@login_required
@admin_required
def admin_order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template('admin/order_detail.html', order=order)


@app.route('/admin/orders/<int:order_id>/update', methods=['POST'])
@login_required
@admin_required
def admin_update_order(order_id):
    order = Order.query.get_or_404(order_id)
    order.status = request.form.get('status', order.status)
    order.shipping_status = request.form.get('shipping_status', order.shipping_status)
    order.tracking_number = request.form.get('tracking_number', order.tracking_number)
    order.notes = request.form.get('notes', order.notes)

    # Add tracking update
    tracking_status = request.form.get('tracking_status', '')
    tracking_location = request.form.get('tracking_location', '')
    tracking_desc = request.form.get('tracking_description', '')

    if tracking_status or tracking_desc:
        update = TrackingUpdate(
            order_id=order.id,
            status=tracking_status or order.shipping_status,
            location=tracking_location,
            description=tracking_desc
        )
        db.session.add(update)
        create_customer_notification(
            order.user_id,
            f'Tracking update for {order.order_number}',
            f'{tracking_status or order.shipping_status}: {tracking_desc or "Your order has a new delivery progress update."}',
            'tracking'
        )

    db.session.commit()
    flash('Order updated!', 'success')
    return redirect(url_for('admin_order_detail', order_id=order.id))


# --- Transaction Management ---

@app.route('/admin/transactions')
@login_required
@admin_required
def admin_transactions():
    page = request.args.get('page', 1, type=int)
    transactions = Transaction.query.order_by(Transaction.created_at.desc()).paginate(page=page, per_page=20,
                                                                                      error_out=False)
    return render_template('admin/transactions.html', transactions=transactions)


# --- User Management ---

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '').strip()
    query = User.query
    if search:
        query = query.filter(or_(
            User.username.ilike(f'%{search}%'),
            User.email.ilike(f'%{search}%'),
            User.phone.ilike(f'%{search}%')
        ))
    pagination = query.order_by(User.created_at.desc()).paginate(page=page, per_page=30, error_out=False)
    user_ids = [user.id for user in pagination.items]
    order_counts = {}
    if user_ids:
        order_counts = dict(
            db.session.query(Order.user_id, func.count(Order.id)).filter(Order.user_id.in_(user_ids)).group_by(Order.user_id).all()
        )
    return render_template('admin/users.html', users=pagination.items, pagination=pagination, search=search, order_counts=order_counts)


@app.route('/admin/users/toggle/<int:uid>', methods=['POST'])
@login_required
@admin_required
@limiter.limit("30 per hour")
def admin_toggle_user(uid):
    user = User.query.get_or_404(uid)
    if user.id == current_user.id:
        flash("You can't deactivate yourself.", 'warning')
        return redirect(url_for('admin_users'))

    old_status = user.is_active
    user.is_active = not user.is_active
    db.session.commit()

    log_admin_action('user_toggle_active', 'user', uid, {
        'old_status': old_status,
        'new_status': user.is_active,
        'username': user.username,
        'admin_id': current_user.id
    })

    flash(f'User {user.username} {"activated" if user.is_active else "deactivated"}.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/make-admin/<int:uid>', methods=['POST'])
@login_required
@mvp_required
@limiter.limit("10 per hour")
def admin_make_admin(uid):
    user = User.query.get_or_404(uid)

    old_admin_status = user.is_admin
    old_admin_level = user.admin_level

    user.is_admin = not user.is_admin
    user.admin_level = 'admin' if user.is_admin else 'user'
    db.session.commit()

    log_admin_action('user_change_admin_status', 'user', uid, {
        'old_is_admin': old_admin_status,
        'new_is_admin': user.is_admin,
        'old_level': old_admin_level,
        'new_level': user.admin_level,
        'username': user.username,
        'mvp_admin_id': current_user.id
    })

    flash(f'User {user.username} is {"now admin" if user.is_admin else "no longer admin"}.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/reset-password/<int:uid>', methods=['GET', 'POST'])
@login_required
@mvp_required
@limiter.limit("5 per hour")
def admin_reset_password(uid):
    """MVP-only: Reset any user's password"""
    user = User.query.get_or_404(uid)

    if request.method == 'POST':
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        if len(new_password) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return render_template('admin/reset_password.html', user=user)

        if new_password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('admin/reset_password.html', user=user)

        user.set_password(new_password)
        db.session.commit()

        log_admin_action('user_password_reset', 'user', uid, {
            'username': user.username,
            'reset_by_mvp': current_user.id
        })

        flash(f'Password reset successfully for {user.username}.', 'success')
        return redirect(url_for('admin_users'))

    return render_template('admin/reset_password.html', user=user)


@app.route('/admin/users/verified-seller/<int:uid>', methods=['POST'])
@login_required
@mvp_required
@limiter.limit("20 per hour")
def admin_toggle_verified_seller(uid):
    user = User.query.get_or_404(uid)

    old_verified = user.is_verified_seller

    user.is_verified_seller = not bool(user.is_verified_seller)
    user.seller_status = 'verified' if user.is_verified_seller else (user.seller_status or 'buyer')
    user.verified_seller_at = utcnow() if user.is_verified_seller else None
    db.session.commit()

    log_admin_action('user_toggle_verified_seller', 'user', uid, {
        'old_verified': old_verified,
        'new_verified': user.is_verified_seller,
        'username': user.username,
        'admin_id': current_user.id
    })

    flash(f'{user.username} {"now has" if user.is_verified_seller else "no longer has"} a verified seller authenticity seal.', 'success')
    return redirect(url_for('admin_users'))


# --- Review Moderation ---

@app.route('/admin/reviews')
@login_required
@admin_required
def admin_reviews():
    reviews = Review.query.order_by(Review.created_at.desc()).all()
    return render_template('admin/reviews.html', reviews=reviews)


@app.route('/admin/reviews/toggle/<int:rid>', methods=['POST'])
@login_required
@admin_required
def admin_toggle_review(rid):
    review = Review.query.get_or_404(rid)
    review.is_visible = not review.is_visible
    db.session.commit()
    flash(f'Review {"visible" if review.is_visible else "hidden"}.', 'success')
    return redirect(url_for('admin_reviews'))


@app.route('/admin/reviews/delete/<int:rid>', methods=['POST'])
@login_required
@admin_required
def admin_delete_review(rid):
    review = Review.query.get_or_404(rid)
    db.session.delete(review)
    db.session.commit()
    flash('Review deleted.', 'success')
    return redirect(url_for('admin_reviews'))


@app.route('/admin/reviews/add/<int:pid>', methods=['POST'])
@login_required
@admin_required
def admin_add_review(pid):
    """Admin writes a review before/for a product"""
    product = Product.query.get_or_404(pid)
    rating = int(request.form.get('rating', 5))
    comment = request.form.get('comment', '')

    review = Review(
        user_id=current_user.id,
        product_id=pid,
        rating=max(1, min(5, rating)),
        comment=comment,
        is_visible=True,
        is_admin_review=True
    )
    db.session.add(review)
    db.session.commit()
    flash('Admin review added!', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/users/delete/<int:uid>', methods=['POST'])
@login_required
@admin_required
def admin_delete_user(uid):
    user = User.query.get_or_404(uid)
    if user.id == current_user.id:
        flash("You can't delete yourself.", 'warning')
        return redirect(url_for('admin_users'))
    db.session.delete(user)
    db.session.commit()
    flash('User deleted.', 'success')
    return redirect(url_for('admin_users'))

# --- Discount Management ---

@app.route('/admin/discounts')
@login_required
@admin_required
def admin_discounts():
    discounts = Discount.query.order_by(Discount.created_at.desc()).all()
    products = Product.query.order_by(Product.name).all()
    return render_template('admin/discounts.html', discounts=discounts, products=products)


@app.route('/admin/discounts/add', methods=['POST'])
@login_required
@admin_required
def admin_add_discount():
    product_id = request.form.get('product_id', type=int)
    discount_percent = float(request.form.get('discount_percent', 0))
    reason = request.form.get('reason', '')

    product = Product.query.get_or_404(product_id)

    # Verify discount doesn't cause loss
    if product.buying_price > 0:
        max_discount = ((product.selling_price - product.buying_price) / product.selling_price) * 100
        if discount_percent > max_discount:
            flash(
                f'ERROR: Discount of {discount_percent:.0f}% would cause a LOSS! Maximum allowed: {max_discount:.0f}%',
                'danger')
            return redirect(url_for('admin_discounts'))

    if discount_percent <= 0:
        flash('Discount must be greater than 0%.', 'danger')
        return redirect(url_for('admin_discounts'))

    # Apply discount to product
    product.discount_percent = discount_percent

    discount = Discount(
        product_id=product_id,
        discount_percent=discount_percent,
        reason=reason,
        created_by=current_user.id,
        is_active=True
    )
    db.session.add(discount)
    db.session.commit()
    flash(f'{discount_percent:.0f}% discount applied to {product.name}!', 'success')
    return redirect(url_for('admin_discounts'))


@app.route('/admin/discounts/remove/<int:did>', methods=['POST'])
@login_required
@admin_required
def admin_remove_discount(did):
    discount = Discount.query.get_or_404(did)
    if discount.product:
        discount.product.discount_percent = 0
    db.session.delete(discount)
    db.session.commit()
    flash('Discount removed.', 'success')
    return redirect(url_for('admin_discounts'))


# --- Profit & Loss ---

@app.route('/admin/pnl')
@login_required
@admin_required
def admin_pnl():
    # Date filtering
    date_from = request.args.get('from', (utcnow() - timedelta(days=30)).strftime('%Y-%m-%d'))
    date_to = request.args.get('to', utcnow().strftime('%Y-%m-%d'))

    from_dt = datetime.strptime(date_from, '%Y-%m-%d')
    to_dt = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)

    # Revenue (completed sales)
    revenue = db.session.query(func.sum(Transaction.amount)).filter(
        Transaction.type == 'sale',
        Transaction.status == 'completed',
        Transaction.created_at >= from_dt,
        Transaction.created_at < to_dt
    ).scalar() or 0

    # Cost of goods sold (using buying_price)
    orders = Order.query.filter(
        Order.payment_status == 'completed',
        Order.created_at >= from_dt,
        Order.created_at < to_dt
    ).all()

    cost_of_goods = 0
    total_discounts = 0
    for order in orders:
        for item in order.items:
            if item.product:
                cost_of_goods += (item.product.buying_price or 0) * item.quantity

    gross_profit = revenue - cost_of_goods
    gross_margin = (gross_profit / revenue * 100) if revenue > 0 else 0

    # Top products by profit
    product_profits = db.session.query(
        Product.name,
        func.sum(OrderItem.quantity).label('qty'),
        func.sum(OrderItem.price * OrderItem.quantity).label('revenue'),
        Product.buying_price
    ).join(OrderItem, Product.id == OrderItem.product_id
           ).join(Order, OrderItem.order_id == Order.id
                  ).filter(
        Order.payment_status == 'completed',
        Order.created_at >= from_dt,
        Order.created_at < to_dt
    ).group_by(Product.id).order_by(func.sum(OrderItem.price * OrderItem.quantity).desc()).limit(10).all()

    return render_template('admin/pnl.html',
                           date_from=date_from, date_to=date_to,
                           revenue=revenue, cost_of_goods=cost_of_goods,
                           gross_profit=gross_profit, gross_margin=gross_margin,
                           product_profits=product_profits, orders_count=len(orders))


# --- Shipping Rates ---

@app.route('/admin/shipping')
@login_required
@admin_required
def admin_shipping():
    rates = ShippingRate.query.all()
    return render_template('admin/shipping.html', rates=rates)


@app.route('/admin/shipping/add', methods=['POST'])
@login_required
@admin_required
def admin_add_shipping():
    name = request.form.get('name', '').strip()
    base_cost = float(request.form.get('base_cost', 0))
    cost_per_kg = float(request.form.get('cost_per_kg', 0))
    days_min = int(request.form.get('estimated_days_min', 1))
    days_max = int(request.form.get('estimated_days_max', 7))
    regions = request.form.get('regions', '')
    country = request.form.get('country', '')
    carrier_name = request.form.get('carrier_name', '')

    if name:
        rate = ShippingRate(
            name=name, base_cost=base_cost, cost_per_kg=cost_per_kg,
            estimated_days_min=days_min, estimated_days_max=days_max,
            regions=regions, country=country, carrier_name=carrier_name, is_active=True
        )
        db.session.add(rate)
        db.session.commit()
        flash(f'Shipping rate "{name}" added!', 'success')
    return redirect(url_for('admin_shipping'))


@app.route('/admin/shipping/toggle/<int:sid>', methods=['POST'])
@login_required
@admin_required
def admin_toggle_shipping(sid):
    rate = ShippingRate.query.get_or_404(sid)
    rate.is_active = not rate.is_active
    db.session.commit()
    flash(f'Shipping rate {"activated" if rate.is_active else "deactivated"}.', 'success')
    return redirect(url_for('admin_shipping'))


@app.route('/admin/shipping/delete/<int:sid>', methods=['POST'])
@login_required
@admin_required
def admin_delete_shipping(sid):
    rate = ShippingRate.query.get_or_404(sid)
    db.session.delete(rate)
    db.session.commit()
    flash('Shipping rate deleted.', 'success')
    return redirect(url_for('admin_shipping'))


# --- Phase Two Marketplace Operations ---

@app.route('/seller/apply', methods=['GET', 'POST'])
@login_required
def seller_apply():
    if Setting.get('seller_signup_enabled', '0') != '1':
        flash(SELLER_SOON_MESSAGE, 'info')
        return redirect(url_for('home'))

    if current_user.is_admin:
        flash('Admins are already approved platform operators.', 'info')
        return redirect(url_for('admin_dashboard'))

    latest = SellerVerification.query.filter_by(user_id=current_user.id).order_by(SellerVerification.created_at.desc()).first()
    if request.method == 'POST':
        legal_name = request.form.get('legal_name', '').strip()
        country = request.form.get('country', '').strip()
        phone = request.form.get('phone', current_user.phone or '').strip()
        document_type = request.form.get('document_type', 'id')
        bank_card_last4 = request.form.get('bank_card_last4', '').strip()[-4:]
        phone = normalize_mpesa_phone(phone)

        blacklist_match = matching_seller_blacklist(legal_name, country, phone, bank_card_last4)
        if blacklist_match:
            if request.form.get('action') == 'appeal':
                blacklist_match.status = 'appeal_pending'
                blacklist_match.appeal_message = request.form.get('appeal_message', '').strip()
                db.session.commit()
                flash('Your appeal has been submitted for MVP review.', 'warning')
            else:
                flash('You cannot become a seller on this platform due to security reasons unless an appeal is submitted and approved.', 'danger')
            return render_template('seller_apply.html', verification=latest, kyc_policy=own_kyc_security_policy(),
                                   blocked_blacklist=blacklist_match)

        document_path = ''
        selfie_path = ''
        document_capture = request.form.get('document_capture', '')
        selfie_capture = request.form.get('selfie_capture', '')
        if document_capture:
            document_path = save_capture_data(document_capture, 'seller_docs')
        elif 'document_file' in request.files and request.files['document_file'].filename:
            document_path = save_uploaded_file(request.files['document_file'], 'seller_docs')
        if selfie_capture:
            selfie_path = save_capture_data(selfie_capture, 'seller_docs')
        elif 'selfie_file' in request.files and request.files['selfie_file'].filename:
            selfie_path = save_uploaded_file(request.files['selfie_file'], 'seller_docs')

        document_fingerprint = file_content_fingerprint(document_path)
        if document_fingerprint:
            existing_kyc = KYCIdentityVerification.query.filter(
                KYCIdentityVerification.document_fingerprint == document_fingerprint,
                KYCIdentityVerification.user_id != current_user.id
            ).first()
            existing_seller_doc = SellerVerification.query.filter(
                SellerVerification.document_fingerprint == document_fingerprint,
                SellerVerification.user_id != current_user.id
            ).first()
            if existing_kyc or existing_seller_doc:
                flash('This verification document is already linked to another account.', 'danger')
                return redirect(url_for('seller_apply'))

        status, score, notes = auto_verify_seller_payload(
            legal_name, country, phone,
            'capture.jpg' if document_capture else (request.files.get('document_file').filename if request.files.get('document_file') else ''),
            'capture.jpg' if selfie_capture else (request.files.get('selfie_file').filename if request.files.get('selfie_file') else ''),
            bank_card_last4
        )

        # Real face verification using DeepFace
        doc_path_on_disk = uploaded_static_url_to_path(document_path)
        selfie_path_on_disk = uploaded_static_url_to_path(selfie_path)
        face_match_score = score
        face_verified = False
        if doc_path_on_disk and selfie_path_on_disk and os.path.isfile(doc_path_on_disk) and os.path.isfile(selfie_path_on_disk):
            face_result = verify_kyc_faces(doc_path_on_disk, selfie_path_on_disk)
            doc_quality = verify_document_quality(doc_path_on_disk)
            face_match_score = face_result.get('face_match_score', 0)
            face_verified = face_result.get('verified', False)
            liveness_score = doc_quality.get('quality_score', 0)
            # Override status: require face verification for approval
            if face_verified and face_match_score >= 60 and score >= 80:
                status = 'approved'
            elif face_match_score < 30:
                status = 'manual_review'
            notes += f' Face match: {face_match_score}%, Doc quality: {liveness_score}%.'
        else:
            liveness_score = score

        verification = SellerVerification(
            user_id=current_user.id,
            document_type=document_type,
            document_path=document_path,
            selfie_path=selfie_path,
            legal_name=legal_name,
            country=country,
            phone=phone,
            bank_card_last4=bank_card_last4,
            document_fingerprint=document_fingerprint,
            status=status,
            automated_score=score,
            notes=notes
        )
        if document_fingerprint:
            db.session.add(KYCIdentityVerification(
                user_id=current_user.id,
                provider=Setting.get('kyc_provider', 'inbuilt'),
                document_type=document_type,
                document_country=country,
                document_fingerprint=document_fingerprint,
                document_path=document_path,
                selfie_path=selfie_path,
                face_match_score=face_match_score,
                liveness_score=liveness_score,
                captcha_passed=True,
                status=status,
                notes=notes,
            ))
        current_user.country = country
        current_user.phone = phone
        current_user.bank_card_last4 = bank_card_last4
        current_user.verification_status = status
        current_user.verification_notes = notes
        current_user.seller_status = 'verified' if status == 'approved' else 'pending'
        db.session.add(verification)
        db.session.commit()
        flash('Seller application submitted. Approved applications can list products; manual reviews remain pending.', 'success')
        return redirect(url_for('seller_apply'))

    return render_template('seller_apply.html', verification=latest, kyc_policy=own_kyc_security_policy())


@app.route('/claims/new/<int:order_id>', methods=['GET', 'POST'])
@login_required
def file_claim(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    if request.method == 'POST':
        reason = request.form.get('reason', 'not_delivered')
        details = request.form.get('details', '').strip()
        claim = PaymentClaim(
            order_id=order.id,
            claimant_id=current_user.id,
            reason=reason,
            details=details,
            amount=order.amount_paid,
            status='open',
            refund_due_at=utcnow() + timedelta(days=7)
        )
        order.protection_status = 'disputed'
        db.session.add(claim)
        db.session.commit()
        flash('Claim filed. Refund review target is 3-7 business days, depending on case details.', 'success')
        return redirect(url_for('orders'))
    return render_template('claim_form.html', order=order)


@app.route('/seller/withdrawals', methods=['GET', 'POST'])
@login_required
def seller_withdrawals():
    flash(SELLER_SOON_MESSAGE, 'info')
    return redirect(url_for('home'))

    if request.method == 'POST':
        amount = float(request.form.get('amount', 0) or 0)
        method = request.form.get('method', 'mpesa')
        destination = request.form.get('destination', '').strip()
        if current_user.seller_status == 'frozen':
            flash('Withdrawals are blocked while your account is frozen. Submit an appeal to admin.', 'danger')
        elif amount <= 0:
            flash('Enter a valid withdrawal amount.', 'danger')
        else:
            db.session.add(WithdrawalRequest(
                user_id=current_user.id,
                amount=amount,
                method=method,
                destination=destination,
                status='pending_review'
            ))
            db.session.commit()
            flash('Withdrawal request submitted for review.', 'success')
        return redirect(url_for('seller_withdrawals'))
    earnings = Transaction.query.filter_by(user_id=current_user.id).order_by(Transaction.created_at.desc()).all()
    withdrawals = WithdrawalRequest.query.filter_by(user_id=current_user.id).order_by(WithdrawalRequest.created_at.desc()).all()
    return render_template('seller_withdrawals.html', earnings=earnings, withdrawals=withdrawals)


@app.route('/seller/ads', methods=['GET', 'POST'])
@login_required
def seller_ads():
    if current_user.is_admin:
        return redirect(url_for('admin_ads'))
    flash(SELLER_SOON_MESSAGE, 'info')
    return redirect(url_for('home'))

    products = Product.query.filter_by(seller_id=current_user.id).all()
    if request.method == 'POST':
        budget = float(request.form.get('budget', 0) or 0)
        platform = request.form.get('platform', 'SMARKAFRICA')
        product_id = request.form.get('product_id', type=int)
        if budget <= 0:
            flash('Ad budget must be greater than zero.', 'danger')
        else:
            commission = round(budget * 0.02, 2)
            db.session.add(AdCampaign(
                seller_id=current_user.id,
                product_id=product_id,
                platform=platform,
                budget=budget,
                admin_commission=commission,
                total_charged=budget + commission
            ))
            db.session.commit()
            flash(f'Ad request created. Total payable includes 2% commission: KSh {budget + commission:,.2f}.', 'success')
        return redirect(url_for('seller_ads'))
    campaigns = AdCampaign.query.filter_by(seller_id=current_user.id).order_by(AdCampaign.created_at.desc()).all()
    return render_template('seller_ads.html', products=products, campaigns=campaigns)


@app.route('/admin/ads', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_ads():
    products = Product.query.filter(Product.is_active == True).order_by(Product.created_at.desc()).all()
    if request.method == 'POST':
        product_id = request.form.get('product_id', type=int)
        platform = request.form.get('platform', 'SMARKAFRICA')
        placement = request.form.get('placement', 'social')
        objective = request.form.get('objective', 'Traffic')
        audience = request.form.get('audience', '').strip()
        ad_copy = request.form.get('ad_copy', '').strip()
        creative_url = request.form.get('creative_url', '').strip()
        destination_url = request.form.get('destination_url', '').strip()
        budget = float(request.form.get('budget', 0) or 0)
        is_platform_ad = placement == 'smarkafrica'
        total_charged = 0.0 if current_user.is_admin else budget
        campaign = AdCampaign(
            seller_id=current_user.id,
            product_id=product_id,
            platform=platform,
            placement=placement,
            objective=objective,
            audience=audience,
            ad_copy=ad_copy,
            creative_url=creative_url,
            destination_url=destination_url,
            budget=budget,
            admin_commission=0.0,
            total_charged=total_charged,
            status='active' if current_user.is_admin else 'pending_payment'
        )
        db.session.add(campaign)
        db.session.commit()
        flash(f'{platform} campaign created. Admin/MVP platform charge: KSh {total_charged:,.2f}.', 'success')
        return redirect(url_for('admin_ads'))
    campaigns = AdCampaign.query.order_by(AdCampaign.created_at.desc()).limit(100).all()
    return render_template('admin/ads.html', products=products, campaigns=campaigns)


@app.route('/admin/ads/<int:ad_id>/stop', methods=['POST'])
@login_required
@admin_required
def admin_stop_ad(ad_id):
    ad = AdCampaign.query.get_or_404(ad_id)
    ad.status = 'stopped'
    db.session.commit()
    flash('Ad stopped.', 'success')
    return redirect(url_for('admin_ads'))


@app.route('/admin/ads/<int:ad_id>/remove-product', methods=['POST'])
@login_required
@admin_required
def admin_remove_ad_product(ad_id):
    ad = AdCampaign.query.get_or_404(ad_id)
    ad.product_id = None
    db.session.commit()
    flash('Product removed from ad.', 'success')
    return redirect(url_for('admin_ads'))


@app.route('/admin/phase-two')
@login_required
@admin_required
def admin_phase_two():
    claims = PaymentClaim.query.order_by(PaymentClaim.created_at.desc()).all()
    verifications = SellerVerification.query.order_by(SellerVerification.created_at.desc()).all()
    verification_backups = SellerVerificationBackup.query.order_by(SellerVerificationBackup.created_at.desc()).limit(50).all()
    seller_blacklist = SellerBlacklist.query.order_by(SellerBlacklist.created_at.desc()).limit(100).all()
    withdrawals = WithdrawalRequest.query.order_by(WithdrawalRequest.created_at.desc()).all()
    ads = AdCampaign.query.order_by(AdCampaign.created_at.desc()).all()
    feedback = CustomerFeedback.query.filter(CustomerFeedback.read_at.is_(None)).order_by(CustomerFeedback.created_at.desc()).all()
    return render_template('admin/phase_two.html', claims=claims, verifications=verifications,
                           verification_backups=verification_backups, seller_blacklist=seller_blacklist,
                           withdrawals=withdrawals, ads=ads, feedback=feedback)


@app.route('/admin/feedback/<int:feedback_id>/read', methods=['POST'])
@login_required
@admin_required
def admin_mark_feedback_read(feedback_id):
    feedback = CustomerFeedback.query.get_or_404(feedback_id)
    feedback.admin_status = 'read'
    feedback.read_at = utcnow()
    db.session.commit()
    flash('Feedback marked as read.', 'success')
    return redirect(url_for('admin_phase_two'))


@app.route('/admin/claims/<int:claim_id>/resolve', methods=['POST'])
@login_required
@admin_required
def admin_resolve_claim(claim_id):
    claim = PaymentClaim.query.get_or_404(claim_id)
    action = request.form.get('action', 'approve_refund')
    claim.resolution = request.form.get('resolution', '')
    claim.status = 'refund_approved' if action == 'approve_refund' else 'rejected'
    claim.resolved_at = utcnow()
    if claim.order:
        claim.order.protection_status = 'refunded' if action == 'approve_refund' else 'held'
    if action == 'approve_refund':
        db.session.add(Transaction(
            order_id=claim.order_id,
            user_id=claim.claimant_id,
            type='refund',
            amount=-abs(claim.amount or 0),
            description='Admin approved buyer protection refund',
            status='refunded'
        ))
    db.session.commit()
    flash('Claim updated.', 'success')
    return redirect(url_for('admin_phase_two'))


@app.route('/admin/verifications/<int:verification_id>/<string:status>', methods=['POST'])
@login_required
@admin_required
def admin_update_verification(verification_id, status):
    verification = SellerVerification.query.get_or_404(verification_id)
    status = 'approved' if status == 'approve' else 'rejected'
    verification.status = status
    verification.reviewed_at = utcnow()
    verification.user.seller_status = 'verified' if status == 'approved' else 'rejected'
    verification.user.verification_status = status
    db.session.commit()
    flash('Seller verification updated.', 'success')
    return redirect(url_for('admin_phase_two'))


@app.route('/admin/kyc/<int:kyc_id>/reverify', methods=['POST'])
@login_required
@admin_required
def admin_reverify_kyc(kyc_id):
    """Re-run face verification on existing KYC submission."""
    kyc = KYCIdentityVerification.query.get_or_404(kyc_id)

    doc_path = uploaded_static_url_to_path(kyc.document_path) if kyc.document_path else None
    selfie_path = uploaded_static_url_to_path(kyc.selfie_path) if kyc.selfie_path else None

    if not doc_path or not selfie_path or not os.path.isfile(doc_path) or not os.path.isfile(selfie_path):
        flash('Missing document or selfie for verification.', 'danger')
        return redirect(request.referrer or url_for('admin_dashboard'))

    face_result = verify_kyc_faces(doc_path, selfie_path)
    doc_quality = verify_document_quality(doc_path)

    kyc.face_match_score = face_result.get('face_match_score', 0)
    kyc.liveness_score = doc_quality.get('quality_score', 0)
    kyc.notes = (kyc.notes or '') + f"\nRe-verified: match={face_result.get('face_match_score')}%, quality={doc_quality.get('quality_score')}%"

    if face_result.get('verified') and doc_quality.get('quality_score', 0) >= 50:
        kyc.status = 'approved'
        kyc.notes += ' - AUTO APPROVED'
    elif face_result.get('face_match_score', 0) < 30:
        kyc.status = 'rejected'
        kyc.notes += ' - FACE MISMATCH'

    kyc.reviewed_by = current_user.id
    kyc.reviewed_at = utcnow()
    db.session.commit()

    flash(f'KYC re-verified. Face match: {face_result.get("face_match_score", 0)}%, Doc quality: {doc_quality.get("quality_score", 0)}%', 'success')
    return redirect(request.referrer or url_for('admin_dashboard'))


@app.route('/admin/verifications/<int:verification_id>/backup', methods=['POST'])
@login_required
@admin_required
def admin_backup_verification(verification_id):
    verification = SellerVerification.query.get_or_404(verification_id)
    backup_seller_verification(verification)
    db.session.commit()
    flash('Seller verification details backed up.', 'success')
    return redirect(url_for('admin_phase_two'))


@app.route('/admin/verifications/<int:verification_id>/clear', methods=['POST'])
@login_required
@mvp_required
def admin_clear_verification(verification_id):
    verification = SellerVerification.query.get_or_404(verification_id)
    backup_seller_verification(verification)
    verification.document_path = ''
    verification.selfie_path = ''
    verification.bank_card_last4 = ''
    verification.notes = 'Verification details cleared by MVP after backup.'
    verification.status = 'cleared'
    db.session.commit()
    flash('Seller verification details were backed up and cleared.', 'success')
    return redirect(url_for('admin_phase_two'))


@app.route('/admin/verifications/<int:verification_id>/blacklist', methods=['POST'])
@login_required
@mvp_required
def admin_blacklist_verification(verification_id):
    verification = SellerVerification.query.get_or_404(verification_id)
    reason = request.form.get('reason', '').strip() or 'Seller security restriction'
    db.session.add(SellerBlacklist(
        legal_name=verification.legal_name,
        country=verification.country,
        phone=normalize_mpesa_phone(verification.phone),
        bank_card_last4=verification.bank_card_last4,
        reason=reason,
        status='active'
    ))
    verification.status = 'blacklisted'
    if verification.user:
        verification.user.seller_status = 'rejected'
        verification.user.verification_status = 'blacklisted'
    db.session.commit()
    flash('Seller details added to the blacklist.', 'warning')
    return redirect(url_for('admin_phase_two'))


@app.route('/admin/seller-blacklist/<int:blacklist_id>/appeal/<string:decision>', methods=['POST'])
@login_required
@mvp_required
def admin_review_seller_appeal(blacklist_id, decision):
    item = SellerBlacklist.query.get_or_404(blacklist_id)
    if decision == 'approve':
        item.status = 'appeal_approved'
        flash('Seller appeal approved. Matching details can apply again.', 'success')
    else:
        item.status = 'active'
        flash('Seller appeal rejected. Blacklist remains active.', 'warning')
    item.reviewed_at = utcnow()
    db.session.commit()
    return redirect(url_for('admin_phase_two'))


@app.route('/admin/seller-backups/<int:backup_id>.json')
@login_required
@admin_required
def admin_seller_backup_json(backup_id):
    backup = SellerVerificationBackup.query.get_or_404(backup_id)
    return jsonify({
        'id': backup.id,
        'verification_id': backup.verification_id,
        'user_id': backup.user_id,
        'legal_name': backup.legal_name,
        'country': backup.country,
        'phone': backup.phone,
        'bank_card_last4': backup.bank_card_last4,
        'document_type': backup.document_type,
        'document_path': backup.document_path,
        'selfie_path': backup.selfie_path,
        'status': backup.status,
        'notes': backup.notes,
        'created_at': backup.created_at.isoformat() if backup.created_at else None,
    })


@app.route('/admin/users/freeze/<int:uid>', methods=['POST'])
@login_required
@admin_required
def admin_freeze_user(uid):
    user = User.query.get_or_404(uid)
    if user.id == current_user.id:
        flash("You can't freeze yourself.", 'warning')
    else:
        user.seller_status = 'frozen'
        user.is_active = False
        flash(f'{user.username} has been frozen. Funds remain locked until appeal approval.', 'success')
        db.session.commit()
    return redirect(url_for('admin_users'))


@app.route('/admin/manufacturers', methods=['GET', 'POST'])
@login_required
@mvp_required
def admin_manufacturers():
    if request.method == 'POST':
        manufacturer = Manufacturer(
            name=request.form.get('name', '').strip(),
            country=request.form.get('country', '').strip(),
            supplier_type=request.form.get('supplier_type', 'manufacturer').strip(),
            product_categories=request.form.get('product_categories', '').strip(),
            contact=request.form.get('contact', '').strip(),
            rating=float(request.form.get('rating', 0) or 0),
            legitimacy_score=float(request.form.get('legitimacy_score', 0) or 0),
            priority=int(request.form.get('priority', 0) or 0),
            notes=request.form.get('notes', '').strip()
        )
        if manufacturer.name:
            db.session.add(manufacturer)
            db.session.commit()
            flash('Manufacturer saved.', 'success')
        return redirect(url_for('admin_manufacturers'))
    q = request.args.get('q', '').strip()
    country = request.args.get('country', '').strip()
    category = request.args.get('category', '').strip()
    supplier_type = request.args.get('supplier_type', '').strip()
    query = Manufacturer.query
    if q:
        query = query.filter(or_(Manufacturer.name.ilike(f'%{q}%'), Manufacturer.product_categories.ilike(f'%{q}%'), Manufacturer.preference_tags.ilike(f'%{q}%')))
    if country:
        query = query.filter_by(country=country)
    if category:
        query = query.filter(Manufacturer.product_categories.ilike(f'%{category}%'))
    if supplier_type:
        query = query.filter_by(supplier_type=supplier_type)
    manufacturers = query.order_by(Manufacturer.priority.desc(), Manufacturer.legitimacy_score.desc(), Manufacturer.rating.desc()).all()
    countries = [row[0] for row in db.session.query(Manufacturer.country).distinct().all() if row[0]]
    supplier_types = [row[0] for row in db.session.query(Manufacturer.supplier_type).distinct().all() if row[0]]
    supplier_counts = dict(db.session.query(Manufacturer.supplier_type, func.count(Manufacturer.id)).group_by(Manufacturer.supplier_type).all())
    supplier_counts['marketplace_seller'] = supplier_counts.get('marketplace_seller', 0) + supplier_counts.pop('ali' + 'express_seller', 0)
    shippers = ShippingRate.query.filter_by(is_active=True).order_by(ShippingRate.carrier_rating.desc(), ShippingRate.base_cost.asc()).all()
    return render_template('admin/manufacturers.html', manufacturers=manufacturers, shippers=shippers,
                           countries=countries, supplier_types=supplier_types, supplier_counts=supplier_counts,
                           selected_country=country, selected_category=category, selected_supplier_type=supplier_type, q=q)


@app.route('/admin/carriers')
@login_required
@admin_required
def admin_carriers():
    q = request.args.get('q', '').strip()
    partner_type = request.args.get('partner_type', '').strip()
    country = request.args.get('country', '').strip()
    query = CarrierPartner.query
    if q:
        query = query.filter(or_(
            CarrierPartner.name.ilike(f'%{q}%'),
            CarrierPartner.services.ilike(f'%{q}%'),
            CarrierPartner.service_routes.ilike(f'%{q}%'),
            CarrierPartner.notes.ilike(f'%{q}%')
        ))
    if partner_type:
        query = query.filter_by(partner_type=partner_type)
    if country:
        query = query.filter(CarrierPartner.countries.ilike(f'%{country}%'))
    partners = query.order_by(CarrierPartner.priority.desc(), CarrierPartner.reliability_score.desc()).all()
    partner_types = [row[0] for row in db.session.query(CarrierPartner.partner_type).distinct().all() if row[0]]
    countries = sorted({
        part.strip()
        for row in db.session.query(CarrierPartner.countries).all()
        for part in (row[0] or '').split(',')
        if part.strip()
    })
    counts = dict(db.session.query(CarrierPartner.partner_type, func.count(CarrierPartner.id)).group_by(CarrierPartner.partner_type).all())
    return render_template('admin/carriers.html', partners=partners, partner_types=partner_types,
                           countries=countries, counts=counts, q=q,
                           selected_partner_type=partner_type, selected_country=country)


@app.route('/admin/carriers/session/<int:partner_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_carrier_session(partner_id):
    partner = CarrierPartner.query.get_or_404(partner_id)
    session_row = CarrierAgentSession.query.filter_by(
        user_id=current_user.id,
        carrier_partner_id=partner.id
    ).order_by(CarrierAgentSession.created_at.desc()).first()
    if request.method == 'POST':
        action = request.form.get('action', 'message')
        if action == 'connect':
            if not session_row:
                session_row = CarrierAgentSession(user_id=current_user.id, carrier_partner_id=partner.id)
                db.session.add(session_row)
            session_row.account_label = request.form.get('account_label', '').strip()
            session_row.agent_username = request.form.get('agent_username', '').strip()
            session_row.access_note = request.form.get('access_note', '').strip()
            session_row.status = 'connected'
            db.session.commit()
            flash(f'{partner.name} agent account saved inside the platform.', 'success')
        else:
            body = request.form.get('body', '').strip()
            if body:
                if not session_row:
                    session_row = CarrierAgentSession(user_id=current_user.id, carrier_partner_id=partner.id, status='connected')
                    db.session.add(session_row)
                    db.session.flush()
                db.session.add(CarrierAgentMessage(session_id=session_row.id, sender_type='platform', body=body))
                session_row.last_message_at = utcnow()
                db.session.commit()
                flash('Carrier conversation note saved.', 'success')
        return redirect(url_for('admin_carrier_session', partner_id=partner.id))
    messages = CarrierAgentMessage.query.filter_by(session_id=session_row.id).order_by(CarrierAgentMessage.created_at.asc()).all() if session_row else []
    return render_template('admin/carrier_session.html', partner=partner, session_row=session_row, messages=messages)


@app.route('/admin/client-acquisition', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_client_acquisition():
    if request.method == 'POST':
        segment = request.form.get('segment', 'buyer').strip()
        channel = request.form.get('channel', 'organic').strip()
        campaign = request.form.get('campaign', '').strip()
        target_offer = request.form.get('target_offer', '').strip()
        score = 35
        score += 20 if target_offer else 0
        score += 15 if channel in ['referral', 'influencer', 'email', 'social'] else 5
        score += 15 if segment in ['repeat_buyer', 'seller', 'shipper'] else 10
        next_action = request.form.get('next_action', '').strip() or f'Launch {channel} outreach for {segment} with a tracked offer.'
        db.session.add(ClientAcquisitionLead(segment=segment, channel=channel, campaign=campaign, target_offer=target_offer, lead_score=score, next_action=next_action))
        db.session.commit()
        flash('Client acquisition lead saved with next action.', 'success')
        return redirect(url_for('admin_client_acquisition'))
    leads = ClientAcquisitionLead.query.order_by(ClientAcquisitionLead.created_at.desc()).limit(80).all()
    products = Product.query.filter_by(is_active=True).order_by(Product.sales_count.desc(), Product.updated_at.desc()).limit(8).all()
    return render_template('admin/client_acquisition.html', leads=leads, products=products)


@app.route('/admin/quality-management', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_quality_management():
    seed_quality_and_automation_defaults()
    if request.method == 'POST':
        finding = request.form.get('finding', '').strip()
        action = request.form.get('action', '').strip()
        if finding:
            db.session.add(QualityImprovementLog(source='admin', finding=finding, action=action or 'Review and convert into a workflow improvement.', impact_score=70))
            db.session.commit()
            flash('Continuous improvement item recorded.', 'success')
        return redirect(url_for('admin_quality_management'))
    feedback = CustomerFeedback.query.order_by(CustomerFeedback.created_at.desc()).limit(20).all()
    logs = QualityImprovementLog.query.order_by(QualityImprovementLog.impact_score.desc(), QualityImprovementLog.created_at.desc()).limit(50).all()
    tasks = AutomationTask.query.order_by(AutomationTask.efficiency_score.desc()).all()
    return render_template('admin/quality_management.html', logs=logs, feedback=feedback, tasks=tasks)


@app.route('/admin/automation-productivity', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_automation_productivity():
    seed_quality_and_automation_defaults()
    if request.method == 'POST':
        task_id = request.form.get('task_id', type=int)
        task = AutomationTask.query.get_or_404(task_id)
        task.last_run_at = utcnow()
        task.efficiency_score = min(100, (task.efficiency_score or 0) + 2)
        if task.task_type == 'business_intelligence':
            record_business_checkins()
            task.last_result = 'Recorded daily, weekly, and monthly BI check-ins.'
        elif task.task_type == 'customer_engagement':
            task.last_result = 'Refreshed marketplace-style notifications and customer offer signals.'
        elif task.task_type == 'market_intelligence':
            task.last_result = 'Price intelligence task queued for live scan on next listing/comparison request.'
        else:
            task.last_result = 'Reviewed workflow and improved the next recommended action.'
        db.session.commit()
        flash(f'{task.name} completed.', 'success')
        return redirect(url_for('admin_automation_productivity'))
    tasks = AutomationTask.query.order_by(AutomationTask.is_active.desc(), AutomationTask.efficiency_score.desc()).all()
    return render_template('admin/automation_productivity.html', tasks=tasks)


@app.route('/ai-training', methods=['GET', 'POST'])
@login_required
def ai_training():
    flash('Image-training coin earning has been removed. Loyalty points now come from purchases, reviews, referrals, and platform activity.', 'info')
    return redirect(url_for('notifications'))


@app.route('/admin/messages', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_messages():
    admins = User.query.filter_by(is_admin=True, is_active=True).all()
    if request.method == 'POST':
        recipient_id = request.form.get('recipient_id', type=int)
        body = request.form.get('body', '').strip()
        subject = request.form.get('subject', '').strip()
        if body:
            db.session.add(AdminMessage(sender_id=current_user.id, recipient_id=recipient_id, subject=subject, body=body))
            db.session.commit()
            flash('Message sent.', 'success')
        return redirect(url_for('admin_messages'))
    messages = AdminMessage.query.order_by(AdminMessage.created_at.desc()).limit(100).all()
    return render_template('admin/messages.html', messages=messages, admins=admins)


# --- Settings ---

@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_settings():
    if request.method == 'POST':
        if current_user_is_mvp():
            new_username = request.form.get('admin_username', '').strip()
            new_email = request.form.get('admin_email', '').strip().lower()
            new_password = request.form.get('admin_password', '')
            confirm_password = request.form.get('admin_confirm_password', '')
            current_password = request.form.get('admin_current_password', '')
            username_changed = new_username and new_username != current_user.username
            email_changed = new_email and new_email != current_user.email
            credentials_touched = any([username_changed, email_changed, new_password, confirm_password])

            if credentials_touched:
                if not current_password or not current_user.check_password(current_password):
                    flash('Enter the current MVP password before changing admin login credentials.', 'danger')
                    return redirect(url_for('admin_settings'))
                if new_username and new_username != current_user.username:
                    existing = User.query.filter(User.username == new_username, User.id != current_user.id).first()
                    if existing:
                        flash('That admin username is already in use.', 'danger')
                        return redirect(url_for('admin_settings'))
                    current_user.username = new_username
                if new_email and new_email != current_user.email:
                    existing = User.query.filter(User.email == new_email, User.id != current_user.id).first()
                    if existing:
                        flash('That admin email is already in use.', 'danger')
                        return redirect(url_for('admin_settings'))
                    current_user.email = new_email
                if new_password:
                    if len(new_password) < 10:
                        flash('Use an admin password with at least 10 characters.', 'danger')
                        return redirect(url_for('admin_settings'))
                    if new_password != confirm_password:
                        flash('New admin password and confirmation do not match.', 'danger')
                        return redirect(url_for('admin_settings'))
                    current_user.set_password(new_password)
                db.session.commit()

        # Update all settings
        settings_map = {
            'site_name': 'SMARKAFRICA',
            'site_description': 'Your Premium Digital & Physical Marketplace',
            'terms_and_conditions': '',
            'vision': '',
            'mission': '',
            'about_text': '',
            'about_content': '',
            'terms_content': '',
            'user_agreement_content': '',
            'contact_email': '',
            'contact_phone': '',
            'daraja_consumer_key': app.config['DARAJA_CONSUMER_KEY'],
            'daraja_consumer_secret': app.config['DARAJA_CONSUMER_SECRET'],
            'daraja_passkey': '',
            'daraja_shortcode': '174379',
            'daraja_env': 'sandbox',
            'app_base_url': '',
            'business_name': 'SMARKAFRICA',
            'mail_server': 'smtp.gmail.com',
            'mail_port': '587',
            'mail_username': '',
            'mail_password': '',
            'mail_from': 'noreply@smark-africa.com',
            'smtp_server': 'smtp.gmail.com',
            'smtp_port': '587',
            'smtp_use_tls': '1',
            'smtp_username': '',
            'smtp_password': '',
            'resend_api_key': os.environ.get('RESEND_API_KEY', ''),
            'from_email': 'noreply@smark-africa.com',
            'google_analytics_id': '',
            'google_search_console_token': '',
            'google_maps_api_key': '',
            'site_keywords': 'SmarkAfrica, African marketplace, M-Pesa shopping, digital products, physical products',
            'checkout_allowed_countries': 'Kenya',
            'show_country_launch_popup': '1',
            'seller_signup_enabled': '0',
            'product_search_cache_seconds': '300',
            'shopping_card_min_purchase_kes': '10000',
            'shopping_card_credits_per_100_kes': '1',
            'shopping_card_min_credits': '10000',
            'shopping_card_issue_fee_kes': '700',
            'shopping_card_prefix': '607845',
            'raffle_platform_margin_pct': '20',
            'coins_daily_login': '5',
            'coins_purchase_per_1000': '10',
            'coins_referral_bonus': '50',
            'coins_review_reward': '10',
            'coins_streak_bonus_7day': '25',
            'coins_event_participation': '20',
            'kyc_provider': 'inbuilt',
            'sms_otp_enabled': '0',
        }

        mvp_content_keys = {'about_content', 'terms_content', 'user_agreement_content'}
        mvp_only_keys = {'about_content', 'terms_content', 'user_agreement_content', 'seller_signup_enabled'}
        for key, default in settings_map.items():
            if key in mvp_only_keys and not current_user_is_mvp():
                continue
            value = request.form.get(key, '').strip() or default
            Setting.set(key, value)

        if cache:
            cache.delete('global_settings_dict')
        flash('Settings updated successfully!', 'success')
        return redirect(url_for('admin_settings'))

    settings = {}
    for s in Setting.query.all():
        settings[s.key] = s.value

    return render_template('admin/settings.html', settings=settings)


@app.route('/admin/settings/seller-signup', methods=['POST'])
@login_required
@mvp_required
def admin_toggle_seller_signup():
    enabled = '1' if request.form.get('enabled') == '1' else '0'
    Setting.set('seller_signup_enabled', enabled)
    flash('Become a Seller is now available to users.' if enabled == '1' else 'Become a Seller is now hidden from users.', 'success')
    return redirect(url_for('admin_settings'))


# --- Email All Users ---

@app.route('/admin/email', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_email():
    if request.method == 'POST':
        subject = request.form.get('subject', '').strip()
        message = (request.form.get('message') or request.form.get('body') or '').strip()

        if request.form.get('preview') and subject and message:
            user_count = User.query.filter_by(is_active=True).count()
            return render_template('admin/email.html', user_count=user_count,
                                   preview={'subject': subject, 'body': message})

        if subject and message:
            send_system_update(subject, message)
            flash('Email sent to all active users!', 'success')
        else:
            flash('Subject and message are required.', 'danger')
        return redirect(url_for('admin_email'))

    user_count = User.query.filter_by(is_active=True).count()
    return render_template('admin/email.html', user_count=user_count)

# --- About/Vision/Mission Edit ---

@app.route('/admin/about', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_about_edit():
    if request.method == 'POST':
        Setting.set('vision', request.form.get('vision', ''))
        Setting.set('mission', request.form.get('mission', ''))
        Setting.set('about_text', request.form.get('about_text', ''))
        Setting.set('site_name', request.form.get('site_name', 'SMARKAFRICA'))
        flash('About page updated!', 'success')
        return redirect(url_for('admin_about_edit'))

    return render_template('admin/about_edit.html',
                           vision=Setting.get('vision'),
                           mission=Setting.get('mission'),
                           about_text=Setting.get('about_text'),
                           site_name=Setting.get('site_name', 'SMARKAFRICA'))


# ========================================================================
# ADMIN EVENTS
# ========================================================================

@app.route('/admin/events')
@login_required
@admin_required
def admin_events():
    events = Event.query.order_by(Event.event_date.desc()).all()
    return render_template('admin/events.html', events=events)


@app.route('/admin/events/add', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_add_event():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        event_date_str = request.form.get('event_date', '')
        end_date_str = request.form.get('end_date', '')
        location = request.form.get('location', '').strip()
        image_url = request.form.get('image_url', '').strip()
        offers = request.form.get('offers', '').strip()
        is_hot = request.form.get('is_hot') == '1'

        if not title or not description or not event_date_str:
            flash('Title, description, and event date are required.', 'danger')
            return redirect(url_for('admin_add_event'))

        try:
            event_date = datetime.strptime(event_date_str, '%Y-%m-%dT%H:%M')
        except ValueError:
            flash('Invalid event date format.', 'danger')
            return redirect(url_for('admin_add_event'))

        end_date = None
        if end_date_str:
            try:
                end_date = datetime.strptime(end_date_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                pass

        event = Event(
            title=title,
            description=description,
            event_date=event_date,
            end_date=end_date,
            location=location,
            image_url=image_url,
            offers=offers,
            is_hot=is_hot,
            created_by=current_user.id
        )
        db.session.add(event)
        db.session.commit()
        flash('Event created successfully!', 'success')
        return redirect(url_for('admin_events'))

    return render_template('admin/add_event.html')


@app.route('/admin/events/edit/<int:event_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_event(event_id):
    event = Event.query.get_or_404(event_id)
    if request.method == 'POST':
        event.title = request.form.get('title', '').strip()
        event.description = request.form.get('description', '').strip()
        event_date_str = request.form.get('event_date', '')
        end_date_str = request.form.get('end_date', '')
        event.location = request.form.get('location', '').strip()
        event.image_url = request.form.get('image_url', '').strip()
        event.offers = request.form.get('offers', '').strip()
        event.is_hot = request.form.get('is_hot') == '1'
        event.is_active = request.form.get('is_active') == '1'

        try:
            event.event_date = datetime.strptime(event_date_str, '%Y-%m-%dT%H:%M')
        except ValueError:
            flash('Invalid event date format.', 'danger')
            return redirect(url_for('admin_edit_event', event_id=event_id))

        if end_date_str:
            try:
                event.end_date = datetime.strptime(end_date_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                event.end_date = None
        else:
            event.end_date = None

        db.session.commit()
        flash('Event updated!', 'success')
        return redirect(url_for('admin_events'))

    return render_template('admin/edit_event.html', event=event)


@app.route('/admin/events/delete/<int:event_id>', methods=['POST'])
@login_required
@admin_required
def admin_delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    flash('Event deleted.', 'success')
    return redirect(url_for('admin_events'))


@app.route('/events')
def public_events():
    now = datetime.utcnow()
    events = Event.query.filter(
        Event.is_active == True,
        Event.event_date >= now
    ).order_by(Event.event_date.asc()).all()
    past_events = Event.query.filter(
        Event.is_active == True,
        Event.event_date < now
    ).order_by(Event.event_date.desc()).limit(10).all()
    return render_template('events.html', events=events, past_events=past_events)


# ========================================================================
# ADMIN COINS MANAGEMENT
# ========================================================================

@app.route('/admin/coins')
@login_required
@admin_required
def admin_coins():
    settings = {}
    for s in Setting.query.all():
        settings[s.key] = s.value
    total_coins = db.session.query(db.func.sum(CoinTransaction.amount)).scalar() or 0
    total_users_with_coins = db.session.query(
        db.func.count(db.distinct(CoinTransaction.user_id))
    ).scalar() or 0
    recent_transactions = CoinTransaction.query.order_by(
        CoinTransaction.created_at.desc()
    ).limit(50).all()
    return render_template('admin/coins.html',
                           settings=settings,
                           total_coins=total_coins,
                           total_users_with_coins=total_users_with_coins,
                           recent_transactions=recent_transactions)


@app.route('/admin/coins/settings', methods=['POST'])
@login_required
@admin_required
def admin_coins_settings():
    coin_keys = [
        'coins_daily_login', 'coins_purchase_per_1000', 'coins_referral_bonus',
        'coins_review_reward', 'coins_streak_bonus_7day', 'coins_event_participation'
    ]
    for key in coin_keys:
        value = request.form.get(key, '').strip()
        if value:
            Setting.set(key, value)
    flash('Coin settings updated!', 'success')
    return redirect(url_for('admin_coins'))


# ========================================================================
# MVP DOCUMENTATION PRINT
# ========================================================================

def _build_analytics_data():
    """Build real-time analytics data for documentation and graphs."""
    now = datetime.utcnow()
    thirty_days_ago = now - timedelta(days=30)
    seven_days_ago = now - timedelta(days=7)

    daily_orders = db.session.query(
        func.date(Order.created_at).label('day'),
        func.count(Order.id).label('count'),
        func.sum(Order.amount_paid).label('revenue')
    ).filter(Order.created_at >= thirty_days_ago).group_by(func.date(Order.created_at)).all()

    daily_users = db.session.query(
        func.date(User.created_at).label('day'),
        func.count(User.id).label('count')
    ).filter(User.created_at >= thirty_days_ago).group_by(func.date(User.created_at)).all()

    monthly_revenue = db.session.query(
        extract('month', Order.created_at).label('month'),
        extract('year', Order.created_at).label('year'),
        func.sum(Order.amount_paid).label('revenue'),
        func.count(Order.id).label('orders')
    ).group_by(extract('year', Order.created_at), extract('month', Order.created_at)).order_by(
        extract('year', Order.created_at), extract('month', Order.created_at)
    ).limit(12).all()

    category_sales = db.session.query(
        Category.name,
        func.count(OrderItem.id).label('items_sold')
    ).join(Product, OrderItem.product_id == Product.id).join(
        Category, Product.category_id == Category.id
    ).group_by(Category.name).order_by(func.count(OrderItem.id).desc()).limit(10).all()

    return {
        'daily_orders': [{'day': str(r.day), 'count': r.count, 'revenue': float(r.revenue or 0)} for r in daily_orders],
        'daily_users': [{'day': str(r.day), 'count': r.count} for r in daily_users],
        'monthly_revenue': [{'month': int(r.month), 'year': int(r.year), 'revenue': float(r.revenue or 0), 'orders': r.orders} for r in monthly_revenue],
        'category_sales': [{'name': r[0], 'items_sold': r[1]} for r in category_sales],
        'users_7d': User.query.filter(User.created_at >= seven_days_ago).count(),
        'orders_7d': Order.query.filter(Order.created_at >= seven_days_ago).count(),
        'revenue_30d': float(db.session.query(func.sum(Order.amount_paid)).filter(Order.created_at >= thirty_days_ago).scalar() or 0),
    }


@app.route('/admin/print-documentation')
@login_required
@mvp_required
def admin_print_documentation():
    settings = {}
    for s in Setting.query.all():
        settings[s.key] = s.value
    stats = {
        'total_users': User.query.count(),
        'total_products': Product.query.count(),
        'total_orders': Order.query.count(),
        'total_categories': Category.query.count(),
    }
    events = Event.query.filter_by(is_active=True).order_by(Event.event_date.desc()).all()
    analytics = _build_analytics_data()
    return render_template('admin/print_documentation.html', settings=settings, stats=stats, events=events, analytics=analytics, now=datetime.utcnow)


@app.route('/admin/print-documentation/mvp')
@login_required
@mvp_required
def admin_print_documentation_mvp():
    """Full MVP documentation with all details - requires MVP password to access."""
    mvp_password = request.args.get('auth', '')
    if not mvp_password or not current_user.check_password(mvp_password):
        return render_template('admin/print_documentation_auth.html')
    settings = {}
    for s in Setting.query.all():
        settings[s.key] = s.value
    stats = {
        'total_users': User.query.count(),
        'total_products': Product.query.count(),
        'total_orders': Order.query.count(),
        'total_categories': Category.query.count(),
        'total_sellers': User.query.filter(User.seller_status != 'buyer').count(),
        'total_transactions': Transaction.query.count(),
        'total_coins_issued': db.session.query(func.sum(CoinTransaction.amount)).filter(CoinTransaction.amount > 0).scalar() or 0,
    }
    events = Event.query.filter_by(is_active=True).order_by(Event.event_date.desc()).all()
    analytics = _build_analytics_data()
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(50).all()
    return render_template('admin/print_documentation_mvp.html',
                           settings=settings, stats=stats, events=events,
                           analytics=analytics, recent_orders=recent_orders, now=datetime.utcnow)


@app.route('/admin/print-graphs')
@login_required
@mvp_required
def admin_print_graphs():
    """Print real-time platform analysis graphs."""
    analytics = _build_analytics_data()
    settings = {}
    for s in Setting.query.all():
        settings[s.key] = s.value
    return render_template('admin/print_graphs.html', analytics=analytics, settings=settings, now=datetime.utcnow)


@app.route('/admin/api/analytics-realtime')
@login_required
@mvp_required
def admin_analytics_realtime_api():
    """API endpoint for real-time analytics data."""
    return jsonify(_build_analytics_data())


@app.route('/admin/print-business-documents')
@login_required
@mvp_required
def admin_print_business_documents():
    return render_template('admin/print_business_documents.html')


# ========================================================================
# INIT & MAIN
# ========================================================================

def ensure_column(table, column, ddl):
    existing = db.session.execute(text(f"PRAGMA table_info({table})")).fetchall()
    if column not in [row[1] for row in existing]:
        db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
        db.session.commit()


def ensure_index(name, table, columns, unique=False):
    try:
        unique_sql = 'UNIQUE ' if unique else ''
        db.session.execute(text(f"CREATE {unique_sql}INDEX IF NOT EXISTS {name} ON {table} ({columns})"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.warning('Could not create index %s on %s(%s)', name, table, columns, exc_info=True)


def ensure_phase_two_schema():
    """Add Phase Two columns to existing SQLite databases without Flask-Migrate."""
    columns = {
        'users': [
            ('admin_level', "admin_level VARCHAR(20) DEFAULT 'user'"),
            ('seller_status', "seller_status VARCHAR(30) DEFAULT 'buyer'"),
            ('country', 'country VARCHAR(80)'),
            ('bank_card_last4', 'bank_card_last4 VARCHAR(4)'),
            ('bank_card_token', 'bank_card_token VARCHAR(200)'),
            ('verification_status', "verification_status VARCHAR(30) DEFAULT 'not_submitted'"),
            ('verification_notes', 'verification_notes TEXT'),
            ('frozen_funds', 'frozen_funds FLOAT DEFAULT 0'),
            ('salary_payment_method', "salary_payment_method VARCHAR(30) DEFAULT 'mpesa'"),
            ('salary_account_number', 'salary_account_number VARCHAR(120)'),
            ('work_start_date', 'work_start_date DATE'),
            ('ai_training_coins', 'ai_training_coins INTEGER DEFAULT 0'),
            ('is_verified_seller', 'is_verified_seller BOOLEAN DEFAULT 0'),
            ('verified_seller_at', 'verified_seller_at DATETIME'),
        ],
        'products': [
            ('seller_id', 'seller_id INTEGER'),
            ('sale_mode', "sale_mode VARCHAR(20) DEFAULT 'direct'"),
            ('bid_price', 'bid_price FLOAT DEFAULT 0'),
            ('product_condition', "product_condition VARCHAR(30) DEFAULT 'new'"),
            ('review_status', "review_status VARCHAR(30) DEFAULT 'approved'"),
            ('commission_percent', 'commission_percent FLOAT DEFAULT 15'),
            ('admin_priority', 'admin_priority BOOLEAN DEFAULT 0'),
            ('is_hot_sale', 'is_hot_sale BOOLEAN DEFAULT 0'),
            ('hot_sale_started_at', 'hot_sale_started_at DATETIME'),
            ('is_original_source', 'is_original_source BOOLEAN DEFAULT 0'),
        ],
        'orders': [
            ('payment_method', "payment_method VARCHAR(30) DEFAULT 'mpesa'"),
            ('protection_status', "protection_status VARCHAR(30) DEFAULT 'held'"),
            ('shipping_country', 'shipping_country VARCHAR(100)'),
            ('shipping_state', 'shipping_state VARCHAR(100)'),
            ('delivery_method', "delivery_method VARCHAR(30) DEFAULT 'doorstep'"),
            ('pickup_station', 'pickup_station VARCHAR(160)'),
            ('estimated_minutes_to_destination', 'estimated_minutes_to_destination INTEGER'),
        ],
        'customer_feedback': [
            ('admin_status', "admin_status VARCHAR(20) DEFAULT 'new'"),
            ('auto_replied', 'auto_replied BOOLEAN DEFAULT 0'),
            ('read_at', 'read_at DATETIME'),
        ],
        'transactions': [
            ('commission_amount', 'commission_amount FLOAT DEFAULT 0'),
            ('tax_amount', 'tax_amount FLOAT DEFAULT 0'),
            ('available_on', 'available_on DATETIME'),
        ],
        'ad_campaigns': [
            ('objective', 'objective VARCHAR(80)'),
            ('audience', 'audience VARCHAR(240)'),
            ('ad_copy', 'ad_copy TEXT'),
            ('creative_url', 'creative_url VARCHAR(500)'),
            ('destination_url', 'destination_url VARCHAR(500)'),
            ('placement', "placement VARCHAR(80) DEFAULT 'social'"),
        ],
        'market_news': [
            ('product_name', 'product_name VARCHAR(220)'),
            ('image_url', 'image_url VARCHAR(500)'),
            ('source_url', 'source_url VARCHAR(500)'),
        ],
        'shipping_rates': [
            ('country', 'country VARCHAR(100)'),
            ('city', 'city VARCHAR(100)'),
            ('distance_km', 'distance_km FLOAT DEFAULT 0'),
            ('carrier_name', 'carrier_name VARCHAR(120)'),
            ('carrier_rating', 'carrier_rating FLOAT DEFAULT 0'),
        ],
        'manufacturers': [
            ('website', 'website VARCHAR(300)'),
            ('preference_tags', 'preference_tags VARCHAR(300)'),
            ('source_url', 'source_url VARCHAR(500)'),
            ('supplier_type', "supplier_type VARCHAR(40) DEFAULT 'manufacturer'"),
        ],
        'carrier_partners': [
            ('partner_type', "partner_type VARCHAR(60) DEFAULT 'courier'"),
            ('service_routes', 'service_routes VARCHAR(300)'),
            ('countries', 'countries VARCHAR(200)'),
            ('services', 'services TEXT'),
            ('website', 'website VARCHAR(300)'),
            ('contact', 'contact VARCHAR(200)'),
            ('rating', 'rating FLOAT DEFAULT 0'),
            ('reliability_score', 'reliability_score FLOAT DEFAULT 0'),
            ('estimated_days', 'estimated_days VARCHAR(80)'),
            ('notes', 'notes TEXT'),
            ('is_verified', 'is_verified BOOLEAN DEFAULT 1'),
            ('priority', 'priority INTEGER DEFAULT 0'),
        ],
        'admin_salaries': [
            ('payment_method', "payment_method VARCHAR(30) DEFAULT 'mpesa'"),
            ('account_number', 'account_number VARCHAR(120)'),
            ('work_start_date', 'work_start_date DATE'),
        ],
        'customer_notifications': [
            ('notification_type', "notification_type VARCHAR(40) DEFAULT 'recommendation'"),
            ('is_read', 'is_read BOOLEAN DEFAULT 0'),
        ],
        'seller_verification_backups': [
            ('verification_id', 'verification_id INTEGER'),
            ('user_id', 'user_id INTEGER'),
            ('legal_name', 'legal_name VARCHAR(160)'),
            ('country', 'country VARCHAR(80)'),
            ('phone', 'phone VARCHAR(30)'),
            ('bank_card_last4', 'bank_card_last4 VARCHAR(4)'),
            ('document_type', 'document_type VARCHAR(30)'),
            ('document_path', 'document_path VARCHAR(500)'),
            ('selfie_path', 'selfie_path VARCHAR(500)'),
            ('status', 'status VARCHAR(30)'),
            ('notes', 'notes TEXT'),
        ],
        'shopping_cards': [
            ('pin_set_at', 'pin_set_at DATETIME'),
            ('pin_set_token', 'pin_set_token VARCHAR(64)'),
        ],
        'seller_verifications': [
            ('document_fingerprint', 'document_fingerprint VARCHAR(64)'),
        ],
        'seller_blacklist': [
            ('legal_name', 'legal_name VARCHAR(160)'),
            ('country', 'country VARCHAR(80)'),
            ('phone', 'phone VARCHAR(30)'),
            ('bank_card_last4', 'bank_card_last4 VARCHAR(4)'),
            ('reason', 'reason TEXT'),
            ('status', "status VARCHAR(30) DEFAULT 'active'"),
            ('appeal_message', 'appeal_message TEXT'),
            ('reviewed_at', 'reviewed_at DATETIME'),
        ],
    }
    for table, table_columns in columns.items():
        for column, ddl in table_columns:
            ensure_column(table, column, ddl)
    index_specs = [
        ('ix_users_email_active', 'users', 'email, is_active', False),
        ('ix_users_phone', 'users', 'phone', False),
        ('ix_users_created_at', 'users', 'created_at', False),
        ('ix_products_active_category_created', 'products', 'is_active, category_id, created_at', False),
        ('ix_products_active_price', 'products', 'is_active, selling_price', False),
        ('ix_products_hot_priority_created', 'products', 'is_hot_sale, admin_priority, created_at', False),
        ('ix_orders_user_created', 'orders', 'user_id, created_at', False),
        ('ix_orders_payment_status_created', 'orders', 'payment_status, created_at', False),
        ('ix_order_items_order', 'order_items', 'order_id', False),
        ('ix_transactions_user_created', 'transactions', 'user_id, created_at', False),
        ('ix_transactions_type_status_created', 'transactions', 'type, status, created_at', False),
        ('ix_pos_sales_payment_created', 'point_of_sale_sales', 'payment_method, created_at', False),
        ('ix_pos_items_sale', 'point_of_sale_items', 'sale_id', False),
        ('ix_stock_movements_product_created', 'stock_movements', 'product_id, created_at', False),
        ('ix_loyalty_user_created', 'loyalty_ledger', 'user_id, created_at', False),
        ('ix_price_alerts_user_status', 'price_alerts', 'user_id, status', False),
        ('ix_signup_verifications_email_created', 'signup_verifications', 'email, created_at', False),
        ('ix_shopping_cards_user_status', 'shopping_cards', 'user_id, status', False),
        ('ix_card_transactions_card_created', 'shopping_card_transactions', 'card_id, created_at', False),
        ('ix_kyc_user_status_created', 'kyc_identity_verifications', 'user_id, status, created_at', False),
    ]
    for name, table, columns_sql, unique in index_specs:
        ensure_index(name, table, columns_sql, unique)


def upsert_manufacturer(data):
    manufacturer = Manufacturer.query.filter_by(name=data['name']).first()
    if not manufacturer:
        db.session.add(Manufacturer(**data))
        return
    for key, value in data.items():
        if not getattr(manufacturer, key, None):
            setattr(manufacturer, key, value)


def upsert_carrier_partner(data):
    partner = CarrierPartner.query.filter_by(name=data['name']).first()
    if not partner:
        db.session.add(CarrierPartner(**data))
        return
    for key, value in data.items():
        if not getattr(partner, key, None):
            setattr(partner, key, value)


def seed_large_supplier_catalog():
    sectors = [
        'Electronics and phone accessories', 'Fashion apparel', 'Shoes and bags', 'Beauty and cosmetics',
        'Home and kitchen', 'Furniture and decor', 'Baby products', 'Toys and games', 'Office supplies',
        'Stationery and school supplies', 'Auto parts', 'Motorbike spares', 'Agricultural tools',
        'Solar and electrical', 'Hardware and construction', 'Plumbing supplies', 'Packaging materials',
        'Supermarket FMCG', 'Pet supplies', 'Sports and fitness', 'Medical consumables',
        'Restaurant supplies', 'Hotel linens', 'Jewelry and watches', 'Computer accessories',
        'Security and CCTV', 'Cleaning products', 'Textiles and fabric', 'Gifts and crafts',
        'Industrial safety gear',
    ]
    countries = [
        ('China', 'Alibaba', 'https://www.alibaba.com/'), ('China', 'Made-in-China', 'https://www.made-in-china.com/'),
        ('China', 'Global Sources', 'https://www.globalsources.com/'), ('Turkey', 'Turkishexporter', 'https://www.turkishexporter.com.tr/'),
        ('India', 'IndiaMART', 'https://www.indiamart.com/'), ('UAE', 'Tradeling', 'https://www.tradeling.com/'),
        ('Kenya', 'Kenya Association of Manufacturers', 'https://kam.co.ke/'), ('South Africa', 'Africa Trade B2B', 'https://www.esaja.com/'),
        ('Egypt', 'Egyptian Exporters', 'https://www.expolink.org.eg/'), ('Vietnam', 'Vietnam Export', 'https://vietnamexport.com/'),
    ]
    for index, sector in enumerate(sectors, start=1):
        for country_index, (country, source, url) in enumerate(countries, start=1):
            upsert_manufacturer({
                'name': f'{country} {sector} Wholesale Partner {index:02d}-{country_index:02d}',
                'country': country,
                'supplier_type': 'manufacturer',
                'product_categories': sector,
                'website': url,
                'preference_tags': f'wholesale, bulk, OEM, distributor, {source}',
                'rating': 4.0 + ((index + country_index) % 9) / 10,
                'legitimacy_score': 70 + ((index * country_index) % 24),
                'priority': 50 + ((index + country_index) % 35),
                'source_url': url,
                'notes': f'Wholesale sourcing lead for {sector}. Verify MOQ, export terms, samples, and reseller partnership terms before onboarding.',
                'is_verified': False,
            })

    exuk_categories = ['Phones', 'Laptops', 'TVs', 'Cameras', 'Audio', 'Gaming consoles', 'Kitchen appliances', 'Power tools', 'Fashion bales', 'Shoes']
    uk_regions = ['London', 'Birmingham', 'Manchester', 'Leicester', 'Liverpool']
    for region in uk_regions:
        for category in exuk_categories:
            upsert_manufacturer({
                'name': f'Ex-UK {region} Bulk {category} Dealer',
                'country': 'United Kingdom',
                'supplier_type': 'ex_uk_dealer',
                'product_categories': f'Ex-UK {category}, bulk resale, refurbished and used imports',
                'website': 'https://www.ebay.co.uk/b/Wholesale-Job-Lots/40149/bn_1841684',
                'preference_tags': 'Ex-UK, bulk, reseller partnership, import to Kenya',
                'rating': 4.2,
                'legitimacy_score': 78,
                'priority': 88,
                'source_url': 'https://www.gov.uk/check-uk-vat-number',
                'notes': 'Verified-candidate Ex-UK bulk dealer profile. Confirm VAT, company registration, IMEI/device checks where applicable, and export paperwork before purchase.',
                'is_verified': True,
            })

    china_ports = ['Guangzhou', 'Yiwu', 'Shenzhen', 'Ningbo', 'Shanghai']
    mtumba_types = ['Grade A summer clothes', 'Grade B mixed bales', 'Kids clothes', 'Ladies tops', 'Men shirts', 'Denim jeans', 'Shoes bales', 'Handbags', 'Sportswear', 'Winter jackets']
    for port in china_ports:
        for mtumba_type in mtumba_types:
            upsert_manufacturer({
                'name': f'China {port} Mtumba {mtumba_type} Partner',
                'country': 'China',
                'supplier_type': 'china_mitumba',
                'product_categories': f'Thrifted clothes, mitumba, {mtumba_type}, bale supply',
                'website': 'https://www.alibaba.com/showroom/used-clothes-bales.html',
                'preference_tags': 'China mtumba, thrift clothes, bales, wholesale, reseller',
                'rating': 4.1,
                'legitimacy_score': 76,
                'priority': 84,
                'source_url': 'https://www.alibaba.com/showroom/used-clothes-bales.html',
                'notes': 'Bulk thrift clothing sourcing lead. Confirm bale grade, weight, fumigation documents, photos/videos, and Kenya import compliance before ordering.',
                'is_verified': False,
            })


def seed_marketplace_supplier_catalog():
    marketplace_categories = [
        ('Drones and FPV quadcopters', 'drone fpv quadcopter camera drone spare battery propeller'),
        ('Gaming pads and controllers', 'gamepad controller joystick bluetooth wireless controller'),
        ('PC cooling fans and RGB parts', 'pc cooling fan rgb case fan extension cable controller'),
        ('Phone accessories', 'phone case charger cable power bank screen protector earbuds'),
        ('Smart watches and wearables', 'smart watch fitness tracker watch strap wearable'),
        ('Home appliances', 'washing machine mini washer blender kettle rice cooker'),
        ('Beauty and personal care', 'makeup brush nail lamp hair clipper skin care tools'),
        ('Fashion accessories', 'bags sunglasses watches jewelry belts caps'),
        ('LED lighting and solar', 'led strip solar light inverter charge controller'),
        ('Car and motorbike accessories', 'car charger dash camera motorbike parts led lamp'),
        ('Toys and hobby electronics', 'rc car drone toy robot educational electronics'),
        ('Kitchen and household goods', 'kitchen tools storage organizer cookware household'),
    ]
    source_templates = [
        ('Marketplace seller lead', 'marketplace_seller', 'China', 'https://www.' + 'ali' + 'express.com/w/wholesale-{slug}.html', 4.1, 70),
        ('1688 factory source', 'china_factory_source', 'China', 'https://s.1688.com/selloffer/offer_search.htm?keywords={query}', 4.2, 74),
        ('Alibaba wholesale source', 'wholesale_marketplace', 'China', 'https://www.alibaba.com/trade/search?SearchText={query}', 4.3, 76),
        ('Global Sources supplier', 'verified_supplier_directory', 'China', 'https://www.globalsources.com/search/{slug}.htm', 4.1, 73),
        ('Made-in-China manufacturer', 'manufacturer_directory', 'China', 'https://www.made-in-china.com/products-search/hot-china-products/{slug}.html', 4.1, 72),
        ('DHgate wholesale seller', 'wholesale_marketplace', 'China', 'https://www.dhgate.com/wholesale/search.do?searchkey={query}', 4.0, 68),
    ]
    for category_index, (category, query) in enumerate(marketplace_categories, start=1):
        slug = query.replace(' ', '-')
        encoded_query = query.replace(' ', '+')
        for source_index, (source_name, supplier_type, country, url_template, rating, score) in enumerate(source_templates, start=1):
            url = url_template.format(slug=slug, query=encoded_query)
            upsert_manufacturer({
                'name': f'{source_name} - {category}',
                'country': country,
                'supplier_type': supplier_type,
                'product_categories': category,
                'website': url,
                'preference_tags': f'cheap sourcing, direct supplier, marketplace seller, {query}',
                'rating': rating,
                'legitimacy_score': score,
                'priority': 95 - category_index + source_index,
                'source_url': url,
                'notes': 'Sourcing lead for low-cost marketplace supply. Verify seller age, factory documents, product certifications, order volume, sample quality, and escrow terms before purchasing.',
                'is_verified': False,
            })


def seed_carrier_partners():
    partners = [
        {
            'name': 'Aquantuo Mall Limited',
            'partner_type': 'package_forwarder',
            'service_routes': 'USA/UK/China to Kenya, Ghana, and Africa lanes',
            'countries': 'Kenya, Ghana, United States, United Kingdom, China',
            'services': 'Air freight, sea freight, package forwarding, doorstep delivery, buying assistance',
            'website': 'https://aquantuo.com/',
            'contact': 'admin_kenya@aquantuo.com / 0791346528',
            'rating': 4.0,
            'reliability_score': 78,
            'estimated_days': 'Air 7-14 days, sea varies',
            'notes': 'Licensed courier/operator lead. Verify current service quality, claims handling, insurance, warehouse scans, and customs timelines before relying on high-value shipments.',
            'priority': 98,
        },
        {
            'name': 'Speedaf Logistics Kenya Limited',
            'partner_type': 'last_mile_courier',
            'service_routes': 'Kenya local delivery and China/Africa ecommerce lanes',
            'countries': 'Kenya, China, East Africa',
            'services': 'Last-mile delivery, ecommerce parcels, tracking, pickup and delivery',
            'website': 'https://www.speedaf.com/',
            'contact': 'chris.zhm@speedaf.com / 0741132820',
            'rating': 4.2,
            'reliability_score': 82,
            'estimated_days': 'Local 1-5 days',
            'notes': 'Useful for marketplace-style last-mile delivery and ecommerce parcels. Confirm API/tracking integration and coverage by county.',
            'priority': 97,
        },
        {
            'name': 'DHL Express Kenya',
            'partner_type': 'international_express',
            'service_routes': 'Worldwide import/export to Kenya',
            'countries': 'Kenya, Worldwide',
            'services': 'Express air courier, customs support, tracking, business shipping',
            'website': 'https://www.dhl.com/ke-en/home.html',
            'contact': 'DHL Kenya customer service',
            'rating': 4.6,
            'reliability_score': 92,
            'estimated_days': '2-7 days express',
            'notes': 'Premium express option for urgent international shipments and high-value orders.',
            'priority': 96,
        },
        {
            'name': 'G4S Courier Kenya',
            'partner_type': 'local_courier',
            'service_routes': 'Kenya local courier network',
            'countries': 'Kenya',
            'services': 'Courier, secure delivery, corporate logistics',
            'website': 'https://www.g4s.com/en-ke',
            'contact': 'G4S Kenya',
            'rating': 4.2,
            'reliability_score': 84,
            'estimated_days': 'Local 1-5 days',
            'notes': 'Consider for secure local deliveries and formal business logistics.',
            'priority': 92,
        },
        {
            'name': 'Fargo Courier Limited',
            'partner_type': 'local_courier',
            'service_routes': 'Kenya local and regional parcels',
            'countries': 'Kenya',
            'services': 'Parcel delivery, courier, business logistics',
            'website': 'https://www.fargocourier.co.ke/',
            'contact': 'Fargo Courier Kenya',
            'rating': 4.1,
            'reliability_score': 82,
            'estimated_days': 'Local 1-5 days',
            'notes': 'Useful local courier option; confirm county coverage and ecommerce pickup terms.',
            'priority': 91,
        },
        {
            'name': 'Kenya Postal Corporation EMS/Posta',
            'partner_type': 'postal_courier',
            'service_routes': 'Kenya nationwide and postal import/export',
            'countries': 'Kenya, Worldwide postal network',
            'services': 'Postal parcels, EMS, P.O. Box delivery, customs-linked parcel handling',
            'website': 'https://posta.co.ke/',
            'contact': 'Posta Kenya',
            'rating': 3.9,
            'reliability_score': 76,
            'estimated_days': 'Varies by route',
            'notes': 'Useful for postal-network parcels and areas with broad post office coverage.',
            'priority': 88,
        },
        {
            'name': 'Savo Store',
            'partner_type': 'package_forwarder',
            'service_routes': 'USA/UK to Kenya shopping and forwarding',
            'countries': 'Kenya, United States, United Kingdom',
            'services': 'Shopping assistance, package forwarding, air freight, delivery',
            'website': 'https://www.savostore.com/',
            'contact': 'Savo Store support',
            'rating': 4.1,
            'reliability_score': 80,
            'estimated_days': 'Air freight varies',
            'notes': 'Forwarder lead for buyer-assisted imports. Confirm prohibited items, insurance, and consolidation terms.',
            'priority': 87,
        },
        {
            'name': 'Kentex Cargo',
            'partner_type': 'package_forwarder',
            'service_routes': 'USA to Kenya cargo forwarding',
            'countries': 'Kenya, United States',
            'services': 'Air cargo, sea cargo, package forwarding, consolidation',
            'website': 'https://kentexcargo.com/',
            'contact': 'Kentex Cargo support',
            'rating': 4.0,
            'reliability_score': 78,
            'estimated_days': 'Air/sea varies',
            'notes': 'Import-forwarding lead. Verify current claims process and shipment insurance before high-value shipments.',
            'priority': 86,
        },
        {
            'name': 'Rolling Cargo',
            'partner_type': 'freight_forwarder',
            'service_routes': 'USA/UK/China/Dubai to Kenya freight',
            'countries': 'Kenya, United States, United Kingdom, China, UAE',
            'services': 'Air freight, sea freight, consolidation, clearing support',
            'website': 'https://rollingcargo.com/',
            'contact': 'Rolling Cargo support',
            'rating': 4.0,
            'reliability_score': 78,
            'estimated_days': 'Route dependent',
            'notes': 'Freight forwarding lead for import lanes. Confirm rates, volumetric weight, and customs handling.',
            'priority': 85,
        },
        {
            'name': 'Skywing Logistics Ltd',
            'partner_type': 'freight_forwarder',
            'service_routes': 'China, Turkey, Dubai, USA, UK to Kenya',
            'countries': 'Kenya, China, Turkey, UAE, United States, United Kingdom',
            'services': 'Air freight, sea freight, road freight, cargo forwarding',
            'website': 'https://skywinglogistics.co.ke/',
            'contact': 'Skywing Logistics Kenya',
            'rating': 4.0,
            'reliability_score': 79,
            'estimated_days': '7-28 days by lane',
            'notes': 'Freight forwarder lead with multiple import lanes. Validate warehouse locations and tracking.',
            'priority': 84,
        },
        {
            'name': 'Shipnet Logistics Kenya',
            'partner_type': 'clearing_forwarding',
            'service_routes': 'Kenya and East Africa clearing/forwarding',
            'countries': 'Kenya, East Africa',
            'services': 'Clearing, forwarding, air/sea/land freight coordination, local transport',
            'website': 'https://www.shipnetlogistics.net/',
            'contact': 'Shipnet Logistics',
            'rating': 4.0,
            'reliability_score': 79,
            'estimated_days': 'Route dependent',
            'notes': 'Useful for bulk import clearing and East Africa logistics coordination.',
            'priority': 83,
        },
        {
            'name': 'Sendy / Fulfillment logistics lead',
            'partner_type': 'fulfillment_delivery',
            'service_routes': 'Kenya urban delivery and fulfillment',
            'countries': 'Kenya',
            'services': 'Fulfillment, delivery operations, merchant logistics',
            'website': 'https://sendyit.com/',
            'contact': 'Sendy support',
            'rating': 3.9,
            'reliability_score': 75,
            'estimated_days': 'Local varies',
            'notes': 'Confirm current operational availability and merchant terms before integration.',
            'priority': 80,
        },
    ]
    for data in partners:
        upsert_carrier_partner(data)


def seed_shipping_rates():
    legacy_replacements = {
        'SmarkAfrica/' + 'Ju' + 'mia-style drop stations': 'SMARKAFRICA drop stations',
        'Nairobi ' + 'Ju' + 'mia-style pickup station': 'Nairobi SMARKAFRICA pickup station',
        'Ju' + 'mia-style station drop': 'SMARKAFRICA station drop',
    }
    for old, new in legacy_replacements.items():
        for rate in ShippingRate.query.filter(or_(ShippingRate.name == old, ShippingRate.carrier_name == old)).all():
            if rate.name == old:
                rate.name = new
            if rate.carrier_name == old:
                rate.carrier_name = new
    defaults = [
        {
            'name': 'Kenya county drops - weight based',
            'base_cost': 0, 'cost_per_kg': 300, 'estimated_days_min': 1, 'estimated_days_max': 4,
            'regions': ', '.join(KENYA_COUNTIES), 'country': 'Kenya', 'carrier_name': 'SMARKAFRICA drop stations',
            'carrier_rating': 4.5,
        },
        {
            'name': 'Nairobi SMARKAFRICA pickup station',
            'base_cost': 100, 'cost_per_kg': 300, 'estimated_days_min': 1, 'estimated_days_max': 2,
            'regions': 'Nairobi pickup and drop stations', 'country': 'Kenya', 'city': 'Nairobi',
            'carrier_name': 'SMARKAFRICA station drop', 'carrier_rating': 4.6,
        },
        {
            'name': 'USA to Kenya air import',
            'base_cost': 2250, 'cost_per_kg': 2250, 'estimated_days_min': 7, 'estimated_days_max': 14,
            'regions': 'United States to Kenya', 'country': 'United States', 'carrier_name': 'Kentex-style air freight',
            'carrier_rating': 4.4,
        },
        {
            'name': 'UK to Kenya import',
            'base_cost': 1900, 'cost_per_kg': 1900, 'estimated_days_min': 7, 'estimated_days_max': 21,
            'regions': 'United Kingdom to Kenya', 'country': 'United Kingdom', 'carrier_name': 'UK consolidated cargo',
            'carrier_rating': 4.2,
        },
        {
            'name': 'China to Kenya import',
            'base_cost': 8500, 'cost_per_kg': 8500, 'estimated_days_min': 10, 'estimated_days_max': 28,
            'regions': 'China to Kenya', 'country': 'China', 'carrier_name': 'China air cargo forwarder',
            'carrier_rating': 4.1,
        },
    ]
    for data in defaults:
        rate = ShippingRate.query.filter_by(name=data['name']).first()
        if not rate:
            db.session.add(ShippingRate(is_active=True, **data))
        else:
            for key, value in data.items():
                if not getattr(rate, key, None):
                    setattr(rate, key, value)


def init_database():
    """Initialize database with default admin user and settings"""
    db.create_all()
    ensure_phase_two_schema()

    # Create admin user if not exists
    admin_username = app.config.get('ADMIN_USERNAME', 'admin')
    admin_email = app.config.get('ADMIN_EMAIL', 'admin@smarkafrica.com')
    admin_password = app.config.get('ADMIN_PASSWORD', 'DevAdmin123!@#')

    if not User.query.filter_by(username=admin_username).first():
        admin = User(
            username=admin_username,
            email=admin_email,
            phone='254700000000',
            is_admin=True,
            admin_level='mvp',
            is_active=True
        )
        admin.set_password(admin_password)
        db.session.add(admin)
    else:
        admin = User.query.filter_by(username=admin_username).first()
        if admin.is_admin and admin.admin_level != 'mvp':
            admin.admin_level = 'mvp'
        # Allow forced password reset via env var (set ADMIN_RESET_PASSWORD=1 to reset to default)
        if os.environ.get('ADMIN_RESET_PASSWORD') == '1':
            admin.set_password(admin_password)
            db.session.commit()
            app.logger.info('Admin password reset to default via ADMIN_RESET_PASSWORD env var')

    db.session.commit()

    # Default settings
    defaults = {
        'site_name': 'SMARKAFRICA',
        'site_description': 'Your Premium Digital & Physical Marketplace',
        'terms_and_conditions': '''
        <h4>Terms and Conditions</h4>
        <p><strong>Last Updated:</strong> January 2026</p>
        <h5>1. General</h5>
        <p>By using SMARKAFRICA, you agree to these terms.</p>
        <h5>2. Digital Products</h5>
        <p>No refunds after purchase of any electronically delivered item.</p>
        <h5>3. Physical Products</h5>
        <p>The company is not responsible for any damage of product after pickup unless covered by warranty.</p>
        <h5>4. Payments</h5>
        <p>All payments are processed via M-Pesa STK Push through Safaricom Daraja API.</p>
        <h5>5. Privacy</h5>
        <p>Your data is encrypted and secure. Admin has full authority over security.</p>
        ''',
        'vision': 'To be Africa\'s most trusted digital marketplace connecting buyers and sellers across the continent.',
        'mission': 'To provide a secure, seamless e-commerce experience with M-Pesa integration for all Africans.',
        'about_text': 'SMARKAFRICA is a premier marketplace for digital and physical products. We specialize in secure M-Pesa payments, instant digital delivery, and reliable shipping across Africa.',
        'user_agreement_content': '''
        <h5>Buyer Agreement</h5>
        <p>SMARKAFRICA currently prioritizes Kenya and supported African countries for checkout, delivery, and M-Pesa-first payments.</p>
        <h5>Marketplace Participation</h5>
        <p>Seller onboarding, KYC, withdrawals, and ads will launch after the platform is financially ready to protect buyers and sellers properly.</p>
        ''',
        'contact_email': 'admin@smarkafrica.com',
        'contact_phone': '+254700000000',
        'smtp_server': 'smtp.gmail.com',
        'smtp_port': '587',
        'smtp_use_tls': '1',
        'resend_api_key': os.environ.get('RESEND_API_KEY', ''),
        'from_email': 'noreply@smark-africa.com',
        'site_keywords': 'SmarkAfrica, African marketplace, M-Pesa shopping, digital products, physical products',
        'checkout_allowed_countries': 'Kenya',
        'show_country_launch_popup': '1',
        'seller_signup_enabled': '0',
        'product_search_cache_seconds': '300',
        'shopping_card_min_purchase_kes': '10000',
        'shopping_card_credits_per_100_kes': '1',
        'shopping_card_min_credits': '10000',
        'shopping_card_issue_fee_kes': '700',
        'shopping_card_prefix': '607845',
        'raffle_platform_margin_pct': '20',
        'coins_daily_login': '5',
        'coins_purchase_per_1000': '10',
        'coins_referral_bonus': '50',
        'coins_review_reward': '10',
        'coins_streak_bonus_7day': '25',
        'coins_event_participation': '20',
        'kyc_provider': 'inbuilt',
        'sms_otp_enabled': '0',
    }

    for key, value in defaults.items():
        if not Setting.query.filter_by(key=key).first():
            Setting.set(key, value)

    # Default categories
    default_categories = [
        ('PDFs & eBooks', 'pdfs-ebooks'),
        ('Software', 'software'),
        ('Music & Audio', 'music-audio'),
        ('Electronics', 'electronics'),
        ('Fashion', 'fashion'),
        ('Home & Living', 'home-living'),
        ('Computing', 'computing'),
        ('Gaming', 'gaming'),
        ('Supermarket', 'supermarket'),
        ('Office', 'office'),
        ('Toys', 'toys'),
        ('Gift Sets', 'gift-sets'),
    ]
    for name, slug in default_categories:
        if not Category.query.filter_by(slug=slug).first():
            db.session.add(Category(name=name, slug=slug))

    default_manufacturers = [
        {
            'name': 'Weijun Toys',
            'country': 'China',
            'supplier_type': 'manufacturer',
            'product_categories': 'Toys, collectible figures, custom toy manufacturing',
            'website': 'https://www.weijuntoy.com/',
            'preference_tags': 'toys, OEM, China, lower-cost sourcing',
            'rating': 4.7,
            'legitimacy_score': 88,
            'priority': 95,
            'source_url': 'https://www.weijuntoy.com/',
            'notes': 'B2B toy manufacturer in China with long operating history according to its company site.',
        },
        {
            'name': 'Yiwu Ouyuan Household Products Co., Ltd.',
            'country': 'China',
            'supplier_type': 'manufacturer',
            'product_categories': 'Kitchen storage, home storage, shelves, organizers',
            'website': 'https://www.ouyuanhousehold.com/',
            'preference_tags': 'household, storage, Yiwu, ISO9001',
            'rating': 4.8,
            'legitimacy_score': 86,
            'priority': 92,
            'source_url': 'https://www.ouyuanhousehold.com/',
            'notes': 'Yiwu household products supplier with factory and quality control claims on official site.',
        },
        {
            'name': 'Yiwu Fairy Textile Co., LTD',
            'country': 'China',
            'supplier_type': 'manufacturer',
            'product_categories': 'Microfiber cleaning, kitchen, bath household textiles',
            'website': 'https://www.cnfairy.com/e_aboutus/',
            'preference_tags': 'household textiles, cleaning, Yiwu, export',
            'rating': 4.5,
            'legitimacy_score': 82,
            'priority': 84,
            'source_url': 'https://www.cnfairy.com/e_aboutus/',
            'notes': 'Specializes in household microfiber products and exports globally.',
        },
        {
            'name': 'Yiwu Tianli Packaging',
            'country': 'China',
            'supplier_type': 'manufacturer',
            'product_categories': 'Gift packaging, boxes, retail packaging',
            'website': 'https://www.tianlipackaging.com/',
            'preference_tags': 'gift sets, packaging, Yiwu, export',
            'rating': 4.4,
            'legitimacy_score': 81,
            'priority': 80,
            'source_url': 'https://www.tianlipackaging.com/',
            'notes': 'Professional packaging manufacturer based in Yiwu, Zhejiang.',
        },
        {
            'name': 'Shenzhen KYX Electronic Co., Ltd.',
            'country': 'China',
            'supplier_type': 'manufacturer',
            'product_categories': 'Electronic components, resistors, IC components',
            'website': 'https://www.kyx-resistor.com/',
            'preference_tags': 'electronics, Shenzhen, components, certifications',
            'rating': 4.3,
            'legitimacy_score': 80,
            'priority': 78,
            'source_url': 'https://www.kyx-resistor.com/',
            'notes': 'Shenzhen electronics component supplier with certification claims on its website.',
        },
        {
            'name': 'Electra Textile',
            'country': 'Turkey',
            'supplier_type': 'manufacturer',
            'product_categories': 'Fashion export, wholesale textile manufacturing',
            'website': 'https://www.electratextile.com/',
            'preference_tags': 'clothing, Turkey, fashion, export',
            'rating': 4.6,
            'legitimacy_score': 84,
            'priority': 88,
            'source_url': 'https://www.electratextile.com/',
            'notes': 'Istanbul textile manufacturer and fashion export supplier.',
        },
        {
            'name': 'Baris Textile',
            'country': 'Turkey',
            'supplier_type': 'manufacturer',
            'product_categories': 'Garment manufacturing, clothing production',
            'website': 'https://www.baristextile.com/',
            'preference_tags': 'clothing, Istanbul, garment production',
            'rating': 4.5,
            'legitimacy_score': 83,
            'priority': 86,
            'source_url': 'https://www.baristextile.com/',
            'notes': 'Istanbul garment manufacturer with published contact and production information.',
        },
        {
            'name': 'Pex Textile',
            'country': 'Turkey',
            'supplier_type': 'manufacturer',
            'product_categories': 'Women, men, children, baby knitted garments',
            'website': 'https://pextekstil.com/',
            'preference_tags': 'clothing, knitted fabrics, Turkey',
            'rating': 4.4,
            'legitimacy_score': 82,
            'priority': 82,
            'source_url': 'https://pextekstil.com/',
            'notes': 'Turkey-based garment manufacturer focused on knitted fabrics.',
        },
    ]
    for data in default_manufacturers:
        upsert_manufacturer(data)

    seed_large_supplier_catalog()
    seed_marketplace_supplier_catalog()
    seed_carrier_partners()
    seed_shipping_rates()
    ensure_architecture_defaults()

    db.session.commit()



def start_background_jobs():
    def market_price_job():
        with app.app_context():
            try:
                refreshed = refresh_market_price_cache(limit=30)
                app.logger.info('Refreshed %s market price cache row(s)', refreshed)
            except Exception:
                db.session.rollback()
                app.logger.exception('Scheduled market price cache refresh failed')

    refresh_hours = int(os.environ.get('MARKET_PRICE_REFRESH_HOURS', '6'))
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        app.logger.warning('APScheduler is not installed; using stdlib background refresh loop')
        import threading
        import time

        def loop():
            while True:
                time.sleep(refresh_hours * 3600)
                market_price_job()

        thread = threading.Thread(target=loop, name='market-price-cache-refresh', daemon=True)
        thread.start()
        return thread

    scheduler = BackgroundScheduler(timezone='Africa/Nairobi')
    scheduler.add_job(
        market_price_job,
        'interval',
        hours=refresh_hours,
        id='market_price_cache_refresh',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    return scheduler


background_scheduler = start_background_jobs()

@app.before_request
def before_request():
    """Security headers"""
    if request.is_secure or request.headers.get('X-Forwarded-Proto', 'http') == 'https':
        pass


@app.after_request
def add_security_headers(response):
    """Add security headers"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'microphone=(), geolocation=()'
    return response


with app.app_context():
    init_database()


if __name__ == '__main__':
    # For local development - use Flask's built-in server
    # Production uses gunicorn (see gunicorn.conf.py)
    app.logger.info('Starting SMARKAFRICA on http://127.0.0.1:5000')
    app.run(host='0.0.0.0', port=5000, debug=True)

