"""Provider interfaces and selection.

A router answers "how far / how long between two points". A geocoder turns a
free-text address into coordinates (and back). Both are looked up lazily by
name so importing this module never requires the external services to be up.
"""

import os
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lng: float

    def as_tuple(self):
        return (self.lat, self.lng)

    def is_valid(self):
        return -90.0 <= self.lat <= 90.0 and -180.0 <= self.lng <= 180.0


@dataclass
class RouteResult:
    """Outcome of a distance/ETA lookup.

    `distance_km` is the billable distance. `is_estimate` is True when the
    number came from a straight-line approximation rather than real road
    routing - the UI surfaces this so staff know when to trust it.
    """
    distance_km: float
    duration_minutes: Optional[float] = None
    provider: str = 'unknown'
    is_estimate: bool = True
    geometry: Optional[List[List[float]]] = None  # [[lng, lat], ...] for map draw

    def rounded_km(self, places=2):
        return round(max(0.0, self.distance_km or 0.0), places)


@dataclass
class GeocodeResult:
    label: str
    point: Optional[GeoPoint] = None
    county: Optional[str] = None
    country: Optional[str] = 'Kenya'
    confidence: float = 0.0
    provider: str = 'unknown'
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def found(self):
        return self.point is not None and self.point.is_valid()


class RoutingProvider:
    """Distance and travel time between two coordinates."""

    name = 'base'

    def route(self, origin: GeoPoint, destination: GeoPoint) -> RouteResult:
        raise NotImplementedError


class GeocodingProvider:
    """Free-text address -> coordinates."""

    name = 'base'

    def geocode(self, query: str, country: str = 'Kenya') -> GeocodeResult:
        raise NotImplementedError

    def reverse(self, point: GeoPoint) -> GeocodeResult:
        raise NotImplementedError


_ROUTER_CACHE = {}
_GEOCODER_CACHE = {}


def _setting(name, default=''):
    """Read config from env.

    Deliberately env-only: this module is imported by main.py at load time and
    must not touch the database or the Flask app context.
    """
    return (os.environ.get(name) or default).strip()


def get_router() -> RoutingProvider:
    choice = _setting('GEO_ROUTING_PROVIDER', 'fallback').lower() or 'fallback'
    if choice in _ROUTER_CACHE:
        return _ROUTER_CACHE[choice]

    provider = None
    if choice == 'valhalla':
        try:
            from .valhalla import ValhallaRouter
            provider = ValhallaRouter()
        except Exception:  # noqa: BLE001 - never let config break checkout
            provider = None
    if provider is None:
        from .fallback import HaversineRouter
        provider = HaversineRouter()

    _ROUTER_CACHE[choice] = provider
    return provider


def get_geocoder() -> GeocodingProvider:
    choice = _setting('GEO_GEOCODER', 'fallback').lower() or 'fallback'
    if choice in _GEOCODER_CACHE:
        return _GEOCODER_CACHE[choice]

    provider = None
    if choice == 'pelias':
        try:
            from .pelias import PeliasGeocoder
            provider = PeliasGeocoder()
        except Exception:  # noqa: BLE001
            provider = None
    if provider is None:
        from .fallback import GazetteerGeocoder
        provider = GazetteerGeocoder()

    _GEOCODER_CACHE[choice] = provider
    return provider


def reset_providers():
    """Drop cached providers so tests can flip env vars between calls."""
    _ROUTER_CACHE.clear()
    _GEOCODER_CACHE.clear()
