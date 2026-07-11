from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, TextAreaField, FloatField, IntegerField, BooleanField, SelectField, FileField, HiddenField
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])

class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    phone = StringField('Phone', validators=[Optional()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired()])

class ProductForm(FlaskForm):
    name = StringField('Product Name', validators=[DataRequired(), Length(max=200)])
    description = TextAreaField('Description', validators=[DataRequired()])
    short_description = StringField('Short Description', validators=[Optional(), Length(max=300)])
    category_id = SelectField('Category', coerce=int, validators=[Optional()])
    buying_price = FloatField('Buying Price', validators=[DataRequired(), NumberRange(min=0)])
    selling_price = FloatField('Selling Price', validators=[DataRequired(), NumberRange(min=0)])
    is_digital = BooleanField('Digital Product')
    stock = IntegerField('Stock Quantity', validators=[Optional(), NumberRange(min=0)], default=0)
    weight_kg = FloatField('Weight (kg)', validators=[Optional(), NumberRange(min=0)], default=0.0)
    is_featured = BooleanField('Featured Product')
    is_active = BooleanField('Active', default=True)

class SettingsForm(FlaskForm):
    site_name = StringField('Site Name')
    site_description = TextAreaField('Site Description')
    terms_and_conditions = TextAreaField('Terms & Conditions')
    vision = TextAreaField('Vision')
    mission = TextAreaField('Mission')
    about_text = TextAreaField('About Us')
    contact_email = StringField('Contact Email')
    contact_phone = StringField('Contact Phone')
    daraja_consumer_key = StringField('Daraja Consumer Key')
    daraja_consumer_secret = StringField('Daraja Consumer Secret')
    daraja_passkey = StringField('Daraja Passkey')
    daraja_shortcode = StringField('Daraja Shortcode')
    daraja_env = SelectField('Daraja Environment', choices=[('sandbox','Sandbox'),('production','Production')])
    mail_server = StringField('SMTP Server')
    mail_port = StringField('SMTP Port')
    mail_username = StringField('SMTP Username')
    mail_password = StringField('SMTP Password')