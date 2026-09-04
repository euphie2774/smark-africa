import json
import secrets
import uuid
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from scale import CACHE_MISS, TTLCache, float_env, int_env

db = SQLAlchemy()


def generate_order_number():
    return 'SAF-' + datetime.utcnow().strftime('%Y%m%d-') + uuid.uuid4().hex[:8].upper()


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    __table_args__ = (
        db.Index('ix_users_email_active', 'email', 'is_active'),
        db.Index('ix_users_phone', 'phone'),
        db.Index('ix_users_created_at', 'created_at'),
        db.Index('ix_users_seller_status', 'seller_status'),
    )
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    phone = db.Column(db.String(20), unique=True)
    is_admin = db.Column(db.Boolean, default=False)
    admin_level = db.Column(db.String(20), default='user')  # user, admin, super_admin, mvp
    seller_status = db.Column(db.String(30), default='buyer')  # buyer, pending, verified, rejected, frozen
    country = db.Column(db.String(80))
    bank_card_last4 = db.Column(db.String(4))
    bank_card_token = db.Column(db.String(200))
    verification_status = db.Column(db.String(30), default='not_submitted')
    verification_notes = db.Column(db.Text)
    frozen_funds = db.Column(db.Float, default=0.0)
    salary_payment_method = db.Column(db.String(30), default='mpesa')
    salary_account_number = db.Column(db.String(120))
    seller_payout_method = db.Column(db.String(30), default='mpesa')
    seller_payout_account = db.Column(db.String(160))
    seller_payout_name = db.Column(db.String(160))
    work_start_date = db.Column(db.Date)
    ai_training_coins = db.Column(db.Integer, default=0)
    is_verified_seller = db.Column(db.Boolean, default=False)
    verified_seller_at = db.Column(db.DateTime)
    # A partnered brand rather than an individual trader. Admin-granted only, so
    # no seller can award themselves the crown. Every product they list carries
    # the brand mark; see Product.is_brand_partner for one-off brand stock.
    is_brand = db.Column(db.Boolean, default=False)
    brand_name = db.Column(db.String(160))
    seller_rating = db.Column(db.Float, default=0.0)
    seller_rating_notes = db.Column(db.Text)
    verified_seller_badge_enabled = db.Column(db.Boolean, default=True)
    # Service linking desk. Two separate ideas on purpose:
    #   service_duty_on      - this admin says they are at the desk right now. Their
    #                          own switch, flipped by them, and the only thing that
    #                          decides whether a client is offered the platform or
    #                          the WhatsApp fallback. Presence is never guessed:
    #                          there is no last_seen on this table, and adding a
    #                          heartbeat would mean a write per request.
    #   service_linking_agent - the MVP nominated this admin for the desk. Routing
    #                          only: their requests come to them first. It does not
    #                          make them on duty, and an unclaimed request stays
    #                          claimable by any admin who is, so a nominated agent
    #                          who never logs in cannot strand a client.
    service_duty_on = db.Column(db.Boolean, default=False)
    service_duty_since = db.Column(db.DateTime)
    service_linking_agent = db.Column(db.Boolean, default=False)
    # Separately assignable: billing a client is not the same trust as introducing
    # one, so the invoice desk is its own nomination rather than a reuse of the
    # linking flag.
    invoice_agent = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    orders = db.relationship('Order', backref='customer', lazy=True)
    reviews = db.relationship('Review', backref='author', lazy=True)
    cart_items = db.relationship('Cart', backref='user', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def first_name(self):
        return self.username

    @property
    def last_name(self):
        return ''


class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    products = db.relationship('Product', backref='category', lazy=True)


class Product(db.Model):
    __tablename__ = 'products'
    __table_args__ = (
        db.Index('ix_products_active_category_created', 'is_active', 'category_id', 'created_at'),
        db.Index('ix_products_active_price', 'is_active', 'selling_price'),
        db.Index('ix_products_active_sales', 'is_active', 'sales_count'),
        db.Index('ix_products_hot_priority_created', 'is_hot_sale', 'admin_priority', 'created_at'),
        db.Index('ix_products_seller_status', 'seller_id', 'review_status'),
    )
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=False)
    short_description = db.Column(db.String(300))
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'))
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    # Pricing
    buying_price = db.Column(db.Float, nullable=False, default=0.0)
    selling_price = db.Column(db.Float, nullable=False)
    discount_percent = db.Column(db.Float, default=0.0)
    vat_applicable = db.Column(db.Boolean, default=False)
    vat_rate = db.Column(db.Float, default=0.0)
    sale_mode = db.Column(db.String(20), default='direct')  # direct, bid
    bid_price = db.Column(db.Float, default=0.0)
    product_condition = db.Column(db.String(30), default='new')  # new, second_hand, thrifted, refurbished
    review_status = db.Column(db.String(30), default='approved')
    commission_percent = db.Column(db.Float, default=15.0)
    admin_priority = db.Column(db.Boolean, default=False)
    is_hot_sale = db.Column(db.Boolean, default=False)
    hot_sale_started_at = db.Column(db.DateTime)
    is_original_source = db.Column(db.Boolean, default=False)
    # Stock from a brand the platform has partnered with. Set per product so
    # admin can flag brand goods listed under any account; a seller marked
    # User.is_brand brands every listing without this needing to be ticked.
    is_brand_partner = db.Column(db.Boolean, default=False)
    brand_label = db.Column(db.String(80))

    # Product type
    is_digital = db.Column(db.Boolean, default=False)
    file_path = db.Column(db.String(500))  # Path to digital product file
    file_size = db.Column(db.Integer)  # File size in bytes
    first_page_preview = db.Column(db.Boolean, default=True)

    # Physical product
    stock = db.Column(db.Integer, default=0)
    weight_kg = db.Column(db.Float, default=0.0)  # For shipping calc

    # Seller (or admin) absorbs delivery on this item. Overrides the calculated
    # quote with zero rather than editing the rate card.
    free_delivery = db.Column(db.Boolean, default=False)

    # Where the item actually is, so buyers see a pin before they open it
    location_label = db.Column(db.String(200))
    location_county = db.Column(db.String(100))
    location_lat = db.Column(db.Float)
    location_lng = db.Column(db.Float)

    # Countries the seller will not ship to, as a JSON list of country names.
    # Kenya and its counties are never excludable - the home market always
    # delivers - so this only ever holds countries outside Kenya.
    excluded_countries = db.Column(db.Text)

    # Media
    image_url = db.Column(db.String(500))
    additional_images = db.Column(db.Text)  # JSON list

    # Stats
    is_active = db.Column(db.Boolean, default=True)
    is_featured = db.Column(db.Boolean, default=False)
    sales_count = db.Column(db.Integer, default=0)
    views_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    reviews = db.relationship('Review', backref='product', lazy=True,
                              order_by='Review.created_at.desc()')
    seller = db.relationship('User', lazy=True)

    @property
    def discounted_price(self):
        if self.discount_percent > 0:
            return round(self.selling_price * (1 - self.discount_percent / 100), 2)
        return self.selling_price

    @property
    def location_display(self):
        """Short human label for the pin, or None when we know nothing."""
        label = (self.location_label or self.location_county or '').strip()
        if label:
            return label
        if self.location_lat is not None and self.location_lng is not None:
            return f'{self.location_lat:.3f}, {self.location_lng:.3f}'
        return None

    @property
    def has_location(self):
        return self.location_display is not None

    @property
    def average_rating(self):
        ratings = [r.rating for r in self.reviews if r.is_visible and r.rating]
        if ratings:
            return sum(ratings) / len(ratings)
        return 0.0

    @property
    def rating_count(self):
        return len([r for r in self.reviews if r.is_visible])


class Cart(db.Model):
    __tablename__ = 'cart'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'product_id', name='uq_cart_user_product'),
        db.Index('ix_cart_user_created', 'user_id', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', lazy=True)


class Order(db.Model):
    __tablename__ = 'orders'
    __table_args__ = (
        db.Index('ix_orders_user_created', 'user_id', 'created_at'),
        db.Index('ix_orders_status_created', 'status', 'created_at'),
        db.Index('ix_orders_payment_status_created', 'payment_status', 'created_at'),
        db.Index('ix_orders_shipping_status_created', 'shipping_status', 'created_at'),
        db.Index('ix_orders_mpesa_checkout', 'mpesa_checkout_request_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(50), unique=True, nullable=False, default=generate_order_number)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    # Payment
    amount_paid = db.Column(db.Float, nullable=False)
    shipping_cost = db.Column(db.Float, default=0.0)
    discount_amount = db.Column(db.Float, default=0.0)
    # Set at checkout, honoured at payment: the referral owner is only paid once
    # the money actually lands, so an abandoned order credits nobody.
    promo_code_id = db.Column(db.Integer, db.ForeignKey('promo_codes.id'), nullable=True)
    mpesa_receipt = db.Column(db.String(100))
    mpesa_phone = db.Column(db.String(20))
    # The Daraja CheckoutRequestID for the in-flight STK push. Lives on the order
    # rather than in a settings row so the callback can find its order with one
    # indexed lookup, inside the same transaction that created the order - a
    # callback can and does arrive before a separate write would have committed,
    # and a payment that cannot be matched to an order is a payment lost.
    mpesa_checkout_request_id = db.Column(db.String(120))
    payment_status = db.Column(db.String(20), default='pending')  # pending, completed, failed
    payment_method = db.Column(db.String(30), default='mpesa')
    protection_status = db.Column(db.String(30), default='held')  # held, released, disputed, refunded

    # Shipping
    shipping_address = db.Column(db.Text)
    shipping_country = db.Column(db.String(100))
    shipping_city = db.Column(db.String(100))
    shipping_state = db.Column(db.String(100))
    shipping_status = db.Column(db.String(20), default='pending')  # pending, processing, shipped, delivered
    tracking_number = db.Column(db.String(200))
    estimated_delivery = db.Column(db.DateTime)
    delivery_method = db.Column(db.String(30), default='doorstep')  # doorstep, pickup_station
    pickup_station = db.Column(db.String(160))
    estimated_minutes_to_destination = db.Column(db.Integer)

    # Status
    status = db.Column(db.String(20), default='pending')  # pending, processing, completed, cancelled
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = db.relationship('OrderItem', backref='order', lazy=True, cascade='all, delete-orphan')
    tracking_updates = db.relationship('TrackingUpdate', backref='order', lazy=True,
                                       order_by='TrackingUpdate.created_at.desc()')

    @property
    def total(self):
        return self.amount_paid or 0.0

    @property
    def total_amount(self):
        return self.amount_paid or 0.0

    @property
    def user(self):
        return self.customer

    @property
    def transaction(self):
        return Transaction.query.filter_by(order_id=self.id).order_by(Transaction.created_at.desc()).first()


class OrderItem(db.Model):
    __tablename__ = 'order_items'
    __table_args__ = (
        db.Index('ix_order_items_order', 'order_id'),
        db.Index('ix_order_items_product', 'product_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    product_name = db.Column(db.String(200))
    price = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.Integer, default=1)
    is_digital = db.Column(db.Boolean, default=False)

    product = db.relationship('Product', lazy=True)


class TrackingUpdate(db.Model):
    __tablename__ = 'tracking_updates'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    status = db.Column(db.String(100))
    location = db.Column(db.String(200))
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Review(db.Model):
    __tablename__ = 'reviews'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'product_id', name='uq_review_user_product'),
        db.Index('ix_reviews_product_visible_created', 'product_id', 'is_visible', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)  # 1-5
    comment = db.Column(db.Text)
    is_visible = db.Column(db.Boolean, default=True)
    is_admin_review = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CustomerFeedback(db.Model):
    __tablename__ = 'customer_feedback'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    experience_rating = db.Column(db.Integer, nullable=False, default=5)
    satisfaction_rating = db.Column(db.Integer, nullable=False, default=5)
    improvement_text = db.Column(db.Text)
    admin_status = db.Column(db.String(20), default='new')
    auto_replied = db.Column(db.Boolean, default=False)
    read_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', lazy=True)


class Transaction(db.Model):
    __tablename__ = 'transactions'
    __table_args__ = (
        db.Index('ix_transactions_user_created', 'user_id', 'created_at'),
        db.Index('ix_transactions_order_created', 'order_id', 'created_at'),
        db.Index('ix_transactions_type_status_created', 'type', 'status', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    type = db.Column(db.String(50))  # sale, refund, withdrawal
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text)
    mpesa_receipt = db.Column(db.String(100))
    status = db.Column(db.String(20), default='completed')
    commission_amount = db.Column(db.Float, default=0.0)
    tax_amount = db.Column(db.Float, default=0.0)
    destination_account = db.Column(db.String(200))
    settlement_group = db.Column(db.String(80))
    disbursed_at = db.Column(db.DateTime)
    available_on = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    order = db.relationship('Order', lazy=True)
    user = db.relationship('User', lazy=True)

    @property
    def transaction_id(self):
        return self.mpesa_receipt


class ShippingRate(db.Model):
    __tablename__ = 'shipping_rates'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    base_cost = db.Column(db.Float, default=0.0)
    cost_per_kg = db.Column(db.Float, default=0.0)
    estimated_days_min = db.Column(db.Integer, default=1)
    estimated_days_max = db.Column(db.Integer, default=7)
    is_active = db.Column(db.Boolean, default=True)
    regions = db.Column(db.String(500))  # Comma-separated regions
    country = db.Column(db.String(100))
    city = db.Column(db.String(100))
    distance_km = db.Column(db.Float, default=0.0)
    carrier_name = db.Column(db.String(120))
    carrier_rating = db.Column(db.Float, default=0.0)


class SellerVerification(db.Model):
    __tablename__ = 'seller_verifications'
    __table_args__ = (
        db.Index('ix_seller_verifications_user_created', 'user_id', 'created_at'),
        db.Index('ix_seller_verifications_status_created', 'status', 'created_at'),
        db.UniqueConstraint('document_fingerprint', name='uq_seller_verification_document_fingerprint'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    document_type = db.Column(db.String(30), nullable=False)
    document_path = db.Column(db.String(500))
    selfie_path = db.Column(db.String(500))
    legal_name = db.Column(db.String(160))
    country = db.Column(db.String(80))
    phone = db.Column(db.String(30))
    bank_card_last4 = db.Column(db.String(4))
    document_fingerprint = db.Column(db.String(64))
    status = db.Column(db.String(30), default='pending')
    automated_score = db.Column(db.Float, default=0.0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime)

    user = db.relationship('User', lazy=True)


class SellerVerificationBackup(db.Model):
    __tablename__ = 'seller_verification_backups'
    id = db.Column(db.Integer, primary_key=True)
    verification_id = db.Column(db.Integer, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    legal_name = db.Column(db.String(160))
    country = db.Column(db.String(80))
    phone = db.Column(db.String(30))
    bank_card_last4 = db.Column(db.String(4))
    document_type = db.Column(db.String(30))
    document_path = db.Column(db.String(500))
    selfie_path = db.Column(db.String(500))
    status = db.Column(db.String(30))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', lazy=True)


class SellerBlacklist(db.Model):
    __tablename__ = 'seller_blacklist'
    id = db.Column(db.Integer, primary_key=True)
    legal_name = db.Column(db.String(160))
    country = db.Column(db.String(80))
    phone = db.Column(db.String(30))
    bank_card_last4 = db.Column(db.String(4))
    reason = db.Column(db.Text)
    status = db.Column(db.String(30), default='active')  # active, appeal_pending, appeal_approved
    appeal_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime)


class PaymentClaim(db.Model):
    __tablename__ = 'payment_claims'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    claimant_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    accused_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    reason = db.Column(db.String(120), nullable=False)
    details = db.Column(db.Text)
    amount = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(30), default='open')
    resolution = db.Column(db.Text)
    refund_due_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime)

    order = db.relationship('Order', lazy=True)
    claimant = db.relationship('User', foreign_keys=[claimant_id], lazy=True)
    accused = db.relationship('User', foreign_keys=[accused_id], lazy=True)


class WithdrawalRequest(db.Model):
    __tablename__ = 'withdrawal_requests'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(30), default='mpesa')
    destination = db.Column(db.String(160))
    status = db.Column(db.String(30), default='pending_review')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime)

    user = db.relationship('User', lazy=True)


class AdCampaign(db.Model):
    __tablename__ = 'ad_campaigns'
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    platform = db.Column(db.String(40), nullable=False)
    budget = db.Column(db.Float, nullable=False)
    admin_commission = db.Column(db.Float, default=0.0)
    total_charged = db.Column(db.Float, default=0.0)
    objective = db.Column(db.String(80))
    audience = db.Column(db.String(240))
    ad_copy = db.Column(db.Text)
    creative_url = db.Column(db.String(500))
    destination_url = db.Column(db.String(500))
    placement = db.Column(db.String(80), default='social')
    status = db.Column(db.String(30), default='pending_payment')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    seller = db.relationship('User', lazy=True)
    product = db.relationship('Product', lazy=True)


class SocialAdPost(db.Model):
    """An ad queued for posting to a social platform by an admin.

    Sellers never touch this table: they buy a campaign through AdCampaign and
    an admin turns a paid campaign into a post here, recording the live URL.
    """
    __tablename__ = 'social_ad_posts'
    __table_args__ = (
        db.Index('ix_social_ad_posts_status_created', 'status', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    # Nullable so admins can also post house ads with no seller behind them.
    campaign_id = db.Column(db.Integer, db.ForeignKey('ad_campaigns.id'), nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    platform = db.Column(db.String(40), default='instagram')
    caption = db.Column(db.Text)
    hashtags = db.Column(db.String(600))
    creative_url = db.Column(db.String(500))
    scheduled_for = db.Column(db.DateTime)
    status = db.Column(db.String(30), default='draft')  # draft|scheduled|posted|archived
    posted_url = db.Column(db.String(500))
    posted_at = db.Column(db.DateTime)
    posted_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    campaign = db.relationship('AdCampaign', lazy=True)
    product = db.relationship('Product', lazy=True)
    posted_by = db.relationship('User', lazy=True, foreign_keys=[posted_by_id])


class Manufacturer(db.Model):
    __tablename__ = 'manufacturers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    country = db.Column(db.String(80))
    supplier_type = db.Column(db.String(40), default='manufacturer')
    product_categories = db.Column(db.String(300))
    contact = db.Column(db.String(200))
    website = db.Column(db.String(300))
    preference_tags = db.Column(db.String(300))
    source_url = db.Column(db.String(500))
    rating = db.Column(db.Float, default=0.0)
    legitimacy_score = db.Column(db.Float, default=0.0)
    priority = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    is_verified = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CarrierPartner(db.Model):
    __tablename__ = 'carrier_partners'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, unique=True)
    partner_type = db.Column(db.String(60), default='courier')
    service_routes = db.Column(db.String(300))
    countries = db.Column(db.String(200))
    services = db.Column(db.Text)
    website = db.Column(db.String(300))
    contact = db.Column(db.String(200))
    rating = db.Column(db.Float, default=0.0)
    reliability_score = db.Column(db.Float, default=0.0)
    estimated_days = db.Column(db.String(80))
    notes = db.Column(db.Text)
    is_verified = db.Column(db.Boolean, default=True)
    priority = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CarrierAgentSession(db.Model):
    __tablename__ = 'carrier_agent_sessions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    carrier_partner_id = db.Column(db.Integer, db.ForeignKey('carrier_partners.id'), nullable=False)
    account_label = db.Column(db.String(160))
    agent_username = db.Column(db.String(160))
    access_note = db.Column(db.Text)
    status = db.Column(db.String(30), default='connected')
    last_message_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', lazy=True)
    carrier = db.relationship('CarrierPartner', lazy=True)


class CarrierAgentMessage(db.Model):
    __tablename__ = 'carrier_agent_messages'
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('carrier_agent_sessions.id'), nullable=False)
    sender_type = db.Column(db.String(30), default='platform')
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    session = db.relationship('CarrierAgentSession', lazy=True, backref='messages')


class AIImageTrainingSubmission(db.Model):
    __tablename__ = 'ai_image_training_submissions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    image_url = db.Column(db.String(500))
    product_label = db.Column(db.String(180))
    category_hint = db.Column(db.String(120))
    attributes = db.Column(db.Text)
    quality_score = db.Column(db.Float, default=0.0)
    coins_awarded = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', lazy=True)


class BusinessCheckIn(db.Model):
    __tablename__ = 'business_checkins'
    id = db.Column(db.Integer, primary_key=True)
    period_type = db.Column(db.String(20), default='daily')
    sales_total = db.Column(db.Float, default=0.0)
    orders_count = db.Column(db.Integer, default=0)
    average_order_value = db.Column(db.Float, default=0.0)
    slow_products_count = db.Column(db.Integer, default=0)
    recommendation = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ClientAcquisitionLead(db.Model):
    __tablename__ = 'client_acquisition_leads'
    id = db.Column(db.Integer, primary_key=True)
    segment = db.Column(db.String(80), default='buyer')
    channel = db.Column(db.String(80), default='organic')
    campaign = db.Column(db.String(160))
    target_offer = db.Column(db.String(220))
    lead_score = db.Column(db.Float, default=0.0)
    next_action = db.Column(db.String(240))
    status = db.Column(db.String(30), default='new')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class QualityImprovementLog(db.Model):
    __tablename__ = 'quality_improvement_logs'
    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(80), default='system')
    finding = db.Column(db.Text, nullable=False)
    action = db.Column(db.Text)
    impact_score = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(30), default='open')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AutomationTask(db.Model):
    __tablename__ = 'automation_tasks'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    task_type = db.Column(db.String(80), default='productivity')
    cadence = db.Column(db.String(60), default='daily')
    efficiency_score = db.Column(db.Float, default=0.0)
    last_result = db.Column(db.Text)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    priority = db.Column(db.String(20), default='normal')
    is_active = db.Column(db.Boolean, default=True)
    last_run_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    assignee = db.relationship('User', foreign_keys=[assigned_to_id], lazy=True)
    assigner = db.relationship('User', foreign_keys=[assigned_by_id], lazy=True)


class PointOfSaleSale(db.Model):
    __tablename__ = 'point_of_sale_sales'
    __table_args__ = (
        db.Index('ix_pos_sales_cashier_created', 'cashier_id', 'created_at'),
        db.Index('ix_pos_sales_payment_created', 'payment_method', 'created_at'),
        db.Index('ix_pos_sales_customer_email', 'customer_email'),
        db.Index('ix_pos_sales_customer_phone', 'customer_phone'),
    )
    id = db.Column(db.Integer, primary_key=True)
    cashier_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    customer_name = db.Column(db.String(160))
    customer_email = db.Column(db.String(160))
    customer_phone = db.Column(db.String(40))
    subtotal = db.Column(db.Float, default=0.0)
    discount_amount = db.Column(db.Float, default=0.0)
    tax_amount = db.Column(db.Float, default=0.0)
    total_amount = db.Column(db.Float, default=0.0)
    payment_method = db.Column(db.String(40), default='cash')
    payment_status = db.Column(db.String(30), default='paid')
    invoice_number = db.Column(db.String(80), unique=True)
    receipt_number = db.Column(db.String(80), unique=True)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    cashier = db.relationship('User', lazy=True)
    items = db.relationship('PointOfSaleItem', backref='sale', lazy=True, cascade='all, delete-orphan')


class PointOfSaleItem(db.Model):
    __tablename__ = 'point_of_sale_items'
    __table_args__ = (
        db.Index('ix_pos_items_sale', 'sale_id'),
        db.Index('ix_pos_items_product', 'product_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('point_of_sale_sales.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    product_name = db.Column(db.String(220))
    quantity = db.Column(db.Integer, default=1)
    unit_price = db.Column(db.Float, default=0.0)
    list_price = db.Column(db.Float, default=0.0)
    discount_amount = db.Column(db.Float, default=0.0)
    tax_amount = db.Column(db.Float, default=0.0)
    line_total = db.Column(db.Float, default=0.0)

    product = db.relationship('Product', lazy=True)


class ProductBarcode(db.Model):
    __tablename__ = 'product_barcodes'
    __table_args__ = (
        db.Index('ix_product_barcodes_product', 'product_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    barcode = db.Column(db.String(80), unique=True, nullable=False)
    barcode_type = db.Column(db.String(30), default='internal')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', lazy=True)


class Supplier(db.Model):
    __tablename__ = 'suppliers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(180), nullable=False)
    contact = db.Column(db.String(200))
    country = db.Column(db.String(80))
    categories = db.Column(db.String(300))
    status = db.Column(db.String(30), default='active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PurchaseOrder(db.Model):
    __tablename__ = 'purchase_orders'
    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=True)
    status = db.Column(db.String(30), default='draft')
    expected_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    supplier = db.relationship('Supplier', lazy=True)
    creator = db.relationship('User', lazy=True)
    items = db.relationship('PurchaseOrderItem', backref='purchase_order', lazy=True, cascade='all, delete-orphan')


class PurchaseOrderItem(db.Model):
    __tablename__ = 'purchase_order_items'
    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(db.Integer, db.ForeignKey('purchase_orders.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity_ordered = db.Column(db.Integer, default=0)
    quantity_received = db.Column(db.Integer, default=0)
    unit_cost = db.Column(db.Float, default=0.0)

    product = db.relationship('Product', lazy=True)


class StockMovement(db.Model):
    __tablename__ = 'stock_movements'
    __table_args__ = (
        db.Index('ix_stock_movements_product_created', 'product_id', 'created_at'),
        db.Index('ix_stock_movements_type_created', 'movement_type', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    movement_type = db.Column(db.String(40), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    before_stock = db.Column(db.Integer, default=0)
    after_stock = db.Column(db.Integer, default=0)
    reference_type = db.Column(db.String(40))
    reference_id = db.Column(db.Integer)
    note = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', lazy=True)
    user = db.relationship('User', lazy=True)


class BusinessStorefront(db.Model):
    __tablename__ = 'business_storefronts'
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    business_name = db.Column(db.String(180), nullable=False)
    slug = db.Column(db.String(180), unique=True, nullable=False)
    categories = db.Column(db.String(500))
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    physical_address = db.Column(db.String(300))
    landmark = db.Column(db.String(180))
    contact_phone = db.Column(db.String(40))
    contact_email = db.Column(db.String(160))
    # Filled in by the owner once the storefront is approved, so shoppers can
    # see what the shop actually deals in.
    about = db.Column(db.Text)
    specialties = db.Column(db.String(500))
    opening_hours = db.Column(db.String(200))
    commission_percent = db.Column(db.Float, default=10.0)
    status = db.Column(db.String(30), default='pending_review')
    verification_notes = db.Column(db.Text)
    # Geocoded once at review time; products listed here inherit the pin.
    location_lat = db.Column(db.Float)
    location_lng = db.Column(db.Float)
    location_county = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime)

    owner = db.relationship('User', lazy=True)
    category = db.relationship('Category', lazy=True)

    @property
    def is_live(self):
        """True once an admin has cleared the storefront to trade."""
        return (self.status or '') in ('approved', 'active', 'verified')

    @property
    def location_label(self):
        parts = [p for p in [self.physical_address, self.landmark, self.location_county] if p]
        return ', '.join(parts[:2]) if parts else None


class ExchangeRate(db.Model):
    __tablename__ = 'exchange_rates'
    id = db.Column(db.Integer, primary_key=True)
    base_currency = db.Column(db.String(3), default='KES', nullable=False)
    quote_currency = db.Column(db.String(3), nullable=False)
    rate = db.Column(db.Float, nullable=False)
    source = db.Column(db.String(120), default='manual')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SupportTicket(db.Model):
    __tablename__ = 'support_tickets'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    channel = db.Column(db.String(30), default='web')
    subject = db.Column(db.String(180), nullable=False)
    body = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(20), default='normal')
    status = db.Column(db.String(30), default='open')
    escalation_level = db.Column(db.Integer, default=0)
    response_due_at = db.Column(db.DateTime)
    resolution_due_at = db.Column(db.DateTime)
    satisfaction_rating = db.Column(db.Integer)
    assigned_admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime)

    user = db.relationship('User', foreign_keys=[user_id], lazy=True)
    assigned_admin = db.relationship('User', foreign_keys=[assigned_admin_id], lazy=True)


class LoyaltyLedger(db.Model):
    __tablename__ = 'loyalty_ledger'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'event_type', 'reference_id', name='uq_loyalty_user_event_reference'),
        db.Index('ix_loyalty_user_created', 'user_id', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    event_type = db.Column(db.String(40), nullable=False)
    points = db.Column(db.Integer, default=0)
    description = db.Column(db.String(240))
    reference_id = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', lazy=True)


class BNPLPlan(db.Model):
    __tablename__ = 'bnpl_plans'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True)
    principal_amount = db.Column(db.Float, nullable=False)
    deposit_percent = db.Column(db.Float, default=15.0)
    term_months = db.Column(db.Integer, default=3)
    risk_score = db.Column(db.Float, default=0.0)
    approval_status = db.Column(db.String(30), default='manual_review')
    device_lock_code = db.Column(db.String(120))
    device_imei = db.Column(db.String(32))
    device_serial = db.Column(db.String(80))
    device_install_method = db.Column(db.String(30), default='imei')
    device_install_status = db.Column(db.String(30), default='pending_install')
    device_installed_at = db.Column(db.DateTime)
    device_remote_payload = db.Column(db.Text)
    device_status_note = db.Column(db.Text)
    lock_status = db.Column(db.String(30), default='unlocked')
    next_due_at = db.Column(db.DateTime)
    last_reminder_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime)

    user = db.relationship('User', lazy=True)
    product = db.relationship('Product', lazy=True)
    order = db.relationship('Order', lazy=True)
    installments = db.relationship('BNPLInstallment', backref='plan', lazy=True, cascade='all, delete-orphan')


class BNPLProductPolicy(db.Model):
    __tablename__ = 'bnpl_product_policies'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False, unique=True)
    is_enabled = db.Column(db.Boolean, default=False)
    min_deposit_percent = db.Column(db.Float, default=15.0)
    max_term_months = db.Column(db.Integer, default=3)
    partner_name = db.Column(db.String(160))
    notes = db.Column(db.Text)
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    product = db.relationship('Product', lazy=True)
    approver = db.relationship('User', lazy=True)


class BNPLInstallment(db.Model):
    __tablename__ = 'bnpl_installments'
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('bnpl_plans.id'), nullable=False)
    sequence = db.Column(db.Integer, nullable=False)
    amount_due = db.Column(db.Float, nullable=False)
    amount_paid = db.Column(db.Float, default=0.0)
    due_at = db.Column(db.DateTime, nullable=False)
    paid_at = db.Column(db.DateTime)
    status = db.Column(db.String(30), default='scheduled')
    reminder_sent_at = db.Column(db.DateTime)


class TrustScore(db.Model):
    __tablename__ = 'trust_scores'
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.Integer, nullable=False)
    score = db.Column(db.Float, default=50.0)
    status = db.Column(db.String(30), default='watch')
    factors = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AdminMessage(db.Model):
    __tablename__ = 'admin_messages'
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    subject = db.Column(db.String(160))
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sender = db.relationship('User', foreign_keys=[sender_id], lazy=True)
    recipient = db.relationship('User', foreign_keys=[recipient_id], lazy=True)


class AdminSalary(db.Model):
    __tablename__ = 'admin_salaries'
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(30), default='mpesa')
    account_number = db.Column(db.String(120))
    work_start_date = db.Column(db.Date)
    status = db.Column(db.String(30), default='pending')
    paid_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    admin = db.relationship('User', lazy=True)


class MarketNews(db.Model):
    __tablename__ = 'market_news'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(220), nullable=False)
    body = db.Column(db.Text, nullable=False)
    product_name = db.Column(db.String(220))
    image_url = db.Column(db.String(500))
    source_url = db.Column(db.String(500))
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    region = db.Column(db.String(80), default='Kenya vs Worldwide')
    direction = db.Column(db.String(30), default='stagnant')
    generated_by = db.Column(db.String(40), default='market_intelligence')
    is_cleared = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    category = db.relationship('Category', lazy=True)


class CategoryFollow(db.Model):
    __tablename__ = 'category_follows'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    email_updates = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', lazy=True)
    category = db.relationship('Category', lazy=True)


class StorefrontFollow(db.Model):
    """A shopper following a shop, so they hear about its deals and clearances."""
    __tablename__ = 'storefront_follows'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'storefront_id', name='uq_storefront_follow'),
        db.Index('ix_storefront_follows_storefront', 'storefront_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    storefront_id = db.Column(db.Integer, db.ForeignKey('business_storefronts.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', lazy=True)
    storefront = db.relationship('BusinessStorefront', lazy=True)


class PriceAlert(db.Model):
    __tablename__ = 'price_alerts'
    __table_args__ = (
        db.Index('ix_price_alerts_user_status', 'user_id', 'status'),
        db.Index('ix_price_alerts_product_status', 'product_id', 'status'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    search_query = db.Column('query', db.String(240))
    target_price = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(30), default='active')
    last_notified_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', lazy=True)
    product = db.relationship('Product', lazy=True)

class MarketPriceCache(db.Model):
    __tablename__ = 'market_price_cache'
    id = db.Column(db.Integer, primary_key=True)
    cache_key = db.Column(db.String(240), unique=True, nullable=False, index=True)
    label = db.Column(db.String(220), nullable=False)
    category_name = db.Column(db.String(120))
    kenya_low = db.Column(db.Float, default=0.0)
    kenya_high = db.Column(db.Float, default=0.0)
    manufacturer_price = db.Column(db.Float, default=0.0)
    source = db.Column(db.String(220))
    source_url = db.Column(db.String(500))
    confidence = db.Column(db.String(60), default='cached_scan')
    payload = db.Column(db.Text)
    refreshed_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class CustomerNotification(db.Model):
    __tablename__ = 'customer_notifications'
    __table_args__ = (
        db.Index('ix_customer_notifications_user_read_created', 'user_id', 'is_read', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    title = db.Column(db.String(180), nullable=False)
    body = db.Column(db.Text, nullable=False)
    notification_type = db.Column(db.String(40), default='recommendation')
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', lazy=True)
    product = db.relationship('Product', lazy=True)


class Discount(db.Model):
    __tablename__ = 'discounts'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    discount_percent = db.Column(db.Float, nullable=False)
    reason = db.Column(db.String(200))
    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime)

    product = db.relationship('Product', lazy=True)
    creator = db.relationship('User', lazy=True)


class PromoCode(db.Model):
    """A referral code the MVP hands to one customer to share.

    The customer named by `owner_id` is the person being thanked; anyone else
    who spends `min_order_amount` or more gets `discount_percent` off their
    goods, and the owner is paid coins for the introduction. Delivery is never
    discounted, so the platform is not subsidising couriers.
    """
    __tablename__ = 'promo_codes'
    __table_args__ = (
        db.Index('ix_promo_codes_owner_active', 'owner_id', 'is_active'),
    )
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(24), unique=True, nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    discount_percent = db.Column(db.Float, default=10.0)
    min_order_amount = db.Column(db.Float, default=1000.0)
    owner_coins = db.Column(db.Integer, default=0)
    # Why this customer was picked. The MVP types it and it stays on the record.
    reason = db.Column(db.String(300))
    # Blank means the code keeps running; a number caps lifetime redemptions.
    max_redemptions = db.Column(db.Integer)
    times_used = db.Column(db.Integer, default=0)
    total_discount_given = db.Column(db.Float, default=0.0)
    total_coins_awarded = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    expires_at = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    owner = db.relationship('User', lazy=True, foreign_keys=[owner_id])
    creator = db.relationship('User', lazy=True, foreign_keys=[created_by])


class PromoCodeRedemption(db.Model):
    """One shopper's use of a code, written only once the order is paid.

    The unique index on (promo_code_id, order_id) is what makes awarding coins
    safe to retry: the M-Pesa callback and the status poll can both finalize the
    same order, and the second one loses the race rather than paying twice.
    """
    __tablename__ = 'promo_code_redemptions'
    __table_args__ = (
        db.UniqueConstraint('promo_code_id', 'order_id', name='uq_promo_redemption_order'),
        db.Index('ix_promo_redemptions_code_user', 'promo_code_id', 'user_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    promo_code_id = db.Column(db.Integer, db.ForeignKey('promo_codes.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    discount_amount = db.Column(db.Float, default=0.0)
    order_subtotal = db.Column(db.Float, default=0.0)
    coins_awarded = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    promo_code = db.relationship('PromoCode', lazy=True)
    order = db.relationship('Order', lazy=True)
    user = db.relationship('User', lazy=True)


class Setting(db.Model):
    __tablename__ = 'settings'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Configuration is read dozens of times per request (payment credentials, SMTP
    # details, feature flags, coin rates) and changes a few times a week. Without
    # a cache each page render is dozens of round trips to the same handful of
    # rows. TTL rather than explicit invalidation only, because several gunicorn
    # workers each hold their own copy: a write invalidates locally and the other
    # workers catch up within the TTL. Keep it short enough that an admin saving a
    # setting sees it take effect while they are still looking at the page.
    _cache = TTLCache(
        ttl_seconds=float_env('SETTING_CACHE_TTL_SECONDS', 30.0),
        max_entries=int_env('SETTING_CACHE_MAX_ENTRIES', 4096),
        name='settings',
    )

    @classmethod
    def cache_stats(cls):
        return cls._cache.stats()

    @classmethod
    def invalidate_cache(cls, key=None):
        if key is None:
            cls._cache.clear()
        else:
            cls._cache.invalidate(key)

    @classmethod
    def get(cls, key, default=''):
        """Read a setting, going to the database at most once per TTL per worker.

        The row's value is cached, never the resolved default, because the same
        key is read with different defaults from different call sites. A missing
        row is cached as None so that absent optional config (the common case for
        unset credentials) does not re-query on every read.
        """
        cached = cls._cache.lookup(key)
        if cached is not CACHE_MISS:
            return default if cached is None else cached
        row = cls.query.filter_by(key=key).first()
        value = row.value if row else None
        cls._cache.set(key, value)
        return default if value is None else value

    @classmethod
    def set(cls, key, value, commit=True):
        """Write a setting.

        ``commit`` exists because this used to commit unconditionally, which meant
        any caller writing a setting mid-flow also committed that flow's partial
        work. Callers inside a larger transaction pass commit=False and let the
        surrounding code decide when the unit of work is complete.
        """
        s = cls.query.filter_by(key=key).first()
        if s:
            s.value = str(value)
        else:
            s = cls(key=key, value=str(value))
            db.session.add(s)
        if commit:
            db.session.commit()
        # Invalidate after the write so a concurrent reader on this worker cannot
        # repopulate the cache from the pre-write state.
        cls._cache.invalidate(key)
        return s

    @classmethod
    def delete(cls, key, commit=True):
        cls.query.filter_by(key=key).delete(synchronize_session=False)
        if commit:
            db.session.commit()
        cls._cache.invalidate(key)

    @classmethod
    def bump_counter(cls, key, step=1, commit=True):
        """Increment a numeric setting and return the new value.

        Counters cannot go through ``get``: a cached read is by definition out of
        date, and read-then-write on a stale number silently loses increments.
        This reads the row directly with a row lock (a no-op on SQLite, a real
        lock on PostgreSQL) so two workers counting the same event both land.
        """
        query = cls.query.filter_by(key=key)
        try:
            row = query.with_for_update().first()
        except Exception:
            # Backends without row locking (or a session already in a state that
            # forbids it) still get the increment, just without the guarantee.
            db.session.rollback()
            row = cls.query.filter_by(key=key).first()
        try:
            current = int(float(row.value)) if row and row.value not in (None, '') else 0
        except (TypeError, ValueError):
            current = 0
        total = current + int(step)
        if row:
            row.value = str(total)
        else:
            db.session.add(cls(key=key, value=str(total)))
        if commit:
            db.session.commit()
        cls._cache.invalidate(key)
        return total


# ============================================================================
# RUNTIME INFRASTRUCTURE
#
# Three tables that exist to keep the request path fast and bounded. None of
# them hold business data: they can all be truncated on a running system
# without losing anything a customer would notice.
# ============================================================================

class EphemeralKV(db.Model):
    """Short-lived values that must be visible to every worker.

    The barcode-scanner handoff is the reason this exists: a phone posts a scan
    to whichever worker answers, and the till polls for it from a different
    worker, so an in-process dict cannot carry it. These used to be written into
    the settings table, one permanent row per user, which meant the config table
    grew with the user base and never shrank.

    Every row carries an expiry and the sweeper deletes what has passed it, so
    the table's size is bounded by how many handoffs are in flight rather than by
    how many have ever happened.
    """
    __tablename__ = 'ephemeral_kv'
    __table_args__ = (
        db.Index('ix_ephemeral_kv_expires', 'expires_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(160), unique=True, nullable=False)
    value = db.Column(db.Text)
    expires_at = db.Column(db.DateTime, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def is_expired(self):
        return (self.expires_at or datetime.utcnow()) <= datetime.utcnow()


class OutboundMessage(db.Model):
    """A queued email, SMS or notification fan-out.

    Anything that talks to a third party or writes an unbounded number of rows
    goes in here instead of running inside the request. An SMTP handshake is one
    to twenty seconds of a worker thread; a fan-out to every follower of a large
    shop is however many rows that shop has followers. Both used to happen while
    a customer waited, and both are the reason a single slow provider could take
    the whole site down.

    Retries are scheduled rather than immediate, backing off per attempt, so a
    provider outage drains slowly instead of hammering a service that is already
    struggling.
    """
    __tablename__ = 'outbound_messages'
    __table_args__ = (
        # The drain query: due work, oldest first.
        db.Index('ix_outbound_status_next_attempt', 'status', 'next_attempt_at'),
        db.Index('ix_outbound_channel_status', 'channel', 'status'),
        db.Index('ix_outbound_created', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    # email, sms, notification_fanout
    channel = db.Column(db.String(30), nullable=False)
    recipient = db.Column(db.String(240))
    subject = db.Column(db.String(300))
    body = db.Column(db.Text)
    # JSON blob for channels that need structured input (fan-out targets, etc).
    payload = db.Column(db.Text)
    # queued, sending, sent, failed, dead
    status = db.Column(db.String(20), default='queued', nullable=False)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    max_attempts = db.Column(db.Integer, default=5, nullable=False)
    next_attempt_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_error = db.Column(db.Text)
    # Set by the worker that claimed the row, so a crashed claim can be spotted.
    claimed_by = db.Column(db.String(120))
    claimed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    sent_at = db.Column(db.DateTime)

    def backoff_seconds(self):
        """Exponential, capped. Attempt 1 waits 30s, attempt 5 waits 8 minutes."""
        return min(480, 30 * (2 ** max(0, (self.attempts or 1) - 1)))


class JobLock(db.Model):
    """A lease that lets exactly one worker run a scheduled job.

    Background jobs were started inside the app process, so every gunicorn
    worker ran its own copy of every schedule - N workers meant N concurrent
    runs of the same job, N times the outbound scraping, and racing writes.

    A database lease rather than a file lock because the deployment can be more
    than one machine, and a file lock only coordinates the workers that happen to
    share a filesystem. The holder renews periodically; if it dies, the lease
    expires and another worker picks the job up without anyone intervening.
    """
    __tablename__ = 'job_locks'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    holder = db.Column(db.String(120))
    acquired_at = db.Column(db.DateTime)
    expires_at = db.Column(db.DateTime)
    last_run_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditLog(db.Model):
    """
    Audit log for tracking admin actions and security events
    """
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    action = db.Column(db.String(100), nullable=False, index=True)
    resource_type = db.Column(db.String(50), index=True)
    resource_id = db.Column(db.Integer, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    username = db.Column(db.String(80))
    ip_address = db.Column(db.String(45))  # IPv6 compatible
    user_agent = db.Column(db.String(500))
    details = db.Column(db.Text)  # JSON string for additional details
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship('User', lazy=True, foreign_keys=[user_id])

    def __repr__(self):
        return f'<AuditLog {self.action} on {self.resource_type}:{self.resource_id} by {self.username}>'


class SignupVerification(db.Model):
    __tablename__ = 'signup_verifications'
    __table_args__ = (
        db.Index('ix_signup_verifications_email_created', 'email', 'created_at'),
        db.Index('ix_signup_verifications_phone_created', 'phone', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(160), nullable=False)
    phone = db.Column(db.String(40))
    code_hash = db.Column(db.String(256), nullable=False)
    attempts = db.Column(db.Integer, default=0)
    expires_at = db.Column(db.DateTime, nullable=False)
    consumed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resume_token = db.Column(db.String(64), unique=True)
    username = db.Column(db.String(80))
    password_hash = db.Column(db.String(256))

    def set_code(self, code):
        self.code_hash = generate_password_hash(str(code))

    def check_code(self, code):
        return check_password_hash(self.code_hash, str(code))


class ShoppingCard(db.Model):
    __tablename__ = 'shopping_cards'
    __table_args__ = (
        db.Index('ix_shopping_cards_user_status', 'user_id', 'status'),
        db.Index('ix_shopping_cards_number', 'card_number'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    card_number = db.Column(db.String(32), unique=True, nullable=False)
    card_last4 = db.Column(db.String(4), nullable=False)
    pin_hash = db.Column(db.String(256), nullable=True)  # Nullable until customer sets PIN
    display_name = db.Column(db.String(160))
    status = db.Column(db.String(30), default='pending_pin')  # pending_pin, active, blocked, lost
    credit_balance = db.Column(db.Integer, default=0)  # 100 credits = KSh 1.00
    cash_balance = db.Column(db.Float, default=0.0)
    issue_fee_paid = db.Column(db.Float, default=0.0)
    issued_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    issued_at = db.Column(db.DateTime)
    printed_at = db.Column(db.DateTime)
    pin_set_at = db.Column(db.DateTime)  # When customer set their PIN
    pin_set_token = db.Column(db.String(64), unique=True)  # Token for PIN setup via SMS
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id], lazy=True)
    issuer = db.relationship('User', foreign_keys=[issued_by], lazy=True)

    def set_pin(self, pin):
        self.pin_hash = generate_password_hash(str(pin).zfill(4))

    def check_pin(self, pin):
        if not self.pin_hash or self.pin_hash == 'PENDING_PIN_SETUP':
            return False
        return check_password_hash(self.pin_hash, str(pin).zfill(4))


class ShoppingCardTransaction(db.Model):
    __tablename__ = 'shopping_card_transactions'
    __table_args__ = (
        db.Index('ix_card_transactions_card_created', 'card_id', 'created_at'),
        db.Index('ix_card_transactions_user_created', 'user_id', 'created_at'),
        db.Index('ix_card_transactions_type_created', 'transaction_type', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey('shopping_cards.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    transaction_type = db.Column(db.String(40), nullable=False)
    credit_amount = db.Column(db.Integer, default=0)
    cash_amount = db.Column(db.Float, default=0.0)
    balance_after_credits = db.Column(db.Integer, default=0)
    balance_after_cash = db.Column(db.Float, default=0.0)
    reference_type = db.Column(db.String(40))
    reference_id = db.Column(db.String(80))
    note = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    card = db.relationship('ShoppingCard', lazy=True)
    user = db.relationship('User', foreign_keys=[user_id], lazy=True)
    creator = db.relationship('User', foreign_keys=[created_by], lazy=True)


class CardAuthorizationRequest(db.Model):
    __tablename__ = 'card_authorization_requests'
    __table_args__ = (
        db.Index('ix_card_auth_card_created', 'card_id', 'created_at'),
        db.Index('ix_card_auth_status_created', 'status', 'created_at'),
        db.Index('ix_card_auth_token', 'authorization_token'),
    )
    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey('shopping_cards.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    pos_sale_id = db.Column(db.Integer, db.ForeignKey('point_of_sale_sales.id'), nullable=True)
    authorization_token = db.Column(db.String(64), unique=True, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    merchant_name = db.Column(db.String(160))
    pos_terminal_id = db.Column(db.String(80))
    phone_number = db.Column(db.String(20))
    status = db.Column(db.String(30), default='pending')  # pending, approved, declined, expired, cancelled
    user_response = db.Column(db.String(30))  # approved, declined
    response_at = db.Column(db.DateTime)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    card = db.relationship('ShoppingCard', lazy=True)
    user = db.relationship('User', lazy=True)
    pos_sale = db.relationship('PointOfSaleSale', foreign_keys=[pos_sale_id], lazy=True)


class KYCIdentityVerification(db.Model):
    __tablename__ = 'kyc_identity_verifications'
    __table_args__ = (
        db.UniqueConstraint('document_fingerprint', name='uq_kyc_document_fingerprint'),
        db.Index('ix_kyc_user_status_created', 'user_id', 'status', 'created_at'),
        db.Index('ix_kyc_provider_reference', 'provider', 'provider_reference'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    provider = db.Column(db.String(60), default='inbuilt')
    provider_reference = db.Column(db.String(160))
    document_type = db.Column(db.String(40), nullable=False)
    document_country = db.Column(db.String(80))
    document_fingerprint = db.Column(db.String(64), nullable=False)
    document_path = db.Column(db.String(500))
    selfie_path = db.Column(db.String(500))
    face_match_score = db.Column(db.Float, default=0.0)
    liveness_score = db.Column(db.Float, default=0.0)
    captcha_passed = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(30), default='pending')
    notes = db.Column(db.Text)
    reviewed_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    reviewed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id], lazy=True)
    reviewer = db.relationship('User', foreign_keys=[reviewed_by], lazy=True)


class PhoneOwnershipEvidence(db.Model):
    """What a seller submitted to prove a second-hand phone is theirs to sell.

    One row per attempt, not one per product: a rejection is immediately
    re-submittable, and the earlier attempts are the record of what was tried. The
    live verdict for a listing is its newest row.

    ``proof_fingerprint`` is unique for the same reason
    ``KYCIdentityVerification.document_fingerprint`` is - the same receipt
    photographed once and uploaded against six different phones is the cheapest
    fraud there is, and the constraint catches it in the database rather than in a
    check somebody has to remember to write. The column therefore means "this is
    the attempt that claimed this image", and is left NULL on later attempts by the
    same listing: a seller retaking a blurred IMEI photo sends the same receipt
    again, and that resubmission must not collide with its own earlier row.
    """
    __tablename__ = 'phone_ownership_evidence'
    __table_args__ = (
        db.UniqueConstraint('proof_fingerprint', name='uq_phone_evidence_proof_fingerprint'),
        # An IMEI is looked up on every submission to see whether it is already
        # listed, and the status is part of that question.
        db.Index('ix_phone_evidence_imei_status', 'imei', 'status'),
        db.Index('ix_phone_evidence_user_created', 'user_id', 'created_at'),
        db.Index('ix_phone_evidence_product_created', 'product_id', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    imei = db.Column(db.String(20), nullable=False)
    imei_photo_path = db.Column(db.String(500))
    proof_path = db.Column(db.String(500))
    proof_fingerprint = db.Column(db.String(64))
    imei_valid = db.Column(db.Boolean, default=False)
    uniqueness_ok = db.Column(db.Boolean, default=False)
    photo_score = db.Column(db.Float, default=0.0)
    proof_score = db.Column(db.Float, default=0.0)
    originality_score = db.Column(db.Float, default=0.0)
    total_score = db.Column(db.Float, default=0.0)
    # approved, auto_rejected, manual_second_review
    status = db.Column(db.String(30), default='pending')
    # JSON list of per-item reasons, so a rejection can say which item failed
    # instead of only that something did.
    notes = db.Column(db.Text)
    reviewed_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    reviewed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', lazy=True)
    user = db.relationship('User', foreign_keys=[user_id], lazy=True)
    reviewer = db.relationship('User', foreign_keys=[reviewed_by], lazy=True)

    @property
    def reasons(self):
        """The stored per-item reasons, always a list."""
        if not self.notes:
            return []
        try:
            parsed = json.loads(self.notes)
        except (ValueError, TypeError):
            return [self.notes]
        return parsed if isinstance(parsed, list) else [str(parsed)]


class Raffle(db.Model):
    __tablename__ = 'raffles'
    __table_args__ = (
        db.Index('ix_raffles_status_ends', 'status', 'ends_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    product_value = db.Column(db.Float, nullable=False)
    ticket_price = db.Column(db.Float, nullable=False)
    total_tickets = db.Column(db.Integer, nullable=False)
    min_participants = db.Column(db.Integer, default=100)
    client_product_fee_pct = db.Column(db.Float, default=25.0)
    tickets_sold = db.Column(db.Integer, default=0)
    status = db.Column(db.String(30), default='active')  # active, sold_out, drawing, completed, cancelled
    winner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    winner_ticket_number = db.Column(db.Integer, nullable=True)
    drawn_at = db.Column(db.DateTime, nullable=True)
    starts_at = db.Column(db.DateTime, default=datetime.utcnow)
    ends_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', lazy=True)
    seller = db.relationship('User', foreign_keys=[seller_id], lazy=True)
    winner = db.relationship('User', foreign_keys=[winner_id], lazy=True)
    tickets = db.relationship('RaffleTicket', backref='raffle', lazy=True)


class RaffleTicket(db.Model):
    __tablename__ = 'raffle_tickets'
    __table_args__ = (
        db.UniqueConstraint('raffle_id', 'ticket_number', name='uq_raffle_ticket_number'),
        db.Index('ix_raffle_tickets_user', 'user_id', 'raffle_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    raffle_id = db.Column(db.Integer, db.ForeignKey('raffles.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    ticket_number = db.Column(db.Integer, nullable=False)
    purchased_at = db.Column(db.DateTime, default=datetime.utcnow)
    mpesa_receipt = db.Column(db.String(50))

    user = db.relationship('User', lazy=True)


class CoinTransaction(db.Model):
    __tablename__ = 'coin_transactions'
    __table_args__ = (
        db.Index('ix_coin_transactions_user_created', 'user_id', 'created_at'),
        db.Index('ix_coin_transactions_type', 'coin_type'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    coin_type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(300))
    reference_id = db.Column(db.String(80))
    balance_after = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', lazy=True)


class CoinDailyCheckIn(db.Model):
    __tablename__ = 'coin_daily_checkins'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'check_in_date', name='uq_coin_checkin_user_date'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    check_in_date = db.Column(db.Date, nullable=False)
    streak_count = db.Column(db.Integer, default=1)
    coins_earned = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', lazy=True)


class Event(db.Model):
    __tablename__ = 'events'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    event_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime)
    location = db.Column(db.String(300))
    image_url = db.Column(db.String(500))
    offers = db.Column(db.Text)
    is_hot = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    ticket_price = db.Column(db.Float, default=0.0)
    ticket_types = db.Column(db.Text)  # JSON: [{"name":"general","price":500},{"name":"vip","price":2000}]
    max_tickets = db.Column(db.Integer, default=0)
    tickets_sold = db.Column(db.Integer, default=0)
    platform_fee_percent = db.Column(db.Float, default=10.0)

    creator = db.relationship('User', lazy=True)


# ========================================================================
# REVENUE / EARNING MODELS
# ========================================================================

class PromotedListing(db.Model):
    """Sellers pay to boost their product visibility in search/homepage."""
    __tablename__ = 'promoted_listings'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    plan = db.Column(db.String(30), default='basic')  # basic, premium, spotlight
    daily_rate = db.Column(db.Float, nullable=False)
    total_paid = db.Column(db.Float, default=0.0)
    impressions = db.Column(db.Integer, default=0)
    clicks = db.Column(db.Integer, default=0)
    starts_at = db.Column(db.DateTime, nullable=False)
    ends_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(30), default='pending_payment')
    mpesa_receipt = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', lazy=True)
    seller = db.relationship('User', lazy=True)


class AffiliateLink(db.Model):
    """Earn commission by sharing product links externally."""
    __tablename__ = 'affiliate_links'
    __table_args__ = (
        db.Index('ix_affiliate_links_user_product', 'user_id', 'product_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    clicks = db.Column(db.Integer, default=0)
    conversions = db.Column(db.Integer, default=0)
    total_earned = db.Column(db.Float, default=0.0)
    commission_percent = db.Column(db.Float, default=5.0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', lazy=True)
    product = db.relationship('Product', lazy=True)


class AffiliateConversion(db.Model):
    """Tracks each successful affiliate sale."""
    __tablename__ = 'affiliate_conversions'
    id = db.Column(db.Integer, primary_key=True)
    link_id = db.Column(db.Integer, db.ForeignKey('affiliate_links.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    order_amount = db.Column(db.Float, nullable=False)
    commission_earned = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(30), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    link = db.relationship('AffiliateLink', lazy=True)


class SellerSubscription(db.Model):
    """Monthly subscription for premium seller features."""
    __tablename__ = 'seller_subscriptions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    plan = db.Column(db.String(30), nullable=False)  # starter, professional, enterprise
    monthly_fee = db.Column(db.Float, nullable=False)
    features = db.Column(db.Text)
    starts_at = db.Column(db.DateTime, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    auto_renew = db.Column(db.Boolean, default=True)
    status = db.Column(db.String(30), default='active')
    mpesa_receipt = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', lazy=True)


class SponsoredBanner(db.Model):
    """Businesses pay for banner ad space on the platform."""
    __tablename__ = 'sponsored_banners'
    id = db.Column(db.Integer, primary_key=True)
    advertiser_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    advertiser_name = db.Column(db.String(160), nullable=False)
    advertiser_email = db.Column(db.String(160))
    advertiser_phone = db.Column(db.String(40))
    title = db.Column(db.String(200), nullable=False)
    image_url = db.Column(db.String(500))
    link_url = db.Column(db.String(500))
    placement = db.Column(db.String(40), default='homepage')
    daily_rate = db.Column(db.Float, nullable=False)
    total_paid = db.Column(db.Float, default=0.0)
    impressions = db.Column(db.Integer, default=0)
    clicks = db.Column(db.Integer, default=0)
    starts_at = db.Column(db.DateTime, nullable=False)
    ends_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(30), default='pending_payment')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    advertiser = db.relationship('User', lazy=True)


class EventTicket(db.Model):
    """Paid event tickets - platform takes commission."""
    __tablename__ = 'event_tickets'
    __table_args__ = (
        db.Index('ix_event_tickets_event_user', 'event_id', 'user_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    ticket_type = db.Column(db.String(60), default='general')
    price = db.Column(db.Float, nullable=False)
    platform_fee = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Integer, default=1)
    ticket_code = db.Column(db.String(40), unique=True)
    status = db.Column(db.String(30), default='valid')
    mpesa_receipt = db.Column(db.String(50))
    purchased_at = db.Column(db.DateTime, default=datetime.utcnow)

    event = db.relationship('Event', lazy=True)
    user = db.relationship('User', lazy=True)


class FeaturedPlacementBid(db.Model):
    """Businesses bid for premium homepage spotlight placement."""
    __tablename__ = 'featured_placement_bids'
    id = db.Column(db.Integer, primary_key=True)
    bidder_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    business_name = db.Column(db.String(200))
    bid_amount = db.Column(db.Float, nullable=False)
    duration_days = db.Column(db.Integer, default=7)
    placement_slot = db.Column(db.String(40), default='homepage_hero')
    status = db.Column(db.String(30), default='pending')
    starts_at = db.Column(db.DateTime)
    ends_at = db.Column(db.DateTime)
    mpesa_receipt = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    bidder = db.relationship('User', lazy=True)
    product = db.relationship('Product', lazy=True)


# How a service is offered, and therefore which fields it has and who takes the
# money. Six shapes rather than one, because the single shape this table started
# with was designed for a laundry: it asked every listing whether pickup was
# offered, so an event ticket told its buyer "No pickup offered, take to the
# location as directed".
#
# `fields` is a whitelist, not documentation. A field absent from a profile is
# absent from the form and absent from the page - not rendered as a negative, not
# hidden with CSS. That is the whole point of the table.
#
# Defined here rather than in main.py because pickup_display below has to agree
# with it, and a second copy over there is how the two would drift.
SERVICE_FULFILMENT_PROFILES = {
    'ticket': {
        'label': 'Ticketed event',
        # No contact step at all: a ticket needs no introduction, only a price.
        'flow': 'buy',
        'fields': ('event_starts_at', 'event_venue', 'tiers'),
        'pay_to': 'platform',
        'pay_when': 'upfront',
        'icon': 'fa-calendar-day',
        'blurb': 'Buyers pick a tier and pay on the platform. No linking desk.',
    },
    'dropoff': {
        'label': 'Drop off / collect',
        'flow': 'request',
        'fields': ('location', 'opening_hours', 'turnaround_note', 'pickup'),
        'pay_to': 'platform',
        'pay_when': 'after',
        'icon': 'fa-truck-pickup',
        'blurb': 'The client brings the work to you, or you collect it.',
    },
    'errand': {
        'label': 'Delivery / errand',
        'flow': 'request',
        'fields': ('location', 'service_area', 'delivery_fee',
                   'min_order_amount', 'pickup'),
        'pay_to': 'platform',
        'pay_when': 'upfront',
        'icon': 'fa-motorcycle',
        'blurb': 'Paid up front on the platform, goods and fee together.',
    },
    'visit': {
        'label': 'Appointment / visit',
        'flow': 'request',
        'fields': ('location', 'serves_at', 'opening_hours',
                   'appointment_required'),
        'pay_to': 'provider',
        'pay_when': 'after',
        'icon': 'fa-person-walking',
        'blurb': 'Paid directly to the provider after the service.',
    },
    'session': {
        'label': 'Session / hourly',
        'flow': 'request',
        'fields': ('rate_unit', 'serves_at', 'turnaround_note'),
        'pay_to': 'platform',
        'pay_when': 'after',
        'icon': 'fa-clock',
        'blurb': 'Scope is agreed in the thread, then paid on the platform.',
    },
    'tenancy': {
        'label': 'Rental / tenancy',
        'flow': 'request',
        'fields': ('location', 'deposit_amount', 'available_from', 'rate_unit',
                   'appointment_required'),
        'pay_to': 'provider',
        'pay_when': 'after',
        'icon': 'fa-key',
        'blurb': 'Viewing first; rent and deposit are paid to the landlord.',
    },
}

DEFAULT_SERVICE_PROFILE = 'dropoff'

# The eighteen seeded categories mapped to a shape. Read once at seed time and by
# the admin catalogue page; after that the catalogue row is the truth, so an admin
# who retags a category is not overwritten on the next boot.
SERVICE_PROFILE_BY_KEY = {
    'events_tickets': 'ticket',
    'laundry': 'dropoff',
    'printing': 'dropoff',
    'device_repair': 'dropoff',
    'cyber_services': 'dropoff',
    'books_stationery': 'dropoff',
    'food_delivery': 'errand',
    'grocery': 'errand',
    'parcel_courier': 'errand',
    'campus_errands': 'errand',
    'barber_beauty': 'visit',
    'fitness': 'visit',
    'health_wellness': 'visit',
    'cleaning': 'visit',
    'tutoring': 'session',
    'career': 'session',
    'student_gigs': 'session',
    'accommodation': 'tenancy',
}


def service_profile_spec(profile):
    """The profile record for a name, falling back rather than raising.

    A listing written before this column existed, or one whose catalogue row an
    admin deleted, still has to render. Falling back to dropoff shows one field
    too many; raising shows a 500.
    """
    return SERVICE_FULFILMENT_PROFILES.get(
        (profile or '').strip().lower(),
        SERVICE_FULFILMENT_PROFILES[DEFAULT_SERVICE_PROFILE])


def profile_has_field(profile, field):
    """Whether a profile carries a field at all. The whitelist, read one way."""
    return field in service_profile_spec(profile)['fields']


class ServiceListing(db.Model):
    """A service someone offers - laundry, printing, repairs, tutoring.

    Sold differently from a product on purpose. The client never receives the
    provider's number: they press "contact admin", an on-duty admin sees the
    request together with ``provider_phone``, and the admin introduces the two.
    ``provider_phone`` is therefore admin-only in every template that touches it -
    the whole design collapses if it renders once to a customer.

    ``service_direct_contact_enabled`` is the switch that changes the model later:
    with it on, clients contact providers themselves and listing stops being free,
    which is what ``listing_fee_*`` is for.

    ``fulfilment_profile`` decides which of the columns below exist for a given
    listing - see SERVICE_FULFILMENT_PROFILES above. It is copied onto the row
    rather than read through the catalogue every time, for two reasons: an admin
    retagging a category must not silently reshape listings their providers have
    already written, and the grid filters on it, which wants one indexed column
    and not a join.
    """
    __tablename__ = 'service_listings'
    id = db.Column(db.Integer, primary_key=True)
    provider_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    category = db.Column(db.String(80), nullable=False)
    # Catalogue key from ServiceCatalogueItem. category stays for rows written
    # before the catalogue existed, so nothing has to be backfilled to keep working.
    service_key = db.Column(db.String(60), index=True)
    price_type = db.Column(db.String(30), default='fixed')
    price = db.Column(db.Float, nullable=False)
    delivery_days = db.Column(db.Integer, default=3)
    image_url = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True)
    orders_completed = db.Column(db.Integer, default=0)
    rating = db.Column(db.Float, default=0.0)
    platform_commission = db.Column(db.Float, default=15.0)

    # Admins only. Never rendered to a client.
    provider_phone = db.Column(db.String(30))

    # Same four columns as Product (see Product.location_label) so the
    # location_display / has_location properties below are the same logic buyers
    # already see on a product card rather than a second dialect of it.
    location_label = db.Column(db.String(200))
    location_county = db.Column(db.String(100))
    location_lat = db.Column(db.Float)
    location_lng = db.Column(db.Float)

    # Pickup. Drives the third field of the chatbot's provider line, which is why
    # eta is free text: "today" and "tomorrow" are what a provider actually says.
    pickup_required = db.Column(db.Boolean, default=False)
    pickup_is_free = db.Column(db.Boolean, default=False)
    pickup_cost = db.Column(db.Float, default=0.0)
    pickup_eta = db.Column(db.String(60))
    # Platform courier does the pickup. The provider is billed for that leg, not
    # the client - a client who only wanted the service never sees a delivery fee.
    pickup_via_platform = db.Column(db.Boolean, default=False)

    # Only consulted while direct contact is on; free and admin-brokered otherwise.
    listing_fee_amount = db.Column(db.Float, default=0.0)
    listing_fee_paid = db.Column(db.Boolean, default=False)
    # Listed by the MVP or an admin, so exempt from the seller_listable filter.
    is_admin_listing = db.Column(db.Boolean, default=False)

    # --- how this one is offered and paid ---------------------------------
    # Blank on rows written before the profiles existed; `profile` below resolves
    # that to the default rather than making every reader remember to.
    fulfilment_profile = db.Column(db.String(20), index=True)
    # Defaulted from the profile at save time, then overridable per listing: a
    # barber who wants the platform to hold the money is allowed to say so, and a
    # profile default is a default rather than a rule.
    pay_to = db.Column(db.String(20), default='platform')      # platform | provider
    pay_when = db.Column(db.String(20), default='after')       # upfront | after

    opening_hours = db.Column(db.String(120))       # dropoff, visit
    turnaround_note = db.Column(db.String(120))     # dropoff, session
    service_area = db.Column(db.String(200))        # errand, visit
    delivery_fee = db.Column(db.Float, default=0.0)      # errand
    min_order_amount = db.Column(db.Float, default=0.0)  # errand
    # visit and session: at their place, at the client's, or both. Two booleans
    # rather than one enum because "both" is the common answer for a mobile barber.
    serves_at_provider = db.Column(db.Boolean, default=True)
    serves_at_client = db.Column(db.Boolean, default=False)
    appointment_required = db.Column(db.Boolean, default=False)  # visit, tenancy
    rate_unit = db.Column(db.String(20))            # hour|session|week|task|month
    deposit_amount = db.Column(db.Float, default=0.0)   # tenancy
    available_from = db.Column(db.DateTime)             # tenancy
    event_starts_at = db.Column(db.DateTime)            # ticket
    event_venue = db.Column(db.String(200))             # ticket

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    provider = db.relationship('User', lazy=True)
    # Ordered here so no caller has to remember to, and so the "from KES x" price
    # and the picker agree about which tier comes first.
    tiers = db.relationship('ServicePriceTier', lazy=True, cascade='all, delete-orphan',
                            order_by='ServicePriceTier.sort_order,ServicePriceTier.price')

    @property
    def profile(self):
        """This listing's profile name, resolved, never blank."""
        name = (self.fulfilment_profile or '').strip().lower()
        return name if name in SERVICE_FULFILMENT_PROFILES else DEFAULT_SERVICE_PROFILE

    @property
    def profile_spec(self):
        return service_profile_spec(self.profile)

    def has_field(self, field):
        """Whether this listing's profile carries a field at all.

        The one question every services template asks. `service.has_field('pickup')`
        rather than `service.profile in ('dropoff', 'errand')` so adding a profile
        is one edit to the table above and none to the templates.
        """
        return profile_has_field(self.profile, field)

    @property
    def pays_provider_direct(self):
        return (self.pay_to or self.profile_spec['pay_to']) == 'provider'

    @property
    def pays_upfront(self):
        return (self.pay_when or self.profile_spec['pay_when']) == 'upfront'

    @property
    def location_display(self):
        label = (self.location_label or self.location_county or '').strip()
        if label:
            return label
        if self.location_lat is not None and self.location_lng is not None:
            return f'{self.location_lat:.3f}, {self.location_lng:.3f}'
        return None

    @property
    def has_location(self):
        return bool(self.location_label or self.location_county
                    or (self.location_lat is not None and self.location_lng is not None))

    @property
    def pickup_display(self):
        """The pickup half of a provider line, or None where pickup is not a thing.

        None - not "no pickup" - for the four profiles that have no pickup concept.
        A ticket, a haircut, a tutoring hour and a rented room cannot be collected,
        and telling a ticket buyer "No pickup offered, take to the location as
        directed" was the exact complaint that produced the profiles. Every caller
        checks the return value, so an absent answer renders nothing at all rather
        than a negative one.

        One property rather than the same three-branch conditional in the detail
        page, the card and the chatbot formatter - three copies would drift, and a
        client told "pickup today" by one and "no pickup" by another has no way to
        know which is true.
        """
        if not self.has_field('pickup'):
            return None
        if not self.pickup_required:
            return 'No pickup offered, take to the location as directed'
        when = (self.pickup_eta or '').strip()
        if self.pickup_is_free:
            return f'free pickup {when}'.strip()
        if self.pickup_cost and self.pickup_cost > 0:
            return f'pickup {when} (KES {self.pickup_cost:,.0f})'.replace('  ', ' ').strip()
        return f'pickup {when}'.strip() if when else 'pickup available'

    @property
    def offer_display(self):
        """The one line that says how this service is offered, per profile.

        Replaces pickup_display on the cards and in the chatbot for the profiles
        that have no pickup, so every listing still says something useful about how
        it works - a ticket says when and where, a barber says whether they come to
        you - instead of the pickup line or a blank.
        """
        profile = self.profile
        if profile == 'ticket':
            when = self.event_starts_at.strftime('%d %b, %H:%M') if self.event_starts_at else None
            where = (self.event_venue or '').strip() or self.location_display
            return ' · '.join(part for part in (when, where) if part) or 'Tickets on sale'
        if profile == 'visit':
            if self.serves_at_client and self.serves_at_provider:
                return 'At their place or yours'
            if self.serves_at_client:
                return 'Comes to you'
            return f'At {self.location_display}' if self.location_display else 'At their premises'
        if profile == 'session':
            unit = (self.rate_unit or 'session').strip()
            where = 'online or in person' if self.serves_at_client else 'in person'
            return f'Per {unit}, {where}'
        if profile == 'tenancy':
            unit = (self.rate_unit or 'month').strip()
            extra = 'viewing first' if self.appointment_required else 'available now'
            return f'Per {unit}, {extra}'
        if profile == 'errand':
            area = (self.service_area or '').strip() or self.location_display
            if self.delivery_fee:
                fee = f'delivery KES {self.delivery_fee:,.0f}'
                return f'{area} · {fee}' if area else fee
            return area or 'Delivered to you'
        return self.pickup_display or 'Take to the location as directed'


class ServicePriceTier(db.Model):
    """One price band on a ticketed service - Regular, VIP, VVIP.

    Its own table rather than columns on the listing because the MVP asked for
    "different prices for the same" service and the number of bands is the
    provider's choice, not ours. ``name`` is free text so "VVIP table of four"
    works as well as "VIP".

    ``quantity_sold`` is incremented in the M-Pesa callback and nowhere else. It is
    the seat count, so the only moment it may move is the moment money arrives -
    incrementing it when the STK push is *sent* would let an abandoned prompt hold
    a seat forever.
    """
    __tablename__ = 'service_price_tiers'
    __table_args__ = (
        db.Index('ix_service_tiers_service_order', 'service_id', 'sort_order'),
    )
    id = db.Column(db.Integer, primary_key=True)
    service_id = db.Column(db.Integer, db.ForeignKey('service_listings.id'), nullable=False)
    name = db.Column(db.String(60), nullable=False)
    price = db.Column(db.Float, nullable=False)
    # 0 means unlimited - a free-entry event with tiers for seating still needs a
    # price band, and refusing to sell because nobody typed a capacity would be a
    # worse default than not capping.
    quantity_total = db.Column(db.Integer, default=0)
    quantity_sold = db.Column(db.Integer, default=0)
    max_per_order = db.Column(db.Integer, default=5)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=100)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    service = db.relationship('ServiceListing', lazy=True, overlaps='tiers')

    @property
    def is_unlimited(self):
        return not self.quantity_total or self.quantity_total <= 0

    @property
    def seats_left(self):
        """None when unlimited, so callers must distinguish it from zero."""
        if self.is_unlimited:
            return None
        return max(0, (self.quantity_total or 0) - (self.quantity_sold or 0))

    @property
    def sold_out(self):
        return self.seats_left == 0

    def can_take(self, quantity):
        """Whether this tier can still sell `quantity` seats."""
        try:
            wanted = int(quantity or 0)
        except (TypeError, ValueError):
            return False
        if wanted < 1 or wanted > max(1, self.max_per_order or 5):
            return False
        left = self.seats_left
        return left is None or wanted <= left


class ServiceOrder(db.Model):
    """Orders for freelance services.

    ``payment_status`` and ``checkout_request_id`` exist because this table used to
    record an order, platform revenue and a completed job with no payment behind any
    of it. Now a row starts life 'pending' with the Daraja checkout id on it, and the
    callback is the only thing that may mark it paid, take revenue or move a seat
    count. See service_order_for_checkout_id in main.py.
    """
    __tablename__ = 'service_orders'
    __table_args__ = (
        db.Index('ix_service_orders_client_created', 'client_id', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    service_id = db.Column(db.Integer, db.ForeignKey('service_listings.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    provider_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    platform_fee = db.Column(db.Float, default=0.0)
    provider_payout = db.Column(db.Float, default=0.0)
    requirements = db.Column(db.Text)
    status = db.Column(db.String(30), default='pending')
    due_date = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    mpesa_receipt = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # pending | paid | failed. Separate from `status`, which is the job's progress:
    # a paid job can still be in progress, and an unpaid one is not a job yet.
    payment_status = db.Column(db.String(20), default='pending')
    # The only thing a Daraja callback arrives holding, so it is indexed.
    checkout_request_id = db.Column(db.String(60), index=True)
    paid_at = db.Column(db.DateTime)
    # Copied off the listing at order time: a listing that later switches to
    # direct-to-provider must not retroactively change how a paid order was settled.
    pay_to = db.Column(db.String(20), default='platform')
    tier_id = db.Column(db.Integer, db.ForeignKey('service_price_tiers.id'))
    quantity = db.Column(db.Integer, default=1)
    ticket_code = db.Column(db.String(24), index=True)

    service = db.relationship('ServiceListing', lazy=True)
    client = db.relationship('User', foreign_keys=[client_id], lazy=True)
    provider = db.relationship('User', foreign_keys=[provider_id], lazy=True)
    tier = db.relationship('ServicePriceTier', lazy=True)

    @property
    def is_paid(self):
        return (self.payment_status or '') == 'paid'


class ServiceCatalogueItem(db.Model):
    """The set of services that may exist on the platform, owned by the admin.

    A table rather than a list in the source, for two reasons that matter to you:
    the admin can add a service that was never on the original eighteen without a
    deploy, and ``seller_listable`` lets them decide which of them a seller may
    choose. Admins are never filtered by that flag - they may list anything.

    Read on every services page, so it is cached in main.service_catalogue() and
    only ever queried once per worker per TTL.
    """
    __tablename__ = 'service_catalogue_items'
    __table_args__ = (
        db.Index('ix_service_catalogue_active_order', 'is_active', 'sort_order'),
    )
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(60), unique=True, nullable=False)
    label = db.Column(db.String(120), nullable=False)
    emoji = db.Column(db.String(16))
    # Off by default: a service the admin adds is not something sellers may list
    # until the admin says so. Opt-in is the safe direction for this switch.
    seller_listable = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=100)
    # Which shape a listing under this key takes - see SERVICE_FULFILMENT_PROFILES.
    # On the catalogue row so a category the admin adds gets a shape at the moment
    # they create it, with no deploy; copied onto each listing at save time so a
    # later retag does not reshape listings that already exist.
    fulfilment_profile = db.Column(db.String(20), default=DEFAULT_SERVICE_PROFILE)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship('User', lazy=True)

    @property
    def display(self):
        return f'{self.emoji} {self.label}'.strip() if self.emoji else self.label


class ServiceLinkRequest(db.Model):
    """One client asking to be introduced to one provider, through an admin.

    This is the record the linking desk works from. The client never sees the
    provider's number; the admin sees both sides and makes the introduction.

    ``channel`` records how the client was actually served, so "how often is
    nobody on duty" is answerable from the table instead of guessed - a row with
    channel='whatsapp' is a client we could not serve on the platform.
    """
    __tablename__ = 'service_link_requests'
    __table_args__ = (
        db.Index('ix_service_requests_status_created', 'status', 'created_at'),
        db.Index('ix_service_requests_client_created', 'client_id', 'created_at'),
        db.Index('ix_service_requests_service_created', 'service_id', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    service_id = db.Column(db.Integer, db.ForeignKey('service_listings.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    # Pre-filled with a nominated agent when there is one on duty. Routing, not
    # ownership: an open request stays claimable by any admin on duty.
    assigned_admin_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    # open | claimed | linked | closed | whatsapp_redirected
    status = db.Column(db.String(30), default='open')
    client_note = db.Column(db.Text)
    client_phone = db.Column(db.String(30))
    channel = db.Column(db.String(20), default='platform')  # platform | whatsapp
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    claimed_at = db.Column(db.DateTime)
    linked_at = db.Column(db.DateTime)
    closed_at = db.Column(db.DateTime)
    # When the provider was told a client is interested. Stamped whether the send
    # was automatic (nobody on duty) or an admin pressing the button, and checked
    # before sending, so the interest message goes out exactly once per request.
    provider_notified_at = db.Column(db.DateTime)

    service = db.relationship('ServiceListing', lazy=True)
    client = db.relationship('User', foreign_keys=[client_id], lazy=True)
    assigned_admin = db.relationship('User', foreign_keys=[assigned_admin_id], lazy=True)

    @property
    def is_open(self):
        return self.status in ('open', 'claimed')


class ServiceLinkMessage(db.Model):
    """A message on a link request thread.

    Purpose-built rather than reusing AdminMessage, which has no thread key: every
    reply there would mean scanning to reassemble a conversation, and this is a
    conversation a client refreshes while waiting.

    Three parties once a request is linked - client, admin, provider - so the
    sender's role is stored rather than derived. Deriving it means loading the
    request and its listing on a thread that is polled every twelve seconds by
    everyone watching it.
    """
    __tablename__ = 'service_link_messages'
    __table_args__ = (
        db.Index('ix_service_messages_request_created', 'request_id', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('service_link_requests.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    from_admin = db.Column(db.Boolean, default=False)
    from_provider = db.Column(db.Boolean, default=False)
    body = db.Column(db.Text, nullable=False)
    read_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sender = db.relationship('User', lazy=True)
    request = db.relationship('ServiceLinkRequest', lazy=True)


def generate_invoice_token():
    """Unguessable path segment for the client's pay page.

    The invoice link is emailed to someone who may have no account here, so the
    token is the only thing standing between a stranger and a named customer's
    billing details. token_urlsafe(32) is 256 bits from the OS CSPRNG - not
    uuid4().hex, which is what the rest of this file uses for order numbers and is
    the wrong tool for a bearer credential.
    """
    return secrets.token_urlsafe(32)


class Invoice(db.Model):
    """A payment request an admin issues to a client, delivered by email.

    Separate from PosSale on purpose. A POS sale is a record of money already
    taken across a counter; this is a request for money not yet paid, addressed to
    someone who may not have an account, and it therefore needs the things a
    receipt does not: a due date, a status that can go overdue, and a public token
    so the client can open and pay it from an email link.

    Totals are stored rather than recomputed from items at read time. An invoice is
    a statement of what was asked for on the day it was sent: if a rate or tax rate
    changes next month, a recomputed total would silently rewrite history on a
    document the client already has in their inbox.
    """
    __tablename__ = 'invoices'
    __table_args__ = (
        db.Index('ix_invoices_status_due', 'status', 'due_date'),
        db.Index('ix_invoices_client_created', 'client_id', 'created_at'),
        db.Index('ix_invoices_issuer_created', 'issued_by_id', 'created_at'),
        db.Index('ix_invoices_email_created', 'client_email', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(40), unique=True, nullable=False)
    # Unguessable, and unique so a token can never address two invoices.
    public_token = db.Column(db.String(64), unique=True, nullable=False,
                             default=generate_invoice_token)

    # The client. client_id is optional because an invoice may be raised for
    # someone who has never registered; the email is what the request is sent to
    # and is therefore the one contact field that is required.
    client_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    client_name = db.Column(db.String(160), nullable=False)
    client_email = db.Column(db.String(160), nullable=False)
    client_phone = db.Column(db.String(30))
    client_address = db.Column(db.Text)

    issued_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(200))
    reference = db.Column(db.String(80))
    notes = db.Column(db.Text)
    terms = db.Column(db.Text)
    currency = db.Column(db.String(8), default='KES')

    subtotal = db.Column(db.Float, default=0.0)
    discount_amount = db.Column(db.Float, default=0.0)
    tax_percent = db.Column(db.Float, default=0.0)
    tax_amount = db.Column(db.Float, default=0.0)
    total_amount = db.Column(db.Float, default=0.0)
    amount_paid = db.Column(db.Float, default=0.0)

    # draft | sent | viewed | partially_paid | paid | overdue | cancelled
    status = db.Column(db.String(30), default='draft')
    due_date = db.Column(db.Date)
    issued_at = db.Column(db.DateTime)
    sent_at = db.Column(db.DateTime)
    viewed_at = db.Column(db.DateTime)
    paid_at = db.Column(db.DateTime)
    cancelled_at = db.Column(db.DateTime)

    payment_method = db.Column(db.String(30))
    mpesa_receipt = db.Column(db.String(50))
    checkout_request_id = db.Column(db.String(80))
    # How many times the request has been emailed, so a client cannot be reminded
    # into a spam folder and an admin can see that a resend actually happened.
    email_sent_count = db.Column(db.Integer, default=0)
    last_email_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = db.relationship('InvoiceItem', backref='invoice', lazy=True,
                            order_by='InvoiceItem.sort_order',
                            cascade='all, delete-orphan')
    payments = db.relationship('InvoicePayment', backref='invoice', lazy=True,
                               order_by='InvoicePayment.created_at',
                               cascade='all, delete-orphan')
    client = db.relationship('User', foreign_keys=[client_id], lazy=True)
    issued_by = db.relationship('User', foreign_keys=[issued_by_id], lazy=True)

    @property
    def balance_due(self):
        return round(max(0.0, (self.total_amount or 0.0) - (self.amount_paid or 0.0)), 2)

    @property
    def is_settled(self):
        return self.status == 'paid' or self.balance_due <= 0.009

    @property
    def is_payable(self):
        """Whether the client may still pay this from the emailed link."""
        return self.status not in ('draft', 'cancelled') and not self.is_settled

    @property
    def is_overdue(self):
        """Derived, not trusted from status.

        status only becomes 'overdue' when something writes it, and nothing runs at
        midnight to do that. Reading the date means a client's page and an admin's
        list agree on the day it happens rather than the day a job next fires.
        """
        if not self.due_date or self.is_settled or self.status in ('draft', 'cancelled'):
            return False
        return self.due_date < datetime.utcnow().date()

    @property
    def status_display(self):
        if self.is_overdue:
            return 'overdue'
        return (self.status or 'draft').replace('_', ' ')


class InvoiceItem(db.Model):
    """One billed line. line_total is stored for the same reason invoice totals are."""
    __tablename__ = 'invoice_items'
    __table_args__ = (
        db.Index('ix_invoice_items_invoice_order', 'invoice_id', 'sort_order'),
    )
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=False)
    description = db.Column(db.String(300), nullable=False)
    quantity = db.Column(db.Float, default=1.0)
    unit_price = db.Column(db.Float, default=0.0)
    line_total = db.Column(db.Float, default=0.0)
    sort_order = db.Column(db.Integer, default=0)


class InvoicePayment(db.Model):
    """Each attempt and each settlement against an invoice.

    A log rather than a single amount_paid field, because an invoice can be paid in
    parts and an STK push can be tried three times before it lands. Without the log
    a client who paid twice and an M-Pesa receipt that arrived late are
    indistinguishable from a mistake, and money is the one thing on this platform
    that must never be reconstructed from a guess.
    """
    __tablename__ = 'invoice_payments'
    __table_args__ = (
        db.Index('ix_invoice_payments_invoice_created', 'invoice_id', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(30), default='mpesa')
    # pending | success | failed
    status = db.Column(db.String(20), default='pending')
    mpesa_receipt = db.Column(db.String(50))
    checkout_request_id = db.Column(db.String(80), index=True)
    phone = db.Column(db.String(30))
    note = db.Column(db.String(255))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    recorded_by = db.relationship('User', lazy=True)


class VendorOnboardingFee(db.Model):
    """One-time fee for sellers to start selling on the platform."""
    __tablename__ = 'vendor_onboarding_fees'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    fee_amount = db.Column(db.Float, nullable=False)
    plan = db.Column(db.String(30), default='standard')
    mpesa_receipt = db.Column(db.String(50))
    status = db.Column(db.String(30), default='pending')
    paid_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', lazy=True)


class PlatformRevenue(db.Model):
    """Tracks all platform earnings from various streams."""
    __tablename__ = 'platform_revenue'
    __table_args__ = (
        db.Index('ix_platform_revenue_stream_created', 'revenue_stream', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    revenue_stream = db.Column(db.String(60), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(300))
    reference_id = db.Column(db.String(80))
    reference_type = db.Column(db.String(40))
    payer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    payer = db.relationship('User', lazy=True)


# ============================================================================
# SHIPPING ZONES, QUOTES & DRIVER TRACKING
# ============================================================================

class ShippingZone(db.Model):
    """A named delivery zone with its own pricing rule.

    Flat zones (e.g. Central at KSh 120) match on county name. Anything not
    matched by an active zone falls through to per-km pricing. Admin-editable
    so competitive rates can change without a deploy.
    """
    __tablename__ = 'shipping_zones'
    __table_args__ = (
        db.Index('ix_shipping_zones_active_priority', 'is_active', 'priority'),
    )
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    pricing_mode = db.Column(db.String(20), default='flat')  # flat, per_km
    flat_fee = db.Column(db.Float, default=120.0)
    per_km_rate = db.Column(db.Float, default=3.0)
    minimum_fee = db.Column(db.Float, default=120.0)
    per_kg_rate = db.Column(db.Float, default=0.0)       # surcharge, 0 = inert
    free_over_amount = db.Column(db.Float, default=0.0)  # 0 = never free
    country = db.Column(db.String(100), default='Kenya')
    counties = db.Column(db.Text)  # comma-separated county names
    estimated_days_min = db.Column(db.Integer, default=1)
    estimated_days_max = db.Column(db.Integer, default=3)
    priority = db.Column(db.Integer, default=100)  # lower wins when several match
    is_active = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def county_list(self):
        return [c.strip() for c in (self.counties or '').split(',') if c.strip()]

    def covers_county(self, county):
        if not county:
            return False
        target = county.strip().lower()
        return any(c.lower() == target for c in self.county_list())


class ShippingQuote(db.Model):
    """Audit trail of every quote issued.

    Kept so a customer dispute can be answered with the exact inputs and rule
    that produced a price, and so pricing changes can be analysed over time.
    """
    __tablename__ = 'shipping_quotes'
    __table_args__ = (
        db.Index('ix_shipping_quotes_created', 'created_at'),
        db.Index('ix_shipping_quotes_order', 'order_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    zone_id = db.Column(db.Integer, db.ForeignKey('shipping_zones.id'), nullable=True)

    origin_label = db.Column(db.String(240))
    origin_lat = db.Column(db.Float)
    origin_lng = db.Column(db.Float)
    destination_label = db.Column(db.String(240))
    destination_lat = db.Column(db.Float)
    destination_lng = db.Column(db.Float)
    destination_county = db.Column(db.String(120))
    destination_country = db.Column(db.String(100), default='Kenya')

    distance_km = db.Column(db.Float, default=0.0)
    duration_minutes = db.Column(db.Float)
    weight_kg = db.Column(db.Float, default=0.0)

    pricing_mode = db.Column(db.String(20))  # flat, per_km
    base_amount = db.Column(db.Float, default=0.0)
    distance_amount = db.Column(db.Float, default=0.0)
    weight_amount = db.Column(db.Float, default=0.0)
    total_amount = db.Column(db.Float, default=0.0)

    # Which provider produced the distance, and whether it was a real route.
    routing_provider = db.Column(db.String(60))
    is_estimate = db.Column(db.Boolean, default=True)
    explanation = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    zone = db.relationship('ShippingZone', lazy=True)


class DriverProfile(db.Model):
    """A delivery driver, linked to a user account for login."""
    __tablename__ = 'driver_profiles'
    __table_args__ = (
        db.Index('ix_driver_profiles_status', 'status'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    display_name = db.Column(db.String(160))
    phone = db.Column(db.String(30))
    vehicle_type = db.Column(db.String(40), default='motorbike')
    vehicle_registration = db.Column(db.String(40))
    carrier_partner_id = db.Column(db.Integer, db.ForeignKey('carrier_partners.id'), nullable=True)
    status = db.Column(db.String(30), default='offline')  # offline, available, on_delivery
    is_active = db.Column(db.Boolean, default=True)

    # Denormalised latest position so the dispatch map is one cheap query.
    last_lat = db.Column(db.Float)
    last_lng = db.Column(db.Float)
    last_ping_at = db.Column(db.DateTime)
    last_accuracy_m = db.Column(db.Float)

    # Token the driver phone posts with; rotatable without touching login.
    tracking_token = db.Column(db.String(64), unique=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', lazy=True, foreign_keys=[user_id])
    carrier_partner = db.relationship('CarrierPartner', lazy=True)

    @property
    def has_fix(self):
        return self.last_lat is not None and self.last_lng is not None


class DriverLocationPing(db.Model):
    """Raw GPS breadcrumb from a driver phone.

    Retained for route replay and delivery-time analysis; prune on a schedule
    since this table grows fastest of anything in the system.
    """
    __tablename__ = 'driver_location_pings'
    __table_args__ = (
        db.Index('ix_driver_pings_driver_created', 'driver_id', 'created_at'),
        db.Index('ix_driver_pings_order_created', 'order_id', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    driver_id = db.Column(db.Integer, db.ForeignKey('driver_profiles.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    accuracy_m = db.Column(db.Float)
    speed_kph = db.Column(db.Float)
    heading = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    driver = db.relationship('DriverProfile', lazy=True)


class DeliveryAssignment(db.Model):
    """Links an order to the driver carrying it."""
    __tablename__ = 'delivery_assignments'
    __table_args__ = (
        db.Index('ix_delivery_assignments_driver_status', 'driver_id', 'status'),
        db.Index('ix_delivery_assignments_order', 'order_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    driver_id = db.Column(db.Integer, db.ForeignKey('driver_profiles.id'), nullable=False)
    # assigned, picked_up, in_transit, delivered, failed
    status = db.Column(db.String(30), default='assigned')
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    picked_up_at = db.Column(db.DateTime)
    delivered_at = db.Column(db.DateTime)

    # Destination snapshot so ETA survives an edit to the order address.
    destination_lat = db.Column(db.Float)
    destination_lng = db.Column(db.Float)
    destination_label = db.Column(db.String(240))

    eta_minutes = db.Column(db.Float)
    eta_updated_at = db.Column(db.DateTime)
    distance_remaining_km = db.Column(db.Float)
    notes = db.Column(db.Text)

    order = db.relationship('Order', lazy=True, backref='delivery_assignments')
    driver = db.relationship('DriverProfile', lazy=True, backref='assignments')
