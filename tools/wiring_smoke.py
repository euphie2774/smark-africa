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

After that comes a group about things that are broken in the browser and correct
everywhere else, which is why they survived. A host missing from the CSP is refused
with no error anywhere - the server sends 200, the markup arrives, the page lays
out, and only the asset vanishes; Font Awesome was loaded from a host in no
directive at all, so every icon on the site drew as nothing. The nav drawer opened
behind its own backdrop, because a sticky navbar is a stacking context and the
backdrop is appended outside it, so the drawer's z-index was never compared to the
backdrop's. And a .container sharing an element with a .row hung a gutter over both
screen edges, which is what scrolled the phone sideways into blank space. None of
these can be seen from a status code, so each is asserted against the CSS or markup
where its fix lives; confirming them by eye stays a manual step.
"""
import os, sys
import pathlib
import re
from array import array

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['DISABLE_BACKGROUND_JOBS'] = '1'
# Prove the new env vars are optional and Redis stays optional.
for key in ('MARKET_COMPARABLE_TTL', 'PRICE_CHECK_RATE_LIMIT', 'PHONE_EVIDENCE_MIN_SCORE',
            'PHONE_EVIDENCE_REQUIRED', 'PHONE_EVIDENCE_RATE_LIMIT',
            'RATE_LIMIT_PER_HOUR', 'RATE_LIMIT_PER_MINUTE',
            'PRODUCT_SEARCH_IDS_TTL', 'PRODUCT_SEARCH_IDS_MAX',
            'SEARCH_FLIGHT_STRIPES', 'SEARCH_FLIGHT_WAIT', 'SITEMAP_FLIGHT_WAIT',
            'SEARCH_EXPANSION_FLIGHT_WAIT',
            'REDIS_URL', 'CACHE_REDIS_URL'):
    os.environ.pop(key, None)

from scale import CACHE_MISS, SingleFlight, pack_ids, unpack_ids

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


def growing_key_space_stays_off_disk():
    """A key space that grows with traffic must not land on FileSystemCache.

    This module boots with REDIS_URL unset, so the shared cache here is the
    filesystem backend - the one whose set() cost measured ~896ms against ~15ms
    below CACHE_THRESHOLD, and permanently, because cachelib's prune only trims
    back down to the threshold rather than below it. Searches are the key space
    that grows with traffic - one entry per search, filter and sort combination -
    so the guard is that many distinct ones write nothing to disk at all.

    Without this, the cliff is reintroduced by any future change that reaches for
    the shared ``cache`` on a per-request key, and nothing would notice until a
    deploy without Redis got real search traffic.
    """
    with app.app_context():
        backend = type(getattr(main.cache, 'cache', None)).__name__
        assert backend == 'FileSystemCache', f'expected the filesystem backend, got {backend}'
        assert main.search_ids_cache_target() == 'local', main.search_ids_cache_target()
        cache_dir = app.config['CACHE_DIR']
        before = len(os.listdir(cache_dir))
        for i in range(60):
            main.cached_product_search_ids(search=f'wiringsmoke-{i}')
        after = len(os.listdir(cache_dir))
        assert after == before, (
            f'{after - before} per-search entries reached the disk cache; a growing '
            f'key space belongs in a bounded TTLCache, not the shared cache')


check('a growing cache key space never reaches the disk cache',
      growing_key_space_stays_off_disk)


def cache_keys_are_stable():
    """The same page asked for twice must build the same cache key.

    ``slugify`` ends in ``uuid4().hex[:8]`` for an empty or punctuation-only value,
    which a product slug needs and a cache key must never have. The default search
    on /shop is '' and so is the default product type, and /categories/<slug> passes
    no search at all, so every one of those requests was building a key nothing
    could ever match: the busiest anonymous pages on the site read their own cache
    zero times and wrote a fresh entry per request instead of per distinct search.

    Asserted as hit and miss *deltas* rather than absolute counters, because the
    counters are cumulative for the life of the process and any probe in this file
    moves them - an absolute assertion here passed and failed for reasons that had
    nothing to do with the code under test.

    ``sort`` and ``type`` come straight off the query string, so the punctuation
    case is the one an outsider can drive: without it, ?sort=! repeated is one new
    cache entry per request on every backend.
    """
    with app.app_context():
        ids_cache = main._product_ids_cache
        assert main.product_search_cache_key('', '', '', 'newest') == \
            main.product_search_cache_key('', '', '', 'newest'), \
            'an empty search built two different keys'

        ids_cache.clear()
        hits, misses = ids_cache.hits, ids_cache.misses
        main.cached_product_search_ids()
        main.cached_product_search_ids()
        # +1 hit, and exactly +2 misses for two calls. The second miss is the
        # single-flight re-reading the cache after taking the stripe: the first caller
        # of a cold key looks twice on purpose, because between its own miss and the
        # lock another thread may have filled the entry. Pinned rather than loosened to
        # >=1 so both directions still fail loudly - one miss would mean the
        # double-check was dropped and concurrent callers all query, three would mean
        # the compute path is reading the cache again after writing it.
        assert ids_cache.hits - hits == 1 and ids_cache.misses - misses == 2, (
            f'the unfiltered shop page did not hit its own cache on repeat: '
            f'+{ids_cache.hits - hits} hits, +{ids_cache.misses - misses} misses')
        assert len(ids_cache._data) == 1, (
            f'{len(ids_cache._data)} entries for one repeated page; the key is not stable')

        category = main.Category.query.first()
        if category is not None:
            ids_cache.clear()
            hits = ids_cache.hits
            main.cached_product_search_ids(category_slug=category.slug)
            main.cached_product_search_ids(category_slug=category.slug)
            assert ids_cache.hits - hits == 1, (
                f'/categories/{category.slug} did not hit its own cache on repeat')

        ids_cache.clear()
        for junk in ('!', '!!', '@@@', '---', ' ', '...', '???', '&&', '%%', '(('):
            main.cached_product_search_ids(search='', sort=junk)
        assert len(ids_cache._data) == 1, (
            f'{len(ids_cache._data)} entries from punctuation-only sort values; a '
            f'scanner varying punctuation can grow the key space per request')

        # The flip side, and the harder half: the key must not collapse searches that
        # the database would answer differently. The search term reaches SQL as
        # ILIKE '%term%', so these two are different queries over different products,
        # and sharing one entry means whichever missed first decides what the other
        # shopper is shown - wrong results that look perfectly plausible.
        ids_cache.clear()
        main.cached_product_search_ids(search='sm a14')
        main.cached_product_search_ids(search='sm-a14')
        assert len(ids_cache._data) == 2, (
            f'"sm a14" and "sm-a14" share a cache entry; they are different LIKE '
            f'patterns and one search is being served the other search results')
        # Two long searches that differ only past a truncation point are also
        # different queries, and used to collapse into the same key.
        ids_cache.clear()
        stem = 'wiringsmoke ' + ('long ' * 20)
        main.cached_product_search_ids(search=stem + 'alpha')
        main.cached_product_search_ids(search=stem + 'omega')
        assert len(ids_cache._data) == 2, (
            'two long searches sharing a prefix collapsed into one cache entry')
        # But case is not a difference: ILIKE is case-insensitive, so these really
        # are one query and splitting them would just halve the hit rate.
        ids_cache.clear()
        main.cached_product_search_ids(search='Phone')
        main.cached_product_search_ids(search='phone')
        assert len(ids_cache._data) == 1, (
            f'{len(ids_cache._data)} entries for "Phone" and "phone"; ILIKE does not '
            f'distinguish them, so neither should the key')

        # And unrecognised filter values must collapse, because the query builder
        # ignores them: every one of these produces the same SQL as the default.
        ids_cache.clear()
        for junk in ('digitalish', 'DIGITAL', 'physical-ish', 'nonsense', '..'):
            main.cached_product_search_ids(product_type=junk)
        assert len(ids_cache._data) == 1, (
            f'{len(ids_cache._data)} entries from unrecognised product types; they all '
            f'produce the same SQL as no filter and belong in one bucket')

        # The trap in collapsing them: the first version of this key lowercased the
        # value, so 'DIGITAL' - which the builder does *not* filter on, because it
        # compares against 'digital' exactly - landed on the same entry as the real
        # 'digital'. An unfiltered result served as a digital-only one, from a query
        # string anyone can type. Same shape for the category slug, which reaches SQL
        # through filter_by(slug=...) and is therefore case-sensitive in the database.
        # Both keys are now built from what the query itself branches on, so a value
        # that changes the SQL cannot share an entry with one that does not.
        ids_cache.clear()
        main.cached_product_search_ids(product_type='digital')
        main.cached_product_search_ids(product_type='DIGITAL')
        assert len(ids_cache._data) == 2, (
            '?type=DIGITAL shares a cache entry with ?type=digital, but the query '
            'only filters on the lowercase one - an unfiltered result is being '
            'served as a digital-only one')
        if category is not None and category.slug != category.slug.upper():
            ids_cache.clear()
            main.cached_product_search_ids(category_slug=category.slug)
            main.cached_product_search_ids(category_slug=category.slug.upper())
            assert len(ids_cache._data) == 2, (
                f'?category={category.slug.upper()} shares an entry with '
                f'{category.slug}, but filter_by(slug=...) matches case-sensitively, '
                f'so only one of them is actually filtered')

        # Sort values that select the same ORDER BY are one query, though, and must
        # share - effective_sort is the single place that decides which do.
        ids_cache.clear()
        main.cached_product_search_ids(sort='rating')
        main.cached_product_search_ids(sort='popular')
        assert len(ids_cache._data) == 1, (
            f'{len(ids_cache._data)} entries for sort=rating and sort=popular; they '
            f'take the same ORDER BY branch, so they are one result')
        assert main.effective_sort('price_low') == 'price_low'
        assert main.effective_sort('!') == main.effective_sort('') == 'newest'
        assert main.effective_product_type('digital') == 'digital'
        assert main.effective_product_type('DIGITAL') == 'any'

        ids_cache.clear()
        main.cached_product_search_ids(search='laptop')
        main.cached_product_search_ids(search='phone')
        main.cached_product_search_ids(search='laptop', sort='price_low')
        assert len(ids_cache._data) == 3, (
            f'{len(ids_cache._data)} entries for three different searches; the key is '
            f'collapsing results that are not the same')

        # And slugify itself keeps the uniqueness real slugs depend on.
        assert main.slugify('Hello World') == 'hello-world'
        assert main.slugify('') != main.slugify(''), 'slugify lost its unique fallback'


check('the same page builds the same cache key every time', cache_keys_are_stable)


def cached_id_lists_stay_small_in_memory():
    """A cache resident for the worker's life must fit the worker's memory budget.

    These caches hold one entry per distinct search and cap each entry at 1000 ids,
    so their resident size is the entry cap times the per-entry cost. Measured: a
    1000-id list is 35.2KB of boxed Python ints, an array('q') of the same ids is
    7.9KB, so a full 2048-entry product cache is 70MB unpacked against 16MB packed -
    and WORKER_MEMORY_MB defaults to 200, per worker, with the app already in it.

    The worst case is reachable on purpose rather than only in theory: a short
    substring matches a large slice of the catalogue and is its own cache key.

    Guarded here because the packing is invisible at the call sites - both return
    plain lists - so nothing else would notice if a future edit stored the list.
    """
    with app.app_context():
        main._product_ids_cache.clear()
        main.cached_product_search_ids(search='wiringsmoke-packing')
        key = main.product_search_cache_key('wiringsmoke-packing', '', '', 'newest')
        stored = main._product_ids_cache.get(key)
        assert isinstance(stored, array), (
            f'the id cache stored a {type(stored).__name__}; ids belong in a packed '
            f'array or a full cache costs 4.5x the memory it needs to')
        # What callers get back is still an ordinary list, and a copy of it: a caller
        # that sorts the result in place must not be editing every later reader's copy.
        got = main.cached_product_search_ids(search='wiringsmoke-packing')
        assert type(got) is list, type(got).__name__
        got.append(-1)
        assert -1 not in main.cached_product_search_ids(search='wiringsmoke-packing'), \
            'a caller mutating the returned list corrupted the cached entry'
        # Packing must never be able to raise on a public search path.
        assert pack_ids([2 ** 70]) == [2 ** 70], 'pack_ids should fall back, not raise'
        assert unpack_ids(pack_ids([])) == []


check('cached id lists are packed and stay copies', cached_id_lists_stay_small_in_memory)


def single_flight_collapses_and_never_blocks():
    """One caller computes a cold entry; the rest must not, and must not be stuck.

    Concurrency itself is measured in tools/concurrency_smoke.py, which is kept out of
    the suite because thread interleaving is not reproducible. What *is* deterministic
    is the contract that measurement relies on, and all three parts of it can be
    checked with no threads at all:

      * A waiter that finds the entry present after acquiring returns it **without
        calling compute**. This is the whole mechanism - a double-check that re-reads
        rather than trusting its earlier miss. Drop the re-read and the class becomes
        an expensive no-op that still runs every query.
      * A miss falls through to compute. Obvious, and the reason the assertion exists:
        a wrapper that swallowed a genuine miss would serve empty search results.
      * ``CACHE_MISS`` is what signals absence, not ``None`` and not a falsy value. A
        cached empty list is a real answer - the searches that legitimately match
        nothing are precisely the cheap ones to cache - so treating ``[]`` as absent
        would re-run the query on every request for them.

    And the property that makes this safe to put on an anonymous page: the wait is
    bounded. With a stripe already held and a zero timeout the caller computes
    immediately rather than queueing, so the worst case is the behaviour that was
    there before the class existed.
    """
    flight = SingleFlight(stripes=4, timeout=0.5, name='wiringsmoke-flight')
    calls = []

    def compute():
        calls.append(1)
        return ['computed']

    # A cold key: nothing to find, so the work happens.
    assert flight.run('k1', lambda: CACHE_MISS, compute) == ['computed'], \
        'a cold key did not reach compute; every search would come back empty'
    assert len(calls) == 1, calls
    # A warm key: found on the re-read, so compute must not run at all.
    assert flight.run('k1', lambda: ['cached'], compute) == ['cached'], \
        'the re-read result was discarded; the flight recomputed an entry it had found'
    assert len(calls) == 1, f'compute ran for an entry that was already present: {calls}'
    # An empty list is a value, not a miss.
    assert flight.run('k2', lambda: [], compute) == [], \
        'a cached empty result was treated as absent'
    assert len(calls) == 1, f'a cached empty result was recomputed: {calls}'
    assert flight.collapsed >= 2, flight.stats()

    # Bounded wait: hold the stripe this key maps to and give the caller no time to
    # wait for it. It must compute rather than block.
    held = flight._locks[hash('k3') % len(flight._locks)]
    held.acquire()
    try:
        impatient = SingleFlight(stripes=4, timeout=0, name='wiringsmoke-nowait')
        impatient._locks = flight._locks
        assert impatient.run('k3', lambda: CACHE_MISS, compute) == ['computed']
        assert impatient.timeouts == 1, impatient.stats()
    finally:
        held.release()


check('single-flight collapses duplicate work without blocking on it',
      single_flight_collapses_and_never_blocks)


class FlightRecorder(SingleFlight):
    """A SingleFlight that notes the keys it was asked for and otherwise behaves as one.

    Shared by the two wiring checks below because they are asking the same question of
    different call sites: was the flight actually reached.
    """

    def __init__(self, name='wiringsmoke-recorder'):
        super().__init__(stripes=4, timeout=0.5, name=name)
        self.keys = []

    def run(self, key, read, compute):
        self.keys.append(key)
        return super().run(key, read, compute)


def search_paths_run_under_the_flight():
    """The searches anyone can hit must actually be wired to the flight.

    Asserted separately from the class's own behaviour because a correct
    SingleFlight that nothing calls is the failure mode with no symptom: every check
    above still passes, the module still imports, and a cold key still costs one
    query per concurrent caller. Counting the flight's own stats cannot show this -
    sequential callers never collapse anything, so those numbers stay at zero whether
    the wrapper is present or absent.

    So the flight object is swapped for a recorder that notes the keys it was asked
    for and otherwise behaves identically. Both caches are cleared first, because a
    warm entry returns before reaching the flight at all - which is the fast path
    working, and would read here as the wiring being gone.
    """
    recorder = FlightRecorder()
    original = main._search_flight
    main._search_flight = recorder
    try:
        with app.app_context():
            main._product_ids_cache.clear()
            main._service_ids_cache.clear()
            ids = main.cached_product_search_ids(search='wiringsmoke-flightpath')
            assert type(ids) is list, type(ids).__name__
            service_ids = main.cached_service_ids(search='wiringsmoke-flightpath')
            assert type(service_ids) is list, type(service_ids).__name__
    finally:
        main._search_flight = original

    assert len(recorder.keys) == 2, (
        f'expected the product and service searches to each take the flight once on a '
        f'cold cache, got {recorder.keys}')
    # The product key carries the full query signature; the service key is its own
    # shorter form. Both must be the cache key, not a constant - a single shared key
    # would serialise every unrelated search behind one lock.
    assert recorder.keys[0] == main.product_search_cache_key(
        'wiringsmoke-flightpath', '', '', 'newest'), recorder.keys[0]
    assert 'wiringsmoke-flightpath' in recorder.keys[1].lower(), recorder.keys[1]
    assert recorder.keys[0] != recorder.keys[1], recorder.keys


check('the product and service searches go through the single-flight',
      search_paths_run_under_the_flight)


def service_reads_run_under_the_flight():
    """The duty state and the catalogue must reach the flight too.

    These two were measured stampeding - 24 queries for 24 simultaneous cold callers
    in tools/concurrency_smoke.py - and the duty state is read on every service page
    and every chatbot turn behind the shortest TTL in the app, so it goes cold four
    times a minute. Wired here for the same reason the searches are: the wrapper being
    silently unreached looks exactly like it working.

    The two keys must differ. They share a stripe pool deliberately, but sharing a
    *key* would mean a catalogue miss and a duty miss each waiting on the other's
    query, and worse, a waiter's double-check reading the wrong cache and returning
    one path's value to the other.
    """
    recorder = FlightRecorder('wiringsmoke-service-recorder')
    original = main._service_flight
    main._service_flight = recorder
    try:
        with app.app_context():
            main._service_duty_cache.clear()
            main._service_catalogue_cache.clear()
            duty = main.service_duty_state()
            assert type(duty) is tuple and len(duty) == 2, duty
            catalogue = main.service_catalogue()
            assert type(catalogue) is list, type(catalogue).__name__
    finally:
        main._service_flight = original

    assert len(recorder.keys) == 2, (
        f'expected the duty state and the catalogue to each take the flight once on a '
        f'cold cache, got {recorder.keys}')
    assert recorder.keys[0] != recorder.keys[1], recorder.keys


check('the duty state and service catalogue go through the single-flight',
      service_reads_run_under_the_flight)


def render_reads_run_under_the_flight():
    """The per-render reads and the search expansion must reach their flights too.

    Same failure mode as the two checks above and the same reason it needs its own
    assertion: an unreached flight is indistinguishable from a working one. These are
    the cheap reads, which is precisely why they were the last to be guarded and why
    the guard matters - the nav categories render on nearly every page in the app, so
    a cold key there multiplies by every request in flight rather than by the traffic
    to one page.

    The expansion is checked here rather than with the searches because it is on its
    own pool: it is the only cached read that can make an outbound network call, so
    nothing cheap may queue behind it.
    """
    render = FlightRecorder('wiringsmoke-render-recorder')
    expansion = FlightRecorder('wiringsmoke-expansion-recorder')
    originals = (main._render_flight, main._expansion_flight)
    main._render_flight, main._expansion_flight = render, expansion
    try:
        with app.app_context():
            main._nav_category_cache.clear()
            main._trusted_seller_cache.clear()
            main._service_live_keys_cache.clear()
            main._search_expansion_cache.clear()
            assert type(main.nav_categories()) is list, 'nav categories not a list'
            assert type(main.trusted_seller_ids()) is frozenset, \
                'trusted seller ids not a frozenset'
            assert type(main.service_keys_with_providers()) is set, \
                'live service keys not a set'
            assert type(main.expanded_search_terms('bluetooth jammer')) is list, \
                'expansion not a list'
    finally:
        main._render_flight, main._expansion_flight = originals

    assert len(render.keys) == 3, (
        f'expected the nav categories, trusted sellers and live service keys to each '
        f'take the render flight once on a cold cache, got {render.keys}')
    assert len(set(render.keys)) == 3, (
        f'two render reads share a flight key, so each would serve the other its '
        f'value on a double-check: {render.keys}')
    assert len(expansion.keys) == 1, (
        f'expected one expansion to take its own flight, got {expansion.keys}')
    # The key must be the normalised term, not a constant: one shared key would put
    # every unrelated failed search behind a single lock, and with the AI layer on that
    # lock can be held for seconds.
    assert expansion.keys[0] == main.normalise_search_text('bluetooth jammer'), \
        expansion.keys[0]


check('the per-render reads and the expansion go through their flights',
      render_reads_run_under_the_flight)


def every_ttl_cache_read_is_guarded():
    """No cached read path may exist without somebody having thought about the cold case.

    The checks above name their call sites, which means a *new* cache added next month
    is guarded by nothing at all - and the defect this work fixed was a class of cache,
    not a handful of instances. So this one is an inventory: every TTLCache in main.py
    must be classified, and the classification must be exhaustive in both directions.
    Add a cache and this fails until it is listed; delete one and it fails until the
    stale entry goes.

    Deliberately a hand-maintained list rather than a textual heuristic. The first
    attempt at this inferred "flighted" from how many times a cache name appeared next
    to a lookup, and it was wrong for exactly the two caches whose fast path and
    double-check share one read() closure - it accused the searches, which were the
    first two paths to be flighted at all. A check that cannot be trusted about its own
    subject is worse than no check, so this asserts the inventory and leaves the
    behaviour to the recorders above and to tools/concurrency_smoke.py.
    """
    # Read through a single-flight. Each of these is proven wired by one of the
    # recorder checks above, or measured in tools/concurrency_smoke.py, or both.
    FLIGHTED = {
        '_product_ids_cache', '_service_ids_cache',        # search flight
        '_comparable_price_cache',                         # search flight
        '_service_duty_cache', '_service_catalogue_cache', # service flight
        '_nav_category_cache', '_trusted_seller_cache',    # render flight
        '_service_live_keys_cache',                        # render flight
        # Render flight too, not the search one: the key space is a single key
        # rather than one per distinct query, so an expiry costs the admin polls in
        # flight at that moment, not every caller of one page.
        '_market_facts_cache',                             # render flight
        '_search_expansion_cache',                         # its own pool
        '_sitemap_entry_cache',                            # its own pool
    }
    # Not flighted, with the reason. An entry here is a decision, not an oversight.
    EXEMPT = {
        # The assembled XML per host. Its query layer is _sitemap_entry_cache, which is
        # flighted; what is left is string concatenation over a list already in memory,
        # and duplicating that costs nothing a lock would not also cost.
        '_sitemap_cache': 'assembly only, no query - the entries cache is flighted',
    }
    source = pathlib.Path(main.__file__).read_text(encoding='utf-8')
    found = set(re.findall(r'^(_\w+)\s*=\s*TTLCache\(', source, re.M))
    assert len(found) >= 12, f'expected to find the caches, found {sorted(found)}'
    unclassified = sorted(found - FLIGHTED - set(EXEMPT))
    assert not unclassified, (
        f'new cached read path(s) with no decision recorded about the cold case: '
        f'{unclassified}. A cold key costs one query per concurrent caller unless the '
        f'miss goes through a single-flight. Wrap it and add it to FLIGHTED here, or '
        f'add it to EXEMPT with the reason.')
    stale = sorted((FLIGHTED | set(EXEMPT)) - found)
    assert not stale, f'these caches no longer exist and should leave the list: {stale}'


check('every TTLCache in main.py is read through a flight or exempted',
      every_ttl_cache_read_is_guarded)


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


def sitemap_host_cannot_cost_a_query():
    """An unfamiliar Host must not make the database rebuild the sitemap.

    ``ProxyFix(x_host=1)`` is active wherever TRUST_PROXY_HEADERS or RENDER is set,
    so ``request.host`` is whatever ``X-Forwarded-Host`` carried. This document was
    keyed on that host in a four-entry cache, so a client varying the header per
    request missed every time and re-ran the build - which is a query capped at
    SITEMAP_MAX_PRODUCTS, default 20,000 rows, on an unauthenticated GET with no
    body. The paths do not depend on the host, so they are now cached once and only
    the URL assembly is per host.

    Counted as SQL statements rather than cache statistics: the claim is that the
    database is not touched, and only watching the database proves that.
    """
    from sqlalchemy import event
    seen = []

    def record(conn, cursor, statement, *rest):
        if 'from products' in (statement or '').lower():
            seen.append(statement)

    with app.app_context():
        main._sitemap_cache.clear()
        main._sitemap_entry_cache.clear()
        with app.test_client() as client:
            client.get('/sitemap.xml', base_url='https://localhost')  # warm, outside the window
            event.listen(main.db.engine, 'before_cursor_execute', record)
            try:
                for i in range(12):
                    resp = client.get('/sitemap.xml', base_url=f'https://host{i}.example.com')
                    assert resp.status_code == 200, resp.status_code
            finally:
                event.remove(main.db.engine, 'before_cursor_execute', record)
    assert seen == [], (
        f'{len(seen)} product queries ran for 12 unfamiliar hosts; the host-independent '
        f'half of the sitemap is being rebuilt per host')


check('an unfamiliar Host cannot make the sitemap re-query', sitemap_host_cannot_cost_a_query)


def sitemap_publishes_the_configured_host():
    """With APP_BASE_URL set, a request cannot get its own host into the document.

    Every visitor served this document from cache would otherwise be handed URLs on
    a host some other request named, and a crawler reading them would treat that
    host as the site. APP_BASE_URL and PUBLIC_URL are the two variables
    ``canonical_url`` already prefers, so this adds no new configuration.
    """
    had = os.environ.get('APP_BASE_URL')
    os.environ['APP_BASE_URL'] = 'https://smark-africa.example'
    try:
        with app.app_context():
            main._sitemap_cache.clear()
            main._sitemap_entry_cache.clear()
            with app.test_client() as client:
                body = client.get('/sitemap.xml',
                                  base_url='https://attacker.invalid').get_data(as_text=True)
        assert 'attacker.invalid' not in body, 'the request host reached the sitemap'
        assert 'https://smark-africa.example/shop' in body, body[:400]
    finally:
        if had is None:
            os.environ.pop('APP_BASE_URL', None)
        else:
            os.environ['APP_BASE_URL'] = had
        with app.app_context():
            main._sitemap_cache.clear()
            main._sitemap_entry_cache.clear()


check('the sitemap publishes the configured host, not the requested one',
      sitemap_publishes_the_configured_host)


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


# Every third-party host a template loads has to be named in the CSP directive
# that governs how it is loaded. A missing host is refused by the browser and
# nothing anywhere reports it: the server sends 200, the markup arrives intact,
# the page lays out, and only the asset is silently dropped. Font Awesome was
# loaded from cdnjs.cloudflare.com by base.html and every other page, and that
# host was in no directive at all, so every <i class="fas fa-*"> on the site drew
# as nothing - which looks like a design choice rather than a bug, and had
# survived however long it had been there.
ASSET_TAG = re.compile(r'<(link|script)\b[^>]*?>', re.I)
ASSET_URL = re.compile(r'(?:href|src)=["\'](https://[^"\'/]+)', re.I)


def external_assets():
    """Yield (host, directive, template) for every third-party asset a template loads.

    The directive follows how the browser fetches the thing, not what it is: a
    stylesheet is style-src, a script is script-src. rel is read rather than
    assumed, because a preconnect or a manifest carries an href too and neither is
    governed by style-src - counting those would report failures that are not real.
    Templates that do not extend base.html are walked as well; two of them load
    their own copies of these CDNs.
    """
    for path in sorted(pathlib.Path(ROOT, 'templates').rglob('*.html')):
        body = path.read_text(encoding='utf-8', errors='replace')
        for tag in ASSET_TAG.finditer(body):
            text = tag.group(0)
            found = ASSET_URL.search(text)
            if not found:
                continue  # url_for(), a relative path, or a data: URI
            rel = path.relative_to(pathlib.Path(ROOT, 'templates')).as_posix()
            if tag.group(1).lower() == 'script':
                yield found.group(1), 'script-src', rel
            elif 'stylesheet' in text.lower():
                yield found.group(1), 'style-src', rel


def csp_allows_every_asset_a_template_loads():
    scanned = list(external_assets())
    # A scan that silently matched nothing would pass this check for the wrong
    # reason, so the scanner is asserted to have found something first.
    assert len(scanned) >= 10, f'only {len(scanned)} external assets found; scanner is broken'
    missing = set()
    for host, directive, template in scanned:
        allowed = main.csp.get(directive) or []
        if isinstance(allowed, str):
            allowed = [allowed]
        if host not in allowed:
            missing.add(f'{host} loaded by {template} is absent from {directive}')
    assert not missing, '; '.join(sorted(missing))


check('the CSP allows every external asset the templates load',
      csp_allows_every_asset_a_template_loads)


def icon_host_may_also_serve_its_fonts():
    """style-src gets the stylesheet in; font-src is what gets the glyphs in.

    Font Awesome's stylesheet then asks the same host for .woff2 files, so allowing
    only the stylesheet swaps invisible icons for blank boxes - still broken, and
    harder to recognise. The scan above cannot see this: the font request is made
    by the stylesheet, not written in any template.
    """
    fonts = main.csp.get('font-src') or []
    assert main.ICON_ASSET_HOST in fonts, f'{main.ICON_ASSET_HOST} not in font-src: {fonts}'


check('the icon host may serve its own webfonts', icon_host_may_also_serve_its_fonts)


def the_nav_drawer_outranks_its_own_backdrop():
    """The hamburger bug: the drawer was painted behind the dimming it triggers.

    #navbarNav is inside .navbar.sticky-top. position: sticky with a z-index makes
    that navbar a stacking context, so the drawer's own z-index only orders it
    against its siblings inside the navbar's layer - while Bootstrap appends the
    backdrop to <body>, in the layer above. The two numbers are never compared, so
    raising the drawer does nothing and the navbar's layer is what has to move.
    Asserted in CSS rather than in a browser because that is where the fix lives
    and this suite has no DOM; the visual confirmation stays a manual step.
    """
    css = pathlib.Path(ROOT, 'static', 'style.css').read_text(encoding='utf-8',
                                                              errors='replace')
    match = re.search(r'\.navbar\.sticky-top\s*\{[^}]*?z-index:\s*(\d+)', css)
    assert match, '.navbar.sticky-top has no z-index; the drawer opens behind the backdrop'
    # 1040 is Bootstrap 5.3's $zindex-offcanvas-backdrop.
    assert int(match.group(1)) > 1040, (
        f'navbar layer is z-index {match.group(1)}, at or below the offcanvas '
        f'backdrop at 1040, so the drawer is painted behind it')
    markup = pathlib.Path(ROOT, 'templates', 'base.html').read_text(encoding='utf-8',
                                                                    errors='replace')
    assert 'data-bs-toggle="offcanvas"' in markup, 'the toggler no longer opens an offcanvas'
    assert 'offcanvas-lg' in markup, 'the drawer is no longer a responsive offcanvas'


check('the nav drawer outranks its own backdrop', the_nav_drawer_outranks_its_own_backdrop)


# Properties that make an element a containing block for its position: fixed
# descendants. transform and filter are the two everyone knows; backdrop-filter,
# perspective and contain: paint|layout|strict|content do it too, and will-change
# naming any of them does it pre-emptively.
CONTAINING_BLOCK_PROPS = re.compile(
    r'(?<![\w-])(?:transform|filter|backdrop-filter|perspective)\s*:\s*(?!none\b)'
    r'|(?<![\w-])will-change\s*:[^;]*(?<![\w-])(?:transform|filter|perspective)(?![\w-])'
    r'|(?<![\w-])contain\s*:[^;]*(?<![\w-])(?:paint|layout|strict|content)(?![\w-])')

# Everything the nav drawer is nested inside: body > nav.navbar.sticky-top >
# .container > #navbarNav. Bootstrap positions the drawer with position: fixed,
# which resolves against the viewport - unless one of these becomes a containing
# block, and then it resolves against that instead.
DRAWER_ANCESTORS = {
    'html', 'body', 'main', 'nav', '.navbar', '.navbar.sticky-top', '.sticky-top',
    '.container', '.container-fluid', '.navbar > .container', '.navbar .container',
}


def selector_parts(selector):
    """Split a selector list on top-level commas only.

    ``:where(img:not([width]), video:not([width]))`` is one selector that contains
    a comma, so a plain split cuts it in half and neither half parses.
    """
    parts, depth, buf = [], 0, ''
    for ch in selector:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and not depth:
            parts.append(buf)
            buf = ''
        else:
            buf += ch
    parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def css_rules(css):
    """Yield (order, selector, declarations) for the innermost rules in a sheet.

    The pattern cannot cross a brace, so an @media wrapper never matches as a
    selector and its contents are yielded on their own. That loses the breakpoint,
    which is deliberate: a containing block on .navbar is a bug in every media
    query, so knowing which one it was written under would not change the answer.
    """
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    for order, match in enumerate(re.finditer(r'([^{}]+)\{([^{}]*)\}', css)):
        yield order, ' '.join(match.group(1).split()), match.group(2)


def specificity(selector):
    """(ids, classes, elements) for one selector, per Selectors 4.

    ``:where()`` contributes nothing whatsoever, which is the entire reason the
    image reset is wrapped in it. ``:is()``, ``:not()`` and ``:has()`` contribute
    their own argument's weight, so unwrapping them to bare text counts the same.
    """
    sel = selector.strip()
    while True:
        start = sel.lower().find(':where(')
        if start < 0:
            break
        depth, i = 0, start + len(':where')
        while i < len(sel):
            if sel[i] == '(':
                depth += 1
            elif sel[i] == ')':
                depth -= 1
                if not depth:
                    break
            i += 1
        sel = sel[:start] + sel[i + 1:]
    sel = re.sub(r':(?:is|not|has|matches)\(', ' ', sel, flags=re.I).replace(')', ' ')
    ids = len(re.findall(r'#[\w-]+', sel))
    rest = re.sub(r'#[\w-]+', ' ', sel)
    classes = (len(re.findall(r'\.[\w-]+', rest))
               + len(re.findall(r'\[[^\]]*\]', rest))
               + len(re.findall(r'(?<!:):(?!:)[\w-]+', rest)))
    bare = re.sub(r'\.[\w-]+|\[[^\]]*\]|::?[\w-]+', ' ', rest)
    elements = len(re.findall(r'(?<![\w-])[a-zA-Z][\w-]*', bare))
    return ids, classes, elements


def the_drawer_resolves_against_the_viewport():
    """The white gap and the dead-looking hamburger, from one declaration.

    .navbar carried ``backdrop-filter: blur(10px)``. A backdrop-filter other than
    none makes an element a containing block for its position: fixed descendants,
    and the drawer is one: below 992px Bootstrap gives #navbarNav position: fixed
    with top/right/bottom 0, a 300px width, and translateX(100%) while it is shut.
    Resolved against the navbar instead of the viewport that is three bugs at once:

      - shut, translateX(100%) parks it 300px past the navbar's right edge, so the
        document is 300px wider than the screen. The phone scrolls sideways into
        bare canvas - a shut drawer is visibility: hidden, so nothing paints out
        there, which is why the gap was blank white rather than dark like the
        drawer that was causing it.
      - open, top: 0 and bottom: 0 resolved to the navbar's own height, so the
        drawer was a 300px-wide sliver inside the bar rather than a full-height
        sheet. Tapping the toggle read as nothing happening.
      - and the stretched navbar container is what sat the brand logo wrong.

    Nothing was gained for it: the navbar's own background is 95% opaque, so the
    blur behind it could not be seen, and it cost a per-frame GPU blur of
    everything scrolling under a sticky bar on mid-range Android hardware.
    """
    css = pathlib.Path(ROOT, 'static', 'style.css').read_text(encoding='utf-8',
                                                              errors='replace')
    offenders = []
    for _order, selector, body in css_rules(css):
        found = CONTAINING_BLOCK_PROPS.search(body)
        if not found:
            continue
        for part in selector_parts(selector):
            if part in DRAWER_ANCESTORS:
                offenders.append(f'{part} sets {found.group(0).split(":")[0].strip()}')
    assert not offenders, (
        'a containing block on an ancestor of the position: fixed nav drawer '
        're-resolves the drawer against that element instead of the viewport, '
        'which hangs it off the right edge of the document: '
        + '; '.join(sorted(set(offenders))))


check('the nav drawer resolves against the viewport',
      the_drawer_resolves_against_the_viewport)


def no_reset_outranks_a_sized_image():
    """The messy home page: every card image had silently lost its crop height.

    The image reset at the end of the sheet is a reset, so it has to lose every
    argument it has with a component rule. Spelt the obvious way,
    ``img:not([width])`` is (0,1,1) - which outranked .site-logo at (0,1,0)
    outright and tied seven ``.thing img`` rules, and being last in the file it won
    every tie. Those heights are exactly what crops a card image to a uniform box,
    so the grid rendered as cards no two of which were the same height, and the
    navbar brand scaled to whatever max-width made of the logo's real proportions.

    Compared by computed specificity rather than by looking for ``:where(``, so a
    future reset that reintroduces the inversion is caught however it is spelt.
    """
    css = pathlib.Path(ROOT, 'static', 'style.css').read_text(encoding='utf-8',
                                                              errors='replace')
    resets, sized = [], []
    for order, selector, body in css_rules(css):
        # min-height and max-height are a different question and are left alone;
        # the lookbehind is what keeps them out.
        height = re.search(r'(?<![\w-])height\s*:\s*([^;!}]+)', body)
        if not height:
            continue
        value = height.group(1).strip().lower()
        parts = selector_parts(selector)
        generic = all(
            re.fullmatch(r'(?:img|video|canvas)(?::not\([^)]*\))?', p, re.I)
            or ':where(' in p.lower()
            for p in parts)
        entry = (specificity(selector), order, selector, value)
        if generic and value in ('auto', 'inherit', 'initial', 'revert', 'unset'):
            resets.append(entry)
        elif (any(re.search(r'(?:img|video)$', p, re.I) for p in parts)
              or 'logo' in selector.lower()):
            sized.append(entry)
    assert resets, 'the image height reset is gone; max-width: 100% alone squashes an image'
    beaten = []
    for spec, order, selector, value in sized:
        for rspec, rorder, rsel, _v in resets:
            if rspec > spec or (rspec == spec and rorder > order):
                beaten.append(f'{selector} (height: {value}) loses to {rsel}')
    assert not beaten, (
        'the image height reset outranks a component that sets an explicit height, '
        'so that image renders at its natural aspect instead of its crop: '
        + '; '.join(sorted(set(beaten))))
    assert any('site-logo' in s for _sp, _o, s, _v in sized), (
        '.site-logo sets no height any more, so the navbar brand is sized only by '
        'max-width and scales to whatever proportions the logo file has')


check('no image reset outranks a component that sizes an image',
      no_reset_outranks_a_sized_image)


def nothing_widens_the_page_past_the_viewport():
    """The sideways scroll into blank space, asserted at its two causes.

    .container and .row on one element is the specific bug that caused it: the
    container's padding and the row's negative margin are the same size and do not
    cancel on a single element, so the box hangs a full gutter over both edges. On
    a phone that is the whole complaint, and in the installed app the blank it
    scrolls into is the manifest's white background_color rather than the site's
    cream, which is why it was reported as a white page.

    The containment guard is asserted separately because it is what stops the next
    one, and it has to be clip rather than hidden - hidden would make the element a
    scroll container and the sticky navbar resolves against the nearest scrollport,
    so hidden is the standard way to break a sticky header while fixing this.
    """
    both = []
    for path in sorted(pathlib.Path(ROOT, 'templates').rglob('*.html')):
        body = path.read_text(encoding='utf-8', errors='replace')
        for attr in re.finditer(r'class="([^"]*)"', body):
            names = attr.group(1).split()
            if 'row' in names and 'container' in names:
                both.append(path.relative_to(pathlib.Path(ROOT, 'templates')).as_posix())
    assert not both, (
        f'container and row share an element in {sorted(set(both))}; the negative '
        f'margin hangs over the viewport and scrolls the page sideways')

    css = pathlib.Path(ROOT, 'static', 'style.css').read_text(encoding='utf-8',
                                                              errors='replace')
    guard = re.search(r'html\s*\{[^}]*?overflow-x:\s*(\w+)', css)
    assert guard, 'no overflow-x guard on html; one wide element widens the document'
    assert guard.group(1) == 'clip', (
        f'overflow-x is {guard.group(1)!r}; hidden makes html a scroll container '
        f'and breaks the sticky navbar - clip does not')
    assert re.search(r'^img,\s*$', css, re.M) or re.search(r'^img\s*\{', css, re.M), (
        'no global img width cap; Bootstrap reboot has none and most images here '
        'do not use .img-fluid, so one oversized upload widens the page')


check('nothing widens the page past the viewport',
      nothing_widens_the_page_past_the_viewport)


def first_party_assets_are_cache_busted():
    """Every same-origin asset base.html loads carries a version in its URL.

    Nothing sets SEND_FILE_MAX_AGE_DEFAULT, so Flask serves static files with no
    explicit Cache-Control and the browser reuses what it has for as long as its own
    heuristics allow. That made the phone layout fixes in style.css unable to reach a
    device that had already installed the app with the broken copy - and static/sw.js
    caches nothing, so the service worker was never what held the old file. Only a
    changed URL moves the plain HTTP cache.

    This is asserted against the rendered page rather than the template source
    because that is the thing the browser sees; a helper that silently returned an
    empty string would still leave a version= in the template and would still ship a
    naked URL. Uploads are skipped: their filenames already carry a content hash, and
    they are not what a release changes.

    sw.js is intentionally absent from this - it is served by a route, not from
    static/, and it must stay unversioned. A changed worker URL registers a second
    worker rather than updating the one already installed, and browsers already
    bypass the HTTP cache for the worker script itself.
    """
    html = app.test_client().get('/').get_data(as_text=True)
    naked = []
    for url in set(re.findall(r'(?:href|src)="(/static/[^"]+)"', html)):
        if url.startswith('/static/uploads/'):
            continue
        if '?v=' not in url:
            naked.append(url)
    assert not naked, (
        f'these first-party assets ship no version: {sorted(naked)}; a browser that '
        f'already holds one keeps it, so a CSS fix cannot reach an installed app')

    # And the token has to actually vary with the file, or it is decoration. Two
    # different files must not share a version.
    stamps = {u.split('?v=')[0]: u.split('?v=')[1]
              for u in re.findall(r'(?:href|src)="(/static/[^"]+\?v=[^"]+)"', html)
              if not u.startswith('/static/uploads/')}
    css = stamps.get('/static/style.css')
    js = stamps.get('/static/main.js')
    assert css and js and css != js, (
        f'style.css and main.js report the same version ({css!r}); the token is not '
        f'derived from the file, so editing one will not bust the other')


check('first-party assets are cache-busted', first_party_assets_are_cache_busted)


def the_payment_run_does_not_iterate_a_capped_list():
    """The disbursement cycle must read its own rows, not the page's display list.

    disbursement_snapshot() returns pending_withdrawals and pending_salaries capped at
    DISBURSEMENT_LIST_LIMIT so the desk can render at a million users. The run_cycle
    action used to loop over those same two lists to queue the actual payments, which
    means the moment the display grew a cap the payment run silently inherited it: the
    hundred-and-first seller does not get paid, no row records that, and the flash
    message still reads as success. Underpaying quietly is the worst failure this
    codebase can have, so it gets an assertion rather than a comment.

    Checked at the source level because there is no runtime signal to check - a run
    that paid a capped batch and a run that paid everything look identical unless the
    backlog is bigger than the cap, which is exactly the case a test fixture does not
    have by default.
    """
    source = pathlib.Path(main.__file__).read_text(encoding='utf-8')
    start = source.index('def admin_disbursements')
    end = source.index('\ndef ', start + 1)
    body = source[start:end]

    cycle = body.index("action == 'run_cycle'")
    nxt = body.find('\n        if action ==', cycle + 1)
    cycle_body = body[cycle:nxt if nxt != -1 else len(body)]

    for capped in ("snapshot['pending_withdrawals']", "snapshot['pending_salaries']"):
        assert capped not in cycle_body, (
            f'run_cycle iterates {capped}, which disbursement_snapshot caps at '
            f'DISBURSEMENT_LIST_LIMIT for display; every row past the cap is a '
            f'payment that is never queued and never reported')

    assert 'DISBURSEMENT_CYCLE_BATCH' in cycle_body, (
        'run_cycle reads payment rows without DISBURSEMENT_CYCLE_BATCH; a payment '
        'run over an unbounded .all() is the thing the cap was meant to avoid')

    # A bounded run has to say what it left, or it is indistinguishable from a run
    # that cleared the backlog.
    assert 'still_pending' in cycle_body, (
        'run_cycle does not count what remains after it commits; a batched run that '
        'reports plain success reads exactly like one that finished the queue')

    # And the display side has to carry its true totals, or the cap hides a payable.
    snap_start = source.index('def disbursement_snapshot')
    snap_end = source.index('\ndef ', snap_start + 1)
    snap = source[snap_start:snap_end]
    assert 'DISBURSEMENT_LIST_LIMIT' in snap, 'disbursement_snapshot is unbounded again'
    for key in ('pending_withdrawal_count', 'pending_salary_count'):
        assert key in snap, (
            f'disbursement_snapshot caps a list without returning {key}; a capped '
            f'queue with no total tells nobody there is more owed')


check('the payment run does not iterate a capped list',
      the_payment_run_does_not_iterate_a_capped_list)


def typing_in_a_search_box_does_not_navigate():
    """No key event may submit a form.

    main.js used to bind `keyup` on the first `[name="search"]` input on the page and
    call `this.form.submit()` behind a 500ms debounce. On a desktop that reads as a
    live search. On a phone it is not: a navigation closes the keyboard, so typing on
    the home page meant three letters, a pause, and the keyboard dropping while the
    page reloaded underneath you. It also fired on arrow keys and Tab, and the
    selector matched the filter boxes on admin/users.html and admin/promo_codes.html
    as readily as the real search field.

    The scaling half matters as much: each of those navigations ran the full product
    search - the most expensive query on the site - for a term nobody had finished
    typing, so one deliberate search cost three or four of them.

    Asserted at the source level and against key events specifically, because
    `onchange="this.form.submit()"` on the sort dropdown in shop.html is the correct
    use of the same call: a select fires change once, when the choice is made. What
    must never come back is a *keystroke* turning into a page load.
    """
    js = pathlib.Path(ROOT, 'static', 'main.js').read_text(encoding='utf-8',
                                                           errors='replace')
    # Strip comments first, or the paragraph in main.js explaining why this was
    # removed would itself fail the check that it was removed.
    stripped = re.sub(r'/\*.*?\*/', '', js, flags=re.S)
    stripped = re.sub(r'^\s*//.*$', '', stripped, flags=re.M)

    for event in ('keyup', 'keydown', 'keypress', 'input'):
        for hit in re.finditer(r'addEventListener\(\s*[\'"]%s[\'"]' % event, stripped):
            # The handler body, bounded generously - a submit anywhere in the next
            # few lines of a key handler is the thing being ruled out.
            window = stripped[hit.end():hit.end() + 400]
            assert '.submit()' not in window, (
                f'a {event} handler in static/main.js calls .submit(); a keystroke '
                f'that navigates closes the keyboard on a phone and runs the product '
                f'search for a half-typed term')

    # And the templates must not do it inline either.
    for path in sorted(pathlib.Path(ROOT, 'templates').rglob('*.html')):
        body = path.read_text(encoding='utf-8', errors='replace')
        for attr in ('onkeyup', 'onkeydown', 'onkeypress', 'oninput'):
            hits = [m for m in re.finditer(attr + r'="([^"]*)"', body)
                    if 'submit()' in m.group(1)]
            assert not hits, (
                f'{path.relative_to(pathlib.Path(ROOT, "templates")).as_posix()} '
                f'submits a form from {attr}; same problem, spelt inline')


check('typing in a search box does not navigate',
      typing_in_a_search_box_does_not_navigate)


def the_static_route_does_not_hand_out_private_uploads():
    """A paid file and an identity document are not downloadable by URL alone.

    Flask serves the whole static/ tree, so static/uploads/digital held paid files at
    a public URL while download_digital's login and order-ownership checks sat on a
    different one and never saw the request. Same for the identity folders: seller_docs
    holds ID photographs and liveness selfies, phone_docs holds IMEI photographs and
    receipts.

    Asserted against the running app rather than the constant, because the constant is
    not what serves the file. The public folders are asserted in the same check on
    purpose - a guard that also blocked product photos would pass a
    "private things are blocked" test while breaking every image on the site, and that
    failure would show up as a green suite and a blank shop page.
    """
    client = app.test_client()

    uploads = pathlib.Path(main.app.config['UPLOAD_FOLDER'])

    def first_file(folder):
        d = uploads / folder
        if not d.is_dir():
            return None
        for item in sorted(d.iterdir()):
            if item.is_file():
                return item.name
        return None

    # Blocked, and blocked as 404 - a 403 would confirm the file exists, which for an
    # ID document is itself the thing not to confirm.
    for folder in ('digital', 'seller_docs', 'kyc', 'phone_docs'):
        name = first_file(folder) or 'probe-that-need-not-exist.bin'
        got = client.get(f'/static/uploads/{folder}/{name}')
        assert got.status_code == 404, (
            f'/static/uploads/{folder}/{name} answered {got.status_code}; that URL '
            f'reaches the file without passing the ownership check that guards it')

    # And the public folders still work, or the fix is worse than the bug.
    for folder in ('products', 'banners', 'services', 'inspo'):
        name = first_file(folder)
        if not name:
            continue
        got = client.get(f'/static/uploads/{folder}/{name}')
        assert got.status_code == 200, (
            f'/static/uploads/{folder}/{name} answered {got.status_code}; the upload '
            f'guard is blocking a public folder and every image on the site with it')

    # Nothing else under static/ is affected.
    assert client.get('/static/style.css').status_code == 200, (
        'the stylesheet stopped being served; the guard is matching more than '
        'uploads/')

    # The rule table itself, because the admin-visible half cannot be exercised here -
    # this script signs nobody in - and "admin only" silently becoming "nobody" would
    # blind the phone-evidence reviewer with no other symptom.
    assert main.GUARDED_UPLOAD_FOLDERS.get('digital') == 'nobody', (
        'digital must be closed to everyone; the paid route reads the folder from '
        'disk and needs no URL')
    for folder in ('seller_docs', 'kyc', 'phone_docs'):
        assert main.GUARDED_UPLOAD_FOLDERS.get(folder) == 'admin', (
            f'{folder} must stay admin-readable; admin/phone_evidence.html renders '
            f'these as <img src> and a reviewer who cannot see the document cannot '
            f'review it')


check('the static route does not hand out private uploads',
      the_static_route_does_not_hand_out_private_uploads)


print()
if failures:
    print(f'{len(failures)} check(s) failed')
    sys.exit(1)
print(f'all {len(passed)} checks passed')
