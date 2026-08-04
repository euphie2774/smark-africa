"""Valhalla routing adapter.

Talks to a Valhalla HTTP instance (self-hosted against an OSM extract).
Enable with:

    GEO_ROUTING_PROVIDER=valhalla
    VALHALLA_URL=http://valhalla:8002

Any failure - unreachable host, bad payload, timeout - degrades to the
haversine estimate rather than blocking a customer checkout.
"""

import os

import requests

from .provider import RoutingProvider, GeoPoint, RouteResult
from .fallback import HaversineRouter


class ValhallaRouter(RoutingProvider):
    name = 'valhalla'

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
