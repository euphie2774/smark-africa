import uuid
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

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
    admin_level = db.Column(db.String(20), default='user')  # user, admin, mvp
    seller_status = db.Column(db.String(30), default='buyer')  # buyer, pending, verified, rejected, frozen
    country = db.Column(db.String(80))
    bank_card_last4 = db.Column(db.String(4))
    bank_card_token = db.Column(db.String(200))
    verification_status = db.Column(db.String(30), default='not_submitted')
    verification_notes = db.Column(db.Text)
    frozen_funds = db.Column(db.Float, default=0.0)
    salary_payment_method = db.Column(db.String(30), default='mpesa')
    salary_account_number = db.Column(db.String(120))
    work_start_date = db.Column(db.Date)
    ai_training_coins = db.Column(db.Integer, default=0)
    is_verified_seller = db.Column(db.Boolean, default=False)
    verified_seller_at = db.Column(db.DateTime)
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
    sale_mode = db.Column(db.String(20), default='direct')  # direct, bid
    bid_price = db.Column(db.Float, default=0.0)
    product_condition = db.Column(db.String(30), default='new')  # new, second_hand, thrifted, refurbished
    review_status = db.Column(db.String(30), default='approved')
    commission_percent = db.Column(db.Float, default=15.0)
    admin_priority = db.Column(db.Boolean, default=False)
    is_hot_sale = db.Column(db.Boolean, default=False)
    hot_sale_started_at = db.Column(db.DateTime)
    is_original_source = db.Column(db.Boolean, default=False)

    # Product type
    is_digital = db.Column(db.Boolean, default=False)
    file_path = db.Column(db.String(500))  # Path to digital product file
    file_size = db.Column(db.Integer)  # File size in bytes
    first_page_preview = db.Column(db.Boolean, default=True)

    # Physical product
    stock = db.Column(db.Integer, default=0)
    weight_kg = db.Column(db.Float, default=0.0)  # For shipping calc

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
    )
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(50), unique=True, nullable=False, default=generate_order_number)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    # Payment
    amount_paid = db.Column(db.Float, nullable=False)
    shipping_cost = db.Column(db.Float, default=0.0)
    discount_amount = db.Column(db.Float, default=0.0)
    mpesa_receipt = db.Column(db.String(100))
    mpesa_phone = db.Column(db.String(20))
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
    is_active = db.Column(db.Boolean, default=True)
    last_run_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


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
    commission_percent = db.Column(db.Float, default=10.0)
    status = db.Column(db.String(30), default='pending_review')
    verification_notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime)

    owner = db.relationship('User', lazy=True)


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


class Setting(db.Model):
    __tablename__ = 'settings'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get(cls, key, default=''):
        s = cls.query.filter_by(key=key).first()
        return s.value if s else default

    @classmethod
    def set(cls, key, value):
        s = cls.query.filter_by(key=key).first()
        if s:
            s.value = str(value)
        else:
            s = cls(key=key, value=str(value))
            db.session.add(s)
        db.session.commit()
        return s


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
