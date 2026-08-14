"""OSRM map-matching adapter.

Talks to an OSRM HTTP instance (self-hosted, or the public demo server for
experiments only - it has no SLA and forbids production traffic). Enable with:

    GEO_TRACE_PROVIDER=osrm
    OSRM_URL=http://osrm:5000

OSRM is the most commonly self-hosted matcher and its `/match` service is built
for exactly this job: a noisy vehicle trace in, road geometry out. Any failure
degrades to the filtered raw trace rather than blanking the dispatch map.
"""

import os

import requests

from .provider import MapMatchingProvider, MatchResult
from .fallback import FilteredTraceMatcher, clean_trace
from .polyline import decode_polyline

# `geometries=polyline6` gives ~0.1 m resolution; the 1e5 default visibly
# staircases a city street at zoom 16.
OSRM_POLYLINE_PRECISION = 6

# OSRM rejects an oversized search radius, and a generous one invites a match
# onto the wrong parallel street, so a phone's accuracy is clamped, not trusted.
MIN_RADIUS_M = 5.0
MAX_RADIUS_M = 50.0

# The /match service takes 100 coordinates per request by default.
MAX_COORDINATES = 100


class OsrmMatcher(MapMatchingProvider):
    """Snaps a driver's GPS breadcrumbs onto the roads they actually drove."""

    name = 'osrm'

    def __init__(self, base_url=None, timeout=None, profile=None):
        self.base_url = (base_url or os.environ.get('OSRM_URL') or '').rstrip('/')
        if not self.base_url:
            raise RuntimeError('OSRM_URL is not configured')
        try:
            self.timeout = float(timeout or os.environ.get('OSRM_TIMEOUT') or 6.0)
        except (TypeError, ValueError):
            self.timeout = 6.0
        # 'driving' matches how the OSM extract was prepared in most builds;
        # 'bike' is closer to a boda weaving through traffic if you have it.
        self.profile = profile or os.environ.get('OSRM_PROFILE') or 'driving'
        self._fallback = FilteredTraceMatcher()

    def match(self, points) -> MatchResult:
        cleaned = clean_trace(points)[-MAX_COORDINATES:]
        if len(cleaned) < 2:
            return self._fallback.match(points)

        path = ';'.join(f'{p.lng:.6f},{p.lat:.6f}' for p in cleaned)
        params = {
            'geometries': f'polyline{OSRM_POLYLINE_PRECISION}',
            'overview': 'full',
            # Lets OSRM discard the stationary clusters a parked phone produces.
            'tidy': 'true',
            'radiuses': ';'.join(self._radius(p) for p in cleaned),
        }

        # Timestamps sharpen the match, but OSRM rejects the whole request unless
        # every one is present and strictly increasing - so send them only when
        # the trace actually satisfies that.
        stamps = [p.epoch_seconds for p in cleaned]
        if all(s is not None for s in stamps) and all(
                b > a for a, b in zip(stamps, stamps[1:])):
            params['timestamps'] = ';'.join(str(int(s)) for s in stamps)

        try:
            resp = requests.get(
                f'{self.base_url}/match/v1/{self.profile}/{path}',
                params=params, timeout=self.timeout)
            resp.raise_for_status()
            body = resp.json() or {}
            if body.get('code') != 'Ok':
                raise ValueError(f'osrm returned {body.get("code")}')

            matchings = body.get('matchings') or []
            if not matchings:
                raise ValueError('osrm returned no matchings')

            # OSRM splits the trace into several matchings wherever it cannot
            # connect two fixes. Concatenate them all in order: keeping only the
            # best one would silently drop part of the journey, which is the very
            # problem this feature exists to fix. Confidence is the weakest link.
            coordinates = []
            confidences = []
            metres = 0.0
            for matching in matchings:
                decoded = decode_polyline(
                    matching.get('geometry') or '', OSRM_POLYLINE_PRECISION)
                if coordinates and decoded and decoded[0] == coordinates[-1]:
                    decoded = decoded[1:]
                coordinates.extend(decoded)
                metres += float(matching.get('distance') or 0.0)
                if matching.get('confidence') is not None:
                    confidences.append(float(matching['confidence']))

            if len(coordinates) < 2:
                raise ValueError('osrm returned no usable geometry')

            return MatchResult(
                coordinates=coordinates,
                distance_km=metres / 1000.0,
                provider=self.name,
                is_road_matched=True,
                confidence=min(confidences) if confidences else None,
                points_used=len(cleaned),
            )
        except Exception:  # noqa: BLE001 - a matching outage must not blank the map
            result = self._fallback.match(points)
            result.provider = f'{self.name}-fallback'
            return result

    @staticmethod
    def _radius(point):
        accuracy = point.accuracy_m if point.accuracy_m is not None else MAX_RADIUS_M
        return f'{max(MIN_RADIUS_M, min(MAX_RADIUS_M, float(accuracy))):.1f}'
