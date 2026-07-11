"""
Test script for Smark-Africa Rewards Card Barcode Generation
"""

import sys
from io import BytesIO

# Set UTF-8 encoding for console output
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

try:
    import barcode
    from barcode.writer import SVGWriter
    print("[OK] Barcode library imported successfully")
except ImportError as e:
    print(f"[ERROR] Failed to import barcode library: {e}")
    print("\nInstall with: pip install python-barcode")
    sys.exit(1)


def generate_card_barcode_svg(card_number):
    """Generate SVG barcode for a shopping card."""
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
        print(f"[ERROR] Barcode generation failed: {e}")
        return None


def test_barcode_generation():
    """Test barcode generation with sample card numbers"""
    print("\n" + "="*60)
    print("Testing Smark-Africa Card Barcode Generation")
    print("="*60 + "\n")

    # Test with sample card numbers
    test_cards = [
        ("607845123456", "Standard card number"),
        ("607845987654", "Another valid number"),
        ("607845111111", "Repeated digits"),
        ("607845999999", "Max digits"),
        ("60784500000001", "16-digit card number"),
    ]

    success_count = 0
    for card_number, description in test_cards:
        print(f"Testing: {description}")
        print(f"Card Number: {card_number}")

        svg = generate_card_barcode_svg(card_number)

        if svg:
            print(f"[OK] Barcode generated successfully")
            print(f"  SVG Length: {len(svg)} characters")
            print(f"  Contains '<svg': {'Yes' if '<svg' in svg else 'No'}")
            print(f"  Contains '{card_number}': {'Yes' if card_number in svg else 'No'}")
            success_count += 1

            # Optionally save to file for inspection
            filename = f"test_card_{card_number}.svg"
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(svg)
                print(f"  Saved to: {filename}")
            except Exception as e:
                print(f"  Warning: Could not save file: {e}")
        else:
            print(f"[ERROR] Barcode generation failed")

        print("-" * 60)

    print(f"\n[OK] Test Results: {success_count}/{len(test_cards)} tests passed\n")

    # Check for Code128 support
    print("Available barcode formats:")
    print(", ".join(barcode.PROVIDED_BARCODES))
    print()

    return success_count == len(test_cards)


def test_barcode_validation():
    """Test barcode validation and edge cases"""
    print("="*60)
    print("Testing Barcode Validation & Edge Cases")
    print("="*60 + "\n")

    edge_cases = [
        ("", "Empty string", False),
        ("123", "Too short", True),  # Should still generate but not ideal
        ("6078451234567890123456", "Very long number", True),
        ("ABC123", "Contains letters", False),  # Should fail for Code128
    ]

    for test_value, description, should_work in edge_cases:
        print(f"Testing: {description}")
        print(f"Value: '{test_value}'")

        if test_value:
            svg = generate_card_barcode_svg(test_value)
            if svg:
                print(f"[OK] Barcode generated (expected: {'Yes' if should_work else 'No'})")
            else:
                print(f"[ERROR] Barcode generation failed (expected: {'Yes' if should_work else 'No'})")
        else:
            print(f"[SKIP] Skipped (empty value)")

        print("-" * 60)


if __name__ == "__main__":
    print("\n" + "="*60)
    print("Smark-Africa Rewards Card - Barcode Generation Test")
    print("="*60 + "\n")

    # Run tests
    test_passed = test_barcode_generation()
    print()
    test_barcode_validation()

    print("\n" + "="*60)
    if test_passed:
        print("[SUCCESS] ALL TESTS PASSED - Barcode system is ready!")
    else:
        print("[WARNING] SOME TESTS FAILED - Check errors above")
    print("="*60 + "\n")

    sys.exit(0 if test_passed else 1)
