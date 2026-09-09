"""Category-specific listing questions, shared by rendering and POST validation."""

SERVICE_QUESTIONS = {
    'printing': [('print_options', 'Printing options', 'Black and white or colour, paper sizes, binding and finishing'), ('file_submission', 'How clients submit work', 'Upload, email or bring documents; accepted file formats'), ('price_basis', 'How printing is charged', 'Per page, copy or bound document; minimum quantities')],
    'laundry': [('laundry_items', 'Items and treatments accepted', 'Clothes, bedding, dry cleaning, ironing'), ('price_basis', 'How laundry is charged', 'Per kg, item or load; minimum weight'), ('return_method', 'How clean items are returned', 'Client collection or delivery; packaging included')],
    'food_delivery': [('menu', 'Meals and portions', 'Available dishes, portion sizes and dietary options'), ('ordering', 'Ordering and delivery times', 'Made to order or scheduled meals; order cutoff'), ('price_basis', 'Meal pricing', 'Per meal or meal plan; what is included')],
    'accommodation': [('property_type', 'Room or property type', 'Bedsitter, single room, shared hostel; capacity'), ('amenities', 'Amenities and rent inclusions', 'Water, electricity, Wi-Fi, furniture and shared facilities'), ('tenancy_terms', 'Stay and tenancy terms', 'Minimum stay, occupancy rules and additional charges')],
    'device_repair': [('devices', 'Devices and faults handled', 'Phone or laptop brands; screen, battery or software repairs'), ('diagnosis', 'Diagnosis and quotation', 'Inspection fee, approval before repairs and parts charges'), ('warranty', 'Repair warranty', 'Warranty period and what is covered')],
    'campus_errands': [('errand_types', 'Errands offered', 'Collections, shopping, document delivery'), ('instructions', 'What clients need to provide', 'Pickup and destination details, list of items and deadlines'), ('price_basis', 'How errands are charged', 'Per trip, distance or time; purchase costs charged separately')],
    'books_stationery': [('stock', 'Books and supplies available', 'Titles, editions, course materials and stationery'), ('condition', 'Condition and order options', 'New or used; in stock or ordered on request'), ('price_basis', 'Item and bundle pricing', 'Per item or bundle; bulk order terms')],
    'cyber_services': [('tasks', 'Tasks offered', 'Typing, scanning, applications, document formatting'), ('requirements', 'What clients should bring', 'Documents or information needed; avoid sharing passwords'), ('price_basis', 'How tasks are charged', 'Per page, application or task; external fees')],
    'cleaning': [('cleaning_scope', 'Cleaning scope', 'Rooms, offices, deep cleaning or move-out cleaning'), ('supplies', 'Equipment and supplies', 'What you bring and what the client supplies'), ('price_basis', 'How cleaning is charged', 'Per room, area, hour or visit; minimum booking')],
    'barber_beauty': [('treatments', 'Treatments and styles', 'Haircuts, braids, nails, makeup and other treatments'), ('duration', 'Typical appointment length', 'Time needed for each treatment'), ('price_basis', 'Treatment pricing', 'Per treatment or package; products included or extra')],
    'grocery': [('products', 'Groceries available', 'Produce, household essentials and available quantities'), ('substitutions', 'Substitution policy', 'How unavailable items and price changes are agreed'), ('ordering', 'Order preparation', 'Order cutoff, shopping time and packaging')],
    'parcel_courier': [('parcel_limits', 'Parcel limits', 'Maximum weight and dimensions; excluded items'), ('delivery_speed', 'Delivery options', 'Same day, next day or scheduled; collection cutoff'), ('handling', 'Packaging and delivery confirmation', 'Packaging requirements, fragile items and proof of delivery')],
    'events_tickets': [('admission', 'Admission and entry rules', 'Age limits, ID requirements and entry times'), ('ticket_inclusions', 'What each tier includes', 'Regular entry, VIP seating, VVIP access or refreshments')],
    'student_gigs': [('deliverables', 'Work and deliverables', 'Tasks offered, output formats and what the client receives'), ('working_method', 'How the work is delivered', 'Remote or in person; milestones and client requirements'), ('revisions', 'Revisions and scope', 'Included revisions, deadlines and extra work charges')],
    'tutoring': [('subjects', 'Subjects and levels', 'Subjects, syllabus and learner level'), ('lesson_format', 'Lesson format', 'Online or in person; individual or group; group size'), ('lesson_duration', 'Lesson length and materials', 'Session duration, materials and practice work included')],
    'fitness': [('activities', 'Activities and coaching', 'Personal training, classes, sports coaching'), ('session_format', 'Session format', 'Individual or group, session length and class schedule'), ('equipment', 'Equipment and requirements', 'Equipment provided, what to bring and experience level')],
    'health_wellness': [('service_scope', 'Services offered', 'Consultations, counselling or wellness sessions'), ('qualifications', 'Professional qualifications', 'Relevant credentials and registration where applicable'), ('consultation_format', 'Consultation format', 'In person or remote, duration and booking requirements')],
    'career': [('career_support', 'Support offered', 'CV review, interview coaching or career guidance'), ('delivery_format', 'How support is delivered', 'Remote or in person; document review or live session'), ('deliverables', 'What clients receive', 'Revised CV, feedback, session length and follow-up included')],
}


def read_service_answers(service_key, form, questions=None):
    """Only accept questions belonging to this category; preserve labels on save."""
    answers = []
    for key, label, _ in (questions if questions is not None else SERVICE_QUESTIONS.get(service_key, ())):
        value = (form.get('detail_' + service_key + '_' + key, '') or '').strip()
        if len(value) > 1000:
            raise ValueError(f'{label} must be 1,000 characters or fewer.')
        if value:
            answers.append({'label': label, 'value': value})
    return answers


# A price is always paired with a unit; money is validated on the server.
PRICE_LABELS = {
    'food_delivery': 'Dish / portion', 'grocery': 'Grocery item / pack',
    'printing': 'Print / finishing option', 'laundry': 'Item / treatment',
    'device_repair': 'Repair / diagnosis', 'campus_errands': 'Errand / trip',
    'books_stationery': 'Book / stationery item', 'cyber_services': 'Task',
    'cleaning': 'Cleaning package', 'barber_beauty': 'Treatment / style',
    'parcel_courier': 'Delivery option', 'student_gigs': 'Deliverable / package',
    'tutoring': 'Lesson / subject', 'fitness': 'Class / coaching',
    'health_wellness': 'Consultation', 'career': 'Support package',
    'accommodation': 'Room / property',
}
PRICE_UNITS = ('item', 'portion', 'kg', 'page', 'trip', 'km', 'hour', 'session',
               'visit', 'night', 'day', 'week', 'month', 'task', 'package')
CLIENT_FIELDS = {
    'ticket': [],
    'dropoff': [('work', 'Items, quantities and work needed'), ('handover', 'Drop-off or collection location'), ('deadline', 'Preferred completion date')],
    'errand': [('pickup', 'Pickup / shop location'), ('destination', 'Delivery address and landmark'), ('deadline', 'Delivery time / deadline'), ('budget', 'Purchase budget and substitution instructions')],
    'visit': [('appointment', 'Preferred appointment date and time'), ('location', 'At the provider or your address'), ('scope', 'Treatment / work and requirements')],
    'session': [('scope', 'Goals and deliverables'), ('appointment', 'Preferred date and duration'), ('format', 'Online or in person')],
    'tenancy': [('arrival', 'Move-in date and length of stay'), ('occupants', 'Number of occupants'), ('viewing', 'Preferred viewing time')],
}

CLIENT_BY_SERVICE = {
    'printing': [('documents', 'Page count, copies and paper size'), ('finish', 'Colour, double-sided and finishing'), ('handover', 'Collection or delivery and deadline')],
    'laundry': [('items', 'Items, approximate weight and treatment'), ('care', 'Care instructions / stains'), ('handover', 'Collection address and return time')],
    'food_delivery': [('destination', 'Delivery address and landmark'), ('deadline', 'Preferred delivery time'), ('diet', 'Dietary requirements / allergies to confirm with the provider')],
    'grocery': [('destination', 'Delivery address and time'), ('substitutions', 'Substitutions allowed and spending limit')],
    'parcel_courier': [('pickup', 'Pickup address and contact'), ('destination', 'Destination address and contact'), ('parcel', 'Contents, weight and dimensions'), ('deadline', 'Delivery deadline and handling needs')],
    'campus_errands': [('task', 'Errand and instructions'), ('pickup', 'Pickup / shopping location'), ('destination', 'Destination and deadline'), ('budget', 'Purchase budget; receipt and substitution requirements')],
    'device_repair': [('device', 'Device model and fault'), ('diagnosis', 'Symptoms and prior repairs (no passwords)'), ('handover', 'Drop-off or collection preference')],
    'books_stationery': [('items', 'Titles, editions or supplies and quantities'), ('condition', 'New or used preference'), ('handover', 'Collection or delivery preference')],
    'cyber_services': [('task', 'Task and number of pages / documents'), ('output', 'Required format and deadline'), ('handover', 'How you will submit documents (no passwords)')],
    'cleaning': [('scope', 'Rooms, approximate area and cleaning needed'), ('location', 'Address and access instructions'), ('appointment', 'Preferred date and time'), ('supplies', 'Equipment / supplies available')],
    'barber_beauty': [('treatment', 'Treatment / style and preferences'), ('appointment', 'Preferred appointment date and time'), ('location', 'At the salon or your address')],
    'tutoring': [('subject', 'Subject, level and learning goals'), ('appointment', 'Preferred schedule and lesson length'), ('format', 'Online or in person; individual or group')],
    'fitness': [('activity', 'Activity and experience level'), ('appointment', 'Preferred schedule and session length'), ('format', 'Individual or group; venue preference')],
    'health_wellness': [('service', 'Type of consultation requested'), ('appointment', 'Preferred appointment date and time'), ('format', 'In person or remote (discuss private health details with the provider)')],
    'career': [('support', 'CV review, interview preparation or other support'), ('goals', 'Target role and requested deliverables'), ('deadline', 'Deadline and preferred session format')],
    'student_gigs': [('scope', 'Deliverables, format and scope'), ('deadline', 'Deadline and milestones'), ('requirements', 'Materials and acceptance criteria')],
}


def form_design(key, label, profile, stored=None):
    """Versioned data, never executable markup; fallback works without AI/network."""
    import json
    if stored:
        try:
            return validate_design(json.loads(stored))
        except (ValueError, TypeError, KeyError):
            pass
    return {'version': 1, 'questions': SERVICE_QUESTIONS.get(key, [
        ('scope', f'What your {label} service includes', 'Deliverables, exclusions and client requirements'),
        ('timing', 'Availability and completion', 'Booking, preparation time and how completion is confirmed'),
        ('charges', 'Charges and cancellation terms', 'Price unit, minimum charge, extras and cancellation terms')]),
        'client_fields': CLIENT_BY_SERVICE.get(key, CLIENT_FIELDS.get(profile, CLIENT_FIELDS['dropoff'])),
        'price_label': PRICE_LABELS.get(key, 'Service / package')}


def validate_design(data):
    import re
    if not isinstance(data, dict):
        raise ValueError('Invalid form design')
    result = {'version': 1}
    for name, width in [('questions', 3), ('client_fields', 2)]:
        rows = data.get(name)
        if not isinstance(rows, list) or not (0 if name == 'client_fields' else 1) <= len(rows) <= 8:
            raise ValueError('Invalid question count')
        seen = set()
        for row in rows:
            if (not isinstance(row, (list, tuple)) or len(row) != width
                    or not all(isinstance(x, str) and 0 < len(x) <= 200 for x in row)
                    or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', row[0]) or row[0] in seen):
                raise ValueError('Invalid question')
            seen.add(row[0])
        result[name] = rows
    label = data.get('price_label')
    if not isinstance(label, str) or not 1 <= len(label) <= 80:
        raise ValueError('Invalid price label')
    result['price_label'] = label
    if 'profile' in data:
        if data['profile'] not in CLIENT_FIELDS:
            raise ValueError('Invalid delivery profile')
        result['profile'] = data['profile']
    return result


def read_price_items(form):
    from decimal import Decimal, InvalidOperation
    names = form.getlist('item_name')
    prices, units, descriptions = [form.getlist('item_' + key) for key in ('price', 'unit', 'description')]
    if len(names) > 60 or any(len(values) != len(names) for values in (prices, units, descriptions)):
        raise ValueError('Use up to 60 complete price rows.')
    rows, seen = [], set()
    for name, price, unit, description in zip(names, prices, units, descriptions):
        name, description = name.strip(), description.strip()
        if not name and not price and not description:
            continue
        if not name or len(name) > 100 or len(description) > 300 or unit not in PRICE_UNITS:
            raise ValueError('Each price row needs an item name, valid unit and short description.')
        try:
            amount = Decimal(price)
            if not amount.is_finite() or amount < 0 or amount > 10000000 or amount != amount.quantize(Decimal('0.01')):
                raise ValueError('Use a price between 0 and 10,000,000 with at most two decimals.')
        except InvalidOperation:
            raise ValueError('Every item needs a valid price.') from None
        identity = (name.casefold(), unit)
        if identity in seen:
            raise ValueError('Duplicate item and unit. Give different portions or packages distinct names.')
        seen.add(identity)
        rows.append({'name': name, 'price': str(amount.quantize(Decimal('0.01'))), 'unit': unit, 'description': description})
    return rows


def request_summary(service, form):
    """Calculate from saved rates, never from a total supplied by the browser."""
    from decimal import Decimal
    lines, subtotal = [], Decimal('0')
    for index, item in enumerate(service.price_items):
        raw = form.get('quantity_' + str(index), '0') or '0'
        try:
            quantity = int(raw)
        except (ValueError, TypeError):
            raise ValueError('Quantities must be whole numbers.') from None
        if not 0 <= quantity <= 100:
            raise ValueError('Choose a quantity between 0 and 100.')
        if quantity:
            amount = Decimal(item['price']) * quantity
            subtotal += amount
            lines.append(f"{quantity} x {item['name']} / {item['unit']}: KSh {amount:.2f}")
    if lines:
        if subtotal < Decimal(str(service.min_order_amount or 0)):
            raise ValueError('Selected items are below the minimum order amount.')
        delivery = Decimal(str(service.delivery_fee or 0)) if service.profile == 'errand' else Decimal('0')
        lines.append(f'Items: KSh {subtotal:.2f}; delivery: KSh {delivery:.2f}; estimate: KSh {subtotal + delivery:.2f}. Confirm availability and extras before payment.')
    for key, label in service.service_design['client_fields']:
        value = (form.get('request_' + key) or '').strip()
        if len(value) > 200:
            raise ValueError(f'{label} must be 200 characters or fewer.')
        if value:
            lines.append(f'{label}: {value}')
    return '\n'.join(lines)
