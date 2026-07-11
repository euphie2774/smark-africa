# Smark-Africa Rewards Card Management System

## Overview

The Smark-Africa Rewards Card system allows customers to earn shopping credits based on their purchase history and use those credits (along with funded cash balances) to make purchases at any Smark-Africa physical POS terminal. Each card includes a unique barcode for quick scanning at checkout.

---

## Features

### 1. **Card Issuance**
- Cards can be issued to customers who meet one of the following criteria:
  - Have accumulated at least **10,000 shopping credits** (default, configurable)
  - Pay a one-time issue fee of **KSh 700** (default, configurable)
  - Are administrators (can issue cards without restrictions)

- Each card includes:
  - **Unique 12-16 digit card number** starting with configurable prefix (default: `607845`)
  - **4-digit PIN** (generated securely, never duplicated)
  - **Customer name** (printed on card)
  - **Barcode** (Code128 format for universal scanner compatibility)
  - **Card balance** (shopping credits + funded cash)

### 2. **Card Printing**
- Cards are designed for **two-sided printing** on **standard PVC card stock** (CR80 size: 85.6mm × 53.98mm)

**FRONT OF CARD** (Customer-facing):
  - **Smark-Africa logo** (gold "SA" badge beside platform name)
  - **Platform name**: "SMARK-AFRICA"
  - **Card type**: "Rewards Card"
  - **EMV chip** design (decorative)
  - **Card number** (formatted in groups of 4 digits)
  - **Cardholder name**
  - **Card identifier** (last 4 digits)

**BACK OF CARD** (Scanning side):
  - **Magnetic stripe** (decorative top stripe)
  - **Shopping cart icon** with tagline: "Your Premium Digital & Physical Marketplace"
  - **Scannable barcode** (Code128 format on white background)
  - **Terms and conditions**: "The use of this card is subject to the terms and conditions of Smark-Africa's card agreement. This card can only be used on a Smark-Africa POS terminal only."

- **PIN Scratch Slip** is printed separately with:
  - The 4-digit PIN covered by scratch-off material
  - Instructions for card usage
  - Security warnings

### 3. **Barcode Scanning**
The system supports multiple barcode scanning methods:

#### a. **Phone/Camera Scanner**
- Mobile phones can scan card barcodes using the camera
- Works with the existing POS phone scanner pairing system
- Automatically detects card barcodes vs. product barcodes

#### b. **USB Barcode Scanner**
- Standard wired barcode scanners (keyboard-wedge type)
- Plug-and-play, no software installation needed
- Scans directly into the POS card input field

#### c. **Bluetooth Barcode Scanner**
- Wireless Bluetooth scanners work as keyboard input
- Same functionality as USB scanners

#### d. **Manual Entry**
- Cashiers can manually type the card number if scanning fails
- System validates the card number format

### 4. **POS Integration**

#### Card Recognition at POS
When a card barcode is scanned:
1. System automatically detects it's a card (not a product) based on the number prefix
2. Retrieves customer information from the database
3. Displays:
   - Card number (masked: •••• 1234)
   - Customer name
   - Available balance (credits + cash in KSh)
   - Card status (Active/Blocked/Lost)
4. Auto-fills customer details (name, email, phone)
5. Prompts cashier to request the 4-digit PIN from customer

#### Payment Authorization
1. Cashier scans card barcode
2. Customer enters 4-digit PIN
3. System verifies PIN and checks balance
4. Deducts purchase amount from card balance
5. Generates receipt with card details

### 5. **Credit Earning System**

Customers automatically earn shopping credits based on purchases:

- **Default earning rate**: 1 credit per KSh 100 spent (configurable)
- **Credit value**: 100 credits = KSh 1.00
- **Minimum purchase**: KSh 10,000 to earn credits (configurable)

**Example Earning Scenarios:**
- Purchase of KSh 10,000 → 100 credits (KSh 1.00)
- Purchase of KSh 15,000 → 150 credits (KSh 1.50)
- Purchase of KSh 20,000 → 200 credits (KSh 2.00)

Credits are automatically credited to the customer's card after:
- Online orders (when marked as completed)
- POS purchases (immediately after sale)

### 6. **Card Funding**

Administrators can fund cards with **cash balance** (not credits):
- Cash balance can be loaded directly via the admin portal
- Cash balance functions identically to shopping credits for redemption
- Useful for gift cards or promotional campaigns

### 7. **Card Management**

#### Card Statuses
- **Active**: Card can be used for purchases
- **Blocked**: Card cannot be used (temporarily disabled)
- **Lost**: Card reported as lost/stolen (permanently disabled)

#### Administrative Actions
- Issue new cards
- Print/reprint cards with barcodes
- Fund cash balances
- Change card status
- View transaction history
- Reset PINs (requires card reissuance)

---

## Technical Implementation

### Database Models

#### `ShoppingCard` Table
```python
- id (primary key)
- user_id (foreign key to users)
- card_number (unique, 12-16 digits)
- card_last4 (last 4 digits)
- pin_hash (bcrypt hashed PIN)
- display_name (printed name)
- status (active/blocked/lost)
- credit_balance (integer, 100 = KSh 1.00)
- cash_balance (float, direct KSh value)
- issue_fee_paid (float)
- issued_by (foreign key to users)
- issued_at (timestamp)
- printed_at (timestamp)
- created_at (timestamp)
- updated_at (timestamp)
```

#### `ShoppingCardTransaction` Table
```python
- id (primary key)
- card_id (foreign key to shopping_cards)
- user_id (foreign key to users)
- transaction_type (issue/credit/redeem/fund)
- credit_amount (integer, can be negative)
- cash_amount (float, can be negative)
- balance_after_credits (integer)
- balance_after_cash (float)
- reference_type (order/pos_sale/card_issue)
- reference_id (related entity ID)
- note (description)
- created_by (foreign key to users)
- created_at (timestamp)
```

### Barcode Generation

The system uses **Code128** barcode format:
- **Library**: `python-barcode` with SVGWriter
- **Output**: SVG format (scalable, print-ready)
- **Configuration**:
  - Module width: 0.3mm
  - Module height: 12mm
  - Quiet zone: 2.5mm
  - Text display: Enabled (shows card number below barcode)

### API Endpoints

#### Card Barcode SVG
```
GET /admin/cards/<card_id>/barcode.svg
```
Generates and serves the barcode as SVG image for a specific card.

#### Scan Card at POS
```
POST /admin/pos/scan-card
Body: {"barcode": "607845123456"}
Response: {
  "success": true,
  "card": {
    "card_number": "607845123456",
    "card_last4": "3456",
    "display_name": "John Doe",
    "customer_name": "johndoe",
    "customer_email": "john@example.com",
    "customer_phone": "254712345678",
    "credit_balance": 150.50,
    "cash_balance": 200.00,
    "total_balance": 350.50,
    "status": "active"
  }
}
```

### Security Features

1. **PIN Security**
   - PINs are hashed using bcrypt (never stored in plaintext)
   - 4-digit format for ease of use
   - Cannot be viewed after initial issuance
   - PIN reset requires card reissuance

2. **Card Number Generation**
   - Cryptographically random generation
   - Unique constraint in database
   - Configurable prefix for brand recognition
   - Automatic validation

3. **Transaction Logging**
   - Every card transaction is logged with full audit trail
   - Includes: type, amounts, balances, references, timestamps, creator
   - Immutable transaction history

4. **POS-Only Usage**
   - Cards only work at Smark-Africa POS terminals
   - Not compatible with external payment networks (Visa, Mastercard, etc.)
   - Cannot be used online or at third-party merchants

---

## Configuration Settings

All card system parameters are configurable via the Settings table:

| Setting Key | Default Value | Description |
|------------|---------------|-------------|
| `shopping_card_min_credits` | 10000 | Minimum credits needed for free card issuance |
| `shopping_card_issue_fee_kes` | 700 | Issue fee in KSh if customer doesn't have enough credits |
| `shopping_card_credits_per_100_kes` | 1 | Credits earned per KSh 100 spent |
| `shopping_card_min_purchase_kes` | 10000 | Minimum purchase amount to earn credits |
| `shopping_card_prefix` | 607845 | Card number prefix for brand identification |

### Modifying Settings

Settings can be changed via:
1. Admin dashboard → Settings page
2. Direct database update
3. Programmatically via `Setting.set(key, value)`

---

## Usage Workflow

### Issuing a Card
1. Navigate to **Admin → Shopping Cards**
2. Select customer from dropdown
3. Enter display name (or use customer's username)
4. Enter issue fee if customer doesn't have enough credits
5. Click **"Issue and Print"**
6. System generates card and PIN
7. Card print page opens automatically with:
   - Card design (with barcode)
   - PIN scratch slip
8. Print on PVC card stock
9. Apply scratch-off material over PIN area
10. Give card and PIN slip to customer

### Using a Card at POS
1. Cashier adds products to cart
2. Customer presents Smark-Africa Rewards Card
3. Cashier scans barcode (or types card number)
4. System displays card details and balance
5. Cashier selects **"Smark-Africa Card"** as payment method
6. Customer enters 4-digit PIN
7. System validates PIN and balance
8. If sufficient balance:
   - Deducts amount from card
   - Completes sale
   - Prints receipt with card transaction details
9. If insufficient balance:
   - Shows error message with available balance
   - Customer can use split payment (partial card + cash/mobile money)

### Funding a Card
1. Navigate to **Admin → Shopping Cards**
2. Find the card in the list
3. In the "Fund" field, enter amount in KSh
4. Click **"Fund"** button
5. System adds cash balance to card
6. Transaction is logged in card history

---

## Troubleshooting

### Barcode Won't Scan
**Possible causes:**
- Poor print quality (barcode too light or smudged)
- Scanner not configured for Code128 format
- Barcode damaged or obscured
- Scanner too far from barcode

**Solutions:**
- Reprint the card on better quality stock
- Check scanner settings (should accept Code128)
- Manual entry of card number
- Test barcode with phone scanner app

### Card Not Recognized
**Possible causes:**
- Card is blocked or lost (status not "active")
- Card number entered incorrectly
- Database connectivity issue
- Card not in system (print error)

**Solutions:**
- Check card status in admin panel
- Verify card number matches database
- Check database connection
- Reissue card if necessary

### PIN Rejected
**Possible causes:**
- Customer entered wrong PIN
- PIN was reset but customer has old scratch slip
- PIN hash corrupted in database

**Solutions:**
- Ask customer to verify PIN (scratch panel)
- Reissue card with new PIN
- Contact system administrator for database check

### Insufficient Balance
**Possible causes:**
- Customer doesn't have enough credits or cash
- Recent transaction not yet processed
- Balance shown doesn't match actual balance

**Solutions:**
- Show customer current balance
- Suggest split payment (partial card + other method)
- Fund card with additional cash if needed
- Check transaction history for discrepancies

---

## Future Enhancements

Potential future features for the card system:

1. **Mobile App Integration**
   - Virtual cards in mobile app
   - Digital barcode/QR code in app
   - Balance checking via app
   - Transaction notifications

2. **Tiered Rewards**
   - Bronze/Silver/Gold tiers based on spending
   - Higher earning rates for higher tiers
   - Exclusive perks for top-tier members

3. **Expiry Management**
   - Optional credit expiry dates
   - Notifications before expiry
   - Auto-renewal options

4. **Card Replacement**
   - Lost card reporting by customers
   - Balance transfer to new card
   - Card replacement fee

5. **Referral Rewards**
   - Credits for referring new customers
   - Bonus when referee makes first purchase

6. **Birthday/Anniversary Bonuses**
   - Automatic credit bonuses on special dates
   - Promotional campaigns

7. **Analytics Dashboard**
   - Card usage statistics
   - Popular redemption patterns
   - Customer segmentation by card usage

---

## Support

For questions or issues with the Rewards Card system:

1. Check this documentation first
2. Review the admin training materials
3. Contact technical support: [support contact]
4. File a bug report: [GitHub issues link]

---

## Changelog

### Version 1.0.0 (Current)
- Initial implementation of Rewards Card system
- Barcode generation and scanning support
- POS integration with card payment
- Credit earning based on purchases
- Card issuance and management
- PIN security with bcrypt hashing
- Transaction logging and audit trail
- Configurable earning rates and thresholds
- Print-ready card design with embedded barcode

---

**Last Updated**: 2026-07-11  
**Document Version**: 1.0.0  
**System Version**: Smark-Africa Platform v2.0
