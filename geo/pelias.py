"""Pelias geocoding adapter.

Talks to a Pelias HTTP instance. Enable with:

    GEO_GEOCODER=pelias
    PELIAS_URL=http://pelias:4000

Falls back to the built-in gazetteer whenever Pelias is unreachable or returns
nothing usable, so address entry keeps working during an outage.
"""

import os

import requests

from .provider import GeocodingProvider, GeoPoint, GeocodeResult
from .fallback import GazetteerGeocoder

# ISO-3 codes for the boundary filter; Pelias expects alpha-3.
COUNTRY_ISO3 = {
    'kenya': 'KEN', 'uganda': 'UGA', 'tanzania': 'TZA', 'rwanda': 'RWA',
    'burundi': 'BDI', 'south sudan': 'SSD', 'ethiopia': 'ETH', 'somalia': 'SOM',
    'nigeria': 'NGA', 'ghana': 'GHA', 'south africa': 'ZAF', 'egypt': 'EGY',
}


class PeliasGeocoder(GeocodingProvider):
    name = 'pelias'

    def __init__(self, base_url=None, timeout=None):
        self.base_url = (base_url or os.environ.get('PELIAS_URL') or '').rstrip('/')
        if not self.base_url:
            raise RuntimeError('PELIAS_URL is not configured')
        try:
            self.timeout = float(timeout or os.environ.get('PELIAS_TIMEOUT') or 5.0)
        except (TypeError, ValueError):
            self.timeout = 5.0
        self._fallback = GazetteerGeocoder()

    def _parse(self, payload, fallback_label, fallback_country):
        features = (payload or {}).get('features') or []
        if not features:
            return None
        top = features[0]
        coords = ((top.get('geometry') or {}).get('coordinates')) or []
        if len(coords) < 2:
            return None
        props = top.get('properties') or {}
        # Pelias GeoJSON is [lng, lat].
        return GeocodeResult(
            label=props.get('label') or fallback_label or '',
            point=GeoPoint(float(coords[1]), float(coords[0])),
            county=props.get('county') or props.get('region'),
            country=props.get('country') or fallback_country,
            confidence=float(props.get('confidence') or 0.0),
            provider=self.name,
            raw=props,
        )

    def geocode(self, query: str, country: str = 'Kenya') -> GeocodeResult:
        if not (query or '').strip():
            return GeocodeResult(label='', provider=self.name, country=country)
        params = {'text': query, 'size': 1}
        iso3 = COUNTRY_ISO3.get((country or '').strip().lower())
        if iso3:
            params['boundary.country'] = iso3
        try:
            resp = requests.get(f'{self.base_url}/v1/search', params=params, timeout=self.timeout)
            resp.raise_for_status()
            parsed = self._parse(resp.json(), query, country)
            if parsed and parsed.found:
                return parsed
        except Exception:  # noqa: BLE001
            pass
        result = self._fallback.geocode(query, country)
        result.provider = f'{self.name}-fallback'
        return result

    def reverse(self, point: GeoPoint) -> GeocodeResult:
        if not (point and point.is_valid()):
            return GeocodeResult(label='', provider=self.name)
        try:
            resp = requests.get(
                f'{self.base_url}/v1/reverse',
                params={'point.lat': point.lat, 'point.lon': point.lng, 'size': 1},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            parsed = self._parse(resp.json(), '', 'Kenya')
            if parsed and parsed.found:
                return parsed
        except Exception:  # noqa: BLE001
            pass
        result = self._fallback.reverse(point)
        result.provider = f'{self.name}-fallback'
        return result
