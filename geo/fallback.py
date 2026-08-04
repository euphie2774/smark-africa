"""Zero-dependency routing and geocoding.

Used when no Valhalla/Pelias instance is configured. Distances are great-circle
(haversine) inflated by a road-winding factor, which is good enough to price a
delivery but is always flagged `is_estimate=True`.

The gazetteer covers all 47 Kenyan county headquarters plus major towns and the
East/Central African capitals the marketplace ships to. It is intentionally a
plain dict - no network, no index build, no service to keep alive.
"""

import math
import os
import re

from .provider import (
    RoutingProvider,
    GeocodingProvider,
    GeoPoint,
    RouteResult,
    GeocodeResult,
)

EARTH_RADIUS_KM = 6371.0088

# Straight-line distance understates road distance. 1.32 is a common planning
# figure for mixed highway/rural networks; admin-tunable via env.
DEFAULT_ROAD_FACTOR = 1.32

# Average door-to-door speed including stops, used only when no router is up.
DEFAULT_AVG_SPEED_KMH = 45.0


def _f(name, default):
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


# (lat, lng, county, country)
GAZETTEER = {
    # --- Nairobi + metro ---
    'nairobi': (-1.286389, 36.817223, 'Nairobi', 'Kenya'),
    'westlands': (-1.2673, 36.8065, 'Nairobi', 'Kenya'),
    'karen': (-1.3191, 36.7062, 'Nairobi', 'Kenya'),
    'embakasi': (-1.3167, 36.9000, 'Nairobi', 'Kenya'),
    'kasarani': (-1.2205, 36.8968, 'Nairobi', 'Kenya'),
    'ruiru': (-1.1450, 36.9600, 'Kiambu', 'Kenya'),
    'juja': (-1.1036, 37.0144, 'Kiambu', 'Kenya'),
    'thika': (-1.0333, 37.0693, 'Kiambu', 'Kenya'),
    'kiambu': (-1.1714, 36.8356, 'Kiambu', 'Kenya'),
    'limuru': (-1.1114, 36.6425, 'Kiambu', 'Kenya'),
    'kikuyu': (-1.2464, 36.6636, 'Kiambu', 'Kenya'),
    'githunguri': (-1.0500, 36.7667, 'Kiambu', 'Kenya'),

    # --- Central ---
    "murang'a": (-0.7167, 37.1500, "Murang'a", 'Kenya'),
    'muranga': (-0.7167, 37.1500, "Murang'a", 'Kenya'),
    'kenol': (-0.9000, 37.1167, "Murang'a", 'Kenya'),
    'nyeri': (-0.4169, 36.9514, 'Nyeri', 'Kenya'),
    'karatina': (-0.4833, 37.1333, 'Nyeri', 'Kenya'),
    'othaya': (-0.5500, 36.9333, 'Nyeri', 'Kenya'),
    'kerugoya': (-0.4989, 37.2803, 'Kirinyaga', 'Kenya'),
    'kirinyaga': (-0.4989, 37.2803, 'Kirinyaga', 'Kenya'),
    'kutus': (-0.5500, 37.2833, 'Kirinyaga', 'Kenya'),
    'ol kalou': (-0.2725, 36.3781, 'Nyandarua', 'Kenya'),
    'nyandarua': (-0.2725, 36.3781, 'Nyandarua', 'Kenya'),
    'nyahururu': (0.0361, 36.3639, 'Nyandarua', 'Kenya'),

    # --- Rift Valley ---
    'nakuru': (-0.3031, 36.0800, 'Nakuru', 'Kenya'),
    'naivasha': (-0.7167, 36.4333, 'Nakuru', 'Kenya'),
    'gilgil': (-0.4939, 36.3200, 'Nakuru', 'Kenya'),
    'molo': (-0.2489, 35.7322, 'Nakuru', 'Kenya'),
    'eldoret': (0.5143, 35.2698, 'Uasin Gishu', 'Kenya'),
    'uasin gishu': (0.5143, 35.2698, 'Uasin Gishu', 'Kenya'),
    'iten': (0.6700, 35.5081, 'Elgeyo-Marakwet', 'Kenya'),
    'kapsabet': (0.2028, 35.1053, 'Nandi', 'Kenya'),
    'nandi': (0.2028, 35.1053, 'Nandi', 'Kenya'),
    'kericho': (-0.3689, 35.2861, 'Kericho', 'Kenya'),
    'bomet': (-0.7817, 35.3417, 'Bomet', 'Kenya'),
    'narok': (-1.0833, 35.8667, 'Narok', 'Kenya'),
    'kajiado': (-1.8522, 36.7767, 'Kajiado', 'Kenya'),
    'kitengela': (-1.4667, 36.9667, 'Kajiado', 'Kenya'),
    'ngong': (-1.3667, 36.6500, 'Kajiado', 'Kenya'),
    'kitale': (1.0157, 35.0062, 'Trans Nzoia', 'Kenya'),
    'trans nzoia': (1.0157, 35.0062, 'Trans Nzoia', 'Kenya'),
    'kabarnet': (0.4919, 35.7431, 'Baringo', 'Kenya'),
    'baringo': (0.4919, 35.7431, 'Baringo', 'Kenya'),
    'maralal': (1.0972, 36.6981, 'Samburu', 'Kenya'),
    'samburu': (1.0972, 36.6981, 'Samburu', 'Kenya'),
    'lodwar': (3.1191, 35.5972, 'Turkana', 'Kenya'),
    'turkana': (3.1191, 35.5972, 'Turkana', 'Kenya'),
    'kapenguria': (1.2389, 35.1119, 'West Pokot', 'Kenya'),
    'west pokot': (1.2389, 35.1119, 'West Pokot', 'Kenya'),

    # --- Western / Nyanza ---
    'kakamega': (0.2827, 34.7519, 'Kakamega', 'Kenya'),
    'mumias': (0.3333, 34.4833, 'Kakamega', 'Kenya'),
    'bungoma': (0.5635, 34.5606, 'Bungoma', 'Kenya'),
    'webuye': (0.6167, 34.7667, 'Bungoma', 'Kenya'),
    'busia': (0.4608, 34.1114, 'Busia', 'Kenya'),
    'vihiga': (0.0500, 34.7167, 'Vihiga', 'Kenya'),
    'kisumu': (-0.0917, 34.7680, 'Kisumu', 'Kenya'),
    'ahero': (-0.1667, 34.9167, 'Kisumu', 'Kenya'),
    'siaya': (0.0607, 34.2881, 'Siaya', 'Kenya'),
    'bondo': (0.2400, 34.2700, 'Siaya', 'Kenya'),
    'homa bay': (-0.5273, 34.4571, 'Homa Bay', 'Kenya'),
    'kisii': (-0.6817, 34.7667, 'Kisii', 'Kenya'),
    'nyamira': (-0.5633, 34.9358, 'Nyamira', 'Kenya'),
    'migori': (-1.0634, 34.4731, 'Migori', 'Kenya'),

    # --- Eastern ---
    'machakos': (-1.5177, 37.2634, 'Machakos', 'Kenya'),
    'athi river': (-1.4500, 36.9833, 'Machakos', 'Kenya'),
    'mlolongo': (-1.4000, 36.9333, 'Machakos', 'Kenya'),
    'kitui': (-1.3667, 38.0106, 'Kitui', 'Kenya'),
    'makueni': (-1.8038, 37.6243, 'Makueni', 'Kenya'),
    'wote': (-1.7833, 37.6333, 'Makueni', 'Kenya'),
    'embu': (-0.5310, 37.4575, 'Embu', 'Kenya'),
    'meru': (0.0463, 37.6559, 'Meru', 'Kenya'),
    'nanyuki': (0.0167, 37.0723, 'Laikipia', 'Kenya'),
    'laikipia': (0.0167, 37.0723, 'Laikipia', 'Kenya'),
    'chuka': (-0.3333, 37.6500, 'Tharaka-Nithi', 'Kenya'),
    'tharaka-nithi': (-0.3333, 37.6500, 'Tharaka-Nithi', 'Kenya'),
    'isiolo': (0.3546, 37.5822, 'Isiolo', 'Kenya'),
    'marsabit': (2.3284, 37.9899, 'Marsabit', 'Kenya'),
    'moyale': (3.5167, 39.0500, 'Marsabit', 'Kenya'),

    # --- North Eastern ---
    'garissa': (-0.4536, 39.6461, 'Garissa', 'Kenya'),
    'wajir': (1.7471, 40.0573, 'Wajir', 'Kenya'),
    'mandera': (3.9366, 41.8670, 'Mandera', 'Kenya'),

    # --- Coast ---
    'mombasa': (-4.0435, 39.6682, 'Mombasa', 'Kenya'),
    'nyali': (-4.0400, 39.7000, 'Mombasa', 'Kenya'),
    'likoni': (-4.0833, 39.6667, 'Mombasa', 'Kenya'),
    'kilifi': (-3.5107, 39.9093, 'Kilifi', 'Kenya'),
    'malindi': (-3.2192, 40.1169, 'Kilifi', 'Kenya'),
    'watamu': (-3.3500, 40.0167, 'Kilifi', 'Kenya'),
    'kwale': (-4.1737, 39.4521, 'Kwale', 'Kenya'),
    'diani': (-4.2767, 39.5933, 'Kwale', 'Kenya'),
    'ukunda': (-4.2900, 39.5700, 'Kwale', 'Kenya'),
    'voi': (-3.3961, 38.5561, 'Taita-Taveta', 'Kenya'),
    'taita-taveta': (-3.3961, 38.5561, 'Taita-Taveta', 'Kenya'),
    'wundanyi': (-3.4000, 38.3667, 'Taita-Taveta', 'Kenya'),
    'lamu': (-2.2717, 40.9020, 'Lamu', 'Kenya'),
    'hola': (-1.5000, 40.0333, 'Tana River', 'Kenya'),
    'tana river': (-1.5000, 40.0333, 'Tana River', 'Kenya'),

    # --- Regional capitals served cross-border ---
    'kampala': (0.3476, 32.5825, None, 'Uganda'),
    'dar es salaam': (-6.7924, 39.2083, None, 'Tanzania'),
    'arusha': (-3.3869, 36.6830, None, 'Tanzania'),
    'kigali': (-1.9441, 30.0619, None, 'Rwanda'),
    'bujumbura': (-3.3614, 29.3599, None, 'Burundi'),
    'juba': (4.8594, 31.5713, None, 'South Sudan'),
    'addis ababa': (9.0300, 38.7400, None, 'Ethiopia'),
    'mogadishu': (2.0469, 45.3182, None, 'Somalia'),
    'dodoma': (-6.1630, 35.7516, None, 'Tanzania'),
    'mwanza': (-2.5164, 32.9175, None, 'Tanzania'),
    'entebbe': (0.0512, 32.4460, None, 'Uganda'),
    'lagos': (6.5244, 3.3792, None, 'Nigeria'),
    'accra': (5.6037, -0.1870, None, 'Ghana'),
    'johannesburg': (-26.2041, 28.0473, None, 'South Africa'),
    'cairo': (30.0444, 31.2357, None, 'Egypt'),
}

# County -> headquarters coordinates, for when only a county is known.
COUNTY_CENTROIDS = {}
for _key, (_lat, _lng, _county, _country) in GAZETTEER.items():
    if _county and _county.lower() not in COUNTY_CENTROIDS:
        COUNTY_CENTROIDS[_county.lower()] = (_lat, _lng, _county, _country)


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    lat1, lng1 = math.radians(a.lat), math.radians(a.lng)
    lat2, lng2 = math.radians(b.lat), math.radians(b.lng)
    dlat, dlng = lat2 - lat1, lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, h)))


def _normalise(text):
    cleaned = re.sub(r'[^a-z0-9\s\'-]', ' ', (text or '').lower())
    return re.sub(r'\s+', ' ', cleaned).strip()


def lookup_place(query, country='Kenya'):
    """Best-effort gazetteer hit. Returns (lat, lng, county, country) or None."""
    text = _normalise(query)
    if not text:
        return None
    if text in GAZETTEER:
        return GAZETTEER[text]
    if text in COUNTY_CENTROIDS:
        return COUNTY_CENTROIDS[text]

    # Strip common suffixes: "Nakuru County", "Nyeri Town", "Kisumu CBD"
    stripped = re.sub(r'\b(county|town|city|cbd|central|sub[- ]county|ward)\b', '', text).strip()
    if stripped and stripped in GAZETTEER:
        return GAZETTEER[stripped]
    if stripped and stripped in COUNTY_CENTROIDS:
        return COUNTY_CENTROIDS[stripped]

    # Longest known place name appearing as a whole word in the query wins, so
    # "Kimathi Street, Nairobi" resolves and "Nairobi Road, Nakuru" prefers the
    # longer/later match rather than the first token.
    best = None
    for name, entry in GAZETTEER.items():
        if re.search(r'\b' + re.escape(name) + r'\b', text) and (best is None or len(name) > len(best[0])):
            best = (name, entry)
    return best[1] if best else None


class HaversineRouter(RoutingProvider):
    """Great-circle distance inflated to approximate road distance."""

    name = 'haversine'

    def route(self, origin: GeoPoint, destination: GeoPoint) -> RouteResult:
        if not (origin and destination and origin.is_valid() and destination.is_valid()):
            return RouteResult(distance_km=0.0, provider=self.name, is_estimate=True)

        factor = _f('GEO_ROAD_FACTOR', DEFAULT_ROAD_FACTOR)
        speed = _f('GEO_AVG_SPEED_KMH', DEFAULT_AVG_SPEED_KMH) or DEFAULT_AVG_SPEED_KMH
        km = haversine_km(origin, destination) * factor
        return RouteResult(
            distance_km=km,
            duration_minutes=(km / speed) * 60.0 if speed > 0 else None,
            provider=self.name,
            is_estimate=True,
            geometry=[[origin.lng, origin.lat], [destination.lng, destination.lat]],
        )


class GazetteerGeocoder(GeocodingProvider):
    """Dictionary lookup over known Kenyan and regional place names."""

    name = 'gazetteer'

    def geocode(self, query: str, country: str = 'Kenya') -> GeocodeResult:
        entry = lookup_place(query, country)
        if not entry:
            return GeocodeResult(label=query or '', provider=self.name, confidence=0.0, country=country)
        lat, lng, county, entry_country = entry
        # Exact key match is high confidence; substring match is weaker.
        exact = _normalise(query) in GAZETTEER or _normalise(query) in COUNTY_CENTROIDS
        return GeocodeResult(
            label=query or '',
            point=GeoPoint(lat, lng),
            county=county,
            country=entry_country or country,
            confidence=0.9 if exact else 0.55,
            provider=self.name,
        )

    def reverse(self, point: GeoPoint) -> GeocodeResult:
        """Nearest known place to a coordinate."""
        if not (point and point.is_valid()):
            return GeocodeResult(label='', provider=self.name)
        best_name, best_entry, best_km = None, None, None
        for name, entry in GAZETTEER.items():
            km = haversine_km(point, GeoPoint(entry[0], entry[1]))
            if best_km is None or km < best_km:
                best_name, best_entry, best_km = name, entry, km
        if not best_entry:
            return GeocodeResult(label='', provider=self.name)
        return GeocodeResult(
            label=best_name.title(),
            point=GeoPoint(best_entry[0], best_entry[1]),
            county=best_entry[2],
            country=best_entry[3],
            # Confidence decays with distance from the nearest known town.
            confidence=max(0.0, min(0.9, 1.0 - (best_km / 100.0))),
            provider=self.name,
        )
