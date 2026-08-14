"""Valhalla routing and map-matching adapter.

Talks to a Valhalla HTTP instance (self-hosted against an OSM extract).
Enable with:

    GEO_ROUTING_PROVIDER=valhalla     # distance/ETA via /route
    GEO_TRACE_PROVIDER=valhalla       # road-snapped driver traces via /trace_route
    VALHALLA_URL=http://valhalla:8002

Any failure - unreachable host, bad payload, timeout - degrades to the
haversine estimate (routing) or the filtered raw trace (matching) rather than
blocking a customer checkout or blanking the dispatch map.
"""

import os

import requests

from .provider import RoutingProvider, MapMatchingProvider, GeoPoint, RouteResult, MatchResult
from .fallback import HaversineRouter, FilteredTraceMatcher, clean_trace
from .polyline import decode_polyline

# Valhalla encodes shapes at 1e6, not the 1e5 Google/OSRM default.
VALHALLA_POLYLINE_PRECISION = 6


class _ValhallaBase:
    """Shared config reading for the Valhalla endpoints."""

    def __init__(self, base_url=None, timeout=None, costing=None):
        self.base_url = (base_url or os.environ.get('VALHALLA_URL') or '').rstrip('/')
        if not self.base_url:
            raise RuntimeError('VALHALLA_URL is not configured')
        try:
            self.timeout = float(timeout or os.environ.get('VALHALLA_TIMEOUT') or 6.0)
        except (TypeError, ValueError):
            self.timeout = 6.0
        # 'auto' suits vans/cars; 'motorcycle' better matches boda deliveries.
        self.costing = costing or os.environ.get('VALHALLA_COSTING') or 'auto'


class ValhallaRouter(_ValhallaBase, RoutingProvider):
    name = 'valhalla'

    def __init__(self, base_url=None, timeout=None, costing=None):
        super().__init__(base_url=base_url, timeout=timeout, costing=costing)
        self._fallback = HaversineRouter()

    def route(self, origin: GeoPoint, destination: GeoPoint) -> RouteResult:
        if not (origin and destination and origin.is_valid() and destination.is_valid()):
            return RouteResult(distance_km=0.0, provider=self.name, is_estimate=True)

        payload = {
            'locations': [
                {'lat': origin.lat, 'lon': origin.lng},
                {'lat': destination.lat, 'lon': destination.lng},
            ],
            'costing': self.costing,
            'directions_options': {'units': 'kilometers'},
        }
        try:
            resp = requests.post(f'{self.base_url}/route', json=payload, timeout=self.timeout)
            resp.raise_for_status()
            summary = ((resp.json() or {}).get('trip') or {}).get('summary') or {}
            km = float(summary.get('length') or 0.0)
            seconds = float(summary.get('time') or 0.0)
            if km <= 0:
                raise ValueError('valhalla returned zero length')
            return RouteResult(
                distance_km=km,
                duration_minutes=seconds / 60.0 if seconds else None,
                provider=self.name,
                is_estimate=False,
            )
        except Exception:  # noqa: BLE001 - a routing outage must not stop a sale
            result = self._fallback.route(origin, destination)
            result.provider = f'{self.name}-fallback'
            return result


class ValhallaMatcher(_ValhallaBase, MapMatchingProvider):
    """Snaps a driver's GPS breadcrumbs onto the roads they actually drove.

    Uses Valhalla's Meili map-matching endpoint with `shape_match=map_snap`, so
    the returned shape is real road geometry: it follows curves the 20-second
    ping interval never captured and ignores fixes that landed in a building.
    """

    name = 'valhalla-trace'

    def __init__(self, base_url=None, timeout=None, costing=None):
        super().__init__(base_url=base_url, timeout=timeout, costing=costing)
        self._fallback = FilteredTraceMatcher()

    def match(self, points) -> MatchResult:
        cleaned = clean_trace(points)
        if len(cleaned) < 2:
            return self._fallback.match(points)

        shape = []
        for point in cleaned:
            entry = {'lat': point.lat, 'lon': point.lng}
            # Timestamps let Meili reason about plausible transitions between
            # candidate edges instead of guessing from geometry alone.
            if point.epoch_seconds is not None:
                entry['time'] = int(point.epoch_seconds)
            if point.accuracy_m is not None:
                entry['accuracy'] = float(point.accuracy_m)
            shape.append(entry)

        payload = {
            'shape': shape,
            'costing': self.costing,
            'shape_match': 'map_snap',
            'directions_options': {'units': 'kilometers'},
        }
        try:
            resp = requests.post(
                f'{self.base_url}/trace_route', json=payload, timeout=self.timeout)
            resp.raise_for_status()
            trip = (resp.json() or {}).get('trip') or {}

            coordinates = []
            for leg in trip.get('legs') or []:
                decoded = decode_polyline(leg.get('shape') or '', VALHALLA_POLYLINE_PRECISION)
                # Legs share an endpoint; drop the duplicate so the line does not
                # double back on itself at every leg boundary.
                if coordinates and decoded and decoded[0] == coordinates[-1]:
                    decoded = decoded[1:]
                coordinates.extend(decoded)

            if len(coordinates) < 2:
                raise ValueError('valhalla returned no usable shape')

            km = float((trip.get('summary') or {}).get('length') or 0.0)
            return MatchResult(
                coordinates=coordinates,
                distance_km=km,
                provider=self.name,
                is_road_matched=True,
                points_used=len(cleaned),
            )
        except Exception:  # noqa: BLE001 - a matching outage must not blank the map
            result = self._fallback.match(points)
            result.provider = f'{self.name}-fallback'
            return result
