"""Geospatial provider layer.

Routing (distance/ETA) and geocoding are resolved through small provider
interfaces so the deployed stack can be swapped by configuration:

    GEO_ROUTING_PROVIDER = fallback | valhalla
    GEO_GEOCODER         = fallback | pelias

The `fallback` providers are pure Python (haversine + a Kenyan town/county
gazetteer) and need no external services, so shipping quotes work on a plain
SQLite dev box. Point the env vars at Valhalla/Pelias in production and the
call sites do not change.
"""

from .provider import (
    GeoPoint,
    RouteResult,
    GeocodeResult,
    get_router,
    get_geocoder,
    reset_providers,
)

__all__ = [
    'GeoPoint',
    'RouteResult',
    'GeocodeResult',
    'get_router',
    'get_geocoder',
    'reset_providers',
]
