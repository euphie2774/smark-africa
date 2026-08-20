"""Check that the app is wired up: it boots bare, and every template link resolves.

Run with: python tools/wiring_smoke.py

Nothing here needs a database fixture, which is what makes it worth running first:
it imports the app with Redis and every new env var unset, counts the routes, and
then walks every ``url_for`` in every template to confirm the endpoint exists and is
given the arguments its rule requires. That last audit is the one that catches a
template calling an endpoint that was renamed or deleted - a link that raises only
when a human happens to load that page, which is to say in production. It has already
found one: an orphan admin template pointing at an endpoint that no longer existed.

The rest are cheap invariants worth asserting on every run rather than reasoning
about: the phone-evidence folder stays off both Cloudinary folder sets, the new env
vars all have working defaults, and the comparables cache actually caches.
"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['DISABLE_BACKGROUND_JOBS'] = '1'
# Prove the new env vars are optional and Redis stays optional.
for key in ('MARKET_COMPARABLE_TTL', 'PRICE_CHECK_RATE_LIMIT', 'PHONE_EVIDENCE_MIN_SCORE',
            'PHONE_EVIDENCE_REQUIRED', 'PHONE_EVIDENCE_RATE_LIMIT',
            'REDIS_URL', 'CACHE_REDIS_URL'):
    os.environ.pop(key, None)

failures = []
passed = []


def check(label, fn):
    try:
        fn()
        passed.append(label)
        print(f'  [ok  ] {label}')
    except Exception as exc:
        failures.append(label)
        print(f'  [FAIL] {label}: {type(exc).__name__}: {exc}')


import models
print('models OK')
import main
print('main imports OK')

app = main.app
routes = {str(r.rule) for r in app.url_map.iter_rules()}
print(f'route count: {len(routes)}')

NEW_ROUTES = [
    '/admin/products/bulk',
    '/admin/products/bulk/cover',
    '/admin/products/bulk/upload',
    '/seller/products/<int:product_id>/ownership',
    '/seller/products/<int:product_id>/ownership/imei-check',
    '/admin/phone-evidence',
    '/admin/phone-evidence/<int:evidence_id>/<decision>',
]
for want in NEW_ROUTES:
    check(f'route {want}', lambda w=want: (_ for _ in ()).throw(AssertionError('missing'))
          if w not in routes else None)

# Templates parse.
TEMPLATES = ['admin/admin_bulk_digital.html', 'seller_phone_ownership.html',
             'admin/products.html', 'admin/add_product.html', 'seller_products.html',
             'admin/phone_evidence.html', 'admin/discounts.html']
for name in TEMPLATES:
    check(f'template {name}', lambda n=name: app.jinja_env.get_template(n))

# Every url_for('endpoint') in every template names a real endpoint, and supplies
# the arguments that endpoint requires. This is the check that would have caught
# admin/discounts.html calling admin_edit_product with product_id instead of pid.
import re
URLFOR = re.compile(r'url_for\(')
ENDPOINT = re.compile(r"""\s*['"]([a-zA-Z_][\w.]*)['"]""")


def url_for_calls(body):
    """Yield (endpoint, supplied-kwarg-names) for every url_for( in a template.

    A scanner rather than one regex, because a regex that stops at the first comma
    reports every multi-argument call as missing its later arguments - a false
    positive on exactly the calls most worth checking. Walks to the matching close
    paren tracking quotes and nesting, then reads the kwarg names at this call's own
    depth so url_for('x', y=f(z=1)) does not credit z to x.
    """
    for match in URLFOR.finditer(body):
        start = i = match.end()
        depth, quote = 1, ''
        while i < len(body) and depth:
            ch = body[i]
            if quote:
                if ch == '\\':
                    i += 1
                elif ch == quote:
                    quote = ''
            elif ch in '"\'':
                quote = ch
            elif ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            i += 1
        inner = body[start:i - 1]
        named = ENDPOINT.match(inner)
        if not named:
            continue  # url_for(request.endpoint, ...) - nothing static to check
        flat, depth, quote = '', 0, ''
        for ch in inner[named.end():]:
            if quote:
                if ch == quote:
                    quote = ''
            elif ch in '"\'':
                quote = ch
            elif ch in '([{':
                depth += 1
            elif ch in ')]}':
                depth -= 1
            elif depth == 0:
                flat += ch
        yield named.group(1), set(re.findall(r',\s*(\w+)\s*=', flat))


def url_for_audit():
    tpl_root = os.path.join(ROOT, 'templates')
    bad = []
    rules = {}
    for rule in app.url_map.iter_rules():
        rules.setdefault(rule.endpoint, []).append(rule.arguments)
    for dirpath, _dirs, files in os.walk(tpl_root):
        for fname in files:
            if not fname.endswith('.html'):
                continue
            path = os.path.join(dirpath, fname)
            with open(path, encoding='utf-8', errors='replace') as handle:
                body = handle.read()
            rel = os.path.relpath(path, tpl_root).replace(os.sep, '/')
            for endpoint, supplied in url_for_calls(body):
                if endpoint not in rules:
                    bad.append(f'{rel}: unknown endpoint {endpoint}')
                    continue
                # A rule is satisfiable if some variant's required args are all
                # supplied. Extra kwargs become query string, which is fine.
                if not any(args <= supplied for args in rules[endpoint]):
                    need = ' | '.join(sorted(','.join(sorted(a)) or '-' for a in rules[endpoint]))
                    bad.append(f'{rel}: {endpoint} needs [{need}], got [{",".join(sorted(supplied)) or "-"}]')
    if bad:
        raise AssertionError(f'{len(bad)} bad url_for call(s):\n    ' + '\n    '.join(bad[:25]))


check('url_for endpoints and args across all templates', url_for_audit)


# Phone detection vectors.
def phone_vectors():
    yes = ['Samsung Galaxy A14', 'iPhone 12 Pro', 'Tecno Spark 10', 'Used phone',
           'Redmi Note 12', 'Infinix Hot 30 handset']
    no = ['Wireless earphones', 'Phone case for iPhone 12', 'Bluetooth headphones',
          'Fast charger', 'Tempered glass screen protector', 'Nursing revision notes',
          'Samsung phone charger cable', 'Airpods Pro']
    for name in yes:
        assert main.is_phone_listing(name), f'should be a phone: {name}'
    for name in no:
        assert not main.is_phone_listing(name), f'should NOT be a phone: {name}'


check('is_phone_listing vectors', phone_vectors)


def luhn_vectors():
    # Known-good IMEIs (Luhn valid). The third is 01396400372548 with its check
    # digit computed rather than copied - a wrong vector here fails the function
    # that is right.
    good = ['490154203237518', '356938035643809', '013964003725480']
    bad = ['123456789012345', '000000000000000', '', '49015420323751',
           '4901542032375180', 'abcdefghijklmno', '111111111111111']
    for value in good:
        assert main.imei_checksum_valid(value), f'should pass Luhn: {value}'
    for value in bad:
        assert not main.imei_checksum_valid(value), f'should fail Luhn: {value}'


check('imei_checksum_valid vectors', luhn_vectors)


def evidence_folder_boundary():
    assert main.PHONE_EVIDENCE_FOLDER not in main.CLOUDINARY_PUBLIC_FOLDERS, \
        'phone evidence must never be publicly hosted'
    assert main.PHONE_EVIDENCE_FOLDER not in getattr(main, 'CLOUDINARY_PRIVATE_FOLDERS', set())


check('phone_docs stays off Cloudinary', evidence_folder_boundary)


def defaults_present():
    assert main.PHONE_EVIDENCE_MIN_SCORE == 70, main.PHONE_EVIDENCE_MIN_SCORE
    assert main.PHONE_EVIDENCE_REQUIRED is True
    assert main.PHONE_EVIDENCE_RATE_LIMIT
    assert main.MARKET_COMPARABLE_TTL


check('new env vars default without being set', defaults_present)


def comparable_cache_hit():
    with app.app_context():
        first = main.comparable_price_stats('Samsung Galaxy A14', None, None)
        second = main.comparable_price_stats('Samsung Galaxy A14', None, None)
        assert isinstance(first, dict) and isinstance(second, dict), (type(first), type(second))
        stats = main._comparable_price_cache.stats()
        assert stats.get('hits', 0) >= 1, f'no cache hit: {stats}'


check('comparable_products cache hit on repeat', comparable_cache_hit)


def market_reference_shape():
    with app.app_context():
        ref = main.market_price_reference('Samsung Galaxy A14', 15000, None)
        assert 'status' in ref and 'competitor_count' in ref, sorted(ref)
        assert isinstance(ref['competitor_count'], int), type(ref['competitor_count'])


check('market_price_reference shape', market_reference_shape)


def evidence_model_wired():
    assert 'phone_ownership_evidence' in models.db.metadata.tables
    columns, indexes = main.phase_two_schema_spec()
    names = {spec[0] for spec in indexes}
    for want in ('ix_phone_evidence_imei_status', 'ix_phone_evidence_user_created',
                 'ix_phone_evidence_product_created', 'ix_products_review_created'):
        assert want in names, f'index spec missing {want}'


check('evidence table and index specs', evidence_model_wired)

print()
if failures:
    print(f'{len(failures)} check(s) failed')
    sys.exit(1)
print(f'all {len(passed)} checks passed')
