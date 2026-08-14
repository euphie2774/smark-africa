"""Geospatial provider layer.

Routing (distance/ETA), geocoding and GPS trace matching are resolved through
small provider interfaces so the deployed stack can be swapped by configuration:

    GEO_ROUTING_PROVIDER = fallback | valhalla
    GEO_GEOCODER         = fallback | pelias
    GEO_TRACE_PROVIDER   = fallback | valhalla | osrm

The `fallback` providers are pure Python (haversine + a Kenyan town/county
gazetteer + a GPS noise filter) and need no external services, so shipping
quotes and driver maps work on a plain SQLite dev box. Point the env vars at
Valhalla/Pelias/OSRM in production and the call sites do not change.

Note the one thing a fallback genuinely cannot do: without a road network there
is no way to know which road a driver used, so `MatchResult.is_road_matched` is
False and the trace stays a cleaned-up join of the raw fixes.
"""

from .provider import (
    GeoPoint,
    TracePoint,
    RouteResult,
    GeocodeResult,
    MatchResult,
    get_router,
    get_geocoder,
    get_matcher,
    reset_providers,
)

__all__ = [
    'GeoPoint',
    'TracePoint',
    'RouteResult',
    'GeocodeResult',
    'MatchResult',
    'get_router',
    'get_geocoder',
    'get_matcher',
    'reset_providers',
]
