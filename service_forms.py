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


def read_service_answers(service_key, form):
    """Only accept questions belonging to this category; preserve labels on save."""
    answers = []
    for key, label, _ in SERVICE_QUESTIONS.get(service_key, ()):
        value = (form.get('detail_' + service_key + '_' + key, '') or '').strip()
        if len(value) > 1000:
            raise ValueError(f'{label} must be 1,000 characters or fewer.')
        if value:
            answers.append({'label': label, 'value': value})
    return answers
