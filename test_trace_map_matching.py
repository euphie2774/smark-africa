"""Smoke test for road-following driver trace lines (map matching).

The dispatch and driver maps used to join raw GPS pings with straight segments,
which cut across blocks and buildings because a fix every ~20s says nothing about
the road taken in between. This covers the map-matching layer that replaced that:
the polyline codec, the GPS cleanup filter, both remote matchers, the cache, the
server payload and the template wiring.

Copies the working database to a throwaway file so this never mutates real data.
Run with the base interpreter (the venv's ctypes is broken):

    PYTHONPATH=".:.venv/Lib/site-packages" \
      "C:/Users/euwin/AppData/Local/Programs/Python/Python314/python.exe" \
      test_trace_map_matching.py
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.abspath(__file__))


def _scratch_database():
    """Clone the dev database so the test can write freely."""
    candidates = [
        os.path.join(REPO, 'instance', 'smarkafrica.db'),
        os.path.join(REPO, 'smarkafrica.db'),
    ]
    source = next((p for p in candidates if os.path.exists(p)), None)
    scratch = os.path.join(tempfile.mkdtemp(prefix='smark-trace-'), 'test.db')
    if source:
        shutil.copy2(source, scratch)
    return scratch


SCRATCH_DB = _scratch_database()
os.environ['DATABASE_URL'] = 'sqlite:///' + SCRATCH_DB.replace('\\', '/')
os.environ['FLASK_ENV'] = 'development'
os.environ['CACHE_TYPE'] = 'NullCache'
os.environ.setdefault('SECRET_KEY', 'smoke-test-key')
# Start from the shipped default so an operator's own .env cannot flip results.
os.environ.pop('GEO_TRACE_PROVIDER', None)
os.environ.pop('OSRM_URL', None)
os.environ.pop('VALHALLA_URL', None)

import main  # noqa: E402
from models import db, User, DriverProfile, DriverLocationPing  # noqa: E402

from geo import TracePoint, get_matcher, reset_providers  # noqa: E402
from geo.cache import cached_match  # noqa: E402
from geo.fallback import FilteredTraceMatcher, clean_trace  # noqa: E402
from geo.polyline import decode_polyline, encode_polyline  # noqa: E402
import geo.osrm as osrm_module  # noqa: E402
import geo.valhalla as valhalla_module  # noqa: E402

app = main.app
app.config['TESTING'] = True
app.config['WTF_CSRF_ENABLED'] = False

FAILURES = []


def check(label, condition, detail=''):
    print(f'  [{"PASS" if condition else "FAIL"}] {label}'
          f'{(" -> " + str(detail)) if detail else ""}')
    if not condition:
        FAILURES.append(label)


def login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def read(path):
    with open(os.path.join(REPO, path), encoding='utf-8') as handle:
        return handle.read()


class FakeResponse:
    """Stand-in for a requests response; only what the adapters touch."""

    def __init__(self, payload, error=None):
        self._payload = payload
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        return self._payload


# A short run down a curving road, as a matcher would return it.
ROAD = [[36.8172, -1.2864], [36.8178, -1.2869], [36.8185, -1.2876],
        [36.8193, -1.2884], [36.8200, -1.2890]]


def trace_points(coords, accuracy=8.0, start=0, step=20):
    return [
        TracePoint(lat=c[1], lng=c[0], accuracy_m=accuracy,
                   epoch_seconds=start + index * step)
        for index, c in enumerate(coords)
    ]


print('\n== polyline codec ==')
# The canonical Google precision-5 fixture from their own documentation.
GOOGLE_P5 = '_p~iF~ps|U_ulLnnqC_mqNvxq`@'
decoded5 = decode_polyline(GOOGLE_P5, 5)
check('known precision-5 fixture decodes',
      [[round(c, 3) for c in p] for p in decoded5]
      == [[-120.2, 38.5], [-120.95, 40.7], [-126.453, 43.252]], decoded5)
check('output is lng,lat not lat,lng', decoded5[0][0] == -120.2, decoded5[0])

roundtrip = decode_polyline(encode_polyline(ROAD, 6), 6)
check('precision-6 round trip is lossless',
      all(abs(a[i] - b[i]) < 1e-6 for a, b in zip(ROAD, roundtrip) for i in (0, 1)),
      roundtrip)
check('precision matters: reading a p6 shape as p5 lands elsewhere',
      abs(decode_polyline(encode_polyline(ROAD, 6), 5)[0][1] - ROAD[0][1]) > 1.0)
check('empty and None decode to nothing',
      decode_polyline('', 6) == [] and decode_polyline(None, 6) == [])
check('a truncated payload yields whole pairs, not an exception',
      isinstance(decode_polyline(GOOGLE_P5[:4], 5), list))


print('\n== clean_trace() GPS filter ==')
noisy = [
    TracePoint(-1.286389, 36.817223, accuracy_m=8, epoch_seconds=0),
    TracePoint(-1.286391, 36.817225, accuracy_m=6, epoch_seconds=20),    # ~2m shuffle
    TracePoint(-1.287500, 36.818500, accuracy_m=500, epoch_seconds=40),  # too vague
    TracePoint(-9.500000, 36.900000, accuracy_m=10, epoch_seconds=60),   # teleport
    TracePoint(-1.290000, 36.822000, accuracy_m=9, epoch_seconds=120),
]
cleaned = clean_trace(noisy)
check('the 500m-accuracy fix is dropped',
      all(p.accuracy_m != 500 for p in cleaned))
check('the impossible jump is dropped', all(p.lat > -2 for p in cleaned))
check('the 2m shuffle is collapsed',
      not any(p.epoch_seconds == 20 for p in cleaned), [p.epoch_seconds for p in cleaned])
check('chronological order is preserved',
      [p.epoch_seconds for p in cleaned] == sorted(p.epoch_seconds for p in cleaned))
check('the live fix survives as the end of the line',
      cleaned[-1].epoch_seconds == 120)

# Without timestamps a long gap is indistinguishable from a teleport, so the
# speed gate must stay out of it rather than delete real travel.
untimed = [TracePoint(-1.2864, 36.8172), TracePoint(-1.3191, 36.7062)]
check('no timestamps means no speed filtering', len(clean_trace(untimed)) == 2)
check('a single fix passes through untouched', len(clean_trace(noisy[:1])) == 1)
check('an empty trace is handled', clean_trace([]) == [] and clean_trace(None) == [])
check('an all-vague trace still draws rather than blanking',
      len(clean_trace([
          TracePoint(-1.2864, 36.8172, accuracy_m=800, epoch_seconds=0),
          TracePoint(-1.2900, 36.8220, accuracy_m=900, epoch_seconds=60),
      ])) == 2)
check('an invalid coordinate is discarded',
      len(clean_trace([TracePoint(-1.2864, 36.8172), TracePoint(99.0, 400.0)])) == 1)


print('\n== fallback matcher is honest about not knowing the road ==')
reset_providers()
fallback = FilteredTraceMatcher()
result = fallback.match(trace_points(ROAD))
check('never claims a road match', result.is_road_matched is False)
check('still returns drawable geometry', result.drawable, len(result.coordinates))
check('coordinates are lng,lat', abs(result.coordinates[0][0] - 36.8172) < 1e-6)
check('reports a distance', result.distance_km > 0, result.distance_km)
check('one fix is not drawable', not fallback.match(trace_points(ROAD[:1])).drawable)


print('\n== get_matcher() honours GEO_TRACE_PROVIDER ==')
reset_providers()
check('defaults to the filtered fallback', get_matcher().name == 'filtered',
      get_matcher().name)

os.environ['GEO_TRACE_PROVIDER'] = 'osrm'
reset_providers()
check('osrm without OSRM_URL degrades instead of raising',
      get_matcher().name == 'filtered', get_matcher().name)

os.environ['OSRM_URL'] = 'http://stub-osrm'
reset_providers()
check('osrm is selected once configured', get_matcher().name == 'osrm',
      get_matcher().name)

os.environ['GEO_TRACE_PROVIDER'] = 'valhalla'
reset_providers()
check('valhalla without VALHALLA_URL degrades',
      get_matcher().name == 'filtered', get_matcher().name)

os.environ['VALHALLA_URL'] = 'http://stub-valhalla'
reset_providers()
check('valhalla is selected once configured',
      get_matcher().name == 'valhalla-trace', get_matcher().name)

os.environ['GEO_TRACE_PROVIDER'] = 'nonsense'
reset_providers()
check('an unknown provider name falls back', get_matcher().name == 'filtered')


print('\n== ValhallaMatcher against a canned /trace_route body ==')
os.environ['GEO_TRACE_PROVIDER'] = 'valhalla'
reset_providers()
original_post = valhalla_module.requests.post
captured = {}

# Two legs sharing an endpoint, which is what Valhalla actually returns.
leg_a = encode_polyline(ROAD[:3], 6)
leg_b = encode_polyline(ROAD[2:], 6)


def fake_post(url, json=None, timeout=None):
    captured['url'] = url
    captured['json'] = json
    return FakeResponse({'trip': {'legs': [{'shape': leg_a}, {'shape': leg_b}],
                                 'summary': {'length': 1.234}}})


valhalla_module.requests.post = fake_post
try:
    matcher = valhalla_module.ValhallaMatcher()
    matched = matcher.match(trace_points(ROAD))
    check('calls /trace_route', captured['url'].endswith('/trace_route'), captured['url'])
    check('asks for map_snap', captured['json'].get('shape_match') == 'map_snap')
    check('sends per-point time so Meili can reason about transitions',
          all('time' in point for point in captured['json']['shape']))
    check('sends per-point accuracy',
          all('accuracy' in point for point in captured['json']['shape']))
    check('reports a road match', matched.is_road_matched is True)
    check('legs are joined without a duplicate at the seam',
          len(matched.coordinates) == len(ROAD), len(matched.coordinates))
    check('geometry is the road shape, not the input fixes',
          abs(matched.coordinates[1][0] - ROAD[1][0]) < 1e-6, matched.coordinates[1])
    check('carries the matched road distance', matched.distance_km == 1.234)

    # Every failure mode has to degrade, never raise: the dispatch map polls this
    # every 15s and an exception would take the whole poll down.
    valhalla_module.requests.post = lambda *a, **k: FakeResponse(
        {'trip': {'legs': [], 'summary': {}}})
    empty = matcher.match(trace_points(ROAD))
    check('an empty shape degrades to the filtered line',
          empty.provider == 'valhalla-trace-fallback' and not empty.is_road_matched,
          empty.provider)
    check('the degraded line is still drawable', empty.drawable)

    valhalla_module.requests.post = lambda *a, **k: FakeResponse(
        None, error=RuntimeError('502 from valhalla'))
    broken = matcher.match(trace_points(ROAD))
    check('an HTTP error degrades', broken.provider == 'valhalla-trace-fallback')

    def boom(*a, **k):
        raise TimeoutError('connect timed out')

    valhalla_module.requests.post = boom
    timed_out = matcher.match(trace_points(ROAD))
    check('a timeout degrades', timed_out.provider == 'valhalla-trace-fallback')
finally:
    valhalla_module.requests.post = original_post


print('\n== OsrmMatcher against a canned /match body ==')
os.environ['GEO_TRACE_PROVIDER'] = 'osrm'
reset_providers()
original_get = osrm_module.requests.get
grabbed = {}
road_shape = encode_polyline(ROAD, 6)


def fake_get(url, params=None, timeout=None):
    grabbed['url'] = url
    grabbed['params'] = params
    return FakeResponse({'code': 'Ok', 'matchings': [
        {'geometry': road_shape, 'distance': 1234.0, 'confidence': 0.97}]})


osrm_module.requests.get = fake_get
try:
    matcher = osrm_module.OsrmMatcher()
    matched = matcher.match(trace_points(ROAD))
    check('calls /match/v1/<profile>', '/match/v1/driving/' in grabbed['url'],
          grabbed['url'])
    check('asks for polyline6, matching the decoder precision',
          grabbed['params']['geometries'] == 'polyline6')
    check('asks for the full overview', grabbed['params']['overview'] == 'full')
    check('tidies stationary clusters', grabbed['params']['tidy'] == 'true')
    check('sends a per-fix search radius', ';' in grabbed['params']['radiuses'],
          grabbed['params']['radiuses'])
    check('reports a road match', matched.is_road_matched is True)
    check('carries confidence through', matched.confidence == 0.97)
    check('converts metres to km', abs(matched.distance_km - 1.234) < 1e-9,
          matched.distance_km)
    check('sends increasing timestamps', 'timestamps' in grabbed['params'])

    # OSRM rejects the request outright unless every timestamp is present and
    # strictly increasing, so a trace that fails that must omit them entirely.
    matcher.match([
        TracePoint(ROAD[0][1], ROAD[0][0], accuracy_m=8, epoch_seconds=100),
        TracePoint(ROAD[1][1], ROAD[1][0], accuracy_m=8, epoch_seconds=100),
        TracePoint(ROAD[2][1], ROAD[2][0], accuracy_m=8, epoch_seconds=90),
    ])
    check('non-increasing timestamps are omitted rather than rejected',
          'timestamps' not in grabbed['params'], grabbed['params'].get('timestamps'))

    # A sloppy fix has to be allowed more slack, but not so much that the match
    # jumps to a parallel street.
    matcher.match(trace_points(ROAD, accuracy=999.0))
    radii = [float(r) for r in grabbed['params']['radiuses'].split(';')]
    check('the search radius is clamped', all(r <= 50.0 for r in radii), radii)
    matcher.match(trace_points(ROAD, accuracy=0.1))
    radii = [float(r) for r in grabbed['params']['radiuses'].split(';')]
    check('the radius has a floor', all(r >= 5.0 for r in radii), radii)

    osrm_module.requests.get = lambda *a, **k: FakeResponse(
        {'code': 'NoMatch', 'matchings': []})
    unmatched = matcher.match(trace_points(ROAD))
    check('a NoMatch response degrades',
          unmatched.provider == 'osrm-fallback' and not unmatched.is_road_matched,
          unmatched.provider)

    osrm_module.requests.get = lambda *a, **k: FakeResponse(
        None, error=RuntimeError('500 from osrm'))
    check('an HTTP error degrades',
          matcher.match(trace_points(ROAD)).provider == 'osrm-fallback')

    # A trace split into two matchings must keep both: dropping the weaker half
    # would silently lose part of the journey, the very bug this feature fixes.
    osrm_module.requests.get = lambda *a, **k: FakeResponse({'code': 'Ok', 'matchings': [
        {'geometry': encode_polyline(ROAD[:3], 6), 'distance': 500.0, 'confidence': 0.9},
        {'geometry': encode_polyline(ROAD[3:], 6), 'distance': 400.0, 'confidence': 0.4},
    ]})
    split = matcher.match(trace_points(ROAD))
    check('all matchings are concatenated',
          len(split.coordinates) == len(ROAD), len(split.coordinates))
    check('confidence is the weakest link', split.confidence == 0.4, split.confidence)
    check('distances are summed', abs(split.distance_km - 0.9) < 1e-9, split.distance_km)
finally:
    osrm_module.requests.get = original_get


print('\n== cached_match() ==')
os.environ['GEO_TRACE_PROVIDER'] = 'fallback'
reset_providers()


class CountingCache:
    def __init__(self):
        self.store = {}
        self.sets = 0

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, timeout=None):
        self.sets += 1
        self.store[key] = value


cache = CountingCache()
check('a one-fix trace never reaches the matcher',
      cached_match(cache, trace_points(ROAD[:1])).coordinates == [])
check('an empty trace is safe', cached_match(cache, []).coordinates == [])
cached_match(cache, trace_points(ROAD))
check('an unmatched fallback line is NOT cached, so it cannot outlive an outage',
      cache.sets == 0, cache.sets)

os.environ['GEO_TRACE_PROVIDER'] = 'osrm'
os.environ['OSRM_URL'] = 'http://stub-osrm'
reset_providers()
osrm_module.requests.get = fake_get
calls = {'n': 0}


def counting_get(url, params=None, timeout=None):
    calls['n'] += 1
    return fake_get(url, params=params, timeout=timeout)


osrm_module.requests.get = counting_get
try:
    first = cached_match(cache, trace_points(ROAD))
    second = cached_match(cache, trace_points(ROAD))
    check('a road match is cached', cache.sets == 1, cache.sets)
    check('an unchanged trace does not hit the matcher twice', calls['n'] == 1, calls['n'])
    check('the cached result is equivalent',
          second.coordinates == first.coordinates and second.is_road_matched)
    # One new ping must produce a new key, or the map would freeze on stale
    # geometry for as long as the TTL.
    cached_match(cache, trace_points(ROAD + [[36.8210, -1.2900]]))
    check('one extra fix re-matches', calls['n'] == 2, calls['n'])

    def explode(*a, **k):
        raise RuntimeError('matcher exploded')

    osrm_module.requests.get = explode
    survived = cached_match(cache, trace_points(ROAD + [[36.8215, -1.2905]]))
    check('a matcher blowing up still returns a drawable line', survived.drawable)
finally:
    osrm_module.requests.get = original_get

os.environ['GEO_TRACE_PROVIDER'] = 'fallback'
os.environ.pop('OSRM_URL', None)
os.environ.pop('VALHALLA_URL', None)
reset_providers()


print('\n== driver_trail() carries what the matcher needs ==')
with app.app_context():
    db.create_all()
    rider = User(username='tracerider', email='tracerider@test.local',
                 password_hash='dummy')
    db.session.add(rider)
    db.session.commit()
    driver = DriverProfile(user_id=rider.id, display_name='Trace Rider',
                           phone='+254700000777', vehicle_type='motorbike',
                           vehicle_registration='KDT 777R', is_active=True,
                           tracking_token='trace-token-smoke')
    # A driver who has never pinged: there is nothing to match, and asking the
    # matcher anyway would burn an HTTP call per poll for an empty line.
    idle = User(username='tracenofix', email='tracenofix@test.local',
                password_hash='dummy')
    db.session.add(idle)
    db.session.commit()
    unpinged = DriverProfile(user_id=idle.id, display_name='Never Pinged',
                            phone='+254700000778', vehicle_type='motorbike',
                            vehicle_registration='KDT 778R', is_active=True,
                            tracking_token='trace-nofix-smoke')
    db.session.add_all([driver, unpinged])
    db.session.commit()
    driver_id = driver.id
    unpinged_id = unpinged.id

    base = main.utcnow() - timedelta(minutes=10)
    for index, coord in enumerate(ROAD):
        db.session.add(DriverLocationPing(
            driver_id=driver_id, order_id=None, lat=coord[1], lng=coord[0],
            accuracy_m=9.0, speed_kph=24.0, heading=90.0,
            created_at=base + timedelta(seconds=index * 20)))
    driver.last_lat, driver.last_lng = ROAD[-1][1], ROAD[-1][0]
    driver.last_ping_at = main.utcnow()
    db.session.commit()

    trail = main.driver_trail(driver_id)
    check('trail carries accuracy for the matcher',
          all(p.get('accuracy_m') == 9.0 for p in trail))
    check('trail carries an epoch for the speed gate',
          all(isinstance(p.get('epoch_seconds'), float) for p in trail))
    check('epochs increase with time',
          [p['epoch_seconds'] for p in trail]
          == sorted(p['epoch_seconds'] for p in trail))
    check('the existing speed readout field survives',
          trail[-1].get('speed_kph') == 24.0)

    print('\n== driver_trail_path() ==')
    path = main.driver_trail_path(trail)
    check('returns the shape the map layers expect',
          set(path) == {'coordinates', 'is_road_matched', 'provider', 'distance_km'},
          sorted(path))
    check('draws a line with no matcher configured', len(path['coordinates']) >= 2,
          len(path['coordinates']))
    check('does not claim a road match without a matcher',
          path['is_road_matched'] is False)
    check('coordinates are lng,lat for GeoJSON',
          abs(path['coordinates'][0][0] - ROAD[0][0]) < 0.01, path['coordinates'][0])

    empty = main.driver_trail_path([])
    check('an empty trail yields no coordinates', empty['coordinates'] == [])
    check('a single fix yields no coordinates',
          main.driver_trail_path(trail[:1])['coordinates'] == [])
    check('a trail of malformed entries is survivable',
          main.driver_trail_path([{'lat': None, 'lng': None}])['coordinates'] == [])

    # 120 pings are kept for the table readouts but OSRM takes 100 per request.
    wide = [dict(lat=-1.2 - i * 0.0005, lng=36.8 + i * 0.0005,
                 accuracy_m=8.0, epoch_seconds=float(i * 20))
            for i in range(main.DRIVER_TRAIL_POINTS)]
    check('the point cap keeps the request inside OSRM\'s limit',
          len(main.driver_trail_path(wide)['coordinates']) <= main.DRIVER_TRACE_MAX_POINTS,
          len(main.driver_trail_path(wide)['coordinates']))

    admin = User.query.filter_by(is_admin=True).first()
    if not admin:
        admin = User(username='traceadmin', email='traceadmin@test.local',
                     password_hash='dummy', is_admin=True, admin_level='mvp')
        db.session.add(admin)
        db.session.commit()
    admin_id = admin.id


print('\n== /api/dispatch/drivers carries trail_path ==')
with app.test_client() as client:
    login(client, admin_id)
    response = client.get('/api/dispatch/drivers')
    check('endpoint responds', response.status_code == 200, response.status_code)
    drivers = (response.get_json() or {}).get('drivers') or []
    entry = next((d for d in drivers if d['id'] == driver_id), None)
    check('the driver is present', entry is not None)
    if entry:
        check('trail_path attached', 'trail_path' in entry, sorted(entry))
        check('raw trail still attached for the fix dots', len(entry.get('trail') or []) >= 2)
        check('trail_path is drawable', len(entry['trail_path']['coordinates']) >= 2)
        check('trail_path declares whether it followed a road',
              entry['trail_path']['is_road_matched'] is False)
    nofix = next((d for d in drivers if d['id'] == unpinged_id), None)
    check('the never-pinged driver is still listed', nofix is not None)
    if nofix:
        check('a driver with no fix carries no trail_path', 'trail_path' not in nofix,
              sorted(nofix))
        check('and no raw trail either', 'trail' not in nofix)


print('\n== driver console page ==')
with app.test_client() as client:
    response = client.get('/driver/trace-token-smoke')
    check('console renders', response.status_code == 200, response.status_code)
    body = response.get_data(as_text=True)
    check('seeds the matched path into the page', 'TRAIL_PATH' in body)
    check('still seeds the raw fixes', 'const TRAIL ' in body)


print('\n== templates draw the road line and the fixes ==')
dispatch_html = read('templates/admin/dispatch.html')
check('dispatch prefers the matched path', 'driver.trail_path' in dispatch_html)
check('dispatch falls back to raw fixes so the map never blanks',
      'driver.trail || []' in dispatch_html)
check('dispatch draws the GPS fixes', "type: 'circle'" in dispatch_html)
check('dispatch dashes an unmatched trace', 'line-dasharray' in dispatch_html)
check('dispatch cleans up the fix layer too',
      "fixSrc + '-dots'" in dispatch_html)
check('dispatch keeps the casing under the line', "'-casing'" in dispatch_html)
check('dispatch prefers the matched road distance',
      'path.is_road_matched && path.distance_km' in dispatch_html)

console_html = read('templates/driver_console.html')
check('console seeds from the matched path', 'TRAIL_PATH' in console_html)
check('console draws the GPS fixes', "id: 'fixes'" in console_html)
check('console still appends live fixes for instant feedback',
      'function pushTrail' in console_html)


print('\n== regression: routing still works ==')
with app.app_context():
    from geo import GeoPoint
    from geo.cache import cached_route
    quote = cached_route(None, GeoPoint(-1.286389, 36.817223), GeoPoint(-0.1022, 34.7617))
    check('a Nairobi-Kisumu quote still prices', quote.distance_km > 200, quote.distance_km)
    check('and is still flagged an estimate', quote.is_estimate is True)


print('\n' + '=' * 62)
if FAILURES:
    print(f'{len(FAILURES)} FAILED:')
    for label in FAILURES:
        print(f'  - {label}')
    sys.exit(1)
print('All trace map-matching checks passed.')
