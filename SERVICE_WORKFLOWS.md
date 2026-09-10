# Service listing and fulfilment

Choose a category at `/services/create`. Each link opens a server-rendered form for that category; ticket forms contain no delivery, pickup, menu or generic payment fields. Billing units are restricted by category on the client and server.

| Category | Listing / customer information | Fulfilment |
| --- | --- | --- |
| Tickets | Event, venue, organiser, entry/refund terms, single price or tier allocations | Admin verifies → buyer pays per tier → individual signed QR admissions → organiser checks in each person once |
| Food | Dishes, portions, allergens, preparation, delivery fee and area | Itemized request → confirmed full quote → platform payment → delivery → receipt confirmation |
| Errands | Trip/hour/km rates, task scope, purchase budget, pickup and destination | Linked request → expense-inclusive quote → payment → task delivery → confirmation |
| Groceries | Items/packs/weights, substitutions, destination | Itemized request and agreed substitutions → quote → payment → delivery |
| Courier | Parcel limits, weight, dimensions, destination and timing | Quote → payment → parcel handover and delivery confirmation |
| Laundry | Per-kg/item treatments, care instructions and return method | Collection/drop-off → agreed quote → work ready → payment and confirmation |
| Printing | Page/copy/finishing options and document requirements | Drop-off/submission → agreed quote → work ready → payment and confirmation |
| Repair | Device/fault, diagnosis, parts and warranty terms | Inspection → approved scope/quote → repair → payment and confirmation |
| Books/stationery | Titles, editions, condition and quantities | Item request → confirmed availability/quote → handover and confirmation |
| Cyber services | Document/task/page scope, format and deadline | Agreed quote → deliverable → payment and confirmation |
| Cleaning | Area, scope, supplies, address and appointment | Linked appointment → agreed charge → work → direct provider payment and confirmation |
| Beauty | Treatments, duration and appointment/location | Linked appointment → agreed charge → service → direct provider payment and confirmation |
| Tutoring | Subject, level, schedule and lesson format | Linked session → agreed quote → lesson → platform payment and confirmation |
| Fitness | Activity, experience, schedule and equipment | Linked appointment → agreed charge → session → direct provider payment and confirmation |
| Health/wellness | Qualifications, consultation type, duration and format | Linked appointment → agreed charge → consultation → direct provider payment and confirmation |
| Career | Target role, document/session scope, deliverables and deadline | Linked request → quote → deliverable/session → platform payment and confirmation |
| Student gigs | Deliverables, milestones, revisions and acceptance criteria | Linked request → quote → deliverable → platform payment and confirmation |
| Accommodation | Property, amenities, stay terms, rent unit, deposit and viewing | Linked viewing → agreed terms → direct provider payment and confirmation |

## Completion and charges

`/services/requests` is the customer/provider inbox. Admins can use the same full conversation from the service desk. A final quote includes the complete scope and extras; accepting it snapshots the amount into an order. A changed quote must be reviewed again. Direct-provider services never generate a platform charge.

Providers can mark linked requests ready. Customers can confirm receipt and satisfaction; this records a timestamp and notifies the assigned admin (or the admin desk when unassigned). Admins can close completed requests in the conversation or desk. An accepted platform quote must be paid before closure. Completion and admission consumption use atomic database guards.

Physical service forms include an address, county and interactive entrance-pin picker, with address lookup, manual coordinates and optional device location. Repair and printing clients can view the selected pin and open it in maps. Latitude/longitude pairs are validated, including valid zero coordinates; unsupported profiles ignore location fields. Address lookup is explicitly approximate so the seller can correct the pin. Map rendering follows the [Leaflet API](https://leafletjs.com/reference.html) and retains OpenStreetMap attribution.

Pickup switches are available for laundry, repair, printing, books/stationery and parcel couriers. They default off, and are absent from food, groceries, tickets, remote work and appointments. New category designs can declare pickup support only within appropriate delivery profiles. Sellers specify collection/return charges and collection windows. Clients choose drop-off or an offered pickup, enter an address/window, and see the fee in the estimate. The request stores this choice. Providers/admins can move it through scheduled, collected and returned/delivered stages; timestamps, conversation messages and client notifications document progress. Unsupported or disabled pickup requests are rejected server-side.

There is no customer refund action. Admins can record exceptional completed refunds for service orders with a reason, transaction reference and explicit confirmation. This is a refund record and revokes ticket access where applicable; money must be returned through the payment provider. Closing a refunded service request preserves its refund status.

## Ticket security

Each paid seat receives its own 256-bit random nonce and signed, event-bound QR payload. The printable wallet and QR routes require buyer/admin authentication. Service callbacks are independently verified using the existing authenticated Daraja checkout query before settlement. An unsigned success payload alone cannot issue tickets. Buyer payment polling retries verification at most once per order every 30 seconds if a callback could not be verified. A capacity or buyer-limit conflict records `oversold_refund_due`, issues no admission and notifies admins.

Sellers may leave capacity blank for unlimited, unseated admission, or enter a seat count for each tier (or the single general-admission tier). Finite allocations receive consecutive seat numbers unique within their tier, across orders. Event settlement is serialized in the database; capacity increments and seat ranges commit with ticket issuance. Legacy paid orders receive stable seat ranges under the same lock. Total/remaining capacity is shown only to the seller and admins, including at `/services/<id>/ticket-sales`. Buyers receive an availability message if their purchase cannot be filled.

The optional event buyer limit applies across all tiers and purchases by the same signed-in account. Optional tier limits apply across purchases of that tier. Blank limits allow further purchases; each checkout is bounded to 50 tickets. This is an account limit, not identity verification across multiple accounts. Cancelled/refunded seat numbers are retained rather than reassigned.

Organisers see payment status, order status, issued admissions, seats and check-in timestamps in the private sales page. Admins can revoke an order or record an already-completed refund with a reason and transaction reference. These actions are audited in `ticket_order_actions`; recording a refund does not send money. Scans of cancelled, refunded, unpaid or unapproved admissions are denied. Check-in and revocation serialize on the order so a simultaneous status change cannot bypass validation.

Gate staff use an ordinary QR scanner and open the encoded page while signed in as the event provider or an admin. The server checks payment, event, signature and consumption state. Pressing **Admit this person** consumes the admission once. A screenshot can be copied, but it cannot be admitted twice; organisers should also check the displayed booking name. These are online checks, not offline verification. Keep `SECRET_KEY` stable across workers/restarts.

Ticket sales require `ticket_review_status=approved`; legacy ticket categories are resolved as tickets even if they retained the old drop-off profile. Existing pending listings need admin verification. Old paid orders receive individual admissions when their wallet is opened. Buyers access tickets through Account → My event tickets, even if sales later pause. Pending payment pages poll only initiated checkouts and open the wallet on confirmation.

The existing M-Pesa integration rounds charges to whole shillings; the form discloses this and callback amount verification uses the same rounding rule. No real payment was initiated during automated verification.

## Conversations and product discovery

Authorised admins can read every service conversation and reply through the same thread, including conversations assigned to another admin. Conversations and provider invitations do not announce admin viewing, and reading does not publish an online-presence indicator. Admin replies are explicitly labelled Admin in both conversation views. Users should keep discussions and platform payments in the app; oversight cannot guarantee a seller will never attempt off-platform contact.

Home and Shop search inputs show up to eight active product-name suggestions after two characters, with a 200 ms debounce, stale-response cancellation, keyboard navigation and accessible combobox labels. Prefix matches rank ahead of contained-name and related-term matches. Category/type filters are retained. Literal `%` and `_` are escaped in suggestion queries. All suggestion labels use DOM text rather than injected HTML.

Meaning-based search uses the existing configurable concept map, expanded with common shopping intents; full search can additionally use the existing optional `SEMANTIC_SEARCH_AI=1` integration and configured key. Suggestions use local concepts and make no AI calls per keystroke. This is keyword/concept expansion, not an embedding index.

Automated checks cover concurrent last-seat settlement, simultaneous one-use scans, cumulative limits, unlimited admissions, cancellation/refund rejection, private capacity, forged success callbacks, payment retry, admin participation and product suggestions. Tests use a disposable SQLite database and mocked payment APIs; real Daraja transactions and production-database load were not exercised.

## New categories

The catalogue immediately supplies a fallback form from the selected delivery profile. The existing leased scheduler processes pending AI form designs in bounded batches, saving validated questions and research sources. Automatic mode lets AI select one of the six supported workflows. An explicitly selected profile is retained. Provider prices and credentials are never invented. Published listings retain a snapshot of their form.

Production research requires `OPENAI_API_KEY` (or the existing `openai_api_key` setting) and the existing background-worker configuration (`RUN_BACKGROUND_JOBS=1` in production, without `DISABLE_BACKGROUND_JOBS=1`). The admin catalogue displays pending/waiting/failed status and offers retries. Three failures stop automatic retries. API calls were mocked during integration tests; live account access was not tested.

## Reference patterns

- Ticket types, prices and allocations: [Eventbrite](https://www.eventbrite.com/help/en-us/articles/644100/how-to-create-custom-ticket-types/).
- Dish/item prices and menu editing: [Uber Eats](https://help.uber.com/en/merchants-and-restaurants/article/how-to-update-an-item?nodeId=41a9b70d-4599-4a5d-a533-90eb890a6bee).
- Service durations and price variations: [Square Appointments](https://api.squareup.com/help/us/en/article/6487-create-a-service-from-the-square-appointments-app).
- Item variations for books, supplies and groceries: [Square items](https://squareup.com/help/us/en/article/8335-create-and-edit-items).
- Work packages, deliverables and revisions: [Upwork Project Catalog](https://www.upwork.com/resources/how-to-create-project-catalog-service).
- Courier quote dimensions and weight: [DHL](https://www.dhl.com/discover/en-gb/ship-with-dhl/shipping-solutions/shipping-tools/mydhl-user-guides/get-rate-and-time-quote).
- Repair estimates: [Apple](https://support.apple.com/iphone/repair).
- Laundry weights, items and minimum charges: [CleanCloud merchant example](https://cleancloudapp.com/s/5551).
- Rental fee disclosure: [Airbnb](https://news.airbnb.com/fee-transparency-on-airbnb).
- Background research API: [OpenAI web search](https://developers.openai.com/api/docs/guides/tools-web-search).

These references informed the form structures; providers supply their actual terms and prices.
