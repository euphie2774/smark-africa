# Smark-Africa Rewards Card - Design Specifications

## Two-Sided Card Design

**Card Size:** CR80 Standard (85.6mm × 53.98mm)  
**Material:** PVC Card Stock  
**Finish:** Glossy or Matte  
**Printing:** Two-sided, full-color

---

## FRONT OF CARD (Customer-Facing Side)

```
╔════════════════════════════════════════════════════════════════╗
║                                                                ║
║   ┌────┐                                                       ║
║   │ SA │  SMARK-AFRICA                                         ║
║   └────┘  Rewards Card                                         ║
║   (Logo)                                                       ║
║                                                                ║
║                                                                ║
║   ┌──────┐                                                     ║
║   │ CHIP │  (EMV-style chip design - decorative)              ║
║   └──────┘                                                     ║
║                                                                ║
║                                                                ║
║   6078 4512 3456 7890                                          ║
║   (Card Number - spaced in groups of 4)                        ║
║                                                                ║
║                                                                ║
║   CARDHOLDER                                                   ║
║   JOHN DOE                                                     ║
║   (Customer Name)                                              ║
║                                                                ║
║                                                                ║
║   Rewards Program                          •••• 7890           ║
║   (Footer text)                            (Last 4 digits)     ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
```

### Design Elements - Front

**Colors:**
- Background: Dark gradient (Black #1a1a1a to Charcoal #2d2d2d)
- Text: Gold #f4c542
- Logo Box: Gold #f4c542 background with black text
- Chip: Gold gradient

**Typography:**
- Platform Name: 21px, Bold, Letter-spacing 1.5px
- Card Type: 10px, Uppercase, Letter-spacing 0.8px
- Card Number: 19px, Monospace (Courier New), Letter-spacing 3.5px
- Cardholder Name: 14px, Bold, Uppercase, Letter-spacing 1px
- Footer: 9px, Uppercase, Letter-spacing 0.5px

**Logo:**
- Size: 48px × 48px
- Contains: "SA" initials
- Style: Bold, centered
- Background: Gold with rounded corners
- Shadow: 0 3px 10px rgba(244,197,66,0.4)

---

## BACK OF CARD (Barcode Side)

```
╔════════════════════════════════════════════════════════════════╗
║████████████████████████████████████████████████████████████████║
║████████████████ MAGNETIC STRIPE ███████████████████████████████║
║████████████████████████████████████████████████████████████████║
║                                                                ║
║                                                                ║
║   ┌────┐                                                       ║
║   │ 🛒 │  Your Premium Digital                                 ║
║   └────┘  & Physical Marketplace                               ║
║   (Cart)  (Tagline)                                            ║
║                                                                ║
║                                                                ║
║   ┌──────────────────────────────────────────────────────┐    ║
║   │  ║║│││║║│║║│││║║│││║║│││║║│││║║│││║║│││║║│││║║│││║  │    ║
║   │  ║║│││║║│║║│││║║│││║║│││║║│││║║│││║║│││║║│││║║│││║  │    ║
║   │             6078 4512 3456 7890                        │    ║
║   └──────────────────────────────────────────────────────┘    ║
║   (Barcode - Code128 format on white background)              ║
║                                                                ║
║   The use of this card is subject to the terms and            ║
║   conditions of Smark-Africa's card agreement. This card      ║
║   can only be used on a Smark-Africa POS terminal only.       ║
║   (Terms and Conditions - small text, justified)              ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
```

### Design Elements - Back

**Colors:**
- Background: Dark gradient (Charcoal #2d2d2d to Black #1a1a1a)
- Magnetic Stripe: Deep black gradient with shadow
- Cart Icon Box: Gold #f4c542 background
- Barcode Area: White background
- Text: Gold #f4c542 and white-gold blend

**Typography:**
- Tagline: 10.5px, Semi-bold, Letter-spacing 0.3px
- Terms: 7px, Light color, Line-height 1.5, Justified

**Cart Icon:**
- Size: 34px × 34px
- Icon: 🛒 (shopping cart emoji or icon font)
- Background: Gold with rounded corners
- Shadow: 0 2px 6px rgba(244,197,66,0.3)

**Barcode:**
- Format: Code128
- Background: White (#ffffff)
- Padding: 6px 8px
- Border-radius: 6px
- Maximum width: 290px
- Height: 55px
- Includes human-readable card number below bars

**Magnetic Stripe:**
- Width: Full card width
- Height: 40px
- Position: Top of back side (16px from edge)
- Color: Black gradient with shadow effect

---

## BARCODE SPECIFICATIONS

### Technical Details
- **Format:** Code128
- **Encoding:** Card number (12-16 digits)
- **Output:** SVG (Scalable Vector Graphics)
- **Module Width:** 0.3mm
- **Module Height:** 12mm
- **Quiet Zone:** 2.5mm on each side
- **Text Display:** Enabled (card number shown below bars)
- **Error Correction:** Built-in Code128 checksum

### Barcode Placement
- **Location:** Back of card
- **Container:** White background box
- **Alignment:** Centered horizontally
- **Margins:** 8px padding inside white box
- **Visibility:** High contrast (black bars on white)

### Scanner Compatibility
Compatible with:
- ✓ USB barcode scanners
- ✓ Bluetooth barcode scanners
- ✓ Phone camera scanners
- ✓ Handheld laser scanners
- ✓ Desktop flatbed scanners

---

## PIN SCRATCH SLIP (Separate Item)

```
┌─────────────────────────────────────────────────────────────┐
│ 🔒 Card PIN                             [CONFIDENTIAL]      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│                    ┌──────────────┐                         │
│                    │              │                         │
│                    │    1 2 3 4   │  ← Apply scratch-off   │
│                    │              │     material here       │
│                    └──────────────┘                         │
│                    (4-digit PIN)                            │
│                                                             │
│  ⚠️ Security Notice:                                        │
│  Cover this PIN with scratch-off material or opaque        │
│  sticker before giving to customer.                         │
│                                                             │
│  How to Use:                                                │
│  1. Scratch panel to reveal PIN                            │
│  2. Present card at Smark-Africa POS                        │
│  3. Cashier scans card barcode (on back)                   │
│  4. Enter 4-digit PIN to authorize payment                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### PIN Slip Specifications
- **Size:** 336mm width (same as card width for alignment)
- **Material:** Paper or card stock
- **Scratch Area:** 20mm × 10mm (approx)
- **PIN Font:** 24px, Bold, Monospace, Letter-spacing 3px
- **Background:** Light gray (#eeeeee)
- **Border:** 2px dashed black

---

## PRINTING GUIDELINES

### Card Stock
- **Material:** White or light-colored PVC
- **Thickness:** 0.76mm (30 mil) standard
- **Finish:** Glossy (recommended) or Matte
- **Durability:** Should withstand normal wallet wear

### Printing Method
**Option 1: In-House Card Printer**
- Use dedicated PVC card printer (Zebra, Evolis, Fargo, etc.)
- Two-sided printing capability required
- CMYK color mode
- High-quality (300 DPI minimum)

**Option 2: Professional Printing Service**
- Provide card design files (front and back)
- Request two-sided printing
- Specify PVC card stock, CR80 size
- Ensure barcode area has high contrast

### Quality Checks
Before distribution, verify:
1. ✓ Barcode scans correctly with all scanner types
2. ✓ Card number is legible (front)
3. ✓ Cardholder name is correct and legible
4. ✓ Terms and conditions are readable (back)
5. ✓ No printing defects or smudges
6. ✓ Colors are accurate (gold and black)
7. ✓ Card dimensions are correct (85.6mm × 53.98mm)

---

## SECURITY FEATURES

### Physical Security
- **Card Number:** Raised or embossed printing (optional)
- **Holographic Overlay:** Optional security feature
- **UV Printing:** Optional invisible ink for authentication
- **Signature Strip:** Not included (POS-only card)

### Digital Security
- **PIN:** 4 digits, bcrypt hashed in database
- **Card Number:** Unique, cryptographically generated
- **Barcode:** Encodes card number only (no PIN)
- **Validation:** Real-time database lookup at POS

---

## BRANDING GUIDELINES

### Logo Usage
The "SA" logo represents Smark-Africa and should:
- Always appear in gold (#f4c542) on dark background
- OR black text on gold background
- Maintain square aspect ratio
- Include rounded corners (border-radius: 10px)
- Cast subtle shadow for depth

### Color Palette
**Primary Colors:**
- Gold: #f4c542 (Smark-Africa brand gold)
- Black: #1a1a1a (Deep black)
- Charcoal: #2d2d2d (Medium gray-black)

**Accent Colors:**
- White: #ffffff (Barcode background only)
- Light Gray: #c0c0c0 (PIN scratch area)
- Deep Black: #0a0a0a (Magnetic stripe)

**Text Colors:**
- Primary Text: #f4c542 (Gold)
- Secondary Text: rgba(255,255,255,0.7-0.95) (White-gold blend)
- Terms Text: rgba(255,255,255,0.65) (Subtle white)

### Typography Standards
**Font Families:**
- Brand Text: Arial, Helvetica, sans-serif
- Card Number: 'Courier New', Consolas, monospace
- Body Text: Arial, sans-serif

**Font Weights:**
- Bold: Platform name, cardholder name
- Semi-bold: Tagline
- Regular: Terms, footer text
- Light: Secondary information

---

## FILE FORMATS

### For Printing
- **Card Front:** PDF or high-res PNG (300 DPI)
- **Card Back:** PDF or high-res PNG (300 DPI)
- **Barcode:** SVG (generated dynamically per card)
- **PIN Slip:** PDF or PNG

### Digital Files
- **Template:** HTML (card_print.html)
- **Barcode Generation:** Python (main.py)
- **Barcode API:** SVG endpoint (/admin/cards/<id>/barcode.svg)

---

## ACCESSIBILITY CONSIDERATIONS

### Visual Design
- **High Contrast:** Gold on black for readability
- **Large Text:** Card number and name in larger fonts
- **Clear Hierarchy:** Important info (card number, name) is prominent
- **Tactile Elements:** Optional embossing for visually impaired

### Barcode Readability
- **White Background:** Ensures maximum contrast
- **Quiet Zones:** Adequate spacing around barcode
- **Multiple Scan Methods:** Supports various scanner types
- **Human-Readable:** Card number printed below barcode

---

## MAINTENANCE & UPDATES

### When to Reprint Cards
- Card damaged or worn
- Barcode no longer scans
- Customer name change
- PIN reset required (issue new card)
- Lost or stolen card (issue replacement)

### Design Updates
If brand guidelines change:
1. Update `card_print.html` template
2. Test with sample prints
3. Verify barcode still scans correctly
4. Update documentation
5. Notify card issuance team

---

## SAMPLE CARD DATA

### For Testing
**Card Number:** 607845123456  
**Cardholder:** TEST CUSTOMER  
**Last 4:** 3456  
**Status:** ACTIVE  

**Note:** Use test cards with "TEST" in the name to avoid confusion with live customer cards.

---

## FREQUENTLY ASKED QUESTIONS

**Q: Can customers customize card colors?**  
A: No, all cards follow the standard Smark-Africa brand design (gold on black).

**Q: Is the EMV chip functional?**  
A: No, the chip is decorative. Cards work via barcode scanning only.

**Q: Can cards be printed on regular paper for testing?**  
A: Yes, but ensure barcode scans correctly. PVC cards recommended for customers.

**Q: How long do cards last?**  
A: With normal use, PVC cards last 2-3 years before showing wear.

**Q: Can the barcode be on the front instead?**  
A: Not recommended. The back placement keeps the front clean and professional.

---

**Document Version:** 1.0.0  
**Last Updated:** 2026-07-11  
**For:** Design Team, Print Vendors, Card Issuance Staff

---

## Quick Reference Checklist

**Before printing:**
- [ ] Verify customer name is correct
- [ ] Check card number is unique
- [ ] Ensure barcode generates correctly
- [ ] Test barcode scanning

**Front of card includes:**
- [ ] Smark-Africa logo ("SA" badge)
- [ ] Platform name
- [ ] Card type label
- [ ] EMV chip (decorative)
- [ ] Card number (spaced correctly)
- [ ] Cardholder name
- [ ] Last 4 digits

**Back of card includes:**
- [ ] Magnetic stripe (decorative)
- [ ] Cart icon with tagline
- [ ] Scannable barcode on white background
- [ ] Terms and conditions text

**PIN slip includes:**
- [ ] 4-digit PIN
- [ ] Scratch-off material applied
- [ ] Usage instructions
- [ ] Security warnings
