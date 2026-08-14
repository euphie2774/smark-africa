"""Provider interfaces and selection.

A router answers "how far / how long between two points". A geocoder turns a
free-text address into coordinates (and back). A matcher snaps a sequence of
GPS fixes onto the road network. All three are looked up lazily by name so
importing this module never requires the external services to be up.
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


@dataclass(frozen=True)
class TracePoint:
    """One GPS fix on its way to being map-matched.

    A bare `GeoPoint` is not enough here: accuracy decides how far a matcher may
    move the fix, and the timestamp is what makes an implausible jump
    distinguishable from genuine fast travel.
    """
    lat: float
    lng: float
    accuracy_m: Optional[float] = None
    epoch_seconds: Optional[float] = None

    @property
    def point(self) -> GeoPoint:
        return GeoPoint(self.lat, self.lng)

    def is_valid(self):
        return self.point.is_valid()


@dataclass
class MatchResult:
    """Outcome of snapping a GPS trace onto roads.

    `is_road_matched` is the honest flag the UI keys off: False means these
    coordinates are still the driver's own fixes (cleaned up, but joined by
    straight lines) because no road network was available. Same contract as
    `RouteResult.is_estimate`.
    """
    coordinates: List[List[float]] = field(default_factory=list)  # [[lng, lat], ...]
    distance_km: float = 0.0
    provider: str = 'unknown'
    is_road_matched: bool = False
    confidence: Optional[float] = None
    points_used: int = 0

    @property
    def drawable(self):
        """A LineString needs two positions; anything less draws as nothing."""
        return len(self.coordinates) >= 2

    def as_dict(self):
        return {
            'coordinates': self.coordinates,
            'distance_km': round(self.distance_km or 0.0, 3),
            'provider': self.provider,
            'is_road_matched': bool(self.is_road_matched),
            'confidence': self.confidence,
            'points_used': self.points_used,
        }


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


class MapMatchingProvider:
    """GPS fix sequence -> the roads that were actually driven."""

    name = 'base'

    def match(self, points: List[TracePoint]) -> MatchResult:
        raise NotImplementedError


_ROUTER_CACHE = {}
_GEOCODER_CACHE = {}
_MATCHER_CACHE = {}


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


def get_matcher() -> MapMatchingProvider:
    """Matcher for snapping a GPS trace onto roads.

    Defaults to `fallback`, which cleans the trace but cannot follow roads -
    there is no road network in-process. Set GEO_TRACE_PROVIDER=valhalla|osrm
    once one of those services is reachable and the trace becomes road-true
    with no call-site change.
    """
    choice = _setting('GEO_TRACE_PROVIDER', 'fallback').lower() or 'fallback'
    if choice in _MATCHER_CACHE:
        return _MATCHER_CACHE[choice]

    provider = None
    if choice == 'valhalla':
        try:
            from .valhalla import ValhallaMatcher
            provider = ValhallaMatcher()
        except Exception:  # noqa: BLE001 - misconfiguration must not blank the map
            provider = None
    elif choice == 'osrm':
        try:
            from .osrm import OsrmMatcher
            provider = OsrmMatcher()
        except Exception:  # noqa: BLE001
            provider = None
    if provider is None:
        from .fallback import FilteredTraceMatcher
        provider = FilteredTraceMatcher()

    _MATCHER_CACHE[choice] = provider
    return provider


def reset_providers():
    """Drop cached providers so tests can flip env vars between calls."""
    _ROUTER_CACHE.clear()
    _GEOCODER_CACHE.clear()
    _MATCHER_CACHE.clear()
