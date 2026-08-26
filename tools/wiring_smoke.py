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

Two later groups cover failures that are invisible from inside the app. The
crawlability checks assert robots.txt serves, sitemap.xml is well-formed, public-only
and cached, and that a canonical tag actually reaches a rendered page - a sitemap
that 500s looks perfectly healthy to every human visitor while costing the site every
search result. The sizing checks assert worker_plan reads the container's limits
rather than the host's, which is the 503 this suite now catches before a deploy does:
nine workers of 200MB cannot start inside a 512MB instance, and the kernel kills them
without an application log line to explain it.

The last group covers the rate limiter, whose defaults apply to every registered
endpoint - including Flask's own 'static'. Untuned, one shop page carrying a dozen
locally stored images spent thirteen requests of the caller's allowance instead of
one, and /healthz shared the same bucket, so a busy address could 429 the health
check and have the host restart the container for looking unhealthy. Both directions
are asserted, because a 200 says nothing about whether a limit was applied.
"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['DISABLE_BACKGROUND_JOBS'] = '1'
# Prove the new env vars are optional and Redis stays optional.
for key in ('MARKET_COMPARABLE_TTL', 'PRICE_CHECK_RATE_LIMIT', 'PHONE_EVIDENCE_MIN_SCORE',
            'PHONE_EVIDENCE_REQUIRED', 'PHONE_EVIDENCE_RATE_LIMIT',
            'RATE_LIMIT_PER_HOUR', 'RATE_LIMIT_PER_MINUTE',
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
    # Popped above, so these are the built-in defaults. Asserted as whole strings
    # because Flask-Limiter parses them itself and a typo like '2000 per hours' is
    # a boot-time crash, not a wrong number.
    assert main.RATE_LIMIT_PER_HOUR == '2000 per hour', main.RATE_LIMIT_PER_HOUR
    assert main.RATE_LIMIT_PER_MINUTE == '180 per minute', main.RATE_LIMIT_PER_MINUTE


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


# ---------- Crawlability ----------
# These exist because the failure mode is silent. A missing robots.txt, a sitemap
# that 500s, or a canonical tag that names the wrong host all serve a working site
# to every human visitor while quietly costing it every search result, and nothing
# in the application logs says so.

def seo_routes_present():
    for want in ('/robots.txt', '/sitemap.xml'):
        assert want in routes, f'missing {want}'


check('robots.txt and sitemap.xml routed', seo_routes_present)


def robots_txt_body():
    with app.test_client() as client:
        resp = client.get('/robots.txt', base_url='https://localhost')
        assert resp.status_code == 200, resp.status_code
        assert resp.headers['Content-Type'].startswith('text/plain'), resp.headers['Content-Type']
        body = resp.get_data(as_text=True)
        assert body.startswith('User-agent: *'), body[:60]
        assert 'Disallow: /admin/' in body, body
        assert 'Disallow: /checkout' in body, body
        # Absolute, and on the host that asked - a relative Sitemap line is ignored.
        assert 'Sitemap: https://localhost/sitemap.xml' in body, body


check('robots.txt serves and names the sitemap', robots_txt_body)


def sitemap_xml_body():
    import xml.etree.ElementTree as ET
    with app.test_client() as client:
        resp = client.get('/sitemap.xml', base_url='https://localhost')
        # Was 500 before the cache lookup was fixed: TTLCache.get() returns None on
        # a miss rather than the sentinel, so the build block was skipped entirely
        # and the response body was None. Asserting the status code is what caught
        # it, which is why this is a request and not a call to the function.
        assert resp.status_code == 200, resp.status_code
        assert resp.headers['Content-Type'].startswith('application/xml'), resp.headers['Content-Type']
        root = ET.fromstring(resp.get_data(as_text=True))  # raises if malformed
        ns = '{http://www.sitemaps.org/schemas/sitemap/0.9}'
        locs = [el.text for el in root.iter(f'{ns}loc')]
        assert 'https://localhost/' in locs, locs[:8]
        assert any(loc.endswith('/shop') for loc in locs), locs[:8]
        # Nothing private may ever appear here.
        for loc in locs:
            assert not any(p.rstrip('/') in loc for p in ('/admin', '/seller', '/cart')), loc


check('sitemap.xml is well-formed and public-only', sitemap_xml_body)


def sitemap_cached():
    before = main._sitemap_cache.stats().get('hits', 0)
    with app.test_client() as client:
        client.get('/sitemap.xml', base_url='https://localhost')
        client.get('/sitemap.xml', base_url='https://localhost')
    after = main._sitemap_cache.stats().get('hits', 0)
    assert after > before, f'sitemap not cached: {before} -> {after}'


check('sitemap.xml caches between crawls', sitemap_cached)


def noindex_vectors():
    public = ['/', '/shop', '/product/some-slug', '/categories/phones', '/about', '/terms']
    private = ['/admin/products', '/cart', '/checkout', '/login', '/register',
               '/seller/dashboard', '/api/anything', '/notifications', '/healthz']
    for path in public:
        with app.test_request_context(path):
            assert not main.page_is_noindex(), f'should be indexable: {path}'
    for path in private:
        with app.test_request_context(path):
            assert main.page_is_noindex(), f'should be noindex: {path}'


check('page_is_noindex vectors', noindex_vectors)


def canonical_vectors():
    for key in ('APP_BASE_URL', 'PUBLIC_URL'):
        os.environ.pop(key, None)
    # Tracking parameters collapse; pagination does not.
    with app.test_request_context('/shop?utm_source=fb&ref=x&page=2'):
        url = main.canonical_url()
        assert url.endswith('/shop?page=2'), url
        assert 'utm_source' not in url and 'ref=' not in url, url
    with app.test_request_context('/shop?page=1'):
        assert main.canonical_url().endswith('/shop'), main.canonical_url()
    # A configured base wins, and is forced to https even when written bare.
    os.environ['APP_BASE_URL'] = 'www.smark-africa.com'
    try:
        with app.test_request_context('/shop', base_url='http://smark-africa.com'):
            assert main.canonical_url() == 'https://www.smark-africa.com/shop', main.canonical_url()
    finally:
        os.environ.pop('APP_BASE_URL', None)
    # Outside a request there is no one URL to name, and it must not raise.
    assert main.canonical_url() == ''


check('canonical_url normalises host and query', canonical_vectors)


def canonical_tag_rendered():
    """The helper being right is worth nothing if the tag never reaches the page."""
    with app.test_client() as client:
        resp = client.get('/about', base_url='https://localhost')
        assert resp.status_code == 200, resp.status_code
        body = resp.get_data(as_text=True)
        assert '<link rel="canonical"' in body, 'no canonical tag on /about'
        assert 'https://localhost/about' in body, 'canonical does not name this page'
        assert 'name="robots"' not in body, 'public page must not be noindex'


check('canonical tag reaches a rendered page', canonical_tag_rendered)


# ---------- Container-aware sizing ----------
def worker_plan_respects_container():
    """A 512MB / 0.1-CPU instance must plan one worker, not nine.

    This is the 503 in a single assertion. os.cpu_count() reports the host's cores
    inside a container, so the classic formula asked for nine workers of roughly
    200MB each on an instance with 512MB, and the kernel killed them before any of
    them logged a reason.
    """
    import scale
    saved_env = {k: os.environ.pop(k, None)
                 for k in ('WEB_CONCURRENCY', 'MAX_WEB_CONCURRENCY', 'WORKER_MEMORY_MB',
                           'GUNICORN_THREADS')}
    orig_mem, orig_cpu = scale.container_memory_limit, scale.container_cpu_quota
    try:
        scale.container_memory_limit = lambda: 512 * 1024 * 1024
        scale.container_cpu_quota = lambda: 0.1
        workers, threads = scale.worker_plan()
        assert workers == 1, f'512MB instance planned {workers} workers'
        assert threads == 4, threads
        # A roomier box still scales up, so the cap is not a permanent ceiling.
        scale.container_memory_limit = lambda: 4 * 1024 * 1024 * 1024
        scale.container_cpu_quota = lambda: 4.0
        bigger, _ = scale.worker_plan()
        assert bigger > 1, f'4GB/4cpu instance planned only {bigger}'
        # Unrestricted (no cgroup) must still return something sane.
        scale.container_memory_limit = lambda: 0
        scale.container_cpu_quota = lambda: 0.0
        free, _ = scale.worker_plan()
        assert free >= 1, free
    finally:
        scale.container_memory_limit, scale.container_cpu_quota = orig_mem, orig_cpu
        for key, value in saved_env.items():
            if value is not None:
                os.environ[key] = value


check('worker_plan sizes to the container, not the host', worker_plan_respects_container)


def cgroup_parsers_are_safe():
    """The parsers must return "unlimited" rather than a wrong number when read fails."""
    import scale
    assert scale._cgroup_value('/definitely/not/a/path') == ''
    # On Windows and any non-cgroup host both must report unrestricted, not crash.
    assert isinstance(scale.container_memory_limit(), int)
    assert isinstance(scale.container_cpu_quota(), float)


check('cgroup readers tolerate a host without cgroups', cgroup_parsers_are_safe)


# ---------- Services and invoices ----------
SERVICE_ROUTES = [
    '/services',
    '/services/<int:service_id>',
    '/services/<int:service_id>/contact-admin',
    '/services/<int:service_id>/order',
    '/services/requests/<int:request_id>/thread',
    '/services/create',
    '/admin/services/duty',
    '/admin/services/requests',
    '/admin/services/requests/<int:request_id>/<action>',
    '/admin/services/catalogue',
    '/admin/users/<int:user_id>/service-agent',
    '/invoice/<token>',
    '/invoice/<token>/print',
    '/invoice/<token>/pay',
    '/invoice/<token>/status',
    '/admin/invoices',
    '/admin/invoices/new',
    '/admin/invoices/<int:invoice_id>',
    '/admin/invoices/<int:invoice_id>/send',
    '/admin/invoices/<int:invoice_id>/cancel',
    '/admin/invoices/<int:invoice_id>/record-payment',
    '/admin/invoices/<int:invoice_id>/print',
    '/admin/users/<int:user_id>/invoice-agent',
]
for want in SERVICE_ROUTES:
    check(f'route {want}', lambda w=want: (_ for _ in ()).throw(AssertionError('missing'))
          if w not in routes else None)

SERVICE_TEMPLATES = ['services.html', 'service_detail.html', 'create_service.html',
                     'admin/service_catalogue.html', 'admin/service_requests.html',
                     'admin/invoices.html', 'admin/invoice_form.html',
                     'admin/invoice_detail.html', 'invoice_public.html',
                     'invoice_print.html', '_badges.html']
for name in SERVICE_TEMPLATES:
    check(f'template {name}', lambda n=name: app.jinja_env.get_template(n))


def services_page_renders():
    """The services listing must serve to an anonymous visitor.

    It is reachable without an account and therefore reachable by every crawler, so
    a 500 here is a public page that is down. Asserted on whatever the operator's
    catalogue happens to hold, including empty.
    """
    with app.test_client() as client:
        resp = client.get('/services', base_url='https://localhost')
        assert resp.status_code == 200, resp.status_code
        body = resp.get_data(as_text=True)
        assert 'name="robots"' not in body, 'services must not be noindex'
        # A search that finds nothing is still a 200, not a 500.
        empty = client.get('/services?search=zzznosuchservicezzz',
                           base_url='https://localhost')
        assert empty.status_code == 200, empty.status_code
        # An id that does not exist is a 404, not a traceback.
        missing = client.get('/services/999999999', base_url='https://localhost')
        assert missing.status_code == 404, missing.status_code


check('/services renders for an anonymous visitor', services_page_renders)


def badge_macro_renders():
    """The four badges, from the one macro, with the crown printing "Brand".

    `trusted` is passed explicitly so this stays fixture-free: the default calls
    seller_is_trusted, which reads the trust table. The order of the three seals is
    asserted because style.css stacks them with sibling selectors - swap two and
    they render on top of each other.
    """
    from types import SimpleNamespace
    crown, gem = chr(0x1F451), chr(0x1F48E)
    # Both of these carry a trailing U+FE0F variation selector in _badges.html, and
    # the match has to include it or the assertion would pass on a template that
    # dropped the selector. Built with chr() so this file stays pure ASCII: its
    # output is captured with the console codec by run_all_checks, and a cp1252
    # console cannot print an emoji - a failure message would become a
    # UnicodeEncodeError on top of the real failure.
    tick, shield = chr(0x2611) + chr(0xFE0F), chr(0x1F6E1) + chr(0xFE0F)
    source = ("{% from '_badges.html' import product_badges, product_badge_pills %}"
              '{{ product_badges(product, trusted=trusted) }}'
              '|{{ product_badge_pills(product, trusted=trusted) }}')
    template = app.jinja_env.from_string(source)

    seller = SimpleNamespace(id=1, is_brand=True, brand_name='Samsung',
                             is_verified_seller=True,
                             verified_seller_badge_enabled=True)
    product = SimpleNamespace(seller=seller, is_original_source=True,
                              is_hot_sale=False, is_brand_partner=False,
                              brand_label='')
    seals, pills = template.render(product=product, trusted=True).split('|')

    for part, where in ((seals, 'seals'), (pills, 'pills')):
        hidden = part.count('aria-hidden="true"')
        assert '>Brand<' in part or '> Brand<' in part, f'{where}: crown not labelled Brand'
        assert 'Samsung' not in part.replace('title="Samsung"', ''), \
            f'{where}: brand name printed instead of kept in the tooltip'
        assert 'title="Samsung"' in part, f'{where}: brand name lost from the tooltip'
        assert crown in part, f'{where}: crown emoji missing'
        assert gem in part, f'{where}: gem emoji missing'
        assert tick in part, f'{where}: tick emoji missing'
        assert shield in part, f'{where}: shield emoji missing'
        assert 'Original' in part and 'Verified Seller' in part and 'Trusted' in part, \
            f'{where}: a badge label is missing'
        # Every emoji is hidden from screen readers and the word beside it is not,
        # so the badge is read as a word rather than as "crown".
        assert hidden == 4, f'{where}: expected 4 hidden emoji, got {hidden}'

    assert seals.index('Original') < seals.index('Verified Seller') < seals.index('Trusted'), \
        'seal order changed; style.css stacks them with sibling selectors'

    # Nothing set: no badges at all, rather than four empty shells.
    bare_seller = SimpleNamespace(id=2, is_brand=False, brand_name='',
                                  is_verified_seller=False,
                                  verified_seller_badge_enabled=False)
    bare = SimpleNamespace(seller=bare_seller, is_original_source=False,
                           is_hot_sale=False, is_brand_partner=False, brand_label='')
    blank_seals, blank_pills = template.render(product=bare, trusted=False).split('|')
    assert not blank_seals.strip(), f'unset product still rendered: {blank_seals[:60]!r}'
    assert not blank_pills.strip(), f'unset product still rendered: {blank_pills[:60]!r}'

    # A verified seller without the privilege gets no seal: the badge is an admin
    # grant, not a side effect of the seller_status column.
    ungranted = SimpleNamespace(id=3, is_brand=False, brand_name='',
                                is_verified_seller=True,
                                verified_seller_badge_enabled=False)
    gated = SimpleNamespace(seller=ungranted, is_original_source=False,
                            is_hot_sale=False, is_brand_partner=False, brand_label='')
    out = template.render(product=gated, trusted=False)
    assert 'Verified Seller' not in out, 'verified seal rendered without the grant'

    # A brand-partner product with no seller account still gets the crown, and a
    # missing seller must not raise - index.html renders cards for deleted sellers.
    orphan = SimpleNamespace(seller=None, is_original_source=False, is_hot_sale=True,
                             is_brand_partner=True, brand_label='Anker')
    orphan_out = template.render(product=orphan, trusted=False)
    assert 'title="Anker"' in orphan_out, 'brand partner label lost'
    assert 'brand-tag-stacked' in orphan_out, 'hot-sale brand tag not stacked'


check('badge macro renders four badges in a fixed order', badge_macro_renders)


# ---------- Rate limiting ----------
# The default ceilings apply to every registered endpoint, so this is the widest
# blast radius of anything in this file: get it wrong and the platform refuses
# ordinary browsing, or - worse - answers 429 to its own health check and the host
# restarts the container for looking unhealthy. Both halves are asserted, because
# "no limit was applied" and "a limit was applied and happened to pass" are
# indistinguishable from the status code alone.


def rate_limit_exempt_endpoints():
    """The endpoints the ceilings skip, and the ones they must not.

    rate_limit_exempt reads request.endpoint, so every case needs a real URL rather
    than a made-up endpoint name - which is the point: it also proves each name in
    RATE_LIMIT_EXEMPT_ENDPOINTS still matches a route. A renamed view would leave a
    stale string in that set silently exempting nothing.
    """
    for url, endpoint in (('/static/style.css', 'static'),
                          ('/favicon.ico', 'favicon'),
                          ('/manifest.webmanifest', 'web_app_manifest'),
                          ('/sw.js', 'service_worker'),
                          ('/healthz', 'healthz'),
                          ('/robots.txt', 'robots_txt')):
        with app.test_request_context(url):
            assert main.request.endpoint == endpoint, \
                f'{url} resolves to {main.request.endpoint!r}, not {endpoint!r}'
            assert main.rate_limit_exempt() is True, url
    assert set(main.RATE_LIMIT_EXEMPT_ENDPOINTS) == {
        'static', 'favicon', 'web_app_manifest', 'service_worker', 'healthz',
        'robots_txt'}, sorted(main.RATE_LIMIT_EXEMPT_ENDPOINTS)

    # Ordinary pages stay limited, and so does an unrouted URL: with no endpoint to
    # name, counting the request is the safe direction.
    for url in ('/services', '/about', '/zzz-no-such-url-here'):
        with app.test_request_context(url):
            assert main.rate_limit_exempt() is False, url


check('the ceilings skip static and healthz, nothing else', rate_limit_exempt_endpoints)


def rate_limit_key_prefers_the_account():
    """Signed-in requests key on the account; anonymous ones on the address.

    Kenyan carrier NAT puts many subscribers behind one public IPv4, so two
    signed-in people sharing an address must not share a bucket. g._login_user is
    set directly because that is the cache Flask-Login itself writes - it exercises
    the real current_user proxy with no database fixture, and it is the same cache
    rate_limit_key's docstring relies on for costing no extra query.
    """
    from types import SimpleNamespace
    import flask

    nat = {'REMOTE_ADDR': '41.90.64.7'}
    with app.test_request_context('/', environ_base=nat):
        # Reads through the real anonymous user before anything is cached.
        assert main.rate_limit_key() == '41.90.64.7', main.rate_limit_key()
        flask.g._login_user = SimpleNamespace(is_authenticated=True,
                                              get_id=lambda: '4242')
        assert main.rate_limit_key() == 'u4242', main.rate_limit_key()

    with app.test_request_context('/', environ_base=nat):
        flask.g._login_user = SimpleNamespace(is_authenticated=True,
                                              get_id=lambda: '77')
        assert main.rate_limit_key() == 'u77', main.rate_limit_key()

    # A user object that raises on attribute access must fall back to the address
    # rather than taking the request down: this runs in before_request on every
    # single request, so an exception here is a site-wide 500.
    class Exploding:
        @property
        def is_authenticated(self):
            raise RuntimeError('login manager not ready')

    with app.test_request_context('/', environ_base=nat):
        flask.g._login_user = Exploding()
        assert main.rate_limit_key() == '41.90.64.7', main.rate_limit_key()


check('rate limit buckets are per account, not per address',
      rate_limit_key_prefers_the_account)


def rate_limit_headers_name_who_is_counted():
    """End to end: an exempt request has no limit attached to it at all.

    RATELIMIT_HEADERS_ENABLED is on, and Flask-Limiter only emits X-RateLimit-* when
    it actually evaluated a limit for the request. So the presence of that header is
    a direct reading of "this request was counted" - far more precise than a status
    code, which is 200 both when a limit passed and when none existed.
    """
    with app.test_client() as client:
        page = client.get('/services', base_url='https://localhost')
        assert 'X-RateLimit-Limit' in page.headers, (
            'an ordinary page carries no rate limit header: the default ceilings '
            'are not live at all, so nothing is being limited')
        for url in ('/static/style.css', '/favicon.ico', '/healthz',
                    '/manifest.webmanifest', '/sw.js', '/robots.txt'):
            resp = client.get(url, base_url='https://localhost')
            # healthz answers 503 when the database is unreachable, which is a
            # legitimate answer here; 429 never is.
            assert resp.status_code != 429, (url, resp.status_code)
            assert 'X-RateLimit-Limit' not in resp.headers, (
                f'{url} still counts against the caller '
                f'({resp.headers.get("X-RateLimit-Limit")} ceiling)')


check('static and healthz are counted against nobody',
      rate_limit_headers_name_who_is_counted)


def static_burst_is_never_refused():
    """The failure the exemption exists for, fired for real.

    One shop page carrying a dozen locally stored product images used to spend
    thirteen requests of the caller's allowance instead of one, so the old "200 per
    hour" ceiling was reached in roughly fifteen page views. Firing more requests
    than the per-minute ceiling allows is the only way to prove the exemption holds
    under repetition rather than only on the first request.
    """
    try:
        ceiling = int(str(main.RATE_LIMIT_PER_MINUTE).split()[0])
    except (ValueError, IndexError):
        raise AssertionError(
            f'unparseable RATE_LIMIT_PER_MINUTE: {main.RATE_LIMIT_PER_MINUTE!r}')
    burst = ceiling + 5
    if burst > 400:
        # Said out loud rather than quietly shortening the loop. A suite that
        # reports a pass it did not run is worse than one that admits a skip.
        print(f'         (burst skipped: a ceiling of {ceiling}/minute is too high '
              f'to fire from a smoke test - the header check above still proves '
              f'the exemption)')
        return
    with app.test_client() as client:
        for index in range(burst):
            resp = client.get('/static/style.css', base_url='https://localhost')
            assert resp.status_code != 429, (
                f'a static file was refused after {index + 1} requests against a '
                f'{ceiling}/minute ceiling; the exemption is not working')
        # And none of it touched the caller's real allowance. Compared as "how much
        # has been spent" so the assertion does not depend on which of the two
        # ceilings Flask-Limiter chooses to report. The slack covers the handful of
        # ordinary page requests the checks above this one already made.
        page = client.get('/services', base_url='https://localhost')
        limit = int(page.headers['X-RateLimit-Limit'])
        spent = limit - int(page.headers['X-RateLimit-Remaining'])
        assert spent <= 40, (
            f'{burst} static requests spent {spent} of a {limit} allowance; they '
            f'should have spent none')


check('a burst of static files is never refused', static_burst_is_never_refused)


print()
if failures:
    print(f'{len(failures)} check(s) failed')
    sys.exit(1)
print(f'all {len(passed)} checks passed')
