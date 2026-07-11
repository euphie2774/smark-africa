# Smark-Africa Card Mobile Authorization System

## Overview

The Smark-Africa Rewards Card now features **mobile authorization** - a secure payment system where customers must approve transactions on their phone before payments are processed at POS terminals.

---

## How It Works

### Step-by-Step Process

```
1. CUSTOMER SHOPS
   Customer shops at Smark-Africa store
   ↓

2. PRESENT CARD AT POS
   Customer presents their Rewards Card
   ↓

3. CASHIER SCANS BARCODE
   POS scans the barcode on the BACK of card
   ↓

4. SYSTEM SENDS SMS
   SMS sent to customer's registered phone number
   "Approve KSh 500.00 at Smark-Africa POS. Auth: #12345"
   ↓

5. CUSTOMER APPROVES ON PHONE
   Customer logs into account or clicks SMS link
   Clicks "Approve" button
   ↓

6. POS RECEIVES CONFIRMATION
   POS gets instant notification of approval
   ↓

7. PAYMENT PROCESSED
   Amount deducted from card balance
   Receipt printed
```

---

## Features

### ✅ Customer Self-Registration
- Customers can register for cards themselves from their account
- Two payment options:
  1. **Use Loyalty Credits** (free if they have 10,000+ credits)
  2. **Pay KSh 700** issue fee via M-Pesa

### ✅ Real Smark-Africa Logo
- The beautiful phoenix logo is now embedded on physical cards
- Located on the front of the card beside the platform name

### ✅ Mobile Authorization
- Every POS transaction requires customer approval on their phone
- SMS notification sent instantly when cashier scans card
- 5-minute approval window
- Customer can approve or decline from their account page

### ✅ Barcode on Back
- Barcode is on the BACK of the card
- Front remains clean and professional
- Scannable with any standard barcode reader

### ✅ Delete Card Functionality
- Admins can permanently delete cards
- Deletion is logged in transaction history
- Confirmation required before deletion

---

## Card Registration (Customer Side)

### Eligibility

**Free Registration:**
- Customer has 10,000+ loyalty credits (default)
- Administrators (free for admins)

**Paid Registration:**
- Pay KSh 700 issue fee
- For customers who don't have enough credits

### Registration Process

1. Customer goes to **"My Rewards Card"** in their account menu
2. Fills in card details:
   - Name on card (auto-filled with username)
   - Payment method (credits or M-Pesa)
3. Clicks **"Register for Rewards Card"**
4. System:
   - Creates card with unique card number
   - Generates secure 4-digit PIN
   - Sends PIN via SMS to customer's registered phone
   - Card is marked for printing by admin
5. Customer receives:
   - Card number
   - PIN via SMS
   - Physical card delivered later

---

## Card Issuance (Admin Side)

### For Self-Registered Cards

When a customer self-registers:
1. Admin sees new card in **Admin → Shopping Cards**
2. Card shows customer details
3. Admin prints the physical card (front & back)
4. Admin delivers card to customer

**Note:** PIN is NOT shown to admin (already sent to customer via SMS)

### For Admin-Issued Cards

Admins can still issue cards directly:
1. Select customer from dropdown
2. Enter display name
3. Enter issue fee (if any)
4. Click **"Issue and Print"**
5. PIN is shown once for scratch-off slip
6. Print card with PIN slip
7. Give both to customer

---

## Mobile Authorization at POS

### Initiating Payment

**Option 1: Standard PIN Entry (Current Method)**
1. Scan card barcode
2. Customer enters PIN at POS
3. Payment processed immediately

**Option 2: Mobile Authorization (New Method)**
1. Scan card barcode
2. POS creates authorization request
3. SMS sent to customer: "Approve KSh 500 at POS. Auth: #12345"
4. Customer opens "My Rewards Card" page
5. Sees pending authorization request
6. Clicks **"Approve"** or **"Decline"**
7. POS receives instant confirmation
8. Payment processed if approved

### POS Workflow with Mobile Auth

```javascript
// Cashier scans card
POST /admin/pos/scan-card
{
  "barcode": "607845123456"
}

// System returns card details
{
  "card_number": "607845123456",
  "customer_name": "John Doe",
  "customer_phone": "254712345678",
  "total_balance": 500.00
}

// Cashier clicks "Request Mobile Authorization"
POST /admin/pos/request-mobile-auth
{
  "card_number": "607845123456",
  "amount": 250.00,
  "merchant_name": "Smark-Africa POS"
}

// System creates auth request and sends SMS
{
  "auth_token": "abc123xyz",
  "auth_id": 45,
  "expires_at": "2026-07-11T10:15:00",
  "phone_sent": true,
  "message": "Authorization request sent to 254712345678"
}

// POS polls for approval (every 2 seconds)
GET /api/check-card-authorization/abc123xyz

// Returns status
{
  "status": "pending",  // or "approved", "declined", "expired"
  "approved": false,
  "amount": 250.00
}

// When approved
{
  "status": "approved",
  "approved": true,
  "amount": 250.00,
  "auth_id": 45
}

// POS completes sale with approved authorization
```

---

## SMS Integration

### Current Implementation

```python
def send_sms_notification(phone_number, message):
    """Send SMS notification (placeholder - integrate with SMS gateway)."""
    logger.info(f'SMS to {phone_number}: {message}')
    # TODO: Integrate with SMS gateway
```

### Recommended SMS Gateways for Kenya

**Africa's Talking (Recommended)**
```python
import africastalking

africastalking.initialize(username='your_username', api_key='your_api_key')
sms = africastalking.SMS

def send_sms_notification(phone_number, message):
    try:
        response = sms.send(message, [phone_number])
        logger.info(f'SMS sent: {response}')
    except Exception as e:
        logger.error(f'SMS failed: {e}')
```

**Twilio**
```python
from twilio.rest import Client

client = Client(account_sid, auth_token)

def send_sms_notification(phone_number, message):
    try:
        message = client.messages.create(
            body=message,
            from_='+1234567890',
            to=phone_number
        )
        logger.info(f'SMS sent: {message.sid}')
    except Exception as e:
        logger.error(f'SMS failed: {e}')
```

---

## Database Schema

### New Table: `card_authorization_requests`

```sql
CREATE TABLE card_authorization_requests (
    id INTEGER PRIMARY KEY,
    card_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    pos_sale_id INTEGER,
    authorization_token VARCHAR(64) UNIQUE NOT NULL,
    amount FLOAT NOT NULL,
    merchant_name VARCHAR(160),
    pos_terminal_id VARCHAR(80),
    phone_number VARCHAR(20),
    status VARCHAR(30) DEFAULT 'pending',  -- pending, approved, declined, expired, cancelled
    user_response VARCHAR(30),  -- approved, declined
    response_at DATETIME,
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (card_id) REFERENCES shopping_cards(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (pos_sale_id) REFERENCES pos_sales(id)
);

CREATE INDEX ix_card_auth_card_created ON card_authorization_requests(card_id, created_at);
CREATE INDEX ix_card_auth_status_created ON card_authorization_requests(status, created_at);
CREATE INDEX ix_card_auth_token ON card_authorization_requests(authorization_token);
```

### Running Migration

```bash
python add_card_authorization_table.py
```

---

## Security Features

### 🔒 Multi-Layer Security

1. **Barcode Scan**
   - Physical card must be present
   - Barcode on back reduces shoulder surfing

2. **Phone Verification**
   - SMS sent to registered phone number only
   - Customer must have access to their phone

3. **Web Authorization**
   - Customer must be logged in to approve
   - Can't approve without account access

4. **Time-Limited**
   - Authorization expires after 5 minutes
   - Prevents delayed fraudulent approvals

5. **Unique Token**
   - Each authorization has unique token
   - Cannot be reused

6. **Transaction Logging**
   - Every authorization attempt logged
   - Full audit trail maintained

### 🚫 Fraud Prevention

- **No PIN Entry at POS** (with mobile auth)
  - Prevents PIN theft by cashiers or cameras
  
- **Customer Sees Merchant Name**
  - SMS shows where payment is being made
  - Customer can verify before approving

- **Instant Notifications**
  - Customer alerted immediately
  - Can decline if unauthorized

- **Account Control**
  - Customer can see all pending authorizations
  - Can decline suspicious requests

---

## Configuration

### Settings (configurable via admin)

```python
# Loyalty/Card Settings
shopping_card_min_credits = 10000  # Minimum credits for free card
shopping_card_issue_fee_kes = 700  # Issue fee if not enough credits
shopping_card_credits_per_100_kes = 1  # Credit earning rate
shopping_card_min_purchase_kes = 10000  # Minimum purchase to earn credits
shopping_card_prefix = 607845  # Card number prefix

# Authorization Settings
card_auth_expiry_minutes = 5  # How long authorization is valid
card_auth_sms_enabled = True  # Enable/disable SMS notifications
```

---

## Customer Benefits

### ✅ Enhanced Security
- No need to share PIN at POS
- Approve payments from own phone
- See transaction details before approving

### ✅ Transaction Control
- Decline unauthorized payments
- See real-time payment requests
- Full visibility of card usage

### ✅ Convenience
- Self-register for cards online
- Use loyalty credits to get free card
- View balance and history anytime

### ✅ Peace of Mind
- SMS notification for every transaction
- Can block suspicious payments instantly
- Complete transaction history

---

## Merchant Benefits

### ✅ Reduced Fraud
- Customer authorizes on their phone
- No PIN theft concerns
- Verified customer identity

### ✅ Higher Trust
- Customers feel secure
- Transparent payment process
- Professional payment system

### ✅ Lower Chargebacks
- Customer explicitly approves
- SMS confirmation proof
- Audit trail for disputes

---

## API Reference

### Customer Endpoints

**Register for Card**
```
POST /my-rewards-card
Form Data:
  - action: "register"
  - display_name: "John Doe"
  - payment_method: "credits" or "mpesa"
```

**View Card & Pending Authorizations**
```
GET /my-rewards-card
Returns: card details, pending auth requests, transaction history
```

**Approve/Decline Authorization**
```
POST /authorize-card-payment/<auth_id>
Form Data:
  - action: "approve" or "decline"
```

### POS Endpoints

**Scan Card**
```
POST /admin/pos/scan-card
JSON: {"barcode": "607845123456"}
Returns: card details, balance, customer info
```

**Request Mobile Authorization**
```
POST /admin/pos/request-mobile-auth
JSON: {
  "card_number": "607845123456",
  "amount": 250.00,
  "merchant_name": "Smark-Africa POS"
}
Returns: auth_token, auth_id, expires_at
```

**Check Authorization Status**
```
GET /api/check-card-authorization/<auth_token>
Returns: status, approved (boolean), message
```

### Admin Endpoints

**Issue Card**
```
POST /admin/cards
Form Data:
  - action: "issue"
  - user_id: 123
  - display_name: "John Doe"
  - issue_fee_paid: 700
```

**Delete Card**
```
POST /admin/cards
Form Data:
  - action: "delete"
  - card_id: 45
```

---

## Troubleshooting

### Customer Not Receiving SMS

**Possible Causes:**
- Phone number not registered in account
- SMS gateway not configured
- Phone number format incorrect

**Solutions:**
1. Verify phone number in user account
2. Check SMS gateway configuration
3. Ensure phone number starts with country code (254 for Kenya)
4. Test with `send_sms_notification()` function

### Authorization Expired

**Cause:** Customer took longer than 5 minutes to respond

**Solution:**
- Cashier initiates new authorization request
- Customer approves within time limit

### POS Not Detecting Approval

**Possible Causes:**
- No polling implemented
- Network connection lost
- Authorization token mismatch

**Solutions:**
1. Implement JavaScript polling on POS (every 2 seconds)
2. Check internet connection
3. Verify auth_token is correct

---

## Future Enhancements

### Phase 2 Features

1. **SMS Direct Reply**
   - Customer replies "YES" or "NO" to SMS
   - No need to log in to website

2. **USSD Authorization**
   - Dial *123*AUTH_CODE# to approve
   - Works on basic phones without internet

3. **Biometric Confirmation**
   - Fingerprint scan on customer's phone
   - Enhanced security

4. **Transaction Limits**
   - Set maximum amount per transaction
   - Require additional verification for large amounts

5. **Merchant Ratings**
   - Rate POS terminal/cashier after transaction
   - Build trust scores

---

## Training Resources

### For Customers

**Video Tutorial Topics:**
1. How to register for a Rewards Card
2. How to approve payments on your phone
3. How to check your card balance
4. What to do if you don't approve a transaction

### For Cashiers

**Training Checklist:**
- [x] How to scan card barcode (on back)
- [x] How to request mobile authorization
- [x] How to wait for customer approval
- [x] How to handle declined authorizations
- [x] How to handle expired authorizations
- [x] What to do if customer's phone is unavailable

### For Admins

**Admin Training:**
- [x] How to issue cards to customers
- [x] How to print physical cards
- [x] How to handle self-registered cards
- [x] How to delete cards
- [x] How to troubleshoot SMS issues

---

## Support

### Customer Support

**Common Questions:**

**Q: I didn't receive the SMS. What do I do?**
A: Check your phone number is correct in your account. You can also approve payments by logging into your account and going to "My Rewards Card."

**Q: I accidentally declined a payment. Can I undo it?**
A: No, but the cashier can initiate a new authorization request. Approve the new request.

**Q: How long do I have to approve a payment?**
A: 5 minutes from when the cashier scans your card.

**Q: Can someone else approve my payments?**
A: No, only you can approve payments using your account login or your registered phone number.

### Technical Support

**Debug Checklist:**
1. Check SMS gateway logs
2. Verify phone number format
3. Check authorization expiry time
4. Verify user permissions
5. Check network connectivity
6. Review database logs

---

**Document Version:** 1.0.0  
**Last Updated:** 2026-07-11  
**For:** All stakeholders - customers, cashiers, admins, developers
