"""Caching wrappers for routing and geocoding.

Route and geocode lookups are the expensive part of a quote - a Valhalla call
is tens of milliseconds and Pelias hits Elasticsearch. Both are highly
repetitive (the same corridors and the same town names over and over), so they
cache well.

Uses the app's existing Flask-Caching instance, which is already Redis-backed
whenever REDIS_URL is set and falls back to the filesystem otherwise. Callers
pass the cache in to keep this module free of app imports.
"""

import hashlib

from .provider import GeoPoint, RouteResult, GeocodeResult, get_router, get_geocoder

ROUTE_CACHE_PREFIX = 'geo:route:'
GEOCODE_CACHE_PREFIX = 'geo:geocode:'
REVERSE_CACHE_PREFIX = 'geo:reverse:'

DEFAULT_ROUTE_TTL = 86400        # roads change slowly
DEFAULT_GEOCODE_TTL = 604800     # place names change even more slowly

# Coordinate rounding for cache keys. 3 decimals ~ 110 m, which is far finer
# than delivery pricing needs and keeps the hit rate high.
KEY_PRECISION = 3


def _route_key(origin: GeoPoint, destination: GeoPoint, provider_name: str):
    raw = (
        f'{provider_name}|'
        f'{round(origin.lat, KEY_PRECISION)},{round(origin.lng, KEY_PRECISION)}|'
        f'{round(destination.lat, KEY_PRECISION)},{round(destination.lng, KEY_PRECISION)}'
    )
    return ROUTE_CACHE_PREFIX + hashlib.sha1(raw.encode('utf-8')).hexdigest()


def _text_key(prefix, text, provider_name, extra=''):
    raw = f'{provider_name}|{(text or "").strip().lower()}|{extra}'
    return prefix + hashlib.sha1(raw.encode('utf-8')).hexdigest()


def cached_route(cache, origin: GeoPoint, destination: GeoPoint, ttl=DEFAULT_ROUTE_TTL) -> RouteResult:
    """Route with a read-through cache. Never raises."""
    router = get_router()
    if not (origin and destination and origin.is_valid() and destination.is_valid()):
        return RouteResult(distance_km=0.0, provider=router.name, is_estimate=True)

    key = _route_key(origin, destination, router.name)
    if cache is not None:
        try:
            hit = cache.get(key)
            if hit:
                return RouteResult(**hit)
        except Exception:  # noqa: BLE001 - a cache failure must not break pricing
            pass

    result = router.route(origin, destination)

    # Only cache real successes. Caching a degraded fallback for a day would
    # outlive the outage that produced it.
    if cache is not None and result.distance_km > 0 and not result.provider.endswith('-fallback'):
        try:
            cache.set(key, {
                'distance_km': result.distance_km,
                'duration_minutes': result.duration_minutes,
                'provider': result.provider,
                'is_estimate': result.is_estimate,
                'geometry': result.geometry,
            }, timeout=ttl)
        except Exception:  # noqa: BLE001
            pass
    return result


def cached_geocode(cache, query, country='Kenya', ttl=DEFAULT_GEOCODE_TTL) -> GeocodeResult:
    geocoder = get_geocoder()
    if not (query or '').strip():
        return GeocodeResult(label='', provider=geocoder.name, country=country)

    key = _text_key(GEOCODE_CACHE_PREFIX, query, geocoder.name, country or '')
    if cache is not None:
        try:
            hit = cache.get(key)
            if hit:
                point = GeoPoint(hit['lat'], hit['lng']) if hit.get('lat') is not None else None
                return GeocodeResult(
                    label=hit.get('label') or '',
                    point=point,
                    county=hit.get('county'),
                    country=hit.get('country'),
                    confidence=hit.get('confidence') or 0.0,
                    provider=hit.get('provider') or geocoder.name,
                )
        except Exception:  # noqa: BLE001
            pass

    result = geocoder.geocode(query, country)
    if cache is not None and result.found and not result.provider.endswith('-fallback'):
        try:
            cache.set(key, {
                'label': result.label,
                'lat': result.point.lat,
                'lng': result.point.lng,
                'county': result.county,
                'country': result.country,
                'confidence': result.confidence,
                'provider': result.provider,
            }, timeout=ttl)
        except Exception:  # noqa: BLE001
            pass
    return result


def cached_reverse(cache, point: GeoPoint, ttl=DEFAULT_GEOCODE_TTL) -> GeocodeResult:
    geocoder = get_geocoder()
    if not (point and point.is_valid()):
        return GeocodeResult(label='', provider=geocoder.name)

    key = _text_key(
        REVERSE_CACHE_PREFIX,
        f'{round(point.lat, KEY_PRECISION)},{round(point.lng, KEY_PRECISION)}',
        geocoder.name,
    )
    if cache is not None:
        try:
            hit = cache.get(key)
            if hit:
                hit_point = GeoPoint(hit['lat'], hit['lng']) if hit.get('lat') is not None else None
                return GeocodeResult(
                    label=hit.get('label') or '',
                    point=hit_point,
                    county=hit.get('county'),
                    country=hit.get('country'),
                    confidence=hit.get('confidence') or 0.0,
                    provider=hit.get('provider') or geocoder.name,
                )
        except Exception:  # noqa: BLE001
            pass

    result = geocoder.reverse(point)
    if cache is not None and result.found and not result.provider.endswith('-fallback'):
        try:
            cache.set(key, {
                'label': result.label,
                'lat': result.point.lat,
                'lng': result.point.lng,
                'county': result.county,
                'country': result.country,
                'confidence': result.confidence,
                'provider': result.provider,
            }, timeout=ttl)
        except Exception:  # noqa: BLE001
            pass
    return result
