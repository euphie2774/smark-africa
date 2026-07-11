"""
Input validation schemas using Marshmallow
Security improvements for SMARKAFRICA
"""
import re
from marshmallow import Schema, fields, validates, validates_schema, ValidationError


class PasswordValidator:
    """Strong password validation"""
    @staticmethod
    def validate(password):
        if len(password) < 8:
            raise ValidationError('Password must be at least 8 characters long')
        if not re.search(r'[A-Z]', password):
            raise ValidationError('Password must contain at least one uppercase letter')
        if not re.search(r'[a-z]', password):
            raise ValidationError('Password must contain at least one lowercase letter')
        if not re.search(r'[0-9]', password):
            raise ValidationError('Password must contain at least one number')
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            raise ValidationError('Password must contain at least one special character (!@#$%^&*(),.?":{}|<>)')
        return True


class RegisterSchema(Schema):
    username = fields.Str(required=True, validate=lambda x: 3 <= len(x) <= 80)
    email = fields.Email(required=True)
    phone = fields.Str(allow_none=True)
    password = fields.Str(required=True, validate=PasswordValidator.validate)
    confirm_password = fields.Str(required=True)

    @validates_schema
    def validate_passwords_match(self, data, **kwargs):
        if data.get('password') != data.get('confirm_password'):
            raise ValidationError('Passwords do not match', field_name='confirm_password')


class LoginSchema(Schema):
    username = fields.Str(required=True)
    password = fields.Str(required=True)


class ProductSchema(Schema):
    name = fields.Str(required=True, validate=lambda x: 1 <= len(x) <= 200)
    description = fields.Str(required=True)
    short_description = fields.Str(allow_none=True, validate=lambda x: not x or len(x) <= 300)
    category_id = fields.Int(allow_none=True)
    buying_price = fields.Float(required=True, validate=lambda x: x >= 0)
    selling_price = fields.Float(required=True, validate=lambda x: x >= 0)
    is_digital = fields.Bool()
    stock = fields.Int(validate=lambda x: x >= 0)
    weight_kg = fields.Float(validate=lambda x: x >= 0)
    is_featured = fields.Bool()
    is_active = fields.Bool()


class ReviewSchema(Schema):
    rating = fields.Int(required=True, validate=lambda x: 1 <= x <= 5)
    comment = fields.Str(allow_none=True, validate=lambda x: not x or len(x) <= 1000)


class CartSchema(Schema):
    product_id = fields.Int(required=True)
    quantity = fields.Int(required=True, validate=lambda x: 1 <= x <= 99)


class CheckoutSchema(Schema):
    phone = fields.Str(required=True)
    phone_country_code = fields.Str(required=True)
    shipping_address = fields.Str(allow_none=True)
    shipping_country = fields.Str(allow_none=True)
    shipping_city = fields.Str(allow_none=True)
    shipping_state = fields.Str(allow_none=True)
    delivery_method = fields.Str(allow_none=True)
    pickup_station = fields.Str(allow_none=True)


class FeedbackSchema(Schema):
    experience_rating = fields.Int(required=True, validate=lambda x: 1 <= x <= 5)
    satisfaction_rating = fields.Int(required=True, validate=lambda x: 1 <= x <= 5)
    improvement_text = fields.Str(allow_none=True, validate=lambda x: not x or len(x) <= 5000)


class ShippingCostSchema(Schema):
    shipping_rate_id = fields.Int(allow_none=True)
    weight_kg = fields.Float(validate=lambda x: x >= 0)
    country = fields.Str(allow_none=True)
    state = fields.Str(allow_none=True)
    city = fields.Str(allow_none=True)


def validate_request(schema_class, data):
    """Helper function to validate request data"""
    schema = schema_class()
    try:
        return schema.load(data), None
    except ValidationError as err:
        return None, err.messages
