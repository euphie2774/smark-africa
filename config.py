import os
from logging.config import dictConfig


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _database_url(required=False):
    value = os.environ.get('DATABASE_URL')
    if value:
        return value
    if required:
        raise RuntimeError('DATABASE_URL is required when FLASK_ENV=production')
    return 'sqlite:///smarkafrica.db'


class Config:
    # Security: SECRET_KEY must be set in environment, no fallback
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        import secrets
        SECRET_KEY = secrets.token_hex(32)
        if os.environ.get('FLASK_ENV') == 'production':
            raise RuntimeError('SECRET_KEY environment variable must be set in production')

    SQLALCHEMY_DATABASE_URI = _database_url(os.environ.get('FLASK_ENV') == 'production')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'connect_args': {'timeout': 30} if SQLALCHEMY_DATABASE_URI.startswith('sqlite') else {},
    }
    MAX_CONTENT_LENGTH = 30 * 1024 * 1024
    PRODUCT_IMAGE_MAX_CONTENT_LENGTH = 5 * 1024 * 1024
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None

    # Rate limiting
    RATELIMIT_STORAGE_URL = os.environ.get('REDIS_URL', 'memory://')
    RATELIMIT_STRATEGY = 'fixed-window'
    RATELIMIT_HEADERS_ENABLED = True

    CACHE_TYPE = os.environ.get('CACHE_TYPE', 'RedisCache' if os.environ.get('REDIS_URL') else 'SimpleCache')
    CACHE_REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    CACHE_DEFAULT_TIMEOUT = int(os.environ.get('CACHE_DEFAULT_TIMEOUT', '300'))

    LOG_DIR = os.environ.get('LOG_DIR', os.path.join(BASE_DIR, 'logs'))
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
    LOGGING = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'default': {'format': '%(asctime)s %(levelname)s [%(name)s] %(message)s'}
        },
        'handlers': {
            'stdout': {'class': 'logging.StreamHandler', 'formatter': 'default', 'level': LOG_LEVEL},
            'file': {
                'class': 'logging.handlers.RotatingFileHandler',
                'formatter': 'default',
                'filename': os.path.join(LOG_DIR, 'smarkafrica.log'),
                'maxBytes': 5 * 1024 * 1024,
                'backupCount': 5,
                'level': LOG_LEVEL,
            },
        },
        'root': {'handlers': ['stdout', 'file'], 'level': LOG_LEVEL},
    }

    DARAJA_CONSUMER_KEY = os.environ.get('DARAJA_CONSUMER_KEY', '')
    DARAJA_CONSUMER_SECRET = os.environ.get('DARAJA_CONSUMER_SECRET', '')
    DARAJA_PASSKEY = os.environ.get('DARAJA_PASSKEY', '')
    DARAJA_SHORTCODE = os.environ.get('DARAJA_SHORTCODE', '174379')
    DARAJA_ENV = os.environ.get('DARAJA_ENV', 'sandbox')

    FLUTTERWAVE_PUBLIC_KEY = os.environ.get('FLUTTERWAVE_PUBLIC_KEY', '')
    FLUTTERWAVE_SECRET_KEY = os.environ.get('FLUTTERWAVE_SECRET_KEY', '')
    FLUTTERWAVE_ENCRYPTION_KEY = os.environ.get('FLUTTERWAVE_ENCRYPTION_KEY', '')
    FLUTTERWAVE_ENV = os.environ.get('FLUTTERWAVE_ENV', 'sandbox')

    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', 'your-email@gmail.com')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', 'your-app-password')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@smarkafrica.com')

    # Africa's Talking SMS gateway
    AFRICASTALKING_USERNAME = os.environ.get('AFRICASTALKING_USERNAME', '')
    AFRICASTALKING_API_KEY = os.environ.get('AFRICASTALKING_API_KEY', '')
    AFRICASTALKING_SENDER_ID = os.environ.get('AFRICASTALKING_SENDER_ID', '')

    # Admin credentials - must be set in environment
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD')
    ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'admin@smarkafrica.com')

    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        if os.environ.get('FLASK_ENV') == 'production':
            raise RuntimeError('ADMIN_USERNAME and ADMIN_PASSWORD must be set in production')
        # Development fallback only
        ADMIN_USERNAME = ADMIN_USERNAME or 'admin'
        ADMIN_PASSWORD = ADMIN_PASSWORD or 'DevAdmin123!@#'


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = 'https'


def init_logging(config_object=Config):
    os.makedirs(config_object.LOG_DIR, exist_ok=True)
    dictConfig(config_object.LOGGING)


def selected_config():
    if os.environ.get('FLASK_ENV') == 'production':
        if not os.environ.get('DATABASE_URL'):
            raise RuntimeError('DATABASE_URL is required when FLASK_ENV=production')
        return ProductionConfig
    return DevelopmentConfig


