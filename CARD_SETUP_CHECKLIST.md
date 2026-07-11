# Smark-Africa Rewards Card - Setup Checklist

## Pre-Launch Checklist for Administrators

Use this checklist to ensure your rewards card system is ready for customers.

---

## ☑ Phase 1: Hardware & Supplies

### Printing Equipment
- [ ] **PVC Card Stock** - CR80 size (85.6mm × 53.98mm), standard credit card size
  - Recommended: White or light-colored PVC cards
  - Quantity: Order based on expected demand (start with 100-500 cards)
  - Quality: Ensure cards are compatible with your printer

- [ ] **Card Printer** OR **Professional Printing Service**
  - Option A: In-house card printer (e.g., Zebra, Evolis, Fargo brands)
  - Option B: Professional printing service (send card designs)

- [ ] **Scratch-Off Labels/Material**
  - Silver or gray scratch-off stickers
  - Size: Approximately 2cm × 1cm (to cover 4-digit PIN)
  - Self-adhesive backing
  - High opacity (PIN must not be visible through sticker)

### Barcode Scanning Equipment
- [ ] **At least ONE barcode scanner** (choose one or more):
  - [ ] USB Barcode Scanner (wired)
  - [ ] Bluetooth Barcode Scanner (wireless)
  - [ ] Smartphone with camera (for phone scanner mode)

- [ ] **Test scanner with sample barcodes**
  - [ ] Print test barcode from: `test_card_607845123456.svg`
  - [ ] Verify scanner can read Code128 format
  - [ ] Confirm scanner connects to POS computer

### Office Supplies
- [ ] Clear plastic card sleeves (optional, for card protection)
- [ ] Envelopes for card distribution
- [ ] Instruction leaflets (print from CARD_POS_QUICK_GUIDE.md)

---

## ☑ Phase 2: System Configuration

### Settings Review
Access: **Admin Dashboard → Settings**

- [ ] **Credit Earning Rate** - Default: 1 credit per KSh 100
  - Key: `shopping_card_credits_per_100_kes`
  - Recommended: 1-5 (higher = more generous rewards)

- [ ] **Minimum Purchase for Credits** - Default: KSh 10,000
  - Key: `shopping_card_min_purchase_kes`
  - Recommended: 5,000-20,000 KSh

- [ ] **Free Card Issuance Threshold** - Default: 10,000 credits
  - Key: `shopping_card_min_credits`
  - Recommended: 10,000-50,000 credits (KSh 100-500 equivalent)

- [ ] **Card Issue Fee** - Default: KSh 700
  - Key: `shopping_card_issue_fee_kes`
  - Recommended: 500-1,000 KSh

- [ ] **Card Number Prefix** - Default: 607845
  - Key: `shopping_card_prefix`
  - Recommended: Keep default or use your brand code

### Database Verification
- [ ] Confirm `shopping_cards` table exists
- [ ] Confirm `shopping_card_transactions` table exists
- [ ] Verify database indexes are created
- [ ] Test database connectivity

### Admin Permissions
- [ ] Identify which admins can issue cards
- [ ] Set up POS staff accounts
- [ ] Configure POS role permissions
- [ ] Test admin access to card management interface

---

## ☑ Phase 3: Testing

### Barcode Generation Test
- [ ] Run test script: `python test_card_barcode.py`
- [ ] Verify all 5 tests pass
- [ ] Check generated SVG files can be opened
- [ ] Confirm barcodes display card numbers

### Test Card Issuance
- [ ] Create a test customer account
- [ ] Issue a test card to the account
  - Navigate to: Admin → Shopping Cards
  - Select test customer
  - Enter display name: "TEST CARD - DO NOT USE"
  - Issue fee: 0 (for testing)
  - Click "Issue and Print"

- [ ] Verify card print page loads correctly
- [ ] Check barcode appears on card design
- [ ] Check PIN is displayed on scratch slip
- [ ] Save/note the test card number and PIN

### Print Test Card
- [ ] Print the test card on regular paper first
- [ ] Verify layout is correct
- [ ] Check barcode is clear and readable
- [ ] Measure card dimensions (should be 85.6mm × 53.98mm)
- [ ] If satisfactory, print on one PVC card
- [ ] Apply scratch-off label over PIN

### Barcode Scanning Test
- [ ] Test with USB scanner (if available):
  - [ ] Connect scanner to POS computer
  - [ ] Open Admin → POS
  - [ ] Click "Wired Scanner Input" field
  - [ ] Scan test card barcode
  - [ ] Verify card info appears

- [ ] Test with Bluetooth scanner (if available):
  - [ ] Pair scanner with POS device
  - [ ] Scan test card barcode
  - [ ] Verify card info appears

- [ ] Test with phone scanner:
  - [ ] Open phone scanner from POS
  - [ ] Scan test card barcode with phone camera
  - [ ] Verify card info appears on POS

- [ ] Test manual entry:
  - [ ] Type test card number into "Card Number" field
  - [ ] Click "Scan Card Barcode" button
  - [ ] Verify card info appears

### POS Payment Test
- [ ] Add a low-value product to POS cart (e.g., KSh 10)
- [ ] Scan test card barcode
- [ ] Verify card details display:
  - [ ] Card number (masked)
  - [ ] Customer name
  - [ ] Balance
  - [ ] Status: Active

- [ ] Select payment method: "Smark-Africa Card"
- [ ] Enter test card PIN
- [ ] Complete sale
- [ ] Verify:
  - [ ] Transaction completes successfully
  - [ ] Receipt prints with card details
  - [ ] Card balance is reduced
  - [ ] Transaction appears in card history

### Error Handling Test
- [ ] Test wrong PIN:
  - [ ] Attempt payment with incorrect PIN
  - [ ] Verify error message appears
  - [ ] Confirm transaction is rejected

- [ ] Test insufficient balance:
  - [ ] Attempt purchase larger than card balance
  - [ ] Verify error message shows available balance
  - [ ] Confirm transaction is rejected

- [ ] Test blocked card:
  - [ ] Block the test card in admin panel
  - [ ] Attempt to use blocked card at POS
  - [ ] Verify error message appears

### Card Management Test
- [ ] **Fund Card**:
  - [ ] Add KSh 100 cash to test card
  - [ ] Verify balance increases
  - [ ] Check transaction log

- [ ] **Change Status**:
  - [ ] Change card status to "Blocked"
  - [ ] Verify card cannot be used at POS
  - [ ] Change back to "Active"

- [ ] **View Transactions**:
  - [ ] Open test card transaction history
  - [ ] Verify all test transactions are logged
  - [ ] Check timestamps, amounts, references

- [ ] **Reprint Card**:
  - [ ] Open reprint page for test card
  - [ ] Verify card design loads
  - [ ] Note: PIN will NOT be shown (security feature)

---

## ☑ Phase 4: Staff Training

### POS Cashiers
- [ ] Print training guide: **CARD_POS_QUICK_GUIDE.md**
- [ ] Schedule training session (30-45 minutes)
- [ ] Cover key topics:
  - [ ] How to scan card barcodes
  - [ ] How to request customer PIN
  - [ ] How to process card payments
  - [ ] How to handle errors (wrong PIN, insufficient balance)
  - [ ] How to process split payments
  - [ ] Security best practices (never write down PINs)

- [ ] Hands-on practice:
  - [ ] Each cashier scans test card
  - [ ] Each cashier processes test transaction
  - [ ] Each cashier handles error scenario

- [ ] Quiz/Assessment (optional):
  - [ ] What do you do if a customer forgets their PIN?
  - [ ] What do you do if the card balance is too low?
  - [ ] Where is the card barcode located?

### Admin Staff
- [ ] Review documentation: **REWARDS_CARD_SYSTEM.md**
- [ ] Training topics:
  - [ ] How to check customer eligibility
  - [ ] How to issue cards
  - [ ] How to print cards correctly
  - [ ] How to apply scratch-off labels
  - [ ] How to fund cards
  - [ ] How to change card status
  - [ ] How to handle lost/stolen cards
  - [ ] How to view transaction history

- [ ] Assign responsibilities:
  - [ ] Who issues cards?
  - [ ] Who prints cards?
  - [ ] Who applies scratch-off labels?
  - [ ] Who handles customer card inquiries?

---

## ☑ Phase 5: Customer Communication

### Marketing Materials
- [ ] Create card program announcement
- [ ] Design promotional posters/flyers
- [ ] Update website with card information
- [ ] Prepare social media posts

### Customer Education
- [ ] Create "How to Use Your Card" flyer
  - [ ] Where to use card (POS only)
  - [ ] How to earn credits
  - [ ] How to check balance
  - [ ] How to protect PIN
  - [ ] What to do if card is lost

- [ ] Create FAQ document
  - [ ] "How do I get a card?"
  - [ ] "How much does it cost?"
  - [ ] "Where can I use it?"
  - [ ] "Do credits expire?" (Currently: No)
  - [ ] "What if I lose my card?"

### Launch Communication
- [ ] Email announcement to existing customers
- [ ] SMS notification (if applicable)
- [ ] In-store signage
- [ ] Website banner
- [ ] Social media announcement

---

## ☑ Phase 6: Soft Launch

### Pilot Program (Recommended)
- [ ] Select 10-20 VIP/loyal customers for pilot
- [ ] Issue cards to pilot group
- [ ] Monitor usage for 1-2 weeks
- [ ] Collect feedback:
  - [ ] Is the card easy to use?
  - [ ] Is the barcode scanning working well?
  - [ ] Is the PIN process clear?
  - [ ] Any confusion or issues?

### Pilot Evaluation
- [ ] Review transaction logs
- [ ] Check for any technical issues
- [ ] Assess cashier comfort level
- [ ] Identify any training gaps
- [ ] Make adjustments based on feedback

---

## ☑ Phase 7: Full Launch

### Launch Day Checklist
- [ ] **Hardware Ready**:
  - [ ] All scanners tested and working
  - [ ] Card stock available
  - [ ] Scratch-off labels available
  - [ ] Printers loaded and tested

- [ ] **Staff Ready**:
  - [ ] All cashiers trained
  - [ ] All admin staff trained
  - [ ] Quick reference guides at each POS station
  - [ ] Support contact information posted

- [ ] **System Ready**:
  - [ ] Database backed up
  - [ ] All settings confirmed
  - [ ] Test transactions cleared or marked
  - [ ] Monitoring in place for errors

- [ ] **Communication Ready**:
  - [ ] Customer announcements sent
  - [ ] In-store signage posted
  - [ ] Website updated
  - [ ] Social media posts scheduled

### Day 1 Monitoring
- [ ] Admin on-site or on-call
- [ ] Monitor first 10-20 transactions
- [ ] Be available for cashier questions
- [ ] Quickly resolve any issues
- [ ] Document any unexpected problems

---

## ☑ Phase 8: Ongoing Operations

### Daily Tasks
- [ ] Check for low card stock
- [ ] Review transaction logs for errors
- [ ] Process card issuance requests
- [ ] Handle customer inquiries

### Weekly Tasks
- [ ] Review card usage statistics
- [ ] Check for blocked/lost cards
- [ ] Analyze earning patterns
- [ ] Review cashier performance

### Monthly Tasks
- [ ] Generate card usage report
- [ ] Review credit earning rate effectiveness
- [ ] Assess customer satisfaction
- [ ] Plan promotional campaigns
- [ ] Update training materials if needed

---

## ☑ Support & Troubleshooting

### Have These Resources Ready
- [ ] **Technical Documentation**: REWARDS_CARD_SYSTEM.md
- [ ] **POS Guide**: CARD_POS_QUICK_GUIDE.md
- [ ] **Implementation Summary**: CARD_IMPLEMENTATION_SUMMARY.md
- [ ] **Workflow Diagram**: CARD_WORKFLOW.txt

### Support Contacts
- [ ] Technical support contact: ___________________________
- [ ] Card issuance contact: ___________________________
- [ ] Customer service contact: ___________________________
- [ ] Emergency contact (after hours): ___________________________

### Common Issues & Solutions
- [ ] **Scanner not reading barcode**: Clean scanner lens, check barcode quality, try different angle
- [ ] **Card not found**: Verify card number, check card status in database
- [ ] **PIN rejected**: Customer should verify PIN on scratch slip, may need card reissue
- [ ] **System slow**: Check internet connection, restart browser, check database connection

---

## ☑ Success Metrics

### Track These KPIs
- [ ] Number of cards issued
- [ ] Number of active cards (used in last 30 days)
- [ ] Total card transactions
- [ ] Average transaction value
- [ ] Credits earned vs. credits redeemed
- [ ] Customer satisfaction scores
- [ ] Cashier error rate

### First Month Goals (Example)
- [ ] Issue 100+ cards
- [ ] Process 500+ card transactions
- [ ] Achieve 90%+ successful scan rate
- [ ] Achieve 95%+ customer satisfaction
- [ ] Zero security incidents

---

## ☑ Completion Sign-Off

### Pre-Launch Approval
- [ ] Technical implementation verified
- [ ] All hardware acquired and tested
- [ ] Staff training completed
- [ ] Test transactions successful
- [ ] Management approval obtained

**Signed by:**

Technical Lead: _________________________ Date: _________

Operations Manager: _____________________ Date: _________

General Manager: ________________________ Date: _________

---

## Need Help?

- **Technical Questions**: See REWARDS_CARD_SYSTEM.md
- **POS Training**: See CARD_POS_QUICK_GUIDE.md
- **Workflow Questions**: See CARD_WORKFLOW.txt
- **Test Barcode Generation**: Run `python test_card_barcode.py`

---

**Good luck with your Smark-Africa Rewards Card launch!** 🎉

---

**Document Version**: 1.0.0  
**Last Updated**: 2026-07-11  
**For**: System Administrators & Launch Team
