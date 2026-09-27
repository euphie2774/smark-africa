# Event entry scanning

After listing a ticketed event, the seller lands on scanner settings. The same
settings are accessible from My services, the event page, and ticket sales.
Only the listing owner can retrieve or replace the shared scanner link. Anyone
given the link can scan without signing in. Replacing it invalidates old links.
Recipients can technically forward a bearer link; share it only with trusted staff.

The scanner accepts QR tickets and Code 128 cards. Phone cameras require HTTPS
(localhost also works) and camera permission. USB/Bluetooth scanners should be
configured as keyboard input with an Enter suffix. Camera decoding uses the
same pinned html5-qrcode library as the existing POS scanner. Camera permission,
hardware readers, and actual printers need a device acceptance check.

Successful scans show Accepted; subsequent scans show Used; forged, wrong-event,
unpaid, cancelled, refunded, or unapproved tickets show Invalid. QR and barcode
represent one admission. The camera stays active and results update in place.
Scanning requires connectivity to enforce one entry across multiple gates.

Admin event review controls barcode printing and sets the listing charge (zero
is free). A nonzero unpaid charge keeps the event awaiting payment. Sellers see
the amount in notifications and scanner settings. Payment is currently arranged
with the admin; the admin records a verified payment reference to open sales.
This workflow does not start a listing-fee M-Pesa prompt or transfer money.

With printing allowed, sellers can select barcode cards and enable a print
dialog after successful QR scans. Cards are 85.60 x 53.98 mm with the event name,
category, barcode, and “Designed by Smark-Africa.com”. Print at actual size with
browser headers/footers disabled. Silent printing requires printer/kiosk setup.
A card issued after admission is already used, so it is a physical record and
does not allow a second entry. Reprinting never resets admission status.

Startup schema migration adds the scanner version, print controls, ticket format,
and fee reference to existing listings. Existing events default to QR and no card
printing. No new external service configuration is required.

Verification: `tools/ticket_workflow_smoke.py` covers shared scanner access,
revocation, QR/barcode replay, concurrent scans, CSRF, print permissions, and
admin charge approval. `test_deploy_migration.py` verifies upgrades on a temporary
copy of the existing SQLite database.
